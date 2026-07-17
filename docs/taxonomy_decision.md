# Should the low/mid/high reasoning-tier taxonomy stay? — a data-driven decision

*The 2026-07-17 meeting asked whether the tier taxonomy should be removed, simplified,
or redesigned — and demanded rigorous definitions for the contested factors (memory,
context, ambiguity) if anything is retained. This document puts the empirical evidence
on the table and makes a recommendation. Evidence source:
`scripts/_analyze_tiers.py` over the 3 independent full sweeps
(`outputs/tier_analysis.json`).*

## 1. The evidence: tiers do not order difficulty

If "high tier = harder" were true, models should pass low ≥ mid ≥ high, and tier
ordinal should correlate with empirical difficulty (1 − mean credit across the 13
non-oracle models × 3 runs). Neither holds:

- **Spearman(tier, difficulty) = −0.217** — weak, and in the *wrong direction*.
- **12 of 13 models violate strict-pass monotonicity** (all 13 violate it on credit).
- **16 of 21 scenarios are "misplaced"** relative to their tier's difficulty band.

The extremes make the point vividly:

| | scenario | tier | difficulty rank (1 = hardest) |
|---|---|---|---|
| hardest overall | `emergency_scene_blocking` | **mid** | 1 |
| 3rd hardest | `barricade_self_detour` | **mid** | 3 |
| among the easiest | `adjacent_lane` | **high** | 20 |
| easiest overall | `red_proceed` | **mid** | 21 |
| 8th hardest | `green_stop` | **low** | 8 |

What actually predicts difficulty is not the tier label but the **required maneuver**
(the DETOUR family is uniformly brutal — 0% strict pass for every non-oracle model)
and the **authority-conflict structure** — which is exactly the axis the meeting wants
the benchmark centered on.

## 2. Why this happened (a post-mortem, briefly)

The tiers encoded *anticipated reasoning load* ("how much inference should this
need?"), assigned at design time. But measured difficulty is dominated by the *action
prior*: models with a stop-bias find every STOP easy regardless of how much reasoning
it "should" take (`unauthorized_go`, high tier, is easy — braking is the right answer
for the wrong reason), and every non-STOP hard (`red_proceed`, mid tier, is the
easiest only because light-followers proceed on green by default... and the officer
agrees). Tier labels measure our intuition; the benchmark measures behavior.

## 3. Recommendation

**Retire low/mid/high as a difficulty taxonomy. Replace it with the
authority-conflict typology.** Concretely:

1. **Grouping for reporting** — group scenarios by **conflict type** (the §5 matrix of
   [scenario_design_justification.md](scenario_design_justification.md)):
   *authority-vs-device*, *authority-vs-authority*, *authority-validity*,
   *contextual/scene authority*, *safety-override*, plus the crosscutting stressors
   (occlusion, ambiguity, attribution, temporal). Report per-group pass/credit — a
   **failure profile** — instead of per-tier pass-rate.
2. **Keep the seven reasoning principles** (signal override, authority verification,
   target attribution, contextual hazard reasoning, temporal directive memory, rule
   hierarchy, ambiguity handling). They are *definitional* (what each scenario tests),
   not a difficulty claim, and they drive the failure-profile analysis
   (`scripts/_failure_profiles.py`).
3. **Difficulty, when needed, is reported empirically** — the measured per-scenario
   difficulty ranking (`outputs/tier_analysis.json`) — never as a designed label.

### Rigorous definitions for the three contested factors (retained as *stressors*, not tiers)

- **Temporal directive memory** — the correct action at time *t* depends on a
  directive issued at *t′ < t* that is **no longer perceptually available** at *t*
  (officer departed, gesture ended, or directive escalated). Operationally: the
  scenario's expected action cannot be computed from the current frame alone.
  (`sequential_directive`, `flagger_slow_then_stop`.)
- **Contextual authority** — the *same actor class* maps to *opposite* correct
  actions depending on **scene evidence**: a civilian gesture is noise in a nominal
  scene (`unauthorized_go` → ignore) but carries warrant at a visible crash
  (`civilian_warning_accident` → heed). Operationally: actor classification alone is
  provably insufficient — the minimal pair holds everything else constant.
- **Ambiguity** — the gesture's mapping to {STOP, GO, SLOW, HOLD} is **not unique
  under MUTCD conventions** (pose is between codified signals, or its target is
  underdetermined). The normatively correct response is the conservative default
  (stop/hold), and the scenario scores *that*, not gesture classification accuracy.
  (`ambiguous_gesture`; attribution variant in `adjacent_lane`.)

## 4. What changes if adopted (implementation sketch)

| Artifact | Change |
|---|---|
| README scenario table | `tier` column → `conflict type` column |
| README results tables | `low/mid/high` columns → per-conflict-type profile (or drop the split entirely; keep `authority-STOP`) |
| `scoreboard.json` | `tier_pass_rate` → `conflict_type_profile` (keep `tier_pass_rate` emitted for one release as legacy) |
| `marshal_metrics.REASONING_TIER` | retained internally (tests, backwards compat), no longer surfaced in headline reporting |
| Slides / paper | difficulty ladder framing out; conflict-space coverage framing in |

**Status: recommended, pending team sign-off.** The narrative de-emphasis (README
headline no longer tier-based) is already applied; the mechanical column/scoreboard
swap above is a small, separately-reviewable change once the team ratifies this.

---

*Evidence:* `outputs/tier_analysis.json` (regenerate:
`python scripts/_analyze_tiers.py`) · failure profiles:
`outputs/failure_profiles.json`. *Companion:*
[scenario_design_justification.md](scenario_design_justification.md) §5 ·
[evaluation_methodology.md](evaluation_methodology.md).
