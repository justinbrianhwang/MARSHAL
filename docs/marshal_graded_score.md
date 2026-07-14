# MARSHAL-Graded — planned continuous score

This documents the **planned secondary** continuous score. The binary pass/fail
result remains the **headline**; MARSHAL-Graded is reported *alongside*, never
replacing it.

> Status: **in development.** A draft scorer exists
> (`marshal_bench/criteria/graded_episode_scoring.py`) but its curves are still
> being refined — the numbers are **not** published in the README yet. See the
> *Known issue* below.

## Why a continuous score

The binary scorer answers "did the ego do what the oracle would?" — un-gameable
and telemetry-grounded, but coarse: a model that stops 1 cm short of a perfect
stop and one that blows through both register as a single bit on most scenarios.
MARSHAL-Graded adds a real-valued score in `[0, 100]` that awards **partial
credit** from the same telemetry, so near-misses and degrees of competence are
visible.

## What it scores (telemetry margins)

Partial credit is computed from per-episode telemetry margins, including:

- **stop distance** — how close to the correct stopline / hazard the ego halted;
- **residual speed** — speed remaining when a STOP/HOLD was required;
- **reaction latency** — time from gesture onset to the first valid response;
- **lateral clearance** — clearance to hazards / pedestrians on a PROCEED/DETOUR;
- **collision status** — any contact zeroes safety-bounded credit;
- **rule compliance** — did the executed action match the authority-aware
  expectation.

Authority-override scenarios are **weighted more heavily** (police-priority
cases count for more), and the score is **calibrated so the oracle ≈ 100**.

## Engagement gate (required, not optional)

MARSHAL-Graded must **not over-credit trivial "creep-and-stop" behavior.** A
model that crawls the whole episode and never approaches the conflict can
otherwise bank STOP partial-credit (a near-zero stop distance) without ever
reading the authority.

> **Engagement gate:** the vehicle must **approach or encounter the
> authority-relevant region** before partial credit is awarded. STOP/HOLD credit
> requires evidence the ego genuinely approached (e.g. a pre-stop approach speed
> above a threshold, or meaningful forward progress toward the stopline) and then
> stopped — not that it merely never moved. A perpetual creeper collapses toward
> a near-zero graded score consistent with its binary / authority-STOP result; a
> decisive model that brakes from real speed keeps full credit.

## The formula (as implemented)

For a policy $\pi$ evaluated over the $N$ scenarios:

```math
\text{MARSHAL-Graded}(\pi) \;=\; 100 \cdot \frac{\sum_{s=1}^{N} w_s \, c_s(\pi)}{\sum_{s=1}^{N} w_s}
```

- $c_s(\pi) \in [0,1]$ — per-episode **telemetry credit** (action correctness, reaction
  latency, safety, maneuver quality). Invalid / malformed / adapter-error telemetry
  scores `0`.
- $w_s > 0$ — the **scenario authority weight** (`SCENARIO_AUTHORITY_WEIGHTS`).
  Authority-override scenarios are deliberately weighted above 1.0; the denominator
  normalizes by the weight sum, so the reported maximum stays 100.
- The **engagement gate is folded into $c_s$**, not applied as a separate outer term.

> **Note on the gate's form.** It is a *continuous* factor $e_s \in [0,1]$, **not** a
> binary $\{0,1\}$ switch, and it applies **only to non-strict STOP/HOLD partial
> credit**. Strict-compliant STOP/HOLD telemetry passes at $e_s = 1$; otherwise
> $e_s$ is derived from approach speed and forward progress (or near-stopline
> progress), with low-speed creep capped at `0.25`. A hard binary gate was tried and
> rejected: it collapsed the calibrated oracle from 100 to ~45, because a legitimate
> decisive stop far upstream is telemetrically indistinguishable from a creep on a
> per-episode basis. Stop-bias is therefore handled *across* scenarios (the suite is
> balanced over STOP and non-STOP actions), not by zeroing individual episodes.

## Implementation status

- [x] Scorer module (`marshal_bench/criteria/graded_episode_scoring.py`).
- [x] **Approach/engagement gate wired in** (`_stop_hold_engagement_factor`), so
      creep-and-stop no longer banks full STOP clearance credit.
- [x] **Calibrated: the privileged oracle scores 100.0**, and the creepers
      (PID 5.8, MPC 13.4, TCP 14.8) sit far below the decisive models.
- [x] Graded ranking broadly tracks the binary PASS + authority-STOP ordering.
- [x] **Published in `README.md`** as the primary metric, reported as the
      mean ± std of 3 independent closed-loop sweeps.

The **binary scorer (`strict_episode_scoring.py`) stays unchanged** — MARSHAL-
Graded is additive and secondary.
