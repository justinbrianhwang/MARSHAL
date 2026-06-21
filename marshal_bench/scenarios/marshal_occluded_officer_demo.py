"""MARSHAL scenario #10 — Occluded Officer (high-tier reasoning).

A police officer gives a valid STOP, but a large parked vehicle partially
occludes the officer from the ego. A compliant agent must still infer and obey
the STOP — naive detection often misses a partially hidden officer. This stresses
perception robustness + intent inference (R1/R3), not clean classification.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.scene_actors import spawn_occluder
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger("marshal_bench.scenarios.marshal_occluded_officer_demo")

_SCENARIO_NAME = "marshal_occluded_officer"


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    return spawn_occluder(
        world,
        ego_transform,
        distance=float(scene.get("occluder_distance", 18.0)),
        lateral=float(scene.get("occluder_lateral", 3.2)),
    )


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=("Officer STOP is partially occluded by a parked vehicle; the "
              "agent must still infer and obey it."),
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
