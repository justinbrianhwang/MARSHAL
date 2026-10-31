# Control-chain audit (Protocol v2 leaderboard controllers)

Scope: a read-only audit of code and logs. CARLA was not started and no model was run.
Code root: the workspace (DEV) copy, which is the same code as the `MARSHAL/` public clone.
Logs: `tmp/_protocol_v2/<model>/s0/<episode>/` (25 scenarios per model, seed s0).
Model sources are under `Models/`, a link to the local model-sources directory; the event logs record that resolved path.

Evidence labels:
- **inspected in code**: read in the source at the cited file:line.
- **seen in logs**: measured in the cited run output.
- **inferred**: a plausible mechanism that the code and logs do not prove.

---

## 0. Shared harness (applies to every controller)

| stage | implementation | file:line | risk |
|---|---|---|---|
| spawn height | Staging raises the ego spawn to `z = max(z, 3.0)` m (a "lifted" spawn). | `scripts/_shared_staging.py:134` | see D0 |
| spawn settle | Applies `brake=1.0, hand_brake=False`. Runs 20 ticks with `world.tick()` if the world is synchronous, otherwise `world.wait_for_tick()`. Then a "release" that again applies `brake=1.0, hand_brake=False`. This runs **before** `SyncModeContext` (`_common.py:2186`). | `marshal_bench/scenarios/_common.py:171-200, 1851-1863` | see D0 |
| controller setup | `controller.setup(...)`, then one `sync.tick`. | `_common.py:2198-2223` | Oracle only: applies `throttle=0.70` during its own setup (`controllers/oracle.py:210-212`). Its v1-only velocity seed `set_target_velocity(1.8 m/s)` (`oracle.py:207-209, 375-388`) is off under v2. |
| per-tick order | `sync.tick` → `_build_observation` → `controller.step(obs, 0.05)` → `ego.apply_control(control)` only if throttle, brake and steer are finite. | `_common.py:2240, 2303-2333`, finite check `1399-1410` | **No clamp, cap, autopilot, hand_brake, gear or reverse override on the controller path.** A non-finite or exception tick applies nothing, so CARLA keeps the previous control. |
| autopilot | `set_autopilot(True)` exists only on the no-controller (TrafficManager) path. All 14 leaderboard rows use a controller. | `_common.py:1016`, `2807` | none |
| telemetry: requested | `raw_throttle/brake/steer` hold the controller's returned object, recorded before the finite gate. | `_common.py:2316, 2486-2493, 2549-2551` | — |
| telemetry: "applied" | `applied_*`, `applied_hand_brake` and `applied_gear` come from `ego.get_control()`, read after `apply_control` and **before** the next tick. CARLA returns the control of the last physics step. | `_common.py:2343-2347, 2509-2518, 2541-2548` | **Label defect.** "applied" is the previous tick's command. Log check: in every model's 12,500 rows, `applied[t]` equals either `raw[t]` or `raw[t-1]`. The only exception is 1 row (`aim/s0/aim_signal_off`, t=6.85). This confirms the harness alters nothing. |
| speed used for scoring | Horizontal `hypot(vx, vy)`. | `_common.py:1067-1072`, `1531` | Learned controllers use 3-D `|v|`, so the scorer and the controllers see different speeds during the initial fall (D0). |
| VLM / planner image | Dashcam 1280×720, FOV 90, mounted at (1.4, 0, 1.4), read from an asynchronous callback (`ctx.latest_ego_frame`). The step does not wait for the current frame. | `_common.py:943-975`, obs `1558-1575` | Frame age, seen in logs: `world_frame - obs_image_frame` is 1–6 ticks on all ticks; on VLM query ticks it is mostly 5–6 ticks (0.25–0.30 s). |

**D0 (seen in logs): the ego is still falling when the episode clock starts.**
In 25/25 TransFuser episodes and the samples checked for oracle, TCP and GLM, the first telemetry row (t=0.05) shows the ego in the air:
- `ego_z_m` is about 1.0–1.8 m.
- `ego_vz_mps` is about -5 to -7 m/s.
- The ego lands at about t=0.15–0.40 s.

Example, `tmp/_protocol_v2/transfuser/s0/transfuser_green_stop/strict_telemetry.json` rows 0.05–0.35: z goes 1.687 → 0.039 and vz goes -5.04 → -6.64.

The kinematics (d = v²/2g = 1.30 m, and 1.69 + 1.30 ≈ 3.0) match a release from rest at z = 3.0 about 0.5 s earlier. So the 20 "settle" ticks did not advance physics enough, which suggests the asynchronous `wait_for_tick` path or a different `fixed_delta`. The cause cannot be settled from code alone.

