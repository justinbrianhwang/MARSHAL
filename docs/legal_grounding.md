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

## Statutory references

**Statutes (driver's duty — the precedence MARSHAL scores).**

- **[NY VTL §1110(a)](https://www.nysenate.gov/legislation/laws/VAT/1110)** —
  *"Every person shall obey the instructions of any official traffic-control
  device … **unless otherwise directed by a traffic or police officer**, subject
  to the exceptions granted the driver of an authorized emergency vehicle."* This
  is the device → officer precedence the whole benchmark turns on.
- **[NY VTL §1102](https://www.nysenate.gov/legislation/laws/VAT/1102)** —
  *"No person shall fail or refuse to comply with any lawful order or direction of
  any **police officer or flagperson** or other person duly empowered to regulate
  traffic."* (Covers `flagger_control`, `flagger_slow_then_stop`.)
- **[Uniform Vehicle Code §11-103](https://bikeleague.org/sites/default/files/UVC%20Rules%20of%20the%20Road%20ch.%2011.pdf)**
  (NCUTLO model code) — *"No person shall willfully fail or refuse to comply with
  any lawful order or direction of any **police officer, firefighter, flagger** at
  [a] highway construction or maintenance site, or uniformed adult **school
  crossing guard** invested by law with authority to direct, control or regulate
  traffic."* (Directly names the actors in `signal_off`, `flagger_control`,
  `school_crossing_guard`.)
- **[NY VTL §1144](https://www.nysenate.gov/legislation/laws/VAT/1144)** —
  operation of vehicles on the approach of authorized emergency vehicles: the duty
  to yield. (Covers `ambulance_yield`.)

**Standards / hand-signal guidance.**

- **[FHWA MUTCD, 11th Edition (2023)](https://mutcd.fhwa.dot.gov/)** — the federal
  standard for traffic-control devices (the signals/signs MARSHAL's authorities
  override); Part 6 covers temporary traffic control and flagger control.
- **VCU Police, Manual Traffic Direction & Control (8-6)** and **FHWA Official
  Interpretation 6(09)-16** (a uniformed officer may direct traffic by hand
  gestures alone in TTC / special-event / incident scenes) — quoted with the
  STOP / GO / LEFT / RIGHT / SLOW / HOLD hand-signal taxonomy in
  [marshal_grounding.md](marshal_grounding.md).

**Caveat.** These are illustrative, not jurisdiction-exhaustive: NY VTL is one
representative state code and the UVC is a model code many states adopt. MARSHAL
treats the precedence as a modeling assumption grounded in common US practice, not
a statement of law for any specific jurisdiction.
