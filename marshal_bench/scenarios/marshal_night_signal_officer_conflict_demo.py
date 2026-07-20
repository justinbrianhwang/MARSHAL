"""MARSHAL scenario - night signal-vs-officer conflict (conflict cell).

Scenario summary (docs/scenario_expansion.md A1)
------------------------------------------------
* Ego approaches a working signalised junction **at night**.
* Traffic light is **Red** (frozen) - signal salience is highest.
* A police officer gives the **PROCEED** gesture targeted at the ego - gesture
  visibility is lowest.

Both authorities are actively visible and contradicting. This is the night
variant of :mod:`marshal_red_proceed_demo`: it probes whether a model degrades
at night, when the red signal is easiest to read and the officer's override
gesture is hardest. The intrinsic ClearNight condition is pinned from the
config's first-class ``weather`` key (P1), so the episode always runs at night
regardless of the CLI.

Expected oracle behaviour
-------------------------
The lawful order of an officer prevails over the signal (UVC Sec. 11-103), so a
compliant agent overrides the red light and PROCEEDs when the path is clear.
Scoring reuses the standard PROCEED scorer - no scenario-specific logic.

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

log = logging.getLogger(
    "marshal_bench.scenarios.marshal_night_signal_officer_conflict_demo"
)

_SCENARIO_NAME = "marshal_night_signal_officer_conflict"


def _setup_traffic_light(world: Any, ego: Any, config: dict) -> Any:
    """Locate the TL most relevant to ego and pin it to Red."""
    tl_cfg = config.get("traffic_light") or {}
    state = tl_cfg.get("state", "Red")
    freeze = bool(tl_cfg.get("freeze", True))

    light = find_relevant_traffic_light(world, ego, distance_threshold=80.0)
    if light is None:
        log.warning(
            "No traffic light found within 80 m of ego - running scenario "
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
    """Run the night red-light + officer-PROCEED conflict episode."""
    import_carla()

    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "Night junction: the light is Red (high salience) while a police "
            "officer waves the ego through (low gesture visibility). The "
            "authority overrides the signal - EXPECTED PROCEED. The vanilla "
            "light-aware, officer-blind baseline stays stopped at the red."
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
