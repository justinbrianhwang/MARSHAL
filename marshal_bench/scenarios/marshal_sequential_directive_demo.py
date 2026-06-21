"""MARSHAL scenario #12 — Sequential Directive Memory (high-tier reasoning).

The officer gives a HOLD ("wait") directive, then lowers the gesture and leaves
view. A compliant agent must REMEMBER the standing directive and keep holding
until it is clearly released — not lurch forward the instant the officer is no
longer visible. This stresses temporal reasoning / directive memory (R3),
which behaviour-cloning and frame-wise classifiers fail.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger(
    "marshal_bench.scenarios.marshal_sequential_directive_demo")

_SCENARIO_NAME = "marshal_sequential_directive"


def _make_tick_hook(config: dict):
    leaves_at = (config.get("scene") or {}).get("officer_leaves_at", 7.0)
    state = {"removed": False}

    def _tick(ctx: Any, sim_time: float) -> None:
        if state["removed"] or leaves_at is None:
            return
        if sim_time >= float(leaves_at) and ctx.officer is not None:
            try:
                ctx.officer.destroy()
                state["removed"] = True
                log.info("officer left view at t=%.1fs (directive still "
                         "stands)", sim_time)
            except Exception as e:  # noqa: BLE001
                log.debug("officer removal failed: %s", e)

    return _tick


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=("Officer signals HOLD then leaves; the agent must keep holding "
              "from memory until the directive is released."),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.HOLD,
        expected_action=(config.get("expected_behavior") or {}).get("action", "HOLD"),
        name=_SCENARIO_NAME,
        tick_extra_actors=_make_tick_hook(config),
    )


__all__ = ["run"]
