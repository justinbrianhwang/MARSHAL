# Baseline provenance, cohort reconciliation and leaderboard reproduction

## Frozen baseline

| item | value |
|---|---|
| Published v2 commit | `e6ae3c8` (2026-10-26). The scorers are byte-identical through `2c84544` (`git diff e6ae3c8 2c84544 -- marshal_bench/criteria` is empty) |
| Audit branch / worktree | `audit/scoring-validity` at `2c84544`, separate worktree. No change to `main`, nothing pushed |
| Untracked user files left untouched | `reports/perception_behavior_validation/`, `scripts/gesture_recognition_probe.py`, `scripts/perception_behavior_validation.py`, two tests |
| Environment | Windows 11 Pro 10.0.26200; Python 3.12.13 (conda env `marshal`); CARLA 0.9.16 (Town03), synchronous mode, 0.05 s/tick; horizon 25 s |
| Raw data | `tmp/_protocol_v2/<model>[_cap3]/s<seed>/<episode>/` in the workspace (telemetry, events, metadata, scoring). Only read, never modified |
| Published tables | `reports/protocol_v2/data/leaderboard_v2.csv` at `e6ae3c8` (copied to `data/published_leaderboard_v2_e6ae3c8.csv`) |

## Episode manifest

[`data/baseline_manifest.csv`](data/baseline_manifest.csv) has one row per episode directory. Each row records:

- episode id, cohort, model, seed and scenario;
- the directory, the first 16 hex characters of the telemetry SHA-256, and whether the collector selected it;
- acceptance;
- the telemetry file time and the run-log entry it matches.

| cohort | episodes | note |
|---|---:|---|
| leaderboard | **650** | (11 × 1 + 3 × 5) × 25. No duplicates, no missing cells, 0 refused, 0 INVALID |
| query-cap comparison (`*_cap3`) | 15 | green_stop × 3 VLMs × seeds 0–4. Excluded from the leaderboard by the collector (`_cap3` suffix) |
| **total accepted** | **665** | explains the 665-row `cells_v2.csv` |

- **Not in the cohort:** no additional towns, no weather variants, no ablation cells. The `tmp/_protocol_v2_ablation` and
  `tmp/_protocol_v2_factorial` runs live in separate directories and are never collected into the leaderboard.
- **Light-only baseline:** included as a leaderboard row.
- **Control baselines:** PID and MPC are included as leaderboard rows.
- **Oracle:** included as the privileged reference row.

### Run history (executed: matched files to `outputs/protocol_v2/*.jsonl`)

**656 of 665 episodes** match an entry in `run_log.jsonl`. The other 9 are accounted for as follows.

- **TCP, 5 cells** (`green_stop`, `red_proceed`, `rule_hierarchy`, `flagger_slow_then_stop`, `stale_directive_residue`):
  - **Source:** the representative-scenario pilot (`pilot_log.jsonl`, 10/25 03:50–04:00), copied with
    `cp -rn tmp/_protocol_v2_pilot/* tmp/_protocol_v2/`. Byte-identical to the pilot copies.
  - **Code:** the scenario code (`_common.py`, `_shared_staging.py`, last modified 10/25 03:34) and the TCP adapter
    (10/15) predate the pilot, so these cells used the same code as the main run.
- **Oracle, 4 cells** (`fallen_person`, `occluded_officer`, `green_stop`, `sequential_directive`, 05:34–05:42):
  - **Source:** a manual re-run loop over the four stop-margin scenarios after the oracle pull-up change. No run log
    was kept.
  - **Code:** every one of the 25 oracle cells was written after 05:30, when the final `oracle.py` was installed. The
    05:53 rewrite of `oracle.py` left its content unchanged.
- **Oracle re-runs:** 17 oracle cells were logged twice (04:27–05:24, then 05:49–06:39). The second run overwrote the
  first. The re-run was driven by a code version (the pull-up), not by outcomes.
- **Qwen3-VL seed 0, five pilot cells:** re-run on 10/26 20:56–21:09 because the pilot copies predated the change that
  puts the seed in the API request (`vlm_model.py`, 10/25 04:49). The re-run was code-version-driven, as the session
  record shows. The earlier copies were set aside.

**Limitation:** episodes carry no code hash. `metadata.code_version` is `None` in every episode checked, so the code
version is inferred from file times and session records. **Recommendation:** stamp the git commit and a dirty flag per
episode.

## Leaderboard reproduction (executed)

`scripts/decompose.py` calls the published collector's own `score_episode` and captures the strict and graded
verdicts it computes. `scripts/analyze.py` then recomputes every column.

- **Result:** [`data/reproduction_tables.md`](data/reproduction_tables.md). **0 mismatches** across graded mean, SD,
  strict total and override count for all 14 models.
- **SD convention:** sample SD (n − 1) over five runs.
- **Rounding:** published values are rounded to 2 decimals; per-episode credit is stored at 6 decimals.
- **Override subset:** 12 cells, listed in [scoring_formulation.md §2](scoring_formulation.md).

### Strict successes vs. full-credit episodes

The two counts differ, and the difference is resolved below.

| | credit = 1 | 0 < credit < 1 | credit = 0 |
|---|---:|---:|---:|
| strict PASS (102) | 95 | 7 | 0 |
| strict FAIL (548) | 0 | 107 | 441 |

- **Every full-credit episode is a strict PASS.** This holds as an exact cross-tab, with no tolerance needed: all 95
  are exactly 1.0.
- **Not every strict PASS gets full credit.** The 7 exceptions are all `ambulance_yield`, at 0.6959–0.9345. Graded
  measures the slowdown speed v_min inside the ambulance's active window, while strict accepts the slowdown at any
  time after onset (see [scoring_formulation.md §3](scoring_formulation.md)).
- This is a real strict/graded window mismatch, not a rounding artefact.

## Reproduction commands (from the workspace root)

```bash
A=MARSHAL_audit/reports/scoring_validity_audit
python $A/scripts/decompose.py --runs tmp/_protocol_v2 --out $A/data            # manifest + decomposition (~3 min)
python $A/scripts/analyze.py --data $A/data --published $A/data/published_leaderboard_v2_e6ae3c8.csv
python $A/scripts/explain_partials.py --data $A/data
python $A/scripts/startup.py --data $A/data
python $A/scripts/counterfactual.py --out $A/counterfactual_raw.md --json $A/data/counterfactual_results.json
python $A/scripts/sensitivity.py --out $A                                        # ~10 min
python $A/scripts/alternatives.py --out $A
python -m pytest -q $A/tests/
```

**Requirements:**

- the `marshal` conda env (Python 3.12, numpy);
- the recorded `tmp/_protocol_v2` runs;
- **no CARLA**, because every step above is scorer-only.
