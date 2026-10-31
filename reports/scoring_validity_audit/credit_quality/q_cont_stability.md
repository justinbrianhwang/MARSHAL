# Q-cont: interpretation and stability (options, none adopted)

**Read Q-cont only next to the strict verdict and the four SV flags.** A high Q-cont does **not** mean safe driving or a strict PASS.

- **Code:** `scripts/q_cont_stability.py`, run from the workspace root.
- **Tables:** `data/qcs_*.csv`.
- **Tests:** `test_credit_quality.py`.
- **Evidence labels:** [E] means recorded CARLA telemetry. [S] means synthetic telemetry on recorded station templates.
- **Data:** 392 in-scope STOP/HOLD episodes [E]; green_stop CARLA fixtures [E]; intent-order behaviours on each scenario's template [S]; 48 instruction-independent policies [S].
- **Unchanged:** strict, v2.2-final, Q-gate and the reference Q-cont.

**Options.** Every option is a parameter setting of Q-cont: a weight, a saturation, or an applicability rule that leaves a term out of the weighted mean.

| option | rule | anchor |
|---|---|---|
| RH_app | R and H are credited only if travel ≥ 1 m or the ego was moving at onset; otherwise R = H = 0 | strict engagement progress (1 m) |
| C_cond | C counts only when a contact occurred | F_safe = 1 is neutral in the published credit, so "no contact" is not a merit |
| P_cond | P is not applicable once the line is crossed | published C2: travel past the line is not approach |

## 1. Why a no-start scores 0.835 [E]

**Components of the no-start fixture:** L = V = R = H = C = 1 and P = 0.01.

- **L, V, R, H and C are vacuous:** the ego never moved, so it never crossed or sped, and the F6h convention "at rest, never moved" gives latency 0.
- **Closed form:** no-start(w_P) = (5 + 0.01 w_P) / (5 + w_P), with every other weight at 1.
- **Break-even weight:** an episode with other-component sum S5 and P = 1 outscores the no-start once w_P > (5 − S5) / 0.99 (`qcs_nostart_wP.csv`).

| case | S5 | w_P* (no-start falls below) |
|---|---:|---:|
| late-stop fixture | 4.707 | 0.30 |
| overshoot fixture | 4.242 | 0.77 |
| Qwen2.5-VL s1 relaunch (green_stop) | 3.786 | 1.23 |
| AIM creep (green_stop) | 3.302 | 1.72 |
| median drive-through, all 114 | 1.335 | 3.70 |
| median drive-through, green_stop | 1.227 | 3.81 |

| w_P | 0.25 | 0.5 | 1 | 2 | 3 | 4 | 5 | 8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| no-start | 0.953 | 0.910 | 0.835 | 0.717 | 0.629 | 0.560 | 0.505 | 0.391 |
| late-stop fixture | 0.944 | 0.947 | 0.951 | 0.958 | 0.963 | 0.967 | 0.971 | 0.977 |
| overshoot fixture | 0.856 | 0.862 | 0.874 | 0.892 | 0.905 | 0.916 | 0.924 | 0.942 |
| AIM creep | 0.677 | 0.691 | 0.717 | 0.757 | 0.788 | 0.811 | 0.830 | 0.869 |
| median drive-through (green_stop) | 0.281 | 0.314 | 0.371 | 0.461 | 0.528 | 0.581 | 0.623 | 0.710 |

**Raising w_P also raises every drive-through,** because a drive-through has P = 1.

**Under RH_app:**

- The no-start scores (L + V + C + 0.01) / 6 = **0.502**, and NO_START episodes range 0.50–0.63.
- Engaged episodes are unchanged.
- The park policy scores 50.0 on STOP/HOLD; under the reference it scores 83.3.

## 2. Why a drive-through keeps ≥ 0.333 [E]

At equal weights, C = 1 (no contact) and P = 1 (moved) give a floor of 2/6.

| setting | drive-through min / median / max | within-label ρ: depth / enforced speed | green_stop drive-throughs | fixture order | dominance violations (1,793) |
|---|---|---|---|---|---:|
| reference | 0.361 / 0.389 / 0.594 | 0.74 / 0.44 | 0.364–0.551 | holds | 20 |
| w_C = w_P = 0.5 | 0.233 / 0.267 / 0.513 | 0.74 / 0.44 | 0.237–0.461 | holds | 20 |
| w_C = w_P = 0.25 | 0.148 / 0.186 / 0.459 | 0.74 / 0.44 | 0.153–0.401 | holds | 20 |
| C_cond + P_cond | 0.041 / 0.084 / 0.391 | 0.74 / 0.44 | 0.047–0.326 | holds | 21 |
| L saturation 2× window | 0.379 / 0.409 / 0.607 | 0.74 / 0.44 | 0.385–0.568 | holds | 20 |
| L saturation published −2 m | 0.353 / 0.372 / 0.521 | **0.59** / 0.52 | 0.354–0.514 | holds | 27 |

