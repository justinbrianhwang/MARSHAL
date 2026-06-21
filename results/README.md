# Results

Committed MARSHAL scoreboards, one JSON per model run.

Each `start.py` run writes `outputs/benchmark/<tag>/scoreboard.json`; copy the
final one here (e.g. `results/baseline.json`, `results/oracle.json`,
`results/<your_model>.json`) to version it. The top-level `README.md` results
table summarizes these.

Schema (per file): `marshal_score_partial`, `tier_pass_rate` (low/mid/high),
`suite` (AOC/FOA/TAA/SBO/CRI/RTL/OCC/APR/DRM/RHC/AGI), `r_scores`,
`per_scenario_pass`, `per_episode`.
