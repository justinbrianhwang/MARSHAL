"""MARSHAL demo: traffic light disabled, officer manually controls the intersection.

Scenario summary (Prompt.txt Step 6.3)
--------------------------------------
* The signal at the intersection is **Off** (simulating a broken / dark
  signal). Every traffic light in the junction is set to ``Off`` and frozen
  via :func:`set_intersection_lights`.
* A police officer takes manual control of the junction; the configured
  gesture (default **PROCEED**) tells the ego what to do.

Expected oracle behaviour
-------------------------
An authority-aware policy should follow the officer's command. The vanilla
TrafficManager autopilot treats an Off signal as an uncontrolled intersection
and falls back to its own right-of-way heuristics — so the run still exposes
the missing authority channel, just without a contradicting light.

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
    set_intersection_lights,
    set_traffic_light_state,
)

log = logging.getLogger("marshal_bench.scenarios.marshal_signal_officer_control_demo")

_SCENARIO_NAME = "marshal_signal_officer_control"


def _setup_traffic_light(world: Any, ego: Any, config: dict) -> Any:
    """Disable every light in the junction the ego is approaching.

    We first locate the ego's affecting traffic light to find the junction
    centre, then call :func:`set_intersection_lights` to turn every sibling
    light Off (frozen if the build supports it). The primary light is still
    returned so callers can introspect / release it later.
    """
    tl_cfg = config.get("traffic_light") or {}
    state = tl_cfg.get("state", "Off")
    freeze = bool(tl_cfg.get("freeze", True))

    primary = find_relevant_traffic_light(world, ego, distance_threshold=80.0)
    if primary is None:
        log.warning(
            "No traffic light found within 80 m of ego — scenario will run "
            "without disabling any signal."
        )
        return None

    # Use the primary light's location as the intersection centre.
    try:
        centre = primary.get_transform().location
    except Exception:
        centre = None

    affected = 0
    if centre is not None:
        affected = set_intersection_lights(world, centre, state, freeze=freeze)
    if affected == 0:
        # Belt-and-braces: at least pin the primary light.
        set_traffic_light_state(primary, state, freeze=freeze)
        affected = 1

    log.info(
        "Disabled %d traffic light(s) around junction at id=%s (state=%s, freeze=%s)",
        affected,
        getattr(primary, "id", "?"),
        state,
        freeze,
    )
    return primary


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    """Run the signal-off + officer-controlled episode.

    Parameters
    ----------
    client:
        Connected :class:`carla.Client`.
    config:
        Parsed YAML dict — see ``marshal_bench/configs/demo_signal_off.yaml``.
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
            "Signal is Off; officer is the sole authority. Autopilot has no "
            "channel to perceive the officer."
        ),
    )

    expected_action_default = "PROCEED"
    expected_action = (config.get("expected_behavior") or {}).get("action", expected_action_default)

    gesture_name = str((config.get("officer") or {}).get("gesture", "PROCEED")).upper()
    try:
        expected_gesture = GestureID[gesture_name]
    except KeyError:
        expected_gesture = GestureID.PROCEED

    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=expected_gesture,
        expected_action=expected_action,
        setup_traffic_light=_setup_traffic_light,
        name=_SCENARIO_NAME,
    )


__all__ = ["run"]
