# Related Work — Prior Benchmarks vs MARSHAL

This document positions MARSHAL against existing autonomous-driving benchmarks:
their **role** (what they are built to evaluate) and their **limitation** with
respect to *traffic-authority reasoning*, followed by **MARSHAL's distinct
role**. The goal is to make precise what MARSHAL adds that the others do not.

> One-line positioning: prior benchmarks evaluate *driving competence*
> (perception, prediction, navigation, comfort, collision avoidance). MARSHAL
> evaluates *authority arbitration* — **when a human or the scene contradicts the
> ordinary signal, who should the vehicle obey?**

## Prior benchmarks: role and limitation

| Benchmark (representative) | Primary role — what it evaluates | Limitation for *authority* reasoning |
|---|---|---|
| **CARLA Leaderboard** (1.0 / 2.0) | Closed-loop route driving + infraction scoring on CARLA towns | Scores route completion and rule/collision infractions; the agent obeys traffic-control devices — there is no human traffic-authority that *overrides* the signal. |
| **Bench2Drive** | Closed-loop, multi-ability end-to-end driving across many short skill scenarios in CARLA | Probes driving *skills* (merging, overtaking, giving way, emergency braking). It does not test "who has authority" when a human directive contradicts the light/road. |
| **nuPlan** | Large-scale closed-loop motion *planning* on real-world logs | Measures planning quality / comfort / safety against logged human driving; no authority conflict, no gesture or officer semantics. |
| **nuScenes / Waymo Open / Argoverse** | Perception + motion forecasting on real logs | Evaluate upstream perception and trajectory prediction; the decision of *whose* instruction to follow is out of scope. |
| **DriveLM / LingoQA / DriveVLM / Reason2Drive** | Language / visual-question-answering reasoning over driving scenes | Ask about objects, intentions, and planning rationale; they do not test authority-*priority* under conflicting cues, nor closed-loop authority *compliance*. |
| **Accident / corner-case sets** (e.g. DeepAccident, CommonRoad) | Physical hazard and accident-avoidance behaviour | The agent reacts to hazards as obstacles; there is no human-authority that overrides the normal right-of-way. |

> The benchmarks above are representative, not exhaustive; see the citation TODOs
> below. MARSHAL is complementary to them — it does not replace driving-competence
> evaluation, it isolates the orthogonal authority-arbitration axis.

## MARSHAL's role (what it adds)

MARSHAL isolates **authority-aware reasoning** and makes it measurable. A model
must:

1. **Recognize** a traffic authority — a police officer, a construction flagger,
   an emergency vehicle, or a hazard-backed civilian warning — as distinct from
   ordinary road users.
2. **Prioritize** it correctly against the signal/road under the hierarchy
   **safety > authorized human command > traffic-control device**.
3. **Act** on it (STOP / PROCEED / HOLD / YIELD / DETOUR), collision-free.
4. **Not** obey a gesture that carries no authority — *false-obedience
   avoidance* — and **attribute** a directive to the correct target (ego vs an
   adjacent lane).

It evaluates this on two tracks: **closed-loop control in CARLA (Track-B)** and
**visual decision QA (Track-C)** — see [tracks.md](tracks.md) — with **strict,
telemetry-grounded, oracle-calibrated** scoring, an authority-STOP subset, and a
per-**conflict-type** failure profile (override / stressed-override / validity /
conflict / scene / safety). The scenario set is principled, not ad-hoc (seven
authority-aware reasoning principles; see
[design_principles.md](design_principles.md),
[scenario_taxonomy.md](scenario_taxonomy.md), and the full design defense in
[scenario_design_justification.md](scenario_design_justification.md)).

## What MARSHAL does *not* claim

MARSHAL is **not** a replacement for driving-competence benchmarks. It does not
evaluate global navigation, long-horizon planning, or full perception; it
deliberately isolates the local authority-conflict decision so success/failure is
attributable to authority reasoning rather than to driving skill (see
[design_principles.md](design_principles.md) §5).

## Citations (verified links)

- **CARLA** — Dosovitskiy et al., *CARLA: An Open Urban Driving Simulator*, CoRL 2017
  ([arXiv:1711.03938](https://arxiv.org/abs/1711.03938)); Leaderboard:
  [leaderboard.carla.org](https://leaderboard.carla.org/).
- **Bench2Drive** — Jia et al., *Towards Multi-Ability Benchmarking of Closed-Loop
  End-to-End Autonomous Driving*, NeurIPS 2024 (44 interactive scenarios;
  [poster](https://neurips.cc/virtual/2024/poster/97436)).
- **nuPlan** — Caesar et al., *nuPlan: A closed-loop ML-based planning benchmark*,
  ([arXiv:2106.11810](https://arxiv.org/abs/2106.11810)).
- **NAVSIM** — Dauner et al., NeurIPS 2024 — non-reactive real-log simulation, PDMS.
- **WOD-E2E** — Xu et al., *Waymo Open Dataset for End-to-End Driving in Challenging
  Long-tail Scenarios*, CVPR 2026 — open-loop RFS; names closed-loop as its own
  limitation.
- **HABIT** — *Human Action Benchmark for Interactive Traffic in CARLA*
  ([arXiv:2511.19109](https://arxiv.org/abs/2511.19109)) — real human *motions*
  (reaction/collision), no authority-obedience ground truth.
- **DriveLM** — Sima et al., *Driving with Graph Visual Question Answering*, ECCV 2024
  ([arXiv:2312.14150](https://arxiv.org/abs/2312.14150)); **DriveBench**
  ([drive-bench.github.io](https://drive-bench.github.io/)) — VLM QA reliability,
  no authority-conflict axis.
- **Officer-gesture perception** — *Traffic Control Gesture Recognition for
  Autonomous Vehicles* (TCG dataset, [arXiv:2007.16072](https://arxiv.org/abs/2007.16072))
  and successors (e.g. [Sci. Reports 2025](https://www.nature.com/articles/s41598-025-02833-y))
  — gesture *classification* only; no validity/override/closed-loop compliance.
- **Deployment motivation** — CNN 2026 investigation of robotaxi emergency-scene
  failures; NHTSA scrutiny (Axios, 2026-07-15); Phoenix officer-direction incident
  (NBC). Full links: [scenario_design_justification.md](scenario_design_justification.md).

- [ ] Camera-ready: confirm each "limitation" wording against the cited source
  (no overclaiming); add LingoQA / Reason2Drive / DeepAccident if the paper's
  related-work section ends up covering them.
