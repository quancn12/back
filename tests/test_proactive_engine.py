import math
import unittest
from dataclasses import replace

from app.services.proactive.adapter import adapt, fixture, services_hash
from app.services.proactive.controller import initial_state, change_mode, poll, acknowledge, reply
from app.services.proactive.engine import decide, stable_choice, bayes_branch, feasibility
from app.services.proactive.model import C01Model, assignments, fit_c01, marginals, posterior_row, row_key
from app.services.proactive.snapshot import build_snapshot
from app.services.proactive.types import Event, Message


class ProactiveMathTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_state()
        self.state['fixture'] = {'origin': 1000., 'question': False}
        change_mode(self.state, 'LIVE', 1000.)
        self.s, self.cfg, self.model = adapt(self.state, 1000., allow_fixture=True, attention=True)

    def decision(self, **changes):
        return decide(replace(self.s, **changes), self.cfg, self.model)

    def test_f3_exact(self):
        self.assertEqual(posterior_row([2, 0], [1, 1]), (.75, .25))

    def test_f2_independent_small_joint_reference(self):
        # C0~Bernoulli(.5), D0|C0 differs; compare six manually derived cells.
        m = C01Model(1, {'C0': [1, 1], 'D0': [1, 1, 1]}, {'D0': {
            row_key({}, (0,)): [1, 2, 0], row_key({}, (1,)): [0, 0, 3]}})
        self.assertEqual(m.joint({}), (1/6, 1/4, 1/12, 1/12, 1/12, 1/3))
        self.assertAlmostEqual(marginals(m.joint({}), 1)[0], 1/3)

    def test_all_1296_cells_normalize_and_marginals_not_softmax(self):
        joint = self.model.joint(self.s.features)
        self.assertEqual(len(joint), 1296)
        self.assertAlmostEqual(math.fsum(joint), 1)
        self.assertAlmostEqual(marginals(joint, 4)[2], .9)
        many = C01Model(4, {**{f'C{j}': [1, 1] for j in range(4)}, **{f'D{j}': [1, 18, 1] for j in range(4)}})
        self.assertGreater(sum(marginals(many.joint({}), 4)), 1)

    def test_f5_f8_high_need(self):
        d = self.decision()
        self.assertEqual(d.initial_action, 'OFFER')
        self.assertAlmostEqual(d.content_score, .9 * .9 - .1 * .6)
        self.assertAlmostEqual(d.net_value, .69)
        self.assertIsNone(d.confidence)

    def test_bad_timing_silence_despite_relevance(self):
        self.assertEqual(self.decision(attention=False).initial_action, 'WAIT')
        self.assertEqual(self.decision(next_commitment=1005).reason_codes, ['NO_FEASIBLE_SUPPORT'])

    def test_low_future_need(self):
        model = C01Model(4, {**self.model.priors, 'D2': [9, 1, 10]})
        self.assertEqual(decide(self.s, self.cfg, model).initial_action, 'WAIT')

    def test_unavailable_stale_unknown_catalog(self):
        for changes in ({'verified': False}, {'captured_at': 700}, {'captured_at': 1001}, {'delay': None}, {'price': None}, {'compatible': None}):
            with self.subTest(changes=changes):
                self.assertEqual(self.decision(services=(replace(self.s.services[0], **changes),)).initial_action, 'WAIT')

    def test_missing_context_cold_start_uses_declared_priors(self):
        self.assertTrue(self.s.missing)
        self.assertEqual(self.decision(features={}).initial_action, 'OFFER')
        self.assertEqual(decide(self.s, None, None).reason_codes, ['NOT_CONFIGURED'])

    def test_high_uncertainty_no_invented_entropy_veto(self):
        model = C01Model(4, {**self.model.priors, 'D2': [1, 2, 1]})
        service = replace(self.s.services[0], benefit=1, harm=0)
        self.assertEqual(decide(replace(self.s, services=(service,)), self.cfg, model).initial_action, 'OFFER')

    def test_missing_versus_empty_ledger(self):
        self.assertEqual(self.decision(ledger=None).reason_codes, ['ELIGIBILITY_UNKNOWN'])
        self.assertEqual(self.decision(ledger=()).initial_action, 'OFFER')
        self.assertEqual(self.decision(consent=None).reason_codes, ['ELIGIBILITY_UNKNOWN'])

    def test_cooldown_boundary_and_burden(self):
        self.assertEqual(self.decision(ledger=(Message('m', 971, 'ASK'),)).reason_codes, ['COOLDOWN'])
        decision = self.decision(ledger=(Message('m', 970, 'ASK'),))
        self.assertAlmostEqual(decision.burden, 2 ** (-30/120))
        self.assertEqual(decision.initial_action, 'OFFER')
        self.assertEqual(self.decision(ledger=(Message('m', 760, 'ASK'),)).count, 0)

    def test_same_episode_suppression_and_no_timer_anchor(self):
        self.assertEqual(self.decision(ledger=(Message('m', 900, 'OFFER', self.s.services[0].episode),)).initial_action, 'WAIT')
        self.assertEqual(self.decision(evidence_ids=()).initial_action, 'WAIT')
        for status in ('resolved', 'dismissed'):
            self.assertEqual(self.decision(episodes=(replace(self.s.episodes[0], status=status),)).initial_action, 'WAIT')

    def test_duplicate_id_fails_closed(self):
        self.assertEqual(self.decision(services=self.s.services * 2).reason_codes, ['MODEL_INVALID'])

    def test_no_candidates(self):
        self.assertEqual(self.decision(services=()).reason_codes, ['NO_FEASIBLE_SUPPORT'])

    def test_threshold_boundary_above_one_and_zero_values(self):
        a = replace(self.s.services[0], benefit=1, harm=0, base_cost=.85)
        self.assertEqual(self.decision(services=(a,)).initial_action, 'WAIT')
        above = replace(a, base_cost=2)
        self.assertGreater(self.decision(services=(above,)).thresholds[a.id], 1)
        zero = replace(a, benefit=0, harm=0, base_cost=0)
        self.assertEqual(decide(replace(self.s, services=(zero,)), replace(self.cfg, tau=0), self.model).initial_action, 'WAIT')

    def test_tolerance_ties_wait_direct_stable_id(self):
        self.assertEqual(stable_choice({('WAIT',''): 0, ('OFFER','a'): 1e-12}), ('WAIT',''))
        self.assertEqual(stable_choice({('WAIT',''): 0, ('OFFER','z'): 1, ('ASK','a'): 1}), ('OFFER','z'))
        self.assertEqual(stable_choice({('WAIT',''): 0, ('OFFER','z'): 1, ('OFFER','a'): 1}), ('OFFER','a'))

    def test_f9_bayes_zero_mass(self):
        mass, posterior = bayes_branch((.5,.5), ((1,0),(0,1)), 0)
        self.assertEqual((mass, posterior), (.5, (1.,0.)))
        self.assertEqual(bayes_branch((1.,0.), ((1,0),(0,1)), 1), (0.,()))

    def test_f10_f11_question_cost_and_abort(self):
        self.state['fixture']['question'] = True
        s, cfg, model = adapt(self.state, 1000., allow_fixture=True, attention=True)
        d = decide(s, cfg, model)
        # yes: mass .49, posterior need=1; uncertain .01 retains .5 need;
        # no yields zero continuation, abort is always zero.
        expected = -.01 - .05 + .49 * (.9 - .01 - .02 - .05) + .01 * (.15 - .01 - .02 - .05)
        self.assertAlmostEqual(d.action_values['ASK:D0-question'], expected)
        self.assertEqual(d.initial_action, 'ASK')
        self.assertEqual(decide(replace(s, questions=(replace(s.questions[0], likelihoods=None),)), cfg, model).initial_action, 'OFFER')

    def test_one_slot_disables_question(self):
        self.state['fixture']['question'] = True
        s, cfg, m = adapt(self.state, 1000., allow_fixture=True, attention=True)
        s = replace(s, ledger=tuple(Message(str(i), 900+i, 'ASK') for i in range(3)))
        self.assertNotEqual(decide(s, cfg, m).initial_action, 'ASK')
        self.assertEqual(decide(replace(s, ledger=s.ledger+(Message('4',905,'ASK'),)), cfg, m).reason_codes, ['CAP'])

    def test_continuation_feasibility_fixed_landmark(self):
        a = replace(self.s.services[0], duration=58)
        self.assertIsNone(feasibility(a, self.s, 1000))
        self.assertEqual(feasibility(a, self.s, 1005), 'WINDOW_INFEASIBLE')
        self.assertEqual(feasibility(replace(self.s.services[0],captured_at=1001),self.s,1005),'CATALOG_STALE')

    def test_invalid_probabilities(self):
        for alpha in ([0,1,1], [-1,2,1], [float('nan'),1,1]):
            m = C01Model(4, {**self.model.priors, 'D2': alpha})
            self.assertEqual(decide(self.s,self.cfg,m).reason_codes, ['MODEL_INVALID'])

    def test_time_known_latest_revision_future_truth_excluded(self):
        events = [Event('plan', 900, 1100, 'plan', True, commitment=True, features={'next_activity':'walk'}),
                  Event('plan', 1001, 1100, 'plan', True, revision=1, commitment=True, features={'next_activity':'swim'}),
                  Event('weather', 990, 1001, 'weather', True, features={'weather':'rain'}),
                  Event('old', 800, 880, 'activity', True), Event('secret', 950,950,'x',False)]
        x, missing, ids, recency, _ = build_snapshot(events,1000,1440,120)
        self.assertEqual(x['next_activity'],'walk')
        self.assertEqual(x['weather'],'UNKNOWN')
        self.assertEqual(ids, ('old','plan'))
        self.assertAlmostEqual(recency['activity'],.5)

    def test_fit_train_only_and_missing_label_not_zero(self):
        rows = [{'split':'train','trip_day_id':'d1','C':[1], 'D':[None], 'features':{}}]
        m = fit_c01(rows,1,{'C0':[1,1], 'D0':[1,1,1]})
        self.assertNotIn('D0',m.counts)
        with self.assertRaises(ValueError): fit_c01([{**rows[0], 'split':'test'}],1,m.priors)
        with self.assertRaises(ValueError): fit_c01(rows*2,1,m.priors)


