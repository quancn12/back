"""Frozen F5–F12 policy. Pure decisions; no network, DB, LLM, or dispatch."""
from __future__ import annotations

import math

from .model import C01Model, assignments, marginals
from .types import ANSWERS, EPS, NEEDS, Config, Decision, Snapshot


def validate_config(cfg):
    if not 1 <= cfg.needs <= 4 or cfg.cap < 1 or int(cfg.cap) != cfg.cap:
        raise ValueError("Invalid structural configuration")
    for value in (cfg.horizon, cfg.history_window, cfg.recency_half_life, cfg.burden_window, cfg.burden_half_life):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid time configuration")
    for value in (cfg.kappa, cfg.tau, cfg.cooldown):
        if not math.isfinite(value) or value < 0:
            raise ValueError("Invalid cost configuration")


def stable_choice(values):
    """Compare every contender to the global maximum, not a running near-tie."""
    best = max(values.values())
    contenders = [key for key, value in values.items() if best - value <= EPS]
    return min(contenders, key=lambda k: ({"WAIT": 0, "OFFER": 1, "ASK": 2}[k[0]], k[1]))


def burden_and_count(s, cfg):
    recent = [m for m in s.ledger if s.time - cfg.burden_window < m.time <= s.time]
    return math.fsum(2 ** (-(s.time - m.time) / cfg.burden_half_life) for m in recent), len(recent)


def gate(s, cfg, continuation=False, own_transaction=None):
    if s.ledger is None or s.consent is None or s.attention is None:
        return "ELIGIBILITY_UNKNOWN"
    if not s.consent or not s.attention:
        return "CONSENT_OR_ATTENTION"
    if any(m.time > s.time for m in s.ledger):
        return "LEDGER_INVALID"
    if s.pending and not continuation:
        return "PENDING"
    _, count = burden_and_count(s, cfg)
    if count >= cfg.cap:
        return "CAP"
    relevant = [m for m in s.ledger if not (continuation and m.transaction == own_transaction and m.kind == "ASK")]
    if relevant and s.time - max(m.time for m in relevant) < cfg.cooldown:
        return "COOLDOWN"
    return None


def feasibility(service, s, at):
    episode = next((e for e in s.episodes if e.id == service.episode and e.need == service.need), None)
    if episode is None or episode.anchor not in s.evidence_ids or episode.status != "open":
        return "EPISODE_NOT_OPEN"
    if service.episode in s.suppressed or any(m.episode == service.episode and m.kind == "OFFER" for m in s.ledger or ()):
        return "EPISODE_SUPPRESSED"
    if not service.verified or not service.authorized or service.compatible is not True or not service.evidence_id:
        return "CAPABILITY_OR_CONSTRAINT_UNKNOWN"
    if any(v is None or not math.isfinite(v) for v in (service.opens, service.closes, service.delay, service.duration,
                                                     service.captured_at, service.ttl, service.price, s.next_commitment)):
        return "FEASIBILITY_UNKNOWN"
    if not service.currency or min(service.delay, service.duration, service.ttl, service.price) < 0:
        return "FEASIBILITY_INVALID"
    if service.captured_at > s.time or not 0 <= at - service.captured_at <= service.ttl:
        return "CATALOG_STALE"
    finish = max(at + service.delay, service.opens) + service.duration
    if finish > min(service.closes, s.next_commitment, s.landmark):
        return "WINDOW_INFEASIBLE"
    return None


def bayes_branch(joint, likelihoods, answer_index):
    weights = tuple(p * row[answer_index] for p, row in zip(joint, likelihoods))
    mass = math.fsum(weights)
    return mass, tuple(w / mass for w in weights) if mass > 0 else ()


def valid_question(q, cfg, joint):
    if q.likelihoods is None:
        return False
    return (0 < q.max_delay <= cfg.horizon and math.isfinite(q.base_cost) and q.base_cost >= 0
            and len(q.likelihoods) == len(joint)
            and all(len(row) == len(ANSWERS) and all(math.isfinite(v) and 0 <= v <= 1 for v in row)
                    and math.isclose(math.fsum(row), 1, abs_tol=EPS) for row in q.likelihoods))


