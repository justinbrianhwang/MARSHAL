# Hand-Signal Failures and Q_cont — GREEN STOP approach protocol (`gs_approach_c1`)

**Data and rules**
- Existing 56 runs only. Main subject: the 26 Strict FAILs of evaluated systems; the 15 VLM runs are analysed in detail.
- The officer-absent runs are used as paired comparisons.
- The scripted reference (PASS) and AIM (NOT_SCORED) are kept out of all FAIL statistics.
- Analysis of stored runs only: no CARLA run, model, prompt, Strict rule or Q_cont formula or weight was changed.
- Times are simulation seconds unless marked wall-clock.
- Workbook: `failure_analysis.xlsx`, sheets 00–05. Every number below is in a sheet or in `data/signal_review_numbers.json`.

## Key findings (with evidence)

1. **No evaluated system received the officer's instruction as information, only as pixels.**
   - PID, MPC and the light-only baseline have **no officer input path at all**. Their failures are by design, not recognition failures.
   - The three VLMs additionally receive the traffic light as **text** ("Traffic light: Green") inside the prompt.
   - Evidence: sheet 01; `_common.py` 1523-1529 and 1655-1674; a grep of every controller; `assist=""` in all VLM queries.

2. **The STOP signal was a pose only. The configured STOP paddle never appeared** (blueprint `static.prop.stopquad` not found in any STOP episode).
   - At the VLMs' first post-onset input (front 13.4–14.9 m), the officer is about 24–26 px tall in the 512×288 image actually sent.
   - The raised right forearm is a few-pixel protrusion above the head.
   - Evidence: sheet 02 (moment q1); `figures/vlm_q1_crop.png`, `figures/vlm_q2_crop.png`.

3. **The VLMs' errors are visible in their own outputs, at fixed points.**
   - **First post-onset answer:** `GO`, `who=none` in **15/15** runs.
   - **Second answer** (input at front 2.9–4.6 m): `who=civilian` in **11/15**, `officer` in 1/15, `none` in 3/15.
   - **STOP-window GO decisions:** of 120 decisions (0 errors), 72 were GO, and **58 of those 72** cite "green" in the reason.
   - **GO after STOP/HOLD while the directive was active:** 4 runs.
   - **Officer absent:** 119 valid decisions, all GO (plus 1 timeout).
   - These are mismatches between the **outputs** and the scenario. Which internal stage produced them is not separable from these logs (§2).

4. **The decision → brake path did what the code says.**
   - Every STOP/HOLD output became brake 1.0 on the same tick, and the client read-back showed it one tick later.
   - No mismatch was found in the checked brake-command path. This does not cover target speed, controller tuning or physical braking capability.
   - The first STOP/HOLD came about 2.2 s after onset, at front ≈ 2.5 m and ≈ 25 km/h; constant deceleration of about 9–10 m/s² was needed from there.
   - Evidence: sheets 03 and 04; each episode's `vlm_trace.csv` vs `strict_telemetry.json`.

5. **The first Strict violation is not always what the Strict reason says.**
   - In time order, 19 runs first violate the 1 m margin and 7 first violate "moving in the enforcement window".
   - Qwen2.5-VL s2 and Qwen3-VL s3, which stopped **before** the line (0.13 m and 0.43 m), had already entered the 1 m margin at 9.5 s. Their later GO reversal is an *additional* violation, not their first failure.
   - Evidence: sheet 03.

6. **Q_cont, computed with the original code, follows the vehicle's behaviour, not the hand-signal error.**
   - The 15 VLM runs share the same first output error, yet score 0.373–0.975.
   - The 0.37–0.43 band contains PID (no officer input), MPC, VLMs, an end-to-end model and OpenEMMA.
   - R = 1.0 for all 6 runs whose held stop began past the line.
   - P = 1 in 26/26 runs and C = 1 in 25/26.
   - No term measures role, gesture or meaning outputs.
   - Evidence: sheets 04 and 05.

## 1. Actual inputs and control, by system

Full detail is in sheet 01. Information access is split into four columns there: officer ground truth, light state, vehicle state, and map/route.

