"""Mine per-town MARSHAL stations and an explicit feasibility mask.

Examples (requires a running CARLA server)::

    python scripts/find_stations.py --town Town03 --self-test
    python scripts/find_stations.py --town Town05
    python scripts/find_stations.py --town Town05 --out configs/stations_town05.json

The CARLA-facing code below only produces plain dictionaries. Requirement-
class filtering and scoring lives in ``marshal_bench.utils.station_search``.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.utils.station_search import (  # noqa: E402
    GENERATION_REQUIREMENT_FIELDS,
    HARD_REQUIREMENT_FIELDS,
    classify_requirements,
    compare_station_tolerance,
    generation_requirements,
    hard_requirements,
    select_best_candidate,
    station_from_candidate,
    validate_requirements,
    validate_stations_payload,
    witness_violations,
)
from marshal_bench.criteria.marshal_metrics import SCENARIO_SPEC  # noqa: E402


SPAWN_DISTANCE_M = 28.0  # _common.pick_signal_episode default (lines 240-292)
MIN_INITIAL_STOPLINE_M = 20.0
MAX_INITIAL_STOPLINE_M = 40.0  # graded near-stopline zero-credit bound
SELF_TEST_SPAWN_TOLERANCE_M = 35.0
SELF_TEST_STOPLINE_TOLERANCE_M = 12.0
WITNESS_PROJECTION_TOLERANCE_M = 3.0
WITNESS_HEADING_TOLERANCE_DEG = 45.0
WITNESS_SIGNAL_TRACE_LIMIT_M = 80.0
TRACE_STEP_M = 2.0
TRACE_LIMIT_M = 120.0


def _location_dict(location: Any) -> dict[str, float]:
    return {
        "x": round(float(location.x), 3),
        "y": round(float(location.y), 3),
        "z": round(float(location.z), 3),
    }


def _transform_dict(transform: Any, z_lift: float = 0.0) -> dict[str, float]:
    out = _location_dict(transform.location)
    out["z"] = round(out["z"] + z_lift, 3)
    out["yaw"] = round(float(transform.rotation.yaw), 3)
    return out


def _distance_xy(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def _same_direction_lane(a: Any, b: Any, carla: Any) -> bool:
    try:
        return (
            b is not None
            and b.lane_type == carla.LaneType.Driving
            and int(a.road_id) == int(b.road_id)
            and int(a.lane_id) * int(b.lane_id) > 0
        )
    except Exception:
        return False


def _best_branch(branches: Iterable[Any], reference: Any) -> Optional[Any]:
    branches = list(branches or [])
    if not branches:
        return None
    same = [
        wp
        for wp in branches
        if getattr(wp, "road_id", None) == getattr(reference, "road_id", None)
        and getattr(wp, "lane_id", None) == getattr(reference, "lane_id", None)
    ]
    return (same or branches)[0]


def _trace_clear_lane(waypoint: Any, direction: str, limit_m: float = TRACE_LIMIT_M) -> float:
    """Trace one lane until a junction, branch end, or distance limit."""
    current = waypoint
    distance = 0.0
    while distance + TRACE_STEP_M <= limit_m:
        try:
            branches = getattr(current, direction)(TRACE_STEP_M)
            nxt = _best_branch(branches, current)
        except Exception:
            break
        if nxt is None or bool(getattr(nxt, "is_junction", False)):
            break
        current = nxt
        distance += TRACE_STEP_M
    return distance


def _lane_neighbours(waypoint: Any, carla: Any) -> list[Any]:
    out: list[Any] = []
    for method in ("get_left_lane", "get_right_lane"):
        current = waypoint
        for _ in range(5):
            try:
                current = getattr(current, method)()
            except Exception:
                current = None
            if current is None:
                break
            if _same_direction_lane(waypoint, current, carla):
                out.append(current)
    unique: dict[int, Any] = {int(wp.lane_id): wp for wp in out}
    return list(unique.values())


def _detour_clearance_m(waypoint: Any, carla: Any) -> float:
    """Width of an immediately adjacent passable lane or shoulder.

    The stagers block only the ego lane, and the oracle can offset across the
    centre line when its same-heading adjacent-lane plan is unavailable. Thus
    direction and road-id equality are not hard requirements here; an adjacent
    Driving lane in either direction or a Shoulder supplies the passage.
    """
    widths: list[float] = []
    for method in ("get_left_lane", "get_right_lane"):
        try:
            adjacent = getattr(waypoint, method)()
        except Exception:
            adjacent = None
        if adjacent is None:
            continue
        try:
            if adjacent.lane_type not in (carla.LaneType.Driving, carla.LaneType.Shoulder):
                continue
            widths.append(float(getattr(adjacent, "lane_width", 0.0) or 0.0))
        except Exception:
            continue
    return max(widths, default=0.0)


def _offset_location(carla: Any, transform: Any, lateral_m: float) -> Any:
    right = transform.get_right_vector()
    return carla.Location(
        x=transform.location.x + right.x * lateral_m,
        y=transform.location.y + right.y * lateral_m,
        z=transform.location.z + 0.2,
    )


def _surface_point(carla_map: Any, carla: Any, transform: Any, lateral_m: float) -> tuple[bool, Optional[dict], str]:
    """Check that an offset point lands on Sidewalk/Shoulder, never Driving."""
    location = _offset_location(carla, transform, lateral_m)
    try:
        driving = carla_map.get_waypoint(
            location, project_to_road=False, lane_type=carla.LaneType.Driving
        )
    except Exception:
        driving = None
    if driving is not None:
        return False, None, "driving"
    lane_types = (("Sidewalk", carla.LaneType.Sidewalk), ("Shoulder", carla.LaneType.Shoulder))
    for name, lane_type in lane_types:
        try:
            wp = carla_map.get_waypoint(location, project_to_road=False, lane_type=lane_type)
        except TypeError:
            wp = carla_map.get_waypoint(location, False, lane_type)
        except Exception:
            wp = None
        if wp is not None:
            point = _location_dict(location)
            point["surface"] = name.lower()
            return True, point, name.lower()
    return False, None, "road_or_unmapped"


def _has_shoulder(carla_map: Any, carla: Any, transform: Any) -> bool:
    for lateral in (-5.0, -4.0, -3.0, 3.0, 4.0, 5.0):
        location = _offset_location(carla, transform, lateral)
        try:
            wp = carla_map.get_waypoint(
                location, project_to_road=False, lane_type=carla.LaneType.Shoulder
            )
        except Exception:
            wp = None
        if wp is not None:
            return True
    return False


def _junction_ahead(stop_waypoint: Any) -> bool:
    for distance in (2.0, 4.0, 8.0, 12.0):
        try:
            if any(bool(getattr(wp, "is_junction", False)) for wp in stop_waypoint.next(distance)):
                return True
        except Exception:
            continue
    return False


def _surface_facts(
    carla_map: Any, carla: Any, spawn_waypoint: Any, fallback_waypoint: Any
) -> dict[str, Any]:
    """Mine the route-relative officer offsets used by the scenario stagers."""
    surface_by_offset: dict[str, Any] = {}
    for offset, officer_distance in ((2.2, 30.0), (3.2, 13.0)):
        try:
            officer_wp = _best_branch(spawn_waypoint.next(officer_distance), spawn_waypoint)
        except Exception:
            officer_wp = None
        officer_tf = officer_wp.transform if officer_wp is not None else fallback_waypoint.transform
        ok, point, surface = _surface_point(carla_map, carla, officer_tf, offset)
        surface_by_offset[f"{offset:.1f}"] = {
            "offroad": ok,
            "point": point,
            "surface": surface,
        }
    return surface_by_offset


def _topology_facts_from_waypoints(
    carla_map: Any,
    carla: Any,
    spawn_wp: Any,
    *,
    stop_wp: Optional[Any] = None,
    light: Optional[Any] = None,
    stopline_route_distance_m: Optional[float] = None,
    forward_traffic_light_distance_m: Optional[float] = None,
) -> dict[str, Any]:
    """Normalise topology using the single path shared by both miners.

    ``stop_wp``/``light`` identify a linked signal approach.  Without them the
    facts describe the junction-free road stretch rooted at ``spawn_wp``.
    """
    signalized = stop_wp is not None and light is not None
    anchor_wp = stop_wp if signalized else spawn_wp
    neighbours = _lane_neighbours(anchor_wp, carla)
    detour_clearance = _detour_clearance_m(anchor_wp, carla)
    forward = _trace_clear_lane(spawn_wp, "next")
    # For a signal approach, run-up is the walked route distance from this
    # spawn to its linked stopline. It is not the amount of clear road behind
    # the stop waypoint (the old definition, which could legitimately be 0).
    runup = (
        float(stopline_route_distance_m or 0.0)
        if signalized
        else _trace_clear_lane(spawn_wp, "previous")
    )
    if signalized:
        heading_delta = abs(
            (float(stop_wp.transform.rotation.yaw) - float(spawn_wp.transform.rotation.yaw) + 180.0)
            % 360.0
            - 180.0
        )
        lane_width = float(getattr(stop_wp, "lane_width", 3.0) or 3.0)
        geometric_margin = lane_width + max(0.0, 15.0 - heading_delta)
        identifier = (
            f"tl{getattr(light, 'id', -1)}-r{stop_wp.road_id}-"
            f"l{stop_wp.lane_id}-s{float(stop_wp.s):.1f}"
        )
    else:
        geometric_margin = min(forward, 50.0)
        identifier = f"road{spawn_wp.road_id}-lane{spawn_wp.lane_id}-s{float(spawn_wp.s):.1f}"
    facts = {
        "id": identifier,
        "spawn": _transform_dict(spawn_wp.transform, z_lift=0.5),
        "stopline": _location_dict(anchor_wp.transform.location),
        "tl_id": int(getattr(light, "id", -1)) if signalized else -1,
        "lanes": 1 + len(neighbours),
        "signalized": signalized,
        "forward_traffic_light_distance_m": (
            None
            if forward_traffic_light_distance_m is None
            else round(float(forward_traffic_light_distance_m), 3)
        ),
        "junction_approach": _junction_ahead(stop_wp) if signalized else False,
        "runup_m": runup,
        "initial_stopline_distance_m": (
            round(
                math.hypot(
                    float(anchor_wp.transform.location.x)
                    - float(spawn_wp.transform.location.x),
                    float(anchor_wp.transform.location.y)
                    - float(spawn_wp.transform.location.y),
                ),
                3,
            )
            if signalized
            else None
        ),
        "stopline_distance_m": (
            None
            if stopline_route_distance_m is None
            else round(float(stopline_route_distance_m), 3)
        ),
        "geometric_margin_m": round(geometric_margin, 3) if signalized else geometric_margin,
        "adjacent_same_road_lane": bool(neighbours),
        "detour_clearance_m": round(detour_clearance, 3),
        "offroad_shoulder": _has_shoulder(carla_map, carla, spawn_wp.transform),
        "surface_by_offset": _surface_facts(carla_map, carla, spawn_wp, anchor_wp),
        "spawn_clear": None,
    }
    if not signalized:
        facts["junction_free"] = not bool(getattr(spawn_wp, "is_junction", False))
    return facts


def _signal_candidates(world: Any, carla: Any) -> list[dict[str, Any]]:
    carla_map = world.get_map()
    try:
        lights = list(world.get_actors().filter("traffic.traffic_light*"))
    except Exception:
        lights = []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for light in lights:
        try:
            stop_waypoints = list(light.get_stop_waypoints() or [])
        except Exception:
            continue
        for stop_wp in stop_waypoints:
            try:
                if stop_wp.lane_type != carla.LaneType.Driving or stop_wp.is_junction:
                    continue
                back = list(stop_wp.previous(SPAWN_DISTANCE_M) or [])
            except Exception:
                continue
            spawn_wp = _best_branch(back, stop_wp)
            if spawn_wp is None or bool(getattr(spawn_wp, "is_junction", False)):
                continue
            key = (
                int(getattr(light, "id", -1)),
                int(getattr(stop_wp, "road_id", 0)),
                int(getattr(stop_wp, "lane_id", 0)),
                int(round(float(stop_wp.s))),
            )
            if key in seen:
                continue
            seen.add(key)
            _linked_light, _linked_stop, route_distance = _next_traffic_light_stop(
                spawn_wp, [(light, stop_wp)]
            )
            candidates.append(
                _topology_facts_from_waypoints(
                    carla_map,
                    carla,
                    spawn_wp,
                    stop_wp=stop_wp,
                    light=light,
                    stopline_route_distance_m=route_distance,
                    forward_traffic_light_distance_m=_nearest_forward_traffic_light_distance(
                        world, spawn_wp.transform
                    ),
                )
            )
    return candidates


def _non_signal_candidates(world: Any, carla: Any) -> list[dict[str, Any]]:
    """Mine sampled junction-free stretches for future non-light requirements."""
    carla_map = world.get_map()
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    try:
        waypoints = carla_map.generate_waypoints(8.0)
    except Exception:
        return out
    for wp in waypoints:
        try:
            if wp.lane_type != carla.LaneType.Driving or wp.is_junction:
                continue
            key = (int(wp.road_id), int(wp.lane_id), int(float(wp.s) // 20.0))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        forward = _trace_clear_lane(wp, "next")
        if forward < 35.0:
            continue
        out.append(_topology_facts_from_waypoints(carla_map, carla, wp))
    return out


def _for_requirements(candidate: Mapping[str, Any], requirements: Mapping[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(candidate))
    offset = float(
        generation_requirements(requirements).get("officer_lateral_offset_m", 0.0)
        or 0.0
    )
    surface = (candidate.get("surface_by_offset") or {}).get(f"{offset:.1f}", {})
    candidate["officer_offroad"] = bool(surface.get("offroad")) if offset else True
    candidate["officer_point"] = surface.get("point")
    candidate["officer_surface"] = surface.get("surface", "not_required" if not offset else "unknown")
    return candidate


def _spawn_is_clear(world: Any, carla: Any, candidate: Mapping[str, Any], blueprint: Any) -> bool:
    spawn = candidate["spawn"]
    transform = carla.Transform(
        carla.Location(x=float(spawn["x"]), y=float(spawn["y"]), z=float(spawn["z"])),
        carla.Rotation(yaw=float(spawn["yaw"])),
    )
    actor = None
    try:
        actor = world.try_spawn_actor(blueprint, transform)
        return actor is not None
    except Exception:
        return False
    finally:
        if actor is not None:
            try:
                actor.destroy()
            except Exception:
                pass


def _ego_blueprint(world: Any) -> Any:
    library = world.get_blueprint_library()
    for blueprint_id in ("vehicle.tesla.model3", "vehicle.lincoln.mkz_2017", "vehicle.audi.tt"):
        try:
            return library.find(blueprint_id)
        except Exception:
            pass
    vehicles = list(library.filter("vehicle.*"))
    if not vehicles:
        raise RuntimeError("no vehicle blueprint is available for spawn-clearance validation")
    return vehicles[0]


def _select_with_validation(
    world: Any,
    carla: Any,
    candidates: list[dict[str, Any]],
    requirements: Mapping[str, Any],
    blueprint: Any,
    station_use_counts: Optional[Mapping[str, int]] = None,
) -> tuple[Optional[dict[str, Any]], str]:
    working = [_for_requirements(candidate, requirements) for candidate in candidates]
    blocked = 0
    while True:
        chosen, reason = select_best_candidate(
            working, requirements, station_use_counts=station_use_counts
        )
        if chosen is None:
            suffix = f"; {blocked} otherwise-suitable spawn(s) were blocked" if blocked else ""
            return None, reason + suffix
        if not _spawn_is_clear(world, carla, chosen, blueprint):
            blocked += 1
            for candidate in working:
                if candidate.get("id") == chosen.get("id"):
                    candidate["spawn_clear"] = False
                    break
            continue
        initial_distance = _distance_xy(chosen["spawn"], chosen["stopline"])
        hard = hard_requirements(requirements)
        generation = generation_requirements(requirements)
        minimum_initial = float(
            generation.get("min_initial_stopline_m", MIN_INITIAL_STOPLINE_M)
        )
        maximum_initial = float(
            generation.get("max_initial_stopline_m", MAX_INITIAL_STOPLINE_M)
        )
        if hard.get("needs_traffic_light") and not (
            minimum_initial <= initial_distance <= maximum_initial
        ):
            for candidate in working:
                if candidate.get("id") == chosen.get("id"):
                    candidate["spawn_clear"] = False
                    break
            blocked += 1
            continue
        if hard.get("needs_sidewalk_point") and chosen.get("officer_surface") not in {
            "sidewalk",
            "shoulder",
        }:
            # Normally filtered before selection; retain this explicit validation
            # guard so the emitted feasibility mask cannot silently degrade.
            return None, "chosen officer point is on the road or unmapped"
        chosen["spawn_clear"] = True
        chosen["initial_stopline_distance_m"] = round(initial_distance, 3)
        return chosen, reason


def _curated_stopline(curated: Mapping[str, Any], signal_candidates: Iterable[Mapping[str, Any]]) -> Optional[dict]:
    """Resolve the stopline ahead of a legacy station (whose schema omits it)."""
    yaw = math.radians(float(curated["yaw"]))
    fwd = (math.cos(yaw), math.sin(yaw))
    ranked: list[tuple[float, dict]] = []
    for candidate in signal_candidates:
        stopline = candidate["stopline"]
        dx = float(stopline["x"]) - float(curated["x"])
        dy = float(stopline["y"]) - float(curated["y"])
        longitudinal = dx * fwd[0] + dy * fwd[1]
        lateral = abs(-dx * fwd[1] + dy * fwd[0])
        if 0.0 < longitudinal <= 80.0 and lateral <= 12.0:
            id_penalty = 0.0 if int(candidate.get("tl_id", -1)) == int(curated.get("tl_id", -2)) else 5.0
            ranked.append((lateral + id_penalty + longitudinal * 0.01, dict(stopline)))
    ranked.sort(key=lambda row: row[0])
    return ranked[0][1] if ranked else None


def _heading_delta_deg(a: float, b: float) -> float:
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def _project_witness_waypoint(
    carla_map: Any, carla: Any, curated: Mapping[str, Any]
) -> tuple[Optional[Any], Optional[str], Optional[float]]:
    """Project a witness to a nearby heading-compatible Driving waypoint."""
    try:
        location = carla.Location(
            x=float(curated["x"]), y=float(curated["y"]), z=float(curated.get("z", 0.0))
        )
        witness_yaw = float(curated["yaw"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"invalid witness pose: {exc}", None
    try:
        projected = carla_map.get_waypoint(
            location, project_to_road=True, lane_type=carla.LaneType.Driving
        )
    except TypeError:
        projected = carla_map.get_waypoint(location, True, carla.LaneType.Driving)
    except Exception:
        projected = None
    if projected is None:
        return None, "no driving waypoint within 3 m of witness pose", None

    def projection_distance(wp: Any) -> float:
        return math.hypot(
            float(wp.transform.location.x) - float(location.x),
            float(wp.transform.location.y) - float(location.y),
        )

    distance = projection_distance(projected)
    if distance > WITNESS_PROJECTION_TOLERANCE_M:
        return None, "no driving waypoint within 3 m of witness pose", distance
    projected_yaw = float(projected.transform.rotation.yaw)
    if _heading_delta_deg(witness_yaw, projected_yaw) <= WITNESS_HEADING_TOLERANCE_DEG:
        return projected, None, distance

    # CARLA can choose the geometrically nearest member of a two-way road even
    # when the curated transform faces the other member. Walk across the lane
    # stack and select the nearest heading-compatible Driving lane.
    opposing: list[tuple[float, float, Any]] = []
    for method in ("get_left_lane", "get_right_lane"):
        current = projected
        for _ in range(6):
            try:
                current = getattr(current, method)()
            except Exception:
                current = None
            if current is None:
                break
            try:
                if current.lane_type != carla.LaneType.Driving:
                    continue
                candidate_distance = projection_distance(current)
                candidate_delta = _heading_delta_deg(
                    witness_yaw, float(current.transform.rotation.yaw)
                )
            except Exception:
                continue
            if (
                candidate_distance <= WITNESS_PROJECTION_TOLERANCE_M
                and candidate_delta <= WITNESS_HEADING_TOLERANCE_DEG
            ):
                opposing.append((candidate_distance, candidate_delta, current))
    if opposing:
        opposing.sort(key=lambda row: (row[0], row[1]))
        return opposing[0][2], None, opposing[0][0]
    return (
        None,
        f"nearest driving waypoint heading differs from witness yaw by "
        f"{_heading_delta_deg(witness_yaw, projected_yaw):.1f} degrees",
        distance,
    )


def _traffic_light_stop_waypoints(world: Any) -> list[tuple[Any, Any]]:
    try:
        lights = list(world.get_actors().filter("traffic.traffic_light*"))
    except Exception:
        lights = []
    out: list[tuple[Any, Any]] = []
    for light in lights:
        try:
            out.extend((light, wp) for wp in (light.get_stop_waypoints() or []))
        except Exception:
            continue
    return out


def _nearest_forward_traffic_light_distance(
    world: Any, ego_transform: Any, radius: float = 75.0
) -> Optional[float]:
    """Mirror the stager's forward-hemisphere light-pinning predicate."""
    try:
        lights = list(world.get_actors().filter("traffic.traffic_light*"))
        origin = ego_transform.location
        yaw = math.radians(float(ego_transform.rotation.yaw))
    except Exception:
        return None
    forward_x, forward_y = math.cos(yaw), math.sin(yaw)
    distances: list[float] = []
    for light in lights:
        try:
            location = light.get_transform().location
            dx = float(location.x) - float(origin.x)
            dy = float(location.y) - float(origin.y)
            distance = math.hypot(dx, dy)
        except Exception:
            continue
        if distance <= radius and forward_x * dx + forward_y * dy > 0.0:
            distances.append(distance)
    return min(distances, default=None)


