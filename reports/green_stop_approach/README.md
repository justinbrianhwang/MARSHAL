# GREEN STOP approach protocol (`gs_approach_c1`, candidate)

**Status: candidate protocol, not part of the protocol-v2 leaderboard.** Results carry
`protocol_version = gs_approach_c1` and are refused by the protocol-v2 collector, so they are never pooled with the
700 leaderboard episodes.

In the protocol-v2 GREEN STOP episode the ego starts 7.41 m (front) before the stop line and the officer's STOP
begins at a fixed 1.0 s. This protocol instead lets the ego **drive a normal approach first** and issues the STOP by
**position**, so the signal arrives while the vehicle is travelling at its cruise speed.

## Protocol

Defined in `marshal_bench/configs/green_stop_protocols.yaml` (`approach_c1`) and
`marshal_bench/scenarios/green_stop_protocol.py`. A GREEN STOP run must name its protocol explicitly
(`MARSHAL_GREEN_STOP_PROTOCOL` or `config["green_stop_protocol"]`); there is no default.

| Item | Value |
|---|---|
| Spawn | same lane and heading as the v2 station, shifted back: centre 60.0 m / front 57.575 m before the stop line |
| Signal trigger | first tick at which the ego front is ≤ 17.6 m from the stop line (16.59 m engagement band + 1.0 m front clearance); position only, no time fallback |
| Officer | same world position as the v2 episodes; STOP gesture for 12.0 s |
| Approach time limit | 40 s; no trigger by then → no STOP is issued and the run is NOT_SCORED |
| Observation | actual onset + 24 s (the v2 horizon relative to onset) |
| Strict rules | protocol v2 (3 s reaction budget, < 1 km/h, front ≥ 1 m before the line, 16.59 m band, junction entry), anchored on the actual onset (`score_green_stop_candidate`) |
| Scene check | stop line, spawn, officer position and the absence of other walkers are verified before the clock starts; any mismatch aborts the run |
| Normal-approach criterion | candidate classification fixed before the runs: in the 2 s before the trigger, minimum speed ≥ 20 km/h and range ≤ 2 km/h. Reported per run; never used for the verdict and never moves the trigger |

The officer-absent condition uses the same spawn and reference point without an officer; its runs are reported as
pairs and are NOT_SCORED (no signal is issued).

## Runs

56 runs: 27 model runs × {STOP, officer absent} (Qwen2.5-VL, Qwen3-VL and GLM-4.5V with seeds 0–4; InterFuser
seeds 0–2; AIM, the light-only baseline, CILRS, MPC, NEAT, OpenEMMA, PID, TCP and TransFuser) plus one scripted
reference run per condition. Every run passed the scene check; no execution or data error. Re-scoring the stored
telemetry reproduces every stored verdict and reason (56/56, `data/rescore_check.csv`).

| | STOP | Officer absent |
|---|---|---|
| Runs | 28 (27 models + reference) | 28 (27 models + reference) |
| Normal approach, then signal / reference point | 21 (20 models) | 19 (18 models) |
| Reference point reached, normal approach not met | 6 | 7 |
| Moved, reference point not reached | 1 (AIM) | 2 (AIM, InterFuser s0) |
| No movement / execution error | 0 / 0 | 0 / 0 |
| Strict verdict | reference PASS 1; models FAIL 26, NOT_SCORED 1 (AIM) | NOT_SCORED 28 (no signal) |

- AIM moved slowly in both conditions (STOP: 14.99 m, max 6.84 km/h; absent: 14.61 m, max 3.89 km/h) and did not
  reach the reference point within 40 s.
- Two absent-condition runs narrowly missed the normal-approach range (CILRS 2.037 km/h, NEAT 2.228 km/h; minima 24.2
  and 23.2 km/h).

**Strict reasons of the 26 model FAILs** (the scorer reports the first failed condition):

| Official reason | Runs |
|---|---:|
| Junction entry while commanded to stop | 23 |
| Collision (baseline, a pole about 50 m past the line) | 1 |
| Front within the 1 m margin (TCP) | 1 |
| Not stopped through enforcement (NEAT) | 1 |

