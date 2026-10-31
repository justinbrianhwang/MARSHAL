# v2.1-candidate leaderboard (experimental; the published v2 leaderboard is unchanged)

- **Credit:** Credit_v2.1 = valid · A′ · F′_lat · F′_safe · G_move · E_app. That is: F1 (A′), F6, F3 (distinct
  contacts), F7 and the F2 gate a3 (peak ≥ 9 km/h or progress ≥ 2.0 m on STOP/HOLD). Definitions are in
  [log_computability.md](log_computability.md) and [f2_handling.md](f2_handling.md).
- **Aggregation:** unchanged. MARSHAL-Graded = 100 · Σ w C / Σ w per run, then the mean over runs (five for VLM
  controllers).
- **Strict:** strict verdicts are unchanged (650/650 identical).
- **Data:** `data/leaderboard_v21.csv`, `data/episodes_v21.csv`, `data/fix_attribution.csv`.

## Leaderboard

| model | track | strict (unchanged) | graded v2 | graded v2.1 | Δ | rank v2 → v2.1 | non-privileged rank v2 → v2.1 | v2.1 without F2 gate | v2.1 with a3r (2.5 m) |
|---|---|---|---:|---:|---:|---|---|---:|---:|
| oracle | A | 24/25 | 96.91 | 96.91 | 0.00 | 1 → 1 | — | 96.91 | 96.91 |
| InterFuser | B | 8/25 | 43.53 | 44.54 | +1.00 | 2 → 2 | 1 → 1 | 44.54 | 44.54 |
| TCP | B | 3/25 | 30.33 | 43.72 | +13.39 | 4 → **3** | 3 → **2** | 43.72 | 40.19 |
| NEAT | B | 5/25 | 42.91 | 42.72 | −0.18 | 3 → **4** | 2 → **3** | 42.91 | 42.72 |
| CILRS | B | 5/25 | 24.57 | 26.40 | +1.83 | 5 → 5 | 4 → 4 | 26.40 | 26.40 |
| Qwen2.5-VL | C | 23/125 | 20.75 (SD 3.41) | 22.29 (SD 4.03) | +1.54 | 6 → 6 | 5 → 5 | 22.29 | 22.29 |
| AIM | B | 1/25 | 13.23 | 16.89 | +3.66 | 8 → **7** | 7 → **6** | 16.89 | 16.89 |
| TransFuser | B | 2/25 | 14.32 | 14.32 | 0.00 | 7 → **8** | 6 → **7** | 14.32 | 14.32 |
| MPC | B | 3/25 | 12.75 | 14.10 | +1.35 | 9 → 9 | 8 → 8 | 14.10 | 14.10 |
| PID | B | 3/25 | 11.76 | 13.51 | +1.75 | 10 → 10 | 9 → 9 | 13.51 | 13.51 |
| Qwen3-VL | C | 13/125 | 11.65 (SD 2.54) | 13.47 (SD 2.50) | +1.82 | 11 → 11 | 10 → 10 | 13.47 | 13.47 |
| OpenEMMA | B | 2/25 | 10.61 | 11.59 | +0.98 | 12 → 12 | 11 → 11 | 11.59 | 11.59 |
| baseline | – | 0/25 | 7.71 | 10.34 | +2.63 | 14 → **13** | — | 10.34 | 10.34 |
| GLM-4.5V | C | 10/125 | 8.14 (SD 0.25) | 8.69 (SD 0.77) | +0.54 | 13 → **14** | 12 → 12 | 8.69 | 8.69 |

**Kendall τ-b, v2 vs v2.1** (audit implementation, ties corrected):

| comparison | all 14 models | 12 non-privileged |
|---|---:|---:|
| v2.1 (recommended) | 0.934 | 0.939 |
| v2.1 without F2 gate | 0.934 | 0.939 |
| v2.1 with a3r | 0.956 | 0.970 |

**Reference row (not a system), instruction-independent policy [S]:** 66.13 (reference family) / 69.63 (held-out
stop-and-go) graded under both v2 and v2.1. It is above every non-privileged system. See
[f2_handling.md](f2_handling.md) §1.

## Attribution (each fix alone vs v2)