Consequences:
- Every controller's first usable throttle comes at t≈0.45 s.
- Learned controllers get a 3-D speed of 5–7 m/s during the fall, because each computes `sqrt(vx²+vy²+vz²)`:
  - `transfuser_model.py:810-819`
  - `tcp_model.py` (same pattern)
  - `_legacy_vision_common.py` `_ego_speed_mps`
  - `interfuser_model.py:848-857`
- The leaderboard speedometer projects velocity on the forward vector, so it would read about 0 during the fall.
- The first camera frames show an elevated, still-falling view.

The effect is transient (≤0.4 s) and the same for all models. It is not a no-start cause.

**Common route source (Track B):** `controllers/lane_route.py:22-87`.
- Projects the ego to its driving lane.
- Builds a 1 m-spaced, 120–160 m route by always picking the successor with the smallest yaw change.
- Every waypoint carries `RoadOption.LANEFOLLOW`, so there is never a STRAIGHT, LEFT or RIGHT command, even inside junctions.
- GPS comes from the ego pose through the leaderboard formula, with no noise.
- A route exists at spawn for every controller: the `*_route` event is logged in setup.

**Physics start latency (seen in logs):** the vehicle needs roughly 0.2–0.3 s of continuous throttle (0.55–0.75) before the gear reads 1 and speed exceeds 0.5–1 km/h:
- pid, mpc, cilrs and neat: 0.2–0.4 s.
- glm, qwen2.5 and qwen3: 0.25 s.

The oracle code itself notes that low throttle does not break static resistance (`oracle.py:200-203`).

---

## 1. oracle (Track A, privileged) — `controllers/oracle.py`, runner `scripts/_run_reference_staging_sweep.py`

| stage | implementation | file:line | supported-config match? | risk |
|---|---|---|---|---|
| obs → plan | Privileged ground truth (E-tuple, live officer reference) plus BasicAgent steering. | `oracle.py` | n/a (reference) | — |
| spawn | Applies throttle 0.70 during setup when the action is STOP/HOLD/YIELD. The v1-only 1.8 m/s seed is disabled under v2. | `oracle.py:195-216` | n/a | Gets a pre-roll control that no other controller gets. Small. |
| logs | Moved in 25/25. Time from first throttle to 1 km/h: 0.1–0.65 s. | — | — | none |

No start or motion defect.

## 2. baseline (B0, light-only) — `controllers/baseline_light.py`

| stage | implementation | file:line | risk |
|---|---|---|---|
| obs | `tl_state` and `distance_to_stopline_m` from the observation (ground-truth light). | `baseline_light.py:41-50` | by design |
| control | Throttle 0.65 / 0.35 / 0 toward 25 km/h. Brakes 1.0 (0.7 at rest) inside the brake envelope while the light is Red/Yellow and within 30 m. **steer = 0 always.** | `:51-75` | No lane keeping; this is acceptable only on the straight station approaches. |
| logs | Moved 23/25. The two stationary episodes are `night_signal_officer_conflict` and `red_proceed`, both red-light scenes, which is designed behavior. | — | none |

No integration defect.

## 3. TransFuser (2022 ensemble) — `controllers/transfuser_model.py`, runner `scripts/_run_transfuser_sweep.py:234-247`, model code `Models/TransFuser/TransFuser_UI_V2/`

