# Credit discrimination: v2.2-final vs v2.2-D, with failure-type labels

**Files.**

- Source: `scripts/discrimination.py`, run from the workspace root.
- Tables: `data/`.
- Tests: `test_discrimination.py` (4 passed).
- v2, v2.1 and v2.2-final are unchanged and reused read-only.

**Evidence labels.** [E] means recorded CARLA telemetry: the 650 leaderboard episodes and the 38 `green_stop`
validation runs. [S] means synthetic telemetry on recorded station templates. It tests scorer logic only.

## What v2.2-D is

v2.2-D is v2.2-final with three changes. Each makes the score follow its stated intent where the computed value did
not. None adds a term or a weight. Next to the score, v2.2-D reports a **failure-type label**: one categorical field
computed from logged features, with no ordering between labels.

| change | intent it restores | what changes |
|---|---|---|
| **D1** | A STOP/HOLD response means reaching and holding a stop. A held stop reached while the directive is still live is late compliance, not "no response". | After the reaction plateau, the latency factor of a **held** stop reaches 0 at the later of the published 8 s and the directive end. Before, any held stop later than 8 s scored the same as never stopping. Egos without a held stop are unchanged (factor 0). |
| **D2** | Low-speed driving through a live STOP is not a stop. v2.2 already scores 0 for this on STOP stations. | `stale_directive_residue` (PROCEED after a live STOP): the hold factor is 1 for a settled dwell, else 0. The published curve paid a rolling creep 0.3 and a faster roll up to 0.94. A 3.5 km/h roll outscored a 2.9 km/h creep, so the curve was not monotone. |
| **D3** | The movement gate exists to zero an ego that never moved. | STOP/HOLD only: the credit-side gate requires ≥ 1 m of forward progress and drops the 5 km/h speed arm. A 4 km/h approach that travels 5 m and stops is no longer scored like a parked car. The strict-compliant shortcut keeps strict's definition, so a strict FAIL cannot reach 1.0. PROCEED/DETOUR/YIELD keep the speed arm, where it blocks the 0.55 `rule_hierarchy` credit for an ego that never proceeds. |

**Failure-type labels** (rules in `failure_label()`, from features in `features()`):

| label | rule |
|---|---|
| NO_START | travel < 1 m |
| COLLISION | ≥ 1 distinct contact, or the strict collision reason |
| DRIVE_THROUGH / CRAWL_THROUGH | crossed the line with no held stop; peak speed after onset ≥ / < 12 km/h (the speed-margin zero point). On `stale_directive_residue`, CRAWL_THROUGH is a rolling creep through the live STOP |
| CREEP_NO_STOP | moving under STOP, never held a stop, did not cross |
| OVERRUN_THEN_STOP | crossed, then held a stop |
| LATE_STOP | held stop before the line, still moving after the reaction deadline |
| STOP_NOT_HELD | held a stop, then moved again inside the window |
| STOP_POSITION | stopped in time but too close (< 1 m), or short of the staged anchor |
| WRONG_ACTION | PROCEED never entered or never proceeded; YIELD did not slow |
| LATE_START | PROCEED started > 3 s late; YIELD with no approach before the ambulance |
| EARLY_ENTRY | entered during the live STOP (`stale_directive_residue`) |
| INCOMPLETE_DETOUR / INCOMPLETE_YIELD / NO_RESUME | named for what was missing |

## 1. Implementation fixes vs discrimination gains

**Fixes from v2 to v2.2-final.** F1, F3, F6h, F7, C1 and C2 are corrections: each makes a computed value match its
stated criterion. Their discrimination effect, measured on the 548 FAIL episodes [E] (`data/discrimination_metrics.csv`):

| | v2 | v2.1 | v2.2-final | **v2.2-D** |
|---|---:|---:|---:|---:|
| distinct credit levels among FAIL (rounded to 4 decimals) | 82 | 143 | 130 | **130** |
| FAIL at exactly 0 | 441 (80.5 %) | 383 (69.9 %) | 400 (73.0 %) | **401 (73.2 %)** |
| all episodes 0 / partial / 1 | 441 / 114 / 95 | 383 / 165 / 102 | 400 / 148 / 102 | **401 / 147 / 102** |
| different-label FAIL pairs tied in the scalar | 56.1 % | 38.0 % | 43.6 % | **43.9 %** |
| distinct FAIL states, score (rounded to 4 decimals) + label | 100 | 160 | 146 | **145** |

