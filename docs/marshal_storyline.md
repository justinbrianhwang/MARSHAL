# MARSHAL Storyline — the one narrative for paper, README, and slides

**Purpose.** Keep every surface (paper Introduction, Related Work, Method,
Evaluation; the README; the slide deck) telling the **same story with the same
message**. When wording drifts across these, reviewers and the advisor lose the
thread — this doc is the single source of narrative truth. It is a *storyline*, not a
results document; numbers live in the README and the sweep outputs.

## The thesis (one sentence, memorize this)

> **A vehicle can be state-of-the-art at driving and still fail every case where a
> legally valid human authority contradicts the traffic signal — MARSHAL is the
> benchmark and evaluation framework that makes this second, orthogonal axis
> (authority-aware reliability) measurable.**

## The narrative chain (do not skip a link)

```
Problem  →  Research Gap  →  Why existing benchmarks fail  →
Why authority conflict matters  →  MARSHAL  →  Evaluation
```

Every surface should be traceable to this chain. The most common failure mode is
jumping *Problem → MARSHAL* and skipping the gap — which is exactly the weakness the
2026-07-01 feedback flagged. The gap is the load-bearing link
([research_gap.md](research_gap.md)).

## Section-by-section (paper)

### Introduction
- **Hook (the question):** *when a human directing traffic contradicts the signal,
  who should the vehicle obey?* Ground it immediately in law (an officer's/flagger's
  direction supersedes the device — [legal_grounding.md](legal_grounding.md)).
- **The tension:** existing benchmarks assume a coherent world (signal = correct
  action); real driving has authorized humans who invert that.
- **The claim (aggressive but honest):** driving competence ≠ authority-aware
  reliability; a SOTA agent can fail *every* authority conflict, and current
  benchmarks/metrics cannot see it ([problem_statement.md](problem_statement.md#why-marshal-matters)).
- **Contributions (3 bullets):** (1) a new **evaluation dimension** — authority-aware
  reliability; (2) **MARSHAL**, a closed-loop benchmark + framework (21 scenarios,
  privileged oracle, authority-conditioned metrics, three tracks); (3) an **initial
  study** across 14 controllers exposing where each fails — with caveats stated up
  front.
- **Framing discipline:** call it a *benchmark + evaluation framework*, never a model
  or planner.

### Related Work
- **Credit** (what these do well, so we're fair): CARLA Leaderboard / Bench2Drive
  (closed-loop skill), nuPlan (planning), perception–forecasting sets, DriveLM /
  DriveVLM (language reasoning), hazard/corner-case sets.
- **Critique** (the wedge, one clean sentence each): none stage a *valid human
  authority vs signal* conflict, and their metrics reward the wrong action in those
  cases (obey-the-light, avoid-the-"obstacle"). Position authority conflict as a
  **semantic long-tail** — *our* defined term
  ([long_tail_definition.md](long_tail_definition.md)).
- **Do not** overclaim novelty of components (gesture recognition, rule-conditioned
  planning exist) — claim novelty of the *isolated, scored, closed-loop evaluation
  dimension*.

### Method (MARSHAL design)
Emphasize, in this order:
1. **The scenario contract** — 21 authority-conflict episodes with a deliberate
   **valid/invalid split** (must *verify*, not just *obey*)
   ([scenarios.md](scenarios.md)).
2. **The privileged oracle** — the correct-action ground truth (there is no logged
   trajectory to regress to); the calibration reference the scorer is tuned against.
3. **Authority-conditioned scoring** — strict, telemetry-grounded verdicts +
   contextual metrics whose "correct" flips with authority context (why standard
   metrics can't be reused — [research_gap.md](research_gap.md#2-why-existing-metrics-cannot-measure-it)),
   aggregated into R1–R9 and the continuous **MARSHAL-Graded** score.
4. **Three tracks** — same scenario, oracle / closed-loop E2E / visual-QA regimes.
- **Honesty inline:** name what is *not yet* instrumented (R4–R6, R8–R9) rather than
  implying full coverage.

### Evaluation
- **Headline message:** lead with **MARSHAL-Graded** (engagement-gated), because the
  raw pass-count and the narrow authority-STOP subset both reward stopping and the
  strong agents share a **stop-bias**. On graded, the per-tick VLM leads all
  non-privileged agents, just ahead of the LiDAR E2E — but the margin is narrow and
  *no learned model approaches the oracle*.
- **The discriminators:** the three contextual-DETOUR scenarios are **oracle-only** —
  the clearest evidence that this is unsolved.
- **The failure taxonomy** (authority-blindness, false-obedience, maneuver-gap, …)
  from [scenarios.md](scenarios.md#failure-mode-taxonomy) — shows *how* models fail,
  not just that they do.
- **Guardrail:** every claim carries the honest caveats — single-seed, partial score,
  the stop-bias confound. Do not oversell "VLM > E2E"; sell "the axis is real and
  unsolved, and here is a principled way to measure it."

### Discussion / Future (keep small)
- Multi-town, multi-seed, instrumenting the remaining requirements.
- **Differentiable MARSHAL** is mentioned **only as an exploratory future direction**,
  never as a current contribution or headline.

## Consistent talking points (reuse verbatim)

- **Elevator (1 line):** "MARSHAL measures whether a self-driving car obeys the right
  authority when a human directing traffic contradicts the light — an axis today's
  benchmarks don't test."
- **Definition to reuse (authority-aware reliability):** "the degree to which an
  agent consistently recognizes, verifies, prioritizes, and follows legitimate
  traffic authority under conflicting traffic cues."
- **What we solve (the sharp version):** "MARSHAL does not solve any one authority
  conflict — it makes **authority-aware reliability measurable**; without it, there is
  no way to know whether an AV will obey the correct authority when it matters."
- **The novelty line:** "Existing benchmarks assume the signal and the correct action
  agree; MARSHAL is the first to evaluate the case where **signal ≠ correct action**."
- **1 paragraph:** the thesis above + the three contributions + the honest scope.
- **The figure everyone should remember:** *Driving performance ≠ authority-aware
  reliability* ([problem_statement.md](problem_statement.md#why-marshal-matters)).
- **The other figure:** the **decision pipeline** — existing *Signal → Decision* vs
  MARSHAL *Signal → Authority Verification → Priority Resolution → Decision*
  ([problem_statement.md](problem_statement.md)).

## What NOT to claim (reviewer guardrails)

- "MARSHAL is a driving model / planner / LLM." → It is a benchmark + framework.
- "VLMs solve authority reasoning." → They lead a narrow margin on graded; all
  learned models trail the oracle; strong agents are stop-biased.
- "Full requirement coverage / validated at scale." → Initial implementation:
  single-seed, partial MARSHAL Score, R4–R6/R8–R9 not yet instrumented.
- "Semantic long-tail is an established term." → It is our defined shorthand.
- "Differentiable MARSHAL is a contribution." → Exploratory future only.

---

*Anchor docs:* [what_is_marshal.md](what_is_marshal.md) ·
[problem_statement.md](problem_statement.md) · [research_gap.md](research_gap.md) ·
[long_tail_definition.md](long_tail_definition.md) · [scenarios.md](scenarios.md).
