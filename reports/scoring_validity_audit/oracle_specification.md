# Oracle specification: Privileged Rule-Based Oracle (PRBO)

Scope: `marshal_bench/controllers/oracle.py` (`OracleController`, `name="oracle"`, `track="A"`, oracle.py:60-62).
Registered by `make_controller("oracle", config)` (controllers/__init__.py:78-80) and built by the episode runner
from the scenario config (`run_scenario` in `scenarios/_common.py`). Line numbers are given for `oracle.py`, the
controller wrappers, docs and scripts; `scenarios/_common.py` is cited by function or field name.

## 0. One-line summary

The oracle is **not a perception or reasoning model**. It is handed the scenario's answer
(`Y_expected_action`) plus the directive's addressee, validity, gesture, timing and scene geometry, and
it executes a hand-written longitudinal/lateral recipe for that answer on top of CARLA's stock
`BasicAgent` lane follower. It never reads the camera image.

## 1. Privileged inputs

### 1.1 How it gets privilege

| Channel | Oracle | Track-B (E2E sensor) | Track-C (VLM) |
|---|---|---|---|
| `setup(..., ground_truth)` | full E-tuple ⟨M,J,L,A,G,T,S,V,W,Y⟩ + geometry handles (`_build_ground_truth`), because `track=="A"` / `name=="oracle"` (`_controller_uses_privileged_ground_truth`, applied in `run_scenario` as `privileged_setup`) | reduced dict `{target_speed_kmh, M_map, L_light_state}` (`_non_privileged_setup_context`); TransFuser/InterFuser/TCP discard even that (`transfuser_model.py:116`, `interfuser_model.py:150`, `tcp_model.py:113`) | same reduced dict; reads only `target_speed_kmh` (`vlm_model.py:320`) unless an explicit privileged ablation flag is set (`vlm_model.py:321-323`) |
| Per-tick `observation` | same dict as everyone (`_build_observation`) | own sensor rig spawned by the wrapper (TransFuser: `rgb_front/left/right`, `lidar`, `imu`, `gps`, `transfuser_model.py:34`, `178-188`); uses `sim_time` from obs | `obs["image"]` (ego dashcam), `ego_speed`, `tl_state` (`vlm_model.py:405-465`) |
| Scenario `config` | read directly (officer, second authority, margins; see 1.3) | passed to every controller constructor (`make_controller(..., config=config)` in `run_scenario`); models examined read only their own sub-section (e.g. `interfuser_model.py:86-87`) | same |
| CARLA `world`/`ego` handles | map waypoints, lane topology, ego bounding box | used to attach sensors | map + `BasicAgent` lane keeping, "same approach as the oracle" (`vlm_model.py:665-681`) |

Note: privilege for Tracks B/C is enforced by what the runner passes to `setup` and by code inspection of the
wrappers, not by a sandbox; the runner records `privileged_ground_truth_provided` per episode
(`run_scenario`: result field `privileged_ground_truth_provided`).

### 1.2 Ground-truth (E-tuple) fields the oracle reads

