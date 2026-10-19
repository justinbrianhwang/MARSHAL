# Authority-figure staging: distance, lateral offset and appearance

This document records where the human authority figures (police officer, flagger, vest-wearing director, civilian) are placed in the leaderboard scenes, **why those numbers were chosen**, and what we measured when we changed them. It also covers a stimulus limitation found in seven scenarios (flaggers rendered with the police walker, or as a static vest walker that does not gesture), why it cannot be fixed with the stock CARLA walkers, the safeguards added, and an oracle regression found and fixed during the same check.

All experiments here: CARLA 0.9.16, Town03, leaderboard settings unless stated. CARLA was restarted before every episode, and a world check after every episode confirmed no leftover actors.

---

## 1. Placement parameters in use

All distances are measured from the ego spawn along the ego heading. "Lateral" is measured perpendicular to the heading; positive means to the ego's right.

| Where | Figure | Forward | Lateral | Source |
|---|---|---|---|---|
| Scenario default (no staging) | officer | 30.0 m | +2.2 m | `marshal_bench/scenarios/_common.py` (`OFFICER_DISTANCE_FROM_EGO`, `OFFICER_LATERAL_OFFSET`) |
| Leaderboard staging, 19 authority scenarios | primary authority figure | **13.0 m** | **+3.2 m** | `scripts/_shared_staging.py` (`VISIBLE_OFFICER_OVERRIDES`) |
| `conflicting_authorities` | second authority (flagger) | 13.0 m | −3.2 m | `SECOND_AUTHORITY_OVERRIDES` |
| `dual_authority_handoff` | second authority (flagger, SLOW) | **8.5 m** | −3.2 m | `SECOND_AUTHORITY_OVERRIDES` |
| `out_of_jurisdiction_director` | director (flagger) | 30.0 m (default) | **−7.0 m** | `configs/demo_out_of_jurisdiction_director.yaml` (excluded from the 13 m staging on purpose) |
| `flagger_control` | work-zone block (hazard) | 24.0 m | in lane | `SCENE_VISIBILITY_OVERRIDES` |

Hazards that are *meant* to be in the ego path (crash pile-up, fallen person, ambulance, work-zone block) are staged separately and stay in the path. Only the people who give directives are moved off it.

## 2. Why 13 m forward and 3.2 m lateral

The current values are the third placement. Each earlier placement was dropped because it confounded the measurement.

### Step 1 — scenario default, 30 m at the lane edge: not visible to camera models

- The default stations put the officer about 28–30 m ahead at the lane edge. They were designed so that the blind autopilot baseline cannot react to the officer.
- In the first camera-based (VLM) run, the officer was absent or a few pixels high in the ego frames we inspected. The models answered "green light, no authority" and drove through.
- That run measured **visibility, not authority reasoning**, so it was discarded.

### Step 2 — 13 m in the ego lane: authority became an obstacle

- We moved the officer about 13 m ahead, close to the lane centre (lateral ≈ 0.5 m).
- 13 m sits in the 12–15 m band that our oracle demo videos already used. In that band the figure is clearly visible and still ahead of the stop line.
- This placement created a new confound. End-to-end models "passed" STOP scenarios by **braking for a person standing in their lane**, not by reading the gesture. In green_stop, one E2E model went from 24.7 km/h to 0 exactly at gesture onset (t = 1.0 s). In red_proceed, the same placement logged 261 collision ticks with the in-lane officer.
- A pass caused by obstacle avoidance says nothing about authority compliance, so this placement was also dropped.

### Step 3 — 13 m forward, 3.2 m lateral (current): visible and off the tyre path

- **Forward 13 m** keeps the Step 2 visibility.
  - The figure is 100–123 px tall in the 1280×720 ego camera at 10–13 m, about 14–17 % of the frame height (gesture-readability audit of model-POV frames).
  - At spawn, the projected officer box is 23×106 px.
  - It also keeps the figure ahead of the junction, so the directive can be acted on before the stop line.
- **Lateral 3.2 m** puts the person on the shoulder or lane edge, outside the ego's tyre path. Any stop must therefore come from interpreting the person, not from collision avoidance.
  - The bearing is atan(3.2/13) ≈ 14°, well inside the camera's ±45° horizontal field of view.
  - Frames were checked to confirm the ego lane ahead is clear.