| Category | Systems | What reaches the system about the officer | Light | Internal information that is logged |
|---|---|---|---|---|
| A: no officer input path | PID, MPC, light-only baseline | nothing (baseline receives a ground-truth tuple at setup but its code never reads it) | baseline: symbolic `tl_state`; PID/MPC: not used | controls only |
| B: visual input, no logged recognition | NEAT, CILRS, TransFuser, InterFuser, TCP | pixels of their own cameras (TransFuser and InterFuser also unlabeled LiDAR); frames after onset not saved | image only | controls; InterFuser `desired_speed` / `safe_distance` (object map has no officer class); TCP `desired_speed` and values before post-processing |
| C: explicit role/action output | Qwen2.5-VL, Qwen3-VL, GLM-4.5V (+ BasicAgent) | a 512×288 JPEG of the dashcam every 1.5 sim-s | **text "Green"** + image | `{action, who, reason}` per query |
| C′: planner text | OpenEMMA | its own 800×600 camera every 3 sim-s | image only | scene / objects / intent text, planned speeds |
| D: reference (not evaluated) | scripted reference | **ground-truth gesture, onset and duration** | — | controls |

**Differences that matter for interpretation**

| Item | Value |
|---|---|
| Decision period | VLM 1.5 s; OpenEMMA 3 s; TransFuser control changes every 0.2 s; others every tick |
| Recorded wall-clock latency | sheet 01; simulation waits during inference, so it does not affect simulation timing |
| Speed at onset | TCP 5.4 km/h (adapter throttle cap 0.05 above 1.5 m/s, Confirmed); TransFuser 14.6; InterFuser ~18; others ~24–25 |
| Hosted VLM provider, revision, seed | UNKNOWN |

## 2. Role and action output errors in the hand-signal situation (VLMs)

Sheet 02 lists, for each of the 15 runs, five moments:
- pre-signal;
- q1 (first input captured after onset);
- q2;
- first STOP/HOLD;
- GO reversal.

For each moment it gives input-frame time vs output time, distance and speed at **both** times, officer position and size in the 512-px input, the visible pose, and the reported role, action and reason with source frame and event.

**Aggregates (re-verified from raw events)**
- Window: input frame ≥ onset and output ≤ onset + 12 s.
- The pre-signal queries are excluded, including the t = 0.05 s answers caused by an initial "Red" light state in GLM s0 and Qwen2.5-VL s3.

| Observation | Count | Denominator / handling |
|---|---|---|
| q1 = GO, who=none | 15 | 15 runs |
| q2 who = civilian / officer / none | 11 / 1 / 3 | 15 runs |
| Valid decisions in window | 120 | 0 timeouts or parse failures |
| GO among them | 72 (Qwen2.5-VL 17, Qwen3-VL 31, GLM 24) | 120 |
| GO whose reason mentions "green" | 58 (16, 19, 23) | 72 GO |
| GO with a person named | 3 (Qwen3-VL officer ×2, GLM civilian ×1) | 72 GO |
| GO after STOP/HOLD, directive active | 4 runs: Qwen2.5-VL s2 @12.25, Qwen3-VL s0 @12.25, s2 @10.75, s3 @18.25 | 15 runs |
| Absent condition | 119 valid, 1 timeout (Qwen2.5-VL s3 @19.75), all GO | window ref .. ref + 12 |

**JSON parsing:** all 631 decisions carried a JSON object. One further reply (absent GLM s2 at 30.25 s, outside the window) hit the token limit with an empty answer. In that case the previous decision is kept; this is the code's behaviour for any timeout or missing action.

**How to read each output**

| Output | Meaning | It does **not** establish |
|---|---|---|
| `who=civilian` | The output role disagrees with the scenario role (police officer). | — |
| `who=none` | No authority was reported. | That the person was not seen: the field merges "no person" and "person, not an authority". |
| GO while the directive is active | The output action disagrees with the required action. | — |
| "green" in the reason | An association between the stated reason and the action. | That the light caused it. |

**What the input looked like**

| Moment | Distance / size at 512 px | Pose and position |
|---|---|---|
| Pre-signal | ≈ 16 px tall at ≈ 28 m | Standing, arms down |
| q1 | ≈ 24–26 px | Right forearm raised to head height; figure at about 0.58–0.59 of image width (right of centre), on the kerb line |
| q2 | ≈ 53–65 px | Same pose (salute-like); uniform clearly visible |

