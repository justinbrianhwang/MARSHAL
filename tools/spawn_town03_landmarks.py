"""Spawn the Town03Landmarks prop and frame the spectator camera so all
3 panels are visible at once.

The mesh is baked at LOCAL origin (0,0,0); the spawn transform locates
the prop at the actual Town03 fountain centre, so re-positioning needs
only a re-run of this script (not a re-import).

Run on a live CARLA server with Town03 loaded.
"""
from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                 os.pardir)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402

# Town03 fountain centre (CARLA frame). The first estimate from the
# spectator gaze (-7, 11) was off-centre by ~13 m; corrected here using a
# top-down screenshot to locate the sculpture's centroid.
CX, CY = 0.0, 4.0

BP_ID = "static.prop.town03landmarks"


def _find_bp(world, carla):
    bl = world.get_blueprint_library()
    direct = bl.find(BP_ID) if BP_ID in [b.id for b in bl.filter("*town03landmarks*")] else None
    if direct is not None:
        return direct
    cands = list(bl.filter("*town03landmarks*"))
    if cands:
        return cands[0]
    cands = list(bl.filter("*Town03Landmarks*"))
    if cands:
        return cands[0]
    return None


def main() -> int:
    carla = import_carla()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(60.0)
    world = client.get_world()
    m = world.get_map()
    print(f"map: {m.name}")

    bp = _find_bp(world, carla)
    if bp is None:
        print("ERROR: blueprint static.prop.town03landmarks not found.")
        print("blueprints matching '*landmark*':",
              [b.id for b in world.get_blueprint_library().filter("*landmark*")])
        return 1
    print(f"found blueprint: {bp.id}")

    # Destroy any existing landmark first.
    for a in world.get_actors():
        if "town03landmarks" in a.type_id.lower():
            print(f"  destroying existing actor id={a.id}")
            a.destroy()

    # Mesh is origin-centered → spawn transform places it at the fountain.
    tr = carla.Transform(carla.Location(CX, CY, 0.0), carla.Rotation())
    actor = world.try_spawn_actor(bp, tr)
    if actor is None:
        print("ERROR: spawn returned None.")
        return 2
    print(f"spawned actor id={actor.id} at fountain ({CX}, {CY}, 0)")

    # Frame spectator to see all 3 signposts from outside the roundabout.
    # Vantage: SW direction, elevated, looking back at the roundabout centre.
    cam_dist = 38.0       # m from centre
    cam_height = 12.0     # m above ground
    cam_angle_deg = 225.0  # SW
    a = math.radians(cam_angle_deg)
    cx = CX + cam_dist * math.cos(a)
    cy = CY + cam_dist * math.sin(a)
    cz = cam_height
    dx, dy, dz = CX - cx, CY - cy, 3.0 - cz
    yaw = math.degrees(math.atan2(dy, dx))
    pitch = math.degrees(math.atan2(dz, math.hypot(dx, dy)))

    spec = world.get_spectator()
    spec.set_transform(carla.Transform(carla.Location(cx, cy, cz),
                                       carla.Rotation(pitch=pitch, yaw=yaw)))
    print(f"spectator -> ({cx:.1f}, {cy:.1f}, {cz:.1f})  "
          f"pitch={pitch:.0f} yaw={yaw:.0f}")

    time.sleep(0.5)
    print("done. look at the editor viewport.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
