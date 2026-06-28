# MARSHAL — Legal / Policy Grounding

MARSHAL assumes traffic-authority precedence based on **US-style traffic-control
policy**. This document states that assumption cautiously and points to the
primary references; the detailed standards mapping and the hand-signal taxonomy
already live in [marshal_grounding.md](marshal_grounding.md) and are **not**
duplicated here.

## Core assumption (stated cautiously)

> MARSHAL is grounded in the common traffic-control principle that authorized
> traffic officers may direct traffic and that their directions can supersede
> ordinary traffic-control devices.

This yields the rule hierarchy MARSHAL scores against:

**safety > authorized human command > traffic light / sign.**

MARSHAL does **not** make jurisdiction-exact legal claims. It is a benchmark of
authority-aware *reasoning*, not a legal simulator; the precedence above is a
modeling assumption grounded in common US practice, not a statement of law for
any specific state.

## References

The following already appear, with quotations and links, in
[marshal_grounding.md](marshal_grounding.md):

- **VCU Police, Manual Traffic Direction & Control (8-6)** — officers may assume
  control of any intersection and their signals take precedence over
  traffic-control devices. (Hand-signal taxonomy: STOP / GO / LEFT / RIGHT /
  SLOW / HOLD.)
- **FHWA MUTCD** — hierarchy of traffic control; manual traffic direction by
  authorized personnel.
- **FHWA Official Interpretation 6(09)-16** — a uniformed officer may direct
  traffic by hand gestures alone in TTC / special-event / incident scenes.

## TODO — references to add / make exact

These are the citations to firm up (some are covered above; listed here as the
authoritative checklist for the camera-ready grounding section):

- [ ] **FHWA MUTCD** — exact edition + section number for traffic-control
  hierarchy and authorized manual traffic direction.
- [ ] **State driver manuals** — at least one or two state DMV handbook citations
  on "obey police officers / flaggers over signals."
- [ ] **Police traffic-direction hand-signal guidance** — an authoritative source
  for the STOP / GO / SLOW / turn hand signals beyond VCU 8-6 (e.g. a state POST
  or academy manual).
- [ ] **Emergency-vehicle yielding rules** — citation for the duty to yield to
  emergency vehicles (used by `ambulance_yield`).

If a reference is added to [marshal_grounding.md](marshal_grounding.md), link it
from there rather than duplicating the text here.
