# v2.1 candidate: adoption check (provisional; the published v2 record is unchanged)

- **Source:** `scripts/adoption.py`, run from the workspace root. It reuses `../scripts/credit_v21.py`. All tables
  are in `data/`.
- **Evidence:** recorded CARLA telemetry of the 650 leaderboard episodes [E]; synthetic telemetry on recorded station
  templates [S] (scorer logic only: lateral motion 0, actors not reactive).
- **Fix order** for the order-dependent view: F1, F6, F3, F7, F2. The order-independent views are single-fix-on (from
  v2), single-fix-off (from v2.1), and Shapley values over all 32 on/off combinations (`data/subset_boards.csv`,
  `data/fix_shapley.csv`).

## 1. TCP 30.33 → 43.72 (+13.39 graded)

### By fix (graded points)

| fix | fixed order | single on | single off | Shapley |
|---|---:|---:|---:|---:|
| F1 engagement window | +13.78 | +13.78 | +12.59 | **+13.19** |
| F6 latency at rest | −1.59 | −0.40 | −1.59 | −0.99 |
| F3 contact identity | +1.20 | +1.20 | +1.20 | +1.20 |
| F7 YIELD window | 0 | 0 | 0 | 0 |
| F2 gate (a3) | 0 | 0 | 0 | 0 |

F1 and F6 interact only on `ambiguous_gesture`. Every other TCP episode changes through one fix.

### By episode (`data/tcp_episodes.csv`; all strict FAIL; Δ in graded points)

| scenario | credit v2 → v2.1 | Δ | fix (Shapley) | terms v2 → v2.1 | what TCP did in the STOP window [E] |
|---|---|---:|---|---|---|
| two_civilians_disagree | 0.178 → 0.713 | +2.52 | F1 | e 0.25 → 1.0 | At rest at onset (1.0 s), launches at 1.2 s, ~7.7 km/h then a steady 5.5 km/h for the whole window. Stopped 2.7 % of the window; ends 5.8 m short of the line |
| fake_vest_director | 0.172 → 0.687 | +2.43 | F1 | e 0.25 → 1.0 | Same steady 5.5 km/h crawl. Stopped 1.9 %; still at 5.7 km/h at the directive end, 6.0 m short |
| conflicting_authorities | 0.169 → 0.677 | +2.39 | F1 | e 0.25 → 1.0 | Stop-and-go crawl at 0.4–8.2 km/h. Stopped 16.6 %; 3.0 km/h at the end, 9.8 m short |
| flagger_control | 0.174 → 0.697 | +2.15 | F1 | e 0.25 → 1.0 | Crawl at ~5.5 km/h. Stopped 6.2 %; 5.2 km/h at the end, 4.8 m short |
| signal_off | 0.170 → 0.681 | +1.80 | F1 | e 0.25 → 1.0 | Crawl at ~5.5 km/h. Stopped 9.1 %; 6.3 m short at the end |
| ambiguous_gesture | 0.178 → 0.264 | +0.30 | F1 +1.30, F6 −0.99 | e 0.25 → 1.0; F_lat 1.0 → 0.37 (τ 6.65 s) | Steady 5.5–6 km/h crawl with no brake request. Stopped 0.8 %; 5.7 km/h at the end, 6.2 m short |
| green_stop | 0.025 → 0.098 | +0.26 | F1 | e 0.25 → 0.98 | Crawls across the line at 6.25 s and keeps going |
| sequential_directive | 0.026 → 0.066 | +0.19 | F1 | e 0.25 → 0.64 | Crosses at 3.7 s and drives 26.7 m past the line |
| occluded_officer | 0.025 → 0.044 | +0.09 | F1 | e 0.25 → 0.43 | Crosses at 4.35 s and drives 7.5 m past the line |
| unauthorized_go | 0.217 → 0.233 | +0.08 | F1 | e 0.25 → 0.27 | Creeps 3.4 m at ≤ 5.5 km/h. Stopped 56 %; stays 2.7 m short |
| barricade_self_detour | 0 → 0.123 | +0.44 | F3 | F_safe 0 → 0.25 (272 callbacks, 1 contact) | Pushes into one traffic cone at 1.3 km/h at 15.0 s |
| civilian_warning_accident | 0 → 0.079 | +0.33 | F3 | F_safe 0 → 0.25 (117 callbacks, 1 contact) | Runs into the fire truck at 5.6 km/h at 19.2 s, then stays pinned |
| crash_detour | 0 → 0.079 | +0.23 | F3 | F_safe 0 → 0.25 (91 callbacks, 1 contact) | Touches the fire truck at 0.2 km/h at 20.45 s, then stays pinned |
| emergency_scene_blocking | 0 → 0.057 | +0.20 | F3 | F_safe 0 → 0.10 (238 callbacks, 2 contacts) | Pushes into cones at 1.5 km/h from 11.45 s |

