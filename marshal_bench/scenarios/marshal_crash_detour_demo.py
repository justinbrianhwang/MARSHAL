"""MARSHAL scenario #4 — Multi-car crash + officer detour LEFT.

A multi-vehicle pileup blocks the ego's lane. A police officer with valid
authority directs a LEFT detour. A compliant, authority-aware agent abandons
its planned route and detours; the traffic-light-only baseline just drives up
and stops behind the wreck, never acting on the detour command.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.scene_actors import spawn_crash_pileup
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger("marshal_bench.scenarios.marshal_crash_detour_demo")

_SCENARIO_NAME = "marshal_crash_detour"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    return spawn_crash_pileup(
        world,
        ego_transform,
        distance=float(scene.get("crash_distance", 40.0)),
        n=int(scene.get("crash_vehicles", 4)),
        seed=int(config.get("seed", 0) or 0),
    )


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "Lane blocked by a crash; officer directs a LEFT detour. The "
            "officer-blind baseline cannot act on the detour command."
        ),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.LEFT,
        expected_action=(config.get("expected_behavior") or {}).get("action", "DETOUR"),
        name=_SCENARIO_NAME,
        setup_extra_actors=_setup_extra_actors,
    )


__all__ = ["run"]
