#!/usr/bin/env python
"""CLI entrypoint for the MARSHAL officer-control demo scenarios.

Implements Step 10 of Prompt.txt. Loads one of the three demo configs and
hands off to the matching ``marshal_bench.scenarios.*`` module.

Examples
--------
::

    python scripts/run_marshal_officer_demo.py \\
        --host 127.0.0.1 --port 2000 \\
        --scenario green_stop \\
        --config marshal_bench/configs/demo_green_stop.yaml \\
        --debug

    python scripts/run_marshal_officer_demo.py --scenario red_proceed
    python scripts/run_marshal_officer_demo.py --scenario signal_off --town Town05

Output
------
Each run writes to ``<--out>/<episode_id>/`` (default ``outputs/marshal_runs``)
via :class:`marshal_bench.utils.logging_utils.EpisodeLogger`, dropping at minimum
``metadata.json``, ``events.json``, and ``metrics.csv``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import traceback
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Make ``marshal_bench.*`` importable regardless of cwd.
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# Scenario name -> (module path, default config relative to repo root).
# The full MARSHAL set — 9 authority-conflict scenarios.
_SCENARIO_MAP: dict[str, tuple[str, str]] = {
    "green_stop": (
        "marshal_bench.scenarios.marshal_green_stop_demo",
        "marshal_bench/configs/demo_green_stop.yaml",
    ),
    "red_proceed": (
        "marshal_bench.scenarios.marshal_red_proceed_demo",
        "marshal_bench/configs/demo_red_proceed.yaml",
    ),
    "signal_off": (
        "marshal_bench.scenarios.marshal_signal_officer_control_demo",
        "marshal_bench/configs/demo_signal_off.yaml",
    ),
    "crash_detour": (
        "marshal_bench.scenarios.marshal_crash_detour_demo",
        "marshal_bench/configs/demo_crash_detour.yaml",
    ),
    "fallen_person": (
        "marshal_bench.scenarios.marshal_fallen_person_demo",
        "marshal_bench/configs/demo_fallen_person.yaml",
    ),
    "unauthorized_go": (
        "marshal_bench.scenarios.marshal_unauthorized_go_demo",
        "marshal_bench/configs/demo_unauthorized_go.yaml",
    ),
    "adjacent_lane": (
        "marshal_bench.scenarios.marshal_adjacent_lane_demo",
        "marshal_bench/configs/demo_adjacent_lane.yaml",
    ),
    "flagger_control": (
        "marshal_bench.scenarios.marshal_flagger_control_demo",
        "marshal_bench/configs/demo_flagger_control.yaml",
    ),
    "ambulance_yield": (
        "marshal_bench.scenarios.marshal_ambulance_yield_demo",
        "marshal_bench/configs/demo_ambulance_yield.yaml",
    ),
    # High-level reasoning scenarios (LLM-required tier).
    "occluded_officer": (
        "marshal_bench.scenarios.marshal_occluded_officer_demo",
        "marshal_bench/configs/demo_occluded_officer.yaml",
    ),
    "conflicting_authorities": (
        "marshal_bench.scenarios.marshal_conflicting_authorities_demo",
        "marshal_bench/configs/demo_conflicting_authorities.yaml",
    ),
    "sequential_directive": (
        "marshal_bench.scenarios.marshal_sequential_directive_demo",
        "marshal_bench/configs/demo_sequential_directive.yaml",
    ),
    "rule_hierarchy": (
        "marshal_bench.scenarios.marshal_rule_hierarchy_demo",
        "marshal_bench/configs/demo_rule_hierarchy.yaml",
    ),
    "ambiguous_gesture": (
        "marshal_bench.scenarios.marshal_ambiguous_gesture_demo",
        "marshal_bench/configs/demo_ambiguous_gesture.yaml",
    ),
    # Expansion scenarios (21-scenario set) — broaden contextual authority,
    # authority verification, and temporal axes.
    "civilian_warning_accident": (
        "marshal_bench.scenarios.marshal_civilian_warning_accident_demo",
        "marshal_bench/configs/demo_civilian_warning_accident.yaml",
    ),
    "emergency_scene_blocking": (
        "marshal_bench.scenarios.marshal_emergency_scene_blocking_demo",
        "marshal_bench/configs/demo_emergency_scene_blocking.yaml",
    ),
    "two_civilians_disagree": (
        "marshal_bench.scenarios.marshal_two_civilians_disagree_demo",
        "marshal_bench/configs/demo_two_civilians_disagree.yaml",
    ),
    "flagger_slow_then_stop": (
        "marshal_bench.scenarios.marshal_flagger_slow_then_stop_demo",
        "marshal_bench/configs/demo_flagger_slow_then_stop.yaml",
    ),
    "school_crossing_guard": (
        "marshal_bench.scenarios.marshal_school_crossing_guard_demo",
        "marshal_bench/configs/demo_school_crossing_guard.yaml",
    ),
    "fake_vest_director": (
        "marshal_bench.scenarios.marshal_fake_vest_director_demo",
        "marshal_bench/configs/demo_fake_vest_director.yaml",
    ),
    "barricade_self_detour": (
        "marshal_bench.scenarios.marshal_barricade_self_detour_demo",
        "marshal_bench/configs/demo_barricade_self_detour.yaml",
    ),
    "stale_directive_residue": (
        "marshal_bench.scenarios.marshal_stale_directive_residue_demo",
        "marshal_bench/configs/demo_stale_directive_residue.yaml",
    ),
    "out_of_jurisdiction_director": (
        "marshal_bench.scenarios.marshal_out_of_jurisdiction_director_demo",
        "marshal_bench/configs/demo_out_of_jurisdiction_director.yaml",
    ),
    "night_signal_officer_conflict": (
        "marshal_bench.scenarios.marshal_night_signal_officer_conflict_demo",
        "marshal_bench/configs/demo_night_signal_officer_conflict.yaml",
    ),
    "dual_authority_handoff": (
        "marshal_bench.scenarios.marshal_dual_authority_handoff_demo",
        "marshal_bench/configs/demo_dual_authority_handoff.yaml",
    ),
}


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    from marshal_bench.utils.conditions import parse_weather_params

    p = argparse.ArgumentParser(
        description="Run a MARSHAL officer-control demo scenario.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host", default="127.0.0.1", help="CARLA server host")
    p.add_argument("--port", type=int, default=2000, help="CARLA server RPC port")
    p.add_argument(
        "--scenario",
        choices=sorted(_SCENARIO_MAP),
        default="green_stop",
        help="Which demo to run.",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Path to YAML config. Defaults to the per-scenario config under marshal_bench/configs/.",
    )
    p.add_argument(
        "--town",
        default=None,
        help="Override the town defined in the YAML config (e.g. Town03).",
    )
    p.add_argument(
        "--controller",
        default=None,
        help="Agent under test: 'baseline' (TM autopilot, default), 'oracle', "
        "or a registered E2E/VLM adapter name.",
    )
    p.add_argument(
        "--out",
        default=os.path.join(_REPO_ROOT, "outputs", "marshal_runs"),
        help="Output root directory for per-episode logs.",
    )
    p.add_argument("--fps", type=float, default=20.0, help="Simulation fixed-delta FPS.")
    p.add_argument("--weather", default=None,
                   help="CARLA WeatherParameters preset name.")
    p.add_argument("--weather-params", type=parse_weather_params, default=None,
                   metavar="K=V,K=V",
                   help="Float weather parameters applied over --weather.")
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Override the per-scenario timeout in seconds.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Verbose logging + force officer.use_debug_visuals=True.",
    )
    return p


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def _load_yaml(path: str) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "PyYAML is required to load MARSHAL configs — `pip install pyyaml`."
        ) from e
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"Config {path!r} did not deserialise to a dict.")
    return data


def _resolve_config_path(scenario: str, override: Optional[str]) -> str:
    if override:
        return os.path.abspath(override)
    rel = _SCENARIO_MAP[scenario][1]
    return os.path.abspath(os.path.join(_REPO_ROOT, rel))


def _apply_cli_overrides(config: dict, args: argparse.Namespace) -> dict:
    """Apply CLI flags on top of the YAML config (CLI wins)."""
    from marshal_bench.utils.conditions import merge_condition_config

    if args.town:
        config["town"] = args.town
    if args.controller is not None:
        config["controller"] = args.controller
    if args.fps is not None:
        config["fps"] = float(args.fps)
    if args.timeout is not None:
        config["timeout_sec"] = float(args.timeout)
    merge_condition_config(config, args.weather, args.weather_params)
    if args.debug:
        officer = dict(config.get("officer") or {})
        officer["use_debug_visuals"] = True
        config["officer"] = officer
    return config


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    log_level = logging.DEBUG if args.debug else logging.INFO

    # The logging_utils module owns root-logger configuration.
    try:
        from marshal_bench.utils.logging_utils import EpisodeLogger, setup_root_logger
    except Exception as e:
        print(f"FATAL: could not import marshal_bench.utils.logging_utils: {e}", file=sys.stderr)
        return 2

    setup_root_logger(level=log_level)
    log = logging.getLogger("marshal_bench.scripts.run_marshal_officer_demo")

    # Resolve & load config.
    config_path = _resolve_config_path(args.scenario, args.config)
    if not os.path.isfile(config_path):
        log.error("Config not found: %s", config_path)
        return 2
    try:
        config = _load_yaml(config_path)
    except Exception as e:
        log.error("Failed to load config %s: %s", config_path, e)
        return 2

    config = _apply_cli_overrides(config, args)
    episode_id = str(config.get("episode_id") or f"marshal_{args.scenario}_run")
    config.setdefault("episode_id", episode_id)

    log.info("Loaded config: %s", config_path)
    log.debug("Effective config: %s", json.dumps(_jsonable(config), indent=2, default=str))

    # Resolve scenario module.
    module_path = _SCENARIO_MAP[args.scenario][0]
    try:
        scenario_module = importlib.import_module(module_path)
    except Exception as e:
        log.error("Could not import scenario module %s: %s", module_path, e)
        return 2
    if not hasattr(scenario_module, "run"):
        log.error("Scenario module %s does not expose a run() function.", module_path)
        return 2

    # Build the per-episode logger.
    logger = EpisodeLogger(episode_id, output_root=args.out)
    logger.save_metadata(
        {
            "episode_id": episode_id,
            "scenario": args.scenario,
            "config_path": config_path,
            "host": args.host,
            "port": args.port,
            "fps": args.fps,
            "debug": bool(args.debug),
            "config": _jsonable(config),
        }
    )

    # Connect to CARLA — lazy so --help works without an install.
    try:
        from marshal_bench.utils.carla_api_compat import import_carla
        carla = import_carla()
    except Exception as e:
        log.error("Failed to import carla: %s", e)
        logger.log_event("fatal", stage="import_carla", error=str(e))
        try:
            logger.close()
        except Exception:
            pass
        return 3

    try:
        client = carla.Client(args.host, args.port)
        client.set_timeout(20.0)
        # Touching get_server_version forces an RPC round-trip.
        try:
            server_version = client.get_server_version()
            log.info("Connected to CARLA server version %s", server_version)
            logger.log_event("carla_connected", server_version=server_version)
        except Exception as e:
            log.warning("Connected but get_server_version failed: %s", e)
    except Exception as e:
        log.error("Could not connect to CARLA at %s:%s — %s", args.host, args.port, e)
        logger.log_event("fatal", stage="connect", error=str(e))
        try:
            logger.close()
        except Exception:
            pass
        return 3

    # Run the scenario.
    result: dict = {}
    exit_code = 0
    try:
        result = scenario_module.run(client, config, logger)
        logger.log_event("scenario_result", **_jsonable(result))
        log.info(
            "Scenario %s finished: terminated_reason=%s",
            args.scenario,
            result.get("terminated_reason"),
        )
    except KeyboardInterrupt:
        log.warning("Interrupted by user (KeyboardInterrupt) — cleaning up.")
        logger.log_event("interrupted", reason="KeyboardInterrupt")
        exit_code = 130
    except Exception as e:
        log.error("Scenario failed: %s\n%s", e, traceback.format_exc())
        logger.log_event("fatal", stage="run", error=str(e), traceback=traceback.format_exc())
        exit_code = 1
    finally:
        # Always flush logs and persist the final result snapshot.
        try:
            if result:
                logger.save_metadata({"result": _jsonable(result)}, name="result.json")
        except Exception as e:
            log.debug("Failed to save result.json: %s", e)
        try:
            logger.close()
        except Exception as e:
            log.debug("logger.close failed: %s", e)

    return exit_code


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def _jsonable(obj: Any) -> Any:
    """Best-effort conversion of arbitrary objects to JSON-serialisable form."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    # Dataclass-ish or carla types — fall back to repr.
    if hasattr(obj, "as_dict"):
        try:
            return _jsonable(obj.as_dict())
        except Exception:
            pass
    if hasattr(obj, "to_json"):
        try:
            return _jsonable(obj.to_json())
        except Exception:
            pass
    try:
        return repr(obj)
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
