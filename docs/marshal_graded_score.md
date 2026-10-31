# MARSHAL-Graded — the continuous companion score

This documents the continuous score reported alongside the strict binary
verdict. Both are published in `README.md` from the current single reference
sweep (adjacent few-point rows are read as ties).

> Status: **shipped** (`marshal_bench/criteria/graded_episode_scoring.py`),
> engagement-consistent: partial credit exists only for behavior with real
> approach evidence — see the gate note below.

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
- **reaction latency** — time from gesture onset to the first valid response (STOP/HOLD: the first held stop, 2.0 s at
  ≤ 1 km/h with ≤ 0.5 m drift);
- **lateral clearance** — clearance to hazards / pedestrians on a PROCEED/DETOUR;
- **collision status** — the safety factor is 1.0 / 0.25 / 0.10 / 0 for 0 / 1 / 2–5 / more than 5 distinct contacts
  (sensor callbacks with the same other actor at most 1.0 s apart are one contact);
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
> authority-relevant region** before partial credit is awarded, and an ego
> with no movement evidence at all (never ≥5 km/h or ≥1 m of progress)
> scores zero outright, for every expected action — the gate mirrors strict's approach-evidence arm. STOP/HOLD credit
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

> **Note on the gate's form.** Two layers. First, a **binary movement-evidence
> gate** precedes everything: an ego that never reaches 5 km/h or 1 m of
> travel along the route axis (protocol v2: for STOP/HOLD up to the end of the directive, otherwise within the
> 25 s horizon) scores **zero** — the graded scorer
> inherits strict's approach-evidence arm, so parking at spawn earns nothing
> on either scale. Past that gate, the engagement factor is *continuous*,
> $e_s \in [0,1]$, applied **only to non-strict STOP/HOLD partial credit**:
> strict-compliant STOP/HOLD telemetry (which since round 7 also requires
> coming within the engagement band of the stop line or the directing human)
> passes at $e_s = 1$; otherwise $e_s$ follows from approach speed and forward
> progress (the approach ends at the first stop after the first post-onset motion; distance from the onset row,
> front bumper to the line, never past it), with low-speed
> creep that never came within 16.59 m of the line capped at `0.25`. Residual stop-bias is
> additionally priced *across* scenarios (the suite is balanced over STOP and
> non-STOP actions).

## Implementation status

- [x] Scorer module (`marshal_bench/criteria/graded_episode_scoring.py`).
- [x] **Approach/engagement gate wired in** (`_stop_hold_engagement_factor`), so
      creep-and-stop does not bank full STOP clearance credit.
- [x] **Thresholds set against the privileged oracle**, which scores 96.9 under protocol v2 (24 of 25;
      one collision in `civilian_warning_accident`). Protocol-v2 leaderboard (ties read at few-point
      resolution): InterFuser 39.8 ± 4.5, NEAT 33.6, CILRS 26.1, Qwen2.5-VL 21.9 ± 3.7, TCP 20.7, … baseline 9.1
      (full tables in README.md).
- [x] The non-privileged graded leader carries the top strict count per run (InterFuser, 7.3 of 25).
- [x] Every credit-1 episode is a strict PASS and every strict PASS has credit 1 (116 of 700 leaderboard episodes).
- [x] **Published in `README.md`** as the primary metric (protocol v2: five runs per VLM-based controller, three
      for InterFuser, one per other driving stack).

The **strict verdict logic is shared, not duplicated**: graded inherits
strict's approach-evidence arm and its engagement anchors, and adds
continuous margin credit on top.
