"""F1 reconstruction. Later revisions and future realized facts never enter x."""
import hashlib
import json
from dataclasses import asdict

from .types import FEATURES, Event


def build_snapshot(events: list[Event], time: float, window: float, half_life: float):
    if window <= 0 or half_life <= 0:
        raise ValueError("Invalid F1 window")
    latest = {}
    for event in events:
        if not event.authorized or event.known_at > time:
            continue
        previous = latest.get(event.event_id)
        if previous is None or (event.known_at, event.revision) > (previous.known_at, previous.revision):
            latest[event.event_id] = event
    kept = sorted((e for e in latest.values() if time - window <= e.valid_at <= time
                   or (e.commitment and e.valid_at > time)), key=lambda e: (e.known_at, e.revision, e.event_id))
    features = dict.fromkeys(FEATURES, "UNKNOWN")
    recency = {}
    for event in kept:
        features.update({k: v for k, v in event.features.items() if k in FEATURES})
        if event.valid_at <= time:
            recency[event.kind] = max(recency.get(event.kind, 0), 2 ** (-(time - event.valid_at) / half_life))
    digest = hashlib.sha256(json.dumps([asdict(e) for e in kept], sort_keys=True).encode()).hexdigest()
    return features, tuple(k for k, v in features.items() if v == "UNKNOWN"), tuple(e.event_id for e in kept), recency, digest