| Field | Source (`_build_ground_truth`) | Used for | oracle.py |
|---|---|---|---|
| `Y_expected_action` | `config.expected_behavior.action` (`_build_ground_truth`: `Y_expected_action`) | **selects the behaviour branch** | 340, 125, 232-239 |
| `T_target_relation` | officer actor metadata (`_build_ground_truth`: `T_target_relation`) | addressee check (non-ego ⇒ PROCEED or HOLD); also returned verbatim as the TAA prediction | 122, 248-249, 341, 346-351 |
| `A_authority.valid` | officer metadata `authority_valid` (`_build_ground_truth`: `A_authority.valid`) | unauthorized-gesture override | 342, 358-360 |
| `A_authority.type` | officer metadata | onset = 0 when no authority; "no-officer STOP" branch | 366-368, 371-373 |
| `G_gesture` | officer metadata `gesture_id` (`_build_ground_truth`: `G_gesture`) | unauthorized-gesture override; detour side fallback | 343, 359, 1180 |
| `L_light_state` | CARLA traffic-light state (`_build_ground_truth`: `L_light_state`) | only inside the unauthorized override (red ⇒ STOP else HOLD) | 344, 360 |
| `S_safety_context` (= `config.scene`) | `_build_ground_truth`: `S_safety_context` | `yield_stop_sec`, `pedestrian_distance`, `pedestrian_clear_sec`, `detour_side`; fallback `crash_distance`, `crash_vehicles`, `block_distance` | 167-177, 1179-1180, 1040-1047 |
| `officer_transform` | officer actor transform (`_build_ground_truth`: `officer_transform`) | ego-officer distance for the far-directive rule | 542-548 |
| `target_speed_kmh` | `config.ego.target_speed` (25 in all configs) | cruise speed of `BasicAgent` | 124, 263, 274 |

E-tuple fields present but **not read** by the oracle: `M_map`, `J_junction`, `G_gesture_onset_sec`,
`G_gesture_duration_sec` (timing is read from config instead, 1.3), `A_second_authority`, `A_appearance`,
`V_visibility`, `W_weather`, `stop_line` (the per-tick distance is used instead), `max_reaction_time_sec`.
`ego_spawn` is read only by `_progress_from_spawn` (1225-1236), which is never called.

### 1.3 Scenario-config fields read directly (bypassing the E-tuple)

| Config key | Used for | oracle.py |
|---|---|---|
| `officer.onset_time` (default 1.0 s) | directive onset; all phase switches key off it | 363-369 |
| `officer.gesture`, `.duration`, `.target_relation` | finite ego-addressed STOP/HOLD ⇒ hold until `onset + duration + 0.5 s` | 23-40, 186-190 |
| `expected_behavior.approach_stop_after_second_authority_m`, `second_authority.distance` (default 16 m) | zone-handoff stop target (dual_authority_handoff) | 155-166 |
| `detour_safety_margin_m` (default 0.55) | detour lateral offset fallback | 927, 1217 |
| `protocol_version` / env `MARSHAL_PROTOCOL` | v1 vs v2 behaviour switches | 66-67, 207, 444 |

### 1.4 Simulator state read every tick

| Quantity | Origin | oracle.py |
|---|---|---|
| `sim_time`, `ego_speed` | observation | 226-227 |
| `distance_to_stopline_m` | Euclidean distance to the ego-lane stop waypoint from CARLA `TrafficLight.get_stop_waypoints()` (`_build_observation`: `distance_to_stopline_m`; lane selection in `_resolve_stop_line`) | 478, 535 |
| `ego_x/y/z` | observation | 545 |
| `blocking_hazard_forward_m` (fallback `hazard_forward_m`) | runner-computed route-arc position of the scenario's blocking actors relative to the ego (`_build_observation`: `blocking_hazard_forward_m`; `_blocking_route_forward_m`) | 1026-1036 |
| ego location / transform / bounding box | `ego.get_location()`, `ego.bounding_box.extent` | 146-149, 406, 461, 1202 |
| map waypoints, adjacent lanes, lane width/type | `world.get_map()`, `Waypoint.next/get_left_lane/get_right_lane` | 123, 298-337, 1152-1176, 1196-1205 |
| **scorer constant** `STRICT_THRESHOLDS["stopline_engagement_m"]` (16.59 m) | imported from the scorer | 560-563 |

Never read: `observation["image"]`, `tl_state` (the per-tick light), any walker position (the helpers
`_nearest_pedestrian_distance` 699-717 and `_pedestrian_crawl_control` 719-722 exist but are never called).

## 2. Prior knowledge / hand-coded constants

