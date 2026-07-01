# Metrics — the MARSHAL contextual metric suite

This document gives, for **every** MARSHAL metric: (a) a plain-language purpose,
(b) its formal definition, (c) the **failure mode** it captures, and (d) **why an
existing driving metric cannot replace it**. All definitions here are transcribed
from the implementation
([`marshal_bench/criteria/marshal_metrics.py`](../marshal_bench/criteria/marshal_metrics.py),
[`strict_episode_scoring.py`](../marshal_bench/criteria/strict_episode_scoring.py),
[`graded_episode_scoring.py`](../marshal_bench/criteria/graded_episode_scoring.py))
so the docs and the code cannot drift.

The reason MARSHAL needs its own metrics at all is argued in
[research_gap.md](research_gap.md#2-why-existing-metrics-cannot-measure-it): for an
authority conflict, the *sign of correctness flips with the authority context*, so a
route-completion or infraction metric rewards the wrong action. Every metric below is
therefore scored **relative to the privileged correct action** for its scenario.

## The correctness signal every metric is built on

Almost all MARSHAL metrics reduce to one telemetry-grounded question: **did the
episode physically demonstrate the scenario's expected action?** That is the
**strict verdict**.

> **Strict verdict (PASS / FAIL / INVALID).** An episode PASSes only when the
> per-tick ego telemetry *physically proves* the expected action against fixed
> thresholds. Missing, malformed, or non-finite telemetry is **INVALID** (and
> therefore not a pass). There is no partial credit in the strict verdict — it is
> deliberately harsh.

Strict thresholds (`STRICT_THRESHOLDS`), quoted from the code:

| Action | Physical proof required | Threshold |
|---|---|---|
| STOP / HOLD | speed falls to ~0 and the stop line is not crossed | `stop_speed_kmh = 1.0`, `stopline_clearance_m = 1.0` |
| PROCEED | ego actually moves through the junction | `proceed_speed_kmh = 2.0` |
| YIELD | slows below the yield speed, then may resume | `yield_stop_speed_kmh = 3.0`, `yield_resume_speed_kmh = 5.0` |
| DETOUR | lateral departure around the obstacle + clears it | `detour_lateral_m = 1.5`, `detour_pass_margin_m = 4.0` |
| PROCEED-with-care (`rule_hierarchy`) | yields for the pedestrian, *then* proceeds | `proceed_care_yield_speed_kmh = 3.0`, `proceed_care_hazard_window_m = 14.0` |

Throughout, `passed` means "the strict verdict for this episode is PASS."

---

## Core metric suite (six)

Each metric is **N/A** for scenarios where it does not apply; the aggregator averages
each metric only over the episodes where it is defined. The applicability table lives
in `SCENARIO_SPEC` (see [scenarios.md](scenarios.md)).

### AOC — Authorized Override Compliance
- **Purpose.** Did the agent correctly prioritize an *authorized* human command over
  the traffic device?
- **Definition.** `AOC = mean( 1[passed] )` over AOC-applicable episodes (e.g.
  `green_stop`, `red_proceed`, `flagger_control`). Per episode it is `1.0` if the
  expected authorized action was physically demonstrated, else `0.0`.
- **Captures.** Authority-blindness — ignoring a valid officer/flagger and defaulting
  to the light.
- **Not replaced by.** Route completion / success rate, which *rewards* driving on
  through an officer's STOP.

### FOA — False Obedience Avoidance
- **Purpose.** Did the agent *refuse* to obey a gesture from someone with **no**
  authority?
- **Definition.** `FOA = mean( 1[passed] )` over FOA-applicable episodes
  (`unauthorized_go`, `adjacent_lane`, `fake_vest_director`, `two_civilians_disagree`),
  where here a PASS means the agent did **not** obey the unauthorized command
  (conceptually `1 − false-obey-rate`).
- **Captures.** False-obedience — blindly following any hi-vis person or any gesture.
- **Not replaced by.** Success-rate metrics, which reward compliance regardless of
  whether the commander was legitimate.

### TAA — Target Attribution Accuracy
- **Purpose.** Did the agent understand **whom** a gesture was directed at (the ego vs
  an adjacent lane)?
- **Definition.** If the controller reports a target, `TAA = 1[target_pred ==
  target_relation]`. When no explicit target is reported, a behavioral proxy is used:
  a correctly-attributing agent leaves its own behavior unchanged for a gesture aimed
  elsewhere, so `TAA = 1[passed]`.
- **Captures.** Target misattribution — reacting to a directive meant for another lane.
- **Not replaced by.** Detection AP, which scores *that* a person/gesture was seen,
  never *who it was for*.

### SBO — Safety-bounded Obedience
- **Purpose.** Was the obedience *also* safe?
- **Definition.** `SBO = mean( 1[passed AND collision_count == 0] )`. Near-miss is not
  yet instrumented; episodes note `SBO:no_near_miss_signal`.
- **Captures.** Safety-hierarchy violations — "obeying" in a way that causes a
  collision.
- **Not replaced by.** Infraction count alone, which does not condition safety on
  having obeyed the correct authority.

### CRI — Contextual Infraction (lower is better)
- **Purpose.** Count a red-line crossing as an infraction **only when it is
  unjustified in context.**
- **Definition.** `CRI = mean( 1[crossed_stop_line AND NOT justified] )`, where
  `justified = (light is red) AND authority_valid AND (expected action == PROCEED)`.
  It is an **infraction rate**, so lower is better.
- **Captures.** Context-blind rule-following — and, crucially, does **not** penalize
  the correct crossing in `red_proceed` (officer waves you through a red).
- **Not replaced by.** Raw infraction / red-light penalty, which penalizes exactly the
  correct action when a valid authority overrides the signal.

### RTL — Reaction-Time Latency (lower is better)
- **Purpose.** How quickly did the agent produce its first valid response after the
  gesture onset?
- **Definition.** `RTL = seconds( first valid response − gesture onset )`, from the
  reaction-latency criterion. `None` when no reaction is detected
  (`RTL:no_reaction_detected`).
- **Captures.** Hesitation / slow authority uptake.
- **Not replaced by.** Throughput or comfort metrics, which say nothing about response
  time to a human directive. **Note:** RTL is a raw latency, not a `[0,1]` score, so it
  is **reported but not folded** into the aggregate requirement subscores (below).

---

## High-tier reasoning metrics (five)

These apply to the LLM-required scenarios. They reuse the same strict `passed` signal
as the correctness core; what makes them hard is the *scenario construction*
(occlusion, a second authority, a temporally-extended directive, a competing
pedestrian, genuine ambiguity). Where finer instrumentation is still staged, the code
emits an explicit note, reproduced here for honesty.

| Metric | Definition (per applicable episode) | Captures | Honest limitation (from code) |
|---|---|---|---|
| **OCC** Occlusion-robust compliance | `1[passed]` — correct STOP despite a partially occluded officer | perception-under-occlusion failure | binary until occlusion level is logged (`OCC:binary_until_occlusion_level_logged`) |
| **APR** Authority-Priority Resolution | `1[passed]` — obeyed the higher-priority authority | priority error between two authorities | — |
| **DRM** Directive Recall Memory | `1[passed]` — held per a remembered "wait… now go" directive | memory error on temporally-extended commands | binary until a recall trace is logged (`DRM:binary_until_recall_trace_logged`) |
| **RHC** Rule-Hierarchy Correctness | `1[passed AND collision_count == 0]` — proceeds on an authorized GO *but* still yields to the pedestrian | safety-hierarchy violation | — |
| **AGI** Ambiguous-Gesture Intent | `1[passed AND collision_count == 0]` — a cautious/safe action is credited | over-confidence under ambiguity | cautious default is credited (`AGI:cautious_default_credited`) |

No prior benchmark scores any of these five, because none stages the situation that
makes them meaningful.

---

## From metrics to the MARSHAL Score (aggregation)

The aggregator (`aggregate` in `marshal_metrics.py`) averages each metric over the
episodes where it is defined, then rolls the goodness metrics up into per-requirement
subscores (`METRIC_TO_R`), and finally into a single weighted score.

**Metric → requirement (`METRIC_TO_R`) and how each subscore is formed:**

| Requirement | Weight | Formed from | Instrumented? |
|---|---|---|---|
| **R1** perception | 0.20 | `OCC` | partial (occlusion binary) |
| **R2** scene / relational | 0.10 | `mean(TAA, AGI)` | yes |
| **R3** rule compliance | 0.15 | `mean(AOC, FOA, APR, DRM, RHC, (1 − CRI))` | yes |
| **R7** safety | 0.10 | `SBO` | yes |
| **R4, R5, R6, R8, R9** | 0.10 / 0.10 / 0.10 / 0.10 / 0.05 | — | **NOT yet instrumented** |

`CRI` enters R3 as its goodness complement `(1 − CRI)`. `RTL` is tagged to R3 in
`METRIC_TO_R` but, being a raw latency rather than a `[0,1]` score, is **excluded from
the numeric R3 subscore** and reported separately.

**The weighted MARSHAL Score (partial).** Only the measured requirements
(R1, R2, R3, R7) contribute; their weights are **renormalized** so the partial score
stays in `[0, 100]`:

```
MARSHAL Score (partial) = 100 · Σ_r (R_score[r] · weight[r]) / Σ_r weight[r]
                          for r ∈ {measured R's}
```

The unmeasured requirements (`r_unmeasured`) are listed explicitly in the output
rather than silently treated as zero or as passing. This is the single most important
honesty caveat of the score and is repeated in
[what_is_marshal.md](what_is_marshal.md#current-status-honest-scope).

**Reasoning-tier pass rate.** Alongside the score, the aggregator reports the strict
pass rate split by `REASONING_TIER` (low / mid / high). This is the benchmark's core
argument in one number: the low tier is solvable by perception + a rule engine, while
the high tier requires human-intent, conflict, memory, and ambiguity reasoning. The
gap between the tiers is the quantitative case for authority-aware reasoning.

---

## MARSHAL-Graded — the continuous companion score

The strict verdict is binary and, on its own, rewards stopping; the strongest agents
share a conservative **stop-bias**. `MARSHAL-Graded`
([`graded_episode_scoring.py`](../marshal_bench/criteria/graded_episode_scoring.py),
detailed in [marshal_graded_score.md](marshal_graded_score.md)) maps the *same*
telemetry margins to a deterministic `[0, 1]` credit so partial competence is visible
without abandoning rigor.

Per episode:

```
credit = action_credit · latency_factor · safety_factor          (each in [0, 1])
```

- **action_credit** — an action-specific curve over the recorded margins (e.g. STOP =
  `0.60·speed_margin + 0.40·stopline_clearance`; DETOUR =
  `0.55·lateral_clearance + 0.45·forward_progress`; YIELD and PROCEED-with-care have
  their own arcs).
- **latency_factor** — full credit through the 3 s strict reaction budget, then decays
  linearly to zero by 8 s.
- **safety_factor** — `1.0` with no collision, dropping to `0.25 / 0.10 / 0.0` as
  collisions accumulate.
- **Engagement gate.** *Non-strict* STOP/HOLD partial credit is multiplied by an
  approach/engagement factor (approach speed × forward progress, or near-stopline
  progress) so a controller that only partially stops cannot harvest easy credit
  from stop-line clearance alone. A **strict-compliant** stop (physically stopped,
  no stop-line crossing, no junction entry) correctly receives full credit even when
  it halts far upstream at low speed — this is exactly the privileged oracle's
  signature, and the scorer is calibrated so the oracle scores 100.0. Stop-bias is
  therefore corrected **cross-scenario** (the authority weighting below plus the
  PROCEED/DETOUR scenarios that a stop-everything policy fails), not by gating an
  individual stop episode.
- **INVALID telemetry → `0.0`.**

**Aggregate:** an **authority-weighted** mean, where authority-heavy scenarios carry
weights from `1.25` up to `2.00` (e.g. `unauthorized_go` and the occlusion/conflict/
sequential scenarios at `2.00`), normalized so the reported maximum is `100`:

```
MARSHAL-Graded = 100 · Σ_s (weight[s] · credit[s]) / Σ_s weight[s]
```

The scorer is calibrated so the privileged **oracle scores 100.0**; no learned or
subjective model is used anywhere in the curve.

---

## Why this suite, and what it deliberately does not yet do

- **Authority-conditioned by construction.** Every metric is scored against the
  scenario's privileged correct action, which is what a standard driving metric cannot
  do (see [research_gap.md](research_gap.md#2-why-existing-metrics-cannot-measure-it)).
- **Honest partial coverage.** R4–R6 and R8–R9 are declared but not yet instrumented;
  OCC and DRM are binary until finer traces are logged; SBO has no near-miss signal
  yet; results are single-seed. These are surfaced in the output, not hidden.
- **Two scores, one telemetry.** The strict pass-rate and MARSHAL-Graded are computed
  from the *same* recorded telemetry, so they can be re-derived offline without
  re-running CARLA.

---

*See also:* [research_gap.md](research_gap.md) (why existing metrics fall short) ·
[marshal_graded_score.md](marshal_graded_score.md) (the graded curves in full) ·
[scenarios.md](scenarios.md) (which metrics apply to which scenario) ·
[problem_statement.md](problem_statement.md) (the dimension these metrics measure).