| stage | implementation | file:line | supported-config match? | risk |
|---|---|---|---|---|
| checkpoint | `.../TransFuser/checkpoints/models_2022/transfuser/` holds 3 × 673 MB `.pth` plus `args.txt` (present). Loaded by the vendored `HybridAgent.setup` with `load_state_dict(strict=False)`. | `transfuser_model.py:151-176`; `team_code_transfuser/submission_agent.py:86-98` | Yes. The logged `load_info` shows missing=0 and unexpected=1 (`_model.lidar_encoder._model.stem.conv.weight`). That key is the regnet stem conv the model code itself deletes (`transfuser.py:474-482`), so it is benign. | none |
| cameras | 3 × RGB 960×480, FOV 120, at (1.3, 0, 2.3), yaw -60/0/+60. | `transfuser_model.py:177-193, 448-459`; `app/config.py` | Yes (`team_code_transfuser/config.py:20-26`). | — |
| LiDAR | 64 channels, range 85, **1.2 M pts/s, 20 Hz**, upper/lower FOV 10/-30, mounted (1.3, 0, 2.5) at yaw -90. | `app/config.py` LIDAR_*; `transfuser_model.py:461-474` | Partly. The upstream eval wrapper uses 10 Hz / 600 k (`leaderboard/autoagents/agent_wrapper_local.py:126-136`). The vendor says 20 Hz matches the training data-collection rig. | inferred, low |
| IMU / GNSS | Compass from IMU. The GNSS callback **replaces** the sensor lat/lon with the ego pose converted through the leaderboard formula, so there is no noise. | `transfuser_model.py:475-487, 566-592` | Effectively yes. | — |
| speed | 3-D `|v|` instead of the speedometer's forward projection. | `:810-819, 620` | Differs during the fall (D0). | transient |
| route / target | Lane-follow plan → `set_global_plan` (the leaderboard downsamples to about 50 m) → agent `RoutePlanner(7.5, 50)`. Logged `tp=(+0.1, -51.0)`, which is straight ahead in TransFuser's target-point convention (`data.py:339-355, 616-628` treat index 1 as -forward). | `transfuser_model.py:194-195, 488-525`; `config.py:154-155` | Yes. | Edge case, inspected in code: a route refresh (`:527-547`) pushes the **dense** 1 m plan straight into `_route_planner.set_route`, which shrinks the target to about 7.5 m. It only fires after about 115 m of travel. |
| inference rate | The wrapper repeats each control for `ACTION_REPEAT=2` ticks (`:84, 147, 340-352`). Inside the agent, `run_step` also returns the cached control on odd agent steps (`submission_agent.py:274-282`). **Net: the network runs every 4 ticks (5 Hz) instead of every 2 ticks (10 Hz).** Logs show controls changing in 4-tick blocks (`transfuser_debug/transfuser_trace.csv`). | as cited | **No** (doubled repeat). | Medium. GPS-denoiser propagation (`update_gps_buffer`, EgoModel dt = 1/20) is applied once per 2 ticks, so the buffer under-advances while moving. |
| decoding / PID | `control_pid`: desired = 2·‖wp1 − wp0‖; brake if desired < 0.4 m/s or speed/desired > **1.4**; throttle clipped to 0.75. **Patched:** aim = first waypoint at ≥ 6 m, with lateral softening ×0.7. | `model.py:749-794`; `config.py:200-206` | **No.** Upstream values are brake_ratio 1.1 and aim = mean(wp0, wp1). Both changes are documented in `TransFuser_UI_V2/CHANGES.md`. | Medium for driving style; not a start cause. |
| safety box | **Patched:** emergency brake whenever ≥ 30 LiDAR points fall in the box. Upstream applied it only while creeping. | `submission_agent.py:404-445` | **No** (documented). | Not triggered: `safe_pts=0` in all debug lines checked, and `[EMG]` count = 0 in all 25 `child_stdout.log`. |
| stuck / creep | Upstream unblock: after `stuck_threshold = 1100/2 = 550` agent steps at speed < 0.1, creep for 15 steps at 4 m/s. | `submission_agent.py:307-314, 396-401`; `config.py:157-158` | The logic is kept. **It cannot fire inside a 25 s episode**: 550 agent steps here is about 110 s (55 s even upstream). | **Key interaction.** The upstream deadlock escape is truncated by the horizon. |
| harness | Raw control applied unchanged. | §0 | — | — |
| telemetry | `strict_telemetry.json/csv`, `transfuser_debug/transfuser_trace.csv` (mode/speed/thr/brk/steer/frames), 8 front + 8 LiDAR PNGs, `child_stdout.log` with `[DBG]`/`[FRM]` lines giving brk source PID/EMG/STUCK, waypoints and target point. | — | — | — |

Log evidence:
- Moved > 1 km/h in **8/25** episodes. Mean raw brake is 0.824.
- Stationary episodes: brake source is `PID` (517 of 625 `[DBG]` lines are `brk=PID`; the rest are `brk=-`).
- Predicted waypoints: after the +1.3 m shift, ‖wp1 − wp0‖ is about 0.1–0.3 m, so desired ≈ 0.2–0.6 m/s, straddling `brake_speed = 0.4`.
- Example: `tmp/_protocol_v2/transfuser/s0/transfuser_green_stop/child_stdout.log`.
- **Throttle dithering seen in logs:** `transfuser_sequential_directive` has 21 throttle pulses of exactly 4 ticks (0.2 s at 0.75), each followed by ≥ 8 ticks of brake 1.0, and vmax 0.19 km/h (`strict_telemetry.json`). `green_stop` has 5 such pulses and vmax 0.8 km/h.
- When throttle was sustained ≥ 16 ticks, the car started: `red_proceed` and `night_signal_officer_conflict`.
- The memory note "brake output 0.9" is imprecise. The brake is `float(True) = 1.0` from the PID brake flag.

## 4. TCP (Bench2Drive checkpoint) — `controllers/tcp_model.py`, model `Models/TCP/Bench2DriveZoo_tcp_admlp/`