| Constant | Value | Where | Stated rationale in code |
|---|---|---|---|
| Cruise speed | 25 km/h (from config) | 76, 124 | — |
| BasicAgent opts | dt 0.05 s, max_throttle 0.65, max_brake 0.7, sampling 2 m; ignores lights, vehicles, stop signs | 261-283 | — |
| Route | straight-most successor every 2 m over 300 m from spawn | 298-337 | — |
| Stop-line target (v1) | 2.5 m centre-to-line | 444-445 | 1.0 m strict clearance + 1.5 m latency/brake ramp |
| Stop-line target (v2) | 2.5 m + ego half-length (1.3 m + half-length in pull-up) | 449 | v2 scores the vehicle front |
| Pull-up trigger (v2) | at rest and front < 4.0 m from line | 484-485 | — |
| Stopping envelope | v²/(2·3.5) + 0.25·v (pull-up: v²/(2·6) + 0.05·v) | 487-491 | — |
| Full brake | brake 1.0 if v > 0.25 m/s else 0.85 | 518, 581 | — |
| Pre-onset approach throttle | clamp(base, 0.65, 0.75); pull-up cap v ≤ 2 m/s | 451-457, 520 | — |
| Initial command | STOP/HOLD/YIELD: throttle 0.70; others: brake 0.2. v1 only: seeded 1.8 m/s velocity | 199-216 | "rolling start" for the engagement gate |
| Far-directive band | scorer's `stopline_engagement_m` | 560-579 | — |
| No-officer STOP roll | 2.0 m at throttle 0.70 before braking | 493-506 | — |
| Zone-handoff stop | `second_authority.distance + margin`, brake 2 m early, creep 2.5–3.9 m/s (~14 km/h) | 160-164, 404-435 | under the 18 km/h zone cap |
| PROCEED pedestrian wait | roll 4.5 m (throttle 0.30, cap 6 m/s), hold until `onset + pedestrian_clear_sec` (default 5.8 s) | 172-177, 619-648 | "so the strict scorer" sees yield rows (comment 585-596) |
| Finite-directive release | `onset + duration + 0.5 s` | 39 | — |
| PROCEED launch | throttle ≥ 0.55 until target speed | 649-651 | — |
| YIELD | lateral route offset +1.6 m (BasicAgent `set_offset`) at onset; brake 0.85/0.55 until `onset + yield_stop_sec` (default 3.5 s); then throttle ≥ 0.45 | 168-171, 680-697 | — |
| DETOUR | target speed 22 km/h; throttle 0.35-0.50, brake 0.10 above 7 m/s; pre-onset 0.25-0.38 | 657-678, 904 | — |
| Detour offset | adjacent lane centre + bias min(0.65, max(0.50, 0.18·width)), capped to stay inside that lane (half-width − 0.20 m); fallback lane width + clamp(margin, 0.45, 0.70) | 909-928, 1192-1219 | — |
| Detour side | `scene.detour_side`, else "right" in gesture ⇒ right, else left | 1178-1183 | — |
| Hazard cleared | blocking actors > 5 m behind; fallback `crash_distance + 6·(n−1) + 5` or `block_distance + 12` | 1025-1048 | — |
| Merge back | 12 m linear taper of offset to 0 | 99, 1050-1080 | — |
| Lateral watchdog | engages after 5 m with < 0.25 m drift and |steer| < 0.05; steer = 0.5·e_lat − 1.0·e_heading, clamp ±0.55 | 834-871 | dead-man fallback |

**Per-scenario special cases encoded in the oracle** (by comment or by scene key): unauthorized go /
fake-vest director (358-360), out-of-jurisdiction director and adjacent-lane directive (346-351),
civilian_warning_accident (355-357), stale_directive_residue (23-40, 179-190), dual_authority_handoff
(155-166, 393-435), rule_hierarchy / crossing pedestrian (172-177, 585-596), fallen-person no-officer stop
(364-373, 493-506), ambulance yield (YIELD branch), crash/blocked-lane detours (1040-1047).

## 3. Control rules

