# Final candidate leaderboard (Credit v2.2 final, frozen)

- **Credit:** the frozen v2.2 formula ([credit_v22_final.md](credit_v22_final.md)). Strict verdicts are the published ones. MARSHAL-Graded = 100 · Σ w C / Σ w per run, then the mean over runs.
- **Runs:** every model keeps its protocol-v2 runs, except InterFuser. InterFuser uses only the final re-measure (forward speed input): 3 complete seed(s) of the required 0, 1, 2 (coverage in `data/final_leaderboard_coverage.csv`); it is ranked only when exactly these three seeds are complete.

| rank | model | runs | strict | graded (mean ± SD) | min–max over runs |
|---:|---|---:|---|---:|---|
| 1 | oracle | 1 | 24/25 | 96.91 ± 0.00 | 96.91–96.91 |
| 2 | InterFuser | 3 | 22/75 | 39.82 ± 4.46 | 36.30–44.84 |
| 3 | NEAT | 1 | 5/25 | 33.62 ± 0.00 | 33.62–33.62 |
| 4 | CILRS | 1 | 5/25 | 26.05 ± 0.00 | 26.05–26.05 |
| 5 | Qwen2.5-VL | 5 | 23/125 | 21.86 ± 3.69 | 16.99–25.62 |
| 6 | TCP | 1 | 3/25 | 20.68 ± 0.00 | 20.68–20.68 |
| 7 | TransFuser | 1 | 2/25 | 14.32 ± 0.00 | 14.32–14.32 |
| 8 | MPC | 1 | 3/25 | 14.10 ± 0.00 | 14.10–14.10 |
| 9 | PID | 1 | 3/25 | 13.51 ± 0.00 | 13.51–13.51 |
| 10 | Qwen3-VL | 5 | 13/125 | 13.47 ± 2.50 | 9.53–16.33 |
| 11 | OpenEMMA | 1 | 2/25 | 11.87 ± 0.00 | 11.87–11.87 |
| 12 | AIM | 1 | 1/25 | 11.59 ± 0.00 | 11.59–11.59 |
| 13 | baseline | 1 | 0/25 | 9.11 ± 0.00 | 9.11–9.11 |
| 14 | GLM-4.5V | 5 | 10/125 | 8.43 ± 0.64 | 7.65–9.29 |

**Excluded: integration defect.** The InterFuser protocol-v2 run (1 run, strict 8/25) is preserved but not compared. It scores 40.68 under this credit and 43.53 under v2 (`data/final_leaderboard_excluded.csv`).

**Superseded: speed input.** The InterFuser fixed-adapter re-measure with the 3-D speed input (3 runs, strict 21/75) scores 36.95 ± 3.93 under this credit (`data/final_leaderboard_excluded.csv`).

## Sensitivity

| row | Kendall τ-b vs final (all / non-privileged) | rank changes |
|---|---|---|
| v2 | 0.824 / 0.788 | InterFuser 2→3, NEAT 3→2, CILRS 4→5, Qwen2.5-VL 5→6, TCP 6→4, MPC 8→9, PID 9→10, Qwen3-VL 10→11, OpenEMMA 11→12, AIM 12→8, baseline 13→14, GLM-4.5V 14→13 |
| F3 upper bound (hidden contacts distinct) | 0.934 / 0.939 | PID 9→10, Qwen3-VL 10→9, OpenEMMA 11→12, AIM 12→11, baseline 13→14, GLM-4.5V 14→13 |
| without F3 | 0.934 / 0.939 | PID 9→10, Qwen3-VL 10→9, OpenEMMA 11→12, AIM 12→11, baseline 13→14, GLM-4.5V 14→13 |
| without the 2 geometry scenarios | 1.0 / 1.0 | none |

**F3 contact-count uncertainty.** After the 20th counted collision event only the type id is logged.

- The final score is the lower contact bound: same-type events within 1 s are one contact.
- The upper bound counts every event without an actor id as a new actor.
- Pairs whose score intervals overlap, so their order is not separated by the logs: MPC–PID, MPC–Qwen3-VL, PID–Qwen3-VL, PID–OpenEMMA, OpenEMMA–AIM, baseline–GLM-4.5V.

**Geometry scenarios.** `occluded_officer` and `sequential_directive` place the line 2.5 m and 4.33 m from spawn, so a stop is feasible only with a near-immediate reaction. The row above drops both scenarios for every model.

## Limits of the metric

**F2: not resolved.** Credit scores observable trajectory compliance:

- stop, hold and proceed timing;
- line position;
- enforced speed;
- engagement and safety.

It cannot observe whether the authority or the instruction was perceived. An instruction-independent trajectory can be identical to an instruction-following one.

- **Reference row (48 instruction-independent policies, synthetic):** the best is "lurch 2.5 m, go 20 km/h at 16.5 s" at 69.63 graded with strict 14/25.
- 37 of the 48 policies score above the best non-privileged system in this table (39.82).
- The remedy is matched counterfactual runs (officer present vs absent, or STOP vs PROCEED), which need new rollouts.

**F4: speed ordering at equal overshoot.**

- The credit rewards a stop reached within the reaction budget.
- A 25 km/h approach that stops on time therefore outscores a 5 km/h creep that overruns by the same distance but stops 4–10 s later (synthetic, `green_stop`): 0 m: 0.486 vs 0.206, 0.1 m: 0.168 vs 0.067, 0.5 m: 0.157 vs 0.054, 2 m: 0.118 vs 0.016, 10 m: 0.055 vs 0.000.
- The metric does not rank impact speed.