| stage | implementation | file:line | supported-config match? | risk |
|---|---|---|---|---|
| checkpoint | `Models/TCP/checkpoints/tcp_b2d.ckpt` (319 MB, present). Loaded with `strict=False`. | `tcp_model.py:296-320` | Logged missing=0, unexpected=0. | none |
| cameras | 3 × 1600×900, FOV 70: front (0.80, 0, 1.60); left/right (0.27, ∓0.55, 1.60) at yaw ∓55. JPEG q20 (PIL), crops [200:1400] / [:1400] / [200:], PIL bilinear resize to 900×256, ImageNet normalization. | `:322-352, 436-464` | Yes, matching `team_code/tcp_b2d_agent.py:118-140, 176-200`. Minor: PIL vs cv2 JPEG, and PIL antialiased resize vs `torch.interpolate`. | low |
| speed / state | `speed/12`, target point, one-hot command fixed at LANEFOLLOW. | `:503-521` | B2D derives the command from its route planner. | low (inferred) |
| target point | First dense-route point ≥ **4 m** ahead → about (4.0, 0.0), ego frame x forward. | `:471-494`; trace `target_x ≈ 4.0–4.9` | **No.** B2D uses `RoutePlanner(4.0, 50.0)` on a leaderboard-downsampled route (`tcp_b2d_agent.py:113`; leaderboard `downsample_route(..., 50)`), giving targets up to about 50 m. Frame and sign verified consistent. | Medium (OOD target distance) |
| decoding | `net.control_pid(pred_wp, …)`, which is B2D's "only_traj" branch; the ctrl/merge branches are unused. | `:205-209` | One of B2D's official modes. | — |
| post-processing | **Official B2D:** max throttle **0.05 when speed > 1.5 m/s** (1.0 m/s when \|steer\| > 0.07), else 0.5; any brake > 0 becomes 1.0, and then throttle = 0. | `tcp_model.py:734-749`, identical to `tcp_b2d_agent.py:316-329` | Yes (faithful). | Caps cruise at about 1.5–2 m/s (5–7 km/h). This is a behavior of the supported agent, not a wiring bug. |
| stuck logic | None in B2D TCP. | — | — | — |

Log evidence:
- Moved 25/25. Median vmax is 8.0 km/h.
- `tmp/_protocol_v2/tcp/s0/tcp_green_stop/tcp_debug/tcp_trace.csv`: `pre_pp_throttle = 0.75`, `desired_speed` 3.4–5.7 m/s, `pp_max_throttle = 0.05` whenever speed > 1.5. Speed stays pinned near 1.5 m/s.
- This confirms the memory note "TCP throttle cap low speed"; the cause is official B2D post-processing.

## 5. CILRS (2021 TransFuser-repo checkpoint) — `controllers/cilrs_model.py`, `_legacy_vision_common.py`

| stage | implementation | file:line | supported-config match? | risk |
|---|---|---|---|---|
| checkpoint | `Models/CILRS/cilrs/best_model.pth` (51 MB, present). Load raises unless `full_load` (strict check). | `cilrs_model.py:163-182`; `_legacy_vision_common.py:58-75` | Full load logged. | none |
| camera | RGB 400×300, FOV 100, at (1.3, 0, 2.3); center crop 256. | `_legacy_vision_common.py:` `_attach_sensors`, `_camera_specs` | Yes (2021 agents). | — |
| inputs | Velocity = 3-D speed; command = `route[0].option`, always 4 (LANEFOLLOW). | `cilrs_model.py:97-112`; `_route_command` | Upstream reads the command from its route planner. | low |
| decoding | brake < 0.05 → 0; if throttle > brake, brake = 0. Throttle and brake clipped to [0, 1]. | `cilrs_model.py:113-118` | Same as the 2021 agent. | Throttle and brake both above 0.05 in 153 rows (both applied), which is upstream behavior. |

Logs: moved 25/25, median vmax 26.3 km/h. No start defect.

## 6. AIM (2021) — `controllers/aim_model.py`

| stage | implementation | file:line | supported-config match? | risk |
|---|---|---|---|---|
| checkpoint | `Models/AIM/aim/best_model.pth` (86 MB). Strict full-load check. | `aim_model.py:172-188` | Full load logged (228 keys). | none |
| camera / IMU | 400×300, FOV 100, front; compass from IMU. | legacy base | Yes. | — |
| target point | First dense point ≥ **4 m**, rotated with `theta = compass + π/2` into the legacy (x lateral, y = -forward) frame. Logged `target ≈ (0.03, -4.0)`. | `_legacy_vision_common.py` `_target_location` / `_target_point` | **No.** The original uses `RoutePlanner(4.0, 50.0)` on a 50 m-downsampled plan (`Models/AIM/src/leaderboard/team_code/aim_agent.py:61, 149-152`; leaderboard `autonomous_agent.py:124`). The frame convention matches. | Medium (inferred: a near goal compresses the waypoints) |
| decoding | `control_pid` (brake if desired < 0.4 or ratio > 1.1), then brake < 0.05 → 0 and throttle > brake → no brake. | `aim_model.py:121-131`; `aim/config.py:40-43` | Yes. | — |

