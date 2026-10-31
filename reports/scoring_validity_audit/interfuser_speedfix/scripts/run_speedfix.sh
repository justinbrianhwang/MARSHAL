#!/usr/bin/env bash
# Speed-input re-measure: the 9 selected STOP scenarios x seeds 0-2 with the current adapter defaults
# (speed_input "forward"); otherwise identical to tmp/_remeasure_interfuser/fixed (same runner, stations, horizon,
# staging, weather, light handling, verbose trace).
#   PY=<marshal env python> bash reports/scoring_validity_audit/interfuser_speedfix/scripts/run_speedfix.sh
# Run from the checkout root; starts and kills CARLA. Finished episodes are skipped on re-run.
set -u
PY="${PY:-python}"
OUT=tmp/_remeasure_interfuser/speedfix
"$PY" scripts/run_protocol_v2.py --models interfuser --seeds 0 1 2 \
  --scenarios signal_off conflicting_authorities two_civilians_disagree occluded_officer fake_vest_director \
              flagger_slow_then_stop dual_authority_handoff school_crossing_guard green_stop \
  --out-root "$OUT" --log "$OUT/run_log.jsonl" --env MARSHAL_INTERFUSER_VERBOSE=1 \n  --env 'MARSHAL_CONTROLLER_OVERRIDES={"interfuser": {"speed_input": "forward"}}'