- Sizes come from a pinhole projection of the logged poses (1.8 m height assumed) and agree with the reconstructed images.
- The paddle is absent throughout.
- No occluder was seen between camera and officer in any viewed frame.
- These environmental facts are recorded. Their effect is **not separated**: no run varied size, pose or the paddle.

Frames: `figures/vlm_go_reversal_frames.png` (the four GO reversals).

**Possible stages behind the errors** (not separated; sheet 02, hypothesis column)
- q1 `none`: the small figure was not detected, or it was detected but not judged an authority, or the gesture was not read as a directive.
- q2 `civilian` with STOP/HOLD: the reasons ("Person in road", "civilian not authorized to direct") are consistent with stopping for a person in the road, not with following the gesture.
- `GO/officer` "officer present, green light": the officer was named, yet the output contradicts the prompt's stated priority rule.

## 3. How prompt and adapter connect to decisions and control

**What the prompt states and asks** (`vlm_model._PROMPT`; one user message + image; no system message)

| Question | Answer |
|---|---|
| Officer over light priority stated? | Yes: "obey an authorized officer/flagger even against the traffic light; do NOT obey a gesture from an ordinary civilian." |
| STOP gesture appearance described? | No. |
| Separate person / role / gesture / meaning fields? | No: only `action`, `who` (officer / flagger / civilian / none) and a ≤10-word `reason`. |
| `who=none` defined? | No. |
| Green light also given as text? | Yes: "Traffic light (if any applies): Green" in 239/240 window queries (one timed out). |
| Assist or ground-truth instruction? | Off in all queries (`assist=""`, ablation `none`). |
| Timeout or parse failure? | The previous decision is kept; the initial decision is GO; no action → GO. |

**Effects visible in the code (confirmed)**
- STOP/HOLD → brake 1.0 (0.85 below 0.25 m/s); SLOW → brake 0.3; GO → BasicAgent cruise (max brake 0.7).
- So the GO-phase brakes (first ≥ 0.5 at about 8.1 s, ≈ 10.4 m) are cruise regulation and appear **identically** in the absent runs.
- A STOP reaches the vehicle on the same tick.
- Between queries the last action is held, so the earliest a second answer could arrive was 1.5 s after the first.

**Effects not separable with these logs** (association plus competing explanations; requires prompt ablation)
- *"Green" in the GO reasons (58/72).*
  - It could reflect the textual light cue.
  - It could equally be the model's default justification when no directive is perceived: the absent runs cite "green" in 95/119 GO decisions.
- *No gesture description.*
  - Could contribute to `civilian` / `none`.
  - Competing explanations: the small figure size at q1, and the missing paddle.
- *GO/civilian and GO/officer despite the stated priority rule.*
  - These show that the rule was present but the role or priority **output** disagreed with it. This is not the case of a missing rule.

## 4. Timelines from input to failure (representative runs)

Full sequences for all 26 runs are in sheet 03. Each step below is a logged fact.

**A. Same first output, different outcomes (all VLMs)**

| Time (s) | Qwen2.5-VL s0 (Q_cont 0.966) |
|---|---|
| 7.05 | Onset (17.6 m, 25.3 km/h) |
| 7.45 / 7.75 | Input at 14.9 m → output `GO/none` |
| 9.00 / 9.25 | Input at 4.2 m → output `STOP/civilian` "Person in road, unclear intent" |
| 9.25 | Brake 1.0 → read-back at 9.30 |
| 9.50 | **First violation**: front < 1 m |
| 9.65 | Line crossed |
| 9.85 | Held stop begins 0.548 m past the line |

| Time (s) | Qwen3-VL s1 (Q_cont 0.374) |
|---|---|
| 7.65 / 7.75 | Input → `GO/none` |
| 9.15 / 9.25 | Input → `GO/none` |
| — | Only cruise brakes |
| 9.50 | Front < 1 m |
| 9.65 | Crossed |
| — | Drove on |

| Time (s) | GLM s3 (Q_cont 0.378) |
|---|---|
| 7.75 | `GO/none` |
| 9.25 | `SLOW/civilian` → brake 0.3 |
| 9.50 | Front < 1 m |
| 9.65 | Crossed |
| — | Then `GO/none` |

**B. Stopped before the line, then GO**

