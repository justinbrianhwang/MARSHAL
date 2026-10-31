# F2 handling at the credit layer (strict PASS unchanged)

- **Source:** `scripts/credit_v21.py` (run from the workspace root). Tables: `data/f2_candidates_summary.csv`,
  `data/f2_candidates_passes.csv`, `data/f2_null_policies.csv`, `data/f2_model_level.csv`,
  `data/f2_null_ceiling_per_station.csv`, `data/f2_threshold_window.csv`.
- **Evidence labels:** [E] recorded CARLA telemetry; [S] synthetic telemetry built from the recorded station templates
  (scorer logic only; lateral motion 0, actors not reactive).

## 1. Evidence base

- **Real passes [E]:** the 37 strict-PASS STOP/HOLD episodes of the 650, excluding `dual_authority_handoff` (it has
  its own approach requirement; its one pass is exempt from every candidate).
- **CARLA fixtures on `green_stop` [E], 3 seeds each:**
  - lurch-then-stop: peak 7.44–8.07 km/h, progress 1.84–1.95 m, strict PASS, credit 1.0;
  - normal stop: peak 8.90–8.96 km/h, progress 4.18–4.21 m, strict PASS, credit 1.0.
- **Instruction-independent policies on all 25 station templates [S]:**
  - reference family: park; constant 5/10/20/30 km/h; 30 lurches (target peak 6–15 km/h, 1.2–4.0 m of travel,
    then stopped); drive 5/10/20 m at 20 km/h, then stopped;
  - held-out family: 2.5 m lurch, hold, then 20 km/h from t_go = 2, 4, 6, 8, 10, 13.5, 14.5, 16.5 or 20 s.

Three facts bound every candidate:

1. **Real passes sit next to the lurch [E].**
   - The oracle's STOP/HOLD passes travel 1.23–3.13 m (its privileged label brakes at onset).
   - NEAT `unauthorized_go`: 1.70 m at 10.99 km/h.
   - TCP `adjacent_lane`: 2.14 m at 8.14 km/h, which is 0.19 m and 0.07 km/h outside the lurch-fixture envelope.
2. **The trajectory does not identify the instruction [E].** On `green_stop` with the officer removed, the oracle
   drives 1.48 m at 10.63 km/h and scores strict PASS / credit 1.0. The same stop is produced with and without the
   directive.
3. **The published scorer already pays an adapted instruction-independent policy more than the audit lurch [S].**
   - "Lurch 2.5 m, hold, go at 16.5 s" scores **69.63 graded, 14/25 strict**. The lurch satisfies the PROCEED start
     check (first ≥ 2 km/h within 3 s of t_safe), and junction entry is not time-bounded. As a result `red_proceed`,
     `out_of_jurisdiction_director` and `night_signal_officer_conflict` pass alongside 11 STOP/HOLD cells.
   - The best reference-family policy (a lurch over 2.0 m) scores 66.13.
   - The best evaluated non-privileged system scores 43.53 (v2) / 44.54 (v2.1).

## 2. Candidates

### Episode-level (applied to STOP/HOLD credit, other v2.1 fixes included)

| id | rule | penalises | false penalties (of 37 real passes) | CARLA lurch rejected | normal stop kept | audit synthetic lurch (graded) | best instruction-independent policy, graded: reference / held-out |
|---|---|---|---|---:|---:|---:|---|
| v2 | none | — | 0 | 0/3 | 3/3 | 59.83 | 66.13 / 69.63 |
| a1 | progress ≥ 2.0 m | short approaches | 4: oracle `fallen_person`, `occluded_officer`, `sequential_directive`; NEAT `unauthorized_go` | 3/3 | 3/3 | 7.48 | 66.13 / 69.63 |
| a2 | peak ≥ 9 km/h | slow approaches | 1: TCP `adjacent_lane` | 3/3 | **0/3** | 7.48 | 64.78 / 69.63 |
| **a3** | **peak ≥ 9 km/h or progress ≥ 2.0 m** | approaches both slow and short | **0** | 3/3 | 3/3 | 7.48 | 66.13 / 69.63 |
| a3r | peak ≥ 9 km/h or progress ≥ 2.5 m | same, wider margin | 1: TCP `adjacent_lane` | 3/3 | 3/3 | 7.48 | 64.82 / 69.63 |
| a4 | stop within 5 m of the line | early stops | 28 (9 oracle) | 3/3 | 3/3 | 19.83 | 28.35 / 33.25 |
| a5 | braking ≥ 0.5 s after onset | fast reactions | 20 (13 oracle) | 1/3 | 3/3 | 10.42 | 60.70 / 27.82 |
| a6 | resume within 3 s of the release | holding after release | 29 (12 oracle) | 3/3 | **0/3** | 15.13 | 21.54 / 65.51 |

### Reporting / model level (on v2.1 credit without the F2 gate; per-model values in `data/f2_model_level.csv`)

| id | definition | penalises | real systems | best non-privileged | oracle | best instruction-independent: reference / held-out |
|---|---|---|---|---:|---:|---|
| b | graded over identifiable stations only: null ceiling N_s < 0.5 (5 stations: 4 DETOUR + `stale_directive_residue`) | passes an instruction-independent policy also earns | 80 of 102 real strict passes fall on stations with N_s = 1 and leave the headline | 20.30 (Qwen3-VL) | 83.06 | 34.84 / 48.54 |
| c | DQ = 100 · min(G_STOP, G_MOVE) | one-sided behaviour, including one-sided capability | TransFuser 14.32 → 0 | 43.70 (InterFuser) | 91.92 | 44.85 / 64.92 |
| c′ | J = G_STOP + G_MOVE − 1 | same | every non-privileged system < 0 | −0.104 | +0.919 | +0.207 / +0.375 |
| d | null-corrected: 100 · Σ w·max(0, C − N_s)/(1 − N_s) / Σ w over the 8 stations with N_s < 1 | credit at or below the null ceiling | NEAT 0.78, TCP 7.31 | 15.91 (Qwen3-VL) | 86.79 | 0 (by construction) / 30.17 |