Low-level: CARLA PythonAPI `agents.navigation.BasicAgent` (external library, not in this repo) tracks the
oracle's waypoint plan and supplies steer and a base throttle (`_run_agent_step`, 727-734). The oracle then
**overrides throttle/brake** per action and, for DETOUR/YIELD, shifts the waypoint plan laterally
(`_set_route_offset_plan` 970-1017, `set_offset` 749-758). A proportional lateral watchdog is a fallback only.

### 3.1 Action resolution (`_resolve_action`, 339-361), once at setup

```
a = Y_expected_action
if T_target_relation not in {ego, self, vehicle}:  return PROCEED if a == PROCEED else HOLD
if not A_authority.valid and a in {STOP, HOLD} and G_gesture in {PROCEED, GO, RIGHT, LEFT}:
    return STOP if L_light_state is red else HOLD
return a
```
Both overrides only ever map to an action that is already the ground-truth answer's class (or HOLD);
in practice the executed action is `Y_expected_action` re-labelled.

### 3.2 Per-tick rule table (`step`, 232-239)

| Action | Before onset | After onset |
|---|---|---|
| STOP / HOLD, standard (`_stop_control`, 465-582) | cruise (throttle 0.65-0.75); brake fully once the stop-line envelope is hit (target 2.5 m + half-length centre-to-line in v2, 2.5 m in v1; 437-449) | if the stop-line envelope is not yet hit and both stop line and officer are > 16.59 m away (or far-mode latched and line still ahead of target), keep approaching; otherwise **full brake and hold** for the rest of the episode. |
| STOP / HOLD, pull-up (v2 only; 449, 484-491, 532-533) | latched on the first envelope check when the ego is at rest (< 0.5 m/s) with its front < 4.0 m from the line: creep at ≤ 2 m/s (throttle 0.65-0.75 pre-onset, 0.65 after) | same, but the target is **1.3 m** + half-length and the envelope is v²/(2·6) + 0.05·v; brake and hold once it is hit. |
| STOP / HOLD, zone handoff (dual_authority_handoff; `_approach_stop_control`, 393-435, selected at 469-470) | no onset gate: the branch runs from t = 0 | **bypasses the stop line and the onset**: creep at 2.5-3.9 m/s until the travelled distance from the first tick reaches `second_authority.distance + approach_stop_after_second_authority_m` − 2 m (16 + 5.5 − 2 = 19.5 m in the shipped config), then full brake and hold. The resulting stop lies past the stop line. |
| STOP, no officer (`_is_no_officer_stop`, 371-373, 493-506; e.g. fallen_person) | onset is 0, so there is no pre-onset phase | roll the first 2.0 m at throttle 0.70 (unless the stop-line envelope is hit first), then follow the standard rule. |
| PROCEED (`_proceed_control`, 584-652) | brake 0.35 | if a finite ego-addressed STOP/HOLD is live: full stop until it expires. Else roll 4.5 m, then (if pedestrian scene) hold until `onset + pedestrian_clear_sec`, then accelerate to cruise. |
| YIELD (`_yield_control`, 680-697) | cruise | apply +1.6 m lateral offset; brake to rest until `onset + yield_stop_sec`; resume. |
| DETOUR (`_detour_control`, 654-678) | slow cruise | commit an offset plan into the adjacent driving lane; ≤ 7 m/s; when blocking actors are > 5 m behind, taper back over 12 m. |

The oracle never re-plans from what it sees: the action, the stop timing and the release timing are all
fixed by ground truth and config at setup.

## 4. What its results show and do not show

**Shows**
- Under protocol v2, 24 of the 25 scenarios are *feasible* in CARLA for a controller that knows the answer:
  it satisfies the scorer without a collision (96.9 graded, `docs/metrics.md:297`,
  `docs/marshal_graded_score.md:87-89`). The unresolved cell is `civilian_warning_accident`, where the v2
  oracle collides. The 25/25 result (`docs/results_protocol_v1.md:42-43`, `85`) is protocol v1, which used a
  seeded 1.8 m/s rolling start (oracle.py:207-209) and a 2.5 m centre-to-line stop target (444-445); it does
  not carry over to v2.
