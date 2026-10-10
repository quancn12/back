"""Atomic session controller, shared by the HTTP adapter and controlled replay.

Transport dispatch reserves one event. Only an idempotent renderer receipt adds
to the actual-message ledger; a pending receipt prevents further decisions.
"""
from dataclasses import asdict, replace
from uuid import uuid4

from .adapter import adapt, services_hash
from .engine import decide, feasibility, gate
from .manifest import digest, software_provenance
from .transactions import Transaction, on_reply
from .types import ALGORITHM, IMPLEMENTATION, PROTOCOL


def initial_state():
    return {"mode": "OFF", "consent": False, "ledger": [], "suppressed": [], "event": None,
            "transaction": None, "last_evaluation": None, "consent_record": None}


def change_mode(state, mode, now):
    state["mode"], state["consent"] = mode, mode != "OFF"
    state["consent_record"] = {"purpose": "PROACTIVE_RESEARCH", "status": "WITHDRAWN" if mode == "OFF" else "GRANTED",
                               "recorded_at": now, "policy_version": "proactive-research-1"}
    if mode == "OFF":
        if state.get("event"):
            state["ledger_unknown"] = True
        state["event"] = state["transaction"] = None
    # Preserve last evaluation and actual-message ledger across toggle/reload.


def audit_record(state, now, kind, **fields):
    return {"timestamp": now, "mode": state["mode"], "algorithm_version": ALGORITHM,
            "protocol_version": PROTOCOL, "implementation_version": IMPLEMENTATION, "kind": kind, **fields}


def event_valid(state, s, cfg, now):
    event = state.get("event")
    if not event or cfg is None or now > event["expires_at"]:
        return False
    if s.context_hash != event["context_hash"] or cfg.config_hash != event["config_hash"]:
        return False
    if gate(replace(s, pending=False), cfg, continuation=event.get("linked", False), own_transaction=event.get("transaction_id")):
        return False
    if event["kind"] == "OFFER":
        return any(a.id == event["candidate_id"] and feasibility(a, s, now) is None for a in s.services)
    q = next((q for q in s.questions if q.id == event["candidate_id"]), None)
    return q is not None and any(a.id in q.service_ids and feasibility(a, s, now + q.max_delay) is None for a in s.services)


def make_event(state, s, cfg, candidate, kind, now, transaction_id=None, linked=False):
    item = next(a for a in (s.services if kind == "OFFER" else s.questions) if a.id == candidate)
    expiry = min(s.landmark, now + (item.max_delay if kind == "ASK" else 1))
    state["event"] = {"id": str(uuid4()), "kind": kind, "candidate_id": candidate,
                      "context_type": "PROACTIVE", "text": item.text, "created_at": now,
                      "expires_at": expiry, "episode_id": item.episode if kind == "OFFER" else None,
                      "transaction_id": transaction_id, "linked": linked,
                      "context_hash": s.context_hash, "config_hash": cfg.config_hash,
                      "landmark": s.landmark}


def public_event(event):
    if event is None:
        return None
    return {k: event[k] for k in ("id", "kind", "context_type", "text", "transaction_id", "expires_at")}


