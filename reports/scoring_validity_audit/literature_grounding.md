# Literature grounding of the MARSHAL scoring formula

Audit date: 2026-10-27. Scope: where each element of the MARSHAL-Graded episode credit, its aggregate, and the
16.59 m engagement band came from, checked against the local design history and against primary sources for
the closest precedents. Read-only audit. No MARSHAL file was changed.

## 0. Ground rules

- **A shared multiplicative structure does not prove adoption.** "Completion × penalty" (CARLA), "multipliers ×
  weighted average" (nuPlan, NAVSIM) and MARSHAL's "action × latency × safety × gate" all have the same
  algebraic shape (gates or penalties multiplying a base score). That shape is common across the field. It is
  not evidence that MARSHAL copied any of them, and no local document or commit says it did.
- **The literature does not validate MARSHAL's thresholds.** None of the sources below contains MARSHAL's ramp
  endpoints (1/12 km/h, 1/−2 m, 3/8 s), its 0.60/0.40 or 0.35 factors, its 1/0.25/0.10/0 collision ladder, its
  5 km/h / 1 m gate or its scenario weights. Where a source has a similar *form*, its *values* are different
  (see §3).
- A value is marked **not verified** when I could not read it in a primary source. No citation in this report
  is inferred.

## 1. What the local record says about provenance

### 1.1 Commit history (`git log -- graded_episode_scoring.py docs/marshal_graded_score.md`)

| Commit | Date | Relevance |
|---|---|---|
| `268531d` | 2026-06-29 | First version of `graded_episode_scoring.py` and `docs/marshal_graded_score.md`. The docstring section "Calibration" says: *"The curves are hardcoded and anchored to the strict thresholds … the latency factor has a full credit plateau through the 3 s strict reaction budget … No learned or subjective model is used."* `_safety_factor` already returns 1.0 / 0.25 / 0.10 / 0.0 with the docstring *"Return a deterministic collision penalty. Only collision count is recorded…"*. `GRADED_THRESHOLDS` already has `latency_full_credit_s 3.0`, `latency_zero_credit_s 8.0`, `stop_speed_zero_kmh 12.0`, `stopline_zero_clearance_m −2.0`. The first doc version is labelled "planned secondary" and lists no source. |
| `0e431b9` | 2026-07-14 | "Add the MARSHAL-Graded formula" to the doc: the Σ w_s c_s / Σ w_s aggregate in writing. No source cited. |
| `47a3ad0`, `72d1e25`, … | 2026-07-21…23 | Adversarial-review fixes (engagement anchors, dwell, gate exemptions). No literature added. |
| `ab1ae40` | 2026-09-18 | Movement gate added to graded scoring. Engagement band changed from a hand-set 15.0 m to d = v·t_r + v²/(2a) = 16.59 m. Commit message: *"stopline/hazard engagement band is physics-derived … replacing the hand-set 15.0 m"*. The new code comment in `strict_episode_scoring.py` says: *"t_r = 1.0 s and a = 2.5 m/s^2 (literature-range candidates; primary-source verification tracked in the formulation document)"*. |
| `c3aa67e`, `637c04f` | 2026-10-24/25 | Movement gate scoped to STOP/HOLD, then applied to every action. |
| `e6ae3c8` | 2026-10-26 | Protocol v2: front-bumper clearance, full credit at the 1.0 m design margin. |

None of the graded-scorer commits or doc versions names CARLA Leaderboard, Bench2Drive, nuPlan or NAVSIM as a
source of the formula.

### 1.2 The "formulation document"

The code comment points to a local note, `notes/FORMULATION_2026-08-12.md` (outside the repository)
(not public). It lists *candidate* sources and says they were not checked:

- §"Threshold | Candidate source": the reaction budget 3.0 s is linked to *"AASHTO A Policy on Geometric Design of
  Highways and Streets (Green Book) — brake reaction time 2.5 s"*, and the engagement distance is linked to
  *"same AASHTO SSD model: d = v·t_r + v²/(2a), design deceleration 3.4 m/s²; human-factors 'comfortable'
  deceleration ≈ 2–3 m/s²"*.
