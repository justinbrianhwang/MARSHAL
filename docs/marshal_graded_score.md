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

## Known issue (why the numbers are not published yet)

The current draft over-credits over-cautious controllers: in a trial scoring,
slow "creep-and-stop" agents floated up near the decisive VLMs purely on STOP
partial-credit, because the engagement gate above is not yet wired in. The graded
ranking should broadly **track the binary PASS + authority-STOP ordering**
(decisive VLMs and the oracle on top; pure creepers near the bottom with the
baseline). Until the engagement-gated curves reproduce that ordering and the
oracle re-calibrates to ≈ 100, the graded numbers stay out of the README.

## Implementation status / TODO

- [x] Draft scorer module exists (`marshal_bench/criteria/graded_episode_scoring.py`).
- [ ] Wire in the **approach/engagement gate** so creep-and-stop no longer earns
  STOP credit.
- [ ] Re-calibrate so oracle ≈ 100 and creepers (AIM / TCP / PID) drop below the
  decisive VLMs.
- [ ] Validate the graded ranking broadly tracks binary + authority-STOP.
- [ ] Only then publish the numbers in `README.md` alongside the binary headline.

The **binary scorer (`strict_episode_scoring.py`) stays unchanged** — MARSHAL-
Graded is additive and secondary.