def poll(state, now, attention, allow_fixture=False):
    if state["mode"] == "OFF":
        return {"event": None, "status": "OFF"}, None
    tx = state.get("transaction")
    if tx and now > tx["expires_at"]:
        if state.get("event"):
            state["ledger_unknown"] = True
        state["transaction"] = None
        state["event"] = None
    event = state.get("event")
    s, cfg, model = adapt(state, now, allow_fixture=allow_fixture, attention=attention,
                          landmark=event["landmark"] if event else None)
    if event:
        if state["mode"] != "LIVE" or not event_valid(state, s, cfg, now):
            state["ledger_unknown"] = True
            state["event"] = state["transaction"] = None
            return {"event": None, "status": "DELIVERY_SUPPRESSED"}, audit_record(state, now, "suppressed", event_id=event["id"])
        return {"event": public_event(event), "status": "PENDING_RECEIPT"}, None
    # E1.0 checkpoints: every 15 minutes plus meaningful source/consent changes.
    signature = digest([s.context_hash, s.consent, cfg.config_hash if cfg else None])
    previous = state.get("last_evaluation")
    if previous and previous["signature"] == signature and now - previous["time"] < 15:
        return {"event": None, "status": previous["status"]}, None
    if not attention:
        return {"event": None, "status": "ATTENTION_UNAVAILABLE"}, None
    decision = decide(s, cfg, model)
    state["last_evaluation"] = {"signature": signature, "time": now, "status": decision.reason_codes[0]}
    record = audit_record(state, now, "decision", **asdict(decision), should_intervene=decision.should_intervene,
                          state_availability={"missing": s.missing, "ledger_available": s.ledger is not None},
                          source_kind="D0-MATH" if cfg else "UNCONFIGURED", input_hash=digest(asdict(s)),
                          context_hash=s.context_hash, evidence_ids=s.evidence_ids, message_delivered=False,
                          model_hash=digest(asdict(model)) if model else None, provenance=software_provenance(),
                          tau=cfg.tau if cfg else None)
    if decision.should_intervene and state["mode"] == "LIVE":
        transaction_id = None
        if decision.initial_action == "ASK":
            q = next(q for q in s.questions if q.id == decision.candidate_id)
            transaction_id = str(uuid4())
            candidates = tuple(a.id for a in s.services if a.id in q.service_ids and feasibility(a, s, now + q.max_delay) is None)
            tx = Transaction(transaction_id, q.id, now, now + q.max_delay, s.landmark, s.context_hash,
                             cfg.config_hash, model.joint(s.features), q.likelihoods, candidates, decision.burden, services_hash(s))
            state["transaction"] = asdict(tx)
        make_event(state, s, cfg, decision.candidate_id, decision.initial_action, now, transaction_id)
        record["event_id"] = state["event"]["id"]
        record["transaction_id"] = transaction_id
    record["suppression_reason"] = "SHADOW" if state["mode"] == "SHADOW" else None
    return {"event": public_event(state.get("event")), "status": state["last_evaluation"]["status"]}, record


def acknowledge(state, event_id, now, attention, allow_fixture=False):
    if any(m["id"] == event_id for m in state["ledger"]):
        return {"status": "ALREADY_RECEIVED"}, None
    event = state.get("event")
    if state["mode"] != "LIVE" or not event or event["id"] != event_id:
        return {"status": "SUPPRESSED"}, None
    s, cfg, _ = adapt(state, now, allow_fixture=allow_fixture, attention=attention, landmark=event["landmark"])
    if not event_valid(state, s, cfg, now):
        state["ledger_unknown"] = True
        state["event"] = state["transaction"] = None
        return {"status": "SUPPRESSED"}, audit_record(state, now, "receipt_recheck_failed", event_id=event_id)
    state["ledger"].append({"id": event_id, "time": now, "kind": event["kind"],
                            "episode": event["episode_id"], "transaction": event["transaction_id"]})
    if event["episode_id"]:
        state["suppressed"].append(event["episode_id"])
    state["event"] = None
    return {"status": "RECEIVED"}, audit_record(state, now, "delivery", event_id=event_id,
                                               transaction_id=event["transaction_id"], episode_id=event["episode_id"],
                                               actual_action=event["kind"], message_delivered=True, delivery_timestamp=now)


def reply(state, transaction_id, answer, reply_id, now, attention, allow_fixture=False):
    tx_data = state.get("transaction")
    if state["mode"] != "LIVE" or not tx_data or tx_data["id"] != transaction_id or state.get("event"):
        return {"event": None, "status": "NO_PENDING_QUESTION"}, None
    tx = Transaction(**tx_data)
    s, cfg, _ = adapt(state, now, allow_fixture=allow_fixture, attention=attention, landmark=tx.landmark)
    result = on_reply(tx, answer, reply_id, s, cfg, services_hash(s)) if cfg else {"action": "WAIT", "reason": "NOT_CONFIGURED"}
    state["transaction"] = None
    if result["action"] == "OFFER":
        make_event(state, s, cfg, result["candidate_id"], "OFFER", now, transaction_id, linked=True)
    return {"event": public_event(state.get("event")), "status": result["reason"]}, audit_record(state, now, "reply", transaction_id=transaction_id,
             reply_category=answer, reply_delay=now - tx.created_at, result=result, message_delivered=False)
