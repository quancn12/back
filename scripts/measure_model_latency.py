"""Measure what a guest actually waits for, and where the models are sitting.

Answers one question that a specification cannot: does the configured chat model
fit in this machine's VRAM, or is part of it running on the CPU? Ollama reports
both the model's total size and how much of it is resident on the GPU, and the
difference explains most of the wait before the first word appears.

Run it against a VietSage that is already up. It changes nothing: no settings
are written, no models are pulled, and the questions are fixed public ones about
the tour library, so no guest message is involved.

    .\\.venv\\Scripts\\python.exe scripts\\measure_model_latency.py

Compare configurations by restarting the server with different environment
variables, never by editing .env, so the machine is left as it was found:

    $env:OLLAMA_EMBED_KEEP_ALIVE='24h'

Standard library only, so it runs on a fresh clone.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import time
import urllib.error
import urllib.request

QUESTIONS = [
    "Suối Giàng có gì đặc biệt?",
    "Trạm Tấu có suối khoáng nóng không?",
    "Mù Cang Chải ruộng bậc thang mùa nào đẹp?",
    "Hồ Thác Bà đi mấy ngày là đủ?",
]


def get_json(url: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def gpu_report() -> str:
    """Name and memory of the GPU, straight from the driver."""
    if platform.system() != "Windows" and platform.system() != "Linux":
        return "khong doc duoc"
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "khong co nvidia-smi"
    line = out.stdout.strip().splitlines()
    return line[0].strip() if line else "khong co GPU NVIDIA"


def residency(ollama_url: str) -> list[dict]:
    """Which models Ollama holds, and how much of each is on the GPU."""
    try:
        models = get_json(f"{ollama_url}/api/ps").get("models", [])
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    rows = []
    for model in models:
        total = float(model.get("size") or 0)
        on_gpu = float(model.get("size_vram") or 0)
        rows.append({
            "name": model.get("name", "?"),
            "total_gb": total / 1e9,
            "gpu_gb": on_gpu / 1e9,
            "gpu_percent": (on_gpu / total * 100.0) if total else 0.0,
        })
    return rows


def print_residency(app_url: str, ollama_url: str, label: str) -> None:
    rows = residency(ollama_url)
    if not rows:
        print(f"  [{label}] Ollama khong giu model nao")
        return
    for row in rows:
        verdict = "toan bo tren GPU" if row["gpu_percent"] >= 99.5 else "CO PHAN CHAY BANG CPU"
        print(
            f"  [{label}] {row['name']:<26} {row['total_gb']:5.2f} GB, "
            f"tren GPU {row['gpu_gb']:5.2f} GB ({row['gpu_percent']:5.1f}%) — {verdict}"
        )


def ask(app_url: str, question: str, timeout: float) -> tuple[float, float, int]:
    """Send one question and time the first word and the whole answer."""
    request = urllib.request.Request(
        f"{app_url}/api/chat/stream",
        data=json.dumps({"message": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    first: float | None = None
    pieces = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:])
            except json.JSONDecodeError:
                continue
            if "text" in payload:
                pieces += 1
                if first is None:
                    first = time.perf_counter() - started
    total = time.perf_counter() - started
    return (first if first is not None else total), total, pieces


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--label", default="", help="Ten cau hinh dang do, in ra cho de doi chieu")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--warmup", action="store_true",
                        help="Hoi mot cau bo di truoc khi do, de bo qua lan nap dau tien")
    args = parser.parse_args()

    app_url = args.app_url.rstrip("/")
    ollama_url = args.ollama_url.rstrip("/")

    try:
        live = get_json(f"{app_url}/api/health/live", timeout=10)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Khong ket noi duoc toi {app_url}: {exc}")
        print("Chay truoc:  .\\scripts\\host_local.ps1 -LocalOnly -StopExisting")
        return 1
    if live.get("status") != "ok":
        print(f"VietSage chua san sang tai {app_url}: {live}")
        return 1

    try:
        health = get_json(f"{app_url}/api/health/ollama", timeout=30)
    except (urllib.error.URLError, TimeoutError, OSError):
        health = {}

    print("=" * 74)
    if args.label:
        print(f"CAU HINH: {args.label}")
    print(f"GPU     : {gpu_report()}")
    print(f"May     : {platform.node()}")
    print("=" * 74)
    print_residency(app_url, ollama_url, "truoc khi do")

    if args.warmup:
        print("  (hoi mot cau bo di de bo qua lan nap dau tien)")
        ask(app_url, QUESTIONS[0], args.timeout)

    firsts: list[float] = []
    totals: list[float] = []
    print()
    for question in QUESTIONS:
        first, total, pieces = ask(app_url, question, args.timeout)
        firsts.append(first)
        totals.append(total)
        print(f"  chu dau {first:6.2f}s | tong {total:7.2f}s | {pieces:4d} manh | {question}")

    print()
    print(f"  CHU DAU TIEN : trung binh {statistics.mean(firsts):6.2f}s  "
          f"(thap nhat {min(firsts):.2f}s, cao nhat {max(firsts):.2f}s)")
    print(f"  TONG THOI GIAN: trung binh {statistics.mean(totals):6.2f}s  "
          f"(thap nhat {min(totals):.2f}s, cao nhat {max(totals):.2f}s)")
    print()
    print_residency(app_url, ollama_url, "sau khi do ")
    if health:
        print(f"  model chat dang cau hinh: san sang={health.get('chat_model_ready')}, "
              f"embedding san sang={health.get('embed_model_ready')}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
