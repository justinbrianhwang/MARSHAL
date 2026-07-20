#!/usr/bin/env python
"""Run the per-town MARSHAL oracle calibration gate.

Examples (requires a running CARLA server)::

    python scripts/calibrate_town.py --town Town01
    python scripts/calibrate_town.py --town Town05 --scenarios green_stop,red_proceed
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import start as benchmark  # noqa: E402
from marshal_bench.criteria.graded_episode_scoring import (  # noqa: E402
    score_episode_from_telemetry as score_graded_episode,
)


CONFIGS_DIR = ROOT / "marshal_bench" / "configs"


def _normalise_town(town: str) -> str:
    town = str(town).strip().replace("\\", "/").split("/")[-1]
    if not town or not all(ch.isalnum() or ch in "_-" for ch in town):
        raise ValueError(f"invalid town name: {town!r}")
    return town


def _load_town_inputs(
    town: str, configs_dir: Path = CONFIGS_DIR
) -> tuple[dict[str, Any], dict[str, str]]:
    key = town.lower()
    station_name = "stations.json" if key == "town03" or key.startswith("town03_") else f"stations_{key}.json"
    station_path = configs_dir / station_name
    feasibility_path = configs_dir / f"feasibility_{key}.json"
    if not station_path.is_file():
        raise FileNotFoundError(f"station file not found: {station_path}")
    if not feasibility_path.is_file():
        raise FileNotFoundError(f"feasibility mask not found: {feasibility_path}")
    station_payload = json.loads(station_path.read_text(encoding="utf-8"))
    feasibility_payload = json.loads(feasibility_path.read_text(encoding="utf-8"))
    stations = station_payload.get("stations") or {}
    masked = {
        scenario: str(entry.get("reason") or "marked infeasible by map mask")
        for scenario, entry in feasibility_payload.items()
        if isinstance(entry, dict) and entry.get("feasible") is False
    }
    return stations, masked


def _station_entry_usable(entry: Any) -> bool:
    """True when a station entry can actually spawn an episode (finite x/y/yaw,
    plus a finite z when one is present — the runtime coerces z with float())."""
    if not isinstance(entry, Mapping):
        return False
    keys = ["x", "y", "yaw"] + (["z"] if "z" in entry else [])
    for key in keys:
        value = entry.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if not math.isfinite(float(value)):
            return False
    return True


def find_unusable_stations(
    scenarios: Iterable[str],
    stations: Mapping[str, Any],
    masked: Mapping[str, str],
) -> list[str]:
    """Alias-aware coverage check for the calibration gate.

    Expansion scenarios reuse an existing witness pose
    (marshal_bench.scenarios._common.STATION_ALIASES), and the runtime station
    lookup resolves the alias — so coverage must too. Presence alone is not
    enough: a present-but-degenerate entry (null, {}, missing/non-numeric
    x/y/yaw) would pass a membership check yet crash or silently random-spawn
    at runtime, so the entry shape is validated as well.
    """
    from marshal_bench.scenarios._common import STATION_ALIASES

    return [
        scenario for scenario in scenarios
        if scenario not in masked
        and not _station_entry_usable(stations.get(STATION_ALIASES.get(scenario, scenario)))
    ]


def _finite_values(rows: Iterable[Mapping[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _telemetry_from_result(result: Mapping[str, Any]) -> tuple[list[dict], Optional[str]]:
    strict = result.get("strict_scoring") or {}
    artifacts = strict.get("artifacts") or {}
    raw_path = artifacts.get("strict_telemetry_json")
    if not raw_path:
        return [], None
    path = Path(str(raw_path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], str(path)
    rows = payload.get("telemetry") or []
    return [dict(row) for row in rows if isinstance(row, dict)], str(path)


def _engagement_factor(graded: Mapping[str, Any]) -> Optional[float]:
    value = (graded.get("component_credits") or {}).get("engagement_gate")
    if value is None:
        value = ((graded.get("evidence") or {}).get("engagement") or {}).get("factor")
    try:
        return round(float(value), 6) if value is not None else None
    except (TypeError, ValueError):
        return None


def _officer_facing(result: Mapping[str, Any]) -> dict[str, Optional[float]]:
    raw = (result.get("officer_metadata") or {}).get("facing_ego_deg") or {}
    if not isinstance(raw, Mapping):
        raw = {"staging": raw}
    out: dict[str, Optional[float]] = {}
    for key in ("staging", "gesture_onset"):
        try:
            value = float(raw.get(key))
            out[key] = round(value, 4) if math.isfinite(value) else None
        except (TypeError, ValueError):
            out[key] = None
    return out


def shape_episode_result(scenario: str, result: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Shape one live or synthetic episode into calibration-report data."""
    if result is None:
        return {
            "passed": False,
            "graded_credit": 0.0,
            "engagement_factor": None,
            "officer_facing_ego_deg": {"staging": None, "gesture_onset": None},
            "diagnosis": {
                "strict_criterion": None,
                "strict_verdict": "NO_RESULT",
                "strict_reason": "episode did not produce result.json",
                "final_ego_to_stopline_m": None,
                "min_distance_to_hazard_m": None,
                "min_distance_to_officer_m": None,
                "strict_telemetry_json": None,
            },
        }

    strict = dict(result.get("strict_scoring") or {})
    rows, telemetry_path = _telemetry_from_result(result)
    graded = score_graded_episode(
        dict(result),
        rows,
        scenario=scenario,
        expected_action=strict.get("expected_action") or result.get("expected_action"),
        setup_errors=(result.get("scene_setup") or {}).get("errors") or (),
    ) if rows else {"credit": 0.0, "component_credits": {}, "evidence": {}}
    passed = bool(strict.get("passed"))
    item: dict[str, Any] = {
        "passed": passed,
        "graded_credit": round(float(graded.get("credit") or 0.0), 6),
        "engagement_factor": _engagement_factor(graded),
        "officer_facing_ego_deg": _officer_facing(result),
    }
    if not passed:
        stopline = _finite_values(rows[-1:], "distance_to_stopline_m")
        hazard = _finite_values(rows, "distance_to_hazard_m")
        officer = _finite_values(rows, "distance_to_officer_m")
        item["diagnosis"] = {
            "strict_criterion": strict.get("expected_action") or result.get("expected_action"),
            "strict_verdict": strict.get("verdict") or "MISSING",
            "strict_reason": strict.get("reason") or "strict scoring result missing",
            "final_ego_to_stopline_m": round(stopline[-1], 4) if stopline else None,
            "min_distance_to_hazard_m": round(min(hazard), 4) if hazard else None,
            "min_distance_to_officer_m": round(min(officer), 4) if officer else None,
            "strict_telemetry_json": telemetry_path,
        }
    return item


