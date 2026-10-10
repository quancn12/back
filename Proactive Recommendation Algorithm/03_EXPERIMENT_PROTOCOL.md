# C01-E1.0 Experiment Protocol

Status: **FROZEN**. Compact implementation contract derived from `Research/experiments/FINAL_EXPERIMENT_DESIGN_SPEC.md`. No empirical results exist.

## 1. Evaluation Goal

Test target measurability, future unresolved-need prediction, consequence representation, complete offer/ask/wait decisions, nuisance control, robustness, and—only in a separate randomized study—actual traveler benefit.

## 2. Research Questions

| RQ | Question | Hypothesis |
|---|---|---|
| RQ0 | Are landmark C/D labels usable? | H00a alpha lower 95% bound >.67; H00b real agreement lower bound >.70 |
| RQ1 | Does context predict unresolved need? | H01a C01 Brier < P0 |
| RQ2 | Do consequences earn extra supervision? | H01b C01 Brier < C02; H01c C01 TOC > C02 |
| RQ3 | Does clarification earn burden/delay? | H02 TOC full > A-Q; H-CAL exploratory answer calibration |
| RQ4 | Does full decision logic improve scenario decisions? | H-SYS TOC full > B-H |
| RQ5 | Do costs/rules control nuisance safely? | H07 NMR full < A-I; H-GATE zero violations |
| RQ6 | Where does behavior fail under shift/missingness? | exploratory sensitivity/robustness |
| RQ7 | Does assignment to full policy improve actual outcomes? | H03 goal success full > reactive-only |

## 3. Hypotheses

Superiority nulls are `H0: delta=0`, two-sided; support additionally requires beneficial sign, practical threshold, and guardrails. H01a/b/c, H02, H-SYS, H07 are one Holm family.

H00a/H00b are measurement gates; H01a tests context; H01b/H01c test consequence representation in prediction/decision; H02 tests clarification; H-SYS tests the complete system against B-H; H07 tests soft interruption costs; H-GATE requires zero conformance violations; H03 is the separate randomized causal hypothesis. H-CAL and RQ6 are exploratory rather than directional confirmatory claims.

## 4. Dataset / Scenario Requirements

- D0-MATH: artificial conformance fixtures only.
- D1-SCEN: independently authored/annotated scenario families with time-known events, 60-minute C/D labels, all question branches/delays, catalog facts, and acceptable service/time windows. Constructed-scope claims only.
- D2-SHADOW: consenting untreated travelers/parties; required for real-need target/prediction claims.
- D3-ELICIT: independent priors, common-anchor utilities/costs, randomized question/response calibration.
- D4-TRIAL: party-randomized full vs reactive-only outcomes; required for causal benefit.

Main scope: adult leisure convenience on one coastal day, Vietnamese scenarios, four frozen need types. Checkpoints every 15 minutes plus known event changes. Party/scenario family is the independent unit.

## 5. Ground Truth

`C_j(T)` and `D_j(T)` are labeled under no newly evaluated proactive transaction. D=1 means goal-relevant support unresolved at T; D=2 relevant and verified resolved; D=0 absent/not relevant. Click, acceptance, booking, post-treatment outcome, or model score is not reference truth.

Two blinded coders label; third adjudicates with reasons; keep originals. Independently annotate acceptable `(service, earliest, latest)` sets, appropriate questions, and `should_silence`. Unknown remains null. Treatment outcomes and assumed utility remain separate.

## 6. Train / Validation / Test Rules

D1 split 60/20/20 by immutable connected family ancestry and SHA-256; validation splits into V-fit and V-policy. D2 uses party-grouped temporal cutoffs with horizon embargo. All derived siblings stay together. One label-independent hash-selected checkpoint per trip-day contributes training counts. Fit on train; select forecast setting on V-fit; tune threshold on V-policy; do not refit; open sealed test once. No row-random split, future truth, test tuning, or rater/seed pseudo-replication.

## 7. Baselines

