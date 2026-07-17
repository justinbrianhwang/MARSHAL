# Why these scenarios — the design justification for MARSHAL's authority-conflict suite

*This document answers the question a reviewer will ask first: "why **these** 21
scenarios?" The answer has four independent legs: (1) the failures are happening in
real deployments today; (2) the required behavior is written into US traffic law and
the MUTCD, so the ground truth is normative, not invented; (3) no existing benchmark —
driving, human-interaction, or VLM-QA — evaluates this axis; (4) staging rare events
in simulation is the established methodology for exactly this class of problem.*

---

## 1. The failure class is real, current, and regulator-visible

The scenario suite is not hypothetical. Every scenario family in MARSHAL corresponds
to a failure mode documented in commercial robotaxi deployments:

- **Authority-blind driving at emergency scenes.** CNN's 2026 investigation found
  Waymo robotaxis "ran red lights, drove into oncoming traffic and active crime
  scenes, and failed to abide by emergency road closures," including parking on top
  of charged fire hoses and driving through law-enforcement checkpoints onto closed
  roads ([CNN](https://www.cnn.com/us/waymo-robotaxis-safety-invs)). San Francisco
  firefighters report repeated obstruction at incident scenes
  ([SF Standard](https://sfstandard.com/2026/07/10/waymo-robotaxi-emergency-response/)).
  → MARSHAL: `emergency_scene_blocking`, `crash_detour`, `civilian_warning_accident`,
  `barricade_self_detour`.
- **Slow or absent response to an officer's direction.** In a Phoenix incident, a
  police officer attempting to wave a Waymo over was ignored for ~90 seconds before
  the vehicle complied ([NBC](https://www.nbcnews.com/news/us-news/driverless-waymo-vehicle-inadvertently-takes-riders-tense-police-stop-rcna246994));
  first responders in SF are trained to physically enter robotaxis that fail to
  respond ([SF Standard](https://sfstandard.com/2023/09/03/robotaxi-waymo-cruise-first-responders-how-to-override-manual-mode/)).
  → MARSHAL: `green_stop`, `red_proceed`, `signal_off`, and the **reaction-latency
  (RTL)** metric.
- **Regulatory attention is already here.** NHTSA's administrator has called AV
  interference with police, ambulances and fire trucks "a disturbing trend"
  ([Axios](https://www.axios.com/2026/07/15/waymo-accountability-emergencies-nhtsa)).
  A benchmark that measures precisely this compliance axis is therefore not a niche
  academic exercise — it measures the behavior regulators are now auditing.

## 2. The expected behavior is normative — written law, not our invention

MARSHAL's correct actions are not design opinions; they transcribe codified US
traffic-control rules (full citations: [legal_grounding.md](legal_grounding.md)):

| Scenario family | Normative basis |
|---|---|
| `green_stop`, `red_proceed`, `signal_off` | UVC §11-103 / NY VTL §1110(a): obey devices *"unless otherwise directed by a traffic or police officer."* The officer's directive **overrides the signal by statute.** |
| `flagger_control`, `flagger_slow_then_stop` | MUTCD 11th ed. **Part 6** (Temporary Traffic Control): flaggers control traffic with STOP/SLOW paddles; UVC §11-103 extends obedience duty to construction flaggers. |
| `school_crossing_guard` | UVC §11-103 names the *"uniformed adult school crossing guard"* explicitly. |
| `ambulance_yield` | NY VTL §1144 / UVC: mandatory yield to authorized emergency vehicles. |
| `unauthorized_go`, `fake_vest_director`, `two_civilians_disagree` | The *converse* of the statute: authority is **conferred by role, not appearance**. MUTCD Part 6 permits hand-movements-only control **only** for uniformed law enforcement / emergency responders — a hi-vis vest alone confers nothing. A model that obeys anyone gesturing is unsafe (and attackable). |
| `civilian_warning_accident` | Duty-of-care counterpart: a civilian warning at a *visible* crash carries contextual weight — same actor class as `unauthorized_go`, opposite correct action *because of scene evidence*. The pair isolates *reasoning* from *actor classification*. |
| `conflicting_authorities`, `rule_hierarchy` | The precedence ladder itself (safety > authorized human > device) — the statutes only make sense as a hierarchy, so conflicts between its levels are the core test. |
| `occluded_officer`, `ambiguous_gesture`, `adjacent_lane`, `sequential_directive` | SOTIF-style *triggering conditions* (ISO 21448): partial occlusion, ambiguous input, attribution to the correct approach, and directive persistence over time — the perception/context conditions under which the normative rule must still be applied. |
| `fallen_person`, `crash_detour` | Safety tier of the ladder: hazard overrides everything, including a GO. |

Because the ground truth is statutory, MARSHAL's oracle is **normatively calibrated**:
"what the law requires," not "what our model prefers."

## 3. No existing benchmark evaluates this axis

Three adjacent literatures each stop short of the question "did the vehicle obey the
right authority?":

**(a) Closed-loop driving benchmarks** — CARLA Leaderboard, Bench2Drive (44
interactive scenarios: cut-ins, merging, yields, emergency braking, blocked lanes…),
nuPlan, NAVSIM. None of their scenario catalogs contains a human *directing* traffic
in contradiction to a signal; their metrics (route completion, infractions,
collisions) would actually *reward* driving through an officer's STOP on a green
([Bench2Drive](https://neurips.cc/virtual/2024/poster/97436)).

**(b) Humans-in-traffic benchmarks** — HABIT (CARLA, 4,730 real pedestrian motions)
evaluates *reaction to human motion* (collision, injury scale, false-braking) and
shows leaderboard agents degrade sharply around realistic humans — but its humans are
traffic *participants*, not traffic *authorities*; there is no obey/ignore ground
truth ([HABIT](https://arxiv.org/abs/2511.19109)). WOD-E2E curates real long-tail
segments (incl. "Special Vehicles") but is **open-loop** trajectory matching — it
cannot observe whether a directive was ultimately obeyed
(see [comparison in our docs](related_work.md)).

**(c) Officer-gesture perception** — a mature line of work (e.g. the TCG dataset and
successors) classifies police gestures from video/skeletons at 96–98% reported
accuracy ([TCG](https://arxiv.org/abs/2007.16072),
[Sci. Reports 2025](https://www.nature.com/articles/s41598-025-02833-y)). This solves
the *perception* stage only: classification accuracy says nothing about (i) whether
the gesture's issuer **has** authority, (ii) whether it targets **this** lane,
(iii) whether it should **override** the current signal, or (iv) whether the vehicle
**physically complies** in closed loop. MARSHAL's Track-C results make the gap
concrete: models that can read the gesture still fail the override decision.

**(d) VLM driving-QA benchmarks** — DriveLM, DriveBench (20k QA pairs over
perception/prediction/planning/behavior), AutoDrive-QA: general scene understanding,
no authority-conflict axis, and open-loop by construction.

**The gap in one sentence:** perception work classifies the gesture, driving
benchmarks score the trajectory, human-interaction benchmarks score collision
avoidance — *nobody scores whether the vehicle obeyed the party it was legally
required to obey.* That is the axis MARSHAL adds.

## 4. Why staged simulation is the right instrument

- **The events are too rare to mine.** WOD-E2E's own pipeline shows genuinely rare
  events occur at <0.03% of real driving even after industrial-scale mining of 6.4M
  miles — and authority-override moments (an officer *contradicting* a live signal)
  are a further sub-slice that no public dataset contains at usable volume.
- **Scenario-based testing is the established methodology** for exactly this
  situation: PEGASUS-lineage logical scenarios and ISO 21448 (SOTIF) prescribe
  *constructing* triggering conditions rather than waiting to observe them
  ([SOTIF review](https://arxiv.org/pdf/2503.02498)).
- **Counterfactual control is the scientific payoff.** Only staging allows the
  minimal pair `unauthorized_go` vs `civilian_warning_accident` (same actor class,
  opposite correct action) or `green_stop` vs `red_proceed` (same officer, opposite
  signal) — the pairs that separate *authority reasoning* from *actor detection*.
  A mined dataset cannot hold everything else constant.

## 5. Coverage argument — the suite spans the conflict space, not a difficulty ladder

The 21 scenarios systematically cover the **dimensions of the authority-conflict
space** (rather than being a graded difficulty ladder):

| Dimension | Values covered (example scenarios) |
|---|---|
| **Authority validity** | valid officer/flagger/guard (`green_stop`, `flagger_control`, `school_crossing_guard`) · invalid civilian/impostor (`unauthorized_go`, `fake_vest_director`) · contextual civilian (`civilian_warning_accident`) |
| **Conflict type** | authority vs. device (`green_stop`, `red_proceed`) · authority vs. authority (`conflicting_authorities`, `two_civilians_disagree`) · authority vs. safety (`rule_hierarchy`) · scene vs. route, no human (`emergency_scene_blocking`, `barricade_self_detour`) |
| **Required action** | STOP · PROCEED · YIELD · DETOUR · HOLD — deliberately balanced so a stop-biased policy cannot score by braking alone |
| **Directive dynamics** | static · sequential (`sequential_directive`) · escalating (`flagger_slow_then_stop`) |
| **Perceptual stress** | occlusion (`occluded_officer`) · ambiguity (`ambiguous_gesture`) · attribution (`adjacent_lane`) · dead signal (`signal_off`) |

Every cell of this matrix that is reachable on stock Town03 is populated by at least
one scenario; each scenario is the *minimal* instantiation of its cell (one new
stressor per scenario). That — not tier difficulty — is the design principle.

---

*See also:* [problem_statement.md](problem_statement.md) ·
[long_tail_definition.md](long_tail_definition.md) ·
[legal_grounding.md](legal_grounding.md) · [scenarios.md](scenarios.md) ·
[related_work.md](related_work.md).

**Sources** (news/deployment): [CNN investigation](https://www.cnn.com/us/waymo-robotaxis-safety-invs) ·
[SF Standard 2026-07-10](https://sfstandard.com/2026/07/10/waymo-robotaxi-emergency-response/) ·
[Axios / NHTSA 2026-07-15](https://www.axios.com/2026/07/15/waymo-accountability-emergencies-nhtsa) ·
[NBC Phoenix stop](https://www.nbcnews.com/news/us-news/driverless-waymo-vehicle-inadvertently-takes-riders-tense-police-stop-rcna246994).
(Regulatory): [MUTCD 11th ed. Part 6](https://mutcd.fhwa.dot.gov/pdfs/11th_Edition/part6.pdf) ·
UVC §11-103 · NY VTL §1102/§1110/§1144. (Academic):
[Bench2Drive](https://neurips.cc/virtual/2024/poster/97436) ·
[HABIT](https://arxiv.org/abs/2511.19109) · [TCG gesture dataset](https://arxiv.org/abs/2007.16072) ·
[DriveLM](https://arxiv.org/abs/2312.14150) · [DriveBench](https://drive-bench.github.io/) ·
[SOTIF SLR](https://arxiv.org/pdf/2503.02498).
