# E. Reproduction

## Offline (no CARLA, no model calls)

Environment: the `marshal` conda env (Python 3.12, numpy 1.26, matplotlib 3.11, the CARLA 0.9.16 Python
package for the scenario registry import). Episode artifacts live in the workspace that contains this repository
(`--dev-root`, default: the parent directory of the repository): leaderboard runs in `tmp/<sweep dir>/`,
authority-directive runs in `outputs/authority_directive_comparison/runs/`, new runs in
`outputs/dac2027_audit/runs/`.

```bash
python scripts/dac2027_audit.py all            # every step below, in order
python scripts/dac2027_audit.py score-trace     # data/green_stop_score_trace.csv, data/green_stop_strict_gates.csv
python scripts/dac2027_audit.py aggregate       # data/suite_cells_rescored.csv, data/suite_aggregate_rescored.csv
python scripts/dac2027_audit.py cases           # data/illustrative_cases.csv (synthetic inputs)
python scripts/dac2027_audit.py rescore-variant # data/variant_front_bumper_*.csv (proposal impact only)
python scripts/dac2027_audit.py timing          # data/timing_per_episode.csv, data/timing_summary.csv
python scripts/dac2027_audit.py geometry        # data/green_stop_geometry_summary.csv, data/green_stop_geometry_per_second.csv
python scripts/dac2027_audit.py figures         # figures/*.png
python scripts/dac2027_audit.py tables          # tables.md
```

Built-in checks (printed): for every green_stop episode the replicated first failed gate equals the gate named by the scorer's reason and the re-scored verdict equals the stored one; credit = action × latency ×
safety and action = base × factors for every episode; every re-scored leaderboard cell and model score equals
`outputs/full_sweep_results.json`. Expected output: `score-trace: 44 episodes, consistency failures: 0`,
`aggregate: 350 cells, 0 differ from published; models matching: 14/14`.

## Original vs re-analysed results

- Original: the stored `strict_scoring.json` of each episode and `outputs/full_sweep_results.json`. Never edited.
- Re-analysed: everything under `reports/dac2027_audit/data/`, computed with the scorers at the working tree
  (scorer files unchanged from commit `1fac326`). Column `stored_strict_verdict` keeps the original next to the
  re-scored `strict_verdict`; they agree in all 44 green_stop episodes.
- Synthetic: `illustrative_cases.csv` only; labelled as such everywhere.
- Proposal: `variant_front_bumper_*.csv`; changes no published score.

## New runs (CARLA required)

`scripts/dac2027_timing_runs.sh` (bash, Windows Git-Bash; interpreters via `MPY`/`TPY`/`IPY`) restarts CARLA
0.9.16 before every episode, loads Town03 and checks that no walker or vehicle is left
(`scripts/dac2027_carla_util.py load-town03 | world-check`), runs the episodes below, and exports the Town03
OpenDRIVE map and traffic-light geometry for the offline geometry tables (`dac2027_carla_util.py export-map`;
`geometry` is skipped with a message when the export is missing). It runs

| Run | Command (from the workspace root) |
|---|---|
| oracle | `MARSHAL_OUT_ROOT=<out> python scripts/_run_reference_staging_sweep.py green_stop --controller oracle` (env `marshal`) |
| TCP | `python scripts/_run_more_e2e_sweep.py green_stop --models tcp --wall-timeout 1800 --results-json <out>.json --report <out>.md` (env `transfuser_ui`) |
| InterFuser | same with `--models interfuser` (env `interfuser_fl`) |
| Qwen3-VL | `MARSHAL_VLM_SAVE_INPUTS=1 python scripts/_run_vlm_test.py green_stop --model Qwen/Qwen3-VL-235B-A22B-Instruct ...` (env `marshal`; hosted API key required) |
| TCP police STOP s1, 25 s | `MARSHAL_NO_STOP_EARLY_TERMINATION=1 MARSHAL_TIMEOUT_S=25 MARSHAL_SEED=1` + the TCP command |

Leaderboard wiring = no `MARSHAL_*` overrides (14 s, early termination, 3 VLM queries). Run date 2026-10-24,
log `outputs/dac2027_audit/logs/batch_timing.log`, world checks `world_checks.jsonl`. The hosted VLM is not
deterministic; a repeat can change a VLM verdict (see `04_…` §5).

## Code changes in this audit

- `marshal_bench/scenarios/_common.py`: timing columns (`world_frame`, `world_elapsed_s`, `obs_image_frame`,
  `obs_image_sim_ts`, `obs_image_cb_wall_s`, `ctrl_step_wall_s`); dash-cam frame metadata stored atomically with
  the image; `image_frame` / `image_sim_timestamp` in the controller observation. The image handed to the
  controller is still `latest_ego_frame`; the metadata is attached only when it belongs to that same array
  (otherwise left empty). The five runs were recorded with this final code; an earlier recording made with a
  variant that took the image from the metadata tuple is kept, unused, in `runs_superseded_tuple_variant/`.
- `marshal_bench/controllers/vlm_model.py`: `input_frame`, `input_sim_ts` columns in `vlm_trace.csv`.
- `scripts/dac2027_audit.py`: this analysis (including the offline `geometry` tables).
- `scripts/dac2027_carla_util.py`, `scripts/dac2027_timing_runs.sh`: the run driver and CARLA helpers (the
  recorded runs used an equivalent workspace-local copy of the driver).
- No scorer, threshold, weight or controller policy was changed.
