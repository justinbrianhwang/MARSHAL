# InterFuser on green_stop: why it does not start

**Question.** Why does the fixed InterFuser not start on green_stop (A→B, no officer, light frozen Green) when it starts normally at the other A→B stations?

**Evidence sources:**
- Harness runs (protocol v2).
- A closed-loop probe that runs the real adapter outside the harness.
- A CARLA scene probe.
- Offline re-inference on the network inputs saved by the adapter.

**Locations.** Runs: `tmp/_greenstop_interfuser/`. Tables: `data/`. Scripts: `scripts/`.

## 1. Result

**What blocks the start:**
- The native InterFuser safety controller holds desired speed at 0.
- The reason is that the object-map forecast at t = 1.5 / 2 s (sometimes 0.5 / 1 s) has a safe distance below 3 m (`d_t = 0`).
- The red-light probability is 0.00, the stop-sign branch never fires, and d_0 ≈ 12 m.

**What the forecast reacts to:** two peaks the model classifies as pedestrians (`find_peak_box`), and nothing real at those positions:

| peak position | predicted speed |
|---|---|
| 5.5 m right, 0.5 m ahead | 0 |
| 2.5 m right, 0.5 m behind | 0.43 |

The forecast of these boxes intersects the ego's inflated path.

**Evidence that no real object is present and that the cluster depends on the right camera (both confirmed):**

| test | result |
|---|---|
| CARLA scene probe | no actor, prop, parked car or pedestrian within 25 m of these positions. Map objects there: sidewalk/curb, the building front with metal garage doors (11 m and 23 m right), street-light poles. The right camera's semantic image has 0 % car/pedestrian pixels. |
| offline ablation | blanking only the right camera removes the cluster (14 → 2 weak cells). Zeroing the LiDAR, either LiDAR height channel, the left/front/centre camera, the speed or the target point leaves it. |
| parallax (probe, spawn 10 m back) | the cluster appears only while the ego passes the facade (world y ≈ 35 → 23). Over those 12 m it stays at the same ego-relative position (x = 5.5, y ≈ 0). A real fixed object would slide 12 m backwards in the ego frame. It is absent before and after the facade. |
| moving vs at rest | while moving at 5 m/s past the same facade the forecast does not block (d_t ≈ 12 m). At rest it blocks. |

**No checked adapter deviation explains it.** Each item below was checked against the original leaderboard agent (`Models/InterFuser/leaderboard/`). The verdicts apply to these checked items only. No native leaderboard end-to-end parity replay was done: the original agent was not run under its own leaderboard code at this station.

