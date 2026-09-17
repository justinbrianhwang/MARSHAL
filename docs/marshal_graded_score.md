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
> authority-relevant region** before partial credit is awarded, and an ego
> with no movement evidence at all (never ≥5 km/h or ≥1 m of progress)
> scores zero outright — the gate mirrors strict's approach-evidence arm. STOP/HOLD credit
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
> forward progress over the whole episode scores **zero** — the graded scorer
> inherits strict's approach-evidence arm, so parking at spawn earns nothing
> on either scale. Past that gate, the engagement factor is *continuous*,
> $e_s \in [0,1]$, applied **only to non-strict STOP/HOLD partial credit**:
> strict-compliant STOP/HOLD telemetry (which since round 7 also requires
> coming within the engagement band of the stop line or the directing human)
> passes at $e_s = 1$; otherwise $e_s$ follows from approach speed and forward
> progress, with low-speed creep capped at `0.25`. Residual stop-bias is
> additionally priced *across* scenarios (the suite is balanced over STOP and
> non-STOP actions).

## Implementation status

- [x] Scorer module (`marshal_bench/criteria/graded_episode_scoring.py`).
- [x] **Approach/engagement gate wired in** (`_stop_hold_engagement_factor`), so
      creep-and-stop no longer banks full STOP clearance credit.
- [x] **Calibrated: the privileged oracle scores 100.0**; current reference
      sweep (single sweep, ties read at few-point resolution): InterFuser
      40.7, NEAT 39.3, GLM-4.5V 29.4, … PID 11.9 (full tables in README.md).
- [x] Graded ranking tracks the strict ordering — the graded leaders carry
      the top strict counts.
- [x] **Published in `README.md`** as the primary metric (single current
      reference sweep; a multi-sweep re-measurement is planned).

The **strict verdict logic is shared, not duplicated**: graded inherits
strict's approach-evidence arm and its engagement anchors, and adds
continuous margin credit on top.
