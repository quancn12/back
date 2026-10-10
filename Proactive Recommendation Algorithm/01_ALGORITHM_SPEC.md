# C01-F1.0 Algorithm Specification

Status: **FROZEN**. This file is the operational consolidation of `Research/algorithm/FROZEN_ALGORITHM_SPEC.md`. The source freeze remains the mathematical provenance record.

## 1. Problem Definition

At an eligible decision time `t` for traveler `u`, predict whether each specified need episode will be unresolved at `T=t+Delta` under a no-new-evaluated-transaction reference continuation. Choose exactly one initial action: wait, offer one feasible single-need service, or ask one approved question. A question may lead to at most one linked offer.

This is ordinary travel convenience support. It is not emergency advice, diagnosis, autonomous purchasing, booking-probability prediction, causal uplift estimation, or multistep planning.

## 2. Algorithm Objective

Maximize assumed incremental user utility on one anchored scale `U`, after false-offer harm, interruption burden, and a per-message caution margin, while satisfying hard consent, timing, freshness, catalog, suppression, cooldown, cap, and transaction constraints.

## 3. Compact Notation

| Symbol | Meaning | Type/domain |
|---|---|---|
| `u,t,T,Delta` | traveler, decision time, landmark, horizon | UTC minutes; `T=t+Delta`, `Delta>0` |
| `H_t,x_t,s_t` | authorized history, categorical features, full state | observed/derived; every optional field has `UNKNOWN` |
| `J,j` | need types/index | `1<=J<=4`; fixed order |
| `C_j` | relevant consequence at `T` | latent binary |
| `D_j` | episode state at `T` | latent: `0` not relevant, `1` unresolved, `2` resolved |
| `Z=(C,D), Omega, b_t(z)` | joint latent assignment, state space, predictive law | `K=6^J`, normalized |
| `p_j` | unresolved probability | `[0,1]` |
| `theta,n,alpha` | CPT probability, count, Dirichlet pseudocount | `alpha>0` |
| `a,j(a),F_t` | service, target need, direct feasible set | each service targets one need |
| `B_a,H_a` | conditional benefit; false/unnecessary-offer harm | `[0,1] U` |
| `q,y,L_q,P_q,b_t^y` | question, answer, likelihood, answer mass, posterior | finite normalized categories |
| `bI_t,i0,kappa,tau` | recent burden, base cost, burden coefficient, caution margin | nonnegative; costs in `U` |
| `G_t` | common eligibility gate | boolean; unknown critical state is false |
| `R,Q^dir,Q^ask,Q^+,V` | content and action values | `U`; wait value `0` |
| `M_t,W_I,h_I,cap,cooldown` | delivered-message ledger and controls | configured |
| `Delta_q,F_q^+` | max reply delay, conservative continuation set | `0<Delta_q<=Delta` |

Main vocabulary order: `meal_coordination`, `clothes_changing_drying`, `rest_opportunity`, `transport_coordination`.

## 4. Inputs

Required: clock/timezone; model/config versions; time-known event versions and episode anchors; explicit consent/attention; available delivery ledger; verified service mapping, bounds, hours, price, freshness, and constraints; fitted/elicited CPT priors/counts, utility/cost tables, and question likelihoods for enabled actions. Optional predictive context may be `UNKNOWN`. Missing critical feasibility excludes an option; missing required model/configuration fails closed.

## 5. State Representation — F1

`H_t` contains only authorized records with `known_at<=t`. Retain past events whose valid time is in `[t-W,t]` and future commitments whose current version was already known at `t`; deduplicate by evidence ID and use the latest known revision. Never use future realized weather, service use, or later plan revisions.

For optional past event type `f`:

`rec_f(t)=max_e 2^(-(t-valid_at(e))/h_f)`, over eligible events, with `max(empty)=0` and a separate missing flag.

`s_t=(u,t,T,x_t,{episode_j},catalog_snapshot,M_t,consent,availability,pending,model_version,config_version)`.

Fixed feature schema: current/last/next activity, trip phase, local-time block, elapsed-time bin, free-time bin, coarse location, weather, explicit budget/accessibility/interruption tolerance, and per-need episode/resolution status. Predicted `C,D` remain outputs and are never recycled as observations.

## 6. Future-Need Prediction — F2–F4

[PROPOSED/ADAPTED] One fixed landmark, finite joint consequence/demand model:

$$
b_t(c,d)=\prod_{j=1}^{J}\theta_{C_j}(c_j\mid x_t,c_{<j})
\prod_{j=1}^{J}\theta_{D_j}(d_j\mid x_t,c,d_{<j}). \tag{F2}
$$

