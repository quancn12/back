# Step 04 Implementation Handoff

## Mission

Implement and integrate **C01-F1.0**, build the **C01-E1.0** experiment system, and preserve exact research semantics and reproducibility. Do not claim empirical readiness or effectiveness until acquisition gates and locked evaluations pass.

## Read First

1. `01_ALGORITHM_SPEC.md`
2. `02_DATA_AND_INTEGRATION_CONTRACT.md`
3. `03_EXPERIMENT_PROTOCOL.md`

Consult the frozen source files only for additional detail/provenance. If the consolidation conflicts with a frozen source, report it before coding the disputed behavior.

## Implementation Objectives

### A. Frozen algorithm

Implement time-correct snapshots, F2/F3 fitting and exact joint enumeration, F4 marginals, catalog/episode feasibility, F5 content ranking, F6/F7 gates and burden, F8 direct values, F9–F11 question transactions, F12 deterministic ties, fail-closed missingness, and idempotent delivery/reply handling.

### B. Experiment harness

Implement `build_snapshot`, `fit_c01`, `fit_c02`, `decide`, `on_reply`, `replay`, and independent `score`. Isolate policy inputs from labels/windows/branch truth. Support train/V-fit/V-policy/locked-test access boundaries.

### C. Baselines and ablations

Implement P0, B-C02, B-H, B-R, secondary B-E, and every named ablation as separate model/variant IDs. Keep shared controls and distinct policy ledgers.

### D. Metrics/evaluation

Implement group-aware Brier/log loss/AP/reliability, TOC, UIR/precision, NMR, silence, timing, clarification, hard violations, grouped bootstrap/permutation, Holm, guardrails, null denominators, and schema validation.

### E. Chatbot integration and modes

- `OFF`: proactive engine does not decide or deliver; reactive handler remains.
- `SHADOW`: build snapshots and decisions/logs, but never deliver proactive messages. Use for integration safety; it does not create counterfactual outcomes.
- `LIVE`: deliver only after complete configuration, consent/catalog/ledger integration, conformance, governance, and applicable study/deployment authorization.

Mode is an application control outside the utility equation. `SHADOW` must never be reported as if users received treatment.

## Required Validation

Verify equations ↔ code with independent exact enumeration and fixed fixtures; probability normalization; threshold/tie boundaries; time-known reconstruction; continuation cost/expiry; cap reservation; abort/double-reply/idempotency; same-episode suppression; missing vs empty ledger; and evaluator denominators/statistics. Passing the historical math audit alone is insufficient for the new implementation.

## Experiment Execution Rules

Do not execute empirical CORE runs while required manifest fields are null. D1+D3 enable bounded scenario evaluation; D2 is required for real-need claims; D4 for causal benefit. Select on validation only, hash/freeze everything, open test once, run the entire planned family, retain negative/blocked results.

## Logging Requirements

Persist input/source/version hashes; snapshot and feature provenance; candidate inclusions/exclusions; all `p,R,Q` values; selected and actual action; gate/recheck reason codes; transaction/message/episode/idempotency IDs; replies/delay/abort; ledger changes; model/config/code/protocol versions; and outcome provenance. Never log future truth inside a policy object or publish identifiable trajectories.

## Reproducibility Rules

Pin dependencies; record runtime/OS/hardware/RNG/timezone; use frozen seeds; maintain SHA-256 inventories because no Git repository is currently present; preserve raw inputs and access logs; never overwrite frozen/historical research sources or empty templates with fabricated examples.

## Expected Artifacts

- proactive engine and typed adapter interfaces;
- transaction/delivery controller with OFF/SHADOW/LIVE;
- unit/property/conformance tests and independent fixtures;
- C01/C02 fit artifacts and manifest validation;
- replay/evaluator/statistics CLI equivalent to `validate-data → validate-implementation → fit → tune → freeze-run → evaluate-locked-test → analyze → report`;
- baseline/ablation implementations;
- schema-conforming raw/processed outputs, hashes, logs, and blocked/readiness reports;
- integration and experiment documentation without rewriting the frozen model.

## Escalation Policy

```text
Implementation bug or integration problem
  → fix in Step 04; preserve behavior and add regression test

Experiment estimand/metric/procedure defect
  → RETURN_TO_CHAT_3 with evidence and affected claims

Algorithm/formula/target/transaction semantic defect
  → RETURN_TO_CHAT_2 with exact equation, evidence, and required revision

Missing data/configuration/partner authorization
  → BLOCKED_DATA_OR_CALIBRATION; continue unaffected implementation work
```

## Forbidden Actions

Do not simplify equations because implementation is difficult; change thresholds after test results; alter baselines to favor C01; redesign from archived research; silently introduce neural/LLM/RL/planner components; invent priors/utilities/catalog facts; treat all-wait as success; omit question/follow-up burden; relax consent/feasibility; or claim user benefit without EXP-HUM-01.

## Immediate Work Order

1. Validate package/frozen-source hashes and create a semantic manifest loader.
2. Define DTOs and pure interfaces; implement snapshot/time correctness.
3. Implement engine and conformance tests before adapters.
4. Implement replay/evaluator, then baselines/ablations and statistics.
5. Add chatbot adapter and OFF/SHADOW mode; LIVE remains gated.
6. Produce a readiness report enumerating every unresolved manifest field.

> **HISTORICAL IMPLEMENTATION CHECKLIST:** The immediate work order below
> predates the existing `app/services/proactive/` implementation and
> `/api/v1/proactive`. Do not recreate those modules. Read
> `docs/human/PROACTIVE_INTEGRATION_STATUS.md` for current implementation and
> remaining research blockers.
