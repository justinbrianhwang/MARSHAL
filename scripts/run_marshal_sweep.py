"""Clean baseline-vs-oracle sweep across all MARSHAL scenarios.

Runs every registered scenario under each controller (TM baseline + oracle),
isolated per-run via subprocess + a per-controller output root, then aggregates
the per-episode marshal_metrics into a scoreboard JSON + a readable table.

The scoreboard's headline is the reasoning-tier pass-rate split — the evidence
for why high-level (LLM-style) reasoning is required beyond E2E/perception.

Usage (CARLA must be running on Town03):
    C:/.../envs/marshal/python.exe scripts/run_marshal_sweep.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from marshal_bench.criteria.marshal_metrics import (  # noqa: E402
    compute_episode_metrics, aggregate, REASONING_TIER)

PY = sys.executable
RUNNER = os.path.join(_THIS, "run_marshal_officer_demo.py")
SCENARIOS = ["green_stop", "red_proceed", "signal_off", "crash_detour",
             "fallen_person", "unauthorized_go", "adjacent_lane",
             "flagger_control", "ambulance_yield",
             "occluded_officer", "conflicting_authorities",
             "sequential_directive", "rule_hierarchy", "ambiguous_gesture"]
CONTROLLERS = ["baseline", "oracle"]


def run_one(controller: str, scenario: str) -> dict | None:
    out_root = os.path.join(_ROOT, "outputs", f"sweep_{controller}")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    cmd = [PY, RUNNER, "--scenario", scenario, "--town", "Town03",
           "--controller", controller, "--out", out_root]
    print(f"  [{controller}] {scenario} ...", flush=True)
    try:
        subprocess.run(cmd, env=env, cwd=_ROOT, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        print(f"     run failed: {e}", flush=True)
    # find the result.json this scenario wrote
    if not os.path.isdir(out_root):
        return None
    for d in os.listdir(out_root):
        if scenario.replace("_", "") in d.replace("_", ""):
            rj = os.path.join(out_root, d, "result.json")
            if os.path.isfile(rj):
                try:
                    blob = json.load(open(rj, encoding="utf-8"))
                    return blob.get("result", blob)
                except Exception:  # noqa: BLE001
                    return None
    return None


def main() -> int:
    board = {}
    for controller in CONTROLLERS:
        print(f"=== controller: {controller} ===", flush=True)
        ems = []
        per = {}
        for scen in SCENARIOS:
            res = run_one(controller, scen)
            if res is None:
                print(f"     {scen}: NO RESULT", flush=True)
                continue
            em = compute_episode_metrics(res, scenario=scen)
            ems.append(em)
            per[scen] = {"passed": em.passed, "tier": REASONING_TIER.get(scen)}
        board[controller] = aggregate(ems)
        board[controller]["per_scenario_pass"] = per

    out = os.path.join(_ROOT, "outputs", "scoreboard.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(board, fh, indent=2)
    print(f"\nwrote {out}")

    # readable summary
    print("\n================ MARSHAL SCOREBOARD ================")
    for controller in CONTROLLERS:
        b = board.get(controller, {})
        print(f"\n[{controller}]  n={b.get('n_episodes')}  "
              f"MARSHAL(partial)={b.get('marshal_score_partial')}")
        print("  tier pass-rate:", b.get("tier_pass_rate"))
        print("  suite:", {k: v for k, v in (b.get("suite") or {}).items()
                           if v is not None})
    return 0


if __name__ == "__main__":
    sys.exit(main())
