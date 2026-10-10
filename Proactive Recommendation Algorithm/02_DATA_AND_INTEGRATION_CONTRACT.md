# Data and Integration Contract

Status: operational consolidation for **C01-F1.0 / C01-E1.0**. No chatbot application code or verified runtime dataset/catalog was found in this repository. Paths below are intended modules/interfaces, not claims that implementations exist.

## 1. Required Algorithm Inputs

| Research variable | Meaning | Required? | Source |
|---|---|---:|---|
| `u,t,T,timezone` | identity and UTC/local clocks | yes | host/session clock + trip profile |
| `H_t` | authorized versioned events with known/valid times | yes for anchored episodes | itinerary/dialogue/event store |
| `x_t` | fixed categorical context + missing masks | yes; optional fields may be UNKNOWN | adapter from `H_t` |
| episode keys/status | need type anchored to activity/commitment | yes | episode ledger |
| consent/attention/pending | delivery eligibility | yes | user controls/session state |
| `M_t` | actual delivered proactive-message ledger | yes, even if explicitly empty | message/transaction store |
| catalog snapshot | service mapping, availability, bounds, constraints, TTL | yes per option | verified service integration |
| CPT priors/counts | F2/F3 parameters | yes | versioned fitted-model artifact |
| `B,H,i0,kappa,tau` | common-scale utility/cost configuration | yes for enabled actions | D3 elicitation + run manifest |
| `L_q,Y_q,Delta_q` | approved question schema/likelihood/reply bound | yes for questions | D3 response artifact/config |

## 2. Existing Repository Data Mapping

| Research concept | Symbol | Existing source | Status |
|---|---|---|---|
| Research goal/scope | — | root problem statement | AVAILABLE |
| Frozen equations/pseudocode | F1–F12 | `Research/algorithm/FROZEN_ALGORITHM_SPEC.md` | AVAILABLE |
| Experiment manifest/schemas | — | `Research/experiments/configs/`, `schemas/` | AVAILABLE |
| Clock/config IDs | `t,T` | future runtime | DERIVABLE |
| Traveler/trip/event records | `H_t` | no verified store in repository | MISSING |
| Context features | `x_t` | feature builder from verified events | REQUIRES_NEW_MODULE |
| Consent/attention/message ledger | `G_t,M_t` | no verified runtime integration | MISSING |
| Need episode ledger | `episode_j` | no runtime store | REQUIRES_NEW_MODULE |
| Verified service catalog | `A_t,F_t` | no catalog integration | MISSING |
| CPT/prior/utility/likelihood artifacts | `theta,B,H,i0,kappa,L` | manifest fields currently null | MISSING |
| Reference labels/branches | `C,D,L` evaluation truth | schemas exist; data absent | MISSING |
| Derived joint/scores/actions | `b,p,R,Q,d` | proactive engine | REQUIRES_NEW_MODULE |
| Experiment replay/evaluator | — | design scripts only; runner absent | REQUIRES_NEW_MODULE |

## 3. Input Schema

Concrete runtime DTOs may vary, but they must preserve these fields and semantics:

```json
{
  "request": {"traveler_id":"...","trip_id":"...","decision_time_utc":"...","timezone":"Asia/Bangkok"},
  "events": [{"event_id":"...","known_at_utc":"...","valid_at_utc":"...","type":"...","payload":{},"authorized":true}],
  "episodes": [{"episode_id":"...","need_type":"meal_coordination","anchor_event_id":"...","status":"open"}],
  "eligibility": {"consent":true,"attention_available":true,"pending_transaction":false},
  "delivered_messages": [{"message_id":"...","transaction_id":"...","kind":"offer","delivered_at_utc":"...","episode_id":"..."}],
  "catalog_snapshot": {"snapshot_id":"...","captured_at_utc":"...","services":[]},
  "model_ref": {"algorithm_version":"C01-F1.0","model_id":"...","model_hash":"...","config_hash":"..."}
}
```

Events require separate known-at and valid-at timestamps. Missing ledger is distinct from empty ledger. Catalog services need stable ID, single target need, capability evidence, price/currency, opening/closing, access delay/duration upper bounds, freshness/TTL, location/accessibility/budget requirements, and source timestamp.

## 4. Output Schema

```json
{
  "decision_id":"...",
  "transaction_id":"...",
  "algorithm_version":"C01-F1.0",
  "model_id":"...",
  "config_hash":"...",
  "should_intervene":true,
  "initial_action":"ASK",
  "candidate_id":"question-or-service-id",
  "target_episode_id":"...",
  "target_need":"rest_opportunity",
  "content_score":0.0,
  "net_value":0.0,
  "need_probabilities":{},
  "answer_probabilities":{},
  "confidence":null,
  "reason_codes":[],
  "excluded_candidates":[],
  "reserved_followup":true,
  "expires_at_utc":"..."
}
```

`confidence` is null unless a separately defined diagnostic is requested; F1.0 does not produce a calibrated policy-confidence scalar. `should_intervene` is `initial_action!=WAIT`; ASK is intervention but not an offer. Persist chosen and alternative values, gates, exclusions, actual delivery/recheck, reply/abort, and idempotency key.

## 5. Integration Boundary

```text
Existing Chatbot / Event & Catalog Systems
        ↕ typed facts, consent, delivery callbacks, explicit requests
Proactive Adapter
        ↕ time-correct snapshot DTO; no future labels
Proactive Engine (pure C01-F1.0 decision logic)
        ↕ decision + reason/exclusion record
Transaction/Delivery Controller
        ↕ idempotent ASK/OFFER/WAIT, reply, expiry, recheck
Chatbot Renderer / Reactive Handler
```

Explicit user requests bypass proactive credit and use the common reactive service handler. The engine must not directly send messages or mutate source records.

## 6. Research-to-Code Mapping

| Research component | Intended module/API |
|---|---|
| F1 snapshot/recency/time correctness | `snapshot_builder.build_snapshot(events,t,manifest)` |
| F2/F3 fitting/joint | `models.c01.fit_c01`, `C01Model.joint(snapshot)` |
| F4 marginals | `C01Model.unresolved_probabilities` |
| feasibility/expiry | `catalog.feasibility.direct`, `.continuation` |
| F5 content value | `policy.content_ranker` |
| F6/F7 gate/burden | `policy.eligibility`, `policy.burden` |
| F8–F12 choice | `policy.decide` |
| reply transaction | `transactions.on_reply` |
| message/recheck/idempotency | `delivery.controller` |
| C02/P0/B-H/B-E/ablations | separate `baselines/` and variant configs |
| reference replay/evaluation | `experiments.replay`, `experiments.score` |

Keep model/policy/evaluator objects separate so labels, acceptable windows, or branch truth cannot leak into decisions.

## 7. Permitted Implementation Changes

Class/file names, storage engine, caching, sparse CPT representation, log-sum-exp, dependency injection, serialization format, monitoring, internal APIs, batch/stream execution, and equivalent optimization. Any optimization must pass independent small-state enumeration and event-trace conformance.

## 8. Forbidden Research Changes

Do not change equation/target/action/trigger semantics; add a probability/confidence cutoff; alter utility roles; guess missing data; collapse ASK and OFFER; omit follow-up cost; change episode suppression; reuse response evidence twice; loosen feasibility; retune on test; expose labels/windows/branch tables to the policy; or infer causal/user-benefit claims from deterministic replay.

## Source Traceability

Runtime roles and missing status: frozen algorithm §18 and `Research/state/CURRENT_STATE.md`. Evaluation DTO/result requirements: experiment protocol §§26–27 and `Research/experiments/schemas/`.