| ID | Definition |
|---|---|
| B-R | reactive-only, common service handler after explicit request |
| P0 | per-need unconditional three-state prevalence, Dirichlet .5/category |
| B-C02 | frozen autoregressive multinomial-logistic D model; same F4–F12 wrapper; primary rival |
| B-H | specified two-stage context heuristic with common hard gates; never asks |
| B-E | entropy-triggered question comparator with same F9/F10 continuation |

Use identical checkpoints, visible inputs, catalog, hard constraints, renderer, groups, labels, and tuning budget; each policy has its own message ledger. Disclose C01's extra C supervision.

## 8. Ablations

`A-Q` no questions; `A-H` H=0; `A-I` i0=kappa=0; `A-B` kappa=0; `A-T` tau=0; `A-X` optional x masked/refit; `A-REC` recency fields masked/refit; `A-PREF` predictive preference fields masked while hard constraints remain; `A-RES` active vs unresolved diagnostic; `A-EXP` direct-time continuation scoring with final checks; `A-SUP` remove offered-only suppression in replay; `A-FEAS` score excluded items but never deliver. Each is a named variant, not F1.0 revision.

## 9. Primary Metrics

- Unresolved Brier: `(p_j-1[D_j=1])^2`.
- Multicategory D Brier: `sum_k(P(D=k)-1[D=k])^2`; joint D log loss; per-need AP/reliability.
- TOC: unique eligible reference episodes receiving at least one appropriate offer in-window and before request / eligible reference episodes. Missed opportunity rate=`1-TOC`.
- Offer precision and UIR: appropriate or unnecessary delivered offers / judged offers; questions excluded.
- NMR: `100*(unnecessary questions + unnecessary direct/linked offers)/exogenous checkpoints`.
- Silence specificity: wait/no delivery when gold `should_silence=1` / such checkpoints.
- Hard violations, content accuracy/NDCG@1 if ordinal labels exist, timing error, answer Brier/log loss, abort/window loss, message frequency.
- Trial primary: party-level pre-enrolled support-goal success fraction; guardrails annoyance 0–4 and opt-out.

Zero offers → precision/UIR null. Zero opportunities → TOC null. Missing truth is excluded with coverage/bounds, never zero. Average within trip then equal-weight independent groups. Core primaries are: measurement agreement (EXP-VAL), violation count (EXP-IMP), unresolved Brier (EXP-PRED), TOC (EXP-DEC/ASK), NMR (EXP-COST), and goal success (EXP-HUM).

## 10. Secondary Metrics

Secondary outputs include multicategory D Brier/log loss, AP/reliability, row support, offer precision/UIR, content quality, silence specificity, timing and request lead, answer calibration/abort/window loss, message frequency, abstention/prior reliance, and descriptive service-use/business outcomes. Optimization `Q` is diagnostic assumed utility, never measured benefit.

## 11. Guardrail Metrics

Decision guardrails: NMR difference <=2/100 and absolute NMR<=10; EXP-COST TOC loss <=.03; trial annoyance increase <.25 and opt-out difference <.05; zero delivered hard violations.

## 12. Parameter Tuning Rules

Main fixed settings: horizon 60, `W=1440`, `h_f=120`, `W_I=240`, `h_I=120`, cooldown 30, cap 4, reply 5 minutes. Select alpha strength `{.5,2,8}` and C02 lambda `{.01,1,100}` on V-fit. Select tau/h/e from `{0,.05,...,1}` on V-policy. Candidate admissible only if NMR<=10, no delivered hard violation, and judgments support numerator; maximize TOC, ties lower NMR, fewer messages, then larger threshold. None admissible → `NO_ADMISSIBLE_POLICY`. No extra p cutoff.

## 13. Threshold Calibration

Replay each of the 21 threshold candidates on V-policy from explicitly initialized ledgers and genuine pre-window history. Apply the admissibility/TOC/tie rule above, freeze the selected tau/h/e before test, and never clamp the derived direct threshold `(H+I+tau)/(B+H)` or tune an independent confidence cutoff. A validation set with zero opportunities cannot calibrate the trigger.

## 14. Statistical Tests

