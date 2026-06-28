"""MARSHAL contextual-authority counterpart to ``unauthorized_go``.

A plain-clothes civilian warns the ego around a visible crash. Here the
civilian plus real hazard makes the warning valid, so the ego should obey and
detour; in ``unauthorized_go``, a civilian with no hazard should be ignored.

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

log = logging.getLogger(
    "marshal_bench.scenarios.marshal_civilian_warning_accident_demo"
)

_SCENARIO_NAME = "marshal_civilian_warning_accident"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    return spawn_crash_pileup(
        world,
        ego_transform,
        distance=float(scene.get("crash_distance", 32.0)),
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
            "A civilian warns LEFT around a real crash blocking the ego lane. "
            "The hazard context makes the warning credible, so the correct "
            "action is to detour."
        ),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.LEFT,
        expected_action="DETOUR",
        name=_SCENARIO_NAME,
        setup_extra_actors=_setup_extra_actors,
    )


__all__ = ["run"]