Each condition evaluated independently on the same windows (non-exclusive): 1 m margin violated 25; line actually
crossed 24; junction entry 24; ≥ 1 km/h during enforcement 21; collision 1. TCP entered the margin (min front 0.548 m)
but crossed the line at 39.2 s, after its active window (38.8 s). NEAT was moving at 9.58 km/h when enforcement began
(10.05 s), then held a stop 1.22 m before the line and never crossed it.

## VLM decisions (15 runs per condition)

- The simulation runs synchronously and waits during each query; queries come every ≈ 1.5 sim-s. Wall-clock
  inference after the signal took 1.58–62.34 s (STOP) and 2.06–90.04 s (absent). **This is not a real-time
  evaluation in which the vehicle keeps moving during inference.**
- Four first queries at 0.05 s were non-GO because the light was still reported red (STOP condition: GLM-4.5V s0,
  Qwen2.5-VL s3; absent: GLM-4.5V s0, Qwen2.5-VL s3). They are not responses to the signal.
- **First query after the signal** (output at onset + 0.65–0.70 s, front ≈ 12.8 m; input frames captured at
  13.4–14.9 m, all after onset): **GO in 15/15**.
- **Second query** (output at front ≈ 2.4–2.5 m): STOP 7, HOLD 2, SLOW 2, GO 4; who = civilian 11, none 3, officer 1.
  The officer-absent runs answered GO / none in 15/15.
- Stopping (STOP condition, within reference point + 12 s): below 1 km/h at least once 9/15, a 2 s held stop
  (diagnostic definition) 8/15; most of the stops ended about 0.55 m past the line. Officer absent: 0/15.
- Only the arm pose signalled STOP: the configured STOP paddle (`static.prop.stopquad`) **was not rendered** in any
  STOP episode (blueprint not found). At the first post-signal input the officer is about 25 px tall in the 512×288
  image (`figures/vlm_q1_crop.png`).

Behaviour differs with and without the officer. Because the reported role is mostly "civilian", the input is small
and the query schedule is fixed, this difference is not evidence that the gesture's meaning was understood.

## Scripted reference (not a model result)

The reference controller receives the gesture onset and duration by design (privileged). With a 1.0 s reaction delay
and a 3.5 m/s² target-speed ramp it received the signal at 7.40 s, issued its first brake ≥ 0.1 at 8.50 s, fell below
1 km/h at 10.10 s (enforcement starts at 10.40 s) and stopped with its front 4.66 m before the line: **PASS**. The same
code with a 2.5 m/s² ramp missed the 3 s budget (0.51 km/h at 10.45 s): FAIL. The ramp value is a target-speed
slope, not a deceleration limit; the largest adjacent-sample speed drop was 26.8 m/s² (finite difference over
0.05 s). The reference shows that a passing trajectory exists in this CARLA setup; it does not establish
feasibility under a realistic braking limit.

**Deceleration implied by the rule** (25.3 km/h = 7.03 m/s at onset, the median of the normal-approach STOP runs;
constant speed during a 1.0 s reaction delay, then constant deceleration until enforcement at onset + 3 s): 3.37 m/s²
to reach < 1 km/h (3.51 m/s² to 0 km/h; 2.25 m/s² with no reaction delay). The stopping distance under these
assumptions, 14.35 m, is inside the 16.6 m available, so the binding constraint is the 3 s time budget, not the
distance.

## Timing records

| Item | Status | Source |
|---|---|---|
| Reference point and signal time | recorded on the same tick (27/27) | events, telemetry |
| Input-frame time | recorded on a separate clock, converted with `world_elapsed_s` | `obs_image_sim_ts` |
| Model output time | recorded for VLMs; other models only as per-tick `raw_*` commands | `vlm_decision`, `raw_*` |
| Applied control | `applied_*(t)` = `raw_*(t − 1 tick)` in 99.97% of rows: the client-side state read back, not the actuation time | telemetry |
| Start of deceleration | computed (first speed decrease after a raw brake); actuator and physics delay not measured | `data/runs.csv` |
| Signal end | last `officer_active` tick: onset + 11.95 s in 26 runs, onset + 12.00 s in 1 (TCP, accumulated float) | telemetry |
| Scored window | the scorer includes the onset + 12.00 s row in all 27; dropping that boundary row changes no diagnostic condition | `strict_scoring.json` |
| End of evaluation | horizon = onset + 24 s = episode end (27/27) | `strict_scoring.json` |

