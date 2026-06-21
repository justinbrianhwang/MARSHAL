"""Validate a custom Police walker after UE import + WalkerFactory registration.

Run in the `marshal` env with CARLA (PIE) up on Town03. Finds candidate custom
walker blueprints, spawns one, confirms get_bones() exposes the limb bones our
gesture engine needs (Mixamo or crl_*), and applies a STOP pose via set_bones.

    C:/.../envs/marshal/python.exe scripts/_validate_police_walker.py [--blueprint walker.pedestrian.XX]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from marshal_bench.utils.carla_api_compat import import_carla
from marshal_bench.actors.gesture_engine import GestureEngine, GestureState, GestureID, infer_upper_limb_bones


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blueprint", default=None,
                    help="explicit walker bp id; else lists all pedestrian bps")
    args = ap.parse_args()

    carla = import_carla()
    c = carla.Client("127.0.0.1", 2000); c.set_timeout(20.0)
    w = c.get_world()
    bl = w.get_blueprint_library()

    peds = [b.id for b in bl.filter("walker.pedestrian.*")]
    print(f"pedestrian blueprints found: {len(peds)}")
    print("  ", peds)

    bp_id = args.blueprint
    if bp_id is None:
        # the custom one is usually the highest index (added last)
        bp_id = sorted(peds)[-1]
        print(f"\n(no --blueprint given; trying highest-index: {bp_id})")

    bp = bl.find(bp_id)
    sp = w.get_map().get_spawn_points()[0]
    walker = w.try_spawn_actor(bp, sp)
    if walker is None:
        print(f"FAIL: could not spawn {bp_id}")
        return 1
    print(f"spawned {bp_id} id={walker.id}")
    try:
        w.tick() if w.get_settings().synchronous_mode else w.wait_for_tick()
        bones = walker.get_bones()
        names = [b.name for b in bones.bone_transforms]
        print(f"bones: {len(names)}")
        mapping = infer_upper_limb_bones(names)
        print("gesture-engine bone mapping:")
        for k, v in mapping.items():
            print(f"  {k:14s} -> {v}")
        critical = ["r_upper_arm", "r_forearm", "r_hand"]
        ok = all(mapping.get(k) for k in critical)
        print("\nset_bones-drivable:", "YES" if ok else "NO (gestures will not animate)")
        if ok:
            eng = GestureEngine()
            eng.apply_gesture(walker, GestureState(GestureID.STOP, onset_time=0.0, duration=1.0), sim_time=1.0)
            print("applied STOP pose OK -> visually check the right arm is raised.")
    finally:
        try:
            walker.destroy()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
