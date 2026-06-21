"""MARSHAL demo: red traffic light + officer PROCEED gesture.

Scenario summary (Prompt.txt Step 6.2)
--------------------------------------
* Ego approaches a signalised intersection.
* Traffic light is **Red** (frozen).
* A police officer gives the **PROCEED** gesture targeted at the ego.

Expected oracle behaviour
-------------------------
A human driver — or an authority-aware policy — should override the red light
and proceed when the path is clear. The vanilla TrafficManager autopilot will
instead obey the red signal and remain stopped, exposing exactly the
authority-arbitration gap that MARSHAL probes.

Public entrypoint
-----------------
``run(client, config, logger) -> dict`` returns the per-episode summary.
"""

from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401  (lazy carla)
from marshal_bench.utils.logging_utils import EpisodeLogger
from marshal_bench.utils.traffic_light_utils import (
    find_relevant_traffic_light,
    set_traffic_light_state,
)

log = logging.getLogger("marshal_bench.scenarios.marshal_red_proceed_demo")

_SCENARIO_NAME = "marshal_red_proceed"


def _setup_traffic_light(world: Any, ego: Any, config: dict) -> Any:
    """Locate the TL most relevant to ego and pin it to Red."""
    tl_cfg = config.get("traffic_light") or {}
    state = tl_cfg.get("state", "Red")
    freeze = bool(tl_cfg.get("freeze", True))

    light = find_relevant_traffic_light(world, ego, distance_threshold=80.0)
    if light is None:
        log.warning(
            "No traffic light found within 80 m of ego — running scenario "
            "without a pinned signal."
        )
        return None
    set_traffic_light_state(light, state, freeze=freeze)
    log.info(
        "Pinned traffic light id=%s to %s (freeze=%s)",
        getattr(light, "id", "?"),
        state,
        freeze,
    )
    return light


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    """Run the red-light + PROCEED-gesture episode.

    Parameters
    ----------
    client:
        Connected :class:`carla.Client`.
    config:
        Parsed YAML dict — see ``marshal_bench/configs/demo_red_proceed.yaml``.
    logger:
        :class:`marshal_bench.utils.logging_utils.EpisodeLogger` already bound to
        the episode's output directory.
    """
    import_carla()

    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "Autopilot is light-aware but officer-blind; ego is EXPECTED to "
            "obey the red signal and ignore the PROCEED gesture under vanilla "
            "TrafficManager."
        ),
    )

    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.PROCEED,
        expected_action=(config.get("expected_behavior") or {}).get("action", "PROCEED"),
        setup_traffic_light=_setup_traffic_light,
        name=_SCENARIO_NAME,
    )


__all__ = ["run"]