**Side effects of lowering w_C and w_P:** the no-start rises (0.901 at 0.5, 0.945 at 0.25), and the park policy rises to 90.0 and 94.4. That is the opposite of section 1.

**C_cond + P_cond:**

- It removes the floor without making any drive-through 0.
- It keeps the within-label ordering.
- It keeps crawl > fast pass-through on green_stop: TCP 0.396 against 0.047–0.137.

## 3. Order stability: normal > late > overrun > drive-through (`qcs_grid.csv`)

**Grid:** w_L and w_R each take 0.25, 0.5, 1, 2, 3 and 4 (others 1), times 4 L saturations, times 2 applicability settings. That is 288 cells, each counting violations or ties on 704 real pairs [E], the synthetic chains [S] and 6 fixture pairs [E].

- **Fixture violations:** 0 in all 288 cells.
- **Real violations or ties never reach 0.** The minimum is 2. They are real feature trade-offs: a shallow, quick overrun against a late stop, and a deep, fast overrun against a slow drive-through.

**Window saturation (reference), cells with 0 synthetic violations:**

| w_L | w_R where synthetic = 0 |
|---|---|
| 2 | 0.25 |
| 3 | ≤ 1 |
| 4 | ≤ 2 |

At equal weights the counts (real / synthetic / fixture) are 8 / 7 / 0.

**Published −2 m or spawn-to-line saturation:** synthetic is 0 at w_L ≥ 1 with w_R ≥ 1, and real is 2–3. But at −2 m, a 2 m and a 70 m crossing get the same L. Under that setting the drive-through depth ρ falls to 0.59 and dominance violations rise to 27.

**Grid region used in section 4:** w_L ≥ 3 × mean(w_V, w_H, w_C, w_P) and w_R ≤ that mean. At window saturation it gives 0 synthetic and 0 fixture violations.

With RH_app + C_cond + P_cond at w_L = 3, w_R = 1, the counts are 6 real / 0 synthetic / 0 fixture.

## 4. Weight sensitivity: 1,000 Dirichlet vectors per set [E] (`qcs_dirichlet_*.csv`)

τ is measured against each setting's equal weights.

| set | τ all: min / p05 / median | τ non-priv: p05 / median | fixture order broken |
|---|---|---|---:|
| reference applicability, full simplex | 0.055 / 0.385 / 0.824 | 0.303 / 0.758 | 0 |
| reference applicability, grid region | 0.385 / 0.780 / 0.846 | 0.727 / 0.788 | 0 |
| RH_app + C_cond + P_cond, full simplex | 0.560 / 0.714 / 0.868 | 0.636 / 0.848 | 0 |
| RH_app + C_cond + P_cond, grid region | 0.626 / 0.714 / 0.802 | 0.727 / 0.848 | 0 |

**Rank 5th–95th percentile under the full simplex:**

| model | reference | RH_app + C_cond + P_cond |
|---|---|---|
| NEAT | 2–7 | 2–4 |
| AIM | 2–8 | 2–4 |
| TransFuser | 2–14 | 5–14 |
| InterFuser | 3–7 | 3–5 |
| GLM-4.5V | 3–12 | 7–10 |
| TCP | 3–12 | 3–7 |
| CILRS | 7–12 | 8–12 |

**Within the grid region** (RH_app + C_cond + P_cond), most models stay within ±1 rank of their median: NEAT 2–4, AIM 2–3, InterFuser 4–6, CILRS 12, OpenEMMA 10–11. The high flip shares there (for example TransFuser–GLM-4.5V 0.97) reflect the move away from equal weights, which lie outside the region, not instability inside it.

**Most frequent flips, full simplex:**

- reference: Qwen2.5-VL–GLM-4.5V 0.55, NEAT–TransFuser 0.52, AIM–TransFuser 0.51;
- RH_app + C_cond + P_cond: Qwen3-VL–CILRS 0.52, MPC–PID 0.44, InterFuser–TCP 0.44.

