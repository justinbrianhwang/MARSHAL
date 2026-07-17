"""Build the canonical Town03 MARSHAL closed-loop route.

The route is defined as a sequence of ANCHORS (manually chosen waypoints
the loop must pass through, in order) and the planner fills in the road
network between them using CARLA's GlobalRoutePlanner.

The result is dumped to `route.json` as a list of (x, y, z, lane_width)
waypoints at 1.0 m spacing — reproducible across runs.

Loop overview (~1700 m, counter-clockwise around the fountain):
  A0 start  (13.7, 18.8)     — east of fountain, heading SW into roundabout
  A1 west   (-65, 6)          — through J861 area, west of fountain
  A2 south  (-85, -25)        — south leg after J861
  A3 SE     (40, -30)         — eastward swing back
  A4 east   (50, 10)          — pass through J1205 area
  A5 north  (5, 60)           — through J1736
  A6 back to A0
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from typing import List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                 os.pardir, os.pardir)))


@dataclass
class Anchor:
    name: str
    x: float
    y: float
    note: str = ""


# Counter-clockwise around the fountain through 3-4 surrounding intersections
ANCHORS: List[Anchor] = [
    Anchor("start",   13.7,   18.8, "SP 246, east of fountain"),
    Anchor("J861_W", -85.0,    9.0, "west exit, into junction 861"),
    Anchor("S_leg",  -70.0,  -50.0, "southbound leg after J861"),
    Anchor("S_loop",  10.0,  -60.0, "south loop"),
    Anchor("J1082",   40.0,  -15.0, "east leg, junction 1082 area"),
    Anchor("J1205",   45.0,   10.0, "junction 1205, east of fountain"),
    Anchor("J1736",    5.0,   55.0, "northbound, junction 1736"),
    Anchor("back",    20.0,   30.0, "approach back to start"),
]

ROUTE_JSON = os.path.abspath(os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir,
    "marshal_bench", "town03_benchmark", "route.json"))
SAMPLE_M = 1.0   # waypoint spacing


def build_route(carla, world_map):
    """Use CARLA's GlobalRoutePlanner to stitch road waypoints between
    each consecutive pair of anchors. Returns a flat list of (x, y, z, yaw)."""
    from marshal_bench.utils.carla_api_compat import ensure_agents_on_path
    ensure_agents_on_path()
    from agents.navigation.global_route_planner import GlobalRoutePlanner

    grp = GlobalRoutePlanner(world_map, SAMPLE_M)
    flat: List[Tuple[float, float, float, float]] = []
    n = len(ANCHORS)
    for i in range(n):
        a = ANCHORS[i]
        b = ANCHORS[(i + 1) % n]
        wa = world_map.get_waypoint(carla.Location(a.x, a.y, 0.5))
        wb = world_map.get_waypoint(carla.Location(b.x, b.y, 0.5))
        path = grp.trace_route(wa.transform.location, wb.transform.location)
        for wp, _opt in path:
            loc = wp.transform.location
            yaw = wp.transform.rotation.yaw
            flat.append((round(loc.x, 2), round(loc.y, 2), round(loc.z, 2),
                          round(yaw, 1)))
    return flat


def save_route(waypoints):
    os.makedirs(os.path.dirname(ROUTE_JSON), exist_ok=True)
    out = {
        "map": "Town03",
        "anchors": [a.__dict__ for a in ANCHORS],
        "sample_m": SAMPLE_M,
        "n_waypoints": len(waypoints),
        "length_m": sum(
            math.hypot(waypoints[i + 1][0] - waypoints[i][0],
                       waypoints[i + 1][1] - waypoints[i][1])
            for i in range(len(waypoints) - 1)),
        "waypoints": waypoints,
    }
    with open(ROUTE_JSON, "w") as fh:
        json.dump(out, fh, indent=2)
    return out


def main() -> int:
    from marshal_bench.utils.carla_api_compat import import_carla
    carla = import_carla()
    c = carla.Client("127.0.0.1", 2000); c.set_timeout(30.0)
    w = c.get_world()
    if "Town03" not in w.get_map().name:
        print("loading Town03 ...")
        w = c.load_world("Town03")
    print(f"map: {w.get_map().name}")
    wps = build_route(carla, w.get_map())
    out = save_route(wps)
    print(f"wrote {ROUTE_JSON}")
    print(f"  anchors={len(out['anchors'])}  waypoints={out['n_waypoints']}  "
          f"length={out['length_m']:.0f} m")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
