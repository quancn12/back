"""Run the data-driven VietSage regression suites.

Offline probes call maintained Python services directly. Browser probes are
delegated to ``run_browser_evals.cjs`` when Puppeteer is available; otherwise
they are reported as skipped with an actionable reason. The command emits a
machine-readable report and exits non-zero only when an assertion fails (or
when ``--require-browser`` is requested and browser capability is absent).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "evals"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


OFFLINE_PROBES = {
    "route",
    "normalize_artifact",
    "generic_artifact",
    "generic_plan_edit",
    "generic_api_sse",
    "itinerary_prompt",
    "structured_prompt",
    "response_prompt",
    "response_guidance",
    "structured_plan_failure_fallback",
    "dedupe_context",
    "sse_event_contract",
    "chat_plan",
    "history_scope",
    "corrected_source_filter",
}
BROWSER_PROBES = {
    "generic_plan_manual",
    "generic_plan_patch",
    "responsive",
    "responsive_artifact",
    "loading",
    "loading_artifact",
    "loading_cancel",
    "loading_error",
    "markdown",
    "initial_route",
    "initial_error",
    "recommendation_controls",
    "artifact_viewer",
    "generic_artifact_reload",
    "context_suggestions",
    "travel_artifact_journey",
    "generic_artifact_journey",
    "auth_layout",
}
LIVE_PROBES = {"live_provider"}
COMMON_ASSERTIONS = {"equals", "not_equals", "null", "nonempty", "contains", "not_contains", "contains_any"}
BROWSER_ASSERTIONS = {
    "dom_count_at_least",
    "dom_not_contains",
    "dom_not_has",
    "each_viewport_no_horizontal_overflow",
    "each_viewport_primary_scroll_owner",
    "assistant_visible_before_stream",
    "assistant_not_blank_before_stream",
    "artifact_skeleton_visible",
    "artifact_visible",
    "loading_removed_after_abort",
    "system_error_visible",
    "generic_artifact_visible",
    "generic_manual_edit_visible",
    "generic_patch_visible",
    "artifact_attachment_visible",
    "artifact_viewer_opened",
    "artifact_viewer_minimized",
    "recommendations_pinned",
    "recommendations_reordered",
    "generic_artifact_reloaded",
    "recommendations_drag_no_click",
    "context_suggestions_visible",
    "context_suggestions_cleared",
    "context_suggestions_scope_safe",
    "artifact_viewer_bounds_safe",
    "artifact_last_control_reachable",
    "artifact_reopen_retained",
    "artifact_focus_returned",
    "artifact_keyboard_close",
    "artifact_reload_anchor_safe",
    "artifact_controls_reachable",
    "artifact_screenshot_open_saved",
    "artifact_screenshot_closed_saved",
    "artifact_closed_surface_visible",
    "recommendations_toolbar_gap",
    "recommendations_surface_visible",
}


class EvalContractError(ValueError):
    """Raised when a JSON suite violates the executable eval contract."""


def _get(value: Any, path: str) -> Any:
    if not isinstance(path, str):
        raise EvalContractError(f"Result path must be a string, got {type(path).__name__}")
    current = value
    for part in path.split(".") if path else []:
        if isinstance(current, dict):
            if part not in current:
                raise EvalContractError(f"Unknown result path {path!r}: missing key {part!r}")
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise EvalContractError(f"Unknown result path {path!r}: index {index} is out of range")
            current = current[index]
        else:
            raise EvalContractError(f"Unknown result path {path!r}: cannot read {part!r} from {current!r}")
    return current


def _assert(result: dict[str, Any], assertion: dict[str, Any]) -> None:
    if not isinstance(assertion, dict) or not isinstance(assertion.get("op"), str):
        raise EvalContractError(f"Assertion must contain a string op: {assertion!r}")
    op = assertion["op"]
    if op in BROWSER_ASSERTIONS:
        raise EvalContractError(f"Browser assertion reached offline runner: {op}")
    if op not in COMMON_ASSERTIONS:
        raise EvalContractError(f"Unknown assertion op {op!r} for offline result")
    if not isinstance(result, dict):
        raise AssertionError(f"Offline probe returned {type(result).__name__}, expected an object")
    value = _get(result, assertion.get("path", ""))
    expected = assertion.get("value")
    if op == "equals" and value != expected:
        raise AssertionError(f"{assertion['path']}={value!r}, expected {expected!r}")
    if op == "not_equals" and value == expected:
        raise AssertionError(f"{assertion['path']} unexpectedly equals {expected!r}")
    if op == "null" and value is not None:
        raise AssertionError(f"{assertion['path']}={value!r}, expected null")
    if op == "nonempty" and not value:
        raise AssertionError(f"{assertion['path']} is empty")
    if op == "contains":
        if isinstance(value, list):
            ok = expected in value
        else:
            ok = str(expected) in str(value)
        if not ok:
            raise AssertionError(f"{assertion['path']} does not contain {expected!r}")
    if op == "not_contains" and str(expected) in str(value):
        raise AssertionError(f"{assertion['path']} contains forbidden {expected!r}")
    if op == "contains_any":
        values = assertion.get("values")
        if not isinstance(values, list) or not values:
            raise EvalContractError("contains_any requires a non-empty values list")
        if not any(str(item) in str(value) for item in values):
            raise AssertionError(f"{assertion['path']} contains none of {values!r}")


def _draft(days: int) -> dict[str, Any]:
    return {
        "title": "Variable plan",
        "destination": "Demo",
        "summary": "A bounded plan",
        "layout": "timeline_map",
        "days": [
            {"label": f"Day {index}", "items": [{"time": "09:00", "title": f"Stop {index}", "place": "A place"}]}
            for index in range(1, days + 1)
        ],
    }


def _generic_artifact() -> Any:
    from app.models.generative_ui import PlanItem, PlanPeriod, StructuredPlanArtifact

    return StructuredPlanArtifact(
        id="eval_plan",
        version=1,
        context="study_plan",
        title="Study plan",
        summary="Bounded generic fixture",
        periods=[
            PlanPeriod(id="period_1", label="Week 1", items=[PlanItem(id="item_1", title="Read chapter 1")]),
            PlanPeriod(id="period_2", label="Week 2", items=[PlanItem(id="item_2", title="Practice chapter 1")]),
        ],
    )


def _offline_probe(case: dict[str, Any]) -> dict[str, Any]:
    probe = case["probe"]
    data = case.get("input", {})
    if probe == "route":
        from app.services.response_registry import classify_response_context, get_response_context

        context = classify_response_context(data["message"], has_current_artifact=data.get("has_current_artifact", False))
        definition = get_response_context(context.id)
        return {"context": context.id, "renderer": definition.preferred_renderer}
    if probe == "normalize_artifact":
        from app.models.generative_ui import TravelItineraryDraft
        from app.services.generative_ui import normalize_itinerary_draft

        draft = TravelItineraryDraft.model_validate(_draft(int(data["days"])))
        requested_days = data.get("requested_days")
        try:
            artifact = normalize_itinerary_draft(
                draft,
                context_text="",
                expected_days=requested_days,
            )
        except ValueError as exc:
            if requested_days is None:
                raise
            return {
                "fallback": "text",
                "fallback_reason": str(exc),
                "day_count": None,
                "item_count": None,
                "requested_days": requested_days,
            }
        return {"day_count": len(artifact.days), "item_count": sum(len(day.items) for day in artifact.days), "requested_days": requested_days}
    if probe == "generic_artifact":
        from app.models.generative_ui import PlanArtifactPatch
        from app.services.generative_ui import apply_structured_plan_patches

        artifact = _generic_artifact()
        patches = [PlanArtifactPatch(op="replace", path="/periods/period_1/items/item_1/title", value="Read chapter 2")]
        updated = apply_structured_plan_patches(artifact, patches, base_version=1)
        return {"context": updated.context, "period_count": len(updated.periods), "title": updated.periods[0].items[0].title, "version": updated.version}
    if probe == "generic_plan_edit":
        from unittest.mock import AsyncMock, patch
        from app.models.unified import UnifiedConversation
        from app.services.unified_chat import build_chat_plan

        raw = {"patches": [{"op": "replace", "path": "/periods/period_1/items/item_1/title", "value": "Read chapter 2"}], "explanation": "Updated generic plan"}
        conversation = UnifiedConversation(id="eval-conversation", locale="en", country_code="VN")
        with patch("app.services.unified_chat.llm_client.chat_structured", new=AsyncMock(return_value=raw)):
            plan = asyncio.run(build_chat_plan(None, conversation, "Update the first item title", "TRAVEL", verified_hotel=None, current_artifact=_generic_artifact()))
        action = plan.action or {}
        return {"action_type": action.get("type"), "patch_path": (action.get("patches") or [{}])[0].get("path"), "explanation": action.get("explanation")}
    if probe == "generic_api_sse":
        from unittest.mock import AsyncMock, patch
        from app.api.unified import stream_message
        from app.core.database import Base
        from app.models import destination, hotel, knowledge, unified as unified_models  # noqa: F401
        from app.models.unified import UnifiedConversation
        from app.models.unified_schemas import UnifiedChatRequest
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        conversation = UnifiedConversation(id="eval-api-conversation", locale="en", country_code="VN")
        session.add(conversation)
        session.commit()
        payload = UnifiedChatRequest(
            message="Update the first item title",
            mode="TRAVEL",
            locale="en",
            destination_slug=None,
            currentArtifact=_generic_artifact().model_dump(by_alias=True),
        )
        raw = {"patches": [{"op": "replace", "path": "/periods/period_1/items/item_1/title", "value": "Read chapter 2"}], "explanation": "Updated generic plan"}
        async def collect(response: Any) -> str:
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else str(chunk))
            return "".join(chunks)
        with patch("app.api.unified.persist_turn"), patch("app.services.unified_chat.llm_client.chat_structured", new=AsyncMock(return_value=raw)):
            response = asyncio.run(stream_message(conversation.id, payload, None, session))
            stream_text = asyncio.run(collect(response))
        session.close()
        return {"status_code": response.status_code, "has_action": "event: action" in stream_text, "has_period_patch": "/periods/period_1/items/item_1/title" in stream_text, "has_done": "event: done" in stream_text}
    if probe == "itinerary_prompt":
        from app.services.generative_ui import build_itinerary_prompt

        prompt = build_itinerary_prompt(message=data["message"], context_text=data.get("context", ""), locale=data.get("locale", "vi"))
        return {"system_prompt": prompt[0]["content"], "user_prompt": prompt[1]["content"]}
    if probe == "structured_prompt":
        from app.services.generative_ui import build_structured_plan_prompt

        prompt = build_structured_plan_prompt(message=data["message"], context=data["context"], locale=data.get("locale", "vi"))
        return {"system_prompt": prompt[0]["content"], "user_prompt": prompt[1]["content"]}
    if probe == "response_prompt":
        from app.services.generative_ui import build_structured_plan_text_prompt
        from app.services.response_registry import classify_response_context

        context = classify_response_context(data["message"])
        prompt = build_structured_plan_text_prompt(message=data["message"], context=context.id, locale=data.get("locale", "vi"))
        return {"context": context.id, "system_prompt": prompt[0]["content"]}
    if probe == "response_guidance":
        from app.services.response_style import prompt_guidance

        preferences, guidance = prompt_guidance(
            data["message"],
            data.get("fallback_locale", data.get("locale", "vi")),
        )
        return {
            "locale": preferences.locale,
            "language_requested": preferences.language_requested,
            "detail": preferences.detail,
            "tone": preferences.tone,
            "format_hint": preferences.format_hint,
            "guidance": guidance,
        }
    if probe == "structured_plan_failure_fallback":
        from unittest.mock import AsyncMock, patch
        from app.models.unified import UnifiedConversation
        from app.services.unified_chat import build_chat_plan

        conversation = UnifiedConversation(id="eval-structured-failure", locale=data.get("locale", "en"), country_code="VN")
        with patch(
            "app.services.unified_chat.llm_client.chat_structured",
            new=AsyncMock(side_effect=ValueError("invalid structured provider output")),
        ):
            plan = asyncio.run(
                build_chat_plan(
                    None,
                    conversation,
                    data["message"],
                    "TRAVEL",
                    verified_hotel=None,
                )
            )
        return {
            "deterministic_reply": plan.deterministic_reply,
            "has_llm_messages": bool(plan.llm_messages),
            "action": plan.action,
        }
    if probe == "dedupe_context":
        from app.services.unified_chat import _dedupe_context_texts

        texts = _dedupe_context_texts(data.get("chunks", []))
        return {"count": len(texts), "texts": texts}
    if probe == "sse_event_contract":
        from app.api.unified import _sse

        events = data.get("events", [])
        encoded = "".join(_sse(event, {"text": "fixture"}) for event in events)
        return {"events": events, "encoded": encoded}
    if probe == "chat_plan":
        from app.models.unified import UnifiedConversation
        from app.services.unified_chat import build_chat_plan

        conversation = UnifiedConversation(id="eval-conversation", locale=data.get("locale", "vi"), country_code="VN")
        plan = asyncio.run(build_chat_plan(None, conversation, data["message"], data.get("mode", "TRAVEL"), verified_hotel=None))
        return {"deterministic_reply": plan.deterministic_reply, "action": plan.action, "sources": plan.sources}
    if probe == "history_scope":
        """Build the normal text prompt from an isolated persisted conversation.

        This is intentionally a runtime probe of ``_history`` and
        ``build_chat_plan``. It checks that a correction remains in the current
        conversation context while another conversation's turns stay out.
        """
        from unittest.mock import AsyncMock, patch
        from app.core.database import Base
        from app.models import destination, hotel, knowledge, unified as unified_models  # noqa: F401
        from app.models.unified import UnifiedConversation, UnifiedMessage
        from app.services.unified_chat import build_chat_plan
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        current_id = "eval-history-current"
        other_id = "eval-history-other"
        current = UnifiedConversation(id=current_id, locale=data.get("locale", "vi"), country_code="VN")
        other = UnifiedConversation(id=other_id, locale=data.get("locale", "vi"), country_code="VN")
        session.add_all([current, other])
        current_turns = data.get("current_turns") or []
        other_turns = data.get("other_turns") or []
        session.add_all([
            UnifiedMessage(conversation_id=current_id, role=turn["role"], context_type="TRAVEL", content=turn["content"])
            for turn in current_turns
        ])
        session.add_all([
            UnifiedMessage(conversation_id=other_id, role=turn["role"], context_type="TRAVEL", content=turn["content"])
            for turn in other_turns
        ])
        session.commit()
        with patch("app.services.unified_chat.travel_vector_store.query", new=AsyncMock(return_value=[])):
            plan = asyncio.run(
                build_chat_plan(
                    session,
                    current,
                    data["message"],
                    "TRAVEL",
                    verified_hotel=None,
                )
            )
        messages = plan.llm_messages or []
        history = messages[1:-1] if len(messages) >= 2 else []
        history_contents = [str(item.get("content") or "") for item in history]
        history_user_contents = [str(item.get("content") or "") for item in history if item.get("role") == "user"]
        excluded_markers = [str(item.get("content") or "") for item in other_turns]
        session.close()
        return {
            "history_contents": history_contents,
            "history_user_contents": history_user_contents,
            "current_message": messages[-1].get("content") if messages else None,
            "history_includes_correction": data.get("correction_marker", "") in history_contents,
            "history_excludes_other_conversation": not any(marker and marker in history_contents for marker in excluded_markers),
            "history_count": len(history),
        }
    if probe == "corrected_source_filter":
        """Exercise the durable Travel planning path after a destination correction.

        The assertion observes the actual retrieval call made by
        ``build_chat_plan``. It guards against a stale persisted editorial slug
        filtering out the destination named in the latest conversation turn.
        """
        from unittest.mock import AsyncMock, patch
        from app.core.database import Base
        from app.models import destination, hotel, knowledge, unified as unified_models  # noqa: F401
        from app.models.unified import UnifiedConversation, UnifiedMessage
        from app.services.unified_chat import build_chat_plan
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        conversation = UnifiedConversation(
            id="eval-corrected-source-filter",
            locale=data.get("locale", "en"),
            country_code="VN",
            destination_slug=data.get("stale_scope", "demo-hue"),
        )
        session.add(conversation)
        session.add_all([
            UnifiedMessage(
                conversation_id=conversation.id,
                role="user",
                context_type="TRAVEL",
                content=data.get("initial_message", "I want to go Hue for 5 days."),
            ),
            UnifiedMessage(
                conversation_id=conversation.id,
                role="user",
                context_type="TRAVEL",
                content=data.get("correction", "Correction: change it to Hoi An, 2 days."),
            ),
        ])
        session.commit()
        chunk = {
            "text": "Hoi An source evidence",
            "source": "hoi-an-guide.pdf",
            "metadata": {"destination_slug": "demo-hoi-an"},
        }
        query = AsyncMock(return_value=[chunk])
        with patch("app.services.unified_chat.travel_vector_store.query", new=query):
            plan = asyncio.run(
                build_chat_plan(
                    session,
                    conversation,
                    data.get("message", "What should I see?"),
                    "TRAVEL",
                    verified_hotel=None,
                )
            )
        session.close()
        kwargs = query.await_args.kwargs if query.await_args else {}
        query_text = str(kwargs.get("query_text") or "")
        return {
            "retrieval_destination_slug": kwargs.get("destination_slug"),
            "query_text": query_text,
            "query_contains_corrected_destination": "Hoi An" in query_text,
            "query_excludes_stale_destination": "Hue" not in query_text,
            "source": (plan.sources[0] if plan.sources else {}).get("source"),
            "llm_has_corrected_source": bool(plan.llm_messages and "Hoi An source evidence" in str(plan.llm_messages)),
        }
    raise ValueError(f"Unknown offline probe {probe!r}")


def _load_cases(selected_suites: set[str] | None, selected_cases: set[str] | None) -> list[dict[str, Any]]:
    all_cases: list[dict[str, Any]] = []
    for path in sorted(EVAL_ROOT.glob("*.json")):
        suite = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(suite, dict) or not isinstance(suite.get("suite"), str) or not isinstance(suite.get("cases"), list):
            raise EvalContractError(f"Invalid eval suite contract in {path.relative_to(ROOT)}")
        if suite.get("schema_version") != "1.0":
            raise EvalContractError(f"Unsupported eval schema in {path.relative_to(ROOT)}: {suite.get('schema_version')!r}")
        if selected_suites and suite["suite"] not in selected_suites:
            continue
        for case in suite["cases"]:
            if not isinstance(case, dict) or not isinstance(case.get("id"), str):
                raise EvalContractError(f"Invalid eval case in {path.relative_to(ROOT)}")
            mode = case.get("mode", "offline")
            probe = case.get("probe")
            expected_probes = {"offline": OFFLINE_PROBES, "browser": BROWSER_PROBES, "live": LIVE_PROBES}.get(mode)
            if expected_probes is None:
                raise EvalContractError(f"Case {case['id']!r} has unknown mode {mode!r}")
            if probe not in expected_probes:
                raise EvalContractError(f"Case {case['id']!r} has unknown {mode} probe {probe!r}")
            assertions = case.get("assertions", [])
            if not isinstance(assertions, list):
                raise EvalContractError(f"Case {case['id']!r} assertions must be a list")
            for assertion in assertions:
                if not isinstance(assertion, dict) or not isinstance(assertion.get("op"), str):
                    raise EvalContractError(f"Case {case['id']!r} has an assertion without a string op")
                op = assertion["op"]
                allowed = COMMON_ASSERTIONS | (BROWSER_ASSERTIONS if mode == "browser" else set())
                if op not in allowed:
                    raise EvalContractError(f"Case {case['id']!r} has unknown assertion op {op!r}")
                if op in COMMON_ASSERTIONS and not isinstance(assertion.get("path"), str):
                    raise EvalContractError(f"Case {case['id']!r} assertion {op!r} requires a string path")
            case = {**case, "suite": suite["suite"], "suite_file": str(path.relative_to(ROOT))}
            if selected_cases and case["id"] not in selected_cases:
                continue
            all_cases.append(case)
    if selected_suites:
        known_suites = {
            json.loads(path.read_text(encoding="utf-8"))["suite"]
            for path in EVAL_ROOT.glob("*.json")
        }
        unknown_suites = sorted(selected_suites - known_suites)
        if unknown_suites:
            raise EvalContractError(f"Unknown suite filter(s): {', '.join(unknown_suites)}")
    if selected_cases:
        known_cases = {
            case["id"]
            for path in sorted(EVAL_ROOT.glob("*.json"))
            for case in json.loads(path.read_text(encoding="utf-8")).get("cases", [])
        }
        unknown_cases = sorted(selected_cases - known_cases)
        if unknown_cases:
            raise EvalContractError(f"Unknown case filter(s): {', '.join(unknown_cases)}")
    if (selected_suites or selected_cases) and not all_cases:
        labels = []
        if selected_suites:
            labels.append(f"suite={sorted(selected_suites)!r}")
        if selected_cases:
            labels.append(f"case={sorted(selected_cases)!r}")
        raise EvalContractError(f"Eval filters matched no cases ({', '.join(labels)})")
    return all_cases


def _run_offline(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reports = []
    for case in cases:
        report = {"suite": case["suite"], "id": case["id"], "mode": case.get("mode", "offline")}
        try:
            result = _offline_probe(case)
            for assertion in case.get("assertions", []):
                _assert(result, assertion)
            report.update({"status": "passed", "result": result})
        except Exception as exc:  # noqa: BLE001
            report.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        reports.append(report)
    return reports


def _run_browser(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not cases:
        return []
    command = ["node", str(ROOT / "scripts" / "run_browser_evals.cjs"), "--cases", json.dumps(cases, ensure_ascii=False)]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0 and not completed.stdout.strip():
        return [{"suite": case["suite"], "id": case["id"], "mode": "browser", "status": "skipped", "reason": completed.stderr.strip() or "browser helper unavailable"} for case in cases]
    try:
        reports = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [{"suite": case["suite"], "id": case["id"], "mode": "browser", "status": "failed", "error": f"invalid browser report: {exc}", "stderr": completed.stderr[-2000:]} for case in cases]
    if not isinstance(reports, list):
        return [{"suite": case["suite"], "id": case["id"], "mode": "browser", "status": "failed", "error": "invalid browser report: expected a list"} for case in cases]
    expected_ids = [case["id"] for case in cases]
    report_ids = [item.get("id") for item in reports if isinstance(item, dict)]
    if report_ids != expected_ids:
        return [{"suite": case["suite"], "id": case["id"], "mode": "browser", "status": "failed", "error": f"invalid browser report case order/coverage: expected {expected_ids!r}, got {report_ids!r}"} for case in cases]
    return reports


def _run_live(cases: list[dict[str, Any]], enabled: bool) -> list[dict[str, Any]]:
    if not cases:
        return []
    reason = (
        "Live provider evaluation is opt-in but no approved model/reference adapter is configured."
        if enabled else
        "Live provider evaluation is disabled by default; use a reviewed provider adapter and corpus."
    )
    return [{"suite": case["suite"], "id": case["id"], "mode": "live", "status": "skipped", "reason": reason} for case in cases]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="append", help="Run only this suite (repeatable).")
    parser.add_argument("--case", action="append", help="Run only this case ID (repeatable).")
    parser.add_argument("--mode", choices=("offline", "browser", "live", "all"), default="offline")
    parser.add_argument("--report", type=Path, help="Write the full JSON report to this path.")
    parser.add_argument("--require-browser", action="store_true", help="Treat skipped browser cases as failures.")
    parser.add_argument("--require-live", action="store_true", help="Treat skipped live provider cases as failures.")
    parser.add_argument("--live", action="store_true", help="Acknowledge live-quality cases; they remain skipped without an approved adapter.")
    args = parser.parse_args()
    # --mode defaults to "offline", so these flags used to be silent no-ops:
    # the cases they guard were never collected, nothing was marked skipped, and
    # the run exited 0. A gate that cannot fail is worse than no gate, because
    # it reads like coverage in a CI file.
    if args.require_browser and args.mode not in {"browser", "all"}:
        parser.error(
            f"--require-browser has no effect with --mode {args.mode}: browser "
            "cases are not collected, so the check would always pass. Use "
            "--mode all (or --mode browser)."
        )
    if args.require_live and args.mode not in {"live", "all"}:
        parser.error(
            f"--require-live has no effect with --mode {args.mode}: live cases "
            "are not collected, so the check would always pass. Use --mode all "
            "(or --mode live)."
        )
    try:
        cases = _load_cases(set(args.suite or []), set(args.case or []))
    except EvalContractError as exc:
        output = {
            "schema_version": "1.0",
            "command": "run_evals.py",
            "summary": {"passed": 0, "failed": 1, "skipped": 0},
            "error": str(exc),
            "cases": [],
        }
        encoded = json.dumps(output, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 1
    offline = [case for case in cases if case.get("mode", "offline") == "offline"]
    browser = [case for case in cases if case.get("mode") == "browser"]
    live = [case for case in cases if case.get("mode") == "live"]
    reports = []
    selected_filter = bool(args.suite or args.case)
    if args.mode in {"offline", "all"}:
        if selected_filter and not offline:
            reports.append({"mode": "offline", "status": "failed", "error": "Selected filters matched no offline cases."})
        else:
            reports.extend(_run_offline(offline))
    if args.mode in {"browser", "all"}:
        if selected_filter and not browser:
            reports.append({"mode": "browser", "status": "failed", "error": "Selected filters matched no browser cases."})
        else:
            reports.extend(_run_browser(browser))
    if args.mode in {"live", "all"}:
        if selected_filter and not live:
            reports.append({"mode": "live", "status": "failed", "error": "Selected filters matched no live cases."})
        else:
            reports.extend(_run_live(live, args.live))
    summary = {status: sum(1 for item in reports if item["status"] == status) for status in ("passed", "failed", "skipped")}
    output = {"schema_version": "1.0", "command": "run_evals.py", "summary": summary, "cases": reports}
    encoded = json.dumps(output, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    failures = summary["failed"]
    if args.require_browser:
        failures += sum(1 for item in reports if item.get("mode") == "browser" and item.get("status") == "skipped")
    if args.require_live:
        failures += sum(1 for item in reports if item.get("mode") == "live" and item.get("status") == "skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
