# Proactive Research Index

## Repository authority

The checked-in research package is this directory. The external `Research/`
tree and `ORIGINAL_RESEARCH_ARCHIVE_2026-09-11.zip` referenced by earlier notes
are not present in this checkout and cannot be verified here. Do not treat
those paths, checksums, or file counts as available sources.

The application already contains an isolated C01-F1.0 experiment under
`app/services/proactive/` and `/api/v1/proactive`. Implementation status,
deployment boundaries, and remaining real-data blockers are owned by
`docs/human/PROACTIVE_INTEGRATION_STATUS.md`; overall project risks and work
order remain in `docs/human/TECHNICAL_DEBT.md` and `NEXT_STEPS.md`.

## Documents

| File | Authority | Read when |
|---|---|---|
| `01_ALGORITHM_SPEC.md` | Frozen C01-F1.0 algorithm semantics | Implementing or reviewing algorithm behavior |
| `02_DATA_AND_INTEGRATION_CONTRACT.md` | Inputs, outputs, missing assets, and adapter boundaries | Changing ingestion or application integration |
| `03_EXPERIMENT_PROTOCOL.md` | Frozen C01-E1.0 evaluation protocol | Building or interpreting experiments |
| `04_IMPLEMENTATION_HANDOFF.md` | Historical initial implementation checklist | Only for original delivery context; not an active work order |

Do not read all four specifications for routine application work. Follow the
task-specific row above, then verify current code and the human status document.

## Current research boundary

- Algorithm C01-F1.0 and experiment protocol C01-E1.0 are frozen research
  specifications.
- Synthetic D0 fixtures can exercise the isolated implementation.
- Real traveler recommendations remain blocked on approved data, calibration,
  and production adapters.
- Do not infer research approval, real-data readiness, or production status
  from the existence of the code or synthetic evaluation results.