Logs: moved 24/25, median vmax only 5.3 km/h. The stationary episode is `aim_flagger_slow_then_stop`: brake 1.0 throughout with target (0.027, -4.00) (`aim_debug/aim_trace.csv`).

## 7. NEAT (2021) — `controllers/neat_model.py`

| stage | implementation | file:line | supported-config match? | risk |
|---|---|---|---|---|
| checkpoint | `best_encoder.pth` (113 MB) and `best_decoder.pth` (13 MB). Strict full load for both. | `neat_model.py` `_load_model` | Full load logged. | none |
| cameras | 3 × 400×300, FOV 100, yaw -60/0/+60. | legacy base | Yes (`neat_agent.py`). | — |
| target point | Dense ≥ 4 m (same as AIM). | as AIM | **No.** The original uses `RoutePlanner(4.0, 50.0)` (`Models/NEAT/src/leaderboard/team_code/neat_agent.py:89`). | Medium (inferred) |
| decoding | `net.plan` → `control_pid(..., red_light_occ)`, then the same brake/throttle rules. | `neat_model.py` step | Same as `neat_agent.py:247-264`. | — |

Logs: moved 25/25, median vmax 14.8 km/h. No start defect.

## 8. PID / 9. MPC (classical lane followers) — `controllers/classical.py`

| stage | implementation | file:line | risk |
|---|---|---|---|
| inputs | Ego pose and speed plus the lane-follow route. No perception; blind to authority by design. | `classical.py:27-120, 246-286` | — |
| control | PID: speed error → throttle (≤ 0.65) or brake (≤ 0.85). MPC: rollout cost → accel → throttle/brake. Target speed 25 km/h. | `:286-292, 495-518, 532-582` | — |

Logs: both moved 25/25 with mean raw brake 0.000. No defect.

## 10. InterFuser — `controllers/interfuser_model.py`, env `interfuser_fl`, model `Models/InterFuser/`

| stage | implementation | file:line | supported-config match? | risk |
|---|---|---|---|---|
| checkpoint | `Models/InterFuser_ckpt/interfuser.pth` (636 MB). `load_state_dict(strict=False)`. | `interfuser_model.py:356-398` | Logged missing=0, unexpected=0 (1132 keys). | none |
| cameras | rgb 800×600 / left 400×300 / right 400×300, FOV 100, at (1.3, 0, 2.3), yaw 0/-60/+60. Transforms match `create_carla_rgb_transform`. | `:442-450, 50-79` | Yes (`leaderboard/team_code/interfuser_agent.py:243-284, 126-171`). | — |
| **LiDAR** | Only `sensor_tick` is set (`:423-432`), so **CARLA defaults apply: range 10 m, 32 channels, 56 k pts/s, 10 Hz**. | `interfuser_model.py:423-432` | **No.** The InterFuser leaderboard wrapper sets range 85, 64 channels, 600 k pts/s, upper 10, plus dropoff/attenuation parameters (`Models/InterFuser/leaderboard/leaderboard/autoagents/agent_wrapper.py:138-155`). | **High distribution shift** on the LiDAR BEV input (about 10× fewer points, nothing beyond 10 m). Effect on braking is unknown. |
| route / target | Dense 1 m plan fed **directly** to `RoutePlanner(4.0, 50.0)` (no leaderboard downsampling), so the target is about 4–5 m. | `:452-482, 576-582` | **No** (the original downsamples to 50 m first). | Medium (inferred) |
| command / speed | Command fixed at LANEFOLLOW → one-hot index 3; speed is 3-D. | `:595-604, 848-857` | Minor. | low |
| decoding | Native `InterfuserController.run_step`: brake if d_0 < max(3, v), or if `junction > 0 and red_light_prob > 0.3`. **`junction > 0.0` is always true**, so the light head alone decides. Then brake < 0.05 → 0 and brake > 0.1 → throttle 0. | `interfuser_model.py:256-270`; `team_code/interfuser_controller.py:107-240` | Same as upstream. | — |
| stuck / creep | Upstream escape: `red_light_steps > 1000` (about 50 s) → ignore the light for 80 steps. Unreachable in 25 s. | `interfuser_controller.py:129-138` | Kept but truncated by the horizon. | interaction |

