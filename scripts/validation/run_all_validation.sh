#!/usr/bin/env bash
# green_stop validation suite (scoring-validity audit): (1) model execution A->B, (2) feature check and
# (3) credit discrimination with scripted fixtures. See reports/scoring_validity_audit/validation_results.md.
#
#   bash scripts/validation/run_all_validation.sh            # everything (~2.5 h)
#   bash scripts/validation/run_all_validation.sh 1          # one phase: 1 | 1b | 23 | summary
#
# Run from anywhere; it works in this checkout's root. run_protocol_v2.py STARTS AND KILLS CARLA itself, so this
# script refuses to start while a CARLA process is up (another experiment may own it). FORCE=1 overrides.
# Already finished episodes are skipped on re-run, so the script can be resumed after an interruption.
# Env overrides: MARSHAL_PY_MARSHAL / _TRANSFUSER_UI / _INTERFUSER_FL / _OPENEMMA (interpreters, else resolved from
# the conda root), MARSHAL_CARLA_DIR (default ../CARLA_0.9.16), OUT (default tmp/_validation_greenstop).
set -u
cd "$(dirname "$0")/../.."
PY="${MARSHAL_PY_MARSHAL:-$HOME/miniconda3/envs/marshal/python.exe}"
[ -x "$PY" ] || PY=python  # must be the 'marshal' env interpreter; the other envs are resolved next to it
OUT="${OUT:-tmp/_validation_greenstop}"
export MARSHAL_CARLA_DIR="${MARSHAL_CARLA_DIR:-../CARLA_0.9.16}"
PHASE="${1:-all}"
RC=0  # any failed phase command makes the launcher fail, even if the summary is complete

if [ "${FORCE:-0}" != "1" ] && [ "$PHASE" != "summary" ] && tasklist 2>/dev/null | grep -qi "CarlaUE4"; then
  echo "CARLA is running (possibly another experiment). Not starting. Re-run when it is free, or FORCE=1." >&2
  exit 2
fi
# Runner checkpoints live under <workspace>/Models; link them into this checkout once (ignored by git).
if [ ! -e Models ] && [ -d ../Models ]; then
  powershell -NoProfile -Command "New-Item -ItemType Junction -Path Models -Target ..\\Models | Out-Null"
  ex="$(git rev-parse --git-common-dir)/info/exclude"
  grep -qx "/Models" "$ex" 2>/dev/null || echo "/Models" >> "$ex"
fi
mkdir -p "$OUT"
LOG="$OUT/run_log.jsonl"
RUN=("$PY" scripts/run_protocol_v2.py --scenarios green_stop --seeds 0 --log "$LOG")
NOOFF=(--env MARSHAL_OFFICER_CONDITION=absent)
ALL_MODELS=(oracle baseline transfuser tcp interfuser cilrs aim neat pid mpc openemma glm-4.5v qwen2.5-vl qwen3-vl)
FIXTURES=(scripted_normal_stop scripted_late_stop scripted_overshoot scripted_no_start scripted_lurch_then_stop scripted_constant_go)

if [ "$PHASE" = all ] || [ "$PHASE" = 1 ]; then
  # (1) A->B: no officer, held-green light, every leaderboard stack as integrated (~55 min)
  "${RUN[@]}" --out-root "$OUT/1_ab" --models "${ALL_MODELS[@]}" "${NOOFF[@]}" --tag _noofficer || RC=1
fi
if [ "$PHASE" = all ] || [ "$PHASE" = 1b ]; then
  # (1b) integration-deviation variants, same no-officer condition (~35 min)
  "${RUN[@]}" --out-root "$OUT/1b_variants" --models transfuser "${NOOFF[@]}" --tag _noofficer_10hz \
    --env 'MARSHAL_CONTROLLER_OVERRIDES={"transfuser": {"action_repeat": 1}}' || RC=1
  "${RUN[@]}" --out-root "$OUT/1b_variants" --models transfuser "${NOOFF[@]}" --tag _noofficer_60s \
    --env MARSHAL_TIMEOUT_S=60 || RC=1
  "${RUN[@]}" --out-root "$OUT/1b_variants" --models transfuser "${NOOFF[@]}" --tag _noofficer_10hz_60s \
    --env 'MARSHAL_CONTROLLER_OVERRIDES={"transfuser": {"action_repeat": 1}}' --env MARSHAL_TIMEOUT_S=60 || RC=1
  "${RUN[@]}" --out-root "$OUT/1b_variants" --models tcp aim neat "${NOOFF[@]}" --tag _noofficer_target50 \
    --env 'MARSHAL_CONTROLLER_OVERRIDES={"tcp": {"lookahead_m": 50}, "aim": {"lookahead_m": 50}, "neat": {"lookahead_m": 50}}' || RC=1
fi
if [ "$PHASE" = all ] || [ "$PHASE" = 23 ]; then
  # (2)+(3) scripted fixtures on the standard green_stop (officer STOP), seeds 0-2 (~55 min)
  "$PY" scripts/run_protocol_v2.py --scenarios green_stop --seeds 0 1 2 --log "$LOG" \
    --out-root "$OUT/23_fixtures" --models "${FIXTURES[@]}" || RC=1
fi
"$PY" scripts/validation/summarize_validation.py --root "$OUT" || RC=1
exit $RC