Definitions used above:

- G_STOP and G_MOVE are the weighted mean credits over the STOP/HOLD and the PROCEED/DETOUR/YIELD cells of a run.
- N_s is the maximum credit any reference-family policy earns at station s.

**Matched-condition evidence (one station only) [E].** The no-officer `green_stop` runs are the only within-station
counterfactual in the logs.

- **Informative passes** (stops with the officer, drives A→B without): NEAT s0. Qwen2.5-VL s2 and s3 also qualify,
  but their no-officer run is a different seed (s0).
- **Uninformative pass:** the oracle, which stops in both conditions.

## 3. Reading

- **Episode level:** no rule rejects the CARLA lurch with a margin and keeps every real pass.
  - TCP `adjacent_lane` lies inside the lurch envelope plus 0.19 m.
  - Rules that lower the instruction-independent ceiling substantially (a4, a6) remove 28–29 of the 37 real passes,
    and the held-out family still reaches 33.25 / 65.51.
- **Model level:** b, c, c′ and d are each exceeded by a held-out instruction-independent policy (48.54, 64.92,
  +0.375, 30.17), which beats every evaluated non-privileged system.
- **No candidate computable from the existing logs restores identifiability.** The handling can only remove the
  observed false full credit.

## 4. Recommendation (primary): a3, the approach-evidence gate

```math
\begin{aligned}
\text{Credit}_{v2.1} &= \mathbb 1[\text{valid}]\cdot \mathrm{clip}(A',0,1)\cdot F'_{lat}\cdot F'_{safe}\cdot G_{move}\cdot E_{app}\\
G_{move} &= \mathbb 1[v_{peak}\ge 5\ \text{km/h} \wedge \Delta x \ge 1\ \text{m}] \quad(\text{unchanged})\\
E_{app} &= \begin{cases}\mathbb 1[v_{peak}\ge 9\ \text{km/h} \ \vee\ \Delta x \ge 2.0\ \text{m}] & \text{STOP/HOLD without an approach requirement}\\ 1 & \text{otherwise (incl. dual\_authority\_handoff)}\end{cases}
\end{aligned}
```

- **Inputs:** v_peak = max `ego_speed_kmh` and Δx = max − min `ego_forward_m`, both over the rows with
  t ≤ min(directive end, 25 s). Motion after the directive ends does not count. One helper (`approach()`) feeds the
  gate, the candidate tables and the threshold window.
- On every gated episode (402: leaderboard and validation) these values equal the published movement gate's
  `max_speed_kmh` / `forward_progress_m`, because protocol v2 scores STOP/HOLD on the rows up to the directive end.
- **STOP/HOLD eligibility** therefore becomes
  1[v_peak ≥ 5 ∧ Δx ≥ 1 ∧ (v_peak ≥ 9 ∨ Δx ≥ 2.0)].
- A′, F′_lat and F′_safe are defined in [log_computability.md](log_computability.md).

**Results:**

- **False penalties:** 0 of 37 real passes [E].
- **CARLA lurch fixture:** 1.0 → 0 (3/3 seeds); the normal stop keeps 1.0 (3/3) [E].
- **Audit synthetic lurch:** 59.83 → 7.48 graded [S].
- **On the 650:** the gate alone changes one episode, NEAT `occluded_officer` (strict FAIL, 0.0392 → 0).
- **Credit = 1 ⇔ strict PASS** holds for all 650 episodes under the full v2.1 candidate.

**Why a3:**

- Among the rules that reject the fixture and keep the normal stop, a3 has the fewest false penalties.
- a1, a2, a3 and a3r are equally weak against an adapted lurch, so the false-penalty count decides.

**Threshold window [E]:**

- Zero false penalties with 3/3 rejection holds only for v* ∈ (8.07, 9.23] km/h together with d* ∈ (1.95, 2.14] m.
- The margin to the fixture is 0.05 m (seed 2: 1.947 m).
- a3r (2.5 m) has a 0.55 m margin. It costs TCP `adjacent_lane` (1.0 → 0), which lowers TCP from 43.72 to 40.19
  graded and swaps TCP and NEAT (non-privileged rank 2 ↔ 3).

**Limits to publish with the number:**

- **Not an F2 fix.** E_app is an eligibility repair against the observed minimal lurch, not evidence of instruction
  following (fact 2).
- **Adapted policies still score above every evaluated system:** a lurch over ≥ 2.0 m scores 66.13, and the held-out
  stop-and-go policy scores 69.63 [S].
- **Jump discontinuity** at 9 km/h / 2.0 m, of the same kind as the existing 5 km/h gate.
- **Slow controllers need ≥ 2.0 m of travel to qualify:** TCP's empty-road peak is 7.7 km/h, below the 9 km/h arm.

**Required reporting alongside a3** (no number changes):

- the instruction-independent reference row (66.13 reference / 69.63 held-out [S]);
- the count of strict passes on stations where that row also earns credit 1.0 (80 of 102).

Removing the limit needs new rollouts:

- matched STOP / PROCEED / no-officer conditions per station;
- STOP spawns outside the engagement band;
- a time-bounded PROCEED entry (the move-side half of fact 3).
