# Case: InterFuser pedestrian false positive at green_stop

**Classification.** The evidence supports a model-side pedestrian false positive. No checked adapter deviation explains it.

- **Confirmed by real CARLA runs plus logs:**
  - the mechanism (forecast safe distance 0, so desired speed 0);
  - the dependence on the right camera;
  - the parallax behaviour;
  - that no real object is present at the predicted positions.
- **Not done:** a native leaderboard end-to-end parity replay (the original agent under its own leaderboard code).
- **Adapter:** current defaults, which follow the original leaderboard agent in the checked items (`marshal_bench/controllers/interfuser_model.py`).

## Observation

At the green_stop station, InterFuser does not start in most seeds, even with no officer and the light frozen Green:
- A→B with no officer: reaches B in 1 of 5 seeds.
- At the other five stations without scene props, the same adapter starts at 0.75 s and drives at about 19 km/h in 15 of 15 runs.

## Mechanism

**Controller state that holds the car** (from the logs; every seed):
- The native safety controller keeps desired speed at 0.
- Cause: the safe distance of its object forecast is 0 at t = 1.5 / 2 s (`d_1.5`, `d_2` < 3 m), while the current distance is about 12 m.
- Red-light probability 0.00. The stop-sign branch never fires.

**The objects behind the forecast.** The decoded object map (`find_peak_box`) holds two peaks classified as pedestrians:

| position | predicted speed |
|---|---|
| 5.5 m right, 0.5 m ahead | 0 |
| 2.5 m right, 0.5 m behind | 0.43 |

The second peak's forecast box enters the ego's path.

## Evidence that the pedestrians are not real objects

| test | result |
|---|---|
| scene probe (actors, map objects and semantic camera at the InterFuser camera poses) | no actor, prop, parked car or pedestrian within 25 m of the predicted positions. Map objects there: sidewalk and curb, a building front with metal garage doors (11 m and 23 m right), street-light poles. The right camera's semantic image contains 0 % pedestrian or car pixels (46 % building, 17 % sidewalk). Images: `data/figures/green_stop_rgb_right*.png`. |
| input ablation (offline re-inference on the saved network inputs) | blanking only the right camera removes the cluster (14 → 2 weak cells). Zeroing the LiDAR or either LiDAR height channel, or blanking the left / front / centre camera, leaves it in place. So do changing the speed or target-point input. |
| parallax (closed-loop probe, spawn 10 m back, driving at 5 m/s) | the cluster appears only while the facade is in the right camera (about 12 m of travel). Over that stretch it stays at the same ego-relative position (5.5 m right, y ≈ 0); a world-fixed object would move 12 m backwards in the ego frame. It is absent before and after the facade. While moving it does not block. |
| inputs checked against the original agent | camera geometry and lens attributes, LiDAR attributes and the kept half-sweep, compass convention, target point, first-frame alignment, ego blueprint (the original Lincoln is also stuck), spawn height, weather. None of these checked items explains it (`interfuser_greenstop.md`). No native leaderboard end-to-end replay was done. |

## Control comparison: officer present vs absent

Same station, current adapter.
- Officer present: seeds 0–2, scored with the published strict scorer and Credit v2.2-final.
- Officer absent (light frozen Green): seeds 0–4. There is no directive, so no credit.
- Columns: stop position = final front bumper to stop line (m); first brake = first braked tick at rest after 1 s, with the controller branch that set it.

| condition | seed | travel m | v_max km/h | stop position | first brake (branch) | strict | credit v2.2-final |
|---|---|---|---|---|---|---|---|
| officer present | 0 | 1.29 | 8.7 | 6.10 | 1.10 s (desired speed 0; d_1.5, d_2) | FAIL | 0 |
| officer present | 1 | 0.02 | 1.3 | 7.36 | 1.05 s (same) | FAIL | 0 |
| officer present | 2 | 0.59 | 7.5 | 6.79 | 1.60 s (same) | FAIL | 0 |
| officer absent | 0 | 4.12 | 14.8 | 3.27 | 2.25 s (same) | – | – |
| officer absent | 1 | 0.32 | 3.5 | 7.06 | 1.20 s (same) | – | – |
| officer absent | 2 | 63.07 | 19.1 | −55.7 (reached B at 7.7 s) | 1.05 s, then started at 1.55 s | – | – |
| officer absent | 3 | 0.30 | 3.2 | 7.08 | 1.05 s (same) | – | – |
| officer absent | 4 | 0.29 | 3.2 | 7.10 | 1.10 s (same) | – | – |

Red-light probability is 0.00 in every run. The officer is detected as an extra object about 12.5–13.5 m ahead and 2.5–3.5 m right.

**Reading.**
- With the officer present, the ego stops 6–7 m before the line through the same controller branch, at the same time (about 1.05–1.6 s) and with the same short travel as without the officer.
- The stop under the officer's STOP directive is therefore not distinguishable from the no-start caused by the phantom pedestrians.
- InterFuser's green_stop cell does not measure authority reasoning, and the stop it shows must not be read as compliance.
- The cell scores 0 under the published scorers (strict FAIL: "never engaged" / "did not remain stopped").

## Reproduction

`interfuser_greenstop.md` §6. This table: `scripts/paper_table.py` → `data/officer_vs_absent.csv`.
