# A. Requirement status

Work types: **A** existing material is enough to write it up · **B** existing logs re-analysed · **C** code or
instrumentation added · **D** new runs. Priority: P0 needed for the paper's evaluation section, P1 strengthens
it, P2 later. Status: Done (this audit) / Partial / Open.

| Requirement | Code | Experiments / logs | Status | Missing evidence | Work | Priority |
|---|---|---|---|---|---|---|
| Expected behaviour per scenario | `scripts/_run_vlm_test.py` `SCENARIOS[*].expect`; `marshal_metrics.SCENARIO_SPEC` | — | Done (25 scenarios: STOP 13, PROCEED 5, DETOUR 4, HOLD 2, YIELD 1) | per-scenario *rationale* is in `docs/scenarios.md`, not in one table with the scored action | A | P1 |
| Ideal / best case | — | oracle runs | Partial: defined as a proposal (`02_…` §5); no source for an upper bound on stop distance | a justified "too far" bound | A + decision | P0 |
| Worst acceptable | `strict_episode_scoring.py` gates | 44 green_stop episodes re-scored, gate cascade reproduced | Done for the implemented rule; front-bumper variant evaluated (flips the oracle's occluded_officer) | decision whether to adopt a bumper-based rule | B | P0 |
| Oracle role and behaviour | `controllers/oracle.py` | 5 green_stop oracle executions; 25/25 leaderboard | Done: passes the same scorer; rolling 1.8 m/s start is a different initial condition | release logic absent (stays stopped) | B | P1 |
| Feature definitions | `_common.py` telemetry row | — | Done (`02_…` §3); lane-boundary margin, traffic-light and officer distances added offline (T4/T5) | yaw not logged in the July runs (box assumed lane-aligned) | A + B | P0 |
| Strict / Graded / MARSHAL Score meaning and relation | three criteria modules | T3 | Done (`02_…` §6) | — | A | P0 |
| Stop-line distance vs score | strict clearance, graded clearance curve | illustrative C1–C5, T2 | Done; centre-based, bumper −1.40 m admitted | — | B | P0 |
| Per-command functions and failure handling | `_score_*` in both scorers | C1–C9 | Done (`02_…` §6.4–6.5) | — | A | P0 |
| Raw measurement → final score | — | `data/green_stop_score_trace.csv`, `suite_cells_rescored.csv` | Done; 350/350 cells and 14/14 models reproduce published values | — | B | P0 |
| Actual model inputs | adapters | traces | Done (`02_…` §2, `03_…` §3) | — | A | P0 |
| Simulation FPS, sensor, decision, control periods | `_common.py`, adapters | 112 episodes timed, including 5 new instrumented runs | Done (`03_…`) | runtime substep values; capture vs receipt time | B + C + D | P0 |
| Time-series figures and model comparison | — | `figures/` | Done for green_stop | other scenarios | B | P0 |
| Every README leaderboard number explained | `aggregate_graded_scores`, `CONFLICT_TYPE`, `marshal_metrics.aggregate` | T6–T8, `07_leaderboard_derivation.md` | Done: graded, scenarios passed, override cells and failure profile reproduced for 14/14 models directly against README.md | — | B | P0 |
| Suite aggregation and reproduction | `aggregate_graded_scores`, `_collect_sweep.py` | T3 | Done; reproduction commands in `06_reproduction.md` | missing-telemetry episodes would shrink the denominator (none today) | B | P1 |
| Hold through release | — | authority-wiring runs only | Partial: leaderboard checks 1.05 s | leaderboard re-run without early termination | D | P1 |
| Validity flag for degraded inputs | — | TCP outlier found | Open (proposal) | — | C | P1 |
| Repeat stability of VLM cells | — | 1 leaderboard episode per cell; authority 3 seeds | Partial: one cell flipped on repeat | multi-seed leaderboard | D | P1 |

## What the existing experiments already covered (re-verified here)

| Prior work | Location | Verified now |
|---|---|---|
| TCP throttle cap and uncapped comparison | `analysis/outputs/green_stop_behavior_audit/` (2026-10-15) | cap still in `tcp_model.py` post-processing; TCP peak ≤ 8.75 km/h in all 9 police-STOP TCP episodes re-scored here |
| Brake calibration | `share_2026-10-16.md` §2, `stop_feasibility.csv` | conditions and limits summarised in `02_…` §5; not usable as a guaranteed bound |
| Police STOP / PROCEED / absent / civilian controls | `outputs/authority_directive_comparison/` | used for hold/release and timing (§3 of `04_…`) |
| VLM query budget and continuous decisions | `docs/oracle_ablation.md`, authority wiring (uncapped) | cap of 3 and 1.5 s period confirmed in code and logs |
| Gesture recognition diagnosis | `reports/perception_behavior_validation/` | referenced for the recognition question; not repeated |
| Stop-line distance, speed, control, trajectory analysis | 2026-10-06/14 packages | recomputed for the current file set with the current scorer |
| Strict and graded leaderboard tables | README, `outputs/full_sweep_results.json` | reproduced exactly (T3) |

## Why new runs were needed (and only five)

Existing logs could not identify the dash-cam frame used by a VLM decision nor confirm one world frame per tick;
both required instrumentation and one run per controller family (oracle, TCP, InterFuser, Qwen3-VL). A fifth
run checked whether the TCP stale-input outlier was reproducible. Scores of existing runs were not changed.
