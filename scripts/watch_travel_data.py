"""Continuously reconcile the data folder and process newly queued revisions."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.ingest_travel_data import discover_pdfs, enqueue  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Theo dõi folder dữ liệu VietSage")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--manifest", type=Path, default=PROJECT_ROOT / "data_sources.json"
    )
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument(
        "--prune", action="store_true", help="Tombstone file đã xóa khỏi folder"
    )
    return parser.parse_args()


async def main() -> int:
    from app.core.schema import initialize_database
    from app.models import destination as destination_model  # noqa: F401
    from app.models import knowledge as knowledge_model  # noqa: F401
    from app.services.travel_ingestion import run_pending_jobs

    args = parse_args()
    initialize_database()
    print(
        f"Đang theo dõi {args.data_root.resolve()} mỗi {args.interval:g} giây. "
        "Nhấn Ctrl+C để dừng."
    )
    while True:
        files = discover_pdfs(args.data_root, args.manifest)
        queued, skipped, deleted = await enqueue(files, args.prune)
        if queued or deleted:
            completed, failed = await run_pending_jobs(stop_when_empty=True)
            print(
                f"[SYNC] queued={queued} skipped={skipped} deleted={deleted} "
                f"completed={completed} failed_attempts={failed}"
            )
        await asyncio.sleep(max(2.0, args.interval))


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Đã dừng theo dõi dữ liệu.")