## 5. Safety-Violation flags [E] (`qcs_sv_report.csv`, `qcs_high_with_sv.csv`)

**A high Q-cont does not mean safe driving or a strict PASS.** 27 of the 192 flagged STOP/HOLD episodes score ≥ 0.8 under the reference.

| label | count ≥ 0.8 |
|---|---:|
| OVERRUN_THEN_STOP | 11 |
| LATE_STOP | 7 |
| COLLISION | 5 |
| STOP_NOT_HELD | 4 |

Examples:

| episode | strict | flags | reference | combined option |
|---|---|---|---:|---:|
| InterFuser fallen_person s0–s2 | FAIL | SV-LINE (−1.1 m) | 0.964 | 0.946 |
| CILRS unauthorized_go | FAIL | SV-LINE (−0.9 m) | 0.961 | 0.942 |
| GLM-4.5V occluded_officer s1 | FAIL | SV-LINE (−1.3 m) | 0.957 | 0.936 |
| InterFuser school_crossing_guard s0 | FAIL | SV-SPEED (16.9 km/h) | 0.894 | 0.872 |
| Qwen2.5-VL flagger_control s0 | FAIL | SV-SPEED (18.7 km/h) | 0.876 | 0.851 |

**Per-model reporting format** (STOP/HOLD in scope; Q-cont episode mean ± SD):

| model | strict PASS rate | Q-cont reference | Q-cont combined option | SV-LINE | SV-NOHOLD | SV-CONTACT | SV-SPEED |
|---|---:|---|---|---:|---:|---:|---:|
| oracle | 1.00 | 1.00 ± 0.00 | 1.00 ± 0.00 | 0 | 0 | 0 | 0 |
| NEAT | 0.21 | 0.81 ± 0.21 | 0.75 ± 0.30 | 0.14 | 0.21 | 0 | 0.36 |
| AIM | 0 | 0.80 ± 0.12 | 0.68 ± 0.21 | 0.14 | 0.50 | 0 | 0.14 |
| TransFuser | 0 | 0.78 ± 0.14 | 0.37 ± 0.08 | 0.14 | 0.14 | 0 | 0.14 |
| InterFuser | 0.24 | 0.79 ± 0.21 | 0.63 ± 0.34 | 0.33 | 0.29 | 0.07 | 0.33 |
| Qwen2.5-VL | 0.17 | 0.76 ± 0.20 | 0.54 ± 0.31 | 0.37 | 0.23 | 0 | 0.49 |
| GLM-4.5V | 0 | 0.75 ± 0.17 | 0.39 ± 0.19 | 0.27 | 0.23 | 0 | 0.29 |
| Qwen3-VL | 0.01 | 0.68 ± 0.22 | 0.31 ± 0.19 | 0.37 | 0.36 | 0 | 0.39 |
| TCP | 0.07 | 0.69 ± 0.12 | 0.59 ± 0.17 | 0.29 | 0.79 | 0 | 0 |
| OpenEMMA | 0 | 0.63 ± 0.21 | 0.34 ± 0.20 | 0.50 | 0.43 | 0 | 0.64 |
| baseline | 0 | 0.58 ± 0.25 | 0.41 ± 0.39 | 0.64 | 0.57 | 0.21 | 0.71 |
| CILRS | 0.14 | 0.53 ± 0.26 | 0.29 ± 0.39 | 0.86 | 0.71 | 0 | 0.79 |
| MPC | 0 | 0.45 ± 0.12 | 0.20 ± 0.21 | 0.93 | 0.86 | 0.14 | 1.00 |
| PID | 0 | 0.44 ± 0.15 | 0.20 ± 0.25 | 0.86 | 0.86 | 0.21 | 0.86 |

The combined option is RH_app + C_cond + P_cond.

## 6. Final comparison [E, S] (`qcs_options.csv`, `qcs_within_label.csv`, `qcs_green_stop.csv`, `qcs_leaderboard.csv`)

**Q-cont* (option, not adopted)** = RH_app + C_cond + P_cond, w_L = 3, window saturation.

