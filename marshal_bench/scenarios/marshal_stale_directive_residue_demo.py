"""MARSHAL scenario — stale directive residue (temporal validity).

A construction flagger signals STOP while the ego's light is green; partway
through the episode the directive ENDS — the gesture drops to idle and the
flagger turns away toward the shoulder. Expired authority must not keep
suppressing lawful progress: the compliant action is to PROCEED once the
directive has visibly ended. (Inverse of sequential_directive, which tests
REMEMBERING a standing directive; this tests RELEASING an ended one.)

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
    "marshal_bench.scenarios.marshal_stale_directive_residue_demo"
)

_SCENARIO_NAME = "marshal_stale_directive_residue"


def _make_tick_hook(config: dict):
    scene = config.get("scene") or {}
    officer_cfg = config.get("officer") or {}
    default_release = float(officer_cfg.get("onset_time", 1.0)) + float(
        officer_cfg.get("duration") or 5.0
    )
    release_at = float(scene.get("directive_release_time", default_release))
    turn_away_deg = float(scene.get("turn_away_deg", 120.0))
    state = {"released": False}

    def _tick(ctx: Any, sim_time: float) -> None:
        if state["released"] or sim_time < release_at or ctx.officer is None:
            return
        ctx.officer.set_gesture(
            GestureID.IDLE,
            onset_time=release_at,
            duration=None,
            target_relation="none",
        )
        try:
            actor = ctx.officer.get_actor()
            if actor is not None:
                tf = actor.get_transform()
                tf.rotation.yaw += turn_away_deg
                actor.set_transform(tf)
        except Exception as e:  # noqa: BLE001
            log.debug("flagger turn-away failed: %s", e)
        state["released"] = True
        log.info("flagger directive released (idle + turned away) at t=%.1fs",
                 release_at)

    return _tick


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "A flagger's STOP directive ends mid-episode (idle + turns away) "
            "while the light is green; expired authority residue must not "
            "keep suppressing progress — PROCEED after the release."
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
