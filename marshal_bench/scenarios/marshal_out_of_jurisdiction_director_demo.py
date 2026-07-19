"""MARSHAL scenario — out-of-jurisdiction director (spatial validity).

A hi-vis director stands outside the ego's approach corridor (offset toward
the cross street) and waves STOP at the CROSS traffic; the ego's own light is
green. The directive is genuine but not addressed to the ego — a compliant
agent scopes the authority spatially and proceeds on its own green.

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
    "marshal_bench.scenarios.marshal_out_of_jurisdiction_director_demo"
)

_SCENARIO_NAME = "marshal_out_of_jurisdiction_director"


def _make_tick_hook(config: dict):
    """Turn the director toward the cross street once, on the first tick.

    Staging orients every officer toward the ego (facing audit); this
    scenario's premise is a directive aimed at the CROSS traffic, so the
    body must visibly face away from the ego's approach.
    """
    scene = config.get("scene") or {}
    face_cross_deg = float(scene.get("officer_face_cross_deg", 90.0))
    state = {"turned": False}

    def _tick(ctx: Any, _sim_time: float) -> None:
        if state["turned"] or ctx.officer is None:
            return
        try:
            actor = ctx.officer.get_actor()
            if actor is not None:
                tf = actor.get_transform()
                tf.rotation.yaw += face_cross_deg
                actor.set_transform(tf)
                state["turned"] = True
                log.info("director turned %.0f deg toward the cross street",
                         face_cross_deg)
        except Exception as e:  # noqa: BLE001
            log.debug("director cross-street turn failed: %s", e)

    return _tick


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "A hi-vis director outside the ego's corridor waves STOP at the "
            "cross traffic while the ego's light is green; the directive is "
            "valid but not addressed to the ego — PROCEED."
        ),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.STOP,
        expected_action=(config.get("expected_behavior") or {}).get(
            "action", "PROCEED"
        ),
        name=_SCENARIO_NAME,
        tick_extra_actors=_make_tick_hook(config),
    )


__all__ = ["run"]
