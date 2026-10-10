"""Runtime adapter: unavailable tourism evidence stays unavailable.

The controlled D0 fixture is separate from real guest/stay data and cannot run in
production. Chat text, bookings, and demo heuristics are never coerced to C/D.
"""
from dataclasses import asdict, replace

from .manifest import digest
from .model import C01Model, assignments
from .snapshot import build_snapshot
from .types import Config, Episode, Event, Message, Question, Service, Snapshot


def fixture(origin: float, question=False):
    """D0-MATH only: explicit artificial priors and a fictional public rest option."""
    parameters = dict(needs=4, horizon=60., history_window=1440., recency_half_life=120.,
                      burden_window=240., burden_half_life=120., cooldown=30., cap=4, kappa=.02, tau=.05)
    priors = {f"C{j}": [1., 1.] for j in range(4)}
    priors.update({f"D{j}": [1., 18., 1.] if j == 2 else [9., 1., 10.] for j in range(4)})
    if question:
        priors["D2"] = [1., 2., 1.]
    cfg = Config(model_id="D0-MATH-QUESTION" if question else "D0-MATH-OFFER",
                 config_hash=digest([parameters, priors, "D0-MATH"]), **parameters)
    model = C01Model(4, priors)
    event = Event("D0-observed-walk", origin, origin, "activity", True, features={"last_activity": "walking"})
    service = Service("D0-rest", 2, "D0-rest-after-walk", "Tình huống giả lập: bạn có thể nghỉ tại khu ghế yên tĩnh trong 10 phút. Đây là dữ liệu kiểm thử, không phải dịch vụ thực tế.",
                      .9, .6, .01, origin, origin + 180, 0., 10., origin, 180., True, True, True, 0., "VND", "D0-catalog")
    questions = ()
    if question:
        likelihoods = tuple((.98, .0, .01, .01) if d[2] == 1 else (.0, .98, .01, .01) for _, d in assignments(4))
        questions = (Question("D0-question", "Tình huống giả lập: bạn có muốn tìm chỗ nghỉ sau khi đi bộ không?", .01, 5., likelihoods, (service.id,)),)
    cfg = replace(cfg, config_hash=digest([parameters, priors, asdict(service), [asdict(q) for q in questions], "D0-MATH"]))
    return cfg, model, (event,), (Episode("D0-rest-after-walk", 2, event.event_id),), (service,), questions


def adapt(state, now, *, allow_fixture=False, attention=None, landmark=None):
    cfg = model = None
    events, episodes, services, questions = (), (), (), ()
    if allow_fixture and state.get("fixture"):
        cfg, model, events, episodes, services, questions = fixture(state["fixture"]["origin"], state["fixture"]["question"])
    features, missing, evidence, recency, context_hash = build_snapshot(list(events), now, 1440, 120)
    ledger = tuple(Message(**m) for m in state["ledger"]) if "ledger" in state and not state.get("ledger_unknown") else None
    s = Snapshot(now, landmark if landmark is not None else now + (cfg.horizon if cfg else 60),
                 features, missing, episodes, services, questions, ledger,
                 state.get("consent", False), attention, state["fixture"]["origin"] + 120 if services else None,
                 pending=bool(state.get("transaction") or state.get("event")), suppressed=tuple(state.get("suppressed", [])),
                 evidence_ids=evidence, recency=recency, context_hash=context_hash)
    return s, cfg, model


def services_hash(snapshot):
    return digest([asdict(a) for a in snapshot.services])