| fix | episodes changed (up / down) | τ-b all / non-priv | largest model effects (graded) |
|---|---|---|---|
| F1 | 27 (27 / 0) | 0.912 / 0.879 | TCP 30.33 → 44.12; AIM 13.23 → 16.89; OpenEMMA +1.06; Qwen2.5-VL +0.76 |
| F6 | 13 (0 / 13) | 1.000 / 1.000 | OpenEMMA −0.51; Qwen2.5-VL −0.25; TCP −0.39; GLM-4.5V −0.13 |
| F3 | 65 (65 / 0) | 0.934 / 0.939 | baseline +2.63; PID +1.75; CILRS +1.64; MPC +1.35; TCP +1.20; Qwen3-VL +1.08 |
| F7 | 10 (10 / 0) | 0.978 / 0.970 | Qwen3-VL +0.74; Qwen2.5-VL +0.56; GLM-4.5V +0.25; InterFuser +0.27 |
| F2 (a3) | 1 (0 / 1) | 1.000 / 1.000 | NEAT −0.18 |
| all | 103 (94 / 9) | 0.934 / 0.939 | as in the leaderboard |

## Changed episodes

- **Total:** 103 of 650 change. All 103 are listed in `data/episodes_v21.csv` (column `changed_by`, with v2 and
  v2.1 components side by side).
- **By fix:** F3 64; F1 15; F1+F6 12; F7 9; F3+F7 1; F6 1; F2 1.
- **By action:** DETOUR 57; STOP 29; YIELD 10; PROCEED 6; HOLD 1.
- **Endpoints:** zero / full / partial changes from 441 / 95 / 114 to 383 / 102 / 165.
  - 64 episodes move from 0 to non-zero credit; 6 move from non-zero to 0.
- **Strict/credit equivalence:** credit = 1 ⇔ strict PASS for all 650 (v2: 7 YIELD passes below 1; now 0).

**Reasons by fix:**

- **F3.** Sustained contacts (up to 813 callbacks on the scored rows) are 1–4 distinct contacts in 75 of the 82
  collided scored episodes. In the 65 credit changes F_safe rises from 0 (or 0.10 in one) to 0.25 in 53 and 0.10 in
  12. Most are DETOUR cells with partial forward progress. Beyond the 20th event the contact count is a lower bound
  ([log_computability.md](log_computability.md)).
- **F1.** 27 STOP/HOLD episodes whose ego was still at rest at onset and approached afterwards. The creep cap
  (e ≤ 0.25) no longer applies to them: TCP 10, AIM 4, Qwen2.5-VL 8, GLM-4.5V 4, OpenEMMA 1.
- **F6.** 13 STOP/HOLD strict FAILs, all at rest at onset, that started moving under the directive and stopped
  3.25–11.3 s after onset. They previously had latency 0. Nine of the 103 changes are downward: 8 of these
  (Qwen2.5-VL 5, GLM-4.5V 2, OpenEMMA 1), plus the F2 change below.
- **F7.** 10 `ambulance_yield` episodes. The 7 strict passes rise from 0.70–0.93 to 1.0; three non-passes rise
  slightly.
- **F2.** NEAT `occluded_officer` (strict FAIL; peak 7.02 km/h, progress 1.72 m), 0.0392 → 0.

## Validation episodes (`green_stop`, recorded CARLA, `data/validation_v21.csv`)

| behaviour (3 seeds) | strict | credit v2 | credit v2.1 | peak (km/h) | progress (m) |
|---|---|---|---|---|---|
| normal stop | PASS | 1.0 / 1.0 / 1.0 | **1.0 / 1.0 / 1.0** | 8.90–8.96 | 4.18–4.21 |
| late stop | FAIL | 0.352 / 0.381 / 0.353 | 0.352 / 0.381 / 0.353 | 7.43–8.07 | 6.65–6.71 |
| overshoot | FAIL | 0.045 / 0.045 / 0.045 | 0.045 / 0.045 / 0.045 | 9.03–9.06 | 9.67–9.68 |
| **lurch then stop** | PASS | **1.0 / 1.0 / 1.0** | **0 / 0 / 0** | 7.44–8.07 | 1.84–1.95 |
| constant go | FAIL | 0 / 0 / 0 | 0 / 0 / 0 | 18.68–19.06 | 61.4–62.2 |
| no start | FAIL | 0 / 0 / 0 | 0 / 0 / 0 | 0.10–0.23 | 0.01 |

- **Fixture ordering:** under v2.1 the fixtures order as normal 1.0 > late ≈ 0.36 > overshoot 0.045 > 0. The lurch
  joins no start and constant go at 0.
- **No-officer runs** (13 stacks + oracle, plus 6 integration variants, scored as STOP):
  - oracle: PASS / 1.0 in both v2 and v2.1. It travels 1.48 m at 10.63 km/h, so the a3 speed arm admits it.
  - TCP: 0.024 → 0.096 (F1). TCP 50 m-target variant: 0.025 → 0.100.
  - every other run: 0 in both.
- **Test:** `test_credit_v21.py` pins lurch → 0 and normal → 1.0 with both strict PASS, E_app = 0 for a lurch
  followed by a fast drive after the directive ends, and the F3 identity rule (3 passed).
