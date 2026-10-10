import unittest
import json
import tempfile
from pathlib import Path
from app.services.proactive.manifest import load_manifest, package_hashes
from app.services.proactive.experiments import score, grouped_mean, fit_p0, holm, sign_flip, bootstrap, replay
from app.services.proactive.adapter import adapt
from app.services.proactive.controller import initial_state, change_mode


class ExperimentConformanceTests(unittest.TestCase):
    def test_manifest_missing_and_source_hash_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'manifest.json';path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'BLOCKED_DATA_OR_CALIBRATION'): load_manifest(path)
            raw={'algorithm_version':'C01-F1.0','protocol_version':'C01-E1.0','source_kind':'D0-MATH',
                 'model_id':'fixture','parameters':{},'priors':{},'counts':{},'utility_provenance':'D0',
                 'consent_protocol':'D0','catalog_provenance':'D0','feature_schema':{},'split_provenance':'D0',
                 'package_hashes':{}}
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'hash mismatch'): load_manifest(path)
            self.assertEqual(len(package_hashes()),5)

    def test_null_denominators_and_missing_labels(self):
        result=score([{'checkpoint_id':'c','need_probabilities':{'rest':.8},'messages':[]}],
                     [{'checkpoint_id':'c','group_id':'g','trip_id':'t','D':{'rest':None},'should_silence':True}])
        self.assertIsNone(result['TOC']['estimate']);self.assertIsNone(result['offer_precision']['estimate'])
        self.assertIsNone(result['unresolved_brier']['estimate']);self.assertEqual(result['silence_specificity']['estimate'],1)

    def test_group_not_checkpoint_weighting(self):
        mean,groups=grouped_mean([('a','1',0)]*20+[('b','2',1)])
        self.assertEqual(mean,.5)

    def test_toc_unique_episode_and_questions_excluded_from_offer_precision(self):
        predictions=[{'checkpoint_id':'c','need_probabilities':{'rest':.8},'messages':[
            {'id':'q','kind':'ASK'}, {'id':'a','kind':'OFFER','episode_id':'ep'}, {'id':'b','kind':'OFFER','episode_id':'ep'}]}]
        refs=[{'checkpoint_id':'c','group_id':'g','trip_id':'t','D':{'rest':1},'eligible_episode_ids':['ep'],
               'message_judgments':{'q':{'appropriate':False}, 'a':{'appropriate':True,'in_window':True,'before_request':True},
                                    'b':{'appropriate':True,'in_window':True,'before_request':True}}}]
        m=score(predictions,refs)
        self.assertEqual(m['TOC']['estimate'],1);self.assertEqual(m['TOC']['numerator'],1)
        self.assertEqual(m['offer_precision']['estimate'],1);self.assertEqual(m['NMR']['estimate'],100)
        self.assertAlmostEqual(m['unresolved_brier']['estimate'],.04)

    def test_missing_judgment_does_not_become_correct_message(self):
        result=score([{'checkpoint_id':'c','messages':[{'id':'m','kind':'OFFER','episode_id':'e'}]}],
                     [{'checkpoint_id':'c','group_id':'g','trip_id':'t'}])
        self.assertIsNone(result['NMR']['estimate'])

    def test_p0_half_pseudocount_and_missing(self):
        self.assertEqual(fit_p0([{'split':'train','trip_day_id':'1','D':[1,None]}],2),[[.2,.6,.2],[1/3]*3])

    def test_exact_permutation_holm_seeded_bootstrap(self):
        self.assertEqual(sign_flip([1,1]),.5)
        self.assertEqual(holm({'a':.01,'b':.04,'c':.03}),{'a':.03,'c':.06,'b':.06})
        self.assertEqual(bootstrap([1,2]),bootstrap([1,2]))

    def test_replay_separate_ledgers(self):
        state=initial_state();change_mode(state,'LIVE',1000);state['fixture']={'origin':1000.,'question':False}
        s,c,m=adapt(state,1000,allow_fixture=True,attention=True)
        self.assertEqual(replay([s,s],c,m)[0]['actual_action'],'OFFER')
        self.assertEqual(replay([s,s],c,m)[1]['actual_action'],'WAIT')
        self.assertEqual(replay([s],c,m)[0]['actual_action'],'OFFER')
        self.assertFalse(state['ledger'])
