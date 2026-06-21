"""Debug-visualisation helpers for MARSHAL scenarios.

Thin wrappers around ``world.debug.draw_*`` calls used by the fallback
gesture-visualisation path and by oracle-track scenario tooling. Every
function tolerates being passed either a ``carla.Location`` or any object
exposing a ``.location`` attribute (e.g. ``carla.Transform``), and either
a ``(r, g, b)`` int tuple or a ``carla.Color``.

If ``world.debug`` is unavailable or a draw call raises, the helper logs at
WARNING (once per process per failure type) and returns; no exception is
propagated to the caller.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple, Union

from marshal_bench.utils.carla_api_compat import import_carla

log = logging.getLogger("marshal_bench.utils.debug_viz")

ColorLike = Union[Tuple[int, int, int], Tuple[int, int, int, int], Any]
LocationLike = Any  # carla.Location, carla.Transform, or anything with .location

_GESTURE_COLOR_MAP = {
    "STOP": (255, 30, 30),
    "PROCEED": (30, 220, 30),
    "LEFT": (0, 200, 255),
    "RIGHT": (255, 0, 200),
    "SLOW": (255, 220, 0),
    "IDLE": (230, 230, 230),
}

_warned_no_debug = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _extract_location(loc_or_transform: LocationLike) -> Optional[Any]:
    """Return a carla.Location from a Location, Transform, or .location-bearing object."""
    if loc_or_transform is None:
        return None
    inner = getattr(loc_or_transform, "location", None)
    if inner is not None:
        return inner
    if hasattr(loc_or_transform, "x") and hasattr(loc_or_transform, "y"):
        return loc_or_transform
    return None


def _to_color(color: Optional[ColorLike], default: Tuple[int, int, int] = (255, 255, 255)) -> Any:
    """Coerce a tuple or carla.Color into a carla.Color."""
    carla = import_carla()
    Color = carla.Color
    if color is None:
        r, g, b = default
        return Color(r, g, b)
    if isinstance(color, Color):
        return color
    try:
        if len(color) == 4:
            r, g, b, a = color
            return Color(int(r), int(g), int(b), int(a))
        r, g, b = color
        return Color(int(r), int(g), int(b))
    except Exception:
        r, g, b = default
        return Color(r, g, b)


def _get_debug(world: Any) -> Optional[Any]:
    """Return ``world.debug`` or None, warning once on absence."""
    global _warned_no_debug
    if world is None:
        return None
    debug = getattr(world, "debug", None)
    if debug is None:
        if not _warned_no_debug:
            log.warning("world.debug is not available; debug_viz calls will be no-ops")
            _warned_no_debug = True
        return None
    return debug


def _offset_location(loc: Any, dz: float) -> Any:
    """Return a new carla.Location shifted by dz on the Z axis."""
    carla = import_carla()
    try:
        return carla.Location(x=loc.x, y=loc.y, z=loc.z + dz)
    except Exception:
        return loc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def draw_gesture_label(
    world: Any,
    location: LocationLike,
    gesture_id_str: str,
    life_time: float = 1.0,
    color: Optional[ColorLike] = None,
) -> None:
    """Draw ``"OFFICER: <GESTURE>"`` floating ~2.2m above ``location``.

    If ``color`` is None a colour is chosen from the gesture name
    (STOP=red, PROCEED=green, LEFT=cyan, RIGHT=magenta, SLOW=yellow,
    IDLE=white, default=white).
    """
    debug = _get_debug(world)
    if debug is None:
        return
    loc = _extract_location(location)
    if loc is None:
        return
    text_loc = _offset_location(loc, 2.2)
    gesture_key = (gesture_id_str or "").strip().upper()
    if color is None:
        default_rgb = _GESTURE_COLOR_MAP.get(gesture_key, (255, 255, 255))
        carla_color = _to_color(None, default=default_rgb)
    else:
        carla_color = _to_color(color)
    text = f"OFFICER: {gesture_key}"
    try:
        debug.draw_string(
            text_loc,
            text,
            draw_shadow=False,
            color=carla_color,
            life_time=float(life_time),
            persistent_lines=False,
        )
    except Exception as e:
        log.warning("draw_string failed for gesture label %r: %s", text, e)


def draw_authority_arrow(
    world: Any,
    src_loc: LocationLike,
    dst_loc: LocationLike,
    color: ColorLike = (255, 140, 0),
    life_time: float = 1.0,
) -> None:
    """Draw an arrow from ``src_loc`` to ``dst_loc`` indicating authority direction."""
    debug = _get_debug(world)
    if debug is None:
        return
    a = _extract_location(src_loc)
    b = _extract_location(dst_loc)
    if a is None or b is None:
        return
    carla_color = _to_color(color, default=(255, 140, 0))
    try:
        debug.draw_arrow(
            a,
            b,
            thickness=0.15,
            arrow_size=0.3,
            color=carla_color,
            life_time=float(life_time),
        )
    except Exception as e:
        log.warning("draw_arrow failed: %s", e)


def draw_target_lane_line(
    world: Any,
    officer_loc: LocationLike,
    target_loc: LocationLike,
    color: ColorLike = (0, 200, 255),
    life_time: float = 1.0,
) -> None:
    """Draw a line from the officer to the target lane / vehicle being directed."""
    debug = _get_debug(world)
    if debug is None:
        return
    a = _extract_location(officer_loc)
    b = _extract_location(target_loc)
    if a is None or b is None:
        return
    carla_color = _to_color(color, default=(0, 200, 255))
    try:
        debug.draw_line(
            a,
            b,
            thickness=0.1,
            color=carla_color,
            life_time=float(life_time),
        )
    except Exception as e:
        log.warning("draw_line failed: %s", e)


def draw_officer_marker(
    world: Any,
    location: LocationLike,
    text: str = "OFFICER",
    life_time: float = 1.0,
) -> None:
    """Draw a vertical marker + label at the officer's location for visual id."""
    debug = _get_debug(world)
    if debug is None:
        return
    loc = _extract_location(location)
    if loc is None:
        return
    top = _offset_location(loc, 2.5)
    label_loc = _offset_location(loc, 2.8)
    carla_color = _to_color((255, 255, 0), default=(255, 255, 0))
    try:
        debug.draw_line(
            loc,
            top,
            thickness=0.08,
            color=carla_color,
            life_time=float(life_time),
        )
    except Exception as e:
        log.warning("draw_officer_marker line failed: %s", e)
    try:
        debug.draw_string(
            label_loc,
            text,
            draw_shadow=False,
            color=carla_color,
            life_time=float(life_time),
            persistent_lines=False,
        )
    except Exception as e:
        log.warning("draw_officer_marker string failed: %s", e)