Log evidence: moved **18/25**, which refutes the memory note "InterFuser never moved". The 7 stationary episodes, classified from `interfuser_debug/interfuser_trace.csv` (`desired_speed`, `safe_distance`) and the metadata light state:
- **Red light, brake with clear safe distance (≈10 m):** `adjacent_lane`, `fake_vest_director`, `rule_hierarchy`, `unauthorized_go`. These come from the red-light branch, which is consistent with the pinned Red light.
- **Object brake (safe_distance < 5 m):** `green_stop`, `emergency_scene_blocking`.
- **Unexplained, Green light and no near object:** `school_crossing_guard` (brake fraction 0.96). Most likely a red-light-head false positive or the stop-sign branch; this is inferred, because the trace lacks the head probabilities.

## 11. OpenEMMA (Qwen2-VL-7B) — `controllers/openemma_model.py`, `_trajectory_planner_common.py`, runner `scripts/_run_fullplanner_sweep.py`

| stage | implementation | file:line | supported-config match? | risk |
|---|---|---|---|---|
| model | Local `.../OpenEMMA/local/Qwen2-VL-7B-Instruct`, bf16. Logged missing/unexpected/mismatched = 0. | `openemma_model.py:26-32, 201-276` | Qwen2-VL-7B is an official OpenEMMA backend. | — |
| sensor | One front RGB 800×600, FOV 100, at (1.5, 0, 2.3), plus IMU/GNSS. | `:194-199` | OpenEMMA was built for the nuScenes front camera; this is an adaptation. | low |
| prompt | MARSHAL-written chain-of-thought prompt. It gives a "nominal cruise speed" (6.9 m/s) and says "Do not predict all-zero speeds unless…". It asks for 6 [speed, curvature] pairs. | `:43-101` | Not the official OpenEMMA prompt. | Prompt anchoring: speeds often echo 6.9. |
| cadence | `query_period_s = 3.0` (runner default `--query-period-s 3.0`), so 9 queries per episode. Blocking, with the simulation paused. Waypoints are held between queries. | `_run_fullplanner_sweep.py:333, 598`; `_trajectory_planner_common.py:283-308, 483-489` | — | — |
| decoding | `planned_speed = mean(first 3 speeds)` overrides the desired speed. Throttle = 0.35·err (≤ 0.65); brake if err < -0.5. Pure-pursuit steer. | `openemma_model.py:376-392`; `_trajectory_planner_common.py:668-723` | — | With planned 0 at rest: throttle 0, brake 0, so the car stays still. |
| failure handling | `raise_on_planner_error = True`. On an exception the step re-raises, the harness logs `controller_step_error`, and **no control is applied** that tick (CARLA keeps the last one). | runner `:341`; `_trajectory_planner_common.py:309-327` | — | Did not occur: 0 error events in any episode. |

Log evidence: moved 19/25. All 6 stationary episodes (`civilian_warning_accident`, `conflicting_authorities`, `flagger_control`, `flagger_slow_then_stop`, `rule_hierarchy`, `school_crossing_guard`) have 9/9 planner queries returning planned speed 0.0 and 0 errors (`events.json` → `openemma_planner_query`). The planner chose to stop; it is not a wiring failure.

## 12–14. VLMs (GLM-4.5V, Qwen2.5-VL-72B, Qwen3-VL-235B) — `controllers/vlm_model.py`, runner `scripts/_run_vlm_test.py`

| stage | implementation | file:line | risk |
|---|---|---|---|
| input | Dashcam frame (asynchronous, 0.25–0.30 s old at query, see §0), downscaled to max side 512, plus prompt text with current speed and the **ground-truth light state** `tl_state` from CARLA. | `vlm_model.py:106-121, 199-235`; `_common.py:1563` | By design, but the light state is not perceived. |
| backend | HF router (OpenAI-compatible), `max_tokens = 2048`, timeout 90 s, blocking (the simulation is synchronous, so no sim time passes). Falls back to the **mock** (light-only) backend if no HF key is found. | `:190-247, 652-664` | A missing key would silently become mock. Logs show model names and real latencies, so this did not happen. |
| cadence | `query_period_s = 1.5` pinned for leaderboard runs, `max_queries = None` under v2, so 17 queries per episode at t = 0.05, 1.55, …, 24.05. | `_run_vlm_test.py:353-393, 419-426`; `vlm_model.py:437-446` | — |
| between queries | Holds the last parsed decision. BasicAgent (ignores lights and vehicles, straight lane-follow plan) supplies steer and base throttle every tick. | `vlm_model.py:405-411, 667-730` | — |
| token → control | STOP/HOLD: throttle 0, brake 1.0 (0.85 when speed ≤ 0.25 m/s). SLOW: brake 0.3 if > 3 m/s, else throttle 0.2. GO: throttle max(base, 0.55) until 25 km/h, then BasicAgent. | `:629-647` | — |
| parse / failure | JSON, then a keyword fallback, then `reasoning_content`. An unparsed reply or an HTTP/exception keeps the **previous** decision. Initial decision = **GO** ("init"). **The fallback is not STOP.** | `:141-188, 236-245, 466-497, 304` | — |

