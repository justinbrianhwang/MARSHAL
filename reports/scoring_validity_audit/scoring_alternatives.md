# Scoring alternatives on identical telemetry (experimental, not official)

## Sources and scope

- **Generated tables:** [`scoring_alternatives_raw.md`](scoring_alternatives_raw.md) and
  `data/alternatives_table.json`, from `scripts/alternatives.py`.
- **What was compared:** every candidate is applied to the same 650 recorded episodes and the same synthetic cases.
- **Not changed:** no controller was rerun, and no official number changed.
- **Not evaluated:** alternatives that need new rollouts (matched-condition pairs, an empty-road control) remain
  unevaluated until those runs exist.

## Desiderata (fixed before computing)

| id | desideratum |
|---|---|
| D1 | Instruction-independent policies (park, lurch-then-stop, constant go) must not outscore the light-only baseline. |
| D2 | Engagement must not depend on whether an identical approach begins before or after the directive onset. |
| D3 | At equal overshoot, a faster crossing must not score higher. |
| D4 | Strict-compliant behaviour keeps full credit. |
| D5 | Ranking changes are reported, not optimised. |

## Candidates

| | definition | D1 | D2 | D3 | D4 |
|---|---|---|---|---|---|
| **A** (v2) | as published | ✗ lurch 59.8, constant-go 18.1 | ✗ | ✗ | ✓ |
| **A′** | v2 with one line changed: the search for the stop that ends the engagement approach window starts at the first motion after onset; the onset row anchoring the approach distance is unchanged (fixes F1 only) | ✗ (unchanged) | ✓ for e (`tests/test_engagement_timing.py`) | ✗ | ✓ |
| **B** | STOP/HOLD compliance = the v2 strict cascade with the movement/proximity eligibility gates removed; move-required cells keep v2 strict; J = (STOP-side rate) + (move-side rate) − 1 over unmatched scenario sets | ✓ on the tested constant policies (park −0.08, lurch −0.15, constant-go −0.69); not guaranteed in general | n/a (verdict-based) | n/a | ✓ |
| **C** | hierarchical: collision-free share, then J, then A graded | — | — | — | — |
| **D** | multidimensional report: STOP-side compliance, move-side completion, collision-free share, J, A graded | ✓ (via J) | — | — | ✓ |

**Why B uses J.** J has the form of Youden's index. Because the STOP side and the move side are different, unmatched
scenarios, J here is a difference of side-specific rates. The tested constant policies score ≤ 0, but an
instruction-independent policy is not guaranteed J ≤ 0 (scenario confounding). A true discrimination measure needs
matched-condition pairs.

**Why C is rejected.** Ranking on collision-free share first puts parked systems on top: TransFuser, NEAT, then AIM,
all ahead of the oracle. This rewards inactivity.

## Results per model (mean over runs)

| model | A graded | A′ graded | B STOP-side | B move-side | B J | collision-free |
|---|---:|---:|---:|---:|---:|---:|
| oracle | 96.91 | 96.91 | 1.000 | 0.892 | **+0.892** | 0.960 |
| InterFuser | 43.53 | 43.53 | 0.590 | 0.415 | +0.006 | 0.920 |
| NEAT | 42.91 | 42.91 | 0.200 | 0.215 | −0.585 | 1.000 |
| TCP | 30.33 | **44.12** | 0.057 | 0.215 | −0.727 | 0.840 |
| CILRS | 24.57 | 24.57 | 0.133 | 0.323 | −0.544 | 0.800 |
| Qwen2.5-VL | 20.75 | 21.51 | 0.457 | 0.215 | −0.327 | 0.872 |
| TransFuser | 14.32 | 14.32 | 0.724 | 0.215 | −0.061 | 1.000 |
| AIM | 13.23 | 16.89 | 0.067 | 0.123 | −0.810 | 1.000 |
| MPC | 12.75 | 12.75 | 0.000 | 0.308 | −0.692 | 0.720 |
| PID | 11.76 | 11.76 | 0.000 | 0.308 | −0.692 | 0.680 |
| Qwen3-VL | 11.65 | 11.65 | 0.583 | 0.234 | −0.183 | 0.856 |
| OpenEMMA | 10.61 | 11.67 | 0.343 | 0.215 | −0.442 | 0.800 |
| GLM-4.5V | 8.14 | 8.65 | 0.623 | 0.200 | −0.177 | 0.960 |
| baseline | 7.71 | 7.71 | 0.000 | 0.000 | −1.000 | 0.560 |

Kendall τ-b against A: A′ 0.912; B (J) 0.221.

## Interpretation

- **A′ is a narrow correction.** It changes 27 STOP/HOLD episodes, all upward, because e was being capped at 0.25.
  It moves TCP from 4th to 1st among non-privileged systems (44.12 vs InterFuser 43.53). This shows the F1 defect is
  rank-relevant, not cosmetic. A′ alone leaves F2 to F4 in place.
- **B changes what is measured, and it is not a recommendation.** No non-privileged system shows clearly positive
  discrimination; InterFuser at +0.006 is the closest to zero. The STOP-side rates of the frequently parked models
  (TransFuser 0.72, GLM-4.5V 0.62, Qwen3-VL 0.58) are matched by low move-side rates. Their suite-level behaviour is
  closer to "usually stop" than to "stop when told".

  **Caveats on J:**
  - J compares different scenarios, not matched pairs, so scenario difficulty is confounded. DETOUR and YIELD cells
    are hard for every system.
  - `red_proceed` and `night_signal_officer_conflict` are not identifiable as authority-following (the spawn is
    already past the line).
  - J is evidence of behavioural discrimination at the suite level. It is not evidence about internal reasoning.
- **D is the recommended reporting form.** It reports compliance, completion, safety, J and the v2 graded score side
  by side, so the trade-off is visible instead of hidden in one scalar. A proper discrimination measure still needs
  **matched-condition pairs**: the same station, initial state and timing, with an authorized STOP, an authorized
  PROCEED, and no officer. Those require new rollouts.
- **Additive vs. multiplicative aggregation was not pursued.** The validity problems found are not caused by
  multiplication per se:
  - F1 is a window definition;
  - F2 is an eligibility definition combined with spawn geometry;
  - F3 is the counting semantics;
  - F4 comes from the creep cap.

  Replacing the product with a sum would add saturation and exploitability questions without addressing those.
