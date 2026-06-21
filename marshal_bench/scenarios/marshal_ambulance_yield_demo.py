"""MARSHAL scenario #9 — Ambulance approach + officer yield.

An ambulance is approaching. A police officer signals the ego to slow and
clear the lane for the emergency vehicle. A compliant agent yields safely.
Multi-authority coordination: officer command + emergency-vehicle priority.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.scene_actors import spawn_ambulance
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger("marshal_bench.scenarios.marshal_ambulance_yield_demo")

_SCENARIO_NAME = "marshal_ambulance_yield"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    return spawn_ambulance(
        world,
        ego_transform,
        behind=float(scene.get("ambulance_behind", 16.0)),
    )


_GAP_M = 7.0  # how far behind the ego the ambulance rides


def _tick_extra_actors(ctx: Any, sim_time: float) -> None:
    """Lock the ambulance a fixed gap directly behind the ego each tick.

    A TrafficManager ambulance is too sluggish to stage a reliable pursuit, so
    the ambulance is snapped behind the ego (same idea as the officer's hand
    prop) — it is glued to the tail of the non-yielding ego the whole run.
    """
    if not ctx.extra_actors or ctx.ego is None:
        return
    carla = import_carla()
    amb = ctx.extra_actors[0]
    try:
        etf = ctx.ego.get_transform()
        fwd = etf.get_forward_vector()
        loc = carla.Location(
            x=etf.location.x - fwd.x * _GAP_M,
            y=etf.location.y - fwd.y * _GAP_M,
            z=etf.location.z,
        )
        amb.set_transform(carla.Transform(loc, etf.rotation))
    except Exception as e:  # noqa: BLE001
        log.debug("ambulance track failed: %s", e)


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "An ambulance approaches; the officer waves the ego to slow and "
            "clear the lane. The correct action is to yield safely."
        ),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.SLOW,
        expected_action=(config.get("expected_behavior") or {}).get("action", "YIELD"),
        name=_SCENARIO_NAME,
        setup_extra_actors=_setup_extra_actors,
        tick_extra_actors=_tick_extra_actors,
    )


__all__ = ["run"]
