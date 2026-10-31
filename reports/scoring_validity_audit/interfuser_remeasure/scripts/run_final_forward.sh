#!/usr/bin/env bash
# InterFuser final re-measure: 25 protocol-v2 scenarios x seeds 0-2 with the final adapter settings pinned
# (forward speed input + the fixed-adapter settings). Same harness, stations, horizon, staging, weather and light
# handling as tmp/_remeasure_interfuser/fixed; only the speed input differs.
#   PY=<marshal env python> bash reports/scoring_validity_audit/interfuser_remeasure/scripts/run_final_forward.sh
# Run from the checkout root; starts and kills CARLA. Finished episodes are skipped on re-run. Do not edit while running.
set -u
PY="${PY:-python}"
OUT=tmp/_remeasure_interfuser/final_forward
FINAL='{"interfuser": {"speed_input": "forward", "route_sample_m": 50, "sensor_profile": "leaderboard", "step_on_sensor_data": true, "hold_odd_step_control": true}}'
"$PY" scripts/run_protocol_v2.py --models interfuser --seeds 0 1 2 --out-root "$OUT" --log "$OUT/run_log.jsonl" \
  --env MARSHAL_INTERFUSER_VERBOSE=1 --env "MARSHAL_CONTROLLER_OVERRIDES=$FINAL"