$$
\theta_V(v\mid pa)=\frac{n_{V,pa,v}+\alpha_{V,pa,v}}
{\sum_{v'}(n_{V,pa,v'}+\alpha_{V,pa,v'})},\quad \alpha>0. \tag{F3}
$$

$$
p_j(t,\Delta)=\sum_{z\in\Omega}b_t(z)\mathbf{1}\{D_j(z)=1\}. \tag{F4}
$$

Enumerate all `6^J` assignments in fixed order. Needs are not independent; `sum_j p_j` may exceed 1. Do not softmax marginals, use missing labels as zero, perform EM/pseudolabeling from purchases, or apply an independent marginal recalibrator.

## 7. Candidate Generation

Generate all catalog options mapped to open episodes; do not prefilter on `p_j`. Suppress all same-episode alternatives after an offer, explicit dismissal, or confirmed resolution. A booking is not fulfillment. A new observed activity/commitment anchor may create a new episode; a timer cannot.

`start_a(t)=max(t+ell_a,open_a)` and require

`finish_a(t)=start_a(t)+dur_a <= min(close_a,next_t,T)`.

Also require consent/scope, verified capability, compatible requirements/budget, `data_age<=TTL`, and known conservative bounds. `F_q^+` repeats feasibility at `t+Delta_q`, keeping `T` fixed. Questions need two remaining message slots and at least one possible continuation.

## 8. Recommendation Utility / Scoring — F5

$$
v(a,z)=B_a\mathbf{1}\{D_{j(a)}=1\}-H_a\mathbf{1}\{D_{j(a)}\ne1\},\quad v(w,z)=0,
$$

$$
R_t(a)=p_{j(a)}B_a-(1-p_{j(a)})H_a. \tag{F5}
$$

`stable_argmax R_t(a)` answers **WHAT to recommend**. It does not authorize delivery.

## 9. Intervention Cost — F6–F7

$$
bI_t=\sum_{r_i\in(t-W_I,t]}2^{-(t-r_i)/h_I},\qquad
I_t(a)=i0_a+\kappa bI_t,\quad I_t(q)=i0_q+\kappa bI_t. \tag{F6–F7}
$$

Both questions and offers count. Continuation cost uses `bI_t+1`. `tau>=0` is a separate minimum advantage per delivered proactive message. The burden model is a transparent proxy, not a validated fatigue law.

## 10. Proactive Trigger — F8–F12

Direct action:

$$Q_t^{dir}(a)=R_t(a)-I_t(a)-\tau,\qquad V_t=\max(0,\max_{a\in F_t}Q_t^{dir}(a)). \tag{F8}$$

For one normalized answer likelihood:

$$P_q(y)=\sum_z b_t(z)L_q(y\mid z,x_t),\qquad
b_t^y(z)=\frac{b_t(z)L_q(y\mid z,x_t)}{P_q(y)}. \tag{F9}$$

For substantive positive-mass branches:

$$Q_t^+(a\mid y)=\mathbb{E}_{b_t^y}[v(a,Z)]-i0_a-\kappa(bI_t+1)-\tau,$$

$$V_q^+(y)=\max(0,\max_{a\in F_q^+}Q_t^+(a\mid y)),\quad V_q^+(abort)=0. \tag{F10}$$

$$Q_t^{ask}(q)=-i0_q-\kappa bI_t-\tau+\sum_yP_q(y)V_q^+(y). \tag{F11}$$

`G_t=ready AND consent/attention AND NOT pending AND cooldown_ok AND count<cap`. If false, wait. Otherwise choose the global maximum of wait/direct/question. A nonwait value must exceed `eps_cmp=1e-12 U`; within-tolerance ties prefer wait, then direct offer, then stable lexicographic ID. Continuations prefer wait, then stable service ID. [F12]

**WHAT versus WHEN/WHETHER:** `R_t` ranks content. `Q^dir`, `Q^ask`, hard gates, and F12 decide intervention. A highly relevant massage may still be withheld when the traveler is busy, the window is infeasible, or interruption cost makes net value nonpositive.

Derived direct threshold when `B+H>0`: `p_j>(H+I+tau)/(B+H)`. Do not clamp a threshold above 1. If `B=H=0`, wait wins.

## 11. Constraints and Transaction Semantics

- One initial message; one question; at most one reserved linked offer; no recursive question.
- `yes/no/uncertain/abort` are fixed answer categories; refusal, confirmed resolution/dismissal, timeout, unsupported reply, opt-out, or material change abort silently.
- Cache the transaction prior/context and apply F9 once. A later fresh forecast may include the reply once through F1.
- Recheck consent, suppression, capacity, and service validity at actual delivery.
- Idempotency keys prevent duplicate delivery/reply processing.
- A linked offer is exempt only from its own question's cooldown/pending check; all other controls remain.

## 12. Normalization

`0 U` is no incremental effect versus the declared reference; `1 U` is the shared maximum ordinary convenience gain/loss anchor for scope. `B,H,i0,kappa*bI,tau` must share this scale. No per-service or per-batch min-max normalization. Time/price are hard constraints or already reflected in elicited value; never add them directly to probabilities.

## 13. Parameter Definitions

| Class | Parameters |
|---|---|
| Learnable/estimated offline | CPT counts/`theta` via F3; `L_q` from separate response/state data |
| Configurable/elicited | `Delta,W,h_f`, bins, `alpha`, `B,H,i0,kappa,tau,W_I,h_I,cooldown,cap,Delta_q,TTL`, service bounds |
| Fixed structural/numeric | target semantics, graph/order, `J<=4`, action/transaction semantics, F1–F12, `eps_cmp=1e-12` |

Main E1.0 configuration: `J=4`, `Delta=60 min`, `W=1440`, `h_f=120`, `W_I=240`, `h_I=120`, `cooldown=30`, `cap=4`, `Delta_q=5`. These are frozen experiment settings, not validated tourism constants.

## 14. Uncertainty Handling

Propagate state ambiguity through the full joint and expected utility. Use explicit false-offer harm and decision-value questioning. Sparse rows use declared priors/UNKNOWN categories. Do not add entropy vetoes, standard-deviation penalties, max-min policies, LLM confidence, or claimed frequentist confidence guarantees.

## 15. Cold Start

Use pooled context-conditioned tables and explicit common priors; no per-user fitting. Optional fields become `UNKNOWN`. Consent and an explicitly available ledger remain required. No approved prior/common utility configuration → `WAIT / NOT_CONFIGURED`.

## 16. Missing Data

- Optional predictor missing → `UNKNOWN` plus mask.
- Critical feasibility missing → exclude affected option.
- Likelihood missing → disable affected question only.
- Invalid probability model → `WAIT / MODEL_INVALID`.
- No feasible option → `WAIT / NO_FEASIBLE_SUPPORT` with diagnostic forecast allowed.
- Missing consent/attention/ledger → `WAIT / ELIGIBILITY_UNKNOWN`.
- Missing outcome label → no supervised update.

## 17. Algorithm Pipeline

Validate manifest → build time-correct state → gate eligibility → enumerate joint/marginals → generate/recheck feasible direct and continuation sets → score WHAT → score direct/ask/wait → deterministic selection → idempotent delivery/wait → optional one-reply posterior and linked offer → log all alternatives, reasons, versions, and outcomes.

## 18. Final Pseudocode

```text
DECIDE(input, manifest, tables):
  reject invalid manifest/tables with WAIT
  s,x,episodes,ledger = build_time_correct_state(input)       # F1
  burden,count,G = build_gate(s)                              # F6,F7
  if not G: return WAIT
  b = enumerate_normalized_joint(x,tables)                    # F2,F3
  p = unresolved_marginals(b)                                 # F4
  direct,questions,continuations = feasible_candidates(s)
  values[WAIT] = 0
  for a in direct:
    content[a] = p[target(a)]*B[a]-(1-p[target(a)])*H[a]       # F5
    values[a] = content[a]-i0[a]-kappa*burden-tau             # F8
  for q in questions:
    values[q] = -i0[q]-kappa*burden-tau
    for y in answers(q):
      mass,posterior = bayes_branch(b,L[q,y])                  # F9
      if mass==0 or y==ABORT: continue
      best = max(0, max_a(E_posterior[v(a,Z)]
                          -i0[a]-kappa*(burden+1)-tau))        # F10
      values[q] += mass*best                                  # F11
  chosen = frozen_stable_argmax(values)                       # F12
  final_recheck; idempotently dispatch or wait; log everything

ON_REPLY(transaction, reply):
  if timeout/material change/abort/schema mismatch: close silently
  else update cached prior once (F9), choose positive continuation (F10),
       final-recheck, dispatch at most one offer, close transaction
```

## 19. Complexity

With retained events `E`, candidates `A`, questions `Q`, max answers `Y`, and `K=6^J<=1296`: state construction `O(E)` (or `O(E log E)` if sorting), joint/marginals `O(KJ)`, direct scoring `O(A)`, questions `O(QY(KJ+A))`, memory `O(K+J+A+Y)` per streamed question plus sparse CPT storage. Dense parameter tables remain statistically/data sparse despite bounded inference.

## 20. Assumptions

Episode/C/D labels are meaningful; reference labels avoid treatment contamination; finite categories retain signal; priors and common utility anchors are defensible; clarification mainly reveals rather than changes the target; response likelihoods are coherent; service bounds hold; one target need per service and episode suppression are acceptable. All remain empirical assumptions.

## 21. Known Limitations

Single landmark misses transient/later needs; CPT rows can be sparse/prior dominated; utilities and likelihoods are unvalidated; selected/dependent annotations can bias confidence; no bundles/reminders/long-term fatigue/personal dynamics/causal effects/revenue objective; conservative reply/suppression rules may miss useful help; no production latency or efficacy evidence.

## 22. Frozen Boundary

Implementation agents MUST NOT reinterpret: target/horizon semantics; need vocabulary/order; `C,D` meanings; graph factorization; F3 estimator; utility/false-harm semantics; F6–F12 costs, Bayes update, trigger, ties, and transaction rules; hard feasibility/suppression; action set; cold-start/missing-data behavior. Class names, caching, storage, dependency injection, APIs, logs, and equivalent numerical optimizations may change only if behavior and auditability remain identical.

## Source Traceability

All F1–F12 and boundaries: `Research/algorithm/FROZEN_ALGORITHM_SPEC.md` §§3–23. Freeze rationale: `Research/algorithm/ALGORITHM_DECISION.md`. Verification/caveats: `Research/algorithm/MATHEMATICAL_AUDIT.md`.

