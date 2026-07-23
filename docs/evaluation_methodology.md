# Evaluation methodology — what each score means, and why the weights are defensible

*The 2026-07-17 meeting asked for (a) a justification of the scenario weighting,
(b) a sensitivity analysis, and (c) a clear statement of how MARSHAL-Graded, strict
Pass/Fail, and the Failure Profile relate. This document is that defense. All numbers
are reproducible offline: `scripts/_weight_sensitivity.py`,
`scripts/_failure_profiles.py`, `scripts/_analyze_tiers.py` over
`outputs/multirun/run_{1,2,3}.json`.*

## 1. Three views, three questions

MARSHAL reports three complementary readings of the *same* telemetry — none is
redundant, because each answers a different question:

| View | Question it answers | Form | Analogy |
|---|---|---|---|
| **Strict Pass/Fail** | *Did the vehicle do what the law required?* | binary verdict per episode, oracle-calibrated (oracle = 21/21) | certification test |
| **MARSHAL-Graded** | *How competently did it do it?* | continuous 0–100 from physical margins (stop distance, residual speed, latency, clearance), authority-weighted, engagement-gated | graded exam |
| **Failure Profile** | *What, specifically, does it fail at?* | failure rate per reasoning principle and per required action, plus a stop-bias index | diagnostic report |

They are ordered by information content: Graded **refines** Pass/Fail (a near-miss
and a blow-through are different failures); the Profile **explains** both (e.g.
Qwen2.5-VL's profile shows a +0.611 stop-bias index — 83% pass on STOP-expected
scenarios vs 22% on non-STOP — which is invisible in either aggregate). A model
should be *selected* on Graded, *certified* on Pass/Fail, and *debugged* on its
Failure Profile.

## 2. Why scenario weights exist, and what they encode

`SCENARIO_AUTHORITY_WEIGHTS` up-weights (1.5–2.0×) the scenarios where a human
directive **overrides** another signal — the benchmark's subject — relative to
plain hazard responses. Two justifications:

- **Normative**: the override cases are where the legal hierarchy
  (safety > authorized human > device) actually binds; a hazard stop is ordinary
  defensive driving that every benchmark already measures.
- **Diagnostic**: the override cases are where the officer-blind baseline and the
  oracle *diverge* — they carry the benchmark's discriminative signal. Weighting them
  is weighting the question.

The aggregate normalizes by the weight sum, so weights redistribute emphasis without
inflating the scale (max stays 100; the oracle stays the calibration anchor).

## 3. Sensitivity analysis: the conclusions do not depend on the weights

1,000 random perturbations (every weight independently scaled by U(0.75, 1.25),
seeded) plus a uniform-weights ablation and 42 one-at-a-time ±25% probes
(`outputs/weight_sensitivity.json`):

| Probe | Result |
|---|---|
| Uniform weights (all 1.0) | **ranking identical** to the shipped weights, all 14 models |
| Random ±25% × 1000 | Kendall τ vs current ranking **0.988 ± 0.013** |
| Top non-privileged model changes | **0.0%** of draws (Qwen2.5-VL always leads) |
| Oracle rank 1 | **100%** of draws |
| Worst one-at-a-time ±25% effect | 1 rank (AIM, `green_stop` +25%) |
| TransFuser ↔ InterFuser flips | 0.9% of draws — consistent with our reporting of the pair as a **statistical tie** |
| Most weight-sensitive adjacent pair | AIM ↔ baseline, 47.3% — their means differ by 0.1 points (24.0 vs 23.9), so this order was never claimed as meaningful |

**Reading:** the weights express *emphasis*, not the *ranking* — every ordering
claim in the README survives removing the weights entirely. The one genuinely
weight-sensitive comparison (AIM vs baseline) is a pair we already report as
indistinguishable. Caveats: this analysis perturbs weights only (measurement noise is
handled separately by the 3-run mean ± std —
[reproducibility.md](reproducibility.md)); Track-C cells are single-sample.

## 4. Known interactions, stated honestly

- **Weighting cannot manufacture discrimination** — with uniform weights the
  benchmark ranks models the same; the discrimination comes from the scenarios, not
  the weights. This is the desired property.
- **Both metrics carry an engagement requirement, in different forms.** The strict
  binary fails an ego that never engaged the staged scene at all (speed + progress),
  and — since the round-7 adversarial review — an ego that never came within the
  engagement radius (15 m) of the stop line *or* the directing officer ("park
  anywhere short" is not a compliant stop; the privileged oracle, which brakes on
  the true stop line's envelope, is unaffected). The graded score's engagement gate
  is continuous — see [marshal_graded_score.md](marshal_graded_score.md) for why
  (a hard binary gate broke oracle calibration). Two further round-7 strict rules:
  a *hold* requires a settled dwell (≥ 2 s within 0.5 m — a rolling sub-3 km/h
  creep is not a hold), and a *PROCEED* entry requires actually crossing the stop
  line when the signed stop-line column exists (junction polygons begin ~8 m
  upstream of the line). STOP/HOLD windows are derived from the per-tick live
  directive metadata, so multi-phase scenes (SLOW→STOP) are scored on the
  directive they enforce, and a HOLD binds until an explicit release — an officer
  leaving the scene is not a release.
- **The Failure Profile is descriptive, not scored** — it enters no aggregate, so it
  cannot be gamed and needs no weighting defense.

---

*Reproduce:* `python scripts/_weight_sensitivity.py` ·
`python scripts/_failure_profiles.py` · inputs in `outputs/multirun/`.
*Companion:* [metrics.md](metrics.md) · [taxonomy_decision.md](taxonomy_decision.md) ·
[reproducibility.md](reproducibility.md).