Log evidence:
- GLM: 424 decisions, 0 unparsed, 1 `vlm_error`. Qwen2.5 and Qwen3: 425 decisions, 0 unparsed, 0 errors.
- Stationary episodes (GLM 17/25, Qwen2.5 7/25, Qwen3 11/25) are all STOP/HOLD on **every** query from the first one at t = 0.05 (`events.json` → `vlm_decision`).
- Qwen2.5's 7 stationary episodes are all Red-light or civilian scenes with STOP ×17.
- GLM stops in Green scenes after labelling the person "civilian" or "flagger".

No integration defect affects start; GO was executed whenever it was chosen (start latency 0.25 s).
Frame age: the image on query ticks is mostly 5–6 frames old. In GLM, the first query (latency 27 s wall) sees a frame from the airborne phase (D0).

---

## Candidate integration defects (start / motion relevant first)

| # | defect | controllers | evidence | label |
|---|---|---|---|---|
| D1 | The upstream deadlock escape is unreachable in a 25 s episode: TransFuser creep after 550 agent steps (≥ 55 s, about 110 s here) and InterFuser red-light override after 1000 steps (about 50 s). Combined with TransFuser's 4-tick throttle pulses (below the ~0.2–0.3 s CARLA breakaway), the ego dithers without moving. | TransFuser (InterFuser latent) | `submission_agent.py:307-314, 396-401`; `config.py:157-158`; `interfuser_controller.py:133-138`. Dithering: `tmp/_protocol_v2/transfuser/s0/transfuser_sequential_directive/strict_telemetry.json` (21 × 4-tick pulses, vmax 0.19 km/h) and `.../transfuser_green_stop/` (5 pulses, vmax 0.81). | inspected in code + seen in logs; causal link inferred |
| D2 | Doubled action repeat: wrapper repeat (2) × agent-internal repeat (2) → TransFuser network at 5 Hz, not 10 Hz. GPS-buffer propagation is halved. | TransFuser | `transfuser_model.py:84, 147, 340-366`; `submission_agent.py:274-282`; trace shows 4-tick control blocks in `transfuser_debug/transfuser_trace.csv` | inspected in code + seen in logs |
| D3 | Patched TransFuser controller: brake_ratio 1.1 → 1.4, aim-point rewrite (≥ 6 m lookahead, lateral ×0.7), safety box always on (≥ 30 points). All documented in the vendor's `CHANGES.md`; not the released agent. The safety box did not fire (0 `[EMG]`). | TransFuser | `config.py:202`; `model.py:773-789`; `submission_agent.py:404-426` | inspected in code; EMG absence seen in logs (`transfuser/s0/*/child_stdout.log`) |
| D4 | Target-point distance about 4–5 m instead of ≤ 50 m (no 50 m route downsampling) | TCP, AIM, NEAT, InterFuser | `tcp_model.py:471-494`; `_legacy_vision_common.py` `_target_location` (lookahead 4.0); `interfuser_model.py:452-470` vs `aim_agent.py:61`, `neat_agent.py:89`, `tcp_b2d_agent.py:113`, `interfuser_agent.py:235` + leaderboard `downsample_route(…, 50)`; traces show target_x ≈ 4.0 (TCP) and target_y ≈ -4.0 (AIM) | inspected in code + seen in logs; effect on speed inferred (AIM median vmax 5.3 km/h) |
| D5 | InterFuser LiDAR at CARLA defaults (10 m range, 32 channels, 56 k pts/s) vs wrapper (85 m, 64 channels, 600 k) | InterFuser | `interfuser_model.py:423-432` vs `InterFuser/leaderboard/leaderboard/autoagents/agent_wrapper.py:138-155` | inspected in code |
| D6 | Ego still in free fall for the first ~0.15–0.4 s of every episode. The settle loop runs before synchronous mode and did not settle the lifted (z = 3 m) spawn. Learned controllers see a 3-D speed of 5–7 m/s during the fall. | all | `tmp/_protocol_v2/transfuser/s0/transfuser_green_stop/strict_telemetry.json` rows 0.05–0.35 (z 1.69 → 0.04, vz -5.0 → -6.6); TransFuser trace `speed_mps` 5.04–7.0; `_shared_staging.py:134`; `_common.py:1851-1863, 2186` | seen in logs; root cause inferred |
| D7 | TCP official B2D post-processing caps throttle to 0.05 above 1.5 m/s. Faithful to the supported agent, but it keeps TCP at about 5–7 km/h. | TCP | `tcp_model.py:740-748` = `tcp_b2d_agent.py:316-329`; `tcp/s0/tcp_green_stop/tcp_debug/tcp_trace.csv` (`pre_pp_throttle` 0.75 → 0.05) | inspected in code + seen in logs (not a wiring bug) |
| D8 | Telemetry `applied_*` is `ego.get_control()` read before the next tick, so it equals the previous tick's command. The harness applies the raw control unchanged. | all (telemetry) | `_common.py:2343-2347`; log check: applied == raw[t] or raw[t-1] in 174,999 of 175,000 rows | inspected in code + seen in logs |
| D9 | Command input fixed at LANEFOLLOW, even in junctions | TCP, CILRS, InterFuser | `lane_route.py:66-70`; `tcp_model.py:517`; `_legacy_vision_common.py` `_route_command` | inspected in code; effect inferred (minor) |
| D10 | Learned controllers use 3-D `|v|` while the leaderboard speedometer uses the forward-projected speed. The difference matters only during the fall or on slopes. | TransFuser, TCP, CILRS, AIM, NEAT, InterFuser | `transfuser_model.py:810-819`, `_legacy_vision_common.py` `_ego_speed_mps`, `interfuser_model.py:848-857` | inspected in code |
| D11 | VLM/planner dashcam frame is 4–6 ticks old at query time (asynchronous callback, not frame-synced) | VLMs, (OpenEMMA uses its own synced camera) | telemetry `world_frame - obs_image_frame` in `tmp/_protocol_v2/glm-4.5v/s0/*/strict_telemetry.json` vs `vlm_debug/vlm_trace.csv` `input_frame` | seen in logs |
| D12 | OpenEMMA `raise_on_planner_error` → no control applied on an error tick (harness skips `apply_control`) | OpenEMMA | runner `:341`; `_trajectory_planner_common.py:323-327`; `_common.py:2324-2332` | inspected in code; did not occur in logs |

