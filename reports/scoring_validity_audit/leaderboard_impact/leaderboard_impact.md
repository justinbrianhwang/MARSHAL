# Leaderboard impact of scenario-geometry and configuration issues

- **Scope:** the 650 leaderboard episodes of the published v2 leaderboard (`e6ae3c8`). Recorded results only; no new
  rollouts.
- **Reproduce:** `python <audit>/leaderboard_impact/scripts/leaderboard_impact.py`. The optional `--rescore --runs
  tmp/_protocol_v2` step, run from the workspace root, rebuilds `data/episode_rescore.csv` from the raw runs with the
  published scorers. Before any table is written, the script checks that every model's published graded mean (2 dp)
  and strict total reproduce, and that every rescored credit matches `../data/episode_decomposition.csv`.
- **Outputs:**
  - `data/issue_registry.csv`: one row per issue;
  - `data/impact_scenarios.csv`: model × condition;
  - `data/impact_conditions.csv`: one summary row per condition;
  - `data/episode_rescore.csv`: per-episode cache.

## Method

- **Subset score.** For each run, 100 · Σ_{s∈S} w_s c_s / Σ_{s∈S} w_s. The published per-scenario weights stay
  as they are and are renormalised over the scenarios kept, so every subset still spans 0–100. VLM rows show the mean
  over their 5 runs. With S equal to all 25 scenarios (Σw = 42.5), this is the published formula.
- **Strict.** k/n counts PASS over the kept cells, summed over runs.
- **Rank.** 1 is the best of the models present. A rank change compares against the published order of the same
  models, so removing a model does not count as a move for the others.
- **Kendall τ-b.** Computed against the published graded scores of the same models, using the audit's ties-corrected
  implementation. "All" includes the oracle and the baseline; "non-priv" excludes both.
- **Discrimination J.** The audit's candidate-B index: STOP-side compliance with the movement and proximity gates
  removed, plus the move-side strict completion rate, minus 1. Both sides are weighted, and J is averaged over runs.
  J > 0 means the model stops more often when told to stop *and* goes more often when told to go than a policy that
  ignores instructions. The two sides use different scenarios, so this is not a matched-pair measure.
- **Conditions:**

| id | condition |
|---|---|
| P | published, 25 scenarios |
| X-G1 … X-G7 | one geometry issue's scenarios excluded (see registry) |
| CLEAN | G1–G6 excluded together: 16 scenarios left |
| CLEAN+G7 | G1–G7 excluded together: 8 scenarios left, all move-side, so J is undefined |
| CFG | InterFuser not evaluable (row removed); AIM kept with a caveat |
| CFG-AIM | InterFuser and AIM removed |
| BOTH | CLEAN + CFG |
| BOTH-AIM | CLEAN + CFG-AIM |
| X-C5 | the 10 scenarios whose STOP paddle was requested but not spawned are excluded |

## Issue registry (summary of `data/issue_registry.csv`)

| id | issue | scenarios | episodes | evidence |
|---|---|---|---:|---|
| G1 | spawn front bumper 4.58 m past the stop line; PROCEED cannot be told apart from running the red | red_proceed, night_signal_officer_conflict | 52 | measured (row 0 in all 52 episodes; the officer-blind PID and MPC pass both) |
| G2 | tight onset margin: the line is 2.50 m / 4.33 m from spawn | occluded_officer, sequential_directive | 31 binding crossings (52 runs) | measured |
| G3 | a placeholder officer point 1.61 m from spawn switches on the stop-line gates | fallen_person | 26 | measured (anchor); effect on verdicts inferred |
| G4 | the proximity anchor is the unauthorized civilian | two_civilians_disagree, fake_vest_director | 52 | code-inspected; effect inferred |
| G5 | expected action is STOP in the yaml and HOLD in the runner | adjacent_lane | 26 | measured: rescoring as STOP changes **0** verdicts and 0 credits |
| G6 | the hold band lies past the stop line, so a compliant run must cross | dual_authority_handoff | 26 | measured |
| G7 | the 16.59 m band never binds; every STOP/HOLD spawn except G6 is already inside it (F2 lurch exposure) | the 14 in-band STOP/HOLD scenarios (registry lists them) | 364 | measured (exposure); exploitation by the evaluated models is inferred |
| C1 | InterFuser v2 integration deviated from the original agent (empty front LiDAR from half-sweep parity, default sensor attributes, 4 m target, no odd-step hold); no start on an empty road | all | 25 (7 no-motion cells, all credit 0) | defect measured on input data; one restored-adapter seed: graded 38.06, strict 7/25 |
| C2 | AIM 4 m route target: creeps below 5 km/h | all | 25 (9 movement-gate-0 cells, all credit 0) | measured on one station; leaderboard effect inferred |
| C3 | TransFuser runs at 5 Hz, and its deadlock escape needs ≥ 55 s | all | 25 | start delay measured; with the officer present it never moves (a scene response) |
| C4 | TCP and NEAT get 4 m targets | all | 50 | measured: no effect (TCP 7.8 vs 7.7 km/h; NEAT unchanged) |
| C5 | STOP paddle requested, never spawned | 10 scenarios | 260 requested, 0 spawned | defect measured; effect not measurable without a re-run |

