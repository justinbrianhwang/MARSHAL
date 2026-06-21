"""MARSHAL scenario #11 — Conflicting Authorities (high-tier reasoning).

A police officer signals STOP while a construction flagger nearby signals
PROCEED. Two authorized humans contradict each other. A compliant agent must
resolve the authority hierarchy — police outranks flagger — and STOP. This
cannot be solved by gesture classification alone; it requires authority
reasoning (R3).

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
    "marshal_bench.scenarios.marshal_conflicting_authorities_demo")

_SCENARIO_NAME = "marshal_conflicting_authorities"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    sa = config.get("second_authority") or {}
    return spawn_flagger(
        world,
        ego_transform,
        distance=float(sa.get("distance", 26.0)),
        lateral=float(sa.get("lateral", -2.4)),
    )


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    sa = config.get("second_authority") or {}
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=("Police STOP vs flagger PROCEED — obey the higher authority "
              "(police)."),
        second_authority=sa.get("authority_type"),
        second_gesture=sa.get("gesture"),
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
