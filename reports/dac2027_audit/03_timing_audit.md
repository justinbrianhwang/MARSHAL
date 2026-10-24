# C. Simulation, sensor, decision and control timing audit

Labels as in `02_evaluation_spec_green_stop.md`. "Sim" = simulation time, "wall" = elapsed real time on the
`time.perf_counter()` monotonic clock of the scenario process. Per-episode numbers:
`data/timing_per_episode.csv` (112 episodes); grouped: `data/timing_summary.csv`.

## 1. What was available and what was added

| Needed | Before this audit | Added |
|---|---|---|
| sim time per tick | `sim_time` (tick count × 0.05) | — |
| wall time per tick | `wall_monotonic_s` (runs from 2026-10 on; not in the July leaderboard runs) | — |
| world frame id, snapshot clock | not logged | `world_frame` (return of `world.tick()`), `world_elapsed_s` (snapshot timestamp) |
| dash-cam frame handed to the controller | not logged (image only) | `obs_image_frame`, `obs_image_sim_ts`, `obs_image_cb_wall_s` (callback receipt, same clock) |
| controller step duration | not logged | `ctrl_step_wall_s` |
| E2E adapter input frames | `*_trace.csv`: world frame + every sensor frame consumed, mode per tick | — |
| VLM queries | `vlm_trace.csv` (per tick: query flag, wall latency, active decision); `vlm_decision` events | `input_frame`, `input_sim_ts` per query |
| controller output vs vehicle state | `raw_*` (returned control) and `applied_*` (`get_control()` readback) | — |

Code: `marshal_bench/scenarios/_common.py` (ego camera callback, `_build_observation`, tick loop),
`marshal_bench/controllers/vlm_model.py` (trace columns). Behaviour unchanged: the image passed to the
controller is still `latest_ego_frame`; metadata is attached only when it belongs to that same array (617 tests
pass). Instrumented runs:
`outputs/dac2027_audit/runs/` (DEV workspace), driver `outputs/dac2027_audit/logs/batch_timing.sh`.

**Why five new runs.** The recorded logs could not answer (a) which dash-cam frame a VLM decision used and
(b) whether the world advances exactly one fixed step per tick. One green_stop episode per controller family
under the leaderboard wiring answers both: oracle (privileged, no sensors), TCP and InterFuser (synchronised
multi-sensor adapters), Qwen3-VL (blocking API). The fifth run repeats the authority-experiment TCP police-STOP
seed 1, whose log showed 229 stale-input ticks, to see whether that was reproducible.

## 2. Simulator clock

