"""Shared non-privileged lane-follow route helpers for Track-B controllers."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LaneFollowPlan:
    """Map-derived lane-follow plan from the ego's current driving lane."""

    gps_plan: list
    world_plan: list
    waypoints: list
    route_end: Any
    lat_ref: float
    lon_ref: float


def build_lane_follow_plan(
    world: Any,
    ego: Any,
    carla: Any,
    *,
    road_option: Any = None,
    horizon_m: float = 160.0,
    step_m: float = 1.0,
    lat_ref: Optional[float] = None,
    lon_ref: Optional[float] = None,
) -> LaneFollowPlan:
    """Build the same rolling lane-follow route used by the TransFuser adapter.

    The route is based only on the CARLA map and ego pose: project ego to its
    current driving lane, then repeatedly choose the next waypoint with the
    smallest yaw discontinuity.
    """

    if world is None or ego is None or carla is None:
        raise RuntimeError("world, ego, and carla are required")
    carla_map = world.get_map()
    wp = carla_map.get_waypoint(
        ego.get_location(),
        project_to_road=True,
        lane_type=carla.LaneType.Driving,
    )
    if wp is None:
        raise RuntimeError("Could not project ego to a driving waypoint")

    if lat_ref is None or lon_ref is None:
        lat_ref, lon_ref = read_latlon_ref(world)

    transforms = []
    waypoints = []
    prev_yaw = float(wp.transform.rotation.yaw)
    n_steps = max(8, int(float(horizon_m) / max(0.5, float(step_m))))
    for _ in range(n_steps):
        transforms.append((wp.transform, road_option))
        waypoints.append((wp, road_option))
        nxt = list(wp.next(float(step_m)))
        if not nxt:
            break
        wp = min(
            nxt,
            key=lambda cand: abs(
                angle_delta(float(cand.transform.rotation.yaw), prev_yaw)
            ),
        )
        prev_yaw = float(wp.transform.rotation.yaw)

    if not transforms:
        raise RuntimeError("Map waypoint route is empty")
    route_end = transforms[-1][0].location
    gps_plan = [
        (location_to_gps(transform.location, lat_ref, lon_ref), option)
        for transform, option in transforms
    ]
    return LaneFollowPlan(
        gps_plan=gps_plan,
        world_plan=transforms,
        waypoints=waypoints,
        route_end=route_end,
        lat_ref=float(lat_ref),
        lon_ref=float(lon_ref),
    )


def read_latlon_ref(world: Any) -> tuple[float, float]:
    lat_ref, lon_ref = 42.0, 2.0
    try:
        root = ET.fromstring(world.get_map().to_opendrive())
        for element in root.iter():
            if not element.tag.endswith("geoReference") or not element.text:
                continue
            for item in element.text.split():
                if "+lat_0" in item:
                    lat_ref = float(item.split("=")[1])
                elif "+lon_0" in item:
                    lon_ref = float(item.split("=")[1])
    except Exception:
        pass
    return lat_ref, lon_ref


def world_xy_to_latlon(
    world_x: float,
    world_y: float,
    lat_ref: float,
    lon_ref: float,
) -> tuple[float, float]:
    earth_radius = 6378137.0
    scale = math.cos(float(lat_ref) * math.pi / 180.0)
    mx = scale * float(lon_ref) * math.pi * earth_radius / 180.0
    my = scale * earth_radius * math.log(
        math.tan((90.0 + float(lat_ref)) * math.pi / 360.0)
    )
    mx += float(world_x)
    my -= float(world_y)
    lon = mx * 180.0 / (math.pi * earth_radius * scale)
    lat = 360.0 * math.atan(math.exp(my / (earth_radius * scale))) / math.pi - 90.0
    return lat, lon


def location_to_gps(location: Any, lat_ref: float, lon_ref: float) -> dict[str, float]:
    lat, lon = world_xy_to_latlon(float(location.x), float(location.y), lat_ref, lon_ref)
    return {"lat": lat, "lon": lon, "z": float(location.z)}


def angle_delta(a: float, b: float) -> float:
    return (float(a) - float(b) + 180.0) % 360.0 - 180.0

