"""Discover, enqueue, reconcile, and optionally process VietSage PDF sources."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.travel_document_processor import process_travel_pdf  # noqa: E402


DEMO_DATA_ROOT = (PROJECT_ROOT / "data" / "demo").resolve()


@dataclass(frozen=True)
class DiscoveredPDF:
    path: Path
    relative_path: str
    source_key: str
    destination: dict[str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Đồng bộ dữ liệu PDF của VietSage")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--manifest", type=Path, default=PROJECT_ROOT / "data_sources.json"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Chỉ parse, không ghi dữ liệu"
    )
    parser.add_argument(
        "--enqueue-only", action="store_true", help="Tạo job nhưng chưa chạy worker"
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Đánh dấu deleted các file folder-managed đã biến mất",
    )
    return parser.parse_args()


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"folders": {}, "files": {}}
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _fallback_destination(folder_or_filename: str) -> dict[str, str]:
    from app.services.destination_catalog import slugify_destination

    name = Path(folder_or_filename).stem.replace("_", " ").strip()
    return {
        "slug": slugify_destination(name),
        "name": name,
        "province_name": name,
        "destination_type": "region",
    }


def discover_pdfs(data_root: Path, manifest_path: Path) -> list[DiscoveredPDF]:
    manifest = _load_manifest(manifest_path)
    folder_map = {key.casefold(): value for key, value in manifest.get("folders", {}).items()}
    file_map = {key.casefold(): value for key, value in manifest.get("files", {}).items()}
    discovered: list[DiscoveredPDF] = []
    for path in sorted(data_root.rglob("*.pdf")):
        # A broad real-data scan must not silently absorb the synthetic corpus.
        # The demo launcher passes a root inside data/demo explicitly.
        try:
            path.resolve().relative_to(DEMO_DATA_ROOT)
        except ValueError:
            pass
        else:
            try:
                data_root.resolve().relative_to(DEMO_DATA_ROOT)
            except ValueError:
                continue
        relative = path.relative_to(data_root).as_posix()
        top_folder = relative.split("/", 1)[0] if "/" in relative else ""
        spec = file_map.get(path.name.casefold())
        if spec is None and top_folder:
            spec = folder_map.get(top_folder.casefold())
        if spec is None:
            spec = _fallback_destination(top_folder or path.stem)
        discovered.append(
            DiscoveredPDF(
                path=path,
                relative_path=relative,
                source_key=f"folder:{relative.casefold()}",
                destination=dict(spec),
            )
        )
    return discovered


async def dry_run(files: list[DiscoveredPDF]) -> int:
    failures = 0
    for item in files:
        try:
            processed = await asyncio.to_thread(
                process_travel_pdf, str(item.path), item.path.name
            )
            print(
                f"[OK] {item.relative_path} | destination={item.destination['slug']} "
                f"tour={processed.tour_code or '-'} chunks={len(processed.chunks)}"
            )
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {item.relative_path}: {exc}")
    return failures


async def enqueue(files: list[DiscoveredPDF], prune: bool) -> tuple[int, int, int]:
    from app.core.database import SessionLocal
    from app.core.schema import initialize_database
    from app.models import destination as destination_model  # noqa: F401
    from app.models import knowledge as knowledge_model  # noqa: F401
    from app.services.destination_catalog import ensure_destination
    from app.services.travel_ingestion import enqueue_pdf_path, mark_missing_sources_deleted

    initialize_database()
    db = SessionLocal()
    queued = skipped = 0
    expected_source_keys = {item.source_key for item in files}
    try:
        for item in files:
            destination = ensure_destination(db, **item.destination)
            job, version, changed = enqueue_pdf_path(
                path=item.path,
                destination=destination,
                db=db,
                source_key=item.source_key,
                filename=item.path.name,
                managed_source_keys=expected_source_keys,
            )
            if changed:
                queued += 1
                print(f"[QUEUED] {item.relative_path}: job={job.id} version={version.version}")
            else:
                skipped += 1
                print(f"[SKIPPED] {item.relative_path}: nội dung không đổi")
        deleted = (
            mark_missing_sources_deleted(db, expected_source_keys)
            if prune
            else 0
        )
        return queued, skipped, deleted
    finally:
        db.close()


async def main() -> int:
    args = parse_args()
    files = discover_pdfs(args.data_root, args.manifest)
    print(f"Tìm thấy {len(files)} PDF trong {args.data_root.resolve()}")
    if not files:
        return 1
    if args.dry_run:
        failures = await dry_run(files)
        print(f"Dry-run: success={len(files) - failures}, failed={failures}")
        return 1 if failures else 0

    queued, skipped, deleted = await enqueue(files, args.prune)
    print(f"Đồng bộ: queued={queued}, skipped={skipped}, deleted={deleted}")
    if args.enqueue_only:
        return 0

    from app.services.travel_ingestion import run_pending_jobs

    completed, failed = await run_pending_jobs(stop_when_empty=True)
    print(f"Worker: completed={completed}, failed_attempts={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