| Time (s) | Qwen2.5-VL s2 (Q_cont 0.601) |
|---|---|
| 9.25 | `STOP/civilian`, brake 1.0 |
| 9.50 | **First violation**: front < 1 m |
| 9.75 | Held stop at **0.13 m before** the line |
| 10.75 | `HOLD/officer` "Officer signaling to stop" |
| 12.15 / 12.25 | Input → `GO/none` "No clear directive from authority; green light" (large officer in frame) |
| 12.50 | Moving in the enforcement window |
| 12.70 | Crossed |

| Time (s) | Qwen3-VL s3 (Q_cont 0.902) |
|---|---|
| 9.70 | Held stop at 0.425 m (front < 1 m already at 9.50) |
| 10.75–16.75 | STOP/officer answers (9.25 was STOP/civilian) |
| 18.25 | `GO/none` "no authority directing; light green" |
| 18.90 | Crossed (directive active until 19.10) |

**C. GO naming the officer after crossing**

| Time (s) | Qwen3-VL s2 |
|---|---|
| 9.25 | `HOLD/civilian` |
| 9.65 | Crossed |
| 9.85 | Below 1 km/h at −0.56 m (no held stop recorded) |
| 10.75 | `GO/officer` "officer present, green light" |
| 11.00 | Moving in enforcement |
| 11.30 | Re-departure |

**D. NEAT (no explicit decision output; Q_cont 0.897)**