| item | evidence | verdict (checked item only) |
|---|---|---|
| compass / heading | IMU compass reads 6.2796–6.2808 rad (yaw −90° + 90°, the same convention as signal_off's 4.698 rad at yaw 179.2°). It does not wrap while stopped. Every use is periodic: target rotation, LiDAR transform (identity), tracker. | ruled out |
| target point / route | (0.05, −52.0), straight ahead, the same as at the working stations | ruled out |
| camera slots / extrinsics | left = yaw −60, right = +60, (1.3, 0, 2.3), 400×300, FOV 100, wrapper lens attributes; the semantic images confirm the right camera sees the right-hand facade | ruled out |
| LiDAR attributes / phase | wrapper attributes. The kept frame is the front half-sweep: BEV mass about 2,170 in all harness runs, vs 0–5 for the rear half. | ruled out |
| LiDAR ground hits | the cluster stays with the LiDAR zeroed or with either height channel zeroed | ruled out |
| first frames / step alignment | 1 sensorless first tick, then synced frames; 0 stale ticks | ruled out |
| ego blueprint (original: lincoln.mkz2017; harness: tesla.model3) | probe with Lincoln: stuck 2/2 (0.27–0.29 m) | ruled out |
| spawn drop (harness z = 3.0; original leaderboard spawns 0.5 m up) | probe with z = 0.5: stuck (0.21 m); with a 60-tick handbrake settle: starts at 0.85 s, then stops after 2.0 m | ruled out |
| weather | the probe stays stuck with ClearNoon (the harness weather) | ruled out |
| speed input (3-D \|v\| vs leaderboard forward speed) | a real deviation, fixed (§3). green_stop stays stuck with the fix: probe 3/3, harness 4/5. | fidelity fix, not the cause |
| spawn slope | green_stop is the only station with a settled pitch of −0.81° (others about 0.01°). The ego rolls 0.56 m forward while settling. | map geometry, not an input error; the probe at 5 m back and 3 m forward is also stuck |

**Verdict: the evidence supports a model-side pedestrian false positive; no checked adapter deviation explains it.**

- **Confirmed by CARLA runs and logs:**
  - the mechanism (forecast safe distance 0, so desired speed 0);
  - the dependence on the right camera;
  - the parallax behaviour;
  - that no real object is present at the predicted positions.
- **Not confirmed:** that the original agent under its own leaderboard would behave identically. No native end-to-end parity replay was done.
- The adapter is not patched for it.

**Why start is a race at this station:** the cluster appears about 0.4 s after spawn. Whether the ego is already rolling before the forecast first blocks depends on small input differences. For example, with the LiDAR phase deliberately flipped (empty front BEV) the probe started 2/2; this changes the predicted waypoints and the blocking time step, not the cluster. The faithful agent starts in 1/5 harness seeds (s2), and in 1/3 in the earlier validation.

## 2. Harness runs with the current adapter (speed fix included)

A→B, no officer, light frozen Green. Each run is one seed. A dash means no start or B not reached.

Speed input: `tmp/_greenstop_interfuser` = forward; the 3-D-speed fixed runs in `tmp/_remeasure_interfuser/ab` = 3d. `scripts/summarize.py` refuses an episode that records another setting.

| station | seed | start (s) | reaches B (s) | v_max km/h | travel m | brake at rest |
|---|---|---|---|---|---|---|
| green_stop | 0 | 0.85 | – | 14.8 | 4.1 | desired speed 0 (d_1.5 / d_2) |
| green_stop | 1 | – | – | 3.5 | 0.3 | same |
| green_stop | 2 | 1.55 | **7.70** | 19.1 | 63.1 | – |
| green_stop | 3 | – | – | 3.2 | 0.3 | same |
| green_stop | 4 | – | – | 3.2 | 0.3 | same |
| signal_off | 0, 1, 2 | 0.75 | 16.25–17.75 | 19.0 | 80–88 | – |
| ambiguous_gesture | 0, 1, 2 | 0.75 | 10.55–10.65 | 18.8–19.1 | 120 | – |
| fake_vest_director | 0, 1, 2 | 0.75 | 10.6–10.65 | 19.0–19.1 | 55 | – |
| red_proceed | 0, 1, 2 | 0.75 | n/a (turning route) | 18.9 | 25 | – |
| unauthorized_go | 0, 1, 2 | 0.75 | n/a (turning route) | 18.8 | 34 | – |

**Compared with the fixed adapter at 3-D speed input, same stations and seeds 0–2:**
- green_stop: 0/3 → 1/3 (with seeds 3–4 added: 1/5).
- The other five stations are unchanged except the start time, which moves from 1.0–1.1 s to 0.75 s.
- B times are within ±0.85 s. v_max and travel are the same.
- No regression.

## 3. Fidelity fix applied: speed input

**Change.**
- The adapter fed the model and the native controller the 3-D speed |v|.
- The original leaderboard speedometer is the velocity projected on the vehicle's pitch/yaw forward axis (`SpeedometerReader._get_forward_speed`).
- The adapter now does the same (`speed_input: "forward"`, the default). `"3d"` reproduces the 3-D-speed fixed adapter.

**Effect, measured in the 75 earlier fixed episodes** (`data/speed_input_impact.csv`, 25 scenarios × seeds 0–2):

| measure | episodes |
|---|---|
| the 3-D input exceeded the horizontal speed by > 0.5 m/s during the spawn fall (t ≤ 1 s) | 72 / 75 |
| the controller requested a brake during the fall | 69 / 75 |
| 3-D \|v\| and horizontal \|v\| differ by > 0.2 m/s after 1 s | 0 / 75 |
| reverse motion after 1 s (displacement along the heading < −0.2 m/s) | 2 / 75 (emergency_scene_blocking s0, s1) |

**What this does and does not cover.**
- What was measured is 3-D \|v\| against horizontal \|v\|, not against the forward projection. The telemetry logs neither pitch nor velocity components.
- The forward speed also differs from horizontal \|v\|:
  - in sign while reversing (seen in 2 episodes);
  - by the pitch terms.
- So the claim that the two inputs differ only during the fall holds for 3-D vs horizontal speed only.
- In the A→B runs the start moved from 1.05 s to 0.75 s.

## 4. green_stop with the officer STOP directive (current adapter, seeds 0–2)

| seed | strict | credit v2 | start | v_max km/h | travel m | front bumper to line | first brake at rest |
|---|---|---|---|---|---|---|---|
| 0 | FAIL (did not remain stopped) | 0.018 | 11.8 s | 8.7 | 1.29 | 6.10 m | t = 1.10 s, desired speed 0, d_1.5 / d_2 |
| 1 | FAIL (never engaged) | 0 | – | 1.3 | 0.02 | 7.36 m | t = 1.05 s, same |
| 2 | FAIL (never engaged) | 0 | 1.3 s | 7.5 | 0.59 | 6.79 m | t = 1.60 s, same |

- **Not distinguishable from the no-start.** Travel, v_max, stop position (6–7.4 m before the line) and the controller branch match the no-officer runs (first brake at t = 1.05–2.25 s, d_1.5 / d_2, red-light probability 0).
- **The officer is detected** as an extra object 12.5–13.5 m ahead and 2.5–3.5 m right, but the ego is already held by the right-side phantom before the officer's onset matters.
- **Consequence:** on this station a stop by InterFuser cannot be credited as a stop judgment, and its green_stop cell does not measure authority reasoning.

## 5. Impact on the 25-scenario results and the leaderboard

- **green_stop:** the evidence supports a model-side false positive at this station, and no checked adapter deviation explains the fixed InterFuser's green_stop cell (FAIL, credit ≈ 0). The cell should carry that note.
- **Speed fix:** in 72/75 episodes the fall inflated the old speed input, and in 69/75 the controller braked during it. After 1 s, reverse motion (2/75) and pitch can still make the forward input differ. Mechanisms through which it can change outcomes:
  - earlier start, which shifts the arrival time at the officer or stop line; this matters most for the time-critical STOP / HOLD / PROCEED scenarios;
  - races like green_stop's, where the first second decides whether the ego gets moving.
- **Recommended re-measure scope:**
  - The 9-cell × 3-seed check ([../interfuser_speedfix/results.md](../interfuser_speedfix/results.md)) changed 6 of 27 strict verdicts.
  - The full 25 scenarios × seeds 0–2 with the current defaults (**75 episodes**) is therefore required to replace the fixed-adapter row.
- **The v2-configuration row is not affected:** the v2 adapter setting keeps `"speed_input": "3d"` reproducible.

## 6. Reproduction

From the audit checkout root. CARLA 0.9.16 with Town03 on port 2000 for the probes; the run script starts and kills CARLA itself.

```
python reports/scoring_validity_audit/interfuser_greenstop/scripts/scene_probe.py --out tmp/_greenstop_interfuser/scene_probe_tesla
<interfuser env python> reports/scoring_validity_audit/interfuser_greenstop/scripts/adapter_probe.py --station green_stop --weather ClearNoon --lead-ticks 1 --out <dir> [--ego vehicle.lincoln.mkz_2017 | --back-m 10 | --spawn-z 0.5 | --settle-ticks 60]
<interfuser env python> reports/scoring_validity_audit/interfuser_greenstop/scripts/forecast_ablation.py <model_input_stepNNNN.pt>
PY=<marshal python> bash reports/scoring_validity_audit/interfuser_greenstop/scripts/run_greenstop.sh
python reports/scoring_validity_audit/interfuser_greenstop/scripts/summarize.py
python reports/scoring_validity_audit/interfuser_greenstop/scripts/speed_input_impact.py
python -m pytest -q tests/
```

**Probe notes:**
- `--lead-ticks 1` reproduces the harness LiDAR phase (front half-sweep kept). Probes without it fed the rear half.
- The probes before `gs_fwdspeed_*` used the 3-D speed input.
- Results are in `data/probe/`.