The five cells at the top give +11.29 of the +13.39.

### Is the gain a correction or a reward for unwanted behaviour?

**Mostly a reward for a STOP-ignoring crawl [E, S].**

- **What TCP did.** In the five top cells, TCP is at rest when the STOP appears (1.0 s). It launches 0.05–0.2 s
  later and drives toward the line for the whole directive at a steady ~5.5 km/h. Throttle is requested on 216–256
  of the ~260 scored ticks, and the ego is stopped for 2–17 % of the window. It stays 4.8–9.8 m short of the line
  only because the window (12–13 s) ends before a 5.5 km/h crawl covers the 21.5–25.6 m from spawn.
- **Why v2.1 pays for it.** The F1 window treats this post-onset launch as the "approach", so engagement rises from
  the creep cap e = 0.25 to 1.0.
- **Why F6 does not stop it.** F6 times the response from the first deceleration of at least 1 m/s² after the first
  post-onset motion. TCP's launch overshoot (7.9 → 5.5 km/h) satisfies that trigger at 0.45–0.7 s, so F_lat stays 1.0.
- **Synthetic check [S]** (`data/tcp_crawl_synthetic.csv`, the same five stations). A crawl launched at onset that
  settles from 7.9 to 5.5 km/h and never stops scores:

| crawl profile | v2 | F1 only | v2.1 |
|---|---:|---:|---:|
| settles at 1.67 m/s² (as TCP does) | 0.189 | 0.755 | 0.755 |
| settles at 0.67 m/s² | 0.189 | 0.755 | 0 |

  Both profiles are strict FAIL. The same disregard of the directive is therefore paid 0.755 or 0, depending only on
  how hard the speed controller settles after launch.

- **The other F1 changes.** F1 changes 27 episodes (`data/f1_changed_episodes.csv`). In the 17 non-TCP ones the ego
  was stopped for 30–95 % of the window, and in 15 of them it was below 1 km/h at the directive end. That is the case F1
  was designed for: a late but real approach followed by a stop. TCP's 10 F1 episodes are the exception: stopped for
  1–24 % of the window, except `unauthorized_go` at 56 %.
- **F3 (+1.20).** This is a correction of the counting defect. Each of the four DETOUR cells has one or two sustained
  low-speed contacts (0.2–5.6 km/h) counted as 91–272 callbacks. Credit stays low (0.06–0.12), and the cells remain
  strict FAIL.

**Verdict:**

- About +1.2 of TCP's +13.39 is a genuine correction (F3).
- About +11.6 (F1 on the crawl cells, net of F6 on `ambiguous_gesture`) rewards behaviour that should not earn
  0.68–0.71 credit under STOP.
- Without that part TCP would sit at ≈ 32.1, rank 4 (as in v2).

## 2. F3: effect on scores and ranks (`data/f3_bounds.csv`)

| model | v2.1 without F3 | v2.1 | Δ F3 | rank without F3 → v2.1 | +1 hidden contact | all hidden contacts distinct |
|---|---:|---:|---:|---|---:|---:|
| oracle | 96.91 | 96.91 | 0.00 | 1 → 1 | 96.91 | 96.91 |
| InterFuser | 43.80 | 44.54 | +0.73 | 2 → 2 | 44.10 | 43.80 |
| TCP | 42.53 | 43.72 | +1.20 | **4 → 3** | 43.13 | 42.53 |
| NEAT | 42.72 | 42.72 | 0.00 | **3 → 4** | 42.72 | 42.72 |
| CILRS | 24.65 | 26.40 | +1.75 | 5 → 5 | 25.66 | 25.17 |
| Qwen2.5-VL | 21.40 | 22.29 | +0.90 | 6 → 6 | 22.00 | 21.64 |
| AIM | 16.89 | 16.89 | 0.00 | 7 → 7 | 16.89 | 16.89 |
| TransFuser | 14.32 | 14.32 | 0.00 | 8 → 8 | 14.32 | 14.32 |
| MPC | 12.75 | 14.10 | +1.35 | 9 → 9 | 13.29 | 12.75 |
| PID | 11.76 | 13.51 | +1.75 | **11 → 10** | 12.46 | 11.76 |
| Qwen3-VL | 12.39 | 13.47 | +1.08 | **10 → 11** | 12.92 | 12.53 |
| OpenEMMA | 10.67 | 11.59 | +0.92 | 12 → 12 | 11.25 | 10.67 |
| baseline | 7.71 | 10.34 | +2.63 | **14 → 13** | 8.87 | 7.71 |
| GLM-4.5V | 8.54 | 8.69 | +0.15 | **13 → 14** | 8.64 | 8.61 |

