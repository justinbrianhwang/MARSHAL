# Track-C — Traffic Authority Decision QA

**Task name: Traffic Authority Decision QA.**

This document defines a **visual decision-QA** task: one answer per image (or
frame set), compared with the expected action, without the car moving. It is a
task definition only; **no results are reported for it**. The reported Track-C
results are driving results of the **VLM-based closed-loop controller** (see
[tracks.md](tracks.md) and [evaluation_protocol_v2.md](evaluation_protocol_v2.md)),
which uses the same VLMs but drives the car.

## Task definition

Given one or more ego-camera frames from a MARSHAL scenario, the model must answer
the correct ego action.

**Allowed answer set:**

```
STOP, PROCEED, SLOW, HOLD, YIELD, DETOUR
```

## Prompt template

```
You are an autonomous driving decision module.
Analyze the current traffic scene.
Consider traffic lights, police officers, construction flaggers, emergency
vehicles, pedestrians, hazards, and rule hierarchy.
If an authorized human is directing traffic, their instruction may override the
traffic light.
If the instruction is ambiguous or unsafe, choose the safest legal action.
Return exactly one action from:
STOP, PROCEED, SLOW, HOLD, YIELD, DETOUR.
```

## Scoring

- **Exact action match** — the headline Track-C metric: does the returned action
  equal the ground-truth expected action for the scenario?
- *(Optional)* **Structured reasoning score** — quality of the stated reasoning.
- *(Optional)* **Authority recognition subscore** — did the model identify the
  authoritative actor (officer / flagger / emergency vehicle) at all?
- *(Optional)* **Gesture recognition subscore** — did it read the gesture
  (STOP / GO / SLOW / LEFT / RIGHT / HOLD)?
- *(Optional)* **Rule-hierarchy subscore** — did it apply safety > authorized
  human command > device when cues conflicted?

**Clarification.** A visual decision-QA score is not a driving score: a correct
answer per image does not establish that the model can drive. The closed-loop
controller results are the driving measurement.

## Input Protocol

Track-C results are only comparable when the input protocol is held fixed. Each
Track-C result should report:

- **Number of frames** — how many frames the model sees per decision.
- **Frame sampling interval** — the time between sampled frames (seconds).
- **Camera view** — which camera (e.g. single forward ego camera).
- **Image resolution** — pixel dimensions of each frame.
- **Frame timing relative to gesture onset** — whether the frames are selected
  **before, during, or after** the gesture onset.
- **Traffic-light state provision** — whether the traffic-light state is given as
  **text** in the prompt or only **visible in the image**.

> Because a Track-C model can be helped or handicapped by these choices (more
> frames, frames centered on the gesture, or the light state spelled out in
> text), they must be reported for every Track-C row so comparisons — especially
> against closed-loop Track-B agents — are fair.

### Input protocol of the VLM-based closed-loop controller (protocol v2)

| field | value |
|-------|-------|
| number of frames | per-tick single frame (the controller queries on a fixed period) |
| frame sampling interval | 1.5 simulation seconds |
| query budget | a query at 0.05 s and then every 1.5 s until the 25 s horizon (17 per episode), no cap; the latest answered decision stays active until the next one. The green_stop query-cap comparison repeats the same runs with a 3-query cap. |
| camera view | single forward ego RGB |
| image resolution | 1280×720, 90° FOV |
| frame timing vs gesture onset | live, during the episode (frames span before/through/after onset) |
| traffic-light state | available to the controller as `observation["tl_state"]`; scene is also visible in the image |

These values are recorded so a future Track-C protocol change (e.g. multi-frame
context, or text-only light state) is an explicit, documented variable rather
than a hidden advantage.

## Machine-readable result schema

Per-decision Track-C records should conform to
[`marshal_bench/configs/track_c_eval_schema.json`](../marshal_bench/configs/track_c_eval_schema.json):
`scenario`, `model`, `num_frames`, `frame_interval_sec`, `camera`, `resolution`,
`prompt_template`, `answer`, `ground_truth_action`, `correct`, `notes`.
