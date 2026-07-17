# MARSHAL — legal/standards grounding for officer signals & authority

MARSHAL's authority semantics and hand-signal taxonomy are grounded in
recognized US traffic-control standards, so the scenarios are defensible as a
benchmark rather than ad-hoc.

## Authority precedence (the core premise)
An authorized officer's directions override traffic-control devices.

- **VCU Police, Manual Traffic Direction & Control (8-6):** *"Police officers
  and uniformed security officers may assume control of traffic at any
  intersection regardless of whether such intersection is controlled by lights,
  other traffic control devices or uncontrolled. In such events, signals by such
  officers shall take precedence over such traffic control devices."*
  <https://police.vcu.edu/facts/policies/8-6-traffic-direction-and-control/>
- **FHWA MUTCD** — hierarchy of traffic control; manual traffic direction by
  authorized personnel.
- **FHWA Official Interpretation 6(09)-16** — a uniformed officer may direct
  traffic by hand gestures alone in TTC / special-event / incident scenes.

This yields the MARSHAL rule hierarchy:
**safety > authorized human command > traffic light/sign.**

## Hand-signal taxonomy (grounded poses)
| Gesture | Basis (VCU 8-6 / common US practice) | MARSHAL `GestureID` |
|---------|--------------------------------------|---------------------|
| STOP | "raise arms... palms toward moving traffic to be stopped" | `STOP` |
| GO / PROCEED | "extend arm toward traffic to be moved... bring hand sharply in direction traffic is to move" | `PROCEED` |
| TURN LEFT / RIGHT | point/sweep toward the lane to be moved | `LEFT` / `RIGHT` |
| SLOW | flagger slow-down (work-zone practice) | `SLOW` |
| HOLD / WAIT | palm-up "wait" — very common on-scene; not a full STOP | `HOLD` (added) |

Whistle cues (VCU): one long blast = stop, two short = go (not modeled; optional).

## Why LLM-level reasoning is required (benchmark argument)
Classifying STOP/GO/LEFT/RIGHT is solvable by **YOLO + action recognition + a
rule engine** — no LLM needed. The capability gap appears only when the agent
must reason about **human intent and rule priority**:

| Reasoning type | Example | MARSHAL scenario |
|----------------|---------|------------------|
| Rule conflict | Red light + police GO → GO | `red_proceed`, `rule_hierarchy` |
| Authority reasoning | Citizen GO → ignore | `unauthorized_go`, `conflicting_authorities` |
| Target assignment | Officer faces the adjacent lane → I wait | `adjacent_lane` |
| Social reasoning | Police GO + ambulance approaching → yield | `ambulance_yield`, `rule_hierarchy` |
| Temporal memory | Remember a withdrawn "wait" directive | `sequential_directive` |
| Perception+intent | Officer partially occluded → still obey | `occluded_officer` |
| Ambiguity | STOP-like but unclear → infer intent, act cautiously | `ambiguous_gesture` |

The benchmark reports an authority-conflict profile (`conflict_type_profile` in
the aggregated scoreboard) to show where a model succeeds across override,
validity, directive conflict, scene authority, and safety cases. The former
`tier_pass_rate` remains as legacy compatibility metadata only.