- Threshold table: reaction budget *"PRT literature 1.5–2.5 s; 3.0 is a lenient upper bound"*.
- Decision 4 (adopted 2026-09-18) uses the physics/PRT *form*. The parameter values chosen (t_r = 1.0 s,
  a = 2.5 m/s²) do not match the AASHTO values the same note quotes.

### 1.3 Public documents

- `docs/related_work.md`, `docs/problem_statement.md`, `docs/research_gap.md`, `README.md`: CARLA Leaderboard,
  Bench2Drive, nuPlan and NAVSIM appear only as *contrasting* benchmarks ("Scores route completion and
  rule/collision infractions…"). None says MARSHAL's score is derived from them.
- `docs/metrics.md`, `docs/evaluation_methodology.md`, `docs/evaluation_protocol_v2.md`: the 16.59 m band is
  called *"physics-derived, v·t_r + v²/(2a) at the 25 km/h cruise (t_r = 1.0 s, a = 2.5 m/s²)"*, with no citation.
  The graded curves are called *"set against the privileged oracle"*.
- `Paper/05_evaluation.md` (local): *"t_r = 1.0 s, a = 2.5 m/s² — literature-range values, primary-source
  verification pending, §7.3"*.

## 2. Primary-source verification of precedents

### 2.1 CARLA Leaderboard

| Version | Page | Driving score | Infraction penalty | Coefficients (verified on page) |
|---|---|---|---|---|
| 1.0 | <https://leaderboard.carla.org/evaluation_v1_0/> | DS_i = R_i × P_i, global = mean over routes | P_i = ∏_j p_j^(#infractions_j) (geometric series) | pedestrian 0.50, vehicle 0.60, static 0.65, red light 0.70, stop sign 0.80. Off-road: no coefficient (off-road distance is removed from route completion). Shutdowns: route deviation > 30 m, agent blocked 180 s, simulation timeout 60 s, route timeout, agent error. |
| 2.0 | <https://leaderboard.carla.org/evaluation_v2_0/> | same | same product; base 1.0, floor 0 | adds failure to yield to emergency vehicle 0.70, scenario timeout 0.70, failure to maintain minimum speed "up to 0.7" (speed compared with nearby vehicles, checked each quarter of the route); keeps 0.50/0.60/0.65/0.70/0.80. |
| 2.1 (current) | <https://leaderboard.carla.org/evaluation_v2_1/> | same R_i × P_i | **changed:** P_i = 1 / (1 + Σ_j c_j · #infractions_j) | pedestrian 1.0, vehicle 0.70, static 0.60, red light 0.40, emergency-vehicle yield 0.40, stop sign 0.25, scenario timeout 0.4 (4-min limit), min speed up to 0.4. |

The front page (<https://leaderboard.carla.org/>) says the leaderboard is at version 2.1, which *"comes with
significant modifications to the infraction score"*. A citation of "CARLA Leaderboard 2.0" coefficients should
name the version, because 2.1 replaced both the aggregation rule and the coefficients.

### 2.2 Bench2Drive

Jia, Yang, Li, Zhang, Yan. *Bench2Drive: Towards Multi-Ability Benchmarking of Closed-Loop End-To-End Autonomous
Driving.* NeurIPS 2024 Datasets and Benchmarks Track. arXiv:2406.03877 (v1 2024-06-06, v3 2024-11-27).
<https://arxiv.org/abs/2406.03877>. Verified in §3.3 and App. F of the HTML version:

- 44 scenarios × 5 short routes (~150 m) = 220 routes.
- **Success Rate** (Eq. 1): a route succeeds if the destination is reached *"without any rule infractions"*.
- **Driving Score** (Eq. 1): *"follows CARLA official metric as reference"*, route completion × cumulative
  product of infraction penalties, averaged over routes. App. F Table 7 uses Leaderboard-2.0-style coefficients
  (pedestrian 0.50, vehicle 0.60, other 0.65, red light 0.70, scenario timeout 0.70, too slow 0.70, no give way 0.70).
- **Efficiency** (Eqs. 2–3): ego speed / mean speed of nearby vehicles, at 20 checkpoints; excluded from DS.
- **Comfortness / smoothness** (Eq. 4): share of 20-frame segments whose kinematics stay within nuPlan
  expert bounds.

### 2.3 nuPlan closed-loop score

Caesar et al., *NuPlan: A closed-loop ML-based planning benchmark for autonomous vehicles*, arXiv:2106.11810 (v4
2022-02-04). The paper does not state the score formula. The authoritative definition is the devkit config
`nuplan/planning/script/config/simulation/metric_aggregator/closed_loop_reactive_agents_weighted_average.yaml`
in <https://github.com/motional/nuplan-devkit> (read at master `e924167`):

- Weighted average: `ego_progress_along_expert_route` **5.0**, `time_to_collision_within_bound` **5.0**,
  `speed_limit_compliance` **4.0**, `ego_is_comfortable` **2.0** (default 1.0).
- Comment in the file: *"The scenario score is defined as the weighted average score of the metrics listed
  above, multiplied by the score of the multiple_metrics below."*
- Multipliers: `no_ego_at_fault_collisions` (*"can be 0, 0.5 or 1"*), `drivable_area_compliance` (0/1),
  `ego_is_making_progress` (0/1; threshold 0.2 progress ratio per the devkit metric docs), and
  `driving_direction_compliance` (0/0.5/1; 2 m / 6 m thresholds per
  <https://nuplan-devkit.readthedocs.io/en/latest/metrics_description.html>).
- `no_ego_at_fault_collisions.py`: per track type, score = max(0, 1 − n/(threshold+1)); thresholds VRU 0,
  vehicle 0, object 1. So one at-fault object collision gives 0.5, and any VRU/vehicle at-fault collision gives 0.

The weights (5, 5, 4, 2) and multiplier values (0/0.5/1) are **verified**.

### 2.4 NAVSIM PDMS

Dauner, Hallgarten, Li, Weng, Huang, Yang, Li, Gilitschenski, Ivanovic, Pavone, Geiger, Chitta. *NAVSIM:
Data-Driven Non-Reactive Autonomous Vehicle Simulation and Benchmarking.* NeurIPS 2024 Datasets and Benchmarks
(arXiv:2406.15349 v2 2024-10-31). <https://arxiv.org/abs/2406.15349>. §3, Eq. 1:

PDMS = (∏_{m∈{NC,DAC}} score_m) × (Σ_{w∈{EP,TTC,C}} weight_w · score_w) / (Σ weight_w), with EP 5, TTC 5, C 2.
NC = 0 for an at-fault collision with a road user and 0.5 for a static object. DAC is a hard 0/1 penalty.
The paper calls it *"an efficient reimplementation of the nuPlan closed-loop score metric"*. (The v2 Eq. 1
lists NC and DAC as penalties. Later NAVSIM code versions add DDC and other terms. Those were not checked here.)

### 2.5 Stopping sight distance (AASHTO Green Book)

- AASHTO, *A Policy on Geometric Design of Highways and Streets* ("Green Book"). **The primary text was not
  accessible (paywalled). Not verified from the primary source.**
- Secondary but official, verified: Iowa DOT *Design Manual*, Ch. 6 "Geometric Design", §6D-1 Sight Distance
  (rev. 2015-07-02), Eq. 6D-1_1: *"SSD = 1.47Vt + 1.075 V²/a … t = break reaction time, 2.5 s … a = deceleration
  rate, 11.2 ft/s² (Reference: Equation 3-2 AASHTO Greenbook, 2011)"*
  (<https://documents.iowa.gov/home/download/9592894>). 11.2 ft/s² ≈ 3.4 m/s².
- NCHRP, *Acceleration, Deceleration, and Stopping Sight Distance Criteria for Geometric Design of Highways and
  Streets* (2024, doi:10.17226/27490). A search snippet says the 2018 Green Book assumes a 2.5 s brake reaction
  time *"long enough to exceed the 90th percentile value for all drivers"*. I could not read the chapter
  text, so this is **not verified**.
- An FHWA *Speed Concepts Informational Guide* page (highways.dot.gov) returned HTTP 403, so it is not verified.

Comparison at v = 25 km/h (6.944 m/s):

| Parameters | Reaction travel | Braking distance | Total |
|---|---:|---:|---:|
| MARSHAL: t_r 1.0 s, a 2.5 m/s² | 6.94 m | 9.65 m | **16.59 m** |
| AASHTO SSD design values: t 2.5 s, a 3.4 m/s² | 17.36 m | 7.09 m | **24.45 m** |
| t 1.5 s (low end of the PRT range in MARSHAL's own note), a 3.4 m/s² | 10.42 m | 7.09 m | 17.51 m |

MARSHAL uses the **same equation form** as SSD with **different parameters**. Its reaction time is shorter
(1.0 s vs 2.5 s) and its deceleration is gentler (2.5 vs 3.4 m/s²). The 2.5 s value is a conservative design
value covering ~90th-percentile drivers that are not alerted. 1.0 s is a different assumption: an attentive
driver who expects the event. Because of the gentler deceleration, the two choices partly cancel, but the result
is still about 8 m shorter than AASHTO SSD.

### 2.6 Reaction-time literature

Green, M. (2000). "How long does it take to stop?" Methodological analysis of driver perception-brake times.
*Transportation Human Factors*, 2(3), 195–216, doi:10.1207/STHF0203_1. The bibliographic record is confirmed by
search listings (SafetyLit, publisher DOI). The publisher pages returned HTTP 403, so **the abstract and values
are not verified from the primary source**. Secondary summaries put the values at about 0.7–0.75 s for fully
expected events, about 1.25 s for unexpected but common signals, and about 1.5 s for surprise events. If those
summaries are correct, t_r = 1.0 s falls between the expected and unexpected cases. Confirm against the paper
before citing.

## 3. Classification table

Classes: **directly adopted**, **mathematically adapted**, **conceptually inspired**, **MARSHAL-specific**,
**provenance unresolved**.

| Element | MARSHAL definition | Closest precedent (source, section) | Correspondence | Differences | Classification | Evidence |
|---|---|---|---|---|---|---|
| Multiplicative episode credit | C = Action × Latency × Safety × MovementGate | CARLA LB DS = R_i × P_i (evaluation_v1_0 / v2_0); nuPlan scenario score = multipliers × weighted avg (devkit yaml); NAVSIM PDMS §3 Eq. 1 | product of a base score with penalty/gate factors in [0,1] | MARSHAL base is an action-conditioned margin credit, not route completion or a weighted EP/TTC/comfort mean; latency is a factor, not a term; no infraction catalogue | **MARSHAL-specific** (same shape as precedents, adoption not shown) | `268531d` docstring: *"curves are hardcoded and anchored to the strict thresholds"*; no doc or commit cites a precedent for the product |
| Aggregate | 100·Σ w_s C_s / Σ w_s | nuPlan / NAVSIM normalized weighted mean (over *metrics*, not scenarios); CARLA global = arithmetic mean over routes | normalized weighted mean | weights are over scenarios (authority emphasis), not sub-metrics; CARLA and Bench2Drive weight routes equally | **MARSHAL-specific** (a generic weighted mean) | `0e431b9`; `docs/evaluation_methodology.md` §2 justifies weights normatively and diagnostically, with no source |
| Scenario weights 1.25–2.0 | authority-override scenarios up-weighted | nuPlan weights 5/5/4/2 (over metrics) | weighting exists | different object, values and rationale | **MARSHAL-specific** | `268531d` `SCENARIO_AUTHORITY_WEIGHTS` with per-line rationale comments; sensitivity study in `evaluation_methodology.md` §3 |
| Ramp L(x; full, zero) | piecewise-linear 1→0 between two thresholds | nuPlan speed-limit compliance and progress ratio are continuous [0,1] scores; Bench2Drive smoothness uses nuPlan bounds | continuous partial credit from kinematics | MARSHAL endpoints are its own; no precedent uses these ramps | **MARSHAL-specific** | `_linear_credit` in `268531d`; `GRADED_THRESHOLDS` *"Soft margins used only for partial credit curves"* |
| STOP credit 0.6·L(v;1,12)+0.4·L(clearance;1,−2) ×0.35 conflict × engagement | weighted residual-speed and stop-line clearance credit | none found | — | full-credit anchors = strict thresholds (1 km/h; 1.0 m front clearance, v2) | **MARSHAL-specific** | `268531d` (12 km/h, −2 m); `e6ae3c8` (1.0 m front-bumper margin); code `curve` string |
| Conflict-entry ×0.35 | partial credit after entering junction | CARLA red-light coefficient 0.70 (v1/v2), 0.40 (v2.1) | a fixed fractional penalty for an illegal entry | value differs; MARSHAL penalizes context-conditioned entry, not red-light running | **provenance unresolved** (no rationale or source recorded for 0.35) | code line `conflict_factor = 0.35`; no doc gives a reason |
| LatencyFactor 1 @3 s → 0 @8 s | linear decay after the strict reaction budget | AASHTO SSD 2.5 s PRT (secondary-verified); Green 2000 (not verified) | a reaction allowance in seconds | 3 s comes from the strict budget (coupled to directive timing per FORMULATION §threshold table); 8 s has no source | 3 s: **MARSHAL-specific** (local note links it to AASHTO only as an unverified candidate "lenient upper bound"); 8 s: **provenance unresolved** | `268531d`: *"full credit plateau through the 3 s strict reaction budget"*; FORMULATION note candidate table |
| SafetyFactor 1/0.25/0.10/0 | step function of collision-sensor count | CARLA collision coefficients (0.50/0.60/0.65 per event, geometric in v1/v2; additive denominator in v2.1); nuPlan/NAVSIM NC 0/0.5/1 | count-based multiplicative collision penalty | MARSHAL ignores collision type (pedestrian = static) and severity; first collision costs 75 %, versus 35–50 % in CARLA v1/v2; nuPlan zeroes any VRU/vehicle at-fault collision; MARSHAL has no at-fault logic | **conceptually inspired at most** (family resemblance); values **MARSHAL-specific**, rationale **unresolved** | `268531d` `_safety_factor` docstring *"deterministic collision penalty"*; no source cited |
| MovementGate (≥5 km/h ∧ ≥1 m) | zero credit without movement evidence | nuPlan `ego_is_making_progress` multiplier (0/1, progress ratio > 0.2); CARLA "agent blocked" shutdown 180 s | binary multiplier that zeroes a non-moving ego | MARSHAL thresholds are absolute (5 km/h, 1 m) and mirror strict's approach-evidence arm; nuPlan is relative to the expert | **MARSHAL-specific** (functionally analogous to nuPlan's progress multiplier; no adoption recorded) | `ab1ae40` message (*"inherits the strict approach-evidence arm … 41 park-at-spawn cells"*), motivated by a measured leak, not by literature |
| Engagement factor (continuous, creep cap 0.25) | approach speed × progress, creep capped | none found | — | — | **MARSHAL-specific** | `268531d` `stop_engagement_*` thresholds; `docs/marshal_graded_score.md` gate note |
| Engagement distance 16.59 m | d = v·t_r + v²/(2a), v 25 km/h, t_r 1.0 s, a 2.5 m/s² | AASHTO SSD equation (Green Book Eq. 3-2, 2011, via Iowa DOT §6D-1) | identical equation form | parameters differ (2.5 s / 3.4 m/s² → 24.45 m); t_r = 1.0 s is below the 1.5–2.5 s range MARSHAL's own note quotes; a = 2.5 m/s² is cited only as "comfortable ≈ 2–3 m/s²" without a source | **mathematically adapted** (form); parameter values **provenance unresolved** | `ab1ae40`; `strict_episode_scoring.py` comment *"literature-range candidates; primary-source verification tracked in the formulation document"*; FORMULATION note |
| Strict verdict (binary gate cascade) | all thresholds must hold | Bench2Drive Success Rate (§3.3 Eq. 1: success without any infraction) | binary all-or-nothing episode success | MARSHAL gates are action-conditioned telemetry proofs, not infraction absence | **conceptually inspired** at most (generic pass/fail); thresholds **MARSHAL-specific** | `docs/metrics.md` strict table; no commit cites Bench2Drive SR |
| "Oracle ≈ 100" calibration | thresholds set so the privileged oracle scores ≈100 | nuPlan EP is relative to the expert log | expert as reference | MARSHAL's oracle is a scripted privileged driver; calibration is a design check, not external validation | **MARSHAL-specific** | `268531d` docstring; `docs/marshal_graded_score.md` |

No element qualifies as **directly adopted**: no MARSHAL constant equals a precedent's constant and is
attributed to it.

## 4. Claims in local documents that cite literature incorrectly or without support

1. **"literature-range values" / "literature-range candidates" for t_r = 1.0 s, a = 2.5 m/s²**
   (`marshal_bench/criteria/strict_episode_scoring.py` docstring; `Paper/05_evaluation.md` §4.1). No source is
   cited. The local FORMULATION note's only named source (AASHTO) uses 2.5 s and 3.4 m/s², and the same note
   gives the PRT range as 1.5–2.5 s, which excludes 1.0 s. "Literature-range" is unsupported as written. It is
   defensible only if a source for an attentive or expected-event PRT ≈ 1 s (e.g. Green 2000, once verified)
   and for a comfortable deceleration ≈ 2.5 m/s² is actually cited.
2. **"physics-derived"** (`docs/metrics.md`, `docs/evaluation_methodology.md` §4,
   `docs/evaluation_protocol_v2.md` §3, `ab1ae40`). The equation is kinematic, but the two parameters are design
   choices. The wording suggests the 16.59 m value is fixed by physics. It is not: AASHTO parameters give 24.45 m.
3. **FORMULATION note, engagement row**: *"physics-derived … d = v²/(2a) + v·t_r ≈ 15 m at 25 km/h"*. With
   a = 2.5 m/s², 15 m needs t_r ≈ 0.77 s. The later 16.59 m (t_r = 1.0 s) replaced it. The note's "AASHTO SSD
   model … design deceleration 3.4 m/s²" row does not support either number. This note is local only.
4. **FORMULATION note, reaction budget**: *"AASHTO stopping-sight distance uses 2.5 s"*. The value matches the
   Green Book as quoted by Iowa DOT (Eq. 3-2, 2011). The primary text was not read. The note frames it as a
   candidate, which is accurate.
5. **`docs/related_work.md`**: the citations checked are correct (Bench2Drive title, NeurIPS 2024, 44
   scenarios; nuPlan arXiv:2106.11810; NAVSIM Dauner et al. NeurIPS 2024 D&B, PDMS). "CARLA Leaderboard (1.0 /
   2.0)" leaves out that the live leaderboard is 2.1 with a different infraction formula. That matters only if
   coefficients are ever quoted. The doc's own TODO (*"confirm each 'limitation' wording against the cited
   source"*) is still open.
6. **`docs/comparison_with_wod_e2e.md`**: *"WOD-E2E criticizes PDMS for prioritizing ego progress + comfort +
   TTC"*. **Not verified** here (outside this audit's sources). Note that per NAVSIM Eq. 1, PDMS gives EP and
   TTC weight 5 and comfort weight 2 *under* hard collision and drivable-area multipliers. Check the WOD-E2E
   wording before keeping it.
7. No public document claims that the graded product, the safety ladder or the latency ramp come from CARLA,
   nuPlan or NAVSIM. There is no false-adoption claim. The gap is the reverse: these constants (0.35 conflict
   factor, 8 s latency zero, 0.25/0.10 safety steps, 0.60/0.40 split, 12 km/h and −2 m ramp ends) have no
   written rationale beyond "anchored to strict thresholds / set against the oracle".

## 5. Bottom line

The MARSHAL-Graded structure is a benchmark-specific design. It has the same algebraic shape as the CARLA
Leaderboard and nuPlan/NAVSIM scores, but no local record shows it was taken from them. The only element tied to
external literature is the engagement band. It uses the AASHTO stopping-sight-distance *equation* with
non-AASHTO *parameters* whose literature support is still unverified. Every numeric threshold is a MARSHAL design
choice. Its support is internal (oracle calibration, measured verdict-flip sensitivity, the weight-perturbation
study), not external validation.