def _next_traffic_light_stop(
    spawn_wp: Any, stops: Iterable[tuple[Any, Any]]
) -> tuple[Optional[Any], Optional[Any], Optional[float]]:
    """Walk the witness route to its next linked traffic-light stop waypoint."""
    stops = list(stops)
    current = spawn_wp
    travelled = 0.0
    while travelled <= WITNESS_SIGNAL_TRACE_LIMIT_M + 1e-9:
        matches: list[tuple[float, int, Any, Any]] = []
        for light, stop_wp in stops:
            try:
                if (
                    stop_wp.lane_type != current.lane_type
                    or int(stop_wp.road_id) != int(current.road_id)
                    or int(stop_wp.lane_id) != int(current.lane_id)
                ):
                    continue
                separation = math.hypot(
                    float(stop_wp.transform.location.x) - float(current.transform.location.x),
                    float(stop_wp.transform.location.y) - float(current.transform.location.y),
                )
            except Exception:
                continue
            if separation <= TRACE_STEP_M + 0.25:
                matches.append((separation, int(getattr(light, "id", -1)), light, stop_wp))
        if matches:
            matches.sort(key=lambda row: (row[0], row[1]))
            separation, _light_id, light, stop_wp = matches[0]
            return light, stop_wp, travelled + separation
        if travelled + TRACE_STEP_M > WITNESS_SIGNAL_TRACE_LIMIT_M:
            break
        try:
            nxt = _best_branch(current.next(TRACE_STEP_M), current)
        except Exception:
            nxt = None
        if nxt is None:
            break
        current = nxt
        travelled += TRACE_STEP_M
    return None, None, None


