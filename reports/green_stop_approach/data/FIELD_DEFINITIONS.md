# Field definitions: `runs.csv` and `vlm_queries.csv`

**Common rules**
- Times are simulation time `sim_time` in seconds (first episode tick 0.05 s).
- `front` = distance in metres along the lane axis from the vehicle front (foremost bounding-box vertex) to the
  ego-lane stop line; positive before the line, negative past it. `forward` = route progress of the vehicle centre.
- Missing values are empty cells, never 0.
- Telemetry row order in the synchronous loop (`marshal_bench/scenarios/_common.py`): world.tick → sim_time += dt →
  trigger / gesture → controller.step → apply_control → get_control → row written.

| Field | Definition | Unit | Evidence |
|---|---|---|---|
| official_verdict / official_reason | copied from the episode's `strict_scoring.json` | — | recorded |
| reference_crossed_s / signal_onset_s | `approach_protocol_outcome` event | s | recorded |
| net_progress_m | front(first row) − min(front) | m | computed from records |
| moved | net_progress_m ≥ 0.5 m (largest front jitter between samples at rest: 0.0131 m) | bool | computed |
| legacy_engaged_5kmh_1m | max speed ≥ 5 km/h AND net_progress ≥ 1 m (the scorer's engagement thresholds) | bool | computed |
| approach_state | 1 execution / data error; 2 no movement (< 0.5 m); 3 moved, reference point not reached; 4 reached, normal approach not met; 5 normal approach, then reference point / signal (mutually exclusive) | — | computed |
| normal_approach, win_speed_* | candidate classification logged at run time (2 s before the reference point: minimum ≥ 20 km/h, range ≤ 2 km/h); never changed afterwards | km/h | recorded |
| first_raw_brake_ge0p1_s | first row after the reference point with raw_brake ≥ 0.1 = the command the controller returned on that tick | s | recorded |
| first_applied_brake_ge0p1_s | first row after the reference point with applied_brake ≥ 0.1. applied_*(t) equals raw_*(t − 1 tick) in 99.97% of 36,773 row pairs, so it is the client-side control state read back, **not** the moment the command took effect | s | recorded |
| first_raw/applied_brake_gt0_s | as above with threshold > 0 | s | recorded |
| first_speed_decrease_after_raw_brake_s | first row after a raw brake (≥ 0.1) whose speed is below the previous sample; not a measured physical latency | s | computed |
| decel_start_s | first row after the reference point whose speed is 2 km/h below the running maximum (search: reference point to episode end) | s | computed |
| first_below_1kmh_s | first < 1 km/h after the reference point (to episode end) | s | computed |
| first_below_1kmh_within_ref12_s | as above, within reference point + 12 s | s | computed |
| mean_decel_mps2 / mean_decel_interval | (v(decel_start) − v(first_below_1kmh)) / 3.6 / Δt with the recorded end speed (never assumed 0); interval and speeds in the interval column | m/s² | computed |
| peak_decel_adjacent_mps2 / peak_decel_at_s | largest speed drop between adjacent samples (0.05 s), reference point to episode end; a finite difference, not an accelerometer reading | m/s² | computed |
| held_stop_start_s / _window_end_s / _front_m | diagnostic held stop: the **earliest** window within reference point + 12 s with every sample ≤ 1.0 km/h, length ≥ 2.0 s, net displacement ≤ 0.5 m and sample spacing ≤ 1.5 × 0.05 s. Not the Strict definition | s, m | computed |
| held_stop_start_s_legacy_fn | the same quantity from the earlier `_held()` helper, for comparison | s | computed |
| redeparture_after_low_s | first ≥ 5 km/h after first_below_1kmh_within_ref12_s | s | computed |
| t_line_cross_s | first row after the reference point with front < 0 | s | recorded |
| officer_active_first/last_s | first / last telemetry row with officer_active = True (run-time rule: accumulated floating-point sim_time ≤ onset + 12) | s | recorded |
| scored_onset_s / scored_active_end_s | `active_window` of `strict_scoring.json` (the scorer includes rows whose rounded time ≤ active_end) | s | recorded |
| diag_* | each STOP condition evaluated **independently** on the scorer's windows (onset to active_end; enforcement from onset + 3 s). Does not replace the official reason. `diag_first_fail_reproduced` = the first failing condition in the scorer's check order equals the official reason | — | computed |
| diag_front_margin_lt1m vs diag_line_crossed_front_lt0 | min front < 1 m in the active window vs < 0 m (the line actually crossed); the first can be true while the second is false | bool | computed |
| vlm_post1/2_* | first and second VLM query after the signal: output tick, input-frame time (obs_image_sim_ts − (world_elapsed_s − sim_time)), action, who, front at output | s, m | recorded + clock conversion |
| vlm_post_wall_latency_min/max_s | wall-clock inference time of post-signal queries within reference point + 12 s; the simulation is paused meanwhile (synchronous mode) | s (wall) | recorded |
| data_valid | no sim_time defect (missing, None, non-numeric, NaN/inf, negative, non-increasing, gap > 1.5 × dt) AND finite ego_speed_kmh and stopline_front_m in every row. If False, approach_state = 1 and no motion or stop values are derived | — | computed |
| data_issues | defect kinds and counts (e.g. `time_none=1;speed_invalid=2`); empty when there is none | — | computed |
| n_gaps / n_nonincreasing | gap and non-increasing counts from the time validation (defective rows skipped) | — | computed |

`held_window()` treats rows with a defective time, speed or forward value as a break in the window (no interpolation).

**Additional columns in `vlm_queries.csv`**
- `raw_brake_same_tick`, `applied_brake_same_tick`, `applied_brake_next_tick`: brake records at the output tick and the next tick.
- `phase`: `pre` if the output tick is before the signal (officer-absent condition: before the reference point), otherwise `post`.
- `input_after_signal`: input-frame time ≥ signal time.
