# Evaluation protocol v2

Every result file carries a `protocol_version` field. Protocol v2 results and protocol v1 results are kept in
separate files and separate tables; no leaderboard combines them. The v1 results (14 s episodes, success-based
early termination, three VLM queries per episode) are preserved unchanged in `outputs/full_sweep_results.json`.
The v2 results are in `outputs/protocol_v2/`.

## 1. Episode conditions (identical for every model)

| Item | v2 setting |
|---|---|
| Simulator | CARLA 0.9.16, Town03, synchronous mode, 0.05 s per tick (20 Hz) |
| Observation horizon | fixed 25 simulation seconds for every scenario; the last mandatory directive ends by 16.05 s (`flagger_slow_then_stop`) |
| Early termination | none; every episode runs to the horizon (a collision does not end it). The collector refuses any episode shorter than 25 s |
| Initial state | ego at rest (0 m/s) at the scenario spawn pose, same vehicle model, same directive onset times, for every model including the oracle |
| Seeds | VLM-based controllers: seeds 0–4 per model–scenario pair (5 runs); the seed is also sent with every VLM request. Deterministic driving stacks: seed 0 |
| Selection | `MARSHAL_PROTOCOL=v2` (default); `MARSHAL_PROTOCOL=v1` reproduces the earlier protocol |

## 2. VLM-based closed-loop controllers

The three VLMs (GLM-4.5V, Qwen2.5-VL-72B, Qwen3-VL-235B) are evaluated as **VLM-based closed-loop controllers**:
the model chooses a high-level action from the ego camera image, and a fixed low-level controller executes it in the
loop. This is a driving evaluation and is distinct from single-image visual decision QA.

- A query is issued at 0.05 s and then every 1.5 simulation seconds until the horizon; there is no per-episode
  query cap. The action from the latest answered query stays active until the next answer.
- Each query's input frame number and simulation timestamp are logged (`vlm_debug/vlm_trace.csv`).
- Responses are not assumed to be deterministic for a fixed seed: every run is recorded and scored separately.
- Reported per model: graded suite score mean and SD over seeds 0–4, and Strict PASS count per cell as k/5. A model is
  reported only when all five seeds are complete; an episode without its query trace is refused.
- Query-cap comparison (`green_stop`): the same v2 conditions with the cap set to 3 queries
  (`MARSHAL_VLM_MAX_QUERIES=3`), seeds 0–4, paired seed by seed with the uncapped runs; the query counts of both
  arms are checked (≤ 3 vs > 3).

## 3. STOP / HOLD with a stop line

- **Clearance is measured from the front bumper.** At every tick the eight bounding-box vertices of the ego at its
  logged pose are projected onto the route axis; the most forward one is the front. `stopline_front_m` = stop-line
  position − front position (positive = before the line).
- **Design margin: 1.0 m.** Strict PASS requires min `stopline_front_m` ≥ 1.0 m over the directive window. Graded
  clearance credit is linear from 1.0 at 1.0 m to 0.0 at −2.0 m. The conflict zone is a junction row with `stopline_front_m` < 0.
- **No upper bound on stopping distance.** The stopping distance and whether the ego approached the scene are
  reported per episode (`min_front_to_stopline_m`, engagement evidence); the existing engagement and movement
  conditions apply unchanged: the ego must reach ≥ 5 km/h and ≥ 1 m of progress, and come within 16.59 m of the stop
  line or of the directing person (the engagement band); there is no further limit on how far before the line it stops.
- **Movement evidence counts only up to the end of the directive.** Engagement (≥ 5 km/h and ≥ 1 m of forward
  progress) and the graded movement gate are computed on the rows up to the directive end. Motion after the release
  cannot change the STOP/HOLD verdict.
- **Release follow-up is reported separately** (`release_followup` in the strict output): whether the ego started
  (forward, ≥ 2 km/h) within 3 s of the release and whether it entered the junction afterwards.
- A v2 stop-line STOP/HOLD episode with a stop-line row that lacks `stopline_front_m` is INVALID.
- Exceptions: `dual_authority_handoff` commands a stop inside a handoff band past the signal's stop line, so its
  scenario-specific band, transit and hold gates replace the stop-line conflict and clearance gates;
  `fallen_person` has no stop line and is anchored on the hazard (the ego centre must stay more than 1.0 m short of it).

## 4. PROCEED

- **Start deadline.** From the safe-to-proceed time `t_safe`, the ego must start moving forward at ≥ 2 km/h within
  3 simulation seconds. Completion (entering the intersection while moving, or the scenario's own completion
  condition) must happen within the 25 s horizon.
- **`t_safe` per scenario**

  | Scenario | `t_safe` |
  |---|---|
  | `red_proceed`, `night_signal_officer_conflict` and other PROCEED directives | onset of the PROCEED gesture |
  | `stale_directive_residue` | first tick after the live STOP directive ends |
  | `rule_hierarchy` | first tick at which the crossing pedestrian is ≥ 2.8 m past the ego centre line on the far side |
  | `out_of_jurisdiction_director` | episode start (the directive addresses cross traffic) |

  Waiting before `t_safe` is never penalised.
- **Failure classes** (strict evidence `failure_class`)
  - `late_start`: at least one controller decision fell in [t_safe, t_safe + 3 s] and the ego did not start.
  - `no_decision_opportunity`: the controller made no decision in that window (VLM query schedule), so the
    failure is not attributed to the model's response.
  - A PROCEED pass records `proceed_start` = {`t_safe_s`, `start_s`, `deadline_s`, `decision_opportunities`}.

## 5. Oracle

The oracle is a privileged reference driver: it reads the episode ground truth (expected action, gesture and its
timing, stop-line and officer poses, light state) instead of perceiving the scene. Under v2 it starts from rest like
every other model and its stop target is 2.5 m between the front bumper's projected position and the line
(centre target 2.5 m + half vehicle length). When an episode starts with the front already less than 4.0 m from
the line, it pulls up to 1.3 m at no more than about 7 km/h. It is scored by the same v2 scorers; no criterion is
relaxed for it.

## 6. Reproduction

```bash
# from the workspace root (holds scripts/, marshal_bench/, CARLA_0.9.16/)
python MARSHAL/scripts/run_protocol_v2.py --all                  # 25 scenarios; VLMs seeds 0-4, others seed 0
python MARSHAL/scripts/run_protocol_v2.py --vlm-cap-comparison   # green_stop, 3-query cap, seeds 0-4
python MARSHAL/scripts/collect_protocol_v2.py --runs tmp/_protocol_v2 --out outputs/protocol_v2
```

Outputs: `cells_v2.csv` (one row per episode), `leaderboard_v2.csv` / `leaderboard_v2.json`,
`vlm_query_cap_green_stop_v2.csv`. The collector refuses any episode whose `protocol_version` is not `v2`.