**Spearman ρ, credit vs a behaviour feature, within the 206 engaged STOP/HOLD FAILs:**

| feature | v2 | v2.1 | v2.2-final | **v2.2-D** |
|---|---:|---:|---:|---:|
| time to held stop (lower is better) | 0.368 | 0.347 | 0.605 | **0.610** |
| stopped share of the enforced window | 0.617 | 0.559 | 0.618 | **0.636** |
| maximum speed after the reaction deadline (lower is better) | 0.571 | 0.558 | 0.438 | **0.465** |
| minimum front-to-line distance | 0.726 | 0.675 | 0.526 | **0.562** |
| distinct contacts (fewer is better) | 0.125 | 0.077 | 0.040 | **0.048** |

**Reading:**

- **The scalar barely moved, and should not move further.** v2 → v2.2-final raised distinct FAIL levels from 82 to 130
  and lowered the zero share from 80.5 % to 73.0 %. These are side effects of the fixes. The largest one is F3:
  collisions with one or two contacts are no longer 0. v2.2-D does not widen the scale (130 levels, 73.2 % at 0).
- **Correlation with time-to-stop rose** from 0.368 to 0.61. The credit now tracks how quickly a stop is reached.
- **Correlation with line distance fell** (0.726 → 0.53/0.56). v2 paid crawls that stayed short of the line
  (CREEP_NO_STOP averaged 0.22 in v2; it is 0 now). Line distance on its own is not a quality measure for a vehicle
  that never stops.
- **The FAIL zero mass is now fully decomposed.** Under v2.2-D the 401 zeros split into 13 labels: NO_START 210,
  DRIVE_THROUGH 115, CREEP_NO_STOP 19, COLLISION 14, STOP_NOT_HELD 11, CRAWL_THROUGH 8, STOP_POSITION 6,
  OVERRUN_THEN_STOP 6, INCOMPLETE_DETOUR 4, EARLY_ENTRY 3, LATE_START 2, WRONG_ACTION 2, LATE_STOP 1.
- **The label carries the separation.** Every tie between different behaviours in the scalar (43.9 % of
  different-label pairs) is separated by the label.

## 2. Component sensitivity

**Synthetic sweeps, one feature at a time [S]** (`data/component_sweeps.csv`):

| feature swept | terms that respond | binary or saturating, and why |
|---|---|---|
| stop position, +6 → −5 m | clearance (linear from +1 to −2 m), conflict (× 0.35 once the front crosses) | 1.0 at ≥ +1 m because strict PASS forces 1. The **step at the line** (0.519 at 0 m → 0.168 at −0.5 m) is the conflict factor, a deliberate binary for entering the conflict zone. Clearance saturates at 0 beyond −2 m: deeper overruns are not separated, and are never rewarded |
| approach peak speed, 4 → 25 km/h | G_move and engagement | v2.2-final: 4 km/h scores **0**, the same as parked (5 km/h movement-gate speed arm). **v2.2-D: 0.294.** At ≥ 8 km/h the ego passes strict and scores 1 |
| reaction time (brake start 1–9 s) | F_lat; speed margin (max speed after the deadline) | v2.2-final: F_lat 0.92 → 0.72 → 0.52 → 0.12 → **0 at 8.4 s**, the same as never stopping. **v2.2-D: 0.96 → 0.84 → 0.73 → 0.51 → 0.40.** Credit 0.397 / 0.480 / 0.453 / 0.316 / 0.247 |
| stop hold before relaunching at 5 km/h | none between 2.5 s and 4 s (0.08 both); 8 s → 0.382 (the relaunch no longer reaches the line) | Hold length is not a term. When a relaunch crosses the line during the live STOP, the outcome is the violation, so the credit does not depend on how long the stop lasted first. STOP_NOT_HELD records it. A hold under 2 s is not a held stop (CRAWL_THROUGH, 0) |
| collisions, 0 / 1 / 2 / 6 contacts | F_safe | Steps 1 / 0.25 / 0.10 / 0 by design |
| travel, 0.5 → 6 m (8 km/h short stop) | G_move | Binary at 1 m: 0.9 m scores 0, 1.2 m passes strict. Strict's own boundary |

**On the 650 [E]** (`data/component_profile_650.csv`, STOP/HOLD, n = 390):