class ProactiveTransactionTests(unittest.TestCase):
    def test_uncertain_dispatch_never_becomes_empty_ledger(self):
        state=initial_state();change_mode(state,'LIVE',1000)
        state['fixture']={'origin':1000.,'question':False}
        self.assertIsNotNone(poll(state,1000,True,True)[0]['event'])
        self.assertIsNone(poll(state,1002,True,True)[0]['event'])
        self.assertEqual(poll(state,1016,True,True)[0]['status'],'ELIGIBILITY_UNKNOWN')

    def setup_question(self):
        state=initial_state();change_mode(state,'LIVE',1000)
        state['fixture']={'origin':1000.,'question':True}
        event=poll(state,1000,True,True)[0]['event']
        self.assertEqual(event['kind'],'ASK')
        acknowledge(state,event['id'],1000,True,True)
        return state,event

    def test_one_linked_offer_and_duplicate_reply_receipt(self):
        state,event=self.setup_question()
        result,record=reply(state,event['transaction_id'],'yes','r1',1001,True,True)
        self.assertEqual(result['event']['kind'],'OFFER')
        linked=result['event'];acknowledge(state,linked['id'],1001,True,True)
        self.assertEqual(acknowledge(state,linked['id'],1001,True,True)[0]['status'],'ALREADY_RECEIVED')
        self.assertIsNone(reply(state,event['transaction_id'],'yes','r1',1002,True,True)[0]['event'])
        self.assertEqual(len(state['ledger']),2)

    def test_all_abort_cases(self):
        for answer,when,attention in [('abort',1001,True),('unsupported',1001,True),('yes',1006,True),('yes',1001,False)]:
            state,event=self.setup_question()
            self.assertIsNone(reply(state,event['transaction_id'],answer,'r',when,attention,True)[0]['event'])

    def test_material_change_aborts(self):
        state,event=self.setup_question();state['fixture']['origin']=1000.5
        self.assertIsNone(reply(state,event['transaction_id'],'yes','r',1001,True,True)[0]['event'])

    def test_off_shadow_and_poll_duplicate(self):
        state=initial_state();state['fixture']={'origin':1000.,'question':False}
        self.assertEqual(poll(state,1000,True,True),({'event':None,'status':'OFF'},None))
        change_mode(state,'SHADOW',1000)
        result,record=poll(state,1000,True,True)
        self.assertTrue(record['should_intervene']);self.assertIsNone(result['event']);self.assertFalse(state['ledger'])
        change_mode(state,'LIVE',1016)
        result,_=poll(state,1016,True,True)
        event=result['event']
        self.assertEqual(poll(state,1016.1,True,True)[0]['event']['id'],event['id'])
        change_mode(state,'OFF',1016.2)
        self.assertIsNone(state['event']);self.assertIsNone(poll(state,1017,True,True)[0]['event'])