| measure | v2.2-final | Q-gate | Q-cont reference | **Q-cont*** |
|---|---|---|---|---|
| deciles, all 392 | 315 13 9 7 3 1 1 0 0 43 | 270 38 6 0 1 5 7 13 10 42 | 0 0 2 68 27 29 19 21 162 64 | 63 29 19 12 7 142 8 24 28 60 |
| FAIL at 0 / distinct levels (4 dp) | 305 / 41 | 263 / 78 | 0 / 184 | 0 / 182 |
| η², label → score (FAIL) | 0.47 | 0.825 | 0.856 | 0.829 |
| ρ FAIL: depth / enforced speed / latency / stopped share | 0.20 / 0.00 / 0.02 / 0.09 | 0.23 / −0.13 / −0.13 / −0.03 | 0.82 / 0.77 / 0.77 / 0.85 | 0.81 / 0.54 / 0.48 / 0.63 |
| within DRIVE_THROUGH ρ: depth / speed | undefined | undefined | 0.74 / 0.44 | 0.79 / 0.41 |
| intent violations: real 704 / synthetic / fixtures | 118 / 18 / 0 | 0 / 0 / 0 | 8 / 7 / 0 | 6 / 0 / 0 |
| dominance violations: leaderboard / fixtures | 12 / 0 | 9 / 3 | 20 / 0 | 20 / 0 |
| near-duplicate max \|Δ\| / fixture seed range | 0.013 / 0.028 | 0.008 / 0.019 | 0.009 / 0.009 | 0.010 / 0.010 |
| DRIVE_THROUGH / NO_START medians (SV: LINE, NOHOLD, SPEED all 100 % / none) | 0 / 0 | 0 / 0 | 0.389 / 0.833 | 0.092 / 0.571 |
| park policy vs best real system (STOP/HOLD) | 0 vs 30.0 | 0 vs 63.5 | **83.3 vs 81.1** | 57.1 vs 78.8 |
| τ vs v2.2-final / vs Q-cont reference | 1 / 0.354 | 0.80 / 0.552 | 0.354 / 1 | 0.464 / 0.802 |

**ρ on all FAILs is lower for Q-cont*.** The 139 no-starts have the best value on every safety feature but now score in the middle (0.57). On the 210 engaged FAILs, RH_app changes nothing.

**green_stop:**

| episode | reference | Q-cont* |
|---|---:|---:|
| normal-stop fixture | 1.000 | 1.000 |
| late-stop fixture | 0.951 | 0.943 |
| overshoot fixture | 0.874 | 0.769 |
| Qwen2.5-VL s1 | 0.798 | 0.676 |
| AIM | 0.717 | 0.757 |
| no-start | 0.835 | 0.573 |
| TCP crawl | 0.597 | 0.453 |
| Qwen2.5-VL s4 | 0.551 | 0.291 |
| constant go | 0.417 | 0.125 |
| drive-throughs (CILRS lowest) | 0.364–0.425 | 0.051–0.142 |

**STOP/HOLD ranks:**

| model | reference | Q-cont* | v2.2-final |
|---|---:|---:|---:|
| oracle | 1 | 1 | 1 |
| NEAT | 2 | 2 | 3 |
| AIM | 3 | 3 | 8 |
| TransFuser | 4 | 7 | 13 |
| InterFuser | 5 | 5 | 2 |
| Qwen2.5-VL | 6 | 6 | 4 |
| GLM-4.5V | 7 | 8 | 11 |
| Qwen3-VL | 8 | 10 | 10 |
| TCP | 9 | 4 | 7 |
| OpenEMMA | 10 | 11 | 9 |
| baseline | 11 | 9 | 6 |
| CILRS | 12 | 12 | 5 |
| MPC | 13 | 13 | 12 |
| PID | 14 | 14 | 14 |

**Spread without a feature basis:** none found.

- Near-duplicate behaviours stay within 0.010 and seed replicates within 0.010, under every option.
- The remaining dominance violations (20) come from R and H credit for momentary or drifting stops. The four-feature safety test does not see these.

## Options for discussion (none adopted)

1. **No-start weight.** RH_app takes the no-start from 0.835 to 0.50 and moves park below every real system. The alternative, w_P ≥ 3.8, also lifts every drive-through.
2. **Drive-through floor.** C_cond + P_cond removes the 0.333 floor (median 0.39 → 0.08) and keeps within-label ρ. Lowering w_C and w_P instead raises the no-start.
3. **Order stability.** w_L ≥ 3 × the other weights with w_R ≤ 1 removes the synthetic violations at window saturation. The −2 m saturation does the same but merges shallow and deep crossings.
4. **Report format.** Report strict pass rate, Q-cont mean ± SD and the 4 SV rates together. Q-cont is never a safety or compliance claim.
