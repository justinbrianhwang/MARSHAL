# MARSHAL Design Principles

This document defines the design philosophy of MARSHAL: what the benchmark is
for, what counts as authority, how scenarios are selected, how reasoning tiers
are defined, and — just as importantly — what MARSHAL does *not* claim.

> MARSHAL is a CARLA-based authority-aware reasoning benchmark that evaluates
> whether autonomous driving agents and VLM decision systems can recognize,
> prioritize, and act on human or contextual traffic authority when it conflicts
> with ordinary road signals.

## 1. Benchmark Scope

MARSHAL evaluates **local authority-aware driving decisions**, not full global
navigation.

> MARSHAL targets local driving scenes where ordinary traffic-control cues
> conflict with human or contextual authority.

Each episode is a short, self-contained closed-loop scene: a drivable lane with a
brief run-up to a real traffic light, where an officer, flagger, ambulance, or
hazard context takes over from the signal. The model under test does not plan a
route across the city; it must answer a single, local question — **"who should
the vehicle obey right now, and what should it do?"**

MARSHAL is **not** intended to cover all possible autonomous-driving corner
cases. It deliberately isolates the authority-conflict decision so that success
or failure can be attributed to authority reasoning rather than to perception,
mapping, or long-horizon planning.

## 2. What Counts as Authority?

Authority in MARSHAL is **not** determined by the gesture alone. The same raised
hand can be a binding command or noise, depending on **who** performs it and
**in what scene context**. MARSHAL distinguishes three categories:

- **Formal human authority** — an actor whose role grants legal traffic-control
  power: a uniformed **police officer**, a **construction flagger**. Their
  directions can supersede ordinary traffic-control devices.
- **Contextual authority** — authority that arises from the *scene*, not from a
  uniform: an **emergency vehicle** (ambulance) requiring yield, a **visible
  accident context**, or a **civilian warning in a hazard scene** (e.g. a
  bystander at a crash waving traffic to slow or detour). The instruction is
  credible because the hazard context supports it.
- **Non-authority** — a **civilian gesture without supporting hazard context**
  (e.g. a pedestrian waving "go" at a red light with nothing wrong on the road).
  A correct agent must *not* obey it.

The central design consequence: **authority is a function of (actor role, scene
context, gesture) — never of the gesture in isolation.** The contrast between
`unauthorized_go` (civilian "go", no hazard → ignore) and the planned
`civilian_warning_accident` (civilian "detour", visible crash → obey) is the
cleanest expression of this principle.

## 3. Scenario Selection Principles

The scenario set is built to cover seven authority-aware reasoning principles.
Every scenario corresponds to at least one of them.

- **P1 — Signal override.** A human authority contradicts a traffic light (or a
  signal is absent and a human governs flow).
- **P2 — Authority verification.** The model must distinguish authorized from
  unauthorized actors.
- **P3 — Target attribution.** The model must determine whether a gesture applies
  to the ego vehicle or to another lane/target.
- **P4 — Contextual hazard reasoning.** The model must consider visible hazards —
  pedestrians, crashes, fallen people, emergency vehicles — and act on them.
- **P5 — Temporal reasoning.** The model must remember directives over time
  (a directive given earlier still binds, or has been withdrawn).
- **P6 — Rule hierarchy.** When cues conflict, the model must prioritize safety
  and the legal hierarchy (safety > authorized human command > device).
- **P7 — Ambiguity handling.** If the instruction is genuinely ambiguous, the
  model should choose a safe, conservative action.

## 4. Grouping: the authority-conflict typology (tiers retired)

Scenarios are grouped by the **structure of the conflict**, not by a designed
difficulty level (`CONFLICT_TYPE` in `marshal_bench/criteria/marshal_metrics.py`):

- **override (6)** — a valid human authority contradicts or replaces the device;
- **stressed-override (5)** — override under a crosscutting stressor
  (occlusion, ambiguity, target attribution, temporal memory/escalation);
- **validity (3)** — is the commander legitimate at all?;
- **conflict (2)** — two directives disagree;
- **scene (2)** — the scene itself carries the authority (no human directs);
- **safety (3)** — safety outranks every directive.

An earlier low/mid/high *reasoning-tier* ladder was retired: measured over three
full sweeps, tier labels do not track empirical difficulty (Spearman ρ = −0.22,
12/13 models violate tier monotonicity). What predicts difficulty is the required
maneuver and the conflict structure — the typology above. Full evidence and the
decision record: [taxonomy_decision.md](taxonomy_decision.md).

## 5. What MARSHAL Does Not Claim

- MARSHAL is **not** a full real-world legal simulator. Authority precedence is
  modeled on common US traffic-control policy (see
  [legal_grounding.md](legal_grounding.md) and
  [marshal_grounding.md](marshal_grounding.md)), not a jurisdiction-exact legal
  engine.
- MARSHAL is **not** a global route-planning benchmark. It scores local
  authority decisions, not navigation quality.
- MARSHAL does **not** claim that VLMs are complete driving systems by
  themselves.
- **Track-C (Visual Decision QA)** evaluates decision reasoning from visual
  observations — *not* closed-loop vehicle control. A VLM's Track-C score is a
  measure of whether it reads authority from images, not a driving score, unless
  it is wrapped as a controller and evaluated under Track-B. See
  [tracks.md](tracks.md) and
  [track_c_visual_decision_qa.md](track_c_visual_decision_qa.md).