def content(service, probability):
    return probability * service.benefit - (1 - probability) * service.harm


def continuation_values(services, joint, cfg, burden):
    p = marginals(joint, cfg.needs)
    return {("OFFER", a.id): content(a, p[a.need]) - a.base_cost - cfg.kappa * (burden + 1) - cfg.tau for a in services}


def decide(s: Snapshot, cfg: Config | None, model: C01Model | None) -> Decision:
    result = Decision()
    if cfg is None or model is None:
        result.reason_codes = ["NOT_CONFIGURED"]
        return result
    result.model_id, result.config_hash = cfg.model_id, cfg.config_hash
    try:
        validate_config(cfg)
        if not math.isfinite(s.time) or not math.isfinite(s.landmark) or cfg.needs != model.needs or abs(s.landmark - s.time - cfg.horizon) > EPS:
            raise ValueError("Snapshot/model mismatch")
        reason = gate(s, cfg)
        if reason:
            result.reason_codes = [reason]
            return result
        joint = model.joint(s.features)
        probabilities = marginals(joint, cfg.needs)
        result.need_probabilities = dict(zip(NEEDS, probabilities))
        burden, count = burden_and_count(s, cfg)
        result.burden, result.count = burden, count
        values = {("WAIT", ""): 0.0}
        # Ambiguous duplicate IDs invalidate the catalog, rather than silently picking one.
        if len({a.id for a in s.services}) != len(s.services) or len({q.id for q in s.questions}) != len(s.questions):
            raise ValueError("Duplicate candidate ID")
        direct = []
        for a in s.services:
            if not 0 <= a.need < cfg.needs or any(not math.isfinite(v) or not 0 <= v <= 1 for v in (a.benefit, a.harm)) or not math.isfinite(a.base_cost) or a.base_cost < 0:
                raise ValueError("Invalid utility")
            exclusion = feasibility(a, s, s.time)
            if exclusion:
                result.excluded_candidates.append({"id": a.id, "reason": exclusion})
                continue
            direct.append(a)
            score = content(a, probabilities[a.need])
            cost = a.base_cost + cfg.kappa * burden
            result.content_values[a.id] = score
            result.intervention_costs[a.id] = cost
            result.thresholds[a.id] = (a.harm + cost + cfg.tau) / (a.benefit + a.harm) if a.benefit + a.harm else None
            values[("OFFER", a.id)] = score - cost - cfg.tau
        for q in s.questions:
            eligible = [a for a in s.services if a.id in q.service_ids and feasibility(a, s, s.time + q.max_delay) is None]
            if count + 2 > cfg.cap or not eligible or not valid_question(q, cfg, joint):
                result.excluded_candidates.append({"id": q.id, "reason": "QUESTION_UNAVAILABLE"})
                continue
            value = -q.base_cost - cfg.kappa * burden - cfg.tau
            result.intervention_costs[q.id] = q.base_cost + cfg.kappa * burden
            result.answer_probabilities[q.id] = {}
            for y, answer in enumerate(ANSWERS):
                mass, posterior = bayes_branch(joint, q.likelihoods, y)
                result.answer_probabilities[q.id][answer] = mass
                if mass == 0 or answer == "abort":
                    continue
                value += mass * max(0, *continuation_values(eligible, posterior, cfg, burden).values())
            values[("ASK", q.id)] = value
        action, candidate = stable_choice(values)
        result.action_values = {f"{a}:{key}": v for (a, key), v in values.items()}
        result.initial_action, result.candidate_id = action, candidate or None
        result.net_value = values[(action, candidate)]
        if action == "OFFER":
            service = next(a for a in direct if a.id == candidate)
            result.target_episode_id = service.episode
            result.target_need = NEEDS[service.need]
            result.content_score = result.content_values[service.id]
        result.reason_codes = ["POSITIVE_VALUE" if action != "WAIT" else "NONPOSITIVE_VALUE" if direct else "NO_FEASIBLE_SUPPORT"]
        return result
    except (ValueError, KeyError, TypeError, OverflowError):
        result.initial_action, result.candidate_id = "WAIT", None
        result.reason_codes = ["MODEL_INVALID"]
        return result
