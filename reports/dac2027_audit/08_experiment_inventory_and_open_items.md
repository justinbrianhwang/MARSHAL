# H. Experiment inventory and evidence on the open evaluation items

```bash
python scripts/dac2027_audit.py inventory    # data/experiment_inventory.csv, data/experiment_episodes.csv
python scripts/dac2027_audit.py open-items   # data/authority_condition_summary.csv, query_cap_comparison.csv,
                                             # repeat_stability.csv, hold_verification.csv,
                                             # variant_movement_window.csv, stale_input_flags.csv, oracle_from_rest.csv
python scripts/dac2027_audit.py tables       # tables.md T9-T16
```

## 1. Scope of the numbers

The audit uses several analysis sets. They overlap and must not be added up:

| Set | Episodes | What it is |
|---|---|---|
| leaderboard cells | 350 | 14 systems × 25 scenarios, one episode each; the README numbers |
| green_stop score trace | 44 | leaderboard green_stop (14), authority police-STOP runs (25, of which 6 with the officer at 7 m), instrumented runs (5) |
| timing analysis | 112 | green_stop episodes with adapter traces or wall-clock columns (107 existing + the 5 new instrumented runs) |
| new runs of this audit | 5 instrumented + 25 oracle from rest + hold checks | only the 5 instrumented runs carry frame-level timing |
| all recorded experiments | 1,089 re-scored episodes in included experiments (1,086 unique telemetry files); excluded, duplicate and development folders are listed only | `tables.md` T9 |

No new timing instrumentation exists for the 350 leaderboard cells. Their timing statements rest on code and on
the 112 timing episodes.

## 2. Inventory (T9)

Every folder that contains episode telemetry in the workspace is assigned to exactly one experiment (0 unassigned).
Included experiments, re-scored with the current scorers:

| Experiment | Episodes | Used in |
|---|---|---|
| Leaderboard sweep | 350 | README tables, 07 |
| Authority experiment: police STOP / PROCEED / absent / civilian STOP / civilian PROCEED, 13 m and 7 m | 93 | 04 §3, T10 |
| Perception intervention (ground-truth perception text vs filler vs none) | 27 | `reports/perception_behavior_validation` §11 |
| GLM-4.5V assist ladder and controls (uncapped queries) | 250 | T11 |
| OpenEMMA assist ladder and controls | 175 | T11 |
| green_stop diagnostic panel (G_STOP / NONSTOP / ABSENT / CIVILIAN) | 21 | T12 |
| TCP paired controls (STOP / PROCEED / absent × 3) | 9 | T9 |
| TCP throttle post-processing on/off; oracle, Qwen3-VL and TCP without early termination | 15 | T9 |
| green_stop with the spawn 25 m further back | 14 | T9 |
| OpenEMMA with a GPT-4o backend | 25 | T9 |
| Oracle re-measured with the current code; current-code repeat of oracle and TCP | 25 + 50 | T12 |
| Instrumented timing runs; oracle from rest; 25 s hold checks | 5 + 25 + 5 | 03, T16, §4.2 |

Non-episode evidence: the brake calibration (02 §5), the offline gesture-recognition study
(`reports/perception_behavior_validation/`), the earlier threshold-formulation study (superseded by the current
scorer), and the earlier 3-sweep variance study (provisional tie band).

Excluded and why (all listed in T9):
- the July 3-seed oracle/baseline repeats (earlier stop-line geometry);
- ghost-officer and no-gesture vest runs (invalid stimulus);
- development regression gates of earlier builds;
- town-calibration runs in other towns;
- demo recordings, unit-test fixtures and archived copies.

Raw episodes are not in the repository; they live in the workspace (`tmp/`, `outputs/`, `analysis/`).
`data/experiment_episodes.csv` lists every included episode with the sha256 and size of its
`strict_telemetry.json`, so a released data archive can be checked file by file.

## 3. Authority experiment by condition (T10)

The expected action depends on the condition: STOP for a police STOP, PROCEED for everything else.

| Model | Police STOP (13 m) | Police STOP (7 m) | Police PROCEED | No police | Civilian STOP | Civilian PROCEED |
|---|---|---|---|---|---|---|
| oracle | 3/3 | – | – | – | – | – |
| NEAT | 2/3 | – | 0/3 | 3/3 | 0/3 | 0/3 |
| TCP | 0/3 | 0/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| InterFuser | 0/1 | – | 0/1 | 0/1 | – | – |
| Qwen3-VL | 0/3 | 1/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Qwen2.5-VL | 1/3 | – | 1/3 | 3/3 | 0/3 | 1/3 |
| GLM-4.5V | 0/3 | – | 3/3 | 3/3 | 3/3 | 3/3 |

The pattern of these results:

- **NEAT stops whenever a person stands at the road.** It holds for a police STOP (2/3) but also for a police
  PROCEED and for civilians, where it should go (0/3 each; graded about 0.25).
- **TCP, GLM-4.5V and Qwen3-VL (13 m) drive through regardless of the person.** They pass every PROCEED-type
  condition and fail the police STOP.
- **Strict and graded can disagree on a late PROCEED.** One Qwen3-VL 7 m police-PROCEED run started moving 8.9 s
  after the signal. Strict PASSES it, because PROCEED has no time limit. Graded gives 0, because its latency
  factor reaches 0 at 8 s.

## 4. Open items

### 4.1 Stop-line reference point

