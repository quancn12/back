# VietSage isolated demo corpus

This folder is a self-contained, fictional dataset for local learning and
reading workflow checks. Every record in the corpus is marked `DEMO`,
`synthetic`, and `non-authoritative`; no hotel, service, map point, review,
photo, booking, or real-world price is represented here.

The default ingestion set contains only `demo_facts.pdf`. The
`negativecases/` fixture is deliberately outside the manifest so tests can
exercise missing or conflicting facts without entering normal retrieval.
`runtime/` is an ignored local output directory when the isolated
commands below are used.

## Isolated ingest and run

Use a separate SQLite database and source storage directory for the demo. This
keeps the existing real-data database and storage untouched:

```powershell
$env:BAYAH_DATA_MODE = "demo"
$env:DATABASE_URL = "sqlite:///./data/demo/runtime/vietsage-demo.db"
$env:SOURCE_STORAGE_DIR = "./data/demo/runtime/sources"
$env:UNIFIED_DEMO_ENABLED = "true"
$env:APP_ENV = "development"
New-Item -ItemType Directory -Force data/demo/runtime/sources | Out-Null
python scripts/ingest_travel_data.py --data-root data/demo/corpus --manifest data/demo/manifest.json --enqueue-only
python scripts/run_ingestion_worker.py --once
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The same setup can be started with the reusable launcher:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1
```

Use `-SkipIngest` when the isolated database already contains the completed
version and chunks. Re-running the ingestion command is safe: unchanged PDF
content and pipeline versions are reported as skipped.

The command scans `data/demo/corpus` exactly, so copied source-storage files
and negative fixtures are never re-ingested. A demo-tagged source outside the
resolved `data/demo` root is rejected by the ingestion boundary; it is never
moved automatically.

The app should be presented as **DEMO DATA - SYNTHETIC, NON-AUTHORITATIVE**
when using this database. This local runtime is for RAG and behavior checks;
it does not fine-tune a model or mutate production prompts.

## Corpus shape

`demo_facts.json` is the readable source ledger used to produce the PDF. The
PDF remains the actual ingestion input and goes through the same
`process_travel_pdf` reader and durable version/chunk schema as real sources.
It contains factual fields only:

- fictional hotel amenities, hours, policies, and service constraints;
- fictional discovery points with durations, hours, costs, indoor/outdoor and
  weather suitability for dynamic itineraries, comparisons, and budgets;
- a small fictional study-room schedule with explicit open/closed slots.

No answer scripts, greetings, forced itinerary lengths, or instruction text are
stored in retrieved facts.
