#!/usr/bin/env python
"""Run baseline/oracle reference controllers with shared runner-local staging."""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import time
import traceback
from typing import Any, Dict, List

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _run_vlm_test as vlm  # noqa: E402
import _shared_staging as staging  # noqa: E402
from marshal_bench.criteria.marshal_metrics import (  # noqa: E402
    REASONING_TIER,
    aggregate,
    compute_episode_metrics,
)
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.utils.conditions import (  # noqa: E402
    merge_condition_config,
    parse_weather_params,
)
from marshal_bench.utils.logging_utils import EpisodeLogger, setup_root_logger  # noqa: E402

OUT_ROOT = os.path.join(ROOT, "tmp", "_codex_reference_sweep_runs")
RESULTS_JSON = os.path.join(ROOT, "tmp", "_codex_reference_sweep.json")
REPORT_MD = os.path.join(ROOT, "tmp", "_codex_reference_sweep_report.md")
CONTROLLERS = ("baseline", "oracle")


def _safe_rmtree(path: str, root: str) -> None:
    path = os.path.abspath(path)
    root = os.path.abspath(root)
    if path == root or not path.startswith(root + os.sep):
        raise RuntimeError(f"refusing to delete outside output root: {path}")
    shutil.rmtree(path, ignore_errors=True)


def _episode_id(controller: str, scenario: str) -> str:
    return f"reference_{controller}_{scenario}"


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "as_dict"):
        try:
            return _jsonable(obj.as_dict())
        except Exception:
            pass
    return str(obj)


def _run_one(
    client: Any,
    controller: str,
    scenario: str,
    seed: Any = None,
    weather: Any = None,
    weather_params: Any = None,
) -> Dict[str, Any]:
    spec = vlm.SCENARIOS[scenario]
    cfg = staging.load_staged_config(ROOT, scenario, spec, controller)
    merge_condition_config(cfg, weather, weather_params)
    if seed is not None:
        cfg["seed"] = int(seed)
    cfg["episode_id"] = _episode_id(controller, scenario) + (f"_s{seed}" if seed is not None else "")
    episode_dir = os.path.join(OUT_ROOT, cfg["episode_id"])
    if os.path.isdir(episode_dir):
        _safe_rmtree(episode_dir, OUT_ROOT)
    logger = EpisodeLogger(cfg["episode_id"], output_root=OUT_ROOT)
    cfg["_episode_logger"] = logger
    mod = importlib.import_module(f"marshal_bench.scenarios.{spec['module']}")
    restore = staging.apply_runner_local_patches(scenario, mod, cfg)
    started = time.perf_counter()
    result: Dict[str, Any] = {}
    error = None
    tb = None
    try:
        result = mod.run(client, cfg, logger)
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
        tb = traceback.format_exc()
    finally:
        restore()
        try:
            logger.close()
        except Exception:
            pass

    compliance = result.get("compliance") or {}
    marshal_metrics = result.get("marshal_metrics") or {}
    strict_scoring = result.get("strict_scoring") or {}
    if result and not strict_scoring:
        strict_scoring = {
            "passed": False,
            "invalid": True,
            "verdict": "INVALID",
            "reason": "missing strict_scoring in scenario result",
        }
    if not result:
        strict_scoring = {
            "passed": False,
            "invalid": True,
            "verdict": "INVALID",
            "reason": error or "scenario returned no result",
        }
    episode_metrics = None
    if result:
        try:
            episode_metrics = compute_episode_metrics(result, scenario=scenario)
        except Exception:
            episode_metrics = None
    passed = episode_metrics.passed if episode_metrics is not None else False
    row = {
        "model": controller,
        "track": "reference",
        "scenario": scenario,
        "expected": spec["expect"],
        "passed": passed,
        "tier": REASONING_TIER.get(scenario),
        "terminated_reason": result.get("terminated_reason", "error" if error else None),
        "compliance_reason": vlm._compliance_reason(compliance),
        "episode_metrics": episode_metrics.as_dict() if episode_metrics is not None else None,
        "marshal_metrics": marshal_metrics,
        "strict_scoring": strict_scoring,
        "final_speed_kmh": strict_scoring.get("final_speed_kmh"),
        "runtime_s": round(time.perf_counter() - started, 2),
        "exception": error,
        "traceback": tb,
        "episode_dir": logger.episode_dir,
        "condition": result.get("condition"),
        "weather_applied": bool((result.get("condition") or {}).get("weather_applied")),
    }
    try:
        with open(os.path.join(logger.episode_dir, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"result": _jsonable(result), "row": row}, fh, indent=2, default=str)
    except Exception:
        pass
    print(
        "{controller} / {scenario}: pass={passed} terminated={terminated}".format(
            controller=controller,
            scenario=scenario,
            passed=row["passed"],
            terminated=row["terminated_reason"],
        ),
        flush=True,
    )
    return row


