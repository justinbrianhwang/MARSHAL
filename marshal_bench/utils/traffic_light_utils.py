"""Traffic-light helper utilities for MARSHAL scenarios.

Implements the Step 5 helpers from Prompt.txt: locating the relevant traffic
light for an ego vehicle, deterministically setting / freezing its state, and
applying a state to every light in an intersection. All CARLA access is
defensive — failures are logged at WARNING and surfaced as None / 0 return
values rather than raised, so that scenarios can degrade gracefully when the
installed CARLA build lacks a feature (e.g. ``TrafficLight.freeze``).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable, Optional, Union

from marshal_bench.utils.carla_api_compat import detect_capabilities, import_carla

log = logging.getLogger("marshal_bench.utils.traffic_light_utils")

StateLike = Union[str, Any]  # string name or carla.TrafficLightState
LocationLike = Union[Any, int]  # carla.Location or junction id (int)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _resolve_state(state: StateLike) -> Optional[Any]:
    """Coerce a string or enum into a ``carla.TrafficLightState``.

    Returns None if the value cannot be resolved.
    """
    carla = import_carla()
    TLState = getattr(carla, "TrafficLightState", None)
    if TLState is None:
        log.warning("carla.TrafficLightState not available in this CARLA build")
        return None

    if isinstance(state, TLState):
        return state

    if isinstance(state, str):
        key = state.strip().capitalize()
        if hasattr(TLState, key):
            return getattr(TLState, key)
        # Tolerate fully lower / upper case as well.
        for name in ("Red", "Yellow", "Green", "Off", "Unknown"):
            if state.strip().lower() == name.lower():
                return getattr(TLState, name)
        log.warning("Unknown traffic-light state string: %r", state)
        return None

    if isinstance(state, int):
        try:
            return TLState(state)
        except Exception:
            log.warning("Unknown traffic-light state int: %r", state)
            return None

    log.warning("Cannot resolve traffic-light state of type %s", type(state).__name__)
    return None


def _location_distance(a: Any, b: Any) -> float:
    """Euclidean distance between two carla.Location-like objects."""
    try:
        return float(a.distance(b))
    except Exception:
        # Fall back to manual computation if .distance is missing.
        try:
            dx = a.x - b.x
            dy = a.y - b.y
            dz = getattr(a, "z", 0.0) - getattr(b, "z", 0.0)
            return math.sqrt(dx * dx + dy * dy + dz * dz)
        except Exception:
            return float("inf")


def _ego_facing_dot(ego_transform: Any, target_location: Any) -> float:
    """Return dot product of ego forward vector and (target - ego) horizontal vector.

    A positive return value means the target lies roughly in front of the ego.
    """
    try:
        forward = ego_transform.get_forward_vector()
        ego_loc = ego_transform.location
        dx = target_location.x - ego_loc.x
        dy = target_location.y - ego_loc.y
        return forward.x * dx + forward.y * dy
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def find_relevant_traffic_light(
    world: Any,
    ego_vehicle: Any,
    distance_threshold: float = 80.0,
) -> Optional[Any]:
    """Return the traffic light most relevant to ``ego_vehicle``.

    First trusts ``ego_vehicle.get_traffic_light()`` (CARLA's own affecting-TL
    query). If that returns None, falls back to a manual scan: collect every
    ``traffic.traffic_light`` actor in the world, keep only those within
    ``distance_threshold`` metres that the ego is roughly facing (dot product
    of the ego forward vector with the vector-to-light is positive), and
    return the nearest of those. Returns None if no candidate qualifies.
    """
    if world is None or ego_vehicle is None:
        return None

    try:
        affecting = ego_vehicle.get_traffic_light()
        if affecting is not None:
            return affecting
    except Exception as e:
        log.debug("ego.get_traffic_light() failed: %s", e)

    try:
        ego_transform = ego_vehicle.get_transform()
        ego_location = ego_transform.location
    except Exception as e:
        log.warning("Could not read ego transform: %s", e)
        return None

    try:
        candidates = list(world.get_actors().filter("traffic.traffic_light*"))
    except Exception as e:
        log.warning("Could not enumerate traffic lights: %s", e)
        return None

    best = None
    best_dist = distance_threshold
    for tl in candidates:
        try:
            tl_loc = tl.get_transform().location
        except Exception:
            continue
        dist = _location_distance(ego_location, tl_loc)
        if dist > best_dist:
            continue
        if _ego_facing_dot(ego_transform, tl_loc) <= 0:
            continue
        best = tl
        best_dist = dist
    return best


def set_traffic_light_state(light: Any, state: StateLike, freeze: bool = True) -> None:
    """Set ``light`` to ``state`` and optionally freeze it.

    ``state`` may be a ``carla.TrafficLightState`` or a string
    ("Red"/"Yellow"/"Green"/"Off"/"Unknown"; case-insensitive).

    If ``freeze`` is True and the CARLA build does not expose
    ``TrafficLight.freeze``, a warning is logged once and the caller is
    expected to re-invoke this function each simulation tick to keep the
    state pinned.
    """
    if light is None:
        log.debug("set_traffic_light_state called with None light; skipping")
        return

    enum_state = _resolve_state(state)
    if enum_state is None:
        return

    try:
        light.set_state(enum_state)
    except Exception as e:
        log.warning("light.set_state(%s) failed: %s", enum_state, e)
        return

    if not freeze:
        return

    caps = detect_capabilities()
    if caps.has_traffic_light_freeze:
        try:
            light.freeze(True)
        except Exception as e:
            log.warning("light.freeze(True) failed: %s", e)
    else:
        log.warning(
            "TrafficLight.freeze not available in this CARLA build "
            "(version=%s); caller must re-apply set_traffic_light_state per tick.",
            caps.carla_version,
        )


def release_traffic_light(light: Any) -> None:
    """Unfreeze a previously frozen traffic light. Safe to call on None."""
    if light is None:
        return
    caps = detect_capabilities()
    if not caps.has_traffic_light_freeze:
        return
    try:
        light.freeze(False)
    except Exception as e:
        log.warning("light.freeze(False) failed: %s", e)


def get_traffic_light_state(light: Any) -> str:
    """Return the current state of ``light`` as a string (e.g. "Red").

    Returns "Unknown" if the light is None or the query fails.
    """
    if light is None:
        return "Unknown"
    try:
        state = light.get_state()
    except Exception as e:
        log.warning("light.get_state() failed: %s", e)
        return "Unknown"
    name = getattr(state, "name", None)
    if name:
        return name
    return str(state)


def set_intersection_lights(
    world: Any,
    junction_id_or_location: LocationLike,
    state: StateLike,
    freeze: bool = True,
    radius: float = 30.0,
) -> int:
    """Apply ``state`` to every traffic light belonging to one intersection.

    ``junction_id_or_location`` accepts either:
      * an int junction id (matched via ``world.get_map().get_topology()`` and
        ``world.get_traffic_lights_in_junction`` when available); or
      * a ``carla.Location`` — in which case every traffic light whose actor
        location *or* stop-waypoint sits within ``radius`` metres is selected.

    Returns the number of traffic lights actually updated.
    """
    if world is None:
        return 0

    enum_state = _resolve_state(state)
    if enum_state is None:
        return 0

    lights: list = []

    if isinstance(junction_id_or_location, int):
        try:
            lights = list(world.get_traffic_lights_in_junction(junction_id_or_location))
        except Exception as e:
            log.warning(
                "world.get_traffic_lights_in_junction(%s) failed: %s — "
                "falling back to empty selection",
                junction_id_or_location,
                e,
            )
            lights = []
    else:
        target_loc = junction_id_or_location
        try:
            all_lights = list(world.get_actors().filter("traffic.traffic_light*"))
        except Exception as e:
            log.warning("Could not enumerate traffic lights: %s", e)
            return 0
        for tl in all_lights:
            if _light_within_radius(tl, target_loc, radius):
                lights.append(tl)

    caps = detect_capabilities()
    affected = 0
    for tl in lights:
        try:
            tl.set_state(enum_state)
            affected += 1
        except Exception as e:
            log.warning("set_state on light %s failed: %s", getattr(tl, "id", "?"), e)
            continue
        if freeze and caps.has_traffic_light_freeze:
            try:
                tl.freeze(True)
            except Exception as e:
                log.warning("freeze on light %s failed: %s", getattr(tl, "id", "?"), e)

    if freeze and not caps.has_traffic_light_freeze:
        log.warning(
            "TrafficLight.freeze unavailable; %d lights set but not frozen "
            "(caller must re-apply state each tick).",
            affected,
        )
    return affected


def _light_within_radius(tl: Any, target_loc: Any, radius: float) -> bool:
    """True if ``tl`` is near ``target_loc`` by actor location or stop waypoint."""
    try:
        tl_loc = tl.get_transform().location
        if _location_distance(tl_loc, target_loc) <= radius:
            return True
    except Exception:
        pass
    try:
        for wp in tl.get_stop_waypoints():
            if _location_distance(wp.transform.location, target_loc) <= radius:
                return True
    except Exception:
        pass
    return False


__all__ = [
    "find_relevant_traffic_light",
    "set_traffic_light_state",
    "release_traffic_light",
    "get_traffic_light_state",
    "set_intersection_lights",
]