| Item | Setting / observation | Evidence |
|---|---|---|
| CARLA | 0.9.16 | [CODE] `carla_api_compat.py` version probe; [LOG] build in use |
| Mode | `synchronous_mode=True`, `fixed_delta_seconds=0.05`, set by `SyncModeContext` for the whole episode | [CODE] `carla_api_compat.py:207-230`, `_common.py:2178` |
| Who ticks | the scenario process, once per loop iteration: `world.tick()` → controller.step → `apply_control` | [CODE] `_common.py:2232-2312` |
| Traffic Manager | set to synchronous in both the autopilot and controller paths | [CODE] `_common.py:1010, 2196` |
| Physics substeps | not set by MARSHAL; `WorldSettings` is constructed with only the three fields above, so CARLA defaults apply (substepping on, ≤ 10 substeps of ≤ 0.01 s; 0.05 ≤ 0.1 satisfies the documented condition) | [CODE]; [CONFIG-only] the actual substep values were not read back at runtime |
| Observed step | world frame +1 on every tick (5/5 instrumented runs, 1 281 tick-to-tick steps); snapshot clock advances exactly 0.05 s per tick (its offset to episode `sim_time` is constant; the absolute offset is the world's age at episode start) | [LOG] `world_frame_step`, `world_elapsed_minus_sim_time_max_abs` |
| Observed `sim_time` step | 0.05 s on every tick in all 112 episodes | [LOG] `sim_dt_observed` |
| Sim / wall ratio | 0.069–0.275 (median per group 0.09–0.27): the simulation runs about 4–15× slower than real time | [LOG] `sim_over_wall` |
| Wall per tick (median) | oracle 0.013–0.040 s; VLM 0.013–0.021 s between queries; E2E 0.19–0.38 s | [LOG] |

Official reference: CARLA 0.9.16 "Synchrony and time-step"
(https://carla.readthedocs.io/en/0.9.16/adv_synchrony_timestep/) — synchronous mode should always use a fixed
step; substepping requires `fixed_delta_seconds <= max_substep_delta_time * max_substeps`; GPU sensors usually
deliver data a couple of frames late; the Traffic Manager must also be synchronous.

**Consequence.** Every behavioural time in the scores is simulation time. Because the server waits for the
client, wall-clock inference time never advances the vehicle: it lengthens the run, not the reaction.

## 3. Sensors and model inputs

| Model | Sensor streams | `sensor_tick` | Frame actually consumed | Evidence |
|---|---|---|---|---|
| TCP | 3 × RGB 1600×900 | 0.05 s | waits up to 0.75 s wall for all three frames of the **current** world frame; age 0 on every inferred tick in all TCP traces | [CODE] `tcp_model.py:151-182, 444-468`; [LOG] `sensor_frame_age_ticks = {0}` |
| InterFuser | 3 × RGB + LiDAR + IMU | 0.05 s | current frame for all five streams, age 0 | [LOG] |
| NEAT / AIM / CILRS / TransFuser / OpenEMMA | camera(s) ± LiDAR/IMU/GNSS | 0.05 s | current frame, age 0 | [LOG] traces in `tmp/` |
| VLM (and oracle, unused) | ego dash-cam 1280×720 | not set (every frame) | **latest frame the callback has delivered, not the current one**: age 1–6 ticks (0.05–0.30 s); at the three Qwen3-VL queries 1, 6, 2 ticks | [CODE] `_common.py` ego camera callback; [LOG] `dashcam_frame_age_ticks`, `vlm_input_frame_age_ticks` |

The dash-cam callback converts the image and writes a PNG for every frame. The larger ages occur when the loop
ticks fast (VLM and oracle runs, ~0.01 s per tick), which is consistent with a callback backlog; GPU sensor
latency, callback scheduling and disk writes were not separated. The
synchronised E2E adapters do not have this problem because they wait for the matching frame. Each model gets a
single frame per decision (no frame stacks); TCP stitches its three cameras into one panorama.

**Stale-input fallback (TCP).** When the three frames do not arrive within 0.75 s wall, TCP repeats its previous
control (`mode = no_synced_sensors`, `tcp_model.py:164-180`). In a separate scan of every TCP adapter trace on disk — 61 episodes: 40 sweep episodes across all scenarios in
`tmp/` and the 21 authority-experiment episodes; `timing_per_episode.csv` holds only the green_stop subset — this happened after the first
tick only in 3 authority-experiment episodes: **police STOP s1, 229 of 500 ticks**, no-police s1 (3) and s2 (1).
The re-run of police STOP s1 with the same seed had **0** such ticks and the same verdict (FAIL, front bumper
crossed the line at 6.95 s vs 6.35 s originally). This is consistent with transient host load rather than a property of
the model (not isolated); the scorer does not see it (see §7).

## 4. Decision cadence

| Model | New decision | Observed rate (sim) | Hold between decisions | Evidence |
|---|---|---|---|---|
| oracle | every tick | 20 Hz | — | [CODE] |
| baseline | TM autopilot every tick | 20 Hz | — | [CODE] |
| TCP, InterFuser, NEAT, AIM, CILRS | every tick (inference) | 19.6–19.96 Hz (first tick has no input) | — | [LOG] trace `mode=infer` |
| TransFuser | every 2nd tick (`action_repeat=2`) | 9.96 Hz | previous control repeated | [LOG] 140 infer / 140 hold |
| PID / MPC | every tick | 20 Hz | — | [LOG] |
| OpenEMMA | planner every 3.0 s sim (0.10, 3.15, 6.20, 9.20, 12.20 s) | 0.36 Hz | trajectory followed between plans | [LOG]; wall latency ≈ 20.4 s per plan |
| Qwen3-VL / Qwen2.5-VL / GLM-4.5V, leaderboard | query at 0.05, 1.55, 3.10 s, then **no more queries** (cap 3) | 3 decisions per episode | last answer held for the rest of the episode | [CODE] `_run_vlm_test.py:364, 375`; [LOG] |
| same, authority experiment | query every 1.5 s, uncapped (17 per 25 s episode; intervals 1.50/1.55 s from tick quantisation) | 0.68 Hz | last answer held | [LOG] |

VLM accounting (authority experiment, 51 VLM episodes; the July leaderboard VLM runs have no per-tick trace,
so requests are counted there as decision + error events): 867 queries requested, 866 parsed into an action, 1
error. Leaderboard GLM-4.5V green_stop: query 3 at 3.10 s **timed out after 90 s** (`vlm_error`), the cap was
spent and the earlier GO stayed active for the remaining 11 s. Wall latency per query (median of per-episode
medians): Qwen3-VL 2.5 s (1.75–3.66), Qwen2.5-VL 1.6 s, GLM-4.5V 6.8 s (5.9–9.0); leaderboard single values
1.46–10.4 s.

## 5. From decision to vehicle

| Question | Answer | Evidence |
|---|---|---|
| Does the simulation pause during inference / API calls? | **Yes.** `controller.step` runs between ticks; the next `world.tick()` is not sent until it returns. A 10.6 s Qwen3-VL query is one 10.6 s wall gap with zero sim time | [CODE] `_common.py:2232-2312`; [LOG] `ctrl_step_wall_max_s` 10.55 s on the first query tick |
| When does a new VLM decision take effect? | on its own submit tick: the decision is parsed inside `step` and converted to control before `step` returns (all 51 authority VLM episodes + the instrumented run) | [LOG] `decision_active_on_submit_tick = True` |
| What drives the car between decisions? | VLM: a fixed mapping of the held action to control every tick (`_apply_action`, `vlm_model.py:620-635`: STOP/HOLD → brake 1.0 (0.85 when < 0.25 m/s); SLOW → brake 0.3 or throttle 0.2; GO → base lane-follow with throttle ≥ 0.55 below 25 km/h). E2E: model output (or repeated output on hold ticks) | [CODE] |
| Is the output modified? | VLM: the mapping above is the adapter. TCP: post-processing caps throttle (0.05 above ~1.5 m/s; `pp_max_throttle` in the trace). No global safety override was found in the runner; the runner only rejects non-finite controls | [CODE] `tcp_model.py` `_make_control`; `_common.py:2310-2318` |
| When is a control physically applied? | `apply_control` after tick N is used in the physics step N → N+1; the readback `get_control()` after tick N+1 equals the controller output of tick N in 97 of the 98 episodes that log both columns (best-match lag 1 tick; one Qwen2.5-VL civilian-PROCEED episode matches best at 2). The July leaderboard runs do not log `raw_*` | [LOG] `applied_readback_lag_ticks` |
| Can an old answer overwrite a newer one? | No: queries are synchronous and sequential; a decision replaces the held one only on success | [CODE] `vlm_model.py:440-485` |
| After an error / timeout / cap? | the previous decision stays active (initial default GO) | [CODE]; [LOG] GLM timeout above |

## 6. Latencies (simulation time)

For VLMs the input-to-application delay in sim time is the input-frame age (0.05–0.30 s) plus one tick until
the control is physically applied; the API wall latency adds nothing in sim time. The gap that matters is the
**query period**: a gesture first shown at 1.0 s is first seen by the 1.55 s query (0.55 s), and with the
leaderboard cap the 3.10 s query is the last chance to change the action. Example (instrumented Qwen3-VL run,
`figures/timeline_new_leaderboard_wiring_qwen3vl_police_stop_sdefault.png`): GO at 0.05 s (input 1 tick old,
10.6 s wall), GO at 1.55 s (input 6 ticks old, 4.4 s wall, reason "no authority figure present"), GO at 3.10 s
→ the front bumper crossed the line at 2.25 s → FAIL. The original July leaderboard run of the same model
answered STOP at 1.55 s and HOLD at 3.10 s and PASSED; run-to-run variation of the hosted model decides this
cell. (A first recording of this audit, kept unused because it was made before the final instrumentation fix,
answered HOLD at 1.55 s, stopped, then GO at 3.10 s and also failed.)

For per-tick E2E models, input-to-application is one tick (current frame, applied in the next step).

Reaction measured on vehicle state (from `data/green_stop_score_trace.csv`): first brake ≥ 0.1 after onset and
first stop are listed per episode; the onset at 1.0 s is the configured time of the scripted gesture, not an
observed render time.

## 7. What these runs do and do not show about real time

- Driving behaviour is measured in a **lock-step** world: inference cost is hidden. A model that needs 5–20 s
  per decision (GLM-4.5V, OpenEMMA) is evaluated as if it answered instantly at its query tick. Leaderboard
  results therefore say nothing about real-time feasibility; they bound decision quality at a given sim-time
  cadence.
- Cadence differs by model by design of the published wiring (20 Hz, 10 Hz, 0.68 Hz, 3 queries, 3 s planner).
  Results under the existing wiring and any controlled-cadence comparison must be reported separately; the
  common-cadence control from the ablation study (`docs/oracle_ablation.md`) is the existing example.
- Stale-input fallback ticks and dash-cam input age are not visible to the scorers. [PROPOSAL] record the
  fallback count in `result.json` and flag an episode for re-run above a fixed fraction (e.g. any fallback after
  the first tick), without changing any score.

## 8. Video frame rate

Episode videos are encoded at 20 fps (`scripts/_make_episode_videos.py:15`,
`analysis/scenario_videos/make_all_videos.py:22`) from one saved frame per tick, so one video second = one
simulation second (real time was 4–14× longer). Frame-to-tick mapping in existing videos was inferred
(`mapping_rule: anchor_last_tick_to_max_external` in the sidecar JSON); with the new `world_frame` /
`obs_image_frame` columns it can be exact. The video rate is not evidence of sensor or decision rates.

## 9. Not measured

- Physics substep values at runtime (defaults assumed from code; not read back).
- Sensor capture time vs callback receipt time: only CARLA's sensor timestamp (sim) and receipt (wall) are logged.
- Internal model latency per tick for E2E models: `ctrl_step_wall_s` exists only for the 5 new runs
  (TCP median 0.19 s, InterFuser 0.27 s per step).
- Any run with a different GPU or host load; the stale-input fallback rate is host dependent.
