# Scoring-validity audit of MARSHAL Protocol v2

The audit of the protocol-v2 graded credit and of the InterFuser integration. The credit it settled on (F1, F6h, F3,
F7, C1, C2; [credit_v22_final/credit_v22_final.md](credit_v22_final/credit_v22_final.md)) is the one the benchmark
scorer computes under protocol v2: re-scoring all 725 recorded episodes with `marshal_bench` reproduces the audit
values exactly. The audit scripts compare credit versions by patching the published v2 functions, so they load the
recorded copy of the published scorer through `scripts/published_v2.py`.
Start with [audit_summary.md](audit_summary.md) (the original findings) and
[credit_v22_final/final_leaderboard.md](credit_v22_final/final_leaderboard.md) (the final leaderboard).

## Documents

| file | content |
|---|---|
| [audit_summary.md](audit_summary.md) | findings F1–F12, severity, evidence status, limitations |
| [reproduction.md](reproduction.md) | frozen baseline, cohort reconciliation, run history, exact leaderboard reproduction, commands |
| [startup_diagnostics.md](startup_diagnostics.md) | motion classes, who did not start and why, control-chain evidence, blocked CARLA checks |
| [scoring_formulation.md](scoring_formulation.md) / [.tex](scoring_formulation.tex) | exact v2 equations with code line references; the green_stop arithmetic traces |
| [score_distribution.md](score_distribution.md) | endpoint counts, the 114 partial cases, zero causes (overlapping and exclusive), component sensitivity |
| [counterfactual_tests.md](counterfactual_tests.md) | boundary, metamorphic and instruction-independent-policy tests with verdicts |
| [threshold_sensitivity.md](threshold_sensitivity.md) | threshold provenance and ±10% / ±25% sensitivity |
| [scoring_alternatives.md](scoring_alternatives.md) | Candidates A, A′, B, C and D on identical telemetry, against predefined desiderata |
| [literature_grounding.md](literature_grounding.md) | primary-source provenance check |
| [scenario_contracts.csv](scenario_contracts.csv) | the 25 scenario contracts |

## Credit versions and InterFuser

| folder | content |
|---|---|
| [credit_v21/](credit_v21/leaderboard_v21_candidate.md) | first correction set (F1, F3, F6, F7) and the F2 approach-gate study |
| [credit_v22/](credit_v22/credit_v22.md) | held-stop response (F6h) |
| [credit_v22_final/](credit_v22_final/credit_v22_final.md) | the final credit (adds C1, C2), its confirmation and the final leaderboard |
| [credit_discrimination/](credit_discrimination/credit_discrimination.md) | how well the credit separates failures; failure-type labels |
| [credit_quality/](credit_quality/q_cont_stability.md) | continuous quality-score candidates (Q-gate, Q-cont): definitions, validation, sensitivity |
| [crossing_analysis/](crossing_analysis/crossing_analysis.md) | causes of stop-line crossings |
| [leaderboard_impact/](leaderboard_impact/leaderboard_impact.md) | effect of scenario-geometry and configuration issues |
| [interfuser_debug/](interfuser_debug/investigation_log.md) | InterFuser no-start investigation (adapter) |
| [interfuser_greenstop/](interfuser_greenstop/paper_case_green_stop.md) | InterFuser pedestrian false positive at `green_stop` |
| [interfuser_speedfix/](interfuser_speedfix/results.md), [interfuser_remeasure/](interfuser_remeasure/final_forward.md) | speed-input fix and the final 75-episode re-measure |
| [green_stop_panel/](green_stop_panel/green_stop_panel.md) | per-model `green_stop` features and figures |
| [paper_tables/](paper_tables/paper_tables.md) | leaderboard, InterFuser setups and `green_stop` control tables |

## Data (`data/`)

| file | content |
|---|---|
| `baseline_manifest.csv` | one row per episode directory: cohort, hash, collector selection, acceptance, run-log match |
| `episode_decomposition.csv` | every strict and graded component, kinematics, control and VLM fields for 665 episodes |
| `partial_credit_episodes.csv` | the 114 partial cases, each with an explanation |
| `startup_diagnostics.csv`, `startup_tables.md` | motion class and cause per episode |
| `reproduction_tables.md`, `distribution_tables.md` | generated tables |
| `feature_score_map.csv` | feature → score dependency map |
| `threshold_inventory.csv` | every threshold: value, use, source class, evidence |
| `threshold_sensitivity.csv` | sensitivity results |
| `counterfactual_results.json` | counterfactual test results |
| `alternatives_table.json`, `alternatives_changed.json` | alternative-candidate results |
| `collision_semantics.json` | callback-event vs distinct-contact counts |
| `control_chain_notes.md` | per-controller code audit, defects D1–D12 |
| `test_output_*.txt` | captured test output |

## Scripts and tests

- **Scripts** (`scripts/`, run from the workspace root):
  - `decompose.py`, `analyze.py`, `explain_partials.py`, `startup.py`;
  - `counterfactual.py`, `sensitivity.py`, `alternatives.py`;
  - `gen_contracts.py`, `gen_thresholds.py`, `blip.py`, `extract.py`.
- **Tests:** `tests/` (pytest).
- **Exact commands:** [reproduction.md](reproduction.md).

All scripts are scorer-only. None needs CARLA, and none writes outside this directory.