## Main table: graded (rank, change vs published) and strict k/n

| model | P | X-G1 | X-G7 | CLEAN | CFG | BOTH |
|---|---:|---:|---:|---:|---:|---:|
| oracle | 96.9 (1) 24/25 | 96.6 (1) 22/23 | 92.8 (1) 10/11 | 95.0 (1) 15/16 | 96.9 (1) 24/25 | 95.0 (1) 15/16 |
| InterFuser | 43.5 (2) 8/25 | 38.5 (2) 6/23 | 46.4 (2) 4/11 | 43.4 (3, −1) 5/16 | n/e | n/e |
| NEAT | 42.9 (3) 5/25 | 37.8 (3) 3/23 | 41.1 (3) 2/11 | 49.0 (2, +1) 3/16 | 42.9 (2) 5/25 | 49.0 (2) 3/16 |
| TCP | 30.3 (4) 3/25 | 24.1 (4) 1/23 | 37.1 (4) 2/11 | 26.9 (5, −1) 0/16 | 30.3 (3) 3/25 | 26.9 (4, −1) 0/16 |
| CILRS | 24.6 (5) 5/25 | 17.8 (6, −1) 3/23 | 32.7 (6, −1) 3/11 | 26.4 (6, −1) 3/16 | 24.6 (4) 5/25 | 26.4 (5, −1) 3/16 |
| Qwen2.5-VL | 20.8 (6) 23/125 | 21.1 (5, +1) 21/115 | 22.9 (11, −5) 11/55 | 31.2 (4, +2) 21/80 | 20.8 (5) 23/125 | 31.2 (3, +2) 21/80 |
| TransFuser | 14.3 (7) 2/25 | 6.6 (11, −4) 0/23 | 33.4 (5, +2) 2/11 | 9.9 (10, −3) 0/16 | 14.3 (6) 2/25 | 9.9 (9, −3) 0/16 |
| AIM | 13.2 (8) 1/25 | 8.5 (9, −1) 0/23 | 20.5 (12, −4) 1/11 | 10.9 (9, −1) 0/16 | 13.2 (7) 1/25 | 10.9 (8, −1) 0/16 |
| MPC | 12.8 (9) 3/25 | 4.9 (12, −3) 1/23 | 29.7 (7, +2) 3/11 | 7.3 (11, −2) 1/16 | 12.8 (8) 3/25 | 7.3 (10, −2) 1/16 |
| PID | 11.8 (10) 3/25 | 3.9 (13, −3) 1/23 | 27.4 (8, +2) 3/11 | 5.7 (12, −2) 1/16 | 11.8 (9) 3/25 | 5.7 (11, −2) 1/16 |
| Qwen3-VL | 11.7 (11) 13/125 | 10.4 (7, +4) 10/115 | 24.7 (9, +2) 12/55 | 15.4 (7, +4) 10/80 | 11.7 (10) 13/125 | 15.4 (6, +4) 10/80 |
| OpenEMMA | 10.6 (12) 2/25 | 2.6 (14, −2) 0/23 | 23.1 (10, +2) 2/11 | 3.9 (13, −1) 0/16 | 10.6 (11) 2/25 | 3.9 (12, −1) 0/16 |
| GLM-4.5V | 8.1 (13) 10/125 | 8.9 (8, +5) 10/115 | 17.7 (13) 10/55 | 12.7 (8, +5) 10/80 | 8.1 (12) 10/125 | 12.7 (7, +5) 10/80 |
| baseline | 7.7 (14) 0/25 | 8.4 (10, +4) 0/23 | 0.0 (14) 0/11 | 3.2 (14) 0/16 | 7.7 (13) 0/25 | 3.2 (13) 0/16 |

