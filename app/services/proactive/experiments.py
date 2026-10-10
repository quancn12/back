"""Offline reuse of the core policy; evaluation truth stays outside Snapshot.

This provides D0 conformance/replay and estimators, not a substitute for the
missing archived E1.0 runner schemas or approved acquisition/calibration data.
"""
import itertools
import math
import random
import statistics
from dataclasses import asdict, replace

from .engine import decide
from .types import Decision, Message

VARIANTS = ('C01', 'B-R', 'P0', 'B-C02', 'B-H', 'B-E', 'A-Q', 'A-H', 'A-I', 'A-B',
            'A-T', 'A-X', 'A-REC', 'A-PREF', 'A-RES', 'A-EXP', 'A-SUP', 'A-FEAS')
IMPLEMENTED_VARIANTS = ('C01', 'B-R', 'A-Q', 'A-H', 'A-I', 'A-B', 'A-T')


def variant_decide(snapshot, cfg, model, variant='C01'):
    if variant not in VARIANTS:
        raise ValueError('Unknown frozen variant')
    if variant not in IMPLEMENTED_VARIANTS:
        raise ValueError(f'BLOCKED_VARIANT_ASSETS: {variant}; exact source/refit artifacts required')
    if variant == 'B-R':
        return Decision(reason_codes=['REACTIVE_ONLY'])
    if variant == 'A-Q':
        snapshot = replace(snapshot, questions=())
    if variant == 'A-H':
        snapshot = replace(snapshot, services=tuple(replace(a, harm=0) for a in snapshot.services))
    if variant == 'A-I':
        cfg = replace(cfg, kappa=0)
        snapshot = replace(snapshot, services=tuple(replace(a, base_cost=0) for a in snapshot.services),
                           questions=tuple(replace(q, base_cost=0) for q in snapshot.questions))
    if variant == 'A-B':
        cfg = replace(cfg, kappa=0)
    if variant == 'A-T':
        cfg = replace(cfg, tau=0)
    return decide(snapshot, cfg, model)


def fit_p0(rows, needs):
    counts = [[.5, .5, .5] for _ in range(needs)]
    seen = set()
    for row in rows:
        if row['split'] != 'train' or row['trip_day_id'] in seen:
            raise ValueError('TRAIN only, one checkpoint per trip-day')
        seen.add(row['trip_day_id'])
        if len(row['D']) != needs:
            raise ValueError('Label cardinality')
        for j, label in enumerate(row['D']):
            if label is not None:
                if label not in (0, 1, 2): raise ValueError('Invalid D label')
                counts[j][label] += 1
    return [[v / sum(row) for v in row] for row in counts]


def replay(snapshots, cfg, model, variant='C01'):
    """Direct-action conformance replay with a distinct ledger per invocation.

ASK transactions need externally authored response branches and are explicitly
blocked here; the controller D0 smoke exercises the shared on_reply path.
"""
    ledger = None
    records = []
    previous = -math.inf
    for s in snapshots:
        if s.time < previous: raise ValueError('Replay times must be ordered')
        previous = s.time
        if ledger is None:
            if s.ledger is None: raise ValueError('Explicit initial ledger required')
            ledger = list(s.ledger)
        s = replace(s, ledger=tuple(ledger))
        d = variant_decide(s, cfg, model, variant)
        record = {'time': s.time, 'variant': variant, 'decision': asdict(d), 'actual_action': 'WAIT'}
        if d.initial_action == 'ASK':
            record['status'] = 'BLOCKED_RESPONSE_BRANCH_DATA'
            records.append(record)
            break
        if d.initial_action == 'OFFER':
            ledger.append(Message(f'replay-{len(ledger)}', s.time, 'OFFER', d.target_episode_id))
            record['actual_action'] = 'OFFER'
        records.append(record)
    return records


def grouped_mean(rows):
    """(group, trip, value): average within trip, then equal-weight groups."""
    trips = {}
    for group, trip, value in rows:
        if value is not None: trips.setdefault((group, trip), []).append(value)
    groups = {}
    for (group, _), values in trips.items():
        groups.setdefault(group, []).append(statistics.mean(values))
    means = {g: statistics.mean(values) for g, values in groups.items()}
    return statistics.mean(means.values()) if means else None, means


