# Follow-ups: AIM route target and TransFuser inference rate

Plan only. Nothing here has been run. Background: [validation_results.md](validation_results.md) §1 and
[data/control_chain_notes.md](data/control_chain_notes.md) (D2, D4).

## A. AIM: 4 m route target

**Observed.** On the no-officer `green_stop` station, AIM never reaches 5 km/h (v_max 4.6 km/h, 8.8 m). With a 50 m
target (`lookahead_m: 50`) it starts at 1.2 s and reaches 16.6 km/h, but it stops about 16.5 m past the line and does
not reach B (20 m past the line) within 25 s.

**Compare with the original** (`Models/AIM/src/leaderboard/team_code/aim_agent.py` + leaderboard
`autonomous_agent.set_global_plan`):

1. Route input: the original downsamples the dense route with `downsample_route(plan, 50)` and then runs
   `RoutePlanner(4.0, 50.0)`, so the target is the next kept point beyond 4 m (up to about 50 m, shrinking as the ego
   approaches it). The `lookahead_m: 50` variant instead pins the target at the first dense point ≥ 50 m on every
   tick, which is not the same signal (it never shrinks). Port the exact downsample-then-`RoutePlanner` path, as
   done for InterFuser (`interfuser_model.downsample_route_ids`).
2. Route refresh: check that a rolling-route rebuild does not push the dense plan into the planner (the TransFuser
   edge case in control_chain_notes §3).
3. Why it stops 16.5 m past the line: read `aim_debug/aim_trace.csv` of the 50 m run for predicted waypoints and
   `control_pid` desired speed at the stop. Candidates: target point collapsing at the end of a downsample segment
   or after a route refresh, or the PID brake rule (desired < 0.4 m/s).
4. Speed input (D10): 3-D |v| during the spawn fall vs forward speed.

**Runs** (no officer, light held green, seeds 0, 1, 2, `--tag` per variant, out root `tmp/_validation_aim/`):
`as-is`, `leaderboard route (downsample 50 + RoutePlanner(4, 50))`, and the old `lookahead_m: 50` for reference.

**Counts as a fix:** the leaderboard-route variant reproduces the original agent's input path line for line, and
with it AIM starts (≥ 5 km/h) and reaches B on all three seeds. If it still stops short of B with the faithful
route, record that as model behaviour, not an integration defect. Then re-run the 25 v2 scenarios (seed 0) and
re-score only with the published collector.

## B. TransFuser: 5 Hz instead of 10 Hz

**Observed.** The wrapper repeats each control for 2 ticks and the vendored agent also returns its cached control on
odd agent steps, so the network runs every 4 ticks (5 Hz) instead of every 2 ticks (10 Hz). No officer: start at
6.15 s at 5 Hz vs 1.4 s with `action_repeat: 1` (10 Hz).

**Compare with the original** (`team_code_transfuser/submission_agent.py` under the leaderboard at 20 Hz):

1. Agent step cadence: the leaderboard calls `run_step` every tick and the agent itself skips odd steps, so the
   network runs at 10 Hz. Confirm the wrapper's `action_repeat` is the only extra repeat.
2. GPS denoiser / `update_gps_buffer`: with EgoModel dt = 1/20 it must be propagated once per tick; check it with
   `action_repeat: 1`.
3. Creep / stuck logic (`stuck_threshold`, `creep_duration`) counted in agent steps: confirm the step counter
   advances at the original rate.
4. The vendored controller patches (brake_ratio 1.4, aim-point rewrite, safety box; D3) are separate deviations.
   Keep them out of this item, but list them as the next comparison.

**Runs** (seeds 0, 1, 2, out root `tmp/_validation_transfuser/`): no-officer `green_stop` with `action_repeat: 1`
and with the default; then, if 10 Hz is adopted, `green_stop` with the officer (leaderboard condition) to check
that the non-start with an officer is unchanged.

**Counts as a fix:** with `action_repeat: 1` the per-tick trace shows a new network output every 2 ticks (as in the
original), the start time on the empty road is about 1.4 s on all seeds, and the default is changed to 1. Then re-run
the 25 v2 scenarios (seed 0) and report which TransFuser leaderboard cells change.