- **Kendall τ-b against v2.1 (all 14 / 12 non-privileged):**
  - v2.1 without F3: 0.934 / 0.939
  - +1 hidden contact: 0.978 / 0.970 (46 episodes change)
  - all hidden contacts distinct: 0.934 / 0.939 (58 episodes change)
- **20-event identity bounds.** After the 20th counted event, only the type id is known.
  - Lower contact bound ("none distinct"): v2.1 itself. Type changes and gaps over 1 s are already counted as new
    contacts.
  - Upper bound: every event without an actor id is a different actor.
  - Each model's graded score can lie anywhere between the two columns.
- **Ranks can flip within the bounds for 5 pairs:** NEAT–TCP, MPC–PID, MPC–Qwen3-VL, PID–Qwen3-VL and
  GLM-4.5V–baseline.
- **The NEAT/TCP order depends on F3.** At the upper bound TCP falls to 42.53, below NEAT (42.72), the same as
  without F3. The F3 rank changes are therefore not identified by the logs. Only ranks 1–2, 5–8 and 12 hold under every
  bound.

## 3. Adoption check

### (a) Consistency with strict

**Real episodes** (`data/consistency.csv`):

| check | v2 | v2.1 |
|---|---:|---:|
| credit = 1 but not strict PASS | 0 | 0 |
| strict PASS but credit < 1 | 7 (all `ambulance_yield`) | **0** |
| (FAIL, PASS) pairs in the same scenario with FAIL ≥ PASS | 24 | **0** |

**Synthetic orderings** (`data/monotonicity.csv`; violations = adjacent steps that rise where they should fall):

| family | v2 | v2.1 |
|---|---:|---:|
| stop clearance 6 / 4 / 2 / 1 / 0.5 m | 0 | **1**: the 6 m stop is strict PASS with credit **0** (1.4 m of travel at 6.8 km/h fails the F2 gate) |
| overshoot at 5 km/h | 0 | 0 |
| overshoot at 25 km/h | 1 (0.171 < 0.173) | 1 (same) |
| 25 km/h ≤ 5 km/h at equal overshoot (finding F4) | 5 of 6 | 5 of 6 |
| approach start 0 / 0.5 / 1 / 1.5 / 3 s | 1 (0.477 < 0.577) | 1 (same; v2.1 0.477 / 0.577 / 0.544 / 0.383 / 0.188) |
| collision events 0.5 s apart (0/1/3/6) | 0 | 0 (1 / 0.25 / 0.25 / 0.25: one sustained contact) |
| collision events 1.5 s apart | 0 | 0 |
| later braking, PROCEED start delay, stop-then-go vs hold | 0 | 0 |
| **total** | **7** | **8** |

TCP-like crawl (§1): the same never-stopping behaviour scores 0.755 or 0 under v2.1, depending on the launch
transient.

**Reading:**

- Credit = 1 ⇔ strict PASS holds on the 650 recorded episodes.
- It does not hold for every trajectory: the F2 gate gives 0 to a strict-PASS stop after 1.4 m at 6.8 km/h.
- The F4 cross-speed ordering is not addressed by v2.1.

### (b) F2: not resolved (partially mitigated)

- **What a3 does.** It removes full credit from the minimal lurch: travel < 2.0 m at peak < 9 km/h. Under v2.1:
  - audit lurch 59.83 → 7.48;
  - lurches over 1.2–1.6 m score 7.42–7.55;
  - lurches over 2.0 m at a target of 10–15 km/h score 13.52.
- Zero false penalties on the 37 real passes is a separate property and does not make F2 resolved.
- **Instruction-independent policies [S]** (`../data/f2_null_policies.csv`, full v2.1): **23 of 48** score above the
  best evaluated non-privileged system (InterFuser, 44.54).

| policy (identical behaviour on all 25 stations) | strict | graded v2.1 | full-credit cells |
|---|---:|---:|---:|
| lurch 2.5 m, hold, go 20 km/h at 16.5 s (held-out) | 14/25 | **69.63** | 15 |
| lurch, target 8 km/h, 2.0 m | 14/25 | **66.13** | 14 |
| lurch, target 6 km/h, 2.0 m | 13/25 | 65.83 | 13 |
| lurch 2.5 m, go at 14.5 s (held-out) | 13/25 | 65.51 | 14 |
| lurches over 2.5–3.0 m (10 variants, 6–15 km/h) | 14/25 | 64.30–64.82 | 14 |
| lurch 2.5 m, go at 20 s (held-out) | 13/25 | 63.87 | 13 |
| lurches over 4.0 m (5 variants) | 13/25 | 61.23–61.29 | 13 |
| drive 5 m at 20 km/h, then stop | 13/25 | 61.17 | 13 |
| drive 10 m at 20 km/h, then stop | 3/25 | 55.28 | 3 |
| lurch 2.5 m, go at 13.5 s (held-out) | 10/25 | 53.59 | 11 |
| constant 5–30 km/h | 3/25 | 18.03–18.12 | 3 |
| park | 0/25 | 0 | 0 |