def build_report(
    town: str,
    scenario_results: Mapping[str, Optional[Mapping[str, Any]]],
    masked: Mapping[str, str],
    *,
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    per = {
        scenario: shape_episode_result(scenario, result)
        for scenario, result in scenario_results.items()
    }
    warnings = []
    for scenario, item in per.items():
        for phase, angle in item["officer_facing_ego_deg"].items():
            if angle is not None and angle > 45.0:
                warnings.append(
                    f"{scenario}: officer facing ego is {angle:.1f} deg at {phase} (>45 deg)"
                )
    return {
        "town": town,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "per_scenario": per,
        "feasible_n": len(per),
        "passed_n": sum(1 for item in per.values() if item["passed"]),
        "masked": dict(masked),
        "warnings": warnings,
    }


def _print_report(report: Mapping[str, Any], path: Path) -> None:
    print("\nscenario                     strict   graded  engagement  facing(stage/onset)")
    print("---------------------------  -------  ------  ----------  -------------------")
    for scenario, item in report["per_scenario"].items():
        verdict = "PASS" if item["passed"] else "FAIL"
        engagement = "n/a" if item["engagement_factor"] is None else f"{item['engagement_factor']:.3f}"
        facing = item["officer_facing_ego_deg"]
        facing_text = f"{facing['staging']}/{facing['gesture_onset']}"
        print(f"{scenario:27s}  {verdict:7s}  {item['graded_credit']:6.3f}  {engagement:>10s}  {facing_text}")
        if not item["passed"]:
            diagnosis = item["diagnosis"]
            print(f"  -> {diagnosis['strict_verdict']} {diagnosis['strict_criterion']}: "
                  f"{diagnosis['strict_reason']}")
            print(f"     final_stopline={diagnosis['final_ego_to_stopline_m']} "
                  f"min_hazard={diagnosis['min_distance_to_hazard_m']} "
                  f"min_officer={diagnosis['min_distance_to_officer_m']}")
            print(f"     telemetry={diagnosis['strict_telemetry_json']}")
    for scenario, reason in report["masked"].items():
        print(f"{scenario:27s}  MASKED   —       —  {reason}")
    for warning in report.get("warnings", ()):
        print(f"WARNING: {warning}")
    print(f"\npassed {report['passed_n']}/{report['feasible_n']} feasible scenarios")
    print(f"report -> {path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--town", required=True, help="CARLA town, e.g. Town05")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--scenarios", help="comma-separated scenario names (default: the full registered suite)")
    parser.add_argument("--out", default=str(ROOT / "outputs" / "calibration"), help="calibration output root")
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--episode-timeout", type=float, default=300.0)
    parser.add_argument("--debug", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        town = _normalise_town(args.town)
        stations, all_masked = _load_town_inputs(town)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    scenarios = (
        [item.strip() for item in args.scenarios.split(",") if item.strip()]
        if args.scenarios else list(benchmark.ALL_SCENARIOS)
    )
    unknown = [scenario for scenario in scenarios if scenario not in benchmark.ALL_SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario(s): {unknown}")
    missing_stations = find_unusable_stations(scenarios, stations, all_masked)
    if missing_stations:
        parser.error(
            "feasible scenario(s) missing or degenerate station entries: "
            f"{missing_stations}"
        )

    masked = {scenario: all_masked[scenario] for scenario in scenarios if scenario in all_masked}
    feasible = [scenario for scenario in scenarios if scenario not in masked]
    town_dir = Path(args.out).resolve() / town
    town_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    episode_root = town_dir / f"episodes_{run_stamp}"
    episode_root.mkdir(parents=True, exist_ok=True)

    run_args = argparse.Namespace(
        town=town,
        host=args.host,
        port=args.port,
        fps=args.fps,
        weather=None,
        weather_params=None,
        debug=args.debug,
        episode_timeout=args.episode_timeout,
    )
    results: dict[str, Optional[Mapping[str, Any]]] = {}
    print(f"MARSHAL oracle calibration | town={town} | feasible={len(feasible)} | masked={len(masked)}")
    for index, scenario in enumerate(feasible, 1):
        print(f"  [{index:2d}/{len(feasible)}] {scenario} ...", flush=True)
        results[scenario] = benchmark._run_episode("oracle", scenario, run_args, str(episode_root))

    report = build_report(town, results, masked)
    report_path = town_dir / f"calibration_{town.lower()}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _print_report(report, report_path)
    return 0 if report["passed_n"] == report["feasible_n"] else 1


if __name__ == "__main__":
    sys.exit(main())
