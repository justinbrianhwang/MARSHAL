# MARSHAL Tracks (A / B / C)

MARSHAL evaluates three kinds of system. They are reported as **separate
tracks** because they receive different inputs and are scored under different
protocols. A model is labeled by *how it is evaluated*, not by its architecture.

## Track A — Privileged Oracle

- Reads ground truth (the officer's true gesture, authority validity, expected
  action).
- Serves as the **upper bound** the benchmark is calibrated against (oracle =
  14/14).
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

## Track C — Visual Decision QA

- Receives **one or more front-camera frames** from a MARSHAL scenario.
- Answers a **driving-decision question** (one action from the allowed set).
- Does **not** directly control the CARLA vehicle unless it is explicitly
  wrapped as a controller and run under Track B.
- Examples: **Qwen2.5-VL, Qwen3-VL, GLM-4.5V**.
- Task definition, prompt template, allowed answers, and input protocol:
  [track_c_visual_decision_qa.md](track_c_visual_decision_qa.md).

> In MARSHAL today, the Track-C VLMs are wired into a per-tick `vlm` controller
> for closed-loop measurement, but the **score reported for Track C is a visual
> decision-QA score** — it measures whether an off-the-shelf VLM reads authority
> from images, and is **not** a closed-loop driving score in the Track-B sense.

## Reporting rule

**Do not label a model "B/C" in a result table.** Pick the track that matches the
evaluation. If the *same* model is evaluated in both modes, report it as **two
separate rows**:

- `OpenEMMA-B` — closed-loop CARLA controller (Track B).
- `OpenEMMA-C` — visual decision QA (Track C).

Currently OpenEMMA is evaluated only as a closed-loop controller, so only
`OpenEMMA-B` has results; `OpenEMMA-C` is planned (a same-backbone B-vs-C
comparison would be a clean future experiment).

## Why Track-B and Track-C results are reported separately

Track-B evaluates closed-loop control in CARLA; Track-C evaluates visual decision
QA from camera observations. They are not directly comparable unless **input
frames, sampling rate, and evaluation protocol are controlled**. Mixing them in
one undifferentiated table would compare a closed-loop driving score against an
image-QA score as if they were the same measurement — they are not. See the
split result tables and caption in the top-level `README.md`.