| | Result |
|---|---|
| Current rule | the vehicle centre must stay ≥ 1.0 m before the line, so the front bumper may be up to 1.40 m past it |
| Front-bumper variant | front bumper ≥ 0 m: 2 of 350 strict verdicts flip (oracle and Qwen3-VL on occluded_officer); oracle graded 100 → 95.29 (`data/variant_front_bumper_summary.csv`) |
| Consequence | a bumper rule needs an oracle adjusted to it before it can be adopted |

### 4.2 Hold duration (T13)

33 STOP/HOLD cells PASS on the leaderboard. In 25 of them the hold was observed until the directive ended. In the
other 8 it was not:

- 4 were cut by early termination after 1.05 s of hold (oracle green_stop and dual_authority_handoff, NEAT and
  Qwen3-VL green_stop);
- 4 are flagger_slow_then_stop (oracle, CILRS, InterFuser, GLM-4.5V). Its scored STOP phase runs from 6.05 s to
  16.05 s, but leaderboard episodes end at 14.05 s, so the last 2 s of the directive are never recorded.

The same cells observed for 25 s without early termination:

| Cell | Result |
|---|---|
| oracle green_stop | held until the release in 3/3 seeds (authority runs) |
| oracle dual_authority_handoff | held until the directive ended (PASS) |
| oracle flagger_slow_then_stop | held until 16.05 s (PASS) |
| InterFuser flagger_slow_then_stop | held until 16.05 s (PASS) |
| NEAT green_stop | held in 2 of 3 seeds (authority runs) |
| Qwen3-VL green_stop | did not stop in 3 of 3 seeds (authority runs, uncapped queries) |
| CILRS flagger_slow_then_stop | the repeat collided at 4.5 s, before the STOP phase, so the hold could not be observed |
| GLM-4.5V flagger_slow_then_stop | the repeat answered STOP at the first query and never left the spawn ("never engaged"), so the hold could not be observed |

### 4.3 Movement-evidence window (T14)

| | Result |
|---|---|
| Current rule | the movement evidence (≥ 5 km/h and ≥ 1 m) may occur anywhere in the episode, including after the directive ends |
| Variant | score STOP/HOLD only on telemetry up to the end of the directive window |
| Impact | 0 of 350 leaderboard cells change. Of the authority runs, only Qwen3-VL 7 m seed 3 changes, PASS → FAIL (it never moved during the STOP) |

### 4.4 VLM decision cadence (T11)

| Comparison | Strict PASS | Graded |
|---|---|---|
| GLM-4.5V, 25 scenarios: leaderboard (3 queries, last held) vs query every 1.5 s | 6 → 1 | 27.1 → 8.9 |
| OpenEMMA, 25 scenarios: leaderboard vs diagnostic wiring, no assist | 3 → 3 | 14.3 → 14.6 |
| Qwen3-VL green_stop: leaderboard (3 queries) vs every 1.5 s (3 seeds, 25 s) | 1/1 → 0/3 | – |

With the cap, the answer at 3.10 s is held for the rest of the episode. With uncapped queries, later answers
replace it: GLM-4.5V's passes on flagger_control, flagger_slow_then_stop, night_signal_officer_conflict,
red_proceed and unauthorized_go all turn to FAIL. The leaderboard Track-C numbers are therefore specific to the
3-query wiring.

### 4.5 Repeat stability (T12)

| Repeat | Result |
|---|---|
| oracle, current code vs leaderboard, 25 scenarios (two independent repeats) | 25/25 identical verdicts, identical credit |
| TCP, current-code repeat vs leaderboard, 25 scenarios | 25/25 identical verdicts; credit differs by at most 0.22 (mean 0.03) |
| Qwen3-VL green_stop with the officer at 13 m, unique recordings | leaderboard wiring 1 PASS of 5; uncapped queries 0 of 3 |
| Authority experiment, 3 seeds per cell | 25 of 30 model × condition cells give the same verdict in all 3 seeds |

Deterministic controllers repeat exactly. The hosted VLM cells do not, so one episode per VLM cell is not a stable
estimate.

### 4.6 Oracle initial condition (T16)

The leaderboard oracle starts STOP/HOLD/YIELD scenarios with a seeded 1.8 m/s rolling velocity; every evaluated
system starts from rest. The oracle was re-run from rest on all 25 scenarios (leaderboard wiring):

| | Result |
|---|---|
| All 25 scenarios | 24 PASS |
| The 16 STOP/HOLD/YIELD scenarios, where the rolling start applies | 16/16 PASS |
| civilian_warning_accident (DETOUR) | FAIL: one collision with the obstacle. The rolling start is not used in DETOUR scenarios, so this is run-to-run variation, not an effect of the start condition; the leaderboard run and the current-code repeat of the oracle both PASS it |

So the oracle's leaderboard result does not depend on its rolling start.

### 4.7 Stale inputs (T15)

| | Result |
|---|---|
| Episodes with adapter fallback ticks after the first tick | 6 |
| Flagged for re-run | 1 (TCP police STOP s1, 229 ticks); its re-run had 0 and the same verdict |
| Others | 1–4 ticks each, noted only |

The flag is computed from the adapter traces and does not change any score.

### 4.8 Ideal behaviour

No measured or sourced bound exists for "stopped too far before the line". The implemented bound is the
16.59 m engagement band, which the green_stop spawn already satisfies. The oracle's stop positions (front bumper
1.82–4.66 m before the line across 5 executions) are one reference policy, not a bound.