| term | share at 0 | share at 1 | distinct values (rounded to 4 decimals) |
|---|---:|---:|---:|
| A (overall) | 73.8 % | — | 53 |
| speed margin | 40.5 % | — | 43 |
| clearance | 39.7 % | — | 17 |
| engagement | 38.5 % | — | 89 |
| F_lat | 36.7 % | 57.2 % | 22 |
| G_move | 37.4 % | — | binary |
| no-conflict | 32.6 % | — | binary |
| F_safe | — | 97.9 % | 4 |

## 3. Where the 0 and 1.0 mass comes from

**The 400 zeros of v2.2-final [E]** (`data/zero_decomposition.csv`; zeroing terms are multiplicative):

| behaviour (label) | zeros | zeroed by |
|---|---:|---|
| NO_START | 210 | G_move (and engagement) |
| DRIVE_THROUGH | 115 | F_lat (no held stop), speed margin and clearance |
| CREEP_NO_STOP | 19 | F_lat (no held stop) |
| STOP_NOT_HELD | 14 | speed margin and clearance (the relaunch crosses at speed) |
| COLLISION | 13 | F_safe (> 5 contacts), or with F_lat |
| STOP_POSITION | 6 | clearance, approach requirement |
| OVERRUN_THEN_STOP | 6 | speed margin and clearance |
| INCOMPLETE_DETOUR | 4 | F_lat or A |
| CRAWL_THROUGH | 3 | F_lat |
| EARLY_ENTRY | 3 | A (directive hold) |
| LATE_STOP | 3 | F_lat beyond 8 s, or engagement (fixed by D1/D3) |
| LATE_START | 2 | various |
| WRONG_ACTION | 2 | various |

**The 1.0 mass** is exactly the 102 strict passes. The constraint credit = 1 ⇔ PASS fixes this mass. A normal stop,
a careful short stop and an instruction-blind lurch therefore all score 1.0.

**Pairs that collapse to the same score, and why** (every pair below is ordered from intent):

| pair | v2.2-D | cause | intended? |
|---|---|---|---|
| normal stop vs F2 lurch (CARLA fixtures) | 1.0 = 1.0 | both strict PASS; the trajectories are equivalent | yes, by construction; not separable from trajectories (§5) |
| normal stop vs late held stop before the line | 1.0 > 0.247–0.48 (synthetic brake at 4–9 s); CARLA late-stop fixture 0.350–0.376 | F_lat and speed margin | yes: the STOP must be in force by the reaction budget |
| late stop vs line overrun then stop | CARLA late stop 0.35 > CARLA overshoot 0.033; synthetic 2 m overrun 0.118 | conflict × 0.35, clearance | yes: crossing the line is the core violation |
| overrun then stop vs crawl or drive-through | up to 0.41 vs 0 (an overrun far past the line at speed is also 0, via speed margin and clearance) | F_lat = 0 without a held stop | yes: no stop was ever reached |
| crawl-through vs drive-through | 0 = 0 | F_lat = 0 for both | **open: design decision.** Ordering them by speed would pay entering the junction under STOP slowly. The label separates them |
| no-start vs constant-go (drive-through) | 0 = 0 | G_move vs F_lat / A | **open: design decision.** No-start is not compliance (no engagement), and drive-through is the violation. Whether over-caution beats danger is a value judgement. The label separates them |
| late held stop (> 8 s) vs never stopping | v2.2-final 0 = 0 → **v2.2-D 0.40 F_lat vs 0** | F_lat ramp end | fixed by D1 |
| slow 4 km/h approach and stop vs parked | v2.2-final 0 = 0 → **v2.2-D 0.294 vs 0** | G_move speed arm | fixed by D3 |
| rolling creep through a live STOP (`stale_directive_residue`) vs held | v2.2-final 0.3 / 0.94 / 0.67 (2.9 / 3.5 / 6 km/h) → **v2.2-D 0** | non-monotone hold curve | fixed by D2 |

## 4. Finer separation of failures from existing features

**What is done:**

- **D1–D3 are in the scalar.** Each removes a collapse in which an intended difference was scored as no difference.
- **Where the ordering cannot be set from intent, there is no weight.** No-start vs drive-through, and crawl vs
  drive-through at speed, are reported as a separate categorical label.

**Considered and not done:**

- *A FAIL floor or ε tie-breaker:* it creates levels with no behavioural meaning.
- *Ordering crawl above drive-through:* it pays slow entry under STOP.
- *Rewarding a no-start under STOP as safe:* it is not compliance.
- *Hold-length credit before a crossing:* the outcome is the violation.

**Per-model label distribution [E]** (`data/labels_per_model.csv`; counts over all runs):