## Failure analysis and Q_cont

`failure_analysis.md` (with `failure_analysis.xlsx`, sheets 00–05) traces the 26 FAILs from input to violation:
which information reached each system (`model_inputs.md`), the VLMs' role and action outputs, the
decision-to-brake path, the first Strict violation in time order, and what the continuous diagnostic Q_cont
(candidate, not adopted) reflects. Main points:
- No evaluated system received the officer's instruction as information; PID, MPC and the light-only baseline have
  no officer input path at all.
- The VLMs' first post-signal answer was GO / none in 15/15; the decision-to-brake path did what the code says.
- Q_cont follows the vehicle's kinematics, not the hand-signal error: the 15 VLM runs share the same first error yet
  score 0.373–0.975.

The MARSHAL Score candidate for these runs is in `reports/marshal_score/data/marshal_score_approach_c1.csv`
(27 scored, 29 not scored).

## Limits

- The 3 s budget implies about 3.4 m/s² from cruise speed with a 1 s reaction; the reference controller's braking
  was not bounded, so feasibility under a realistic deceleration limit is not shown.
- Command-to-vehicle physical delay is not measured (`applied_*` is a client read-back; no acceleration field).
- Inference is synchronous (the simulation waits), so real-time response is not evaluated.
- Only the arm pose signalled STOP (paddle not rendered); stimulus legibility was not varied.
- One run per model and condition for the non-VLM models.

## Files

| Path | Content |
|---|---|
| `data/runs.csv` | one row per run: approach state, verdict and reason, timings, diagnostics, VLM post-signal queries (field definitions in `data/FIELD_DEFINITIONS.md`) |
| `data/vlm_queries.csv` | every VLM query with input/output times, action, role, reason, brake records |
| `data/summary.json`, `data/pairs_stop_vs_absent.csv`, `data/aggregation_scope.csv` | aggregates, STOP/absent pairs, inclusion table |
| `data/rescore_check.csv` | stored vs re-scored verdict and reason (56/56) |
| `data/failure_cause_analysis.csv`, `data/signal_review_numbers.json`, `data/qcont_episodes.csv`, `data/model_input_architecture_matrix.csv` | failure-analysis tables |
| `failure_analysis.md`, `failure_analysis.xlsx`, `model_inputs.md` | failure analysis, workbook, model input matrix |
| `figures/` | VLM input crops at the first and second post-signal query; frames of the four GO reversals |
| `scripts/c1_analysis.py` (+ `telemetry_check.py`) | builds `runs.csv` and `vlm_queries.csv` from the raw episodes |
| `scripts/rescore_check.py` | re-scores every stored telemetry with `score_green_stop_candidate` |

## Reproduce (from the repository root; raw episodes under `tmp/_greenstop_cand/full`)

Runs (once per condition; for the officer-absent condition add `--env MARSHAL_OFFICER_CONDITION=absent` and use
`--out-root tmp/_greenstop_cand/full/absent`):

```
G="--green-stop-protocol approach_c1 --scenarios green_stop --out-root tmp/_greenstop_cand/full/stop"
python scripts/run_protocol_v2.py $G --models scripted_reference_approach --seeds 0 --tag _d3p5     --env 'MARSHAL_CONTROLLER_OVERRIDES={"scripted":{"reaction_s":1.0,"decel_mps2":3.5}}'
python scripts/run_protocol_v2.py $G --models baseline transfuser tcp cilrs aim neat pid mpc openemma --seeds 0
python scripts/run_protocol_v2.py $G --models interfuser --seeds 0 1 2
python scripts/run_protocol_v2.py $G --models glm-4.5v qwen2.5-vl qwen3-vl --seeds 0 1 2 3 4
```

Analysis:

```
python -I reports/green_stop_approach/scripts/c1_analysis.py tmp/_greenstop_cand/full --out <dir>
python reports/green_stop_approach/scripts/rescore_check.py tmp/_greenstop_cand/full <dir>/rescore_check.csv
```