Things checked that showed **no** defect:
- Every checkpoint file exists and loaded fully. TransFuser's single unexpected key is the deleted stem conv.
- No sensor-sync failures beyond tick 1: every trace's `no_synced_sensors` count is 25 per 25 episodes, i.e. the first tick only.
- No `controller_step_error` or `controller_invalid_control` events in any model.
- `hand_brake` is False on every row; gear is 0/1 automatic; no reverse.
- No harness throttle cap and no autopilot on controller runs.
- TransFuser target-point frame is correct (straight ahead).
- VLM parse/fallback never produced a spurious STOP.

## Only resolvable by a live CARLA rollout

1. **D6 root cause:** world `synchronous_mode` and `fixed_delta_seconds` during the settle loop, and whether `spawn_settle_ticks` executes before or after another client's settings. Whether moving settle inside `SyncModeContext` removes the fall.
2. **D1 / TransFuser no-start:** does TransFuser start in the same stations with the scenario actors removed (empty-road control), or with an extended horizon (> 55 s, to reach the creep)? Is the throttle-pulse breakaway threshold (≥ 0.2–0.3 s at 0.75) the same on these Town03 stations?
3. **D2:** re-run TransFuser with the wrapper `action_repeat=1` so the agent's own repeat gives 10 Hz. Does dithering change?
4. **D3:** re-run TransFuser with upstream `brake_ratio = 1.1`, the upstream aim point, and the safety box gated on creep. This measures how far the patched controller departs from the released agent.
5. **D4:** re-run TCP, AIM, NEAT and InterFuser with leaderboard-style targets (route downsampled to 50 m, `RoutePlanner(4, 50)`). Does median speed or stop frequency change, especially AIM `flagger_slow_then_stop`?
6. **D5:** re-run InterFuser with the wrapper LiDAR attributes (85 m / 64 channels / 600 k / dropoff). Do the object-brake episodes (`green_stop`, `emergency_scene_blocking`) and the unexplained `school_crossing_guard` brake persist? Log the red-light, stop-sign and junction probabilities in the trace to attribute `school_crossing_guard`.
7. **D7:** TCP with `--tcp-no-postprocess` (already wired, `_run_more_e2e_sweep.py:348-349`) or the B2D "merge_ctrl_traj" mode, to separate cap-induced slowness from model intent.
8. **D11:** does feeding a frame-synced dashcam image change any VLM first decision? The first decisions were all STOP/HOLD from t = 0.05 in the stationary episodes.
