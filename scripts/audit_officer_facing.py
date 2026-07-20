#!/usr/bin/env python
"""Stage every feasible scenario and audit human directors' ego-facing angle."""
from __future__ import annotations

import argparse
import importlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_marshal_officer_demo as runner  # noqa: E402
from marshal_bench.scenarios._common import (  # noqa: E402
    STATION_ALIASES,
    ScenarioContext,
    build_officer,
    default_setup_traffic_light,
    ensure_town,
    facing_ego_deg,
    spawn_ego,
    teardown,
)
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402


CONFIGS = ROOT / "marshal_bench" / "configs"
DEFAULT_TOWNS = ("Town01", "Town03", "Town05")
DIRECTIVE_EXTRAS = {
    "conflicting_authorities": ("second_flagger",),
    "two_civilians_disagree": ("second_civilian",),
}
THRESHOLD_DEG = 45.0


def _town_paths(town: str) -> tuple[Path, Path]:
    key = town.lower()
    station = CONFIGS / ("stations.json" if key == "town03" else f"stations_{key}.json")
    return station, CONFIGS / f"feasibility_{key}.json"


def _inputs(town: str) -> tuple[dict[str, Any], dict[str, str]]:
    station_path, feasibility_path = _town_paths(town)
    stations = json.loads(station_path.read_text(encoding="utf-8"))["stations"]
    feasibility = json.loads(feasibility_path.read_text(encoding="utf-8"))
    masked = {
        name: str(item.get("reason") or "marked infeasible")
        for name, item in feasibility.items()
        if isinstance(item, dict) and item.get("feasible") is False
    }
    return stations, masked


def _config(scenario: str, town: str, station: dict[str, Any]) -> dict[str, Any]:
    config_path = ROOT / runner._SCENARIO_MAP[scenario][1]
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config["town"] = town
    config.setdefault("ego", {})["spawn_transform"] = dict(station)
    return config


def _record(label: str, actor: Any, ego_transform: Any) -> dict[str, Any]:
    try:
        transform = actor.get_transform()
    except Exception:
        transform = None
    angle = facing_ego_deg(transform, ego_transform)
    return {
        "director": label,
        "actor_id": getattr(actor, "id", None),
        "type_id": getattr(actor, "type_id", None),
        "facing_ego_deg": angle,
        "flagged": angle is None or angle > THRESHOLD_DEG,
    }


def stage_one(client: Any, town: str, scenario: str, station: dict[str, Any]) -> dict[str, Any]:
    """Run the production spawn helpers through scene-actor staging, then tear down."""
    ctx = ScenarioContext()
    try:
        config = _config(scenario, town, station)
        world = ensure_town(client, town)
        ctx.world = world
        ctx.ego, ego_transform = spawn_ego(world, dict(config.get("ego") or {}), seed=config.get("seed"))
        for _ in range(max(1, int(config.get("spawn_settle_ticks", 20)))):
            try:
                world.tick() if world.get_settings().synchronous_mode else world.wait_for_tick()
            except Exception:
                break
        ego_transform = ctx.ego.get_transform()
        ctx.traffic_light = default_setup_traffic_light(world, ctx.ego, config)
        officer_cfg = dict(config.get("officer") or {})
        directors = []
        if officer_cfg:
            ctx.officer = build_officer(world, ego_transform, officer_cfg)
            actor = ctx.officer.get_actor()
            if actor is None:
                raise RuntimeError("primary director failed to spawn")
            directors.append(_record("primary", actor, ego_transform))

        module = importlib.import_module(runner._SCENARIO_MAP[scenario][0])
        setup_extra = getattr(module, "_setup_extra_actors", None)
        if setup_extra is not None:
            extra = setup_extra(world, ctx.ego, ego_transform, ctx.officer, config)
            ctx.extra_actors = [actor for actor in (extra or []) if actor is not None]
        for label, actor in zip(DIRECTIVE_EXTRAS.get(scenario, ()), ctx.extra_actors):
            directors.append(_record(label, actor, ego_transform))
        return {
            "status": "staged",
            "directors": directors,
            "flagged": any(item["flagged"] for item in directors),
            "error": None,
        }
    except Exception as exc:  # staging defects must remain visible in the table
        return {"status": "error", "directors": [], "flagged": True, "error": repr(exc)}
    finally:
        teardown(ctx)


def _print(rows: list[dict[str, Any]]) -> None:
    print("town    scenario                     director          facing_ego_deg  result")
    print("------  ---------------------------  ----------------  --------------  ------")
    for row in rows:
        if row.get("masked_reason"):
            print(f"{row['town']:6s}  {row['scenario']:27s}  {'-':16s}  {'-':>14s}  MASKED")
            continue
        if row["status"] == "error":
            print(f"{row['town']:6s}  {row['scenario']:27s}  {'-':16s}  {'-':>14s}  ERROR")
            continue
        if not row["directors"]:
            print(f"{row['town']:6s}  {row['scenario']:27s}  {'none':16s}  {'-':>14s}  N/A")
        for item in row["directors"]:
            angle = item["facing_ego_deg"]
            text = "missing" if angle is None else f"{angle:.2f}"
            result = "FLAG" if item["flagged"] else "OK"
            print(f"{row['town']:6s}  {row['scenario']:27s}  {item['director']:16s}  {text:>14s}  {result}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--town", action="append", help="town to audit; repeatable")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default=str(ROOT / "outputs" / "audit"))
    args = parser.parse_args(argv)

    towns = args.town or [town for town in DEFAULT_TOWNS if all(path.is_file() for path in _town_paths(town))]
    carla = import_carla()
    client = carla.Client(args.host, args.port)
    client.set_timeout(args.timeout)
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for town in towns:
        stations, masked = _inputs(town)
        rows = []
        for scenario in runner._SCENARIO_MAP:
            if scenario in masked:
                row = {"town": town, "scenario": scenario, "status": "masked", "directors": [], "flagged": False, "masked_reason": masked[scenario]}
            else:
                # Same alias resolution as the runtime station lookup and the
                # calibration gate: expansion scenarios reuse a witness pose.
                station_key = STATION_ALIASES.get(scenario, scenario)
                station = stations.get(station_key)
                if station is None:
                    row = {"town": town, "scenario": scenario, "status": "missing_station", "directors": [], "flagged": True, "station_key": station_key}
                else:
                    row = {"town": town, "scenario": scenario, **stage_one(client, town, scenario, station)}
            rows.append(row)
        payload = {"town": town, "threshold_deg": THRESHOLD_DEG, "scenarios": rows}
        (out_dir / f"officer_facing_{town.lower()}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        all_rows.extend(rows)
    _print(all_rows)
    return 1 if any(row.get("flagged") for row in all_rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