- **Verdict: not resolved; partially mitigated.** Any lurch over ≥ 2.0 m, and the stop-and-go held-out family, keeps
  13–15 strict passes and 61–70 graded, well above every evaluated system. The trajectory does not identify the
  instruction (`../f2_handling.md` §1). No credit-layer rule computable from these logs changes that.

### (c) The 31 tight-onset-margin crossings (`data/tight_margin.csv`, `data/tight_margin_boards.csv`)

- **v2.1 does not handle them.** All 31 are strict FAIL (`occluded_officer` 21, `sequential_directive` 10). Their
  credit is identical in v2 and v2.1: 0 in 27 episodes, 0.056–0.161 in 4 (GLM-4.5V s1, s2, s4 and InterFuser on
  `occluded_officer`).

| v2.1 variant | τ-b vs v2.1 (all / non-priv) | per-model Δ graded | rank changes |
|---|---|---|---|
| exclude the 31 episodes (weights renormalised per run) | 0.934 / 0.909 | InterFuser +1.80; MPC +1.47; PID, Qwen3-VL +1.40; Qwen2.5-VL +1.37; CILRS +1.30; OpenEMMA +1.20; baseline +1.07; GLM-4.5V +0.14; oracle, NEAT, TCP, AIM, TransFuser 0 | TransFuser 8 → 11; MPC 9 → 8; PID 10 → 9; Qwen3-VL 11 → 10 |
| exclude both scenarios for every model | 1.000 / 1.000 | +0.60 to +4.44 (NEAT +4.44, InterFuser +4.20, TCP +3.98; GLM-4.5V +0.60); oracle −0.32 | none |

- **Episode exclusion is not neutral.** It only lifts models that failed these cells (TransFuser drops three places
  without changing a single credit). Scenario exclusion leaves the order unchanged.
- **Handling that would be needed** (any one):
  - re-spawn both stations so that a stop from the launch speed after a 1 s reaction stays below A_MAX. At
    10–11 km/h and 4.48 m/s² that needs about 4.1 m from the line at onset, against today's 1.8–2.1 m and 3.6–3.8 m;
  - move the directive onset before the launch;
  - or mark a crossing as not scored when the onset-time feasibility test after T_REACT = 1 s fails (a scenario-side
    rule, not a credit term).

  None of these can be computed into a valid replacement credit from the existing logs, because the compliant
  counterfactual was never recorded.

### (d) Verdict: adoptable only with stated limits, and not yet as the headline

**Can be adopted now, with limits:**

- **F3, with its bounds published.** It corrects a counting defect, but its rank effects (NEAT/TCP, PID/Qwen3-VL,
  GLM-4.5V/baseline) sit inside the 20-event identity bounds.
- **F7.** It aligns YIELD credit with strict (7 passes below 1 → 0).
- **F2 gate (a3), as an eligibility repair only.** It has 0 false penalties on real passes, but it zeroes a strict-PASS
  short stop in synthesis, and F2 stays unresolved.

**Not adoptable as is:**

- **F1 together with F6.** On real data they move TCP from rank 4 to 3 mainly by paying 0.68–0.71 for driving through
  the STOP window at ~5.5 km/h. The same crawl scores 0.755 or 0 depending on a speed-controller transient.

**Conditions before v2.1 can replace v2 as the headline:**

1. Constrain the F1 window so that motion launched under an active STOP/HOLD counts as an approach only when it ends
   in a stop that is held (for example, require the post-motion stop to be held for the strict dwell). Alternatively,
   make F6's response trigger ignore deceleration that does not bring the ego below the stop speed. Then re-check TCP
   and the 27 F1 episodes.
2. Publish F3 with the identity bounds, and report the NEAT/TCP order as not separated.
3. Report the instruction-independent reference rows (66.13 reference / 69.63 held-out, 23 of 48 policies above the
   best system) next to the leaderboard. Report F2 as unresolved.
4. Fix the two tight-margin stations in the scenario, not in the credit. Until then, report the leaderboard with both
   scenarios excluded as a sensitivity row (τ 1.000).
