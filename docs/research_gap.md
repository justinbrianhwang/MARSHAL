# Research Gap — why authority-aware reliability is unmeasured

This document sharpens the **research gap** MARSHAL addresses. It is the missing link
in the paper's logic:

> **Problem → Research Gap → Why existing benchmarks fail → Why authority conflict
> matters → MARSHAL → Evaluation.**

The [problem statement](problem_statement.md) says *what* MARSHAL measures; this
document argues *why that measurement does not yet exist* — at the level of both
**benchmarks** and **metrics**.

## The gap in one sentence

> **No existing autonomous-driving benchmark stages a conflict between a legally
> valid human authority and the traffic signal, and no existing metric scores whether
> the vehicle resolved that conflict correctly — so "authority-aware reliability" is
> currently unmeasured.**

The gap is one of **measurability**, not of any single hard scenario. Existing
benchmarks implicitly assume the **signal and the correct action always agree**, so a
system that only ever obeys the signal looks perfect — and no one can tell whether it
would obey the *correct authority* when the two disagree, because that case is never
presented and never scored. Closing the gap means making that capability observable.

## 1. Why existing *benchmarks* cannot measure it

Existing benchmarks are built on a **coherent-world assumption**: the signal, the
road rules, and the correct action all agree. Their scenarios are designed to test
*competence within that coherent world*, so the authority conflict simply never
appears.

| Benchmark family | What it stages | Why authority conflict is absent |
|---|---|---|
| CARLA Leaderboard, Bench2Drive | routes + driving skills; agents **obey** traffic-control devices | a human that *overrides* the device is out of scope; obeying the light is always correct |
| nuPlan | closed-loop planning vs logged human driving | logs contain ordinary driving; no staged officer-vs-signal conflict |
| nuScenes / Waymo Open / Argoverse | perception + forecasting on real logs | upstream of the decision; no action-level authority arbitration |
| DriveLM / DriveVLM / LingoQA | language/VQA about scenes | ask *questions*; do not run a closed-loop episode where the agent must **act** on contested authority |
| DeepAccident / CommonRoad (hazard sets) | physical hazard avoidance | treat everything as an obstacle; a *human directing traffic* is not a hazard to avoid but an authority to obey |

The common thread: **the conflict is never presented**, so no amount of running these
benchmarks reveals whether an agent handles it. Absence of the scenario is absence of
the measurement.

## 2. Why existing *metrics* cannot measure it

Even if the scenario appeared, the standard metrics would not score the right thing.
They are **authority-agnostic** — they reward driving outcomes, not authority
resolution.

| Metric | What it rewards | Why it cannot capture authority resolution |
|---|---|---|
| Route completion / success rate | reaching the goal | a model that *ignores* an officer's STOP and drives on scores **higher**, not lower |
| Infraction / red-light penalty | obeying traffic-control devices | penalizes the **correct** action in `red_proceed` (officer waves you through a red) |
| Collision / near-miss rate | physical safety | passes a model that never perceives the officer at all, as long as it doesn't crash |
| Planning error (L2 / minADE vs log) | matching logged trajectories | there is no logged trajectory for "obey this officer"; the ground truth is the *authority*, not a path |
| Comfort / smoothness | trajectory quality | orthogonal to obeying the right authority |

The decisive point: for the conflict cases, **maximizing the standard metrics can
require the wrong behavior.** Obeying a red light is normally correct and is exactly
wrong in `red_proceed`; avoiding an "obstacle" is normally correct and is exactly
wrong when the "obstacle" is an officer you must approach and obey. You cannot fix
this by re-weighting existing metrics, because the *sign of correctness flips with the
authority context* — which those metrics do not observe.

This is why MARSHAL defines **authority-conditioned** metrics (AOC, FOA, TAA, APR,
DRM, RHC, …): each is scored **relative to the privileged correct action for that
scenario**, so "obeyed" is only counted as good when obeying was actually right.

## 3. Why a *new evaluation dimension* is needed (not just new numbers)

Driving competence and authority-aware reliability are **orthogonal axes**:

- A model can be **SOTA on competence** (route completion, low infractions, smooth
  planning) and **untested on authority** — the two do not imply each other.
- The failure is **safety-critical and legally grounded**: US traffic law makes an
  authorized officer's/flagger's directions supersede the signal (see
  [legal_grounding.md](legal_grounding.md)), so getting it wrong is not a style
  preference but a violation.
- The failure is **invisible without the second axis**: a benchmark that only reports
  competence will rank an authority-blind model above a compliant one whenever
  ignoring the human happens to complete the route faster.

Measuring a second, orthogonal axis is precisely what a **new evaluation dimension**
means — and why the contribution is an *evaluation dimension + harness*, not a new
dataset or a re-weighting of old scores.

## 4. What the gap demands (and MARSHAL provides)

Closing the gap requires three things that no prior benchmark supplies together:

1. **Staged conflicts** — scenarios where a valid human authority contradicts the
   signal, *and* decoy scenarios where an actor has no authority (so "verify," not
   just "obey," is tested). → MARSHAL's 21 scenarios with the valid/invalid split.
2. **A privileged correct-action oracle** — a ground-truth "who to obey" for each
   scenario, since there is no logged trajectory to regress to. → MARSHAL's oracle
   (calibration reference).
3. **Authority-conditioned scoring** — metrics whose notion of "correct" depends on
   the authority context. → MARSHAL's contextual metric suite + strict,
   telemetry-grounded verdict.

## 5. Honest bound on the claim

The claim is about an **evaluation gap**, stated conservatively: individual pieces
exist in the literature (officer-gesture recognition datasets, rule-conditioned
planning, VQA about scenes), but **none isolates authority-conflict resolution as a
closed-loop, scored evaluation dimension.** MARSHAL is an **initial implementation**
of that dimension — single-map, single-seed, partial weighted score — so the
contribution is the *framing and the harness*, with the coverage limits stated in
[what_is_marshal.md](what_is_marshal.md#current-status-honest-scope). A fuller,
citation-backed positioning against specific prior work is tracked as the survey
deliverable.

---

*See also:* [problem_statement.md](problem_statement.md) ·
[long_tail_definition.md](long_tail_definition.md) ·
[marshal_storyline.md](marshal_storyline.md) (how this gap threads the paper).