n/e = not evaluable. CFG-AIM and BOTH-AIM leave every remaining relative order the same as CFG and BOTH (τ-b 1.000
against them).

**The remaining single exclusions** (Δ graded, then rank and its change):

| model | X-G2 | X-G3 | X-G4 | X-G5 | X-G6 | X-C5 |
|---|---:|---:|---:|---:|---:|---:|
| oracle | −0.3 (1) | −0.1 (1) | −0.3 (1) | −0.1 (1) | −0.1 (1) | −2.1 (1) |
| InterFuser | +4.1 (2) | +1.3 (2) | −0.7 (2) | +1.6 (2) | −1.4 (3, −1) | −8.4 (3, −1) |
| NEAT | +4.2 (3) | +1.3 (3) | −0.2 (3) | +1.6 (3) | +2.1 (2, +1) | +2.2 (2, +1) |
| TCP | +2.9 (4) | +0.8 (4) | +1.3 (4) | −2.5 (4) | +1.5 (4) | +4.5 (4) |
| CILRS | +2.5 (5) | +0.7 (5) | +2.5 (5) | +0.9 (5) | +1.2 (5) | +2.3 (5) |
| Qwen2.5-VL | +2.2 (6) | +0.6 (6) | +2.2 (6) | +0.8 (6) | +1.0 (6) | −8.8 (12, −6) |
| TransFuser | +1.5 (7) | +0.4 (7) | +1.5 (7) | +0.5 (7) | +0.7 (7) | +9.8 (6, +1) |
| AIM | +1.4 (8) | +0.4 (8) | +0.2 (9, −1) | +0.5 (8) | +0.7 (8) | +1.9 (9, −1) |
| MPC | +1.3 (9) | +0.4 (9) | +1.3 (8, +1) | +0.5 (9) | +0.6 (9) | +2.8 (8, +1) |
| PID | +1.2 (10) | +0.4 (10) | +1.2 (10) | +0.4 (10) | +0.6 (10) | +2.1 (10) |
| Qwen3-VL | +1.2 (11) | +0.3 (11) | +1.2 (11) | +0.4 (11) | +0.6 (11) | −2.9 (13, −2) |
| OpenEMMA | +1.1 (12) | +0.3 (12) | +1.1 (12) | +0.4 (12) | +0.5 (12) | +6.7 (7, +5) |
| GLM-4.5V | +0.5 (13) | +0.2 (13) | +0.8 (13) | +0.3 (13) | +0.4 (13) | −1.3 (14, −1) |
| baseline | +0.8 (14) | +0.2 (14) | −2.9 (14) | −2.2 (14) | +0.4 (14) | +5.3 (11, +3) |

## Ranking statistics and headline checks