def _write_outputs(rows: List[Dict[str, Any]]) -> None:
    boards: Dict[str, Any] = {}
    for controller in CONTROLLERS:
        metrics = []
        for row in rows:
            if row.get("model") != controller or not row.get("episode_metrics"):
                continue
            try:
                metrics.append(compute_episode_metrics({"compliance": {}}, scenario=row["scenario"]))
                metrics[-1] = type(metrics[-1])(**row["episode_metrics"])
            except Exception:
                pass
        boards[controller] = aggregate(metrics) if metrics else {}
    payload = {
        "run": {
            "staging_source": staging.STAGING_SOURCE,
            "fps": 20,
            "timeout_sec": 14,
            "controllers": list(CONTROLLERS),
        },
        "rows": rows,
        "aggregate": boards,
    }
    os.makedirs(os.path.dirname(RESULTS_JSON), exist_ok=True)
    with open(RESULTS_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    lines = [
        "# Reference Staging Sweep",
        "",
        f"Shared staging source: `{staging.STAGING_SOURCE}`.",
        "",
        "| Controller | Scenario | Tier | Expected | Pass | Terminated |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {model} | {scenario} | {tier} | {expected} | {passed} | {terminated} |".format(
                model=row.get("model"),
                scenario=row.get("scenario"),
                tier=row.get("tier"),
                expected=row.get("expected"),
                passed=row.get("passed"),
                terminated=row.get("terminated_reason"),
            )
        )
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*",
                        help="Scenario keys. Defaults to all (SCENARIO_ORDER).")
    parser.add_argument("--controller", action="append", dest="controllers",
                        choices=list(CONTROLLERS),
                        help="Restrict to these controllers (default: both).")
    parser.add_argument("--seed", type=int, default=None,
                        help="Scenario RNG seed; also namespaces the episode id "
                             "as <id>_s<seed> so seeds do not overwrite each other.")
    parser.add_argument("--weather", default=None,
                        help="CARLA WeatherParameters preset name.")
    parser.add_argument("--weather-params", type=parse_weather_params, default=None,
                        metavar="K=V,K=V",
                        help="Float weather parameters applied over --weather.")
    args = parser.parse_args()
    controllers = args.controllers or list(CONTROLLERS)
    scenarios = args.scenarios or list(vlm.SCENARIO_ORDER)

    setup_root_logger()
    carla = import_carla()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(120.0)
    world = client.get_world()
    map_name = world.get_map().name
    if map_name.rsplit("/", 1)[-1].lower() != "town03":
        raise SystemExit(f"CARLA is on {map_name!r}, expected Town03. Not loading maps.")
    os.makedirs(OUT_ROOT, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for controller in controllers:
        for scenario in scenarios:
            rows.append(_run_one(
                client,
                controller,
                scenario,
                seed=args.seed,
                weather=args.weather,
                weather_params=args.weather_params,
            ))
            _write_outputs(rows)
    _write_outputs(rows)
    print(f"Wrote {RESULTS_JSON}")
    print(f"Wrote {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
