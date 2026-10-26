# MARSHAL Tracks (A / B / C)

MARSHAL evaluates three kinds of system. They are reported as **separate
tracks** because they receive different inputs and are scored under different
protocols. A model is labeled by *how it is evaluated*, not by its architecture.

## Track A — Privileged Oracle

- Reads ground truth (the officer's true gesture, authority validity, expected
  action).
- Serves as the **reference** the scorers are checked against (protocol v2:
  24 of 25 scenarios PASS, MARSHAL-Graded 96.9; see the README results).
- **Not a deployable model** — it is a reference for "what the correct
  authority-aware response is," not a competitor.

## Track B — Closed-loop Driving Agent

- Runs **inside CARLA**.
- Produces a `carla.VehicleControl` every tick (throttle / brake / steer).
- Evaluated using **telemetry** (recorded ego trajectory: speed, position,
  junction entry, lateral offset, collisions).
- Uses each model's **native sensor rig** (multi-camera + LiDAR + ego state +
  a non-privileged lane-follow route, as applicable).
- Examples: **TransFuser, InterFuser, TCP, CILRS, AIM, NEAT, PID, MPC**, and
  **OpenEMMA** (integrated as a CARLA controller — a full-planning VLM-E2E that
  regresses a trajectory and tracks it with pure-pursuit).

## Track C — VLM-based closed-loop controller

- A vision-language model chooses a high-level action (STOP / GO / SLOW / HOLD)
  from the single forward dash-cam frame, every 1.5 simulation seconds for the
  whole 25 s episode; the latest answer stays active until the next one.
- A fixed low-level controller (route following plus the action's speed rule)
  turns the active action into a `carla.VehicleControl` every tick, so the VLM
  drives the car closed-loop.
- Scored from **telemetry** with the same strict and graded scorers as Track B;
  each model–scenario pair is run 5 times (seeds 0–4) and reported as mean ± SD
  and strict PASS k/5.
- Examples: **Qwen2.5-VL-72B, Qwen3-VL-235B-A22B, GLM-4.5V**.

> The reported Track-C numbers are **driving results of a VLM-based closed-loop
> controller**. They are not a visual decision-QA score: a QA score would compare
> one answer per image with the expected action, without the car moving.
> Pure visual decision QA is defined as a separate task in
> [track_c_visual_decision_qa.md](track_c_visual_decision_qa.md); no results are
> reported for it.

## Reporting rule

**Do not label a model "B/C" in a result table.** Pick the track that matches the
evaluation. If the *same* model is evaluated in both modes, report it as **two
separate rows**:

- `OpenEMMA-B` — closed-loop CARLA controller (Track B).
- `OpenEMMA-C` — VLM-based closed-loop controller (Track C).

Currently OpenEMMA is evaluated only as a closed-loop controller, so only
`OpenEMMA-B` has results; `OpenEMMA-C` is planned (a same-backbone B-vs-C
comparison would be a clean future experiment).

## Why Track-B and Track-C results are reported separately

Both tracks are closed-loop driving in CARLA under the same scorers and the same
25 s horizon, but the inputs differ: Track-B stacks read their native sensor rig
and decide every tick, while a Track-C controller reads one dash-cam frame per
1.5 s query. The rows are therefore labelled by track, and Track-C rows carry a
seed spread (5 runs) that the single-run Track-B rows do not.