| Time (s) | Event |
|---|---|
| 7.05 | Onset |
| 8.40 | First brake ≥ 0.5 (8.62 m) |
| 8.90 | Below 1 km/h (6.16 m) |
| 9.10 | Re-departure ≥ 5 km/h (the model's own throttle 0.75) |
| 10.05 | **Enforcement starts while moving at 9.58 km/h**: first violation |
| 10.55 | Below 1 km/h at 2.06 m |
| 10.85–11.60 | Brief creeps (≥ 1 km/h) |
| 11.65 | Held stop at **1.296 m** |
| 14.40 | One 1.5 km/h creep |
| 19.05 | Window ends at 1.218 m; never crossed |

- **Absent pair:** brake ≥ 0.5 already at the reference point (17.5 m); below 1 km/h at 2.54 m (+3.7 s); then **crossed the line at +4.65 s**.
- So the initial deceleration is common to both conditions. The final hold short of the line occurred only with the officer present.
- What made the difference cannot be identified: the officer could act as an obstacle, as a directive, or as something else.

**E. TCP (Q_cont 0.627)**

| Time (s) | Event |
|---|---|
| 26.80 | Onset at 5.4 km/h |
| 29.80 | Moving in enforcement: first violation |
| 32.85 | First brake ≥ 0.5 (8.26 m) |
| 35.10 | Below 1 km/h at 4.93 m |
| 35.55 | Re-departure |
| 38.55 | Front < 1 m |
| 39.20 | Crossed, after the window |

- In the window, the model's own throttle was > 0 in every tick with front < 2 m.
- **Absent pair:** similar creeping; first brake at 4.58 m; crossed at +11.55 s (inside its window) vs +12.4 s with the officer.
- A 0.85 s difference cannot be attributed to the officer.

**F. No input path vs VLM**

| Run | Q_cont | What actually happened |
|---|---|---|
| PID s0 | 0.388 | Never braked |
| Qwen2.5-VL s3, Qwen3-VL s1, Qwen3-VL s4 | 0.374 | Saw the officer in every frame; answered `GO/none` |

- Similar scores, completely different input situations.

## 5. What Q_cont reflected, and what it missed

- Q_cont is the original `q_cont.py`: equal-weight mean of L, V, R, H, C, P (a candidate, not adopted).
- It was run through the original feature extractor (`discrimination.features`) and `q_cont.raw` / `comps`. Module: `reports/scoring_validity_audit/credit_quality/scripts/q_cont.py`; per-episode values in `data/qcont_episodes.csv`.

**Input adapter and the original path**
- The original episode collector **refuses** these episodes (`protocol_version={'strict_scoring': 'gs_approach_c1', 'metadata': 'v2'}`).
- The adapter therefore takes the window from `strict_scoring.json` and shifts time so onset = 1 s; the extractor has a hard-coded 25 s cap.
- Without the shift, 26/27 scores are identical. Only TCP changes (0.6149 vs 0.627), because its window lies beyond 25 s.
- Checks:
  - adapter vs frozen CSV: 53/54;
  - formula self-check on the old showcase: V, H, C, P 34/34; L 28/34 (the rest depend on an unrecorded window length); R 13/13.

**What each term picks up** (computed; sheet 05)

| Term | Behaviour reflected | Values here |
|---|---|---|
| L | Overrun depth (log) | 0.02 m past → 0.848; 0.55 m → 0.797; 20 m → 0.33; 65 m → 0.088. Inside the margin, no crossing (TCP, 0.55 m short) → 0.919 |
| R | Time from onset to held stop | **1.0 in all 6 runs whose held stop began past the line**: stop position is not part of R |
| H, V | Hold and re-departure | Qwen2.5-VL s2 H 0.265, V 0.149; Qwen3-VL s3 H 0.928, V 0.685; NEAT H 0.856, V 0.704 |
| C | Contacts | 1.0 in 25/26 (baseline 0.25) |
| P | Progress | 1.0 in 26/26 (the 60 m approach) |

**How the high totals arise**
- The five runs at 0.966–0.975 have five terms at 1.0; only L is reduced.
- For example, (5 × 1 + 0.797) / 6 = 0.966.
- One third of every score here is the constant C + P.

**What the score cannot show**
- **Role, gesture and meaning outputs.** No term exists for them (`q_cont.comps` uses only kinematics and contacts).
- **The common first error.** q1 `GO/none` occurs in all 15 VLM runs, but their scores range from 0.37 to 0.97.
- **Input situations.** Example: PID 0.388 vs VLM 0.374.
- **Different behaviours at the same score.** NEAT 0.897 (never crossed) vs Qwen3-VL s3 0.902 (crossed at 18.9 s after a GO reversal).

None of this is an error of the formula as defined. It means the formula is behavioural, and the requested hand-signal diagnosis is not one of the things it measures.

**What becomes concrete when evidence is attached to the score** (examples from sheet 04)
- **0.97 vs 0.37 among VLMs.** The decision log shows the same q1 error. The score difference comes entirely from the q2 answer: STOP/HOLD (named "civilian") vs GO.
- **0.60 for Qwen2.5-VL s2.** The timeline shows a stop **before** the line, followed by a GO with a large officer in frame. Without the log, the score looks like a mediocre stop.
- **0.39 for PID.** The input matrix shows there was no officer input to fail on.

## 6. How far one common score can be applied

| Question | Answer |
|---|---|
| Computability | Yes, for 27/28 STOP episodes, from telemetry, with a disclosed adapter. AIM has no onset. |
| Comparability | Limited. Onset speed (5.4–25 km/h), decision period (per tick to 3 s), the light given as text to VLMs only, and the absence of any officer input for 3 systems all enter the same number. |
| Behavioural diagnosticity | Yes for position (L), enforced speed (V), stopped share (H) and time to held stop (R). Gaps: R ignores position; P and C are (near) constant here. |
| Cause diagnosticity | Not from the score. With decision logs, frames and brake-path checks, the VLM scores decompose into specific output sequences (§5). For category-B and category-A systems no such evidence exists, so their scores stay behavioural. |

**Conclusion supported by this batch**
- Report one common behavioural score together with the SV flags, plus system-specific diagnostic evidence where it exists.
- Do not read causes from the score.
- Do not score missing internal logs as perception failures.
- Do not require VLM-only fields from other systems.

## 7. Evidence that would separate the remaining causes

1. **Save the actual model inputs after onset:** the VLM's JPEG and each E2E model's own frames.
2. **Log a minimal intermediate output per system:**
   - waypoints / desired speed for NEAT, CILRS and TransFuser;
   - InterFuser's object map (the verbose dump exists but was off);
   - TCP before and after post-processing.
3. **Fix the stimulus:** render the paddle, then contrast the same officer showing neutral vs STOP vs PROCEED.
4. **Prompt ablation at fixed frames and seeds:**
   - without the textual light;
   - with separate person / role / gesture / meaning fields.
5. **Decision injection:** a correct STOP at q1 under the same 1.5 s schedule.
6. **Record the VLM seed and sampling settings.**