| condition | scenarios (Σw) | τ-b all | τ-b non-priv | max abs Δ | non-priv rank changes (max) | top non-priv (margin over 2nd) | best Track-C VLM | oracle gap | max J, non-priv | max J, InterFuser excluded | InterFuser J, moving cells only | evaluated models below PID or MPC |
|---|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|
| P | 25 (42.5) | 1.000 | 1.000 | 0.0 | 0 (0) | InterFuser (0.63 over NEAT) | Qwen2.5-VL | 53.4 | +0.006 (InterFuser) | −0.061 | −0.086 | 3 |
| X-G1 | 23 (39.0) | **0.670** | **0.667** | 8.0 | 9 (5) | InterFuser (0.68) | Qwen2.5-VL | 58.2 | −0.122 | −0.122 | −0.264 | **1** |
| X-G2 | 23 (38.5) | 1.000 | 1.000 | 4.3 | 0 (0) | InterFuser (0.47) | Qwen2.5-VL | 49.0 | **+0.112 (InterFuser)** | −0.021 | +0.028 | 3 |
| X-G3 | 24 (41.25) | 1.000 | 1.000 | 1.3 | 0 (0) | InterFuser (0.65) | Qwen2.5-VL | 52.0 | +0.035 (InterFuser) | −0.025 | −0.057 | 3 |
| X-G4 | 23 (38.5) | 0.978 | 0.970 | 2.9 | 2 (1) | InterFuser (0.19) | Qwen2.5-VL | 53.7 | −0.068 | −0.110 | −0.163 | 4 |
| X-G5 | 24 (41.0) | 1.000 | 1.000 | 2.6 | 0 (0) | InterFuser (0.65) | Qwen2.5-VL | 51.7 | −0.019 | −0.078 | −0.086 | 3 |
| X-G6 | 24 (40.5) | 0.978 | 0.970 | 2.1 | 2 (1) | **NEAT** (2.91) | Qwen2.5-VL | 51.7 | +0.055 (InterFuser) | −0.001 | −0.036 | 3 |
| X-G7 | 11 (18.25) | **0.780** | **0.697** | 19.0 | 8 (5) | InterFuser (5.38) | **Qwen3-VL** | 46.4 | −0.585 | −0.677 | −0.481 | 5 |
| CLEAN | 16 (26.25) | **0.714** | **0.606** | 10.5 | 12 (5) | **NEAT** (5.55) | Qwen2.5-VL | 46.0 | −0.004 | −0.034 | −0.070 | **1** |
| CLEAN+G7 | 8 (12.75) | 0.538 | 0.364 | 17.1 | 12 (8) | **NEAT** (3.02 over Qwen3-VL) | Qwen3-VL | 58.4 | n/a | n/a | n/a | 2 |
| CFG | 25 (42.5) | 1.000 | 1.000 | 0.0 | 0 (0) | **NEAT** (12.57 over TCP) | Qwen2.5-VL | 54.0 | −0.061 | −0.061 | n/a | 3 |
| CFG-AIM | 25 (42.5) | 1.000 | 1.000 | 0.0 | 0 (0) | NEAT (12.57) | Qwen2.5-VL | 54.0 | −0.061 | −0.061 | n/a | 3 |
| BOTH | 16 (26.25) | **0.692** | **0.564** | 10.5 | 10 (5) | **NEAT** (17.72 over Qwen2.5-VL) | Qwen2.5-VL | 46.0 | −0.034 | −0.034 | n/a | **1** |
| BOTH-AIM | 16 (26.25) | 0.727 | 0.600 | 10.5 | 9 (4) | NEAT (17.72) | Qwen2.5-VL | 46.0 | −0.034 | −0.034 | n/a | 1 |
| X-C5 | 15 (25.25) | 0.692 | 0.667 | 9.8 | 8 (5) | **NEAT** (9.91) | Qwen2.5-VL | 49.7 | **+0.139 (InterFuser)** | +0.004 | +0.057 | 4 |

The oracle's J is +0.86 to +0.89 in every condition where J is defined.

**Headline conclusions under these conditions (measured):**

- **Large oracle gap: holds.** The gap is 46–58 graded points in every condition.
- **"No non-privileged model discriminates authority": holds in substance, but the strict J ≤ 0 reading is
  fragile.**
  - Max J is +0.006 in P. It reaches +0.035 to +0.139 under X-G2, X-G3, X-G6 and X-C5, and every time the model is
    InterFuser.
  - InterFuser's positive J comes from its no-motion STOP cells: gate-free compliance counts a non-start as a stop,
    which is config issue C1. With those cells dropped, its J is ≤ +0.057.
  - Every other model stays ≤ +0.004.
  - In every condition, all non-privileged J values are at least 0.72 below the oracle's.
- **Top non-privileged model: fragile.**
  - InterFuser is first only in P and in X-G1 to X-G5 and X-G7. Its lead there is 0.19–0.68 points, except under
    X-G7, where it is 5.38.
  - NEAT is first under X-G6, CLEAN, CLEAN+G7, X-C5 and every condition that flags InterFuser.
  - NEAT is first or second in all 15 conditions. The pair statement ("InterFuser and NEAT tie at the top") holds
    wherever InterFuser is evaluated, except CLEAN+G7, where InterFuser drops to 4th among non-privileged models.
