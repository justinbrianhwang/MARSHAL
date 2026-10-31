# Credit v2.2: verification checks

**Source and tests:**

- `scripts/checks.py` produces items 1–3: `data/item1_reaction_timing.csv`, `data/item2_f4_trace.csv`,
  `data/item3_step_trace.csv`, `data/monotonicity_variants.csv`, `data/variant_effects.csv` and
  `data/variant_boards.csv`.
- `scripts/final_leaderboard.py` produces item 5: `data/final_leaderboard*.csv`.
- Tests: `test_checks.py` (3 passed).
- Run everything from the workspace root.

**Evidence and scope:**

- [E] is recorded CARLA telemetry (650 leaderboard episodes). [S] is synthetic telemetry on the `green_stop` station
  template (scorer logic only).
- Every variant below changes one existing term of v2.2. None adds a term.
- The v2, v2.1 and v2.2 outputs are not modified.

## 1. F6h response time vs the strict reaction criterion

**What each scorer measures:**

| | strict STOP/HOLD | F6h (v2.2) |
|---|---|---|
| criterion | every row from onset + budget (3 s; scenario budget if larger) to the directive end has speed < 1 km/h | latency to the first held stop, full credit up to max(3 s, budget), linear to 0 at +5 s |
| time point | when the ego reaches stop speed **for good** (the final stop, kept to the directive end) | the **start** of the first 2.0 s window at ≤ 1 km/h with drift ≤ 0.5 m |
| role of the hold | the stop must last to the directive end | the 2 s hold is a qualifying condition and is not added to the latency. Latency is taken at hold start |
| anchor | onset | onset. For an ego at rest at onset, its first post-onset launch |

**Agreement on the 650 episodes [E]** (`item1_reaction_timing.csv`, 390 valid STOP/HOLD episodes):

| strict reaction | F6h latency within its plateau | episodes |
|---|---|---:|
| within budget | yes | 191 |
| outside budget | no | 167 |
| **within budget** | **no** | **0** |
| **outside budget** | **yes** | **32** (29 engaged) |

- **The disagreement goes one way only.** No ego that strict counts as reacting in time loses F6h credit.
- **The 32** are all strict FAIL. They reach a held stop in time but do not keep it to the directive end:
  - 22 are egos at rest at onset that launch under the active STOP and re-stop, so F6h times them from the launch;
  - 10 are egos that stop in time and later roll on.
- Their v2.2 credit is already reduced by the speed-margin term (maximum speed after the deadline), and 19 of the 32
  score 0.

**Minimal alignment ("final hold").** Take the F6h response at the start of the stop that is held to the directive
end, which is the strict time point. Keep the same 2 s / 0.5 m hold test and the same anchors.

- Effect on the 650 [E]: 11 episodes change, all downward.
  - Graded points: AIM −1.35, Qwen2.5-VL −1.15, NEAT −0.57, Qwen3-VL −0.34, MPC −0.08, GLM-4.5V −0.05.
  - No rank changes; τ-b vs v2.2 is 1.000 / 1.000.
  - Strict consistency is unchanged.
- **Recommendation: keep F6h as defined.**
  - Both scorers time the moment stop speed is reached, and their disagreement only ever goes one way.
  - Failing to keep the stop is what strict and the speed-margin term score. Counting it again in latency would
    penalise it twice.
  - The variant is available if exact time-point identity with strict is preferred. It moves no rank.

## 2. The five F4 violations (25 km/h overrun above 5 km/h at equal overshoot)

**Term trace [S]** (`item2_f4_trace.csv`):

| overshoot | 5 km/h (a = 1.0) | 25 km/h (a = 4.0) | terms that differ |
|---|---|---|---|
| 0 m | 0.076 | 0.486 | speed margin 0.636 vs 1.0; e 0.25 (creep cap) vs 0.561; F_lat 0.47 (held stop 5.65 s after onset) vs 1.0 (1.9 s) |
| 0.1 m | 0.025 | 0.171 | same three terms; both enter the conflict zone (× 0.35 applies to both) |
| 0.5 m | 0.020 | 0.173 | same |
| 2 m | 0.006 | 0.164 | same |
| 10 m | 0 | 0.081 | 5 km/h: no held stop inside the window (F_lat 0) |

**Two causes:**

1. **Stop timing, the intended criterion.** The 5 km/h profile stops 6.9–14 s after spawn (43 s at 50 m). The 25 km/h profile
   stops 2.9–4.5 s after spawn.
   - The slow ego is still moving after the 3 s reaction deadline. That costs it speed margin and latency, as strict
     intends: strict fails it on "did not remain stopped".
   - Ordering by impact speed instead of stop timing is not a criterion of any existing term. It would need a new
     term.