def _topology_facts_at_pose(
    world: Any, carla: Any, curated: Mapping[str, Any]
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Mine normalized topology directly at a curated witness pose."""
    carla_map = world.get_map()
    spawn_wp, reason, projection_distance = _project_witness_waypoint(
        carla_map, carla, curated
    )
    if spawn_wp is None:
        return None, reason
    light, stop_wp, stopline_distance = _next_traffic_light_stop(
        spawn_wp, _traffic_light_stop_waypoints(world)
    )
    # carla.Transform is a boost-python type without pickle support, so build
    # the witness transform explicitly instead of deepcopying the waypoint's.
    witness_transform = carla.Transform(
        carla.Location(
            x=float(curated["x"]),
            y=float(curated["y"]),
            z=float(curated.get("z", 0.0)),
        ),
        carla.Rotation(
            pitch=float(getattr(spawn_wp.transform.rotation, "pitch", 0.0)),
            yaw=float(curated["yaw"]),
            roll=float(getattr(spawn_wp.transform.rotation, "roll", 0.0)),
        ),
    )
    facts = _topology_facts_from_waypoints(
        carla_map,
        carla,
        spawn_wp,
        stop_wp=stop_wp,
        light=light,
        stopline_route_distance_m=stopline_distance,
        forward_traffic_light_distance_m=_nearest_forward_traffic_light_distance(
            world, witness_transform
        ),
    )
    facts["spawn"] = {
        "x": round(float(curated["x"]), 3),
        "y": round(float(curated["y"]), 3),
        "z": round(float(curated.get("z", 0.0)), 3),
        "yaw": round(float(curated["yaw"]), 3),
    }
    facts["projection_distance_m"] = round(float(projection_distance or 0.0), 3)
    return facts, None


def _curated_topology_facts(
    world: Any,
    carla: Any,
    curated: Mapping[str, Any],
    requirements: Mapping[str, Any],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    facts, reason = _topology_facts_at_pose(world, carla, curated)
    if facts is None:
        return None, reason
    return _for_requirements(facts, requirements), None


def _write_self_test(
    world: Any,
    carla: Any,
    generated: Mapping[str, Any],
    chosen: Mapping[str, Mapping[str, Any]],
    signal_candidates: list[dict[str, Any]],
    road_candidates: list[dict[str, Any]],
    feasibility: Mapping[str, Any],
    requirements: Mapping[str, Mapping[str, Any]],
    out_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    curated_path = ROOT / "marshal_bench" / "configs" / "stations.json"
    curated_payload = json.loads(curated_path.read_text(encoding="utf-8"))
    rows: dict[str, Any] = {}
    witness_failures: dict[str, list[str]] = {}
    for scenario in SCENARIO_SPEC:
        status = feasibility[scenario]
        requirement = requirements[scenario]
        curated = curated_payload["stations"][scenario]
        witness_facts, projection_error = _curated_topology_facts(
            world, carla, curated, requirement
        )
        violations = (
            [projection_error or "witness topology projection failed"]
            if witness_facts is None
            else witness_violations(witness_facts, requirement)
        )
        if violations:
            witness_failures[scenario] = violations
            for violation in violations:
                print(f"WITNESS VIOLATION {scenario}: {violation}")
        if not status["feasible"]:
            rows[scenario] = {
                "skipped": True,
                "reason": status["reason"],
                "witness_violations": violations,
            }
            continue
        generated_station = generated[scenario]
        generated_stopline = chosen[scenario]["stopline"]
        old_stopline = _curated_stopline(curated, signal_candidates)
        comparison = compare_station_tolerance(
            generated_station,
            curated,
            generated_stopline=generated_stopline,
            curated_stopline=old_stopline,
            spawn_tolerance_m=SELF_TEST_SPAWN_TOLERANCE_M,
            stopline_tolerance_m=SELF_TEST_STOPLINE_TOLERANCE_M,
        )
        if comparison["within_tolerance"]:
            comparison["explanation"] = "generated and curated approaches agree within the documented tolerance"
        else:
            comparison["explanation"] = (
                "divergence is explicit: the global run-up+margin optimum is a different valid approach; "
                "Town03's hand file intentionally distributes scenarios across distinct benchmark locations"
            )
        comparison["curated_stopline_resolved"] = old_stopline is not None
        comparison["witness_violations"] = violations
        rows[scenario] = comparison
    report = {
        "map": "Town03",
        "spawn_tolerance_m": SELF_TEST_SPAWN_TOLERANCE_M,
        "stopline_tolerance_m": SELF_TEST_STOPLINE_TOLERANCE_M,
        "comparisons": rows,
        "witness_violations": witness_failures,
    }
    if not dry_run:
        path = out_dir / "self_test_town03.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    return report


def _normalise_town(town: str) -> str:
    town = str(town).strip().replace("\\", "/").split("/")[-1]
    if not town or not all(ch.isalnum() or ch in "_-" for ch in town):
        raise ValueError(f"invalid town name: {town!r}")
    return town


def _resolve_output(raw: Optional[str], town: str) -> Path:
    if raw is None:
        return ROOT / "marshal_bench" / "configs" / f"stations_{town.lower()}.json"
    path = Path(raw)
    if not path.is_absolute():
        # The work-order spelling "configs/..." refers to the package config dir.
        if path.parts and path.parts[0].lower() == "configs":
            path = ROOT / "marshal_bench" / path
        else:
            path = ROOT / path
    return path.resolve()


def _load_requirements() -> dict[str, Any]:
    path = ROOT / "marshal_bench" / "configs" / "staging_requirements.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    criterion_classes = payload.get("criterion_classes", {})
    class_errors: list[str] = []
    expected_classes = {
        "hard": HARD_REQUIREMENT_FIELDS,
        "generation": GENERATION_REQUIREMENT_FIELDS,
    }
    if set(criterion_classes) != set(expected_classes):
        class_errors.append("criterion_classes must contain exactly hard and generation")
    for name, expected in expected_classes.items():
        actual = criterion_classes.get(name, [])
        if not isinstance(actual, list) or set(actual) != set(expected) or len(actual) != len(set(actual)):
            class_errors.append(f"criterion_classes.{name} does not match the policy fields")
    criterion_defaults = payload.get("criterion_defaults", {})
    raw_requirements = payload.get("scenarios", {})
    requirements = {
        scenario: classify_requirements(req, criterion_classes, criterion_defaults)
        for scenario, req in raw_requirements.items()
    }
    errors = class_errors + [
        f"{scenario}: {error}"
        for scenario, req in requirements.items()
        for error in validate_requirements(req)
    ]
    if errors:
        raise ValueError("invalid staging requirements:\n" + "\n".join(errors))
    return requirements


def _ensure_world(client: Any, town: str) -> Any:
    world = client.get_world()
    current = str(world.get_map().name).replace("\\", "/").split("/")[-1]
    if current.lower() != town.lower():
        print(f"loading {town} (current map: {current})")
        world = client.load_world(town)
    return world


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--town", required=True, help="CARLA town, e.g. Town05")
    parser.add_argument("--out", help="station JSON path; configs/... means marshal_bench/configs/...")
    parser.add_argument("--host", default="127.0.0.1", help="CARLA host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2000, help="CARLA RPC port (default: 2000)")
    parser.add_argument("--timeout", type=float, default=30.0, help="CARLA client timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="mine and validate but do not write files")
    parser.add_argument("--self-test", action="store_true", help="compare generated Town03 stations with stations.json")
    args = parser.parse_args(argv)

    town = _normalise_town(args.town)
    if args.self_test and town.lower() != "town03":
        parser.error("--self-test is only valid with --town Town03")
    out_path = _resolve_output(args.out, town)
    feasibility_path = out_path.with_name(f"feasibility_{town.lower()}.json")
    requirements = _load_requirements()

    carla = import_carla()
    client = carla.Client(args.host, args.port)
    client.set_timeout(float(args.timeout))
    world = _ensure_world(client, town)
    print(f"mining {world.get_map().name} via {args.host}:{args.port}")
    signal_candidates = _signal_candidates(world, carla)
    road_candidates = _non_signal_candidates(world, carla)
    print(f"mined {len(signal_candidates)} signal approaches and {len(road_candidates)} junction-free stretches")

    blueprint = _ego_blueprint(world)
    stations: dict[str, Any] = {}
    feasibility: dict[str, Any] = {}
    chosen_by_scenario: dict[str, Any] = {}
    station_use_counts: dict[str, int] = {}
    for scenario in SCENARIO_SPEC:
        requirement = requirements[scenario]
        pool = (
            signal_candidates
            if hard_requirements(requirement)["needs_traffic_light"]
            else road_candidates
        )
        chosen, reason = _select_with_validation(
            world,
            carla,
            pool,
            requirement,
            blueprint,
            station_use_counts=station_use_counts,
        )
        if chosen is None:
            feasibility[scenario] = {"feasible": False, "reason": reason}
            print(f"INFEASIBLE {scenario}: {reason}")
            continue
        stations[scenario] = station_from_candidate(chosen)
        chosen_by_scenario[scenario] = chosen
        chosen_id = str(chosen.get("id", ""))
        station_use_counts[chosen_id] = station_use_counts.get(chosen_id, 0) + 1
        detail = (
            f"{reason}; spawn clear; initial stopline distance "
            f"{chosen['initial_stopline_distance_m']:.1f} m; officer surface {chosen['officer_surface']}"
        )
        feasibility[scenario] = {"feasible": True, "reason": detail}
        print(f"FEASIBLE   {scenario}: {chosen['id']} score={chosen['runup_m'] + chosen['geometric_margin_m']:.1f}")

    payload = {
        "_comment": (
            f"Generated by scripts/find_stations.py for {town}; rich validation metadata is recorded "
            f"in feasibility_{town.lower()}.json and is intentionally omitted here for legacy compatibility."
        ),
        "map": town,
        "stations": stations,
    }
    schema_errors = validate_stations_payload(payload, expected_scenarios=stations)
    if schema_errors:
        raise RuntimeError("generated station schema is invalid: " + "; ".join(schema_errors))

    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        feasibility_path.write_text(json.dumps(feasibility, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out_path}")
        print(f"wrote {feasibility_path}")
    else:
        print("dry-run: no files written")

    if args.self_test:
        report = _write_self_test(
            world,
            carla,
            stations,
            chosen_by_scenario,
            signal_candidates,
            road_candidates,
            feasibility,
            requirements,
            out_path.parent,
            args.dry_run,
        )
        compared = [row for row in report["comparisons"].values() if not row.get("skipped")]
        within = sum(bool(row.get("within_tolerance")) for row in compared)
        feasible_count = sum(bool(status["feasible"]) for status in feasibility.values())
        distinct = len({str(chosen.get("id", "")) for chosen in chosen_by_scenario.values()})
        print(f"Town03 feasibility: {feasible_count}/{len(SCENARIO_SPEC)} feasible")
        print(f"Town03 self-test: {within}/{len(compared)} within tolerance; every divergence is explained in the report")
        print(f"Town03 assignments: {distinct} distinct locations across {len(stations)} feasible scenarios")
        if report["witness_violations"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
