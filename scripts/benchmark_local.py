"""Sequential native Ollama benchmark; fixed synthetic/public cases, never pulls models.

Timing measures first content, not first useful answer. Saved text needs human
quality review. An optional ``--retrieval-url`` calls the read-only Travel RAG
probe before generation and stores only result summaries, never retrieved text.
The script uses only Python's standard library and does not alter app config.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


MESSAGES = [
    {"role": "system", "content": "Chỉ dùng dữ liệu kiểm thử được cung cấp. Trả lời ngắn gọn bằng tiếng Việt."},
    {"role": "user", "content": "Dữ liệu kiểm thử giả lập: bữa sáng phục vụ từ 6:30 đến 9:30 tại tầng 2. Khách hỏi: Tôi ăn sáng lúc nào và ở đâu?"},
]


# These are deliberately fixed public/synthetic prompts. Do not add guest
# questions or paste private source text into this file or its JSON evidence.
TRAVEL_CASES = [
    {
        "id": "short_fact",
        "question": "Hồ Thác Bà có những hoạt động nào phù hợp cho chuyến đi cuối tuần?",
        "history": [],
        "fallback_context": (
            "Hồ Thác Bà phù hợp tham quan hồ, khám phá đảo và trải nghiệm cảnh quan "
            "sông nước trong chuyến đi cuối tuần."
        ),
    },
    {
        "id": "comparison",
        "question": "So sánh Trạm Tấu và Suối Giàng cho chuyến đi 2 ngày 1 đêm: thời gian, cảnh quan và hoạt động.",
        "history": [],
        "fallback_context": (
            "Trạm Tấu có tắm khoáng nóng và cảnh quan núi; Suối Giàng có chè cổ thụ, "
            "khí hậu mát và trải nghiệm bản địa."
        ),
    },
    {
        "id": "itinerary_long",
        "question": (
            "Lập lịch trình du lịch Yên Bái 5 ngày 4 đêm, mỗi ngày có điểm đến, "
            "hoạt động, di chuyển và thời lượng; ưu tiên thiên nhiên, nhịp độ vừa "
            "phải, không di chuyển ban đêm, kèm phương án dự phòng khi mưa."
        ),
        "history": [],
        "fallback_context": (
            "Yên Bái có Hồ Thác Bà, Suối Giàng, Trạm Tấu, Tú Lệ, đèo Khau Phạ và "
            "Mù Cang Chải. Trạm Tấu có tắm khoáng nóng; Suối Giàng có chè cổ thụ; "
            "các điểm ngoài trời cần cân nhắc thời tiết."
        ),
    },
    {
        "id": "followup_constraints",
        "question": "Giữ các ưu tiên đó và điều chỉnh thành lịch trình 3 ngày 2 đêm.",
        "history": [
            {"role": "user", "content": "Tôi muốn chuyến đi thiên nhiên, tránh lịch trình quá dày và không đi đêm."},
            {"role": "assistant", "content": "Đã ghi nhận ưu tiên thiên nhiên, nhịp độ vừa phải và không di chuyển ban đêm."},
        ],
        "fallback_context": (
            "Yên Bái có Hồ Thác Bà, Suối Giàng và Trạm Tấu; thời tiết có thể ảnh hưởng "
            "các hoạt động ngoài trời."
        ),
    },
    {
        "id": "insufficient_source",
        "question": "Giá vé lễ hội năm 2030 tại một điểm đến không có trong dữ liệu hiện có là bao nhiêu?",
        "history": [],
        "fallback_context": "(Không có dữ liệu phù hợp cho câu hỏi này.)",
    },
]


def request_json(base: str, route: str, payload: dict | None, timeout: float,
                 headers: dict[str, str] | None = None) -> dict:
    request = make_request(base, route, payload, headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError(f"Invalid/error response from {route}: {result!r}")
    return result


def make_request(base: str, route: str, payload: dict | None,
                 headers: dict[str, str] | None = None) -> urllib.request.Request:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    return urllib.request.Request(base.rstrip("/") + route, data=data,
                                  headers=request_headers)


def names(snapshot: dict) -> set[str]:
    return {item.get("name") or item.get("model") for item in snapshot.get("models", [])
            if item.get("name") or item.get("model")}


def select_cases(args: argparse.Namespace) -> list[dict]:
    cases = [{"id": "synthetic_breakfast", "question": MESSAGES[1]["content"],
              "history": [], "fallback_context": MESSAGES[1]["content"]}]
    if args.case_set == "travel":
        cases = TRAVEL_CASES
    if args.case_ids:
        wanted = [value.strip() for value in args.case_ids.split(",") if value.strip()]
        by_id = {case["id"]: case for case in cases}
        missing = [case_id for case_id in wanted if case_id not in by_id]
        if missing:
            raise ValueError(f"Unknown case ids for {args.case_set}: {missing}")
        cases = [by_id[case_id] for case_id in wanted]
    if args.case_limit is not None:
        cases = cases[:args.case_limit]
    if not cases:
        raise ValueError("At least one benchmark case is required")
    return cases


def case_messages(case: dict, context: str | None = None) -> list[dict]:
    if case["id"] == "synthetic_breakfast":
        return MESSAGES
    context = context or case["fallback_context"]
    messages = [{
        "role": "system",
        "content": (
            "Bạn là trợ lý du lịch VietSage. Chỉ dùng dữ liệu trong CONTEXT, trả "
            "lời bằng tiếng Việt, nêu rõ khi dữ liệu không đủ và không tự tạo giá, "
            "thời gian, địa chỉ hoặc cam kết.\n\nCONTEXT:\n" + context
        ),
    }]
    messages.extend(case.get("history", []))
    messages.append({"role": "user", "content": case["question"]})
    return messages


def safe_message_summary(messages: list[dict]) -> dict:
    encoded = json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"message_count": len(messages), "prompt_sha256": hashlib.sha256(encoded).hexdigest()}


def retrieval_run(retrieval_base: str, ollama_base: str, case: dict, top_k: int,
                  timeout: float, embed_model: str, admin_key: str) -> tuple[dict, str]:
    """Run the read-only Travel RAG probe and retain context only in memory."""
    started = time.perf_counter()
    before = request_json(ollama_base, "/api/ps", None, timeout)
    payload = {
        "question": case["question"],
        "country_code": "VN",
        "top_k": top_k,
    }
    response = request_json(
        retrieval_base,
        "/api/rag-test",
        payload,
        timeout,
        headers={"X-Admin-Key": admin_key},
    )
    results = response.get("results")
    if not isinstance(results, list):
        raise ValueError("Travel RAG response has no results list")
    context_parts: list[str] = []
    summaries: list[dict] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            context_parts.append(text)
        metadata = item.get("metadata") or {}
        summaries.append({
            "id": item.get("id"),
            "source": item.get("source") or metadata.get("source") or metadata.get("filename"),
            "distance": item.get("distance"),
            "keyword_score": item.get("keyword_score"),
            "text_chars": len(text) if isinstance(text, str) else 0,
        })
    after = request_json(ollama_base, "/api/ps", None, timeout)
    record = {
        "status": "completed",
        "result_count": len(summaries),
        "results": summaries,
        "embedding_model": embed_model,
        "embedding_cold": embed_model not in names(before),
        "before_loaded_models": sorted(names(before)),
        "after_loaded_models": sorted(names(after)),
        "wall_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    return record, "\n\n".join(context_parts)


def numeric_summary(values: list[float | int]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def comparison_summary(records: list[dict]) -> dict:
    by_model: dict[str, dict] = {}
    for model in sorted({record["model"] for record in records}):
        model_records = [record for record in records if record["model"] == model]
        by_model[model] = {"runs": len(model_records), "cases": {}}
        for case_id in sorted({record["case_id"] for record in model_records}):
            case_records = [record for record in model_records if record["case_id"] == case_id]
            chats = [record.get("chat", {}) for record in case_records]
            successful = [
                chat for chat in chats
                if chat.get("status") == "completed_requires_quality_review"
            ]
            completed = [chat for chat in successful if not chat.get("output_limit_reached")]
            capped = [chat for chat in successful if chat.get("output_limit_reached")]
            by_model[model]["cases"][case_id] = {
                "runs": len(case_records),
                "completed": len(completed),
                "output_limited": len(capped),
                "errors": len(chats) - len(successful),
                "first_content_ms": numeric_summary([
                    chat["first_content_ms"] for chat in completed
                    if isinstance(chat.get("first_content_ms"), (int, float))
                ]),
                "wall_ms": numeric_summary([
                    chat["wall_ms"] for chat in completed
                    if isinstance(chat.get("wall_ms"), (int, float))
                ]),
                "output_tokens": numeric_summary([
                    chat["eval_count"] for chat in completed
                    if isinstance(chat.get("eval_count"), (int, float))
                ]),
                "output_chars": numeric_summary([
                    chat["content_chars"] for chat in completed
                    if isinstance(chat.get("content_chars"), (int, float))
                ]),
                "output_limited_first_content_ms": numeric_summary([
                    chat["first_content_ms"] for chat in capped
                    if isinstance(chat.get("first_content_ms"), (int, float))
                ]),
                "output_limited_tokens": numeric_summary([
                    chat["eval_count"] for chat in capped
                    if isinstance(chat.get("eval_count"), (int, float))
                ]),
                "output_limited_chars": numeric_summary([
                    chat["content_chars"] for chat in capped
                    if isinstance(chat.get("content_chars"), (int, float))
                ]),
                "capped": len(capped),
                "cold_runs": sum(1 for record in case_records if record.get("chat_cold")),
                "warm_runs": sum(1 for record in case_records if not record.get("chat_cold")),
            }
    return by_model


def chat_run(base: str, payload: dict, timeout: float) -> dict:
    started = time.perf_counter()
    result = {"content": "", "thinking": "", "first_content_ms": None,
              "events": [], "status": "error", "output_limit_reached": False}
    terminal = None
    try:
        with urllib.request.urlopen(make_request(base, "/api/chat", payload), timeout=timeout) as response:
            for line in response:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    result["malformed_line"] = line.decode("utf-8", errors="replace")
                    raise ValueError("Malformed JSON stream line")
                result["events"].append(event)
                if not isinstance(event, dict):
                    raise ValueError("Stream event is not an object")
                if event.get("error"):
                    raise ValueError(str(event["error"]))
                message = event.get("message") or {}
                if not isinstance(message, dict):
                    raise ValueError("Invalid stream message")
                for field in ("content", "thinking"):
                    value = message.get(field) or ""
                    if not isinstance(value, str):
                        raise ValueError(f"Invalid {field} field")
                    if field == "content" and value and result["first_content_ms"] is None:
                        result["first_content_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    result[field] += value
                if event.get("done") is True:
                    terminal = event
                    break
        if terminal is None:
            raise ValueError("Stream closed without terminal done event")
        result["terminal"] = terminal
        for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
            value = terminal.get(key)
            result[key + "_ms"] = value / 1_000_000 if isinstance(value, (int, float)) else None
        for key in ("prompt_eval_count", "eval_count", "done_reason"):
            result[key] = terminal.get(key)
        duration, count = terminal.get("eval_duration"), terminal.get("eval_count")
        result["output_tokens_per_second"] = count / duration * 1e9 if isinstance(count, (int, float)) and isinstance(duration, (int, float)) and duration > 0 else None
        if not result["content"].strip():
            raise ValueError("No nonempty content returned")
        result["content_chars"] = len(result["content"])
        result["content_words"] = len(result["content"].split())
        result["output_limit_reached"] = terminal.get("done_reason") == "length"
        if result["output_limit_reached"]:
            result["warning"] = "Output limit reached; answer may be incomplete"
        result["status"] = "completed_requires_quality_review"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True, help="Exact installed model names; no automatic downloads")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path, help="JSON file, or directory for timestamped JSON; default runtime/benchmarks")
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--think", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--keep-alive", default="1h")
    parser.add_argument("--timeout", type=float, default=300, help="Socket operation timeout in seconds")
    parser.add_argument("--embed-before-chat", action="store_true")
    parser.add_argument("--embed-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--embed-keep-alive", default="0", help="Keep-alive sent to /api/embed; use 0 for cold embedding probes")
    parser.add_argument("--case-set", choices=("synthetic", "travel"), default="synthetic")
    parser.add_argument("--case-ids", help="Comma-separated fixed case ids; defaults to the selected case set")
    parser.add_argument("--case-limit", type=int, help="Run only the first N selected cases for a small diagnostic")
    parser.add_argument("--retrieval-url", help="Optional app base URL for admin-only POST /api/rag-test timing")
    parser.add_argument(
        "--retrieval-admin-key",
        default=os.environ.get("ADMIN_API_KEY"),
        help="Admin key for --retrieval-url; defaults to ADMIN_API_KEY and is never saved",
    )
    parser.add_argument("--retrieval-top-k", type=int, default=4)
    parser.add_argument("--cleanup", action="store_true", help="Unload only models this benchmark requested that were not initially loaded")
    args = parser.parse_args()
    if args.runs < 1 or args.num_ctx < 1 or args.num_predict < 1 or args.timeout <= 0:
        parser.error("runs, context, predict and timeout must be positive")
    if args.case_limit is not None and args.case_limit < 1:
        parser.error("case-limit must be positive")
    if args.retrieval_top_k < 1 or args.retrieval_top_k > 20:
        parser.error("retrieval-top-k must be between 1 and 20")
    if args.retrieval_url and not args.retrieval_admin_key:
        parser.error("--retrieval-url requires --retrieval-admin-key or ADMIN_API_KEY")
    args.models = list(dict.fromkeys(args.models))
    if any('cloud' in model.rsplit(':', 1)[-1].lower() for model in args.models + [args.embed_model]):
        parser.error('Cloud models are outside this local benchmark')
    return args


def main() -> int:
    args = parse_args()
    cases = select_cases(args)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output = args.output or Path(__file__).resolve().parents[1] / "runtime" / "benchmarks"
    if output.suffix.lower() != ".json":
        output = output / f"benchmark_{timestamp}.json"
    # Refuse overwriting evidence from an earlier run, before making model calls.
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    report = {
        "created_utc": timestamp,
        "settings": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
            if key != "retrieval_admin_key"
        },
        "retrieval_admin_key_configured": bool(args.retrieval_admin_key),
        "case_set": args.case_set,
        "cases": [{"id": case["id"], "question": case["question"],
                   "history_turns": len(case.get("history", []))} for case in cases],
        "quality_warning": "First content is not necessarily a useful answer. Human review required.",
        "privacy_note": "Cases are fixed synthetic/public prompts; retrieved text is not saved in this report.",
        "models": {}, "retrieval": {}, "runs": [], "comparison": {}, "cleanup": [],
    }
    if args.case_set == "synthetic":
        report["messages"] = MESSAGES
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)

    def save_report():
        with output.open('w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    initial: set[str] = set()
    requested: set[str] = set()
    failed = False
    retrieval_contexts: dict[str, str | None] = {}
    try:
        report["initial_ps"] = request_json(args.base_url, "/api/ps", None, args.timeout)
        initial = names(report["initial_ps"])
        if initial:
            raise ValueError('Ollama must be idle with no loaded models before benchmarking; existing models were left untouched')
        report["installed_models"] = request_json(args.base_url, "/api/tags", None, args.timeout)
        needed = args.models + ([args.embed_model] if args.embed_before_chat else [])
        missing = set(needed) - names(report["installed_models"])
        if missing:
            raise ValueError(f"Models unavailable (no pull attempted): {sorted(missing)}")
        for model in dict.fromkeys(needed):
            identity = request_json(args.base_url, "/api/show", {"model": model}, args.timeout)
            if identity.get('remote_host') or identity.get('remote_model'):
                raise ValueError('Remote/cloud models are outside this local benchmark')
            template = identity.get("template", "")
            report["models"][model] = {"show": identity, "template_sha256": hashlib.sha256(template.encode("utf-8")).hexdigest()}

        for case in cases:
            if args.retrieval_url:
                try:
                    retrieval_record, context = retrieval_run(
                        args.retrieval_url,
                        args.base_url,
                        case,
                        args.retrieval_top_k,
                        args.timeout,
                        args.embed_model,
                        args.retrieval_admin_key,
                    )
                    retrieval_contexts[case["id"]] = context or None
                except Exception as exc:
                    retrieval_record = {
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "wall_ms": None,
                    }
                    retrieval_contexts[case["id"]] = None
                    failed = True
                report["retrieval"][case["id"]] = retrieval_record
            else:
                report["retrieval"][case["id"]] = {
                    "status": "not_requested",
                    "wall_ms": None,
                }
                retrieval_contexts[case["id"]] = None
        save_report()

        for model in args.models:
            for case in cases:
                messages = case_messages(case, retrieval_contexts[case["id"]])
                for index in range(1, args.runs + 1):
                    record = {
                        "model": model,
                        "case_id": case["id"],
                        "question": case["question"],
                        "run": index,
                    }
                    run_started = time.perf_counter()
                    report["runs"].append(record)
                    try:
                        before_ps = request_json(args.base_url, "/api/ps", None, args.timeout)
                        before_names = names(before_ps)
                        record["before_loaded_models"] = sorted(before_names)
                        record["chat_cold"] = model not in before_names
                        if args.embed_before_chat:
                            embed_payload = {
                                "model": args.embed_model,
                                "input": [case["question"]],
                                "keep_alive": args.embed_keep_alive,
                            }
                            record["embedding_request"] = {
                                "model": args.embed_model,
                                "input_count": 1,
                                "input_sha256": hashlib.sha256(
                                    case["question"].encode("utf-8")
                                ).hexdigest(),
                                "keep_alive": args.embed_keep_alive,
                            }
                            record["embedding_cold"] = args.embed_model not in before_names
                            requested.add(args.embed_model)
                            started = time.perf_counter()
                            embedding = request_json(args.base_url, "/api/embed", embed_payload, args.timeout)
                            record["embed_wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
                            vectors = embedding.get("embeddings", [])
                            if len(vectors) != 1 or not isinstance(vectors[0], list) or not vectors[0]:
                                raise ValueError("Expected one nonempty embedding vector")
                            record["embedding_dimensions"] = len(vectors[0])
                            record["after_embedding_loaded_models"] = sorted(
                                names(request_json(args.base_url, "/api/ps", None, args.timeout))
                            )
                        payload = {
                            "model": model,
                            "messages": messages,
                            "stream": True,
                            "think": args.think,
                            "keep_alive": args.keep_alive,
                            "options": {
                                "num_ctx": args.num_ctx,
                                "num_predict": args.num_predict,
                                "temperature": args.temperature,
                            },
                        }
                        record["chat_request"] = {
                            "model": model,
                            "case_id": case["id"],
                            "stream": True,
                            "think": args.think,
                            "keep_alive": args.keep_alive,
                            "options": payload["options"],
                            **safe_message_summary(messages),
                        }
                        requested.add(model)
                        record["chat"] = chat_run(args.base_url, payload, args.timeout)
                        failed |= record["chat"]["status"] == "error"
                        record["after_loaded_models"] = sorted(
                            names(request_json(args.base_url, "/api/ps", None, args.timeout))
                        )
                        print(
                            f"model={model} case={case['id']} run={index} "
                            f"status={record['chat']['status']} "
                            f"wall_ms={record['chat']['wall_ms']}".encode(
                                "ascii", "backslashreplace"
                            ).decode("ascii"),
                            flush=True,
                        )
                    except Exception as exc:
                        record["error"] = f"{type(exc).__name__}: {exc}"
                        failed = True
                    finally:
                        record["total_wall_ms"] = round(
                            (time.perf_counter() - run_started) * 1000, 3
                        )
                        save_report()
            if args.cleanup:
                result = request_json(args.base_url, '/api/generate', {'model': model, 'keep_alive': 0, 'stream': False}, args.timeout)
                report['cleanup'].append({'model': model, 'result': result, 'phase': 'between_models'})
                requested.discard(model)
                save_report()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        failed = True
    finally:
        if args.cleanup:
            for model in sorted(requested - initial):
                try:
                    result = request_json(args.base_url, "/api/generate", {"model": model, "keep_alive": 0, "stream": False}, args.timeout)
                    report["cleanup"].append({"model": model, "result": result})
                except Exception as exc:
                    report["cleanup"].append({"model": model, "error": f"{type(exc).__name__}: {exc}"})
                    failed = True
        report["failed"] = failed
        report["comparison"] = comparison_summary(report["runs"])
        save_report()
        for model, summary in report["comparison"].items():
            cases_count = len(summary["cases"])
            completed = sum(item["completed"] for item in summary["cases"].values())
            capped = sum(item["capped"] for item in summary["cases"].values())
            print(
                f"comparison model={model} cases={cases_count} completed={completed} capped={capped}".encode(
                    "ascii", "backslashreplace"
                ).decode("ascii"),
                flush=True,
            )
        print(f"Evidence: {output}".encode("ascii", "backslashreplace").decode("ascii"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
