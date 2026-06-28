"""Temporal flagger variant: SLOW-to-STOP vs static flagger_control STOP.

A construction flagger first signals SLOW, then switches to STOP. A compliant
agent must resolve the latest formal-authority directive and stop despite the
green traffic light.

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

log = logging.getLogger(
    "marshal_bench.scenarios.marshal_flagger_slow_then_stop_demo"
)

_SCENARIO_NAME = "flagger_slow_then_stop"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    return spawn_construction_zone(
        world,
        ego_transform,
        block_distance=float(scene.get("construction_block", 30.0)),
    )


def _make_tick_hook(config: dict):
    scene = config.get("scene") or {}
    switch_at = float(scene.get("switch_to_stop_time", 6.0))
    stop_duration = scene.get("stop_duration", 10.0)
    state = {"switched": False}

    def _tick(ctx: Any, sim_time: float) -> None:
        if state["switched"] or sim_time < switch_at or ctx.officer is None:
            return
        ctx.officer.set_gesture(
            GestureID.STOP,
            onset_time=switch_at,
            duration=stop_duration,
            target_relation="ego",
        )
        state["switched"] = True
        log.info("flagger switched from SLOW to STOP at t=%.1fs", switch_at)

    return _tick


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "Authorized construction flagger first signals SLOW, then switches "
            "to STOP; the terminal directive overrides the green light."
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
        tick_extra_actors=_make_tick_hook(config),
    )


__all__ = ["run"]
