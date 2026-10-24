#!/bin/bash
# Instrumented green_stop runs for the evaluation/timing audit (reports/dac2027_audit/06_reproduction.md).
#
#   A) leaderboard wiring (14 s timeout, early termination on, VLM 3-query cap): oracle, tcp, interfuser, qwen3vl
#   B) authority-experiment wiring (25 s, no early termination): TCP police STOP, seed 1
#   C) Town03 OpenDRIVE + traffic-light export for the offline geometry tables
#
# Run from the workspace root (the directory that holds scripts/, marshal_bench/ and CARLA_0.9.16/).
# Override the interpreters / CARLA path with the environment variables below. The hosted VLM needs its API key.
# Exit status is the number of failed steps (0 = every episode completed and was scored, and the map was exported).
set -u
CARLA_DIR=${CARLA_DIR:-CARLA_0.9.16}
MPY=${MPY:-python}                 # env with carla + marshal deps
TPY=${TPY:-$MPY}                   # env for TCP (torch)
IPY=${IPY:-$MPY}                   # env for InterFuser
OUT=${OUT:-outputs/dac2027_audit}
R=$OUT/runs; LOG=$OUT/logs/batch_timing.log
mkdir -p "$R" "$OUT/logs"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 PYTHONPATH="$PWD"
# Leaderboard wiring means no behaviour override: drop every inherited MARSHAL_* variable.
for v in $(env | grep -o '^MARSHAL_[A-Za-z0-9_]*'); do unset "$v"; done
FAILED=0

restart_carla() {  # a fresh server per episode: no actor can leak from one episode into the next
  taskkill //F //IM CarlaUE4-Win64-Shipping.exe > /dev/null 2>&1; taskkill //F //IM CarlaUE4.exe > /dev/null 2>&1; sleep 5
  (cd "$CARLA_DIR" && ./CarlaUE4.exe -carla-rpc-port=2000 -quality-level=Epic -windowed -ResX=640 -ResY=480 > /dev/null 2>&1 &)
  sleep 40
  for i in 1 2 3 4; do timeout 240 $MPY scripts/dac2027_carla_util.py load-town03 > /dev/null 2>&1 && break; sleep 20; done
  $MPY scripts/dac2027_carla_util.py world-check "pre:$1" >> "$OUT/logs/world_checks.jsonl" \
    || { echo "ABORT: world not clean before $1" >> "$LOG"; return 3; }
}

complete() {  # done = scenario_finished event, PASS/FAIL strict verdict, non-empty telemetry (parsed as JSON)
  local ep
  ep=$(ls -d "$1"/*/ 2>/dev/null | head -1)
  [ -n "$ep" ] && $MPY scripts/dac2027_carla_util.py episode-complete "$ep" > /dev/null 2>&1
}

run() {  # run <label> <out> <command...>
  local label=$1 out=$2; shift 2
  complete "$out" && { echo "skip complete $label" >> "$LOG"; return 0; }
  rm -rf "$out"
  restart_carla "$label" || { FAILED=$((FAILED + 1)); return 0; }
  echo "=== $(date +%T) $label" >> "$LOG"
  "$@" >> "$LOG" 2>&1
  local rc=$?
  echo "exit=$rc $(date +%T)" >> "$LOG"
  if [ $rc -ne 0 ] || ! complete "$out"; then
    echo "FAILED: $label (exit $rc, or episode incomplete / INVALID)" >> "$LOG"
    FAILED=$((FAILED + 1))
  fi
}

L=$R/leaderboard_wiring
run lb_oracle "$L/oracle" env MARSHAL_OUT_ROOT="$L/oracle" \
  $MPY scripts/_run_reference_staging_sweep.py green_stop --controller oracle
run lb_tcp "$L/tcp" env MARSHAL_OUT_ROOT="$L/tcp" \
  $TPY scripts/_run_more_e2e_sweep.py green_stop --models tcp --wall-timeout 1800 --results-json "$L/tcp.json" --report "$L/tcp.md"
run lb_interfuser "$L/interfuser" env MARSHAL_OUT_ROOT="$L/interfuser" \
  $IPY scripts/_run_more_e2e_sweep.py green_stop --models interfuser --wall-timeout 1800 --results-json "$L/interfuser.json" --report "$L/interfuser.md"
run lb_qwen3vl "$L/qwen3vl" env MARSHAL_VLM_SAVE_INPUTS=1 MARSHAL_OUT_ROOT="$L/qwen3vl" \
  $MPY scripts/_run_vlm_test.py green_stop --model Qwen/Qwen3-VL-235B-A22B-Instruct --results-json "$L/qwen3vl.json" --report "$L/qwen3vl.md"

A=$R/authority_wiring/tcp/police_stop_s1
run auth_tcp_police_stop_s1 "$A" env MARSHAL_NO_STOP_EARLY_TERMINATION=1 MARSHAL_TIMEOUT_S=25 MARSHAL_SEED=1 MARSHAL_OUT_ROOT="$A" \
  $TPY scripts/_run_more_e2e_sweep.py green_stop --models tcp --wall-timeout 1800 --results-json "$A.json" --report "$A.md"

map_ok() { [ -s "$OUT/map/Town03.xodr" ] && [ -s "$OUT/map/town03_traffic_lights.json" ]; }
if ! map_ok; then
  { restart_carla map_export && $MPY scripts/dac2027_carla_util.py export-map "$OUT/map" >> "$LOG" 2>&1 && map_ok; } \
    || { echo "FAILED: map export" >> "$LOG"; FAILED=$((FAILED + 1)); }
fi

taskkill //F //IM CarlaUE4-Win64-Shipping.exe > /dev/null 2>&1; taskkill //F //IM CarlaUE4.exe > /dev/null 2>&1
echo "DONE $(date +%T), failed steps: $FAILED" >> "$LOG"
exit $FAILED