2. **The creep cap.** Approach-speed credit is 0 at ≤ 5 km/h, so engagement is capped at 0.25.
   - The cap's documented purpose is "stayed below 5 km/h far upstream". The slow ego here reached and crossed the
     line, which is outside that purpose.

**Diagnostic with timing held equal [S].** The 25 km/h profile is launched later so it stops at the same time as the
5 km/h one.

- v2.2 still ranks it higher in 3 of 6 overshoots: 0.190 vs 0.076, 0.064 vs 0.025, 0.054 vs 0.020.
- The cause is cause 2 alone (e 0.71 vs 0.25).

**Minimal in-term fix ("creep cap only far upstream"):** apply the existing cap only when the minimum distance to
the line exceeds the existing engagement radius (`stopline_engagement_m`, 16.59 m).

- Equal-timing violations: 3 → 0.
- The F4 family as specified keeps its 5 violations, because the timing difference is the intended criterion.
- Effect on the 650 [E]: 2 episodes, AIM +0.06; no rank change; τ-b 1.000.
- **Recommendation: adopt** (it corrects the cap's scope).
- **Not fixable without a new criterion:** "a faster overrun scores no higher than a slower, later one." That is a
  property of a timing-based metric. It is reported as a limit.

## 3. The other two synthetic inversions

| inversion [S] | cause (term, published curve) | minimal in-term fix | synthetic result | effect on the 650 [E] | recommendation |
|---|---|---|---|---|---|
| 25 km/h overrun: 0.1 m → 0.171, 0.5 m → 0.173 | Clearance falls (0.634 → 0.500), but the engagement approach distance grows with the overrun (6.15 → 6.55 m, e 0.572 → 0.616). It is computed from the vehicle-centre `distance_to_stopline_m`, which keeps shrinking while the front bumper is already past the line | **front-capped approach distance:** onset front-to-line − max(minimum front-to-line, 0). This uses the `stopline_front_m` column the clearance term already uses, so travel past the line is not approach | 0.168 ≥ 0.157 ≥ 0.118 ≥ 0.055 (monotone) | 25 episodes (13 up, 12 down): CILRS −0.36, InterFuser −0.25, GLM-4.5V −0.18, Qwen2.5-VL −0.03, AIM +0.04; no rank change; τ-b 1.000 | **adopt** (the term measured the wrong quantity) |
| approach start 0 s → 0.477, 0.5 s → 0.577 | The engagement approach distance is anchored at the onset row (1.0 s). An ego that started at 0 s has already covered ≈ 1 m before onset, so its counted approach is shorter (5.60 vs 6.56 m; e 0.511 vs 0.618) | **spawn-anchored approach distance:** measure from the first scored row | 0.612 vs 0.613; the 0.0006 residual is 50 ms stop-position quantisation | 22 episodes, all up: NEAT +0.39, baseline +0.39, MPC +0.20, InterFuser +0.16, CILRS +0.13, GLM-4.5V +0.11, Qwen2.5-VL +0.07; no rank change; τ-b 1.000 | **keep** |

**Why keep the onset anchor:**

- It defines engagement as approach under the directive. That is a definitional choice, not a computation error.
- The fix only raises 22 strict-FAIL credits and moves no rank.

**Adopted set** (`front_cap + creep_cap_far_only`) on the 650 [E]:

- 26 episodes change (14 up, 12 down).
  - Graded points: CILRS −0.36, InterFuser −0.25, GLM-4.5V −0.18, Qwen2.5-VL −0.03, AIM +0.29.
  - No rank changes; τ-b vs v2.2 is 1.000 / 1.000.
- Strict consistency is unchanged: credit = 1 ⇔ PASS (0 / 0).
- Synthetic ordering violations: 7 → 6. Remaining: the 5 F4 timing cases and the 0.477/0.577 anchor step.
- Equal-timing diagnostic: 3 → 0.

## 4. Scope of Credit with respect to F2

**What Credit evaluates: observable trajectory compliance.**

- Stop, hold and proceed timing relative to the directive (latency, held stop, start delay).
- Line position: front-bumper clearance and conflict-zone entry.
- Speed while a directive is enforced.
- Approach engagement, safety (distinct contacts) and the detour or yield geometry.

**What it cannot evaluate: whether the authority and the instruction were perceived and reasoned about.** A
trajectory that complies because the instruction was understood can be identical to one produced without it.

**Evidence:**

- **48 instruction-independent policies [S].** These drive the same speed profile on all 25 stations, ignoring every
  directive. Under v2.2:
  - 37 of the 48 score above the best evaluated non-privileged system (40.93);
  - the best, "2.5 m lurch, hold, go at 16.5 s", scores 69.63 graded with 14/25 strict, rank 2 of 14 behind the
    oracle only;
  - the optional a3 gate lowers the count to 23 but leaves the maximum at 69.63.
- **Lurch-then-stop fixture [E].** It is strict PASS with credit 1.0 in 3/3 seeds under v2.2, the same as the normal
  stop.
- **The oracle on `green_stop` without the officer [E]** stops after 1.48 m and scores PASS / 1.0.

**Remedy: matched counterfactual runs**, i.e. the same station and traffic with the officer present vs absent, or STOP
vs PROCEED. Instruction following is then scored as the difference in behaviour. This needs new rollouts. Credit v2.2
does not resolve F2. F2 is reported as a limit of the metric.

## 5. Final candidate leaderboard script (`scripts/final_leaderboard.py`)

**Inputs:**

- Every model keeps its protocol-v2 runs.
- InterFuser is replaced by the re-measured fixed-adapter runs (`tmp/_remeasure_interfuser/fixed/interfuser/s<seed>`
  in the audit checkout, or `--remeasure DIR`).
- Only seeds with all 25 scenarios accepted are used. The InterFuser row reports the mean, SD and min–max over those
  seeds.

**Inputs of this table [E]:** no complete fixed-adapter seed was available (s0: 16/25 scenarios accepted), so the
InterFuser row below is its protocol-v2 run, labelled "pending" (`data/final_leaderboard_coverage.csv`). The
leaderboard with the completed fixed-adapter seeds 0–2 is
[../../credit_v22_final/final_leaderboard.md](../../credit_v22_final/final_leaderboard.md).

**Verification:** pointed at the v2 run tree, the re-measure path reproduces the v2 InterFuser row (40.93 under v2.2).
This is pinned in `test_checks.py`.

**Columns and sensitivity rows** (`data/final_leaderboard.csv`, `data/final_leaderboard_sensitivity.csv`), current
inputs:

| model | v2 | **v2.2** | v2.2 + adopted fixes | v2.2, all hidden contacts distinct | v2.2 without F3 | v2.2 without the two geometry scenarios |
|---|---:|---:|---:|---:|---:|---:|
| oracle | 96.91 | **96.91** (1) | 96.91 (1) | 96.91 (1) | 96.91 (1) | 96.59 (1) |
| InterFuser (pending) | 43.53 | **40.93** (2) | 40.68 (2) | 40.20 (2) | 40.20 (2) | 44.76 (2) |
| NEAT | 42.91 | **33.62** (3) | 33.62 (3) | 33.62 (3) | 33.62 (3) | 36.92 (3) |
| CILRS | 24.57 | **26.40** (4) | 26.05 (4) | 25.17 (4) | 24.65 (4) | 29.15 (4) |
| Qwen2.5-VL (5 runs, SD 3.70) | 20.75 | **21.89** (5) | 21.86 (5) | 21.23 (5) | 20.99 (5) | 24.16 (5) |
| TCP | 30.33 | **20.68** (6) | 20.68 (6) | 19.48 (6) | 19.48 (6) | 22.83 (6) |
| TransFuser | 14.32 | **14.32** (7) | 14.32 (7) | 14.32 (7) | 14.32 (7) | 15.81 (7) |
| MPC | 12.75 | **14.10** (8) | 14.10 (8) | 12.75 (8) | 12.75 (8) | 15.57 (8) |
| PID | 11.76 | **13.51** (9) | 13.51 (9) | 11.76 (10) | 11.76 (10) | 14.91 (9) |
| Qwen3-VL (5 runs, SD 2.50) | 11.65 | **13.47** (10) | 13.47 (10) | 12.53 (9) | 12.39 (9) | 14.86 (10) |
| OpenEMMA | 10.61 | **11.87** (11) | 11.87 (11) | 10.95 (12) | 10.95 (12) | 13.11 (11) |
| AIM | 13.23 | **11.30** (12) | 11.59 (12) | 11.30 (11) | 11.30 (11) | 12.48 (12) |
| baseline | 7.71 | **9.11** (13) | 9.11 (13) | 6.48 (14) | 6.48 (14) | 10.05 (13) |
| GLM-4.5V (5 runs, SD 0.82) | 8.14 | **8.61** (14) | 8.43 (14) | 8.53 (13) | 8.46 (13) | 9.20 (14) |

**Kendall τ-b vs v2.2** (all / non-privileged):

- adopted fixes: 1.000 / 1.000;
- F3 all-distinct bound: 0.934 / 0.939;
- without F3: 0.934 / 0.939;
- without the two geometry scenarios: 1.000 / 1.000;
- v2: 0.846 / 0.818.

**Pairs not separated by the F3 bounds:** MPC–PID, MPC–Qwen3-VL, PID–Qwen3-VL, PID–OpenEMMA, OpenEMMA–AIM and
GLM-4.5V–baseline.

With complete fixed-adapter seeds, the script replaces the InterFuser row and recomputes every column and sensitivity
row; no other input changes.
