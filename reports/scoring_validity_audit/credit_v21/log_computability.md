# Computability of the v2.1-candidate credit from existing logs

**Verdict:** every term of the v2.1-candidate credit can be computed from the recorded episodes, with no rollout. It
has been computed for all 650 leaderboard episodes and all 38 `green_stop` validation episodes
(`data/episodes_v21.csv`, `data/validation_v21.csv`).

**Implementation:** each fix is a patched copy of the published function, applied only while rescoring
(`scripts/credit_v21.py`).

**Reproduction gate:** with every fix off, all 650 credits and the 14 published leaderboard rows (graded and strict
k/n) reproduce exactly.

## Fix definitions

| fix | change (strict unchanged everywhere) |
|---|---|
| F1 (A′) | The STOP/HOLD engagement approach window ends at the first stop **after the first post-onset motion** (> 1 km/h), not at the first sub-1 km/h tick at or after onset. The onset row that anchors the approach distance is unchanged (the audit's A′). |
| F6 | STOP/HOLD latency for an ego at rest (≤ 1 km/h) at onset: τ = t_resp − onset. Here t_resp is the first deceleration ≥ 1 m/s² or the first v ≤ 1 km/h **after its first post-onset motion**. An ego that never moves after onset keeps τ = 0. |
| F3 | F_safe = 1 / 0.25 / 0.10 / 0 for n = 0 / 1 / 2–5 / > 5 **distinct contacts**. A new contact starts when the counted event's identity changes or when more than 1.0 s has passed since the previous counted event. Identity = (actor id where logged, type id): two events match when their types match and, where both carry an actor id, their actor ids match. |
| F7 | YIELD: v_min is taken over every post-onset row (the strict window) instead of the ambulance-active window. |
| F2 (a3) | E_app = 1[v_peak ≥ 9 km/h ∨ Δx ≥ 2.0 m], with v_peak and Δx taken over t ≤ min(directive end, 25 s), on STOP/HOLD cells without an approach requirement ([f2_handling.md](f2_handling.md)). |

## Inputs × availability

All per-tick fields are in `strict_telemetry.json` → `telemetry` (50 ms ticks, 25 s horizon).

| fix | input | field(s) | available | coverage |
|---|---|---|---|---|
| F1 | ego speed, time | `ego_speed_kmh`, `sim_time` | yes | 650/650 |
| F1 | directive onset / end | `officer_active`, `officer_gesture_id`, `officer_onset_time`, `officer_duration_sec` (phase detection as published) | yes | 650/650 |
| F1 | approach distance | `distance_to_stopline_m`; fallback `ego_forward_m` | yes | 650/650 |
| F6 | ego speed, time, onset | as F1 | yes | 650/650 |
| F3 | per-tick cumulative callback count | `collision_count` | yes | 650/650 |
| F3 | type of **every** counted event, in callback order | `events.json`: `name == "collision"`, `payload.other_actor` | yes | 90/90 collided episodes (horizon) |
| F3 | actor id of the first 20 counted events | `strict_telemetry.json` → `metadata.collisions[*]` (`sim_time`, `other_actor_id`, `other_type_id`) | yes, first 20 only | 90/90 |
| F7 | ego speed after onset | `ego_speed_kmh`, `sim_time` | yes | 650/650 |
| F2 | peak speed, forward progress for t ≤ min(directive end, 25 s) | `ego_speed_kmh`, `ego_forward_m`, `sim_time` | yes | 650/650 |
| all | expected action, scenario, reaction budget | collector arguments (scenario registry) | yes | 650/650 |

## Verification of the collision logs (F3)

**The event log is complete [E].**

- In all 90 episodes with a collision inside the horizon, `events.json` holds at least as many counted collision
  events as the telemetry counter. The surplus (0–30) comes from callbacks after the last telemetry row.
- The counter and the event log share one callback: `static.road` and self-contacts are excluded from both.
- The first 20 event types match `metadata.collisions` in order in 90/90 episodes.
- Event k is timed at the first tick whose cumulative `collision_count` reaches k.

**Identity is exact for events 1–20 and type-level after event 20.**

- Actor ids exist for the first 20 counted events only. Event 21 onwards carries its type id, and it continues the
  current contact when its type matches.
- Type-level identity is not sufficient where it can be checked. On events 1–20, counting by type alone gives fewer
  contacts than counting by actor id in 8 of the 90 episodes: 1 instead of 4–18, because same-type actors alternate
  within 1 s. These are baseline, MPC and PID on `flagger_control`, `flagger_slow_then_stop` or
  `barricade_self_detour`, and OpenEMMA on `barricade_self_detour`. In the other 82 the two counts agree.
- After event 20 the same merge cannot be detected. 77 of the 90 episodes have more than 20 counted events in the
  horizon, so their count is a **lower bound** beyond event 20.
- How far the bound can bind: one hidden contact would change the F_safe band in 56 of those 77 episodes
  (1 → 2 contacts, F_safe 0.25 → 0.10). Per-episode counts are in `data/collision_logs.csv` (type-only and
  actor-id counts, events without an actor id, and whether one hidden contact changes the band).

**Merge gap** (`data/f3_merge_gap.csv`). F_safe band changes among the 82 collided scored episodes, relative to 1.0 s:

| gap | 0.25 s | 0.5 s | 2.0 s | 5.0 s |
|---|---:|---:|---:|---:|
| band changes | 14 | 7 | 4 | 8 |

**Effect.**

| | count |
|---|---|
| collided scored episodes (STOP/HOLD scored up to the directive end) | 82 |
| > 5 callbacks | 76 |
| > 5 distinct contacts | 7 (15–18 separated contacts) |
| contacts per episode | 1 contact: 61; 2: 11; 3: 2; 4: 1 |
| credit changes under F3 | 65 |

F_safe after F3, in the 65 changed episodes: 0.25 in 53 and 0.10 in 12.

## Limitations

- **Timing resolution:** event times have tick resolution (50 ms). `events.json` timestamps are wall-clock and are
  used only for order.
- **Scored rows only:** distinct contacts are counted on the scored rows, the same rows the published safety factor
  uses. STOP/HOLD rows end at the directive end, so contacts after the directive do not count.
- **Synthetic results are scorer-logic only:** the instruction-independent policy results (F2 analysis) use the
  recorded station templates with a scripted speed profile, lateral motion 0, and no actor reactions. They are not
  CARLA rollouts. The CARLA evidence for F2 is the `green_stop` fixture set (3 seeds per behaviour).
- **Provenance:** episodes carry no code hash (`code_version = None`). Nine leaderboard episodes come from a pilot
  copy or an unlogged re-run, as already recorded in the audit.
- **Not computable from these logs:** instruction-response evidence on any station other than `green_stop`, which
  needs matched no-officer or PROCEED runs. The rule F2 (a3) is therefore an eligibility repair, not an
  identifiability fix.