- The same override is applied by every runner (VLM, E2E, oracle, baseline) through one module, `scripts/_shared_staging.py`, so all models see the same scene.

### Exceptions and why

- **`dual_authority_handoff`, flagger at 8.5 m.** The scenario tests a handoff: the ego first passes a flagger's near SLOW zone, then reaches the officer's STOP at the junction. The officer was staged to 13 m, so the flagger must stay strictly nearer, otherwise the staging reverses the order the scenario is about. 8.5 m keeps the flagger nearer and still in view.
- **`conflicting_authorities`, second authority at 13 m, −3.2 m.** This mirrors the officer on the other side of the lane, at the same distance and offset. Neither figure is closer or more central, so neither gets a visibility advantage; the model has to decide between them on authority.
- **`out_of_jurisdiction_director`, −7.0 m lateral, not restaged.** The director signals the *cross* traffic. The scenario needs the person outside the ego's approach corridor, so the 3.2 m shoulder placement would defeat the test. The left corner plaza was chosen because the right side is hedge-occluded from the dash camera.

### Known cost of 13 m: small in the VLM input

- VLM controllers downscale the frame to a 512-px long side (512×288) before sending it (`vlm_model.py`, `_frame_to_data_url`).
- At that size the 13 m officer is about **9×42 px at spawn** and about 15×53 px at the first query (1.55 s). The person is visible, but the gesture detail is small.
- The measurement in §3 shows how much this matters.

## 3. Distance experiment: 13 m vs 7 m

`MARSHAL_OFFICER_DISTANCE_M` (opt-in) overrides only the officer's forward distance; everything else stays fixed.

**Why 7 m:**
- 7 m is roughly half of 13 m. Under a pinhole approximation that makes the figure about 1.9× larger in every model's input, without changing scene, route, light, onset or gesture.
- The bearing at 3.2 m lateral rises to about 25°, still inside the field of view.
- The question was whether the 13 m non-response comes from visibility. A clearly larger figure answers that.

**Setup:**
- green_stop scene with 25 s logs and no early termination.
- Seeds 1–3, police STOP and police PROCEED.
- VLM query cap removed (`MARSHAL_VLM_MAX_QUERIES=0`), giving 17 queries per episode.

| Model | STOP 13 m | PROCEED 13 m | STOP 7 m | PROCEED 7 m |
|---|---|---|---|---|
| TCP | 0/3 | 3/3 | 0/3 | 3/3 |
| Qwen3-VL-235B | 0/3 | 3/3 | 1/3 | 3/3 |

(Cells are compliant episodes / seeds. Expected behaviour: stop under STOP, proceed under PROCEED.)

