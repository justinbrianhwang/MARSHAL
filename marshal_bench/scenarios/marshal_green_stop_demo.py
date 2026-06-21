"""MARSHAL demo: green traffic light + officer STOP gesture.

Scenario summary (Prompt.txt Step 6.1)
--------------------------------------
* Ego approaches a signalised intersection.
* Traffic light is **Green** (frozen).
* A police officer gives the **STOP** gesture targeted at the ego.

Expected oracle behaviour
-------------------------
A human driver — or an authority-aware policy — should stop *before* entering
the conflict zone. CARLA's TrafficManager autopilot, however, only obeys the
traffic light: it cannot perceive the officer. The benchmark therefore
*expects* the vanilla autopilot to roll past the gesture; this gap is the
research insight MARSHAL is built to measure, and the criteria modules log the
violation rather than treating the run as broken.

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

log = logging.getLogger("marshal_bench.scenarios.marshal_green_stop_demo")

_SCENARIO_NAME = "marshal_green_stop"


def _setup_traffic_light(world: Any, ego: Any, config: dict) -> Any:
    """Locate the TL most relevant to ego and pin it to Green."""
    tl_cfg = config.get("traffic_light") or {}
    state = tl_cfg.get("state", "Green")
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
    """Run the green-light + STOP-gesture episode.

    Parameters
    ----------
    client:
        Connected :class:`carla.Client`.
    config:
        Parsed YAML dict — see ``marshal_bench/configs/demo_green_stop.yaml``.
    logger:
        :class:`marshal_bench.utils.logging_utils.EpisodeLogger` already bound to the
        episode's output directory.
    """
    # Lazy import primarily so this module is import-safe without a CARLA
    # install (for unit / syntax tests).
    import_carla()

    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "Autopilot is light-aware but officer-blind; ego is EXPECTED to "
            "ignore the STOP gesture under vanilla TrafficManager."
        ),
    )

    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.STOP,
        expected_action=(config.get("expected_behavior") or {}).get("action", "STOP"),
        setup_traffic_light=_setup_traffic_light,
        name=_SCENARIO_NAME,
    )


__all__ = ["run"]
