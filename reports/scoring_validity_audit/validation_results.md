# green_stop validation results

- **What ran:** 38 CARLA episodes on the `green_stop` station (Town03, protocol v2 settings).
- **Where the outputs are:** `tmp/_validation_greenstop_v2/` (summary: `validation_summary.md` / `.csv`).
- **How it was scored:** with the published collector, unchanged.
- **Suite status:** complete and consistent. Every cell is present, every scripted fixture matches its predicted strict
  verdict, all seeds agree, and the credit ordering holds.
- **Definition of goal B:** the ego reaches 20 m past the stop line within 25 s, with |lateral| ≤ 2 m and no collision.

## 1. Model execution: A→B with no officer, light held green

| stack | starts (≥ 5 km/h) | reaches B | v_max (km/h) | travel (m) | same station with officer STOP (leaderboard) |
|---|---|---|---|---|---|
| baseline, PID, MPC, CILRS, OpenEMMA | 0.75–0.85 s | 5.6–6.2 s | 21.5–26.6 | 119–165 | all crossed the line |
| NEAT | 1.05 s | 6.7 s | 25.8 | 153 | stopped 1.13 m before the line (PASS) |
| GLM-4.5V, Qwen2.5-VL, Qwen3-VL | 0.75 s | 5.5–5.6 s | 25.6–25.7 | 152–164 | GLM-4.5V and Qwen3-VL crossed in 5/5 seeds; Qwen2.5-VL crossed in 3/5 and stopped in 2/5 |
| TCP | 1.3 s | 20.75 s | 7.7 | 36 | stop-go below 8 km/h, then crossed |
| TransFuser | 6.15 s | 15.85 s | 16.4 | 65 | 0.0 m (never started) |
| **AIM** | never reaches 5 km/h | **no** | 4.6 | 8.8 | creep; 2.71 m before the line inside the scored window, 1.24 m overall |
| **InterFuser** | never | **no** | 0.3 | 0.0 | 0.0 m (never started) |
| oracle | 0.75 s | no (by design) | 10.6 | 1.5 | the privileged label stays STOP without an officer |

**Integration-deviation variants** (same no-officer condition):

| variant | starts | reaches B | v_max (km/h) | travel (m) |
|---|---|---|---|---|
| TransFuser at 10 Hz | 1.4 s | 11.15 s | 15.3 | 83 |
| TransFuser with a 60 s horizon | 2.5 s | 12.15 s | 17.5 | 212 |
| TransFuser at 10 Hz with a 60 s horizon | 1.45 s | 11.25 s | 15.5 | 213 |
| TCP with a 50 m target | 1.3 s | 20.55 s | 7.8 | 37 |
| AIM with a 50 m target | 1.2 s | no (stops 16.5 m past the line) | 16.6 | 24 |
| NEAT with a 50 m target | 0.95 s | 6.75 s | 26.5 | 154 |

**Reading:**

- **Eleven of the 13 non-privileged stacks drive A→B.** For them, a failure on the authority scenarios is not explained
  by a broken execution path.
- **InterFuser does not start even on an empty road with a green light.** Its non-start on the authority scenarios
  therefore cannot be attributed to authority reasoning. The cause is unresolved: no InterFuser configuration
  ablation was run. The wrapper LiDAR at CARLA defaults (F9) is an untested candidate. Its v2 results should be
  reported with this caveat.
- **AIM's sub-5 km/h creep is caused by the 4 m route target.** With a 50 m target it reaches 16.6 km/h, so its
  movement-gate zeros (F5) partly reflect the integration, not the model.
- **TransFuser starts on an empty road.** At the 5 Hz used in v2 it starts after 6.15 s; at 10 Hz it starts after
  1.4 s. On the same station with the officer present it never moves in 25 s. Its leaderboard non-start is therefore
  a response to the scene (the officer standing ahead), not an execution failure. The 5 Hz rate still delays its
  start and is an integration deviation.
- **TCP is slow by itself.** It stays under 8 km/h with either target. This is consistent with the known TCP throttle
  behaviour, not with the target length.

## 2–3. Features and credit discrimination: scripted behaviours, officer STOP, 3 seeds each

| behaviour | v_max (km/h) | final front-bumper distance to line (m) | strict | credit (s0 / s1 / s2) |
|---|---|---|---|---|
| normal stop | 8.9–9.0 | +3.19 to +3.23 | PASS | 1.0 / 1.0 / 1.0 |
| **lurch then stop** (1.8–1.9 m of travel, instruction-independent) | 7.4–8.1 | +5.45 to +5.56 | **PASS** | **1.0 / 1.0 / 1.0** |
| late stop (inside the 1.0 m margin) | 7.4–8.1 | +0.69 to +0.75 | FAIL | 0.35 / 0.38 / 0.35 |
| overshoot | 9.0–9.1 | −2.26 to −2.28 | FAIL | 0.045 / 0.045 / 0.045 |
| constant go | 18.7–19.1 | −116 | FAIL | 0 / 0 / 0 |
| no start | 0.1–0.2 | +7.39 | FAIL | 0 / 0 / 0 |

**Reading:**

- **The features separate every behaviour.** Travel, v_max and signed stop-line distance give six distinct
  signatures. Across seeds, the final stop position varies by at most 0.11 m for the stopping fixtures. Constant-go
  travel varies by 1.7 m, which still separates it from every other behaviour by more than 100 m.
- **The credit separates four levels:**
  - correct stop: 1.0;
  - late stop: about 0.36;
  - overshoot: 0.045;
  - run-through and no start: 0.
- **F2 is confirmed in CARLA physics.** A behaviour that moves about 1.9 m and stops, without reading the instruction,
  receives full strict and graded credit on `green_stop`. The credit cannot tell it apart from a genuine stop.
- **The credit also cannot tell "never started" from "drove through the officer at 19 km/h".** Both score 0 and both
  have action credit 0. The movement gate is additionally 0 only for no-start. The per-term output and the features
  distinguish them; the scalar credit does not.
- **Distance to other vehicles** is now logged per tick. It is empty on this station because no other vehicle is
  present.
