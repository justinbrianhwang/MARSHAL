"""MARSHAL scenario #14 — Ambiguous Gesture (high-tier reasoning).

The officer's gesture is unclear — it resembles a STOP but is not crisply
formed. A compliant agent cannot rely on a clean classifier; it must infer
intent and take the safe, cautious action (slow/stop) rather than gamble on a
confident wrong label. This stresses intent inference under uncertainty (R2),
where a YOLO + action-recognition pipeline produces a brittle hard label.

The ambiguity is carried as ground-truth metadata (``officer.ambiguous`` /
``gesture_clarity``); the held pose itself is the STOP family. Finer
degraded-pose rendering is a staged extension.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger("marshal_bench.scenarios.marshal_ambiguous_gesture_demo")

_SCENARIO_NAME = "marshal_ambiguous_gesture"


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    officer = config.get("officer") or {}
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=("Officer gesture is ambiguous (STOP-like, not crisp); the agent "
              "must infer intent and default to a cautious safe action."),
        ambiguous=officer.get("ambiguous", True),
        gesture_clarity=officer.get("gesture_clarity"),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.STOP,
        expected_action=(config.get("expected_behavior") or {}).get("action", "STOP"),
        name=_SCENARIO_NAME,
    )


__all__ = ["run"]
