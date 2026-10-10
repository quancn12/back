"""One cached-prior question transaction, one reply, at most one linked offer."""
from dataclasses import dataclass, field

from .engine import bayes_branch, continuation_values, feasibility, gate, stable_choice
from .types import ANSWERS, Config, Snapshot


@dataclass
class Transaction:
    id: str
    question_id: str
    created_at: float
    expires_at: float
    landmark: float
    context_hash: str
    config_hash: str
    prior: tuple[float, ...]
    likelihoods: tuple[tuple[float, ...], ...]
    service_ids: tuple[str, ...]
    burden: float
    # Original values/bounds must not silently change while awaiting a reply.
    services_hash: str
    closed: bool = False
    reply_ids: list[str] = field(default_factory=list)


def on_reply(tx: Transaction, reply: str, reply_id: str, s: Snapshot, cfg: Config,
             services_hash: str, material_change: bool = False):
    if tx.closed or reply_id in tx.reply_ids:
        return {"action": "WAIT", "reason": "DUPLICATE_REPLY"}
    tx.reply_ids.append(reply_id)
    tx.closed = True
    if (reply not in ANSWERS or reply == "abort" or s.time > tx.expires_at or s.time < tx.created_at
            or material_change or s.context_hash != tx.context_hash or services_hash != tx.services_hash
            or cfg.config_hash != tx.config_hash or s.landmark != tx.landmark):
        return {"action": "WAIT", "reason": "TRANSACTION_ABORTED"}
    reason = gate(s, cfg, continuation=True, own_transaction=tx.id)
    if reason:
        return {"action": "WAIT", "reason": reason}
    mass, posterior = bayes_branch(tx.prior, tx.likelihoods, ANSWERS.index(reply))
    if mass == 0:
        return {"action": "WAIT", "reason": "ZERO_MASS_REPLY"}
    services = [a for a in s.services if a.id in tx.service_ids and feasibility(a, s, s.time) is None]
    values = {("WAIT", ""): 0, **continuation_values(services, posterior, cfg, tx.burden)}
    action, candidate = stable_choice(values)
    return {"action": action, "candidate_id": candidate or None, "net_value": values[(action, candidate)],
            "reason": "LINKED_OFFER" if action == "OFFER" else "NONPOSITIVE_VALUE", "answer_mass": mass}
