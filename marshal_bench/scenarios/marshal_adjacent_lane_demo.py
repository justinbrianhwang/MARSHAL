"""MARSHAL scenario #7 — Adjacent-lane officer command.

The light is Red. A police officer directs the vehicle in the lane *next* to
the ego (not the ego itself). A compliant agent attributes the gesture to the
correct target and keeps obeying its own red light. This is the
Target-Attribution-Accuracy (TAA) probe.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.scene_actors import spawn_adjacent_vehicle
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger("marshal_bench.scenarios.marshal_adjacent_lane_demo")

_SCENARIO_NAME = "marshal_adjacent_lane"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    return spawn_adjacent_vehicle(
        world,
        ego_transform,
        distance=float(scene.get("adjacent_distance", 26.0)),
        side=str(scene.get("adjacent_side", "right")),
    )


def _after_autopilot(ctx: Any, traffic_manager: Any, config: dict) -> None:
    """Keep the adjacent-lane target car staged in view.

    Handing this spawned vehicle to CARLA's TrafficManager with
    ``set_autopilot(True, tm.get_port())`` reproducibly aborts the Windows
    client process with 0xC0000409 in this Town03 setup. The attribution probe
    only needs the other-lane target to be visible, so leave it stationary.
    """
    if not ctx.extra_actors:
        return
    car = ctx.extra_actors[0]
    try:
        car.set_simulate_physics(False)
    except Exception as e:  # noqa: BLE001
        log.debug("adjacent car physics freeze failed: %s", e)
    log.info("adjacent car left stationary; TrafficManager handoff disabled")


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "Officer's gesture targets the adjacent lane, not the ego. The "
            "correct action is to recognise the mis-attribution and hold."
        ),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.RIGHT,
        expected_action=(config.get("expected_behavior") or {}).get("action", "STOP"),
        name=_SCENARIO_NAME,
        setup_extra_actors=_setup_extra_actors,
        setup_after_autopilot=_after_autopilot,
    )


__all__ = ["run"]
