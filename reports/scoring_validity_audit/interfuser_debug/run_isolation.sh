#!/usr/bin/env bash
# InterFuser no-start isolation: green_stop, no officer, one adapter variant per cell, per-tick verbose trace.
#   PY=<marshal env python> bash reports/scoring_validity_audit/interfuser_debug/run_isolation.sh [variant ...]
# Run from the checkout root. Starts and kills CARLA (run_protocol_v2.py), so nothing else may use CARLA.
# Finished cells are skipped on re-run. SEEDS (default 0) and OUT override the seeds and output root.
set -u
PY="${PY:-python}"
OUT="${OUT:-tmp/_validation_interfuser/isolation}"
SEEDS="${SEEDS:-0}"
# Each variant is the protocol-v2 adapter configuration (LEGACY) with one or more items restored to the original.
LEGACY='"route_sample_m": 0, "sensor_profile": "carla_default", "step_on_sensor_data": false, "hold_odd_step_control": false, "speed_input": "3d"'
declare -A V=(
  [asis]="{\"interfuser\": {$LEGACY}}"
  [sensors]="{\"interfuser\": {$LEGACY, \"sensor_profile\": \"leaderboard\"}}"
  [route50]="{\"interfuser\": {$LEGACY, \"route_sample_m\": 50}}"
  [sensors_route50]="{\"interfuser\": {$LEGACY, \"sensor_profile\": \"leaderboard\", \"route_sample_m\": 50}}"
  [phase]="{\"interfuser\": {$LEGACY, \"step_on_sensor_data\": true}}"
  [phase_sensors]="{\"interfuser\": {$LEGACY, \"step_on_sensor_data\": true, \"sensor_profile\": \"leaderboard\"}}"
  [fixed]='{"interfuser": {"speed_input": "3d"}}'
)
for v in "${@:-asis sensors route50 sensors_route50 phase phase_sensors fixed}"; do
  for name in $v; do
    "$PY" scripts/run_protocol_v2.py --scenarios green_stop --seeds $SEEDS --models interfuser \
      --log "$OUT/run_log.jsonl" --out-root "$OUT" --tag "_noofficer_$name" \
      --env MARSHAL_OFFICER_CONDITION=absent --env MARSHAL_INTERFUSER_VERBOSE=1 \
      --env "MARSHAL_CONTROLLER_OVERRIDES=${V[$name]}"
  done
done
