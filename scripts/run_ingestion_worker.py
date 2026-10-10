"""Run the durable ingestion worker continuously or until the queue is empty."""
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VietSage ingestion worker")
    parser.add_argument(
        "--once", action="store_true", help="Thoát khi hàng đợi đang trống"
    )
    return parser.parse_args()


async def main() -> int:
    from app.core.schema import initialize_database
    from app.models import destination as destination_model  # noqa: F401
    from app.models import knowledge as knowledge_model  # noqa: F401
    from app.services.travel_ingestion import run_pending_jobs

    args = parse_args()
    initialize_database()
    completed, failed = await run_pending_jobs(stop_when_empty=args.once)
    print(f"Worker dừng: completed={completed}, failed_attempts={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