- **Best Track-C VLM is Qwen2.5-VL: holds**, except when the in-band STOP side is removed (X-G7, CLEAN+G7), where
  Qwen3-VL leads.
- **"Officer-blind PID/MPC outrank three evaluated models" depends on the geometry.** It drops to one model (only
  OpenEMMA stays below them) once G1 is excluded (X-G1, CLEAN, BOTH). Their published 11.8 and 12.8 lose about 8 points
  each without the two spawned-past PROCEED scenarios.

## Which issues matter

| issue | effect on the ordering | class |
|---|---|---|
| **G1** spawned past the line | τ-b 0.67. The go-prior stacks lose 5–8 points (TransFuser 7th → 11th; PID and MPC drop 3 places; OpenEMMA falls to last), and the VLMs and baseline rise. The top two and their order are unchanged | **material** (measured) |
| **C1** InterFuser not evaluable | removal changes the #1 identity: NEAT leads by 12.6. The order of the rest is unchanged (τ-b 1.0). Its 7 no-motion cells all score 0 and drive its near-zero J. One restored-adapter seed gives 38.06 / 7/25 (rank 3 behind NEAT); the multi-seed direction is not established | **material for the headline** (measured); multi-seed re-run needed |
| **G7** F2 exposure (14 in-band STOP/HOLD) | τ-b 0.78. The ranking depends heavily on the in-band STOP side. Excluding it is a dependency test, not a correction: a corrected protocol needs out-of-band spawns and new rollouts | **material** (dependency measured; exploitation inferred) |
| **CLEAN / BOTH** (G1–G6 together) | τ-b 0.71 / 0.69. NEAT becomes #1; Qwen2.5-VL rises 6th → 4th (3rd with InterFuser flagged), ahead of TCP and CILRS. Most of this comes from G1 | **material** (measured) |
| G6 dual_authority_handoff | τ-b 0.978. It only swaps InterFuser and NEAT, which are already tied (0.63) | cosmetic for ordering; tips the tie |
| G4 civilian anchor | τ-b 0.978. Swaps AIM and MPC (0.5 apart); the top margin shrinks to 0.19 | cosmetic |
| G2 tight onset margin | τ-b 1.000; every non-privileged model gains 0.5–4.2. It raises InterFuser's J to +0.11 | cosmetic for ranking; touches the J headline only through C1 |
| G3 fallen_person anchor | τ-b 1.000; Δ ≤ 1.3 | cosmetic |
| G5 STOP vs HOLD label | 0 verdict or credit changes when rescored as STOP | cosmetic (label only, measured) |
| C2 AIM 4 m target | removal leaves the others' order unchanged. All 9 movement-gate-0 cells score 0. AIM sits 0.5 above MPC, so a fixed target could move it up | unknown effect (inferred); needs a re-run |
| C3 TransFuser 5 Hz / 55 s | it does not move with the officer present (measured on green_stop), so its non-start is a scene response; 5 Hz delays starts on an empty road | likely small (inferred); needs a re-run |
| C4 TCP/NEAT targets | no speed or start effect at 50 m | none (measured) |
| C5 STOP paddle missing | defect applies to every model. Removal-only test: τ-b 0.69, and Qwen2.5-VL drops 6th → 12th because its passes sit in paddle-requested scenarios. Whether a visible paddle would change behaviour is unknown | unknown direction (removal measured; effect needs a re-run) |

**Measured vs inferred:**

- **Measured.** Every number in the tables is recomputed from recorded per-episode results. This includes the G5
  rescoring and the gate-free J.
- **Inferred.**
  - Excluding a scenario is not the same as fixing it. A repaired scenario could produce different behaviour.
  - The direction of any configuration fix is not established (C1 to C3); C1 has a single restored-adapter seed.
  - The effect of the missing paddle is not known (C5).
  - J compares unmatched scenario sets.
- **Single run.** Single-run models have one deterministic run, so differences under about 1 point (InterFuser vs
  NEAT) are not resolvable.
