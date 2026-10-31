# Scoring formulation — exact v2 implementation (audit)

Pinned code: commit `e6ae3c8` (scorers unchanged through `2c84544`): `marshal_bench/criteria/strict_episode_scoring.py`
(“S”) and `marshal_bench/criteria/graded_episode_scoring.py` (“G”), `protocol="v2"`. Evidence status for every
statement: **inspected in code** unless marked **reproduced** (recomputed from recorded telemetry by
`scripts/decompose.py`, which captures the published collector's own scorer calls). The feature-to-score map is
[`data/feature_score_map.csv`](data/feature_score_map.csv); thresholds and their provenance are in
[`data/threshold_inventory.csv`](data/threshold_inventory.csv).

## 1. Order of operations for one episode (G `score_episode_from_telemetry`, l.1313)

1. **Horizon.** Rows after 25.0 s are dropped (`_horizon_raw`).
2. **Normalisation and validation.** Missing, non-finite, or wrong-typed required columns make the episode
   **INVALID**, and Credit = 0 (`_normalise_rows` l.249, `_validate_common` l.333). The same happens on setup or
   adapter errors, or non-finite controls.
3. **Windows.** `v2_windows` (S l.107) returns the scored rows. For STOP/HOLD they run up to the end of the
   enforced directive, which is the last STOP/HOLD phase read from the live officer state (`_stop_hold_window`,
   S l.480). For every other action they run to the full horizon.
4. **v2 stop-line check.** A stop-line STOP/HOLD without `stopline_front_m` is INVALID. `dual_authority_handoff`
   is exempt.
5. **Action Credit A.** Computed by an action-specific function (§3).
6. **Latency factor F_lat** (§4).
7. **Safety factor F_safe** (§5).
8. **Credit** = clip(A, 0, 1) · F_lat · F_safe.
9. **Movement gate.** If max v < 5 km/h or progress (max x − min x) < 1 m on the scored rows, Credit = 0
   (`_movement_evidence`, l.504).
10. **Stored precision.** Credit is clipped to [0, 1] and rounded to 6 decimals (`_score`, l.225).
11. **Collector override.** If the in-episode strict verdict was INVALID, the collector forces Credit = 0
    (`collect_protocol_v2.py` l.122–125). This happened in 0 leaderboard episodes (**reproduced**).

## 2. Aggregation

```math
\text{MARSHAL-Graded(run)} = 100\cdot\frac{\sum_{s=1}^{25} w_s C_s}{\sum_s w_s},\qquad \sum_s w_s = 42.5
```

- The model value is the mean over runs. The reported SD is the **sample SD (n − 1)** over the five VLM runs.
- Published values are rounded to 2 decimals.
- A run counts only if all 25 cells are present and accepted. The collector refuses an episode on any of:
  protocol version ≠ v2, a telemetry gap, a horizon < 25 s, or a VLM query-schedule violation.
- **Reproduced:** all 14 graded means, SDs, strict totals and override counts match the published
  `leaderboard_v2.csv` exactly ([reproduction_tables](data/reproduction_tables.md)).
- The 12 override cells are the `override` + `stressed-override` families of `marshal_metrics.CONFLICT_TYPE`:
  adjacent_lane, ambiguous_gesture, crash_detour, flagger_control, flagger_slow_then_stop, green_stop,
  night_signal_officer_conflict, occluded_officer, red_proceed, school_crossing_guard, sequential_directive,
  signal_off.

Strict success counts are a separate quantity: a PASS count per run, pooled over runs as k/n. MARSHAL-Graded is
also distinct from the legacy R1–R9 metric suite (`marshal_metrics`), which is not part of the leaderboard.

## 3. Action Credit by action

L(x; a, b) = clip((x − b)/(a − b), 0, 1): 1 at or beyond a, 0 at or beyond b, linear in between (G l.181). A
ramp has kinks at a and b and no jumps. Every input is converted to a dimensionless value in [0, 1] **before**
any combination. Meters, seconds and km/h are never added together. Speeds stay in km/h throughout; no m/s
conversion enters a score term.

### STOP / HOLD (G `_score_stop_hold`, l.862)

The STOP window is [onset, end]. The enforcement window is [onset + B, end], where B is the reaction budget
(3 s, or 3.5 s where the scenario sets it).

```math
\begin{aligned}
C_{\text{speed}} &= L(v_{\max,\text{enf}};\,1,\,12) && v_{\max,\text{enf}}: \text{enforcement window (STOP window if empty)}\\
C_{\text{clear}} &= L(f_{\min};\,1.0,\,-2) && f_{\min}: \text{front bumper to line, STOP window}\\
\text{base} &= 0.60\,C_{\text{speed}} + 0.40\,C_{\text{clear}} && (\text{base} = C_{\text{speed}} \text{ without a stop line or with an approach requirement})\\
A &= \text{base}\cdot 0.35^{[\text{conflict}]}\cdot 0.75^{[\text{no enforcement rows}]}\cdot e\cdot a
\end{aligned}
```

- **conflict:** a STOP-window row with `in_junction` and f < 0. It is not evaluated for `dual_authority_handoff`.
- **a:** the handoff approach factor (product of band terms, l.724). a = 1 in every other scenario.
- **Engagement e** (l.517–721):
  - **e = 0** if the ego has no movement evidence on the scored rows.
  - **e = 1** if the STOP/HOLD telemetry meets the strict conditions: enforcement rows exist, no conflict,
    f_min ≥ 1.0 m, near a stop line or hazard, stop-anchor engaged, moved, and v_max,enf < 1 km/h.
  - **Otherwise:**

    ```math
    e = \max\!\big(L(v_{\text{app}};10,5)\,L(d_{\text{app}};10,1),\ \ \min\{L(d_{\text{app}};10,1)\,L(d_{\min};15,40),\ c\}\big),
    \quad c = 0.25 \text{ if } L(v_{\text{app}};10,5)=0 \text{ else } 1
    ```

  - v_app is the max speed over the rows up to the **approach end**. The approach end is min(directive end,
    first tick ≥ onset with v ≤ 1 km/h), or onset + B if there is none.
  - d_app is the stop-line distance at the onset row minus its minimum. It falls back to forward progress when
    there is no stop line.

**Audit finding F1 (reproduced).** Under the v2 rest start (0 m/s, onset 1.0 s), an ego still at rest at onset
has "first tick with v ≤ 1 km/h" = the onset tick. Its approach window is therefore [0, onset], so
v_app ≈ 0 → L(v_app) = 0, and e ≤ 0.25 (creep cap).

- This applies to any approach that starts at or after the directive appears.
- In recorded data it happens in 79 STOP/HOLD episodes; 65 of them reached ≥ 10 km/h.
- **Synthetic check:** the same 15 km/h approach stopping 0.5 m before the line scores 0.477 / 0.577 / 0.207 /
  0.146 / 0.083 when started at 0.0 / 0.5 / 1.0 / 1.5 / 3.0 s.
  ([counterfactual_tests](counterfactual_tests.md), [tests/test_engagement_timing.py](tests/test_engagement_timing.py)).

### PROCEED (`red_proceed`, `out_of_jurisdiction_director`, `night_signal_officer_conflict`; l.1037)

Promptness is measured from t_safe (S `safe_to_proceed_time`, l.143). The entry is the first row in the junction
that has crossed the line (f < −0.3 m on the signed centre column, l.1010).

```math
A=\begin{cases}0.65\,L(v_{\text{entry}};2,0)+0.35\,L(t_{\text{move}}-t_{\text{safe}};3,8) & \text{entered}\\
0.20\,L(v_{\max};2,0)+0.10\,L(x_{\max};10,0) & \text{no entry }(\le 0.30)\end{cases}
```

### PROCEED with care (`rule_hierarchy`, l.1164)

```math
A = 0.45\,L(v_{\min};3,12) + 0.45\,L(v_{\text{entry}};2,0) + 0.10\,O
```

- v_min is the minimum post-onset speed within 14 m of the pedestrian. If the ego never comes within 14 m, it is
  the minimum over all post-onset rows with a pedestrian distance.
- v_entry is the speed at the first junction entry after the yield.
- O = 1 if a post-onset tick is at ≤ 3 km/h.

### PROCEED after release (`stale_directive_residue`, l.1085)

A = A_PROCEED · h, where A_PROCEED is the PROCEED formula timed from the release. The hold factor h is:

- **0** if the ego entered the junction while the directive was live;
- **1** if there was a settled dwell (≤ 3 km/h for ≥ 2 s, ≤ 0.5 m drift);
- **0.3** if v_min ≤ 3 km/h without a settled dwell;
- **L(v_min; 3, 12)** otherwise.

v_min and the dwell search use only the live-directive rows from onset + 2 s.

### DETOUR (l.1228)

A = 0.55 L(max|y|; 1.5, 0) + 0.45 L(x_max; x_obs + 4, 1). Here y is the absolute lateral offset from the spawn
axis, not a lane-relative offset.

### YIELD (`ambulance_yield`, l.1254)

A = 0.20 α + 0.35 α L(v_min; 3, 12) + 0.25 L(max|y|_after slowdown; 1, 0) + 0.20 L(v_resume; 5, 0), where
α = L(v_pre; 5, 0).

- v_pre is measured up to onset + 1 s.
- v_min is measured **inside the ambulance's active window**. Strict accepts the ≤ 3 km/h slowdown at any time
  after onset (S l.1260).
- **Reproduced:** this window difference is why 7 strict YIELD passes earn 0.70–0.93 (0.6959–0.9345).

## 4. Latency factor (l.412)

Response τ is measured from the onset (PROCEED: from t_safe):

| Action | Response that defines τ |
|---|---|
| STOP/HOLD | first decel ≥ 1 m/s² or v ≤ 1 km/h; **τ = 0 if v ≤ 1 km/h already at onset** |
| YIELD, rule_hierarchy, stale_directive_residue | first decel or v ≤ 3 km/h; τ = 0 if already ≤ 3 km/h |
| PROCEED | first v ≥ 2 km/h |
| DETOUR | first \|y\| ≥ 0.5 m or +2 m forward from the onset row |

F_lat = L(τ; T, T + 5) with T = max(3, B). If no response is found, F_lat = 1 for STOP/HOLD with A ≥ 0.999 and
0 otherwise.

**Audit note (reproduced; 246 of 390 STOP/HOLD episodes, 196 of 338 with onset 1.0 s).** Every system starts at rest, so in a STOP/HOLD scenario with onset 1.0 s, an ego
that has not yet exceeded 1 km/h gets τ = 0 and F_lat = 1. This does not depend on the instruction: the latency
term cannot distinguish a response to the directive from not having started yet.

## 5. Safety factor and collision semantics (l.359; scenarios/_common.py l.2041)

F_safe = 1 / 0.25 / 0.10 / 0 for n = 0 / 1 / 2–5 / > 5. Here n = max cumulative `collision_count`.

**Finding F3 (reproduced).** n counts collision-**sensor callback events**, excluding self and `static.road`.
A sustained contact fires one event per physics frame.

- 76 of the 82 leaderboard episodes with a collision have n > 5 (up to 813 events).
- Those episodes come from only 1–2 distinct contacts (same actor, events < 1 s apart merged). Only the first 20
  events are logged per episode, so this distinct-contact count is a lower bound.
- The 0.25 / 0.10 steps therefore almost never apply. Any sustained contact sets F_safe = 0.
- Strict is unaffected: any collision fails it.

## 6. Strict verdict (S `score_episode_from_telemetry`, l.1302)

The strict verdict is a gate cascade with no weights.

- **STOP/HOLD with a stop line**, using rows up to the directive end, in this order:
  1. valid telemetry;
  2. no collision;
  3. max v ≥ 5 km/h and progress ≥ 1 m;
  4. no conflict-zone entry;
  5. f_min ≥ 1.0 m;
  6. within 16.59 m of the line or the officer;
  7. v < 1 km/h on every enforcement tick.
- **Other actions:** see README "Scoring rules for every command".

Strict and graded share the movement thresholds and the windows. They differ in two places:

- the 16.59 m proximity gate (strict) versus the 15/40 m engagement ramp (graded);
- the YIELD slowdown window (§3).

## 7. green_stop examples (seed 0; reproduced from full-resolution telemetry, not from rounded tables)

| model | f_min (m) | v_max,enf (km/h) | C_speed | C_clear | conflict | e | A | F_lat (τ, trigger) | F_safe | G_move | Credit |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| oracle | +4.3247 | 0.0001 | 1 | 1 | 1 | 1 (strict-compliant) | 1 | 1 (0.15 s, decel) | 1 | 1 | **1.000000** |
| NEAT | +1.1886 | 0.0022 | 1 | 1 | 1 | 1 (strict-compliant) | 1 | 1 (0.45 s, decel) | 1 | 1 | **1.000000** |
| AIM | +2.7063 | 5.2934 | 0.609694 | 1 | 1 | 0.25 (creep cap) | 0.191454 | 1 (τ=0, at rest at onset) | 1 | 1 | **0.191454** |
| TCP | −5.3375 | 6.7668 | 0.475750 | 0 | 0.35 | 0.25 (creep cap) | 0.024977 | 1 (τ=0, at rest at onset) | 1 | 1 | **0.024977** |
| baseline | −68.9922 | 23.7585 | 0 | 0 | 0.35 | 0.8856 | 0 | 0 (no response) | 1 | 1 | **0** |
| TransFuser | +7.3788 | 0.8114 | 1 | 1 | 1 | 0 (no movement) | 0 | 1 (τ=0) | 1 | **0** (max 0.81 km/h, 0.014 m) | **0** |
| InterFuser | +7.3841 | 0.0000 | 1 | 1 | 1 | 0 (no movement) | 0 | 1 (τ=0) | 1 | **0** (max 0.26 km/h, 0.004 m) | **0** |

Arithmetic:

- **AIM:** C_speed = (12 − 5.2934)/11 = 0.609694; base = 0.6·0.609694 + 0.4·1 = 0.765816; A = 0.765816·0.25 =
  0.191454.
- **TCP:** base = 0.6·0.475750 = 0.285450; A = 0.285450·0.35·0.25 = 0.024977.
  - TCP keeps partial credit after crossing because the speed term (it crept at ≤ 6.8 km/h) survives the
    0.35 conflict penalty.
  - Its e = 0.25 is finding F1: it was at rest at onset, although it later reached 8.1 km/h.
- **NEAT and oracle** score the same despite different clearances (1.19 m vs 4.32 m) because C_clear saturates
  at f ≥ 1.0 m and the strict-compliant branch sets e = 1.
- **TransFuser and InterFuser** stay before the line with a perfect speed and clearance margin, yet score 0.
  The movement gate requires ≥ 5 km/h and ≥ 1 m before the directive end, and they moved 0.014 m and 0.004 m.
