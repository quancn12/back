"""Call every API route once, for real, and save the request/response pairs.

docs/human/API.md shows a request and a response example for each route. They
are not invented: this script runs the app in-process (FastAPI TestClient with
its startup) against a copy of a database, with the real Ollama model, walks
the routes in the order a client would use them, and writes what was sent and
what came back to scripts/api_examples.json. Tokens, keys and PINs issued by
reception are shortened before saving. Then run scripts/export_api_docs.py.

Usage:
  python scripts/capture_api_examples.py --database path/to/copy.db
The database must be at Alembic head; it is modified (demo rows, test
partner, uploads), so pass a copy. Ollama must be running.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "scripts" / "api_examples.json"
ADMIN_KEY = "vi-du-khoa-admin"
DEMO_PDF = PROJECT_ROOT / "data" / "demo" / "corpus" / "demo_facts.pdf"
SECRET_FIELDS = {"csrf_token", "access_token", "api_key", "token", "pin"}


def mask(value: str) -> str:
    return value if len(value) <= 12 else value[:12] + "…"


def _secret(key: str, value: str) -> str:
    # A PIN is short, so it keeps only two digits rather than twelve characters.
    return value[:2] + "…" if key == "pin" else mask(value)


def redact(value):
    if isinstance(value, dict):
        return {k: (_secret(k, v) if k in SECRET_FIELDS and isinstance(v, str) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class Recorder:
    def __init__(self, client):
        self.client = client
        self.examples: dict[str, list[dict]] = {}

    def call(self, key, method, url, *, headers=None, json_body=None, params=None, files=None,
             client=None, label=None, follow=False, show_headers=None):
        client = client or self.client
        response = client.request(method, url, headers=headers, json=json_body, params=params,
                                  files=files, follow_redirects=follow)
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type or "manifest+json" in content_type:
            body = response.json() if response.content else None
            kind = "json"
        elif "text/event-stream" in content_type:
            body, kind = response.text, "sse"
        elif response.status_code in (301, 302, 303, 307):
            body, kind = None, "redirect"
        else:
            body, kind = None, "file"
        shown = {}
        for name, value in (headers or {}).items():
            shown[name] = value if name in {"Content-Type", "X-Partner-User"} else _masked_header(name, value)
        record = {
            "label": label,
            "url": url,
            "request": {"headers": shown, "query": params, "json": redact(json_body) if json_body is not None else None,
                        "multipart": sorted(files) if files else None},
            "status": response.status_code,
            "content_type": content_type.split(";")[0],
            "kind": kind,
            "response": redact(body) if kind == "json" else body,
            "location": re.sub(r"(state|nonce)=([^&]{6})[^&]*", r"\1=\2…", response.headers.get("location") or "") or None,
            "response_headers": {h: response.headers[h] for h in (show_headers or []) if h in response.headers},
        }
        self.examples.setdefault(key, []).append(record)
        if response.status_code >= 500:
            print(f"  ! {key} -> {response.status_code}: {response.text[:200]}")
        else:
            print(f"  {key} -> {response.status_code}")
        return response


def _masked_header(name, value):
    if name == "X-Admin-Key":
        return "<ADMIN_API_KEY>"
    if name == "Authorization":
        return "Bearer " + mask(value.split(" ", 1)[1])
    return mask(value)


def sse_events(text):
    events = []
    for block in text.strip().split("\n\n"):
        event = re.search(r"^event: (.*)$", block, re.M)
        data = re.search(r"^data: (.*)$", block, re.M)
        if event and data:
            events.append((event.group(1), json.loads(data.group(1))))
    return events


def run(database: Path) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="vietsage-api-examples-"))
    os.environ.update({
        "DATABASE_URL": f"sqlite:///{database.resolve().as_posix()}",
        "APP_ENV": "development",
        "HOTEL_MODE_ENABLED": "true",
        "ADMIN_API_KEY": ADMIN_KEY,
        "CHROMA_PERSIST_DIR": str(workdir / "chroma"),
        "SOURCE_STORAGE_DIR": str(workdir / "sources"),
        "CATALOGUE_PATH": str(PROJECT_ROOT / "datasets" / "demo" / "catalogue.json"),
        "PROACTIVE_RESEARCH_ENABLED": "true",
        "PROACTIVE_FIXTURE_ENABLED": "true",
        "GOOGLE_CLIENT_ID": "vi-du.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "vi-du-bi-mat",
        "OLLAMA_CHAT_MODEL": os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:3b"),
        "OLLAMA_WARMUP_ENABLED": "false",
    })
    os.chdir(workdir)  # no local .env
    sys.path.insert(0, str(PROJECT_ROOT))
    from fastapi.testclient import TestClient

    import app.main as main

    # A server error is recorded as the route's answer instead of stopping the run.
    with TestClient(main.app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as client:
        r = Recorder(client)
        admin = {"X-Admin-Key": ADMIN_KEY}
        print("pages")
        for key, url in (("GET /", "/"), ("GET /app", "/app"), ("GET /chat-ui", "/chat-ui"), ("GET /hotel-chat", "/hotel-chat"),
                         ("GET /manifest.webmanifest", "/manifest.webmanifest"), ("GET /sw.js", "/sw.js"),
                         ("GET /app-assets/{asset_path}", "/app-assets/scripts/motion.js"), ("GET /api/info", "/api/info")):
            r.call(key, "GET", url)
        r.call("GET /operations", "GET", "/operations")

        print("health")
        for name in ("live", "database", "ollama", "provider"):
            r.call(f"GET /api/health/{name}", "GET", f"/api/health/{name}")
        r.call("POST /api/health/chat-test", "POST", "/api/health/chat-test", headers=admin, json_body={"message": "Xin chào"})
        r.call("POST /api/health/embed-test", "POST", "/api/health/embed-test", headers=admin, json_body={"text": "Hồ Thác Bà"})

        print("auth")
        csrf = r.call("GET /api/v1/auth/session", "GET", "/api/v1/auth/session").json()["csrf_token"]
        csrf = r.call("POST /api/v1/auth/signup", "POST", "/api/v1/auth/signup", headers={"X-CSRF-Token": csrf},
                      json_body={"email": "khach.demo@example.com", "password": "mat-khau-dai-it-nhat-15", "display_name": "Khách Demo"}).json()["csrf_token"]
        r.call("GET /api/v1/auth/me", "GET", "/api/v1/auth/me")
        csrf = r.call("POST /api/v1/auth/logout", "POST", "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).json()["csrf_token"]
        csrf = r.call("POST /api/v1/auth/login", "POST", "/api/v1/auth/login", headers={"X-CSRF-Token": csrf},
                      json_body={"email": "khach.demo@example.com", "password": "mat-khau-dai-it-nhat-15"}).json()["csrf_token"]
        start = r.call("GET /api/v1/auth/google/start", "GET", "/api/v1/auth/google/start")
        state = re.search(r"[?&]state=([^&]+)", start.headers.get("location", "")).group(1)
        r.call("GET /api/v1/auth/google/callback", "GET", "/api/v1/auth/google/callback",
               params={"state": state, "error": "access_denied"})
        csrf = client.get("/api/v1/auth/session").json()["csrf_token"]
        write = {"X-CSRF-Token": csrf}

        print("destinations and documents")
        r.call("POST /api/destinations", "POST", "/api/destinations", headers=admin,
               json_body={"slug": "vi-du-da-lat", "name": "Đà Lạt", "destination_type": "city", "province_name": "Lâm Đồng", "aliases": "Da Lat,Dalat"})
        r.call("GET /api/destinations", "GET", "/api/destinations", params={"country_code": "VN"})
        r.call("GET /api/destinations/{slug}", "GET", "/api/destinations/vi-du-da-lat")
        with DEMO_PDF.open("rb") as pdf:
            upload = r.call("POST /api/destinations/{slug}/documents", "POST", "/api/destinations/vi-du-da-lat/documents",
                            headers=admin, files={"file": ("gioi-thieu-da-lat.pdf", pdf.read(), "application/pdf")}).json()
        document_id, job_id = upload["source"]["id"], upload["job"]["id"]
        try:
            from app.services.travel_ingestion import run_pending_jobs
            client.portal.call(run_pending_jobs)
        except Exception as exc:  # noqa: BLE001 - the example then shows a pending job
            print(f"  (ingestion not run: {type(exc).__name__})")
        r.call("GET /api/destinations/{slug}/documents", "GET", "/api/destinations/vi-du-da-lat/documents")
        r.call("GET /api/documents/{document_id}/versions", "GET", f"/api/documents/{document_id}/versions")
        r.call("GET /api/ingestion-jobs", "GET", "/api/ingestion-jobs", params={"limit": 2})
        r.call("GET /api/ingestion-jobs/{job_id}", "GET", f"/api/ingestion-jobs/{job_id}")

        print("durable travel chat")
        first = r.call("POST /api/chat", "POST", "/api/chat", json_body={"message": "Hồ Thác Bà có gì chơi?"},
                       label="lượt 1").json()
        r.call("POST /api/chat", "POST", "/api/chat", label="lượt 2, gửi lại conversation_id",
               json_body={"message": "Đi thuyền ở đó mất bao lâu?", "conversation_id": first["conversation_id"]})
        r.call("POST /api/chat/stream", "POST", "/api/chat/stream",
               json_body={"message": "Còn ăn gì ở đó?", "conversation_id": first["conversation_id"]})
        r.call("POST /api/rag-test", "POST", "/api/rag-test", headers=admin, json_body={"question": "Hồ Thác Bà có gì chơi?", "top_k": 2})

        print("unified travel")
        r.call("GET /api/v1/media/catalogue/{name}", "GET", "/api/v1/media/catalogue/venue_demo_ben_lau_007_2.svg")
        scenarios = r.call("GET /api/v1/demo/scenarios", "GET", "/api/v1/demo/scenarios").json()["scenarios"]
        conversation = r.call("POST /api/v1/conversations", "POST", "/api/v1/conversations", headers=write,
                              json_body={"country_code": "VN", "locale": "vi"}).json()["id"]
        r.call("GET /api/v1/conversations/{conversation_id}", "GET", f"/api/v1/conversations/{conversation}")
        stream = r.call("POST /api/v1/conversations/{conversation_id}/messages/stream", "POST",
                        f"/api/v1/conversations/{conversation}/messages/stream", headers=write,
                        json_body={"message": "Hồ Thác Bà có gì chơi?", "mode": "TRAVEL", "locale": "vi"}, label="TRAVEL")
        message_id = sse_events(stream.text)[-1][1].get("message_id")
        r.call("GET /api/v1/feedback/reasons", "GET", "/api/v1/feedback/reasons")
        feedback = f"/api/v1/conversations/{conversation}/messages/{message_id}/feedback"
        key = "{} /api/v1/conversations/{{conversation_id}}/messages/{{message_id}}/feedback"
        r.call(key.format("PUT"), "PUT", feedback, headers=write, json_body={"rating": "down", "reason": "off_topic"})
        r.call(key.format("GET"), "GET", feedback)
        r.call(key.format("DELETE"), "DELETE", feedback, headers=write)
        client.put(feedback, headers=write, json={"rating": "up", "reason": "accurate"})
        r.call("GET /api/v1/feedback/summary", "GET", "/api/v1/feedback/summary", headers=admin, params={"context_type": "TRAVEL"})
        r.call("PUT /api/v1/conversations/{conversation_id}/consents", "PUT", f"/api/v1/conversations/{conversation}/consents",
               headers=write, json_body={"purpose": "PERSONALIZED_RECOMMENDATIONS", "status": "GRANTED"})
        r.call("GET /api/v1/conversations/{conversation_id}/consents", "GET", f"/api/v1/conversations/{conversation}/consents")

        print("stay and hotel services")
        room = next(item for item in scenarios if item["room_number"] == "305")
        connection = r.call("POST /api/v1/stays/connect", "POST", "/api/v1/stays/connect", headers=write,
                            json_body={"connect_code": room["connect_code"]}).json()["connection_id"]
        verified = r.call("POST /api/v1/stays/verify", "POST", "/api/v1/stays/verify", headers=write,
                          json_body={"connection_id": connection, "pin": room["pin"]}).json()
        stay = {"X-Stay-Token": verified["access_token"]}
        r.call("GET /api/v1/stays/active", "GET", "/api/v1/stays/active", headers=stay)
        services = r.call("GET /api/v1/hotel/services", "GET", "/api/v1/hotel/services", headers=stay).json()
        r.call("GET /api/v1/hotel/nearby", "GET", "/api/v1/hotel/nearby", headers=stay)
        service = next(item for item in services if item.get("requires_confirmation")) if services else None
        r.call("POST /api/v1/service-requests", "POST", "/api/v1/service-requests", headers={**write, **stay},
               json_body={"service_item_id": service["id"], "quantity": 1, "note": "Sau 9 giờ sáng",
                          "confirmed": True, "idempotency_key": "vi-du-yeu-cau-0001"})
        r.call("GET /api/v1/service-requests", "GET", "/api/v1/service-requests", headers=stay)
        hotel_conversation = client.post("/api/v1/conversations", headers={**write, **stay},
                                         json={"locale": "vi", "preferred_mode": "HOTEL"}).json()["id"]
        r.call("POST /api/v1/conversations/{conversation_id}/messages/stream", "POST",
               f"/api/v1/conversations/{hotel_conversation}/messages/stream", headers={**write, **stay},
               json_body={"message": "Cho tôi thêm khăn tắm", "mode": "HOTEL", "locale": "vi"}, label="HOTEL")
        hotel_slug = room["hotel_slug"]
        r.call("POST /api/v1/admin/hotels/{hotel_slug}/rooms/{room_number}/stay-pin", "POST",
               f"/api/v1/admin/hotels/{hotel_slug}/rooms/410/stay-pin", headers=admin)
        r.call("POST /api/v1/admin/hotels/{hotel_slug}/rooms/{room_number}/pin-unlock", "POST",
               f"/api/v1/admin/hotels/{hotel_slug}/rooms/410/pin-unlock", headers=admin)
        r.call("DELETE /api/v1/stays/active", "DELETE", "/api/v1/stays/active", headers=stay)

        print("partner api")
        created = r.call("POST /api/v1/admin/partners", "POST", "/api/v1/admin/partners", headers=admin,
                         json_body={"slug": "tour-abc", "name": "Tour ABC", "rate_limit_per_minute": 120}).json()
        r.call("GET /api/v1/admin/partners", "GET", "/api/v1/admin/partners", headers=admin)
        key = r.call("POST /api/v1/admin/partners/{slug}/rotate-key", "POST", "/api/v1/admin/partners/tour-abc/rotate-key",
                     headers=admin).json()["api_key"]
        del created
        partner = TestClient(main.app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False)
        ph = {"Authorization": f"Bearer {key}", "X-Partner-User": "khach-42"}
        pconv = r.call("POST /api/v1/partner/conversations", "POST", "/api/v1/partner/conversations", client=partner,
                       headers=ph, json_body={"locale": "vi"}).json()["id"]
        r.call("POST /api/v1/partner/conversations/{conversation_id}/messages", "POST",
               f"/api/v1/partner/conversations/{pconv}/messages", client=partner, headers=ph,
               json_body={"message": "Lên lịch trình 2 ngày 1 đêm ở Hồ Thác Bà"})
        r.call("POST /api/v1/partner/conversations/{conversation_id}/messages/stream", "POST",
               f"/api/v1/partner/conversations/{pconv}/messages/stream", client=partner, headers=ph,
               json_body={"message": "Hồ Thác Bà có gì chơi?"})
        r.call("GET /api/v1/partner/conversations/{conversation_id}/messages", "GET",
               f"/api/v1/partner/conversations/{pconv}/messages", client=partner, headers=ph)
        r.call("DELETE /api/v1/partner/conversations/{conversation_id}", "DELETE",
               f"/api/v1/partner/conversations/{pconv}", client=partner, headers=ph)
        r.call("POST /api/v1/admin/partners/{slug}/deactivate", "POST", "/api/v1/admin/partners/tour-abc/deactivate", headers=admin)

        print("operations")
        r.call("GET /api/v1/operations/summary", "GET", "/api/v1/operations/summary", headers=admin)

        print("proactive research")
        r.call("GET /api/v1/proactive/availability", "GET", "/api/v1/proactive/availability")
        token = r.call("POST /api/v1/proactive/sessions", "POST", "/api/v1/proactive/sessions").json()["token"]
        research = {"X-Research-Token": token}
        r.call("POST /api/v1/proactive/fixture", "POST", "/api/v1/proactive/fixture", headers=research, json_body={"scenario": "question"})
        r.call("PUT /api/v1/proactive/mode", "PUT", "/api/v1/proactive/mode", headers=research, json_body={"mode": "LIVE", "consent": True})
        r.call("GET /api/v1/proactive/mode", "GET", "/api/v1/proactive/mode", headers=research)
        polled = r.call("POST /api/v1/proactive/poll", "POST", "/api/v1/proactive/poll", headers=research,
                        json_body={"attention_available": True}).json()
        event = polled.get("event") or {}
        r.call("POST /api/v1/proactive/receipt", "POST", "/api/v1/proactive/receipt", headers=research,
               json_body={"event_id": event.get("id") or "khong-co-su-kien", "attention_available": True})
        r.call("POST /api/v1/proactive/reply", "POST", "/api/v1/proactive/reply", headers=research,
               json_body={"transaction_id": event.get("transaction_id") or "khong-co-giao-dich", "reply_id": "tra-loi-0001",
                          "answer": "yes", "attention_available": True})

        print("legacy hotel")
        r.call("POST /api/hotels", "POST", "/api/hotels", headers=admin,
               json_body={"slug": "khach-san-vi-du", "name": "Khách sạn Ví Dụ", "city": "Yên Bái"})
        r.call("GET /api/hotels", "GET", "/api/hotels")
        r.call("GET /api/hotels/{slug}", "GET", "/api/hotels/khach-san-vi-du")
        with DEMO_PDF.open("rb") as pdf:
            r.call("POST /api/hotels/{slug}/documents", "POST", "/api/hotels/khach-san-vi-du/documents", headers=admin,
                   files={"file": ("noi-quy.pdf", pdf.read(), "application/pdf")})
        r.call("GET /api/hotels/{slug}/documents", "GET", "/api/hotels/khach-san-vi-du/documents")
        r.call("POST /api/hotels/{slug}/rag-test", "POST", "/api/hotels/khach-san-vi-du/rag-test", headers=admin,
               json_body={"question": "Giờ trả phòng là mấy giờ?", "top_k": 2})
        r.call("POST /api/hotels/{slug}/chat", "POST", "/api/hotels/khach-san-vi-du/chat", json_body={"message": "Giờ trả phòng là mấy giờ?"})
        r.call("POST /api/hotels/{slug}/chat/stream", "POST", "/api/hotels/khach-san-vi-du/chat/stream",
               json_body={"message": "Khách sạn có chỗ đỗ xe không?"})

        print("errors")
        r.call("_errors", "POST", "/api/chat", json_body={}, label="422")
        r.call("_errors", "GET", "/api/destinations/khong-co-diem-den-nay", label="404")
        r.call("_errors", "POST", "/api/v1/conversations", json_body={"locale": "vi"}, label="403")
        r.call("_errors", "POST", "/api/destinations", headers={"X-Admin-Key": "sai-khoa"},
               json_body={"slug": "x-y", "name": "X"}, label="401-admin")
        r.call("_errors", "POST", "/api/v1/partner/conversations", client=partner,
               headers={"Authorization": "Bearer vsk_00000000_" + "a" * 43, "X-Partner-User": "khach-42"},
               json_body={}, label="401-partner")

        print("clean up")
        r.call("DELETE /api/documents/{document_id}", "DELETE", f"/api/documents/{document_id}", headers=admin)
    shutil.rmtree(workdir, ignore_errors=True)
    return r.examples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True, help="SQLite copy at Alembic head (it is modified)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    examples = run(args.database)
    output.write_text(json.dumps(examples, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(examples)} routes to {output}")


if __name__ == "__main__":
    main()
