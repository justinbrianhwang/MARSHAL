# MARSHAL Protocol v2 — scoring-validity audit: summary

- **Scope:** the published v2 leaderboard (`e6ae3c8`); 650 episodes plus 15 query-cap episodes.
- **Isolation:** the audit ran in the `audit/scoring-validity` worktree. Official artefacts are unchanged, nothing was
  pushed, and no raw telemetry was modified.

**Evidence labels used throughout:**

- **[E]** executed or reproduced from recorded data;
- **[S]** executed on synthetic telemetry (scorer logic only);
- **[C]** inspected in code or history;
- **[I]** inferred;
- **[N]** not yet validated (needs CARLA).

## 1. What reproduces exactly (no defect found)

- **Leaderboard reproduction [E].** All 14 rows reproduce from the raw telemetry with the published collector: graded
  mean, SD (n − 1), strict k/n and the 12-cell override count, with 0 mismatches.
  ([reproduction.md](reproduction.md))
- **Cohort [E].** The leaderboard is 650 = (11 × 1 + 3 × 5) × 25 episodes, with no duplicates, no INVALID episodes
  and no refusals. The 665-row collection adds 15 auxiliary query-cap episodes, which the collector excludes from the
  leaderboard. Nine episodes come from a pilot copy or an unlogged manual re-run (source matching [E]); that they ran the same
  code version is reconstructed from file times and session records [C]+[I], because episodes carry no code hash.
- **Endpoints [E].**
  - Counts: 441 zero, 95 full and 114 partial. The endpoints are exact values, not display rounding.
  - Credit 1 ⇒ strict PASS, without exception.
  - Strict PASS ⇒ credit 1 fails for 7 `ambulance_yield` episodes (finding F7).
  - The full list of the 114 partial cases is in `data/partial_credit_episodes.csv`, with one explanation per
    episode.
- **Robustness to thresholds [E].** Scaling any one threshold by ±10% or ±25% flips at most 6 strict verdicts. The
  ranking keeps Kendall τ-b ≥ 0.91.

## 2. Findings

| id | severity | finding | evidence | class |
|---|---|---|---|---|
| **F1** | high | **The STOP/HOLD engagement window collapses under the v2 rest start.** The approach window ends at the first tick ≤ 1 km/h after onset, which is the onset tick itself for an ego still at rest. Every approach made after the directive appears is scored as a creep (e ≤ 0.25). An identical approach scores 0.577 if started at 0.5 s and 0.207 if started at 1.0 s. This affects 79 episodes. Correcting it changes 27 credits, and TCP moves from 30.33 to 44.12, above InterFuser. | [E] [S]; `tests/test_engagement_timing.py` (strict xfail on v2, pass on A′) | specification defect from the v1 → v2 interaction (the rule assumed a moving approach at onset) |
| **F2** | high | **The STOP side can be gamed by an instruction-independent policy.** "Accelerate to ~8 km/h for ~1.6 m, then stay stopped" scores **59.8 graded and 13/25 strict** on all 25 scenarios. That is above every evaluated non-privileged system (best 43.5). Constant 20 km/h scores 18.1, above the 7.7 baseline. The causes combine: (i) eligibility needs only ≥ 5 km/h and ≥ 1 m once; (ii) all 14 STOP/HOLD spawns other than `dual_authority_handoff` are already inside the 16.59 m engagement band; (iii) in 11 of the 13 passed scenarios the directive is live from 1.0 s (`fallen_person` from 0 s; `flagger_slow_then_stop` enforces STOP from 6.05 s). Recorded oracle and NEAT passes after 1.2–2.7 m of travel show such trajectories occur in CARLA and pass strict. | [S]; real-data support [E]; CARLA run of a scripted lurch controller [N] | specification property (faithfully implemented) |
| **F3** | medium | **The "collision count" counts sensor-callback events.** One sustained contact produces hundreds of events: 76 of 82 collided episodes have > 5 events, but 75 of 82 have only 1–4 distinct contacts (identity exact for the first 20 events, a lower bound after; [credit_v21/log_computability.md](credit_v21/log_computability.md)). The 0.25 / 0.10 safety steps almost never apply, so any sustained contact gives F_safe = 0. With distinct-contact counting, 65 episodes change credit. Strict is unaffected. | [E]; `tests/test_findings.py::test_F3` | implementation ≠ documented semantics (suspected defect; the intended semantics must be confirmed) |
| **F4** | medium | **At equal overshoot, a 25 km/h crossing outscores a 5 km/h creep** (0.5 m past the line: 0.173 vs 0.031). The cause is the 0.25 creep cap plus slower deceleration lowering F_lat. | [S]; `test_F4` | specification property |
| **F5** | medium | **Most STOP/HOLD zeros are compliant-but-ineligible.** all 146 STOP/HOLD movement-gate zeros never crossed the line and had no collision; 145 also stayed ≥ 1 m short of it; 133 satisfy every remaining strict check once the movement/proximity eligibility gates are removed. Separately, 8 AIM episodes moved up to 7 m but stayed below 5 km/h, and score 0 as "stationary ego never engaged". | [E] | specification consequence; reporting gap |
| **F6** | medium | **The latency factor is 1 for any ego still at rest (≤ 1 km/h) at the enforced onset,** with τ = 0. This applies in 246 of 390 STOP/HOLD episodes (196 of 338 with onset 1.0 s); in those episodes the latency term does not depend on the instruction. | [C] [E] | specification consequence of v2 |
| **F7** | low | **YIELD window mismatch.** Graded measures v_min inside the ambulance window; strict accepts the slowdown at any time after onset. As a result, 7 strict passes score 0.70–0.93. | [E] [C] | strict/graded inconsistency |
| **F8** | medium | **Scenario contracts.** (a) `red_proceed` and `night_signal_officer_conflict` spawn with the front bumper 4.58 m **past** the stop line, so always-go from t = 0 is a strict PASS: obeying the officer is not identifiable against running the red. (b) The 16.59 m band never binds: 0 verdict changes at ±25%. (c) In `two_civilians_disagree` and `fake_vest_director`, the proximity anchor is an unauthorized civilian. (d) `fallen_person` turns on the stop-line gates through a placeholder officer point 1.6 m from spawn. (e) `adjacent_lane` is STOP in its yaml and HOLD in the runner. | [E] [C] ([scenario_contracts.csv](scenario_contracts.csv)) | task-specification issues |
| **F9** | medium | **Integration deviations from the supported configurations.** TransFuser: its deadlock escape needs ≥ 55 s (unreachable in 25 s), it runs at 5 Hz instead of 10 Hz, and its controller is patched. TCP, AIM, NEAT and InterFuser get about 4 m targets instead of up to 50 m. InterFuser's LiDAR runs at CARLA defaults. Every episode starts with a 0.15–0.4 s fall. Whether these cause TransFuser's or InterFuser's non-start or AIM's slowness is untested. | [C] + logs; causal effect [N] | candidate integration defects |
| **F10** | low | **Stimulus: the STOP paddle prop was requested in 275 episodes and spawned in none.** The gesture is shown by pose alone, for every model. | [E] | stimulus defect (not scoring) |
| **F11** | low | **Provenance gaps.** No per-episode code hash (`code_version = None`). `applied_*` telemetry is the previous tick's command. | [E] [C] | reporting / instrumentation |
| **F12** | low | **Documentation overstates literature support.** The engagement band is called "physics-derived" with "literature-range" parameters, but t_r = 1.0 s has no source and the AASHTO values give 24.45 m. No public doc mentions CARLA Leaderboard 2.1's changed penalty. No scoring element is literature-adopted; most are MARSHAL-specific engineering choices with no recorded rationale. | [C] + primary sources ([literature_grounding.md](literature_grounding.md)) | documentation |