Paired group-level sign-flip permutation: exact for `G<=20`, else 99,999 draws seed 314159. Paired cluster bootstrap: 9,999 draws seed 271828; report undefined-draw fraction and gate if >5%. Fewer than 30 independent groups → descriptive only. Holm FWER .05 for the six offline contrasts; also 99.167% two-sided decision intervals and specified one-sided guardrail bounds. Secondary families use declared BH q=.05. Report absolute effects, ordinary 95% CIs, independent counts, practical and guardrail decisions separately.

Practical thresholds: Brier improvement >=.02; TOC >=.05; NMR decrease >=2/100; trial goal success >=.05. Nonsignificance is not equivalence.

## 15. Sensitivity Tests

Run predeclared grids for tau, scaled B/H/i0/kappa, time windows/half-lives, cooldown/cap/reply delay, alpha, and separately labeled 30/120-minute horizons. Report stable regions and cliffs; do not select a new test operating point.

## 16. Robustness Tests

Stress 25/50/75% optional missingness, correlated weather/location missingness, truncated history, unseen rows, timestamp noise, catalog removal/invalidation, abort/delay variation with seeds `{11,29,47,71,101}`. Preserve consent and distinguish useful support from safe all-silence behavior.

## 17. Negative Tests

Conformance must cover unknown consent/ledger, suppression, stale/infeasible options, caps/cooldown, one-slot questions, low-p and high-entropy boundaries, one-update/one-follow-up, all aborts, duplicate dispatch, epsilon ties, threshold>1, new-anchor semantics, and future-information exclusion.

## 18. Core Experiment Matrix

| ID | Purpose | Comparison/source | Primary |
|---|---|---|---|
| EXP-VAL-00 | target/data validity | raters/reference; D1,D2 | agreement gates |
| EXP-IMP-00 | implementation/evaluator conformance | invariants; D0 | violation count |
| EXP-PRED-01 | future prediction/consequence value | full vs P0/C02; D1,D2 | Brier |
| EXP-DEC-01 | complete decisions/timing | full vs C02/B-H; D1 | TOC |
| EXP-ASK-01 | clarification | full vs A-Q; D1,D3 | TOC |
| EXP-COST-01 | interruption accounting | full vs A-I; D1 | NMR |
| EXP-ABL-01 | component mechanisms | named ablations | matched metric |
| EXP-SENS-01 | parameter stability | frozen grids | curves |
| EXP-ROB-01 | deployment stress | corruptions | degradation |
| EXP-HUM-01 | actual causal user benefit | party randomized full vs B-R; D4 | goal success |
| EXP-BIZ-01 | optional business description | D4 | descriptive only |

## 19. Execution Order

Hash algorithm → inventory/construct pilot → validate construct/data → implement and pass conformance/evaluator fixtures → grouped splits/sample plan → train/D3 estimation → V-fit/V-policy selection → freeze hashes/code/config/labels → one locked offline test + full family → conditional D4 randomized trial.

## 20. Reproducibility Requirements

Separate snapshot builder, C01/C02 fitters, policy/reply, replay, and evaluator. Policy never receives labels, acceptable windows, or branch truth. Record algorithm/protocol/data/model/config/code hashes, split, seeds, versions, hardware/timezone, raw per-checkpoint predictions/actions, actual messages, failures/exclusions, group metrics, comparisons, and access logs.

## 21. Result Schema

Use schema fields in `Research/experiments/schemas/`; null + reason, never NaN/blank-as-zero. Every aggregate row records versions/hashes, experiment/run/model/dataset/split/code IDs, source kind, metric, estimate/CI/unit, numerator/denominator, group/trip/episode/checkpoint counts, missing count, status, seed, and UTC timestamp. Comparison rows additionally record comparator/contrast, effect, raw/adjusted p, family, guardrail status, and decision.

## 22. Do-Not-Change Rules

Do not alter C01-F1.0; tune on test; change hypotheses/metrics/gates after access; use weak/redefined baselines; omit failed/missing contrasts; count checkpoints/messages/seeds as tourists; validate L with model-generated replies; claim causal benefit from replay; or let revenue override user guardrails.

## Source Traceability

Exact definitions and full CORE procedures: experiment protocol §§3–30. Matrix: §23 / `Research/experiments/EXPERIMENT_MATRIX.md`. Active handoff: `Research/state/CURRENT_STATE.md`.
