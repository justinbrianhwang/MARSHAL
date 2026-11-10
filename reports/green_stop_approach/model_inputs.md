# Model Input and Architecture Matrix — GREEN STOP `gs_approach_c1`

**What this records**
- What each driving system **actually used** in the gs_approach_c1 runs, not what its original paper describes.
- The runs used the code in this repository (`marshal_bench/`, with the GREEN STOP approach protocol); the run batch stores SHA-256 hashes of the 12 protocol-related source files (`tmp/_greenstop_cand/full/code/sha256.txt`), which match this repository.
- Actual use is evidenced by the run config (`metadata.json` per-controller block), `events.json` (`*_setup`, `vlm_decision`, `openemma_planner_query`), child logs and `*_trace.csv`.
- `metadata.json` holds the pre-protocol scene config (spawn and `onset_time 1.0`). The protocol overrides recorded in the `green_stop_protocol` event replaced those values at run time.

The full per-system table, with an evidence column, is in `data/model_input_architecture_matrix.csv` and sheet 01 of `failure_analysis.xlsx`.

## Summary

| System | Architecture (as run) | Officer can reach it via | Traffic light given as | Decision / output | Update | Privileged |
|---|---|---|---|---|---|---|
| Qwen2.5-VL, Qwen3-VL, GLM-4.5V | Hosted VLM decides longitudinal action; BasicAgent cruise + steering; fixed action→brake mapping | Dashcam pixels only (512×288 JPEG) | **Text field in prompt** ("Green") + image | `{action, who, reason}` | 1 query / 1.5 sim-s (sim waits) | No |
| OpenEMMA | Local Qwen2-VL-7B 4-stage CoT planner → pure pursuit + P speed | Own front camera pixels (800×600) | Image only | scene / objects / intent text + 6 speed–curvature pairs | Planner / 3.0 sim-s | No |
| NEAT | Learned E2E → waypoints → built-in PID | 3 cameras (400×300, crop 256) | Image only | waypoints (+ red-light head) | every tick | No |
| CILRS | Learned E2E direct control | 1 camera | Image only | steer / throttle / brake | every tick | No |
| TransFuser | Learned camera + LiDAR fusion (3-model ensemble) → PID + LiDAR safety box | 3 cameras (960×480) + unlabeled LiDAR | Image only | waypoints | control every 0.2 s | No |
| InterFuser | Learned transformer + native safety controller | 3 cameras + LiDAR; object map has no officer/gesture class | Image only (light head) | waypoints, object map, light / stop-sign / junction heads | every tick | No |
| TCP | Learned E2E (waypoint branch) → PID → **adapter post-processing** (throttle ≤ 0.05 above 1.5 m/s) | 3 cameras (1600×900, JPEG q20) | Image only | waypoints | every tick | No |
| PID, MPC | Rule-based lane follow | **None** (no camera used) | Not used | steer / throttle / brake | every tick | No |
| Light-only baseline | Rule-based; obeys the light, blind to humans | **None** | **Symbolic** `tl_state` + stop-line distance | throttle / brake | every tick | Receives the GT tuple at setup (track "A"), never reads it |
| *Scripted reference (not evaluated)* | PID + scripted brake | **Privileged officer metadata** (gesture, onset, duration) | Not used | throttle / brake | every tick | **Yes, by design** |

AIM is in the full table. It never reached the trigger point (NOT_SCORED).

## Could the officer and the STOP gesture reach each system?

1. **No evaluated system received symbolic officer, gesture or expected-action information.**
   - Every controller is constructed with the full scene config (`_common.py:1782`), but no non-reference controller reads the `officer` or `expected_behavior` keys. This was checked by grep over every controller file.
   - The per-tick observation has no officer field (`_common.py:1655-1674`).
   - Non-privileged setup gets only target speed, map and light state (`_common.py:1523-1529`).
   - In every VLM and OpenEMMA query, `assist` was empty and the ablation was `none`.
2. **Pixels:**
   - The three VLMs, OpenEMMA, NEAT, CILRS, TransFuser, InterFuser and TCP could see the officer.
   - TransFuser and InterFuser also receive LiDAR points, which carry geometry without labels.
3. **No input channel at all:** PID, MPC and the light-only baseline. Their failure is by design (lower bounds).
4. **What the pixels showed:**
   - The configured STOP paddle (`static.prop.stopquad`) **was never rendered**. The blueprint was not found: all STOP-episode `officer_spawned` events have `hand_prop_blueprint_id = null`, and the child logs carry the warning.
   - STOP was therefore signalled only by the arm pose (right forearm raised, hand at head height).
   - At the VLMs' first post-onset query, the officer is about 25 px tall in the 512-px input.
5. **Input frames available for audit:**
   - The VLMs' input stream is recoverable: `frames_ego/<input_frame>.png` is the pre-resize source of each query image.
   - For every other camera model, only 4 debug frames were saved, all before onset. Whether those models' own cameras showed the officer legibly after onset is **UNKNOWN**.

## Code support versus actual use

| Feature | In the code | In these runs |
|---|---|---|
| VLM/OpenEMMA ablation ladder and shadow oracle | Present | Off (`none`) |
| OpenEMMA GPT backend | Present | Not used; `qwen2vl` ran |
| InterFuser `carla_default` sensor profile, 3-D speed input | Present | Not used; `leaderboard` profile and forward speed ran |
| TCP post-processing | Can be switched off via `MARSHAL_TCP_POSTPROCESS=0` | **On** (env empty) |
| VLM input-image saving (`MARSHAL_VLM_SAVE_INPUTS`) | Present | Off; no `vlm_inputs/` directory |
| VLM request `seed` | Forwarded if `MARSHAL_SEED` is set | Not recoverable from the run artefacts → UNKNOWN |

## UNKNOWN items

- **Hosted VLMs:** serving provider, revision, quantization and sampling defaults.
- **TransFuser:** whether HybridAgent's own checkpoint load is strict. MARSHAL's check reports 1 unexpected key per checkpoint.
- **Traffic-light actor id:** InterFuser logged "Pinned traffic light id=66"; the other end-to-end batches logged 69. These are actor ids from separate CARLA sessions; whether it is the same physical light was not checked.
- **Legibility:** post-onset legibility of the officer in each non-VLM model's own camera.
