"""Validate the synthetic VietSage demo dataset and its proactive test oracle."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / "demo_data" / "vietsage_demo_source.json"
CASES_PATH = PROJECT_ROOT / "demo_data" / "expected_proactive_cases.json"


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _ids(rows: list[dict], label: str) -> set[str]:
    values = [row["id"] for row in rows]
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate ids in {label}")
    return set(values)


def main() -> int:
    source = _load(SOURCE_PATH)
    oracle = _load(CASES_PATH)
    if source["dataset_id"] != oracle["dataset_id"]:
        raise ValueError("Dataset id does not match proactive oracle")

    guest_ids = _ids(source["personas"], "personas")
    stay_ids = _ids(source["stays"], "stays")
    service_ids = _ids(source["service_catalog"], "service_catalog")
    place_ids = _ids(source["places"], "places")
    event_ids = _ids(source["scenario_events"], "scenario_events")
    _ids(oracle["cases"], "expected cases")

    for stay in source["stays"]:
        if stay["guest_id"] not in guest_ids:
            raise ValueError(f"Unknown guest in stay {stay['id']}")
        start = datetime.fromisoformat(stay["check_in_at"])
        end = datetime.fromisoformat(stay["check_out_at"])
        if end <= start:
            raise ValueError(f"Invalid stay interval: {stay['id']}")

    for consent in source["consents"]:
        if consent["guest_id"] not in guest_ids:
            raise ValueError("Consent references unknown guest")
        datetime.fromisoformat(consent["recorded_at"])

    for event in source["scenario_events"]:
        if event["guest_id"] not in guest_ids:
            raise ValueError(f"Unknown guest in event {event['id']}")
        datetime.fromisoformat(event["occurred_at"])

    for case in oracle["cases"]:
        if case["guest_id"] not in guest_ids:
            raise ValueError(f"Unknown guest in case {case['id']}")
        datetime.fromisoformat(case["simulated_now"])
        event_id = case.get("trigger_event")
        if event_id and event_id not in event_ids:
            raise ValueError(f"Unknown event in case {case['id']}")
        expected = case["expected"]
        service_id = expected.get("service_id")
        if service_id and service_id not in service_ids:
            raise ValueError(f"Unknown service in case {case['id']}")
        for place_id in expected.get("candidate_place_ids", []):
            if place_id not in place_ids:
                raise ValueError(f"Unknown candidate place in case {case['id']}")
        for place_id in expected.get("excluded_place_ids", []):
            if place_id not in place_ids:
                raise ValueError(f"Unknown excluded place in case {case['id']}")

    if not stay_ids:
        raise ValueError("At least one stay is required")
    if not source["rag_test_questions"]:
        raise ValueError("At least one RAG test question is required")

    print(
        "Demo data valid: "
        f"guests={len(guest_ids)}, stays={len(stay_ids)}, "
        f"services={len(service_ids)}, places={len(place_ids)}, "
        f"events={len(event_ids)}, cases={len(oracle['cases'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

