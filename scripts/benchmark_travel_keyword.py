"""Measure Travel keyword retrieval: FTS5 index versus the compatibility scan.

Roadmap C3 asks for golden queries and retrieval time before relying on the
SQLite FTS5 index. This runs the same questions through both paths of
``TravelVectorStore`` on an ingested database and reports hit@1, hit@3, MRR and
latency. The "scan" path is the pre-index behaviour (every active chunk scored
in Python); "fts" is the migration-owned index added in revision 20260923_0010.

The golden answers come from the corpus itself, so nothing is hand-labelled:

* a tour-code question expects the one PDF whose file name carries that code;
* a proper-name question expects every PDF whose text contains that name.

The tour PDFs are not in Git, so this runs where the real corpus is ingested:

    python scripts/benchmark_travel_keyword.py --database runtime/verify/verify.db
    python scripts/benchmark_travel_keyword.py --database <db> --embeddings

The database must be at the Alembic head (run ``alembic upgrade head`` on a copy
first). ``--embeddings`` also measures the hybrid path with Ollama query
embeddings; without it only the no-embedding fallback is measured.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Distinctive names from the tour programmes. Common names (Hồ Thác Bà, Yên
# Bái) are left out: nearly every PDF contains them, so they test nothing.
PROPER_NAMES = [
    "Động Thủy Tiên",
    "Vũ Linh Palm House",
    "Khu du lịch sinh thái Ruby",
    "Khu du lịch Bảo Ngọc",
    "La Pán Tẩn",
    "Le Champ Tú Lệ",
    "Bản Sà Rèn",
    "An Bình Village",
    "Lau Camping",
    "Đền Mẫu",
    "Đèo Khau Phạ",
    "Bản Hốc",
]
CODE_PATTERN = re.compile(r"^(HV[A-Z]{2} \d{4}-\d{2})\b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", required=True, help="SQLite file at the Alembic head")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--repeat", type=int, default=5, help="timed runs per question")
    parser.add_argument("--embeddings", action="store_true", help="also measure the hybrid path")
    parser.add_argument("--report", help="write the full result as JSON here")
    return parser.parse_args()


def build_golden(db, normalize) -> list[dict]:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT s.filename, c.text FROM document_chunks c "
            "JOIN document_versions v ON v.id = c.document_version_id "
            "JOIN source_documents s ON s.id = c.source_document_id "
            "WHERE v.is_active = 1 AND v.status = 'done' AND s.status = 'active'"
        )
    ).all()
    corpus: dict[str, str] = {}
    for filename, chunk_text in rows:
        corpus[filename] = corpus.get(filename, "") + "\n" + normalize(chunk_text)

    golden = []
    for filename in sorted(corpus):
        match = CODE_PATTERN.match(filename)
        if match:
            code = match.group(1)
            golden.append({"kind": "code", "question": f"Chương trình {code} đi những đâu?", "expected": [filename]})
    for name in PROPER_NAMES:
        needle = normalize(name)
        expected = sorted(filename for filename, body in corpus.items() if needle in body)
        if expected:
            golden.append({"kind": "name", "question": f"Tour nào có {name}?", "expected": expected})
    return golden


def score(results: list[dict], expected: list[str]) -> tuple[bool, bool, float]:
    files: list[str] = []
    for item in results:
        metadata = item.get("metadata") or {}
        name = metadata.get("filename") or metadata.get("source")
        if name and name not in files:
            files.append(name)
    rank = next((index for index, name in enumerate(files, start=1) if name in expected), None)
    return rank == 1, rank is not None and rank <= 3, (1.0 / rank) if rank else 0.0


async def run_path(store, golden, *, path: str, mode: str, top_k: int, repeat: int) -> dict:
    from unittest.mock import patch

    from app.services.travel_vector_store import TravelVectorStore

    patches = []
    if mode == "scan":
        patches.append(patch.object(TravelVectorStore, "_query_indexed_keywords", staticmethod(lambda *a, **k: None)))
    if path == "keyword":
        patches.append(patch.object(store, "_embed_query", side_effect=RuntimeError("benchmark: no embeddings")))
    for active in patches:
        active.start()
    try:
        per_question = []
        for case in golden:
            results = await store.query(query_text=case["question"], top_k=top_k)
            timings = []
            for _ in range(repeat):
                started = time.perf_counter()
                await store.query(query_text=case["question"], top_k=top_k)
                timings.append((time.perf_counter() - started) * 1000)
            hit1, hit3, reciprocal = score(results, case["expected"])
            per_question.append({**case, "hit1": hit1, "hit3": hit3, "rr": reciprocal, "ms": statistics.median(timings)})
    finally:
        for active in reversed(patches):
            active.stop()
    latencies = sorted(item["ms"] for item in per_question)
    count = len(per_question)
    return {
        "path": path,
        "mode": mode,
        "questions": count,
        "hit1": sum(item["hit1"] for item in per_question) / count,
        "hit3": sum(item["hit3"] for item in per_question) / count,
        "mrr": sum(item["rr"] for item in per_question) / count,
        "median_ms": statistics.median(latencies),
        "p95_ms": latencies[min(count - 1, round(0.95 * (count - 1)))],
        "cases": per_question,
    }


async def main() -> int:
    args = parse_args()
    database = Path(args.database).resolve()
    if not database.is_file():
        print(f"No database at {database}", file=sys.stderr)
        return 2
    os.environ["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    sys.path.insert(0, str(ROOT))
    logging.getLogger("app").setLevel(logging.ERROR)

    from app.core.database import SessionLocal
    from app.services.travel_vector_store import TravelVectorStore, normalize_search_text

    db = SessionLocal()
    try:
        golden = build_golden(db, normalize_search_text)
    finally:
        db.close()
    if not golden:
        print("No active documents in this database.", file=sys.stderr)
        return 2

    store = TravelVectorStore()
    paths = ["keyword"] + (["hybrid"] if args.embeddings else [])
    if args.embeddings:
        # Warm the query-embedding cache so the timings measure retrieval only.
        for case in golden:
            await store.query(query_text=case["question"], top_k=args.top_k)
    runs = []
    for path in paths:
        for mode in ("scan", "fts"):
            runs.append(await run_path(store, golden, path=path, mode=mode, top_k=args.top_k, repeat=args.repeat))

    kinds = sorted({case["kind"] for case in golden})
    counts = ", ".join("%d %s" % (sum(case["kind"] == kind for case in golden), kind) for kind in kinds)
    print(f"{len(golden)} questions ({counts}), top_k={args.top_k}")
    print(f"{'path':8} {'mode':5} {'hit@1':>6} {'hit@3':>6} {'MRR':>6} {'median ms':>10} {'p95 ms':>8}")
    for run in runs:
        print(f"{run['path']:8} {run['mode']:5} {run['hit1']:6.2f} {run['hit3']:6.2f} {run['mrr']:6.2f} {run['median_ms']:10.1f} {run['p95_ms']:8.1f}")
    for kind in kinds:
        print(f"\nhit@1 by question kind: {kind}")
        for run in runs:
            cases = [case for case in run["cases"] if case["kind"] == kind]
            print(f"  {run['path']:8} {run['mode']:5} {sum(c['hit1'] for c in cases)}/{len(cases)}")
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
