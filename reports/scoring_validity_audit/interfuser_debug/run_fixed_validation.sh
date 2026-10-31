#!/usr/bin/env bash
# Re-validation of the InterFuser adapter with its defaults restored to the original agent.
#   PY=<marshal env python> bash reports/scoring_validity_audit/interfuser_debug/run_fixed_validation.sh
# (1) A->B, no officer, light held green, green_stop seeds 0-2   -> tmp/_validation_interfuser/ab/
# (2) all 25 protocol-v2 scenarios, seed 0 (leaderboard setting) -> tmp/_validation_interfuser/authority/
# Starts and kills CARLA (run_protocol_v2.py). Finished episodes are skipped on re-run.
set -u
PY="${PY:-python}"
OUT=tmp/_validation_interfuser
# Pinned: these runs were made with the 3-D speed input (adapter default at the time).
PIN='MARSHAL_CONTROLLER_OVERRIDES={"interfuser": {"speed_input": "3d"}}'
"$PY" scripts/run_protocol_v2.py --scenarios green_stop --seeds 0 1 2 --models interfuser \
  --log "$OUT/ab/run_log.jsonl" --out-root "$OUT/ab" --tag _noofficer \
  --env MARSHAL_OFFICER_CONDITION=absent --env MARSHAL_INTERFUSER_VERBOSE=1 --env "$PIN"
"$PY" scripts/run_protocol_v2.py --seeds 0 --models interfuser \
  --log "$OUT/authority/run_log.jsonl" --out-root "$OUT/authority" --env MARSHAL_INTERFUSER_VERBOSE=1 --env "$PIN"