| model | PASS | NO_START | DRIVE_THROUGH | COLLISION | CREEP_NO_STOP | CRAWL_THROUGH | LATE_STOP | STOP_NOT_HELD | OVERRUN_THEN_STOP | other |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| oracle | 24 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| InterFuser (v2 run) | 8 | 7 | 3 | 2 | 0 | 0 | 2 | 0 | 1 | 2 |
| NEAT | 5 | 0 | 2 | 0 | 1 | 1 | 6 | 2 | 0 | 8 |
| CILRS | 5 | 0 | 11 | 5 | 0 | 0 | 0 | 1 | 1 | 2 |
| Qwen2.5-VL (5 runs) | 23 | 36 | 15 | 16 | 3 | 2 | 2 | 13 | 5 | 10 |
| TCP | 3 | 0 | 0 | 4 | 8 | 4 | 0 | 2 | 1 | 3 |
| TransFuser | 2 | 18 | 2 | 0 | 0 | 0 | 0 | 0 | 1 | 2 |
| MPC | 3 | 0 | 13 | 7 | 0 | 0 | 0 | 0 | 0 | 2 |
| PID | 3 | 0 | 12 | 8 | 0 | 0 | 0 | 0 | 0 | 2 |
| Qwen3-VL (5 runs) | 13 | 55 | 27 | 18 | 0 | 0 | 0 | 2 | 0 | 10 |
| OpenEMMA | 2 | 7 | 6 | 5 | 1 | 0 | 1 | 0 | 2 | 1 |
| AIM | 1 | 2 | 2 | 0 | 5 | 1 | 2 | 3 | 0 | 9 |
| baseline | 0 | 2 | 8 | 11 | 0 | 0 | 3 | 0 | 0 | 1 |
| GLM-4.5V (5 runs) | 10 | 83 | 15 | 5 | 1 | 0 | 3 | 0 | 4 | 4 |

"other" is STOP_POSITION, WRONG_ACTION, LATE_START, EARLY_ENTRY and the detour/yield labels.

**What the labels show.** The profiles answer "how does it fail?", which the scalar cannot:

- **TransFuser** fails mostly by not starting (18 of 25).
- **MPC and PID** fail by driving through or colliding (20 of 25 each).
- **TCP** fails by creeping or crawling through (12 of 25).
- **NEAT** fails by late stops (6 of 25).
- **GLM-4.5V** mostly does not start (83 of 125).

**Label validation.**

- **CARLA fixtures [E]** (`data/validation_labels.csv`) all match: normal stop and lurch → PASS, late stop →
  LATE_STOP, overshoot → OVERRUN_THEN_STOP, constant go → DRIVE_THROUGH, no start → NO_START (3/3 seeds each).
- **No-officer runs:** 13 of 14 are DRIVE_THROUGH, CRAWL_THROUGH or NO_START; the oracle passes. They match the strict reasons.
- **Real episodes:** a three-per-label sample is in `data/label_samples.csv`. Each label agrees with its strict
  reason and features. For example:
  - CREEP_NO_STOP: AIM never above 7 km/h, never crossing.
  - DRIVE_THROUGH: crossings at 12.7–23.8 km/h.

## 5. Normal stop vs instruction-blind stop

Existing-log signals and their power [E, S]:

| signal | result |
|---|---|
| deceleration starting after the directive onset | 14 of 38 real STOP/HOLD passes start decelerating **before** onset, so a rule requiring later deceleration falsely penalises 37 %. An adapted lurch can also brake after onset. **No usable power.** |
| cross-station consistency per model: ISI = P(held stop, onset+3–8 s \| STOP/HOLD) − P(same \| PROCEED) (`data/f2_instruction_sensitivity.csv`) | oracle 1.00. Non-privileged systems −0.19 to +0.23 (InterFuser +0.11, OpenEMMA +0.23, NEAT −0.19, baseline −0.26). The 48 instruction-independent policies range from −0.13 to +0.13. **Real systems are not separable from instruction-blind policies.** Only the oracle stands out. The index is also confounded by non-officer cues (for example light state) |
| existing no-officer `green_stop` runs (one station, 14 models, 1 seed) | Stops with the officer and drives without: NEAT. The oracle stops in both conditions, so its stop is not attributable to the officer. One station and one seed cannot support a per-model claim |

**Conclusion.** On one trajectory, an instruction-following stop and an instruction-blind stop are identical. They
cannot be separated from existing logs.