def draw_compliance_zone(
    world: Any,
    location: LocationLike,
    radius: float = 3.0,
    color: ColorLike = (0, 255, 0),
    life_time: float = 1.0,
) -> None:
    """Sketch a polygonal ring around ``location`` representing a compliance zone.

    CARLA's DebugHelper exposes draw_box but no native circle; we approximate
    by drawing a regular 16-gon out of line segments at ground level.
    """
    debug = _get_debug(world)
    if debug is None:
        return
    loc = _extract_location(location)
    if loc is None:
        return
    carla = import_carla()
    carla_color = _to_color(color, default=(0, 255, 0))

    import math as _math
    segments = 16
    points = []
    try:
        cx, cy, cz = loc.x, loc.y, loc.z + 0.1
    except Exception:
        return
    for i in range(segments + 1):
        theta = 2.0 * _math.pi * i / segments
        points.append(
            carla.Location(
                x=cx + radius * _math.cos(theta),
                y=cy + radius * _math.sin(theta),
                z=cz,
            )
        )
    try:
        for a, b in zip(points[:-1], points[1:]):
            debug.draw_line(
                a,
                b,
                thickness=0.05,
                color=carla_color,
                life_time=float(life_time),
            )
    except Exception as e:
        log.warning("draw_compliance_zone failed: %s", e)


__all__ = [
    "draw_gesture_label",
    "draw_authority_arrow",
    "draw_target_lane_line",
    "draw_officer_marker",
    "draw_compliance_zone",
]