def score(predictions, references):
    """Independent minimal metric DTO, not the unavailable archived result schema.

Both inputs keyed by checkpoint_id. Reference rows alone carry labels, episode
opportunities, appropriateness, and silence judgments. Unknown is None.
"""
    by_id = {r['checkpoint_id']: r for r in references}
    if len(by_id) != len(references): raise ValueError('Duplicate reference checkpoint')
    seen = set(); brier = []; missing = 0
    trip_counts = {}; opportunities = {}; useful = {}
    for p in predictions:
        key = p['checkpoint_id']
        if key in seen: raise ValueError('Duplicate prediction checkpoint')
        seen.add(key)
        r = by_id.get(key)
        if r is None: missing += 1; continue
        group, trip = r['group_id'], r['trip_id']; gt = (group, trip)
        counts = trip_counts.setdefault(gt, dict(checkpoints=0, judged_offers=0, appropriate_offers=0,
                                                unnecessary_messages=0, judged_messages=0, total_messages=0,
                                                silence=0, correct_silence=0))
        counts['checkpoints'] += 1
        for need, probability in p.get('need_probabilities', {}).items():
            if not math.isfinite(probability) or not 0 <= probability <= 1: raise ValueError('Invalid prediction')
            label = r.get('D', {}).get(need)
            if label is None: missing += 1; continue
            if label not in (0,1,2): raise ValueError('Invalid reference label')
            brier.append((group, trip, (probability - int(label == 1)) ** 2))
        opportunities.setdefault(gt,set()).update(r.get('eligible_episode_ids', []))
        useful.setdefault(gt,set())
        messages = p.get('messages', [])
        for m in messages:
            counts['total_messages'] += 1
            judgment = r.get('message_judgments', {}).get(m['id'])
            if judgment is None:
                missing += 1; continue
            counts['judged_messages'] += 1
            counts['unnecessary_messages'] += int(not judgment['appropriate'])
            if m['kind'] == 'OFFER':
                counts['judged_offers'] += 1
                counts['appropriate_offers'] += int(judgment['appropriate'])
                if judgment['appropriate'] and judgment.get('in_window') is True and judgment.get('before_request') is True:
                    useful[gt].add(m['episode_id'])
        if r.get('should_silence') is True:
            counts['silence'] += 1; counts['correct_silence'] += int(not messages)
    metrics = {}
    def metric(name, values, numerator=None, denominator=None):
        estimate, groups = grouped_mean(values)
        metrics[name] = {'estimate':estimate,'group_values':groups,'independent_groups':len(groups),
                         'numerator':numerator,'denominator':denominator,
                         'status':'DESCRIPTIVE_ONLY' if estimate is not None else 'UNDEFINED_DENOMINATOR',
                         'missing_count':missing}
    metric('unresolved_brier',brier,denominator=len(brier))
    for name,num,den in (('offer_precision','appropriate_offers','judged_offers'),('silence_specificity','correct_silence','silence')):
        metric(name,[(g,t,c[num]/c[den] if c[den] else None) for (g,t),c in trip_counts.items()],
               sum(c[num] for c in trip_counts.values()),sum(c[den] for c in trip_counts.values()))
    # Missing message judgments do not silently count as necessary messages.
    metric('NMR',[(g,t,100*c['unnecessary_messages']/c['checkpoints'] if c['judged_messages']==c['total_messages'] else None)
                  for (g,t),c in trip_counts.items()])
    metric('TOC',[(g,t,len(useful[(g,t)] & eps)/len(eps) if eps else None) for (g,t),eps in opportunities.items()],
           sum(len(useful[gt]&eps) for gt,eps in opportunities.items()), sum(len(eps) for eps in opportunities.values()))
    for name, source in (('UIR', 'offer_precision'), ('missed_opportunity_rate', 'TOC')):
        base = metrics[source]
        metrics[name] = {**base, 'estimate': None if base['estimate'] is None else 1-base['estimate'],
                         'group_values': {g:1-v for g,v in base['group_values'].items()},
                         'numerator': base['denominator'] - base['numerator']}
    return metrics


def sign_flip(differences):
    if not differences: return None
    observed = abs(statistics.mean(differences)); n = len(differences)
    if n <= 20:
        values = (sum(a*b for a,b in zip(signs,differences))/n for signs in itertools.product((-1,1),repeat=n))
        return sum(abs(v) >= observed-1e-15 for v in values) / (2**n)
    rng = random.Random(314159)
    extreme = sum(abs(sum(rng.choice((-1,1))*d for d in differences)/n) >= observed-1e-15 for _ in range(99999))
    return (extreme+1)/100000


def bootstrap(differences):
    if not differences: return {'interval':None,'status':'UNDEFINED_DENOMINATOR'}
    rng = random.Random(271828); n=len(differences)
    samples=sorted(statistics.mean(rng.choices(differences,k=n)) for _ in range(9999))
    return {'interval':[samples[249],samples[9749]], 'undefined_draw_fraction':0,
            'independent_groups':n,'status':'DESCRIPTIVE_ONLY' if n<30 else 'ESTIMATED', 'seed':271828}


def holm(p_values):
    adjusted={};previous=0.;n=len(p_values)
    for rank,(name,p) in enumerate(sorted(p_values.items(),key=lambda pair:pair[1])):
        if not 0<=p<=1: raise ValueError('Invalid p value')
        previous=max(previous,min(1.,(n-rank)*p));adjusted[name]=previous
    return adjusted
