# Long-tail, corner case, and where authority-conflict belongs

**Why this doc exists.** The terms *long-tail*, *corner case*, and *long-tail corner
case* are often used interchangeably in autonomous-driving discussion, including in
earlier MARSHAL materials. This document fixes precise, distinct definitions and then
states, carefully, where authority-conflict scenarios sit — because that positioning
is part of MARSHAL's argument.

> **Note on citations.** The conceptual definitions below rest on widely-used notions
> (heavy-tailed event distributions; the Operational Design Domain / operational
> envelope). Specific literature citations are being firmed up in the accompanying
> survey pass and are marked *[cite]* where a source will be attached; they are not
> asserted here as settled references.

## Three distinct definitions

**Long-tail** — a statement about **frequency**. Driving situations follow a
heavy-tailed distribution: a small set of common situations dominates mileage, while a
very large number of individually **rare** situations collectively account for a
disproportionate share of risk and of the remaining failure cases. "Long-tail" refers
to those rare-but-numerous situations *[cite]*.

**Corner case** — a statement about **coverage / competence envelope**. A corner case
is a situation at or beyond the **edge of the system's operational envelope** (its
ODD, its training distribution, or its assumed conditions) — where the system's
competence is not guaranteed. It is defined by being *out-of-envelope*, independent of
how often it occurs *[cite]*.

**Long-tail corner case** — the **intersection**: a situation that is both **rare**
(long-tail) **and out-of-envelope** (corner case). Not every rare event is a corner
case (a rare but well-handled situation is only long-tail), and not every corner case
is rare (a systematic blind spot can occur often).

## The 2×2

|                         | **In-envelope** (handled) | **Out-of-envelope** (corner case) |
|-------------------------|---------------------------|-----------------------------------|
| **Common** (head)       | ordinary driving          | systematic blind spot             |
| **Rare** (long-tail)    | rare-but-handled event    | **long-tail corner case**         |

The dangerous cell is the bottom-right: rare **and** out-of-envelope. MARSHAL's
authority-conflict scenarios are constructed to live there — but with an important
twist described next.

## Where authority-conflict belongs: a *semantic* long-tail

> **Terminology (our term, not a standard one).** *"Semantic long-tail"* is a label
> **we introduce in this work** for clarity; it is not established terminology.
> **In this work, we refer to** rare, high-consequence *decisions* that arise under
> otherwise-ordinary percepts as a **semantic (decision-level) long-tail**, to
> distinguish them from the *perceptual* long-tail (rare percepts) that most existing
> "long-tail" benchmarks target. A reviewer should read it as our defined shorthand,
> not as a citation to prior art.

Authority-conflict scenarios are long-tail corner cases of this specific kind — a
semantic long-tail rather than a perceptual one:

- **The perceptual inputs are common.** A person standing in the road, an arm raised,
  a hi-vis vest, a traffic light — all are ordinary, in-distribution percepts for a
  modern perception stack.
- **The required decision is rare and high-consequence.** *Obey this human instead of
  the green light* is an infrequent decision that inverts the normal
  signal-following prior, and getting it wrong is high-consequence.
- **The failure is in reasoning/priority, not perception.** The gap is not "did it see
  the person" but "did it treat the person as an *authority that overrides the
  signal*, and only when that authority is legitimate."

This is why authority-conflict is **under-covered** by existing benchmarks: perception
and skill benchmarks sample the *perceptual* tail (unusual objects, weather, geometry)
but not the *decision* tail (unusual priority under conflicting-but-ordinary cues).
A model can be strong on perceptual corner cases and still fail every authority
override.

## Partial membership — stated carefully

Authority-conflict is **partly**, not wholly, a classic long-tail problem:

- It **is** long-tail in *frequency* — authorized human override of a signal is rare
  in everyday driving.
- It **is** a corner case in *competence* — it sits outside the "obey the signal"
  prior that most driving policies are optimized for.
- It is **not** primarily a *perceptual* tail problem — the inputs are common; this
  is what distinguishes it from the perceptual long-tail that most "long-tail"
  benchmarks target.

Presenting it as "just another long-tail" would overstate the overlap; presenting it
as unrelated would miss the frequency/competence argument. MARSHAL positions it as a
**semantic long-tail corner case**: rare, out-of-envelope, and reasoning-bound.

## Comparison

| Category | Defined by | Example | Primary failure mode | Typical benchmark coverage |
|---|---|---|---|---|
| Perceptual long-tail | rare *percepts* | unusual object, rare weather/geometry | detection / recognition | perception & corner-case sets |
| Operational corner case | out-of-envelope conditions | sensor degradation, ODD edge | competence outside envelope | robustness / ODD studies |
| **Semantic long-tail (MARSHAL)** | rare *decisions* under ordinary percepts | authorized human overrides the signal | priority / authority reasoning | **not isolated by prior benchmarks** |

---

*See also:* [problem_statement.md](problem_statement.md) (the evaluation dimension
this positioning supports), [design_principles.md](design_principles.md) (how the
scenarios operationalize the reasoning requirements). Citation-firming and a broader
literature summary are tracked as the survey deliverable in the redesign plan.
