#!/usr/bin/env python
"""MARSHAL benchmark — single entry point for scoring an autonomous-driving model.

This is the script a **third party** runs to measure their model on MARSHAL.
You only need to provide one thing: a *controller* — a small class that turns each
tick's observation into a ``carla.VehicleControl``. Everything else (the Town03
stations, the gesturing officer, the construction flagger, the following
ambulance, the fountain lab-logo landmarks, the metrics, the MARSHAL Score) is
spawned and computed for you.

Quick start
-----------
1. Start CARLA on the benchmark map (Town03, or Town03_MARSHAL once baked)::

       CarlaUE4.exe          # or ./CarlaUE4.sh  -quality-level=Epic

2. Run the full benchmark with a built-in controller to sanity-check::

       python start.py --controller baseline        # TM autopilot (officer-blind)
       python start.py --controller oracle           # privileged upper bound

3. Run YOUR model — point ``--controller`` at your EpisodeController subclass::

       python start.py --controller my_pkg.my_model:MyController --tag my_model

   See ``marshal_bench/controllers/example_model.py`` for a copy-paste template
   and ``docs/benchmarking_your_model.md`` for the full guide.

Output
------
* A per-model scoreboard JSON at ``<out>/<tag>/scoreboard.json``.
* A readable table on stdout: per-scenario pass + authority-conflict type, the
  conflict-type profile, and the weighted MARSHAL Score.

The diagnostic headline is the **authority-conflict profile**: strict passes over
override, stressed-override, validity, conflict, scene, and safety cases.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from marshal_bench.criteria.marshal_metrics import (  # noqa: E402
    compute_episode_metrics, aggregate, CONFLICT_TYPE, CONFLICT_TYPE_ORDER,
    REASONING_TIER, SCENARIO_SPEC)
from marshal_bench.utils.conditions import (  # noqa: E402
    merge_condition_config,
    parse_weather_params,
)

PY = sys.executable
RUNNER = os.path.join(_THIS, "scripts", "run_marshal_officer_demo.py")

# The 21 MARSHAL scenarios: 14 core + 7 expansion (authority-axis coverage).
ALL_SCENARIOS = [
    "green_stop", "red_proceed", "signal_off", "crash_detour", "fallen_person",
    "unauthorized_go", "adjacent_lane", "flagger_control", "ambulance_yield",
    "occluded_officer", "conflicting_authorities", "sequential_directive",
    "rule_hierarchy", "ambiguous_gesture",
    # Expansion scenarios (broaden the contextual/verification/temporal axes).
    "civilian_warning_accident", "emergency_scene_blocking",
    "two_civilians_disagree", "flagger_slow_then_stop", "school_crossing_guard",
    "fake_vest_director", "barricade_self_detour",
]


# ---------------------------------------------------------------------------
def _run_episode(controller: str, scenario: str, args, out_root: str) -> dict | None:
    """Run one scenario in an isolated subprocess and return its result dict."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    cmd = [PY, RUNNER,
           "--scenario", scenario,
           "--town", args.town,
           "--controller", controller,
           "--host", args.host,
           "--port", str(args.port),
           "--fps", str(args.fps),
           "--out", out_root]
    condition_cfg = _build_episode_condition_cfg(args)
    condition = condition_cfg.get("weather") or {}
    if "preset" in condition:
        cmd.extend(["--weather", condition["preset"]])
    if "params" in condition:
        encoded = ",".join(f"{key}={value}" for key, value in condition["params"].items())
        cmd.extend(["--weather-params", encoded])
    if args.debug:
        cmd.append("--debug")
    try:
        proc = subprocess.run(
            cmd, env=env, cwd=_THIS, timeout=args.episode_timeout,
            stdout=(None if args.debug else subprocess.DEVNULL),
            stderr=(None if args.debug else subprocess.DEVNULL),
        )
        if proc.returncode not in (0, None):
            print(f"     (subprocess exit {proc.returncode})", flush=True)
    except subprocess.TimeoutExpired:
        print(f"     TIMEOUT after {args.episode_timeout}s", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"     run error: {e}", flush=True)

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


def _print_scoreboard(tag: str, board: dict, per: dict) -> None:
    print("\n" + "=" * 64)
    print(f"  MARSHAL SCOREBOARD  —  model: {tag}")
    print("=" * 64)
    print(f"\n  {'scenario':24s} {'conflict type':19s} {'pass':5s}  expected")
    print(f"  {'-'*24} {'-'*19} {'-'*5}  {'-'*8}")
    for scen in ALL_SCENARIOS:
        info = per.get(scen)
        if info is None:
            print(f"  {scen:24s} {'-':19s} {'NORUN':5s}")
            continue
        exp = SCENARIO_SPEC.get(scen, {}).get("expected", "?")
        mark = "PASS" if info["passed"] else "FAIL"
        print(f"  {scen:24s} {str(info['conflict_type']):19s} {mark:5s}  {exp}")

    profile = board.get("conflict_type_profile", {})
    print("\n  authority-conflict profile:")
    for conflict_type in CONFLICT_TYPE_ORDER:
        item = profile.get(conflict_type)
        if item:
            pct = round(100.0 * item["pass_rate"], 1)
            print(f"    {conflict_type:19s} {pct:5.1f}%   "
                  f"({item['passed']}/{item['total']})")

    # Legacy tier summary retained behind the conflict-type headline.
    tp = board.get("tier_pass_rate", {})
    print("\n  reasoning-tier pass-rate (legacy):")
    for tier in ("low", "mid", "high"):
        t = tp.get(tier)
        if t:
            pct = round(100.0 * t["pass_rate"], 1)
            print(f"    {tier:5s}  {pct:5.1f}%   ({t['n']} scenarios)")

    suite = {k: v for k, v in (board.get("suite") or {}).items() if v is not None}
    print(f"\n  metric suite : {suite}")
    print(f"  R-subscores  : {board.get('r_scores')}")
    print(f"  unmeasured R : {board.get('r_unmeasured')}")
    print(f"\n  >>> MARSHAL Score (partial): {board.get('marshal_score_partial')} / 100")
    print("=" * 64 + "\n")


# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Score an autonomous-driving model on the MARSHAL benchmark.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--controller", required=True,
                   help="The model under test: 'baseline', 'oracle', or a "
                        "'module:ClassName' path to your EpisodeController subclass.")
    p.add_argument("--tag", default=None,
                   help="Label for this run's output folder + scoreboard "
                        "(default: derived from --controller).")
    p.add_argument("--scenarios", nargs="*", default=None,
                   help="Subset of scenarios to run (default: all 14).")
    p.add_argument("--town", default="Town03",
                   help="Benchmark map. Use 'Town03_MARSHAL' once the logo-baked "
                        "map is packaged.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2000)
    p.add_argument("--fps", type=float, default=20.0)
    p.add_argument("--weather", default=None,
                   help="CARLA WeatherParameters preset name.")
    p.add_argument("--weather-params", type=parse_weather_params, default=None,
                   metavar="K=V,K=V",
                   help="Float weather parameters applied over --weather.")
    p.add_argument("--episode-timeout", type=float, default=300.0,
                   help="Wall-clock seconds before an episode is abandoned.")
    p.add_argument("--out", default=os.path.join(_THIS, "outputs", "benchmark"),
                   help="Output root; results go to <out>/<tag>/.")
    p.add_argument("--debug", action="store_true",
                   help="Stream per-episode logs + officer debug visuals.")
    return p


def _build_episode_condition_cfg(args: argparse.Namespace) -> dict:
    """Build the exact condition fragment forwarded to every episode."""
    return merge_condition_config({}, args.weather, args.weather_params)


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    scenarios = args.scenarios or ALL_SCENARIOS
    unknown = [s for s in scenarios if s not in ALL_SCENARIOS]
    if unknown:
        print(f"Unknown scenario(s): {unknown}\nKnown: {ALL_SCENARIOS}",
              file=sys.stderr)
        return 2

    tag = args.tag or args.controller.replace(":", "_").replace(".", "_").replace("/", "_")
    out_root = os.path.join(args.out, tag)
    os.makedirs(out_root, exist_ok=True)

    print(f"MARSHAL benchmark | model={tag} | controller={args.controller} | "
          f"map={args.town} | {len(scenarios)} scenarios")
    t0 = time.monotonic()

    metrics = []
    per = {}
    episode_conditions = []
    for i, scen in enumerate(scenarios, 1):
        print(f"  [{i:2d}/{len(scenarios)}] {scen} ...", flush=True)
        res = _run_episode(args.controller, scen, args, out_root)
        if res is None:
            print(f"        NO RESULT (episode did not produce result.json)",
                  flush=True)
            continue
        em = compute_episode_metrics(res, scenario=scen)
        metrics.append(em)
        if isinstance(res.get("condition"), dict):
            episode_conditions.append(res["condition"])
        per[scen] = {
            "passed": em.passed,
            "conflict_type": CONFLICT_TYPE.get(scen),
            "tier": REASONING_TIER.get(scen),  # legacy
            "weather_applied": bool((res.get("condition") or {}).get("weather_applied")),
        }
        print(f"        {'PASS' if em.passed else 'FAIL'}  "
              f"(conflict_type={CONFLICT_TYPE.get(scen)})", flush=True)

    if not metrics:
        print("\nNo episodes produced results. Is CARLA running on "
              f"{args.host}:{args.port} with map {args.town}?", file=sys.stderr)
        return 1

    board = aggregate(metrics)
    board["model"] = tag
    board["controller"] = args.controller
    board["map"] = args.town
    # All episodes receive the same requested condition.  Use the first actual
    # world-derived block so the scoreboard records what CARLA ran, not merely
    # what the user requested.
    board["condition"] = episode_conditions[0] if episode_conditions else None
    board["per_scenario_pass"] = per
    board["wall_seconds"] = round(time.monotonic() - t0, 1)

    sb_path = os.path.join(out_root, "scoreboard.json")
    with open(sb_path, "w", encoding="utf-8") as fh:
        json.dump(board, fh, indent=2)

    _print_scoreboard(tag, board, per)
    print(f"  scoreboard -> {sb_path}")
    print(f"  per-episode logs -> {out_root}/")
    print(f"  wall time: {board['wall_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
