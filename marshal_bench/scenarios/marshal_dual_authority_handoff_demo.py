"""MARSHAL scenario - dual-authority handoff (conflict cell).

Two authorized controllers govern adjacent zones on the same approach: a
construction flagger signals SLOW in the first zone, and a police officer
signals STOP at the junction beyond it. Unlike ``conflicting_authorities``
(where the two directives contradict and the higher rank wins), here the
directives do NOT contradict - each governs its own zone. A compliant agent
must obey the flagger's SLOW through zone 1 and then the officer's STOP at the
junction, rather than averaging the two into a rolling creep.

The terminal directive is the officer's STOP, so the episode is scored as a
STOP (the standard scorer): a model that "averages" SLOW and STOP and keeps
creeping through never satisfies the full-stop check.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.scene_actors import spawn_flagger
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger(
    "marshal_bench.scenarios.marshal_dual_authority_handoff_demo")

_SCENARIO_NAME = "marshal_dual_authority_handoff"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    """Spawn the first-zone flagger nearer than the junction officer, so the
    ego transits the flagger's SLOW zone before reaching the officer's STOP."""
    sa = config.get("second_authority") or {}
    return spawn_flagger(
        world,
        ego_transform,
        distance=float(sa.get("distance", 16.0)),
        lateral=float(sa.get("lateral", -2.4)),
    )


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    sa = config.get("second_authority") or {}
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "Zone handoff: a construction flagger signals SLOW in the near "
            "zone, then a police officer signals STOP at the junction. Obey "
            "each in its own zone - SLOW through, then a full STOP at the "
            "officer - not an averaged creep."
        ),
        first_zone_authority=sa.get("authority_type"),
        first_zone_gesture=sa.get("gesture"),
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