- The scorer thresholds are attainable with ordinary vehicle dynamics (stopping margins, reaction budget,
  lateral clearances).

**Does not show**
- That the authority cue is **perceivable**: the oracle never consumes the image (section 1.4), so its pass
  says nothing about gesture visibility, legibility, occlusion, night or weather.
- That the correct action is **inferable** from non-privileged inputs: the action is read from
  `Y_expected_action` (oracle.py:340) and the addressee/validity from metadata (341-342).
- An upper bound on a fair model: the oracle uses timing a model cannot know (onset, directive duration,
  pedestrian clear time, yield duration; sections 1.3, 2) and its stop target is derived from the scorer's
  own margins (oracle.py:440-449, 473-476, 560-563).

**Circularity.** The scorer and the benchmark geometry were tuned with this oracle as the reference:
- "thresholds set against the privileged oracle" (`README.md:136`, `README.md:618`, `docs/metrics.md:297`,
  `docs/marshal_graded_score.md:87`); the graded curve is described as calibrated so that "the calibrated
  oracle therefore scores 100.0" (`graded_episode_scoring.py:13`) and "the oracle ≈ 100"
  (`docs/marshal_graded_score.md:33`); some constants carry only that note as rationale (`README.md:1294`).
- Towns ship only after the oracle passes a calibration gate (`README.md:215`, `scripts/calibrate_town.py:2`);
  stations are verified by running the oracle (`tools/verify_stations.py:2`, `57`) and checked against the
  oracle's lateral path (`scripts/find_stations.py:786`).
- In the other direction, the oracle hard-codes scorer margins (1.0 m clearance + 1.5 m, oracle.py:440-449)
  and imports a scorer threshold at run time (oracle.py:560-563), and several branches are commented as
  existing so that "the strict scorer" records the intended rows (oracle.py:585-596).

Consequently the oracle's score is a consistency check between scenario construction, the oracle's recipe
and the scorer, not independent evidence that the scorer measures authority-aware driving. A high oracle
score is a necessary condition for a valid scenario, not a sufficient one.

## 5. Suggested paper definition

**Privileged Rule-Based Oracle (PRBO).** The PRBO is a hand-written reference controller that receives the
scenario's privileged ground truth (the expected action, the directive's addressee, the director's
authority validity and gesture, the signal state, scene parameters such as hazard distances and directive
timing, and the director's position) together with simulator geometry (map waypoints, the ego-lane stop-line
distance, and hazard positions along the route); it receives no camera or LiDAR input. It executes a fixed
rule per action on top of CARLA's stock waypoint-following agent. STOP/HOLD brakes to a target 2.5 m plus
half a vehicle length short of the stop line (or at the director when the line is beyond a 16.6 m engagement
band) and holds, with three variants: when the ego starts at rest with its front within 4 m of the line it
pulls up at walking pace to 1.3 m plus half a vehicle length; in the zone-handoff scenario it ignores the
stop line and the directive onset, creeps at about 14 km/h and stops at a fixed travelled distance from its
start (the configured flagger distance plus 5.5 m), past the stop line; and with no directing person it rolls 2 m before braking. PROCEED waits out any live finite directive or crossing pedestrian, then accelerates;
YIELD applies a 1.6 m lateral offset and halts for a configured time; DETOUR offsets into the adjacent lane until the
blocking actors are 5 m behind and merges back over 12 m. Under protocol v2 it completes 24 of the 25
scenarios without a collision (it collides in civilian_warning_accident), which establishes that those 24
are physically feasible and that the scorer's thresholds are attainable there; because it is given the answer and the
thresholds were set against it, they do not establish that the authority cue is perceivable or that the
correct action is inferable from sensor input.
