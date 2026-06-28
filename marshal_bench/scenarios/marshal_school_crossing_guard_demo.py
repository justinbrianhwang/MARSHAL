"""MARSHAL scenario: school crossing guard authority + VRU.

A school crossing guard holds a STOP sign while pedestrians cross the ego
lane under a green light. The correct action is to STOP for the crossing.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.scene_actors import spawn_crossing_pedestrian
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger("marshal_bench.scenarios.marshal_school_crossing_guard_demo")

_SCENARIO_NAME = "school_crossing_guard"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    count = max(1, min(2, int(scene.get("pedestrian_count", 2))))
    distance = float(scene.get("pedestrian_distance", 18.0))
    spacing = float(scene.get("pedestrian_spacing", 2.0))
    from_side = str(scene.get("pedestrian_from_side", "right"))

    actors = []
    for i in range(count):
        actors.extend(
            spawn_crossing_pedestrian(
                world,
                ego_transform,
                distance=distance + spacing * i,
                from_side=from_side,
            )
        )
    return actors


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "A school crossing guard holds STOP while pedestrians cross under "
            "a green light; the guard's authority and VRU safety require STOP."
        ),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.STOP,
        expected_action=(config.get("expected_behavior") or {}).get("action", "STOP"),
        name=_SCENARIO_NAME,
        setup_extra_actors=_setup_extra_actors,
    )


__all__ = ["run"]
