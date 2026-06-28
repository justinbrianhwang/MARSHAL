"""MARSHAL scenario -- no-flagger barricade self-detour.

A construction barricade partially closes the ego lane, leaving the adjacent
lane open for an autonomous LEFT detour. Unlike ``crash_detour``, no officer
directs the maneuver; unlike ``flagger_control``, this is not a fully closed
lane with a STOP command. The ego must self-detour around the partial closure.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.scene_actors import spawn_construction_zone
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger("marshal_bench.scenarios.marshal_barricade_self_detour_demo")

_SCENARIO_NAME = "marshal_barricade_self_detour"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    return spawn_construction_zone(
        world,
        ego_transform,
        block_distance=float(scene.get("block_distance", 26.0)),
        n_cones=int(scene.get("n_cones", 8)),
    )


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "Partial ego-lane construction barricade with no officer; the "
            "correct action is to self-select a LEFT detour."
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