**Minimal additional data:** matched officer-absent runs under the same traffic and light, for every STOP/HOLD
station.

- 15 stations × 3 seeds × 10 single-run models = 450 episodes.
- Plus 15 × 5 seeds × 3 VLMs = 225 episodes.
- **675 new episodes in total.**
- Optionally, add directive-flipped (STOP → PROCEED) runs on the same stations.

The per-model score is then the behaviour difference with vs without the officer.

## 6. v2.2-final vs v2.2-D

| | v2.2-final | **v2.2-D** |
|---|---:|---:|
| credit = 1 without strict PASS | 0 | **0** |
| strict PASS with credit < 1 | 0 | **0** |
| FAIL ≥ PASS pairs in the same scenario | 0 | **0** |
| episodes changed vs final | — | 29: 23 up (LATE_STOP 15, STOP_NOT_HELD 7, OVERRUN_THEN_STOP 1), 6 down (CRAWL_THROUGH 5, COLLISION 1, all on `stale_directive_residue`) |
| gains for DRIVE_THROUGH, CRAWL_THROUGH, CREEP_NO_STOP, COLLISION, NO_START, EARLY_ENTRY, WRONG_ACTION | — | **none** (tested) |
| Kendall τ-b vs v2 (all / non-privileged) | 0.846 / 0.818 | 0.868 / 0.848 |
| Kendall τ-b vs v2.2-final | 1 / 1 | 0.978 / 0.970 |
| rank changes vs final | — | AIM 12 → 11, OpenEMMA 11 → 12 |

**Synthetic ordering families** (`data/ordering.csv`) have the same remaining violations in both versions:

- the 5 F4 cases (fast vs slow at equal overshoot, a timing property);
- the approach-start step (0.477 < 0.577, onset-anchored approach distance);
- v2.2-final has one more: the `stale_directive_residue` hold curve (2.9 → 3.5 km/h). D2 removes it.

**48 instruction-independent policies [S]** (`data/policies.csv`):

- The best scores 69.63 under both versions.
- 37 of 48 are above the best non-privileged system in each.
- Four policies move by at most 2 points: "stop after 20 m" +1.99 and "stop after 10 m" +0.31 (late held stops); the
  2 s and 4 s stop-and-go policies −1.38 and −1.24.

**Graded on the 650 [E]** (`data/leaderboard_650.csv`; the InterFuser row is its v2 run):

| model | v2 | v2.2-final | v2.2-D |
|---|---:|---:|---:|
| oracle | 96.91 | 96.91 | 96.91 |
| InterFuser | 43.53 | 40.68 | 42.28 |
| NEAT | 42.91 | 33.62 | 36.18 |
| CILRS | 24.57 | 26.05 | 26.05 |
| Qwen2.5-VL | 20.75 | 21.86 | 21.71 |
| TCP | 30.33 | 20.68 | 19.86 |
| TransFuser | 14.32 | 14.32 | 14.32 |
| MPC | 12.75 | 14.10 | 14.10 |
| PID | 11.76 | 13.51 | 13.51 |
| Qwen3-VL | 11.65 | 13.47 | 13.47 |
| OpenEMMA | 10.61 | 11.87 | 11.63 |
| AIM | 13.23 | 11.59 | 12.59 |
| baseline | 7.71 | 9.11 | 9.74 |
| GLM-4.5V | 8.14 | 8.43 | 8.46 |

## 7. Verdict: partially

**What the credit (v2.2-D, score plus failure type) now answers from existing logs:**

- whether the observable trajectory complied: stop, hold, proceed, detour or yield, with timing, line position,
  enforced speed and contacts;
- how a failure happened, through the label;
- how quickly a stop was reached (ρ 0.61 with time to held stop).

**Fundamental limits** (cannot be solved from existing data):

1. **Whether the instruction was perceived (F2).** Instruction-blind policies score up to 69.63, and 37 of 48 are
   above every real system. Real systems are not separable from them on any existing-log signal. This needs the
   matched officer-absent runs in §5.
2. **Separating strict passes.** Credit = 1 ⇔ PASS by requirement. A normal stop and a lurch both score 1.0.
3. **Value orderings** with no objective basis: no-start vs drive-through, crawl vs drive-through, and impact speed
   at equal overshoot (F4). They are reported as labels, not as weights. Whether any of them should enter the scalar is a
   design decision.
4. **Contact identity after the 20th collision event** (F3 bounds). This needs actor ids logged on every event.
