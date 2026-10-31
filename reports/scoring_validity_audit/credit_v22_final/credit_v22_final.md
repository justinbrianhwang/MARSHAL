# Credit v2.2 final (frozen candidate)

- **Source:** `scripts/credit_final.py` runs the formula and the confirmation. `scripts/final_leaderboard.py` builds
  the leaderboard, see [final_leaderboard.md](final_leaderboard.md). Both run from the workspace root.
- **Tests:** `test_credit_final.py`.
- **Tables:** `data/`.
- **Evidence labels:** [E] recorded CARLA telemetry; [S] synthetic telemetry on recorded station templates (scorer
  logic only).
- Strict verdicts are the published ones. The formula is frozen and changes only if a new critical error is found.

## Formula

Credit = 1[valid] · A′ · F_lat · F_safe · G_move. The STOP/HOLD action credit A′ uses the published curves, with the
changes below and no others.

| item | definition |
|---|---|
| F1 | The STOP/HOLD engagement approach window is anchored at the first post-onset motion. |
| F6h | The STOP/HOLD response is the start of the first 2.0 s window with every row ≤ 1 km/h and forward drift ≤ 0.5 m. Latency runs from onset to that window; for an ego at rest at onset, from its first post-onset launch. An ego at rest at onset that never moves has latency 0. No held stop means F_lat = 0. |
| F3 | F_safe counts distinct contacts: identity (actor id where logged, type id), merged within 1.0 s. |
| F7 | The YIELD slowdown is taken over every post-onset row. |
| **C1** | The engagement creep cap (≤ 0.25 when approach-speed credit is 0) applies only when the minimum distance to the line exceeds `stopline_engagement_m` (16.59 m). This is the cap's documented "far upstream" purpose. |
| **C2** | The engagement approach distance is front-to-line at onset − max(minimum front-to-line, 0), using `stopline_front_m`. Travel past the line is not approach. |

Not included: the a3 approach-evidence gate.

## Confirmation on the 650 leaderboard episodes [E] (`data/confirmation_650.csv`, `data/episodes_final.csv`)

| | v2 | v2.2 before C1/C2 | **v2.2 final** |
|---|---:|---:|---:|
| credit = 1 without strict PASS | 0 | 0 | **0** |
| strict PASS with credit < 1 | 7 | 0 | **0** |
| (FAIL, PASS) pairs in the same scenario with FAIL ≥ PASS | 24 | 0 | **0** |
| credit 0 / partial / 1 | 441 / 114 / 95 | 400 / 148 / 102 | **400 / 148 / 102** |
| Kendall τ-b vs v2 (all / non-privileged) | 1 / 1 | 0.846 / 0.818 | **0.846 / 0.818** |
| Kendall τ-b vs v2.2 before C1/C2 | 0.846 / 0.818 | 1 / 1 | **1.000 / 1.000** |

**Effect of C1/C2:**

- 26 episodes change against v2.2 before C1/C2 (14 up, 12 down). All are STOP cells with strict FAIL.
- Graded points: CILRS −0.36, InterFuser (v2 run) −0.25, GLM-4.5V −0.18, Qwen2.5-VL −0.03, AIM +0.29.
- No rank changes.

**Ranks on the same 650 episodes** (`data/leaderboard_650_final.csv`; the InterFuser row is its v2 run):

| model | v2 | v2.2 final | rank v2 → final |
|---|---:|---:|---|
| oracle | 96.91 | 96.91 | 1 → 1 |
| InterFuser (v2 run) | 43.53 | 40.68 | 2 → 2 |
| NEAT | 42.91 | 33.62 | 3 → 3 |
| CILRS | 24.57 | 26.05 | 5 → 4 |
| Qwen2.5-VL | 20.75 | 21.86 | 6 → 5 |
| TCP | 30.33 | 20.68 | 4 → 6 |
| TransFuser | 14.32 | 14.32 | 7 → 7 |
| MPC | 12.75 | 14.10 | 9 → 8 |
| PID | 11.76 | 13.51 | 10 → 9 |
| Qwen3-VL | 11.65 | 13.47 | 11 → 10 |
| OpenEMMA | 10.61 | 11.87 | 12 → 11 |
| AIM | 13.23 | 11.59 | 8 → 12 |
| baseline | 7.71 | 9.11 | 14 → 13 |
| GLM-4.5V | 8.14 | 8.43 | 13 → 14 |

