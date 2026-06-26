"""Capture clean close-up figures of EVERY MARSHAL hand signal (new US poses).

Reuses the framing of ``capture_officer_photo.py`` (camera ~3 m in front, FOV 55,
960x720) but loops over all gesture IDs and also snaps the plain-clothes civilian
GO wave, so the README ``docs/figures/gestures/*.png`` can be refreshed to the
corrected US-traffic-signal poses.

Outputs to ``outputs/gesture_figures/``:
  stop.png proceed.png left.png right.png slow.png hold.png civilian_go.png

Run only when CARLA is FREE (it enters synchronous mode):
    conda run -n marshal --no-capture-output python scripts/_capture_gesture_figures.py

Reuses the currently-loaded town (no load_world). Cyclic gestures (PROCEED, SLOW)
are ticked to the phase where the motion is most legible before the snap.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.actors.traffic_officer import TrafficOfficer  # noqa: E402
from marshal_bench.actors.gesture_engine import GestureID  # noqa: E402

log = logging.getLogger("marshal_bench.scripts.capture_gesture_figures")

# gesture -> (GestureID, settle-seconds to reach the most legible phase)
_GESTURES = [
    ("stop", GestureID.STOP, 0.6),
    ("proceed", GestureID.PROCEED, 0.8),   # cyclic 1.6s -> mid (inward beckon)
    ("left", GestureID.LEFT, 0.6),
    ("right", GestureID.RIGHT, 0.6),
    ("slow", GestureID.SLOW, 0.6),         # cyclic -> downward pat extreme
    ("hold", GestureID.HOLD, 0.6),
]


def _spawn_transform(world, carla):
    sps = list(world.get_map().get_spawn_points())
    if not sps:
        raise RuntimeError("No spawn points in the current town.")
    base = sps[0]
    fwd = base.get_forward_vector()
    loc = carla.Location(base.location.x + fwd.x * 6.0,
                         base.location.y + fwd.y * 6.0,
                         base.location.z + 0.1)
    return carla.Transform(loc, base.rotation)


def _cam_in_front(otf, carla, distance, height):
    fwd = otf.get_forward_vector()
    loc = carla.Location(otf.location.x + fwd.x * distance,
                         otf.location.y + fwd.y * distance,
                         otf.location.z + height)
    rot = carla.Rotation(pitch=-8.0, yaw=otf.rotation.yaw + 180.0, roll=0.0)
    return carla.Transform(loc, rot)


def _enter_sync(world, carla):
    prev = world.get_settings()
    world.apply_settings(carla.WorldSettings(
        synchronous_mode=True, fixed_delta_seconds=1.0 / 20.0, no_rendering_mode=False))
    return prev


def _snap(world, camera, frames, label, settle_s, officer=None, sim_t0=0.0):
    """Apply ticks (driving the officer gesture) then grab one frame as `label`."""
    frames.pop(label, None)
    camera.listen(lambda img: frames.__setitem__(label, img))
    n = max(8, int(settle_s * 20) + 8)
    sim_t = sim_t0
    for _ in range(n):
        world.tick()
        sim_t += 1.0 / 20.0
        if officer is not None:
            officer.tick(sim_t)
    for _ in range(40):
        if label in frames:
            break
        time.sleep(0.05)
    camera.stop()
    return sim_t


def _capture_actor(world, carla, args, gestures, blueprint_id, authorized, out_names):
    """Spawn one walker, snap each gesture in `gestures`. out_names maps gesture-key->file stem."""
    officer = TrafficOfficer(
        world, _spawn_transform(world, carla),
        authority_type="police" if authorized else "civilian",
        authorized=authorized, blueprint_id=blueprint_id,
        use_debug_visuals=False, use_skeleton=True, fixed_location=True)
    officer.spawn()
    for _ in range(8):
        world.tick()
    otf = officer.get_transform()
    cam_bp = world.get_blueprint_library().find("sensor.camera.rgb")
    cam_bp.set_attribute("image_size_x", str(args.width))
    cam_bp.set_attribute("image_size_y", str(args.height))
    cam_bp.set_attribute("fov", str(args.fov))
    camera = world.spawn_actor(cam_bp, _cam_in_front(otf, carla, args.camera_distance, args.camera_height))
    frames: dict = {}
    try:
        for key, gid, settle in gestures:
            officer.set_gesture(gid, onset_time=0.0, duration=12.0)
            _snap(world, camera, frames, out_names[key], settle, officer=officer)
            log.info("captured %s", out_names[key])
        for label, img in frames.items():
            img.save_to_disk(os.path.join(args.out_dir, f"{label}.png"))
    finally:
        try:
            camera.destroy()
        except Exception:
            pass
        try:
            officer.destroy()
        except Exception:
            pass
    return list(frames)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2000)
    p.add_argument("--out-dir", default=os.path.join(_REPO_ROOT, "outputs", "gesture_figures"))
    p.add_argument("--width", type=int, default=960)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fov", type=float, default=55.0)
    p.add_argument("--camera-distance", type=float, default=3.0)
    p.add_argument("--camera-height", type=float, default=1.5)
    p.add_argument("--civilian-blueprint", default=None,
                   help="walker blueprint for the unauthorized civilian GO shot.")
    args = p.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    carla = import_carla()
    client = carla.Client(args.host, args.port)
    client.set_timeout(15.0)
    world = client.get_world()
    log.info("Connected, map=%s", world.get_map().name)
    prev = _enter_sync(world, carla)
    try:
        # police officer: all six signals
        names = {k: k for k, _, _ in _GESTURES}
        done = _capture_actor(world, carla, args, _GESTURES, None, True, names)
        log.info("officer figures: %s", done)
        # plain-clothes civilian: the GO wave only -> civilian_go.png
        civ = [("proceed", GestureID.PROCEED, 0.8)]
        done2 = _capture_actor(world, carla, args, civ, args.civilian_blueprint, False,
                               {"proceed": "civilian_go"})
        log.info("civilian figure: %s", done2)
        print("=" * 56)
        for f in sorted(os.listdir(args.out_dir)):
            print("  ", os.path.join(args.out_dir, f))
        print("=" * 56)
        return 0
    finally:
        try:
            world.apply_settings(prev)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