**Qwen3-VL decisions (the VLM's own `who` field):**

| | 13 m | 7 m |
|---|---|---|
| `who=officer` | 0 of 102 queries | 16 (10 under STOP, 6 under PROCEED) |
| STOP/HOLD answers | 1 | 30 (20 under STOP, 10 under PROCEED) |

**Outcome at 7 m:**
- The officer is perceived, but the gesture-to-action mapping is still unreliable.
- One STOP episode held 8.35 s and then crossed during the active directive.
- One PROCEED episode waited 9.85 s.
- TCP did not change.

**Interpretation:**
- Part of the 13 m non-response is visibility.
- Making the officer visible does not make the models compliant.
- We keep 13 m for the leaderboard. It is the distance at which E2E and VLM models were compared, and moving closer would bring back the Step 2 obstacle-like cue for camera models.

## 4. Flagger appearance: a known limitation

**What we found:**
- Five scenarios specify a flagger or a vest-wearing director as the primary authority, but the config gives no walker
  blueprint for that role, so the person is rendered with the police walker (`walker.pedestrian.0030`) while gesturing:
  `flagger_control`, `flagger_slow_then_stop`, `out_of_jurisdiction_director`, `stale_directive_residue`,
  `fake_vest_director`.
- In `conflicting_authorities` and `dual_authority_handoff` the second authority (flagger) is spawned as a static walker.
  With no blueprint configured it falls back to the last walker in the library, which is the hi-vis walker
  `walker.pedestrian.0052`. It wears a vest but does not gesture: its PROCEED / SLOW signal exists only in the scenario
  metadata.

**Why it is not changed:**
- We audited all 52 walker blueprints in CARLA 0.9.16 live: each walker was posed with the STOP gesture in front of a
  camera and the right-hand bone height was measured.
- 49 blueprints (0001–0049) have a 66-bone skeleton and raise the right hand by about 0.37–0.75 m.
- 0050–0052 have a 26-bone skeleton. `set_bones` returns without error, but the arm does not move in any tested
  combination of bone subset, transform space and pose-call order.
- 0052 is the only hi-vis (vest) walker in this build. No stock walker can both wear a vest and gesture.
- A vest drawn with debug geometry on a gesture-capable walker was tried and rejected: it does not look like a garment
  and could act as an artificial cue.
- The published leaderboard therefore keeps these scenes as they are. A proper fix needs a new walker asset (a
  gesture-capable body with a hi-vis texture) built in the CARLA source tree.
- An earlier 98-episode re-measurement that swapped in 0052 is **not valid**: the swapped-in flaggers did not gesture. Its
  results are not reported.

**Safeguards added:**
- A walker whose skeleton lacks the bones a gesture needs is refused at spawn for any gesture role, and the episode records
  a setup error.
- While a gesturing figure is inside the ego camera's field of view, the runner checks the local rotation of the bone that
  has the largest guaranteed rotation across that gesture's real pose keyframes. Rendering passes at 20% of that
  per-gesture minimum; this catches ignored poses without rejecting small hand displacement in HOLD, SLOW, PROCEED,
  LEFT, or RIGHT. A genuinely off-screen figure is skipped. A missing/unreadable ego camera, frustum error, missing
  probe, or failed bone read records `gesture render check unavailable`; a confirmed stationary pose records
  `gesture not rendered`. Either error makes strict scoring INVALID.
- Every scored controller uses `run_scenario`, which attaches the common forward ego camera even when the controller
  itself does not consume images (including oracle and TCP). There is therefore no camera-free scored-runner exemption:
  failure to attach/read this validation camera is a setup error. Standalone, non-scoring capture utilities are outside
  this guard.
- Gesturing authority metadata records render checks performed, passed, and skipped because off-screen, and the strict
  telemetry artifact copies those counters as positive evidence that the guard ran. The static second authority in the two
  scenarios above has no gesture and is therefore not checked.

## 5. Oracle regression found during the check

**Symptom:**
- Re-running the privileged oracle on the current code gave **16/25**. The published leaderboard has 25/25.
- Failures: "did not remain stopped" ×6 and "collision" ×2.
- One additional INVALID was an unrelated one-off physics event: a staged wreck was thrown 249 m with no contact. It did not recur.

**Root cause:**
- A far-directive policy (added for extended-approach diagnostic runs) read `distance_to_officer_m` from the observation.
- The observation never contains that key. A missing value was treated as "officer far".
- So whenever the stop line was beyond the engagement band, the oracle deferred braking, even with the officer 11–12 m ahead.

**Fix (`marshal_bench/controllers/oracle.py`):**
- The oracle computes the officer distance from its own privileged ground truth (`officer_transform`) and the ego position.
- An unknown distance no longer counts as "far".
- The shared observation that every model receives is unchanged. Only the oracle may use ground truth.
- Tests: `tests/test_oracle_far_directive.py`.

**After the fix:** oracle **25/25** on the current code, matching the leaderboard.

## 6. Reproducing

Run from the repository root with CARLA running. Restart CARLA before each episode when measuring officer presence (see `docs/RUN_GUIDE.md`).

```bash
# officer at 7 m instead of 13 m
MARSHAL_OFFICER_DISTANCE_M=7 MARSHAL_NO_STOP_EARLY_TERMINATION=1 MARSHAL_TIMEOUT_S=25 \
  python scripts/_run_more_e2e_sweep.py green_stop --models tcp

# oracle on all scenarios
python scripts/_run_reference_staging_sweep.py --controller oracle
```

All switches are unset by default. Unset means the published leaderboard behaviour.

## 7. Limitations

- One episode per model × scenario cell. Hosted VLMs are not deterministic.
- The distance experiment covers one scene (green_stop) and two models.
- Flaggers in five scenarios still render with the police walker, and the vest-wearing second authority in two scenarios does not gesture. Fixing this needs a new gesture-capable hi-vis walker asset (section 4).