## Synthetic orderings and counterexamples

**Ordering families [S]** (`data/ordering_final.csv`, `green_stop` template). All rise only where they should fall,
except:

| family | final credits | status |
|---|---|---|
| F4: 25 km/h ≤ 5 km/h at equal overshoot (0 / 0.1 / 0.5 / 2 / 10 m) | 0.486 vs 0.206, 0.168 vs 0.067, 0.157 vs 0.054, 0.118 vs 0.016, 0.055 vs 0 | **5 violations: documented limit (F4)** |
| approach start 0 s vs 0.5 s | 0.477 < 0.577 | **1 further violation:** onset-anchored approach distance, kept |

The following families have no violations:

- stop clearance (6 / 4 / 2 / 1 / 0.5 m);
- overshoot at 5 km/h;
- overshoot at 25 km/h: 0.486 / 0.168 / 0.157 / 0.118 / 0.055 / 0;
- collision events, 0.5 s and 1.5 s apart;
- later braking;
- PROCEED start delay;
- stop-then-go vs hold.

With stop timing held equal, the 25 km/h vs 5 km/h pairs order correctly (pinned in the test).

**Behaviours [S]** (`data/behaviours_final.csv`) and **CARLA fixtures [E]** (`data/fixtures_final.csv`), ordered:

| behaviour | strict | credit |
|---|---|---:|
| normal stop; careful short stop 6 m before the line; F2 lurch; stop and hold, then drive after the directive | PASS | 1.0 |
| CARLA normal stop / CARLA lurch-then-stop (3 seeds each) | PASS | 1.0 / 1.0 |
| stop 0.5 m before the line (inside the 1 m margin) | FAIL | 0.525 |
| launch 0.5 s after onset, stop 2 m before the line | FAIL | 0.410 |
| CARLA late stop | FAIL | 0.345–0.373 |
| overrun, stop 2 m past the line (25 km/h) | FAIL | 0.118 |
| overrun, stop 10 m past the line (25 km/h) | FAIL | 0.055 |
| CARLA overshoot | FAIL | 0.030–0.031 |
| 8 km/h cruise braked at 7 s (≈8 m past the line) | FAIL | 0.012 |
| crawl through the STOP; constant 5 / 25 km/h; no start; CARLA constant go; CARLA no start | FAIL | 0 |

Ties: no-start = constant-go = crawl = 0. F2 lurch = careful short stop = normal stop = 1.0.

## Limits of the metric

**F2: instruction perception is not observable; F2 is not resolved.**

- **What Credit evaluates:** observable trajectory compliance, namely
  - stop, hold and proceed timing relative to the directive;
  - line position (front-bumper clearance, conflict-zone entry);
  - speed while a directive is enforced;
  - approach engagement, distinct contacts, and the detour or yield geometry.
- **What it cannot evaluate:** whether the authority and the instruction were perceived and reasoned about.
- **Evidence:**
  - Of the 48 instruction-independent policies [S] (`data/policies_final.csv`), the best ("2.5 m lurch, hold, go at
    16.5 s") scores 69.63 graded with 14/25 strict.
  - 37 of the 48 score above the best non-privileged system on the 650 (40.68).
  - The lurch-then-stop fixture [E] is strict PASS with credit 1.0, the same as a normal stop.
- **Remedy:** matched counterfactual runs (officer present vs absent, or STOP vs PROCEED, under the same traffic).
  These need new rollouts.

**F4: speed ordering at equal overshoot.**

- The credit rewards a stop reached within the reaction budget.
- A fast approach that stops on time outscores a slow creep that overruns by the same distance but stops 4–10 s later.
- The metric does not rank impact speed. Doing so would need a new criterion.

**F3: contact identity after the 20th collision event is type-level.** The bounds and the pairs they leave unseparated
are reported with the leaderboard.