## 3. Non-movement: what explains it

- **Shown by the recorded control [E]:**
  - The 197 genuinely non-starting episodes did not move because the controller's own output asked it not to. VLMs
    answered STOP/HOLD on every query. TransFuser, InterFuser and AIM requested zero throttle, or throttle pulses
    of ≤ 0.2 s cancelled by full braking. OpenEMMA's planner returned zero speed.
  - The harness never overrode control.
- **Not shown:**
  - That this reflects authority reasoning rather than configuration deviations (F9) or the stimulus (F10). Both need
    CARLA controls [N].
  - That "movement gate = 0" means "did not move". 20 of the 217 movement-gate zeros fall outside the
    no-meaningful-motion class: 8 AIM episodes with real motion below 5 km/h, 4 that moved only after the directive
    ended, 1 reverse/lateral, and 7 with only spawn-landing jitter (effectively no motion).

## 4. Score concentration: what explains it

The endpoints come from three things working together:

- **calibration to strict:** meeting the strict conditions gives exactly 1;
- **multiplicative binary-like gates:** movement, collision-event safety, and zero action credit on fast
  pass-throughs;
- **behaviour far from every threshold:** parked vehicles, 18–26 km/h pass-throughs, sustained contacts.

Concentration by itself is not a defect. The defects are F1 to F4.

## 5. Recommendation

- **Keep the v2 numbers as the published record.** They are reproducible and correctly aggregated.
- **Clarify the reporting now.**
- **Do not silently migrate.** A versioned correction (v2.1-candidate: F1 + F3 + F7) has to be decided
  explicitly, because it moves rankings.
- **The STOP-side validity issue (F2) is not solved by any rescoring of existing logs.** It needs a protocol change
  (spawn-outside-band approach requirement, or matched-condition pairs). This is v3 material and needs new rollouts.

## 6. Limitations

- CARLA was occupied by the running factorial study, so no new rollout was executed. Items [N] are blocked, not
  concealed.
- Synthetic cases fix lateral motion at 0 and do not model actor reactions.
- The distinct-contact counts use the first 20 logged collision events per episode, so they are lower bounds.
- There is one deterministic run per driving stack.
- The Green Book was verified only through official secondary sources.
