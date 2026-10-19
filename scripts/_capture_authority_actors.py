"""Capture front-camera and external proof images for authority actor variants.

Run with a free CARLA server already running Town03.  This script deliberately
does not start CARLA or change maps.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterator

import numpy as np
from PIL import Image

_THIS_DIR = Path(__file__).resolve().parent
_ROOT = _THIS_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from marshal_bench.scenarios._common import (  # noqa: E402
    _hold_ego_during_spawn_settle,
    _release_ego_after_spawn_settle,
    build_officer,
    officer_transform_on_ego_route,
    spawn_ego,
)
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.utils.traffic_light_utils import (  # noqa: E402
    find_relevant_traffic_light,
    release_traffic_light,
    set_traffic_light_state,
)
from scripts._capture_gesture_figures import _enter_sync  # noqa: E402
from scripts._shared_staging import load_staged_config  # noqa: E402

log = logging.getLogger("marshal_bench.scripts.capture_authority_actors")

FPS = 20
FRONT_CAMERA = {
    "width": 1280,
    "height": 720,
    "fov": 90.0,
    "relative_transform": {
        "location": {"x": 1.4, "y": 0.0, "z": 1.4},
        "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
    },
}
EXTERNAL_CAMERA = {"width": 1280, "height": 720, "fov": 60.0}
CAPTURE_OFFSETS_S = (0.5, 2.0)
_GREEN_SPEC = {
    "config": "marshal_bench/configs/demo_green_stop.yaml",
    "expect": "STOP",
}
VARIANTS = (
    "police_stop",
    "police_proceed",
    "civilian_stop",
    "no_police",
)


@contextmanager
def _officer_condition(value: str | None) -> Iterator[None]:
    key = "MARSHAL_OFFICER_CONDITION"
    old = os.environ.get(key)
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _variant_config(name: str) -> dict:
    condition = {
        "police_stop": None,
        "police_proceed": "proceed",
        "civilian_stop": "civilian_stop",
        "no_police": "absent",
    }.get(name)
    with _officer_condition(condition):
        return load_staged_config(str(_ROOT), "green_stop", _GREEN_SPEC, "capture")


def _xyz(value: Any) -> dict[str, float]:
    return {axis: float(getattr(value, axis)) for axis in ("x", "y", "z")}


def _transform_dict(transform: Any) -> dict[str, dict[str, float]] | None:
    if transform is None:
        return None
    return {
        "location": _xyz(transform.location),
        "rotation": {
            axis: float(getattr(transform.rotation, axis))
            for axis in ("pitch", "yaw", "roll")
        },
    }


def _camera_blueprint(world: Any, spec: dict) -> Any:
    bp = world.get_blueprint_library().find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", str(spec["width"]))
    bp.set_attribute("image_size_y", str(spec["height"]))
    bp.set_attribute("fov", str(spec["fov"]))
    return bp


def _look_at_pitch_yaw(camera_location: Any, target_location: Any) -> tuple[float, float]:
    """Return CARLA pitch/yaw angles that point camera_location at target_location."""
    dx = float(target_location.x) - float(camera_location.x)
    dy = float(target_location.y) - float(camera_location.y)
    dz = float(target_location.z) - float(camera_location.z)
    return (
        math.degrees(math.atan2(dz, math.hypot(dx, dy))),
        math.degrees(math.atan2(dy, dx)),
    )


def _external_camera_transform(focus_tf: Any, carla: Any) -> Any:
    """Place a three-quarter camera in front of the actor and look at its torso."""
    forward = focus_tf.get_forward_vector()
    right = focus_tf.get_right_vector()
    camera_location = carla.Location(
        x=focus_tf.location.x + forward.x * 5.5 + right.x * 2.0,
        y=focus_tf.location.y + forward.y * 5.5 + right.y * 2.0,
        z=focus_tf.location.z + 2.5,
    )
    target_location = carla.Location(
        x=focus_tf.location.x,
        y=focus_tf.location.y,
        z=focus_tf.location.z + 1.0,
    )
    pitch, yaw = _look_at_pitch_yaw(camera_location, target_location)
    return carla.Transform(
        camera_location,
        carla.Rotation(pitch=pitch, yaw=yaw, roll=0.0),
    )


def _spawn_cameras(world: Any, ego: Any, focus_tf: Any, carla: Any) -> tuple[Any, Any]:
    rel = FRONT_CAMERA["relative_transform"]
    front_tf = carla.Transform(
        carla.Location(**rel["location"]), carla.Rotation(**rel["rotation"])
    )
    front = world.spawn_actor(
        _camera_blueprint(world, FRONT_CAMERA), front_tf, attach_to=ego
    )
    external_tf = _external_camera_transform(focus_tf, carla)
    external = world.spawn_actor(
        _camera_blueprint(world, EXTERNAL_CAMERA), external_tf
    )
    return front, external


def _world_road_users(world: Any) -> list[Any]:
    return sorted(
        (
            actor
            for actor in world.get_actors()
            if str(getattr(actor, "type_id", "")).startswith(("vehicle.", "walker."))
        ),
        key=lambda actor: int(getattr(actor, "id", 0)),
    )


def _require_clean_world(world: Any, allow_dirty_world: bool) -> None:
    actors = _world_road_users(world)
    if not actors:
        log.info("world is clear of walkers and vehicles")
        return
    details = ", ".join(
        f"id={getattr(actor, 'id', '?')} type={getattr(actor, 'type_id', '?')}"
        for actor in actors
    )
    if allow_dirty_world:
        log.warning("continuing with existing walkers/vehicles: %s", details)
        return
    raise RuntimeError(
        "capture requires an empty world; found existing walkers/vehicles: "
        f"{details}. Stop them first or pass --allow-dirty-world to override; "
        "no foreign actors were destroyed."
    )


def _listen_latest(camera: Any) -> dict[int, Any]:
    frames: dict[int, Any] = {}

    def receive(image: Any) -> None:
        frames[int(image.frame)] = image
        for frame_id in sorted(frames)[:-4]:
            frames.pop(frame_id, None)

    camera.listen(receive)
    return frames


def _wait_for_frame(frames: dict[int, Any], frame_id: int, label: str) -> Any:
    deadline = time.monotonic() + 5.0
    while frame_id not in frames and time.monotonic() < deadline:
        time.sleep(0.01)
    if frame_id not in frames:
        raise RuntimeError(f"timed out waiting for {label} camera frame {frame_id}")
    return frames[frame_id]


def _project_actor_bbox(actor: Any, camera: Any, width: int, height: int, fov: float):
    """Return a clipped actor bbox in image pixels, using CARLA's pinhole model."""
    if actor is None:
        return None
    try:
        vertices = actor.bounding_box.get_world_vertices(actor.get_transform())
        inverse = np.asarray(camera.get_transform().get_inverse_matrix(), dtype=float)
        focal = width / (2.0 * math.tan(math.radians(fov) / 2.0))
        pixels = []
        for vertex in vertices:
            point = inverse @ np.array([vertex.x, vertex.y, vertex.z, 1.0])
            x_cam, y_cam, z_cam = point[1], -point[2], point[0]
            if z_cam <= 0.05:
                continue
            pixels.append((focal * x_cam / z_cam + width / 2.0,
                           focal * y_cam / z_cam + height / 2.0))
        if not pixels:
            return None
        x1 = max(0, min(width - 1, int(math.floor(min(p[0] for p in pixels)))))
        y1 = max(0, min(height - 1, int(math.floor(min(p[1] for p in pixels)))))
        x2 = max(0, min(width - 1, int(math.ceil(max(p[0] for p in pixels)))))
        y2 = max(0, min(height - 1, int(math.ceil(max(p[1] for p in pixels)))))
        if x2 <= x1 or y2 <= y1:
            return None
        return {"x_min": x1, "y_min": y1, "x_max": x2, "y_max": y2}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not project actor bbox: %s", exc)
        return None


def _save_actor_crop(image: Any, bbox: dict[str, int], path: Path) -> None:
    """Save a 40%-padded, 3x crop from a CARLA BGRA camera image."""
    width = int(image.width)
    height = int(image.height)
    box_width = bbox["x_max"] - bbox["x_min"] + 1
    box_height = bbox["y_max"] - bbox["y_min"] + 1
    pad_x = 0.4 * box_width
    pad_y = 0.4 * box_height
    left = max(0, int(math.floor(bbox["x_min"] - pad_x)))
    top = max(0, int(math.floor(bbox["y_min"] - pad_y)))
    right = min(width, int(math.ceil(bbox["x_max"] + 1 + pad_x)))
    bottom = min(height, int(math.ceil(bbox["y_max"] + 1 + pad_y)))
    bgra = np.frombuffer(image.raw_data, dtype=np.uint8).reshape(height, width, 4)
    rgb = bgra[:, :, :3][:, :, ::-1]
    crop = Image.fromarray(rgb).crop((left, top, right, bottom))
    crop.resize(
        (crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS
    ).save(path)


def _capture_variant(world: Any, carla: Any, name: str, out: Path) -> dict:
    cfg = _variant_config(name)
    ego = officer = front = external = traffic_light = None
    try:
        ego, spawn_tf = spawn_ego(world, cfg["ego"], seed=cfg.get("seed"))
        if getattr(ego, "type_id", None) != "vehicle.tesla.model3":
            raise RuntimeError(
                "vehicle.tesla.model3 is unavailable; refusing a non-matching proof capture"
            )
        _hold_ego_during_spawn_settle(ego)
        for _ in range(int(cfg.get("spawn_settle_ticks", FPS))):
            world.tick()
        _release_ego_after_spawn_settle(ego)
        ego.apply_control(carla.VehicleControl(brake=1.0, hand_brake=True))
        traffic_light = find_relevant_traffic_light(world, ego, distance_threshold=80.0)
        if traffic_light is None:
            log.warning("%s: no relevant traffic light found within 80 m", name)
        else:
            set_traffic_light_state(traffic_light, "Green", freeze=True)
            log.info(
                "%s: pinned traffic light id=%s to Green",
                name,
                getattr(traffic_light, "id", "?"),
            )

        officer_cfg = cfg.get("officer")
        if officer_cfg:
            officer = build_officer(world, spawn_tf, officer_cfg)
            actor = officer.get_actor()
            if actor is None:
                raise RuntimeError(f"{name}: required authority actor did not spawn")
        else:
            placement_cfg = _variant_config("police_stop")["officer"]
            focus_tf = officer_transform_on_ego_route(
                world,
                spawn_tf,
                distance=float(placement_cfg["distance"]),
                lateral=float(placement_cfg["lateral_offset"]),
            )
            actor = None

        # Let a newly spawned walker settle, then base the shot on its actual
        # world transform rather than the configured placement transform.
        for _ in range(2):
            if traffic_light is not None:
                set_traffic_light_state(traffic_light, "Green", freeze=False)
            world.tick()
            if officer is not None:
                officer.tick(0.0)
        if actor is not None:
            focus_tf = actor.get_transform()

        front, external = _spawn_cameras(world, ego, focus_tf, carla)
        front_frames = _listen_latest(front)
        external_frames = _listen_latest(external)
        onset = float((officer_cfg or {}).get("onset_time", 1.0))
        targets = [onset + offset for offset in CAPTURE_OFFSETS_S]
        captures = []
        sim_time = 0.0
        for target in targets:
            while sim_time + 1e-9 < target:
                if traffic_light is not None:
                    set_traffic_light_state(traffic_light, "Green", freeze=False)
                frame_id = int(world.tick())
                sim_time = round(sim_time + 1.0 / FPS, 10)
                if officer is not None:
                    officer.tick(sim_time)
            front_image = _wait_for_frame(front_frames, frame_id, "front")
            external_image = _wait_for_frame(external_frames, frame_id, "external")
            time_tag = f"{target:.2f}".rstrip("0").rstrip(".")
            front_name = f"{name}_t{time_tag}_front.png"
            external_name = f"{name}_t{time_tag}_external.png"
            front_image.save_to_disk(str(out / front_name))
            external_image.save_to_disk(str(out / external_name))
            actor_bbox = _project_actor_bbox(
                actor,
                front,
                FRONT_CAMERA["width"],
                FRONT_CAMERA["height"],
                FRONT_CAMERA["fov"],
            )
            crop_name = None
            if actor_bbox is not None:
                crop_name = f"{name}_t{time_tag}_front_crop.png"
                _save_actor_crop(front_image, actor_bbox, out / crop_name)
            snapshot = world.get_snapshot()
            captures.append({
                "target_sim_time_s": target,
                "actual_sim_time_s": sim_time,
                "world_elapsed_seconds": float(snapshot.timestamp.elapsed_seconds),
                "carla_frame": frame_id,
                "front_file": front_name,
                "front_crop_file": crop_name,
                "external_file": external_name,
                "actor_world_transform": _transform_dict(
                    actor.get_transform() if actor is not None else None
                ),
                "ego_transform": _transform_dict(ego.get_transform()),
                "front_camera_world_transform": _transform_dict(front.get_transform()),
                "external_camera_world_transform": _transform_dict(external.get_transform()),
                "projected_actor_bbox_front_px": actor_bbox,
            })

        metadata = officer.get_metadata() if officer is not None else {
            "authority_valid": False,
            "authority_type": "none",
            "gesture_id": "IDLE",
            "role_name": "none",
            "blueprint_id": None,
        }
        return {
            "variant": name,
            "actor_blueprint_id": metadata.get("blueprint_id"),
            "role_name": metadata.get("role_name"),
            "authority_metadata": metadata,
            "appearance": (officer_cfg or {}).get("appearance"),
            "gesture_id": metadata.get("gesture_id"),
            "actor_world_transform": _transform_dict(
                actor.get_transform() if actor is not None else None
            ),
            "ego_transform": _transform_dict(ego.get_transform()),
            "camera_specs": {
                "front": FRONT_CAMERA,
                "external": {
                    **EXTERNAL_CAMERA,
                    "placement": (
                        "live actor transform + 5.5 m forward + 2.0 m right + "
                        "2.5 m up; look at actor + 1.0 m"
                    ),
                },
            },
            "sim_times_s": targets,
            "captures": captures,
        }
    finally:
        release_traffic_light(traffic_light)
        for camera in (front, external):
            if camera is not None:
                try:
                    camera.stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    camera.destroy()
                except Exception:  # noqa: BLE001
                    pass
        if officer is not None:
            officer.destroy()
        if ego is not None:
            try:
                ego.destroy()
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=2000, type=int)
    parser.add_argument(
        "--allow-dirty-world",
        action="store_true",
        help="capture despite pre-existing walkers or vehicles (never destroys them)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    carla = import_carla()
    client = carla.Client(args.host, args.port)
    client.set_timeout(15.0)
    world = client.get_world()
    if "Town03" not in world.get_map().name:
        raise RuntimeError("green_stop capture requires the running CARLA world to be Town03")
    _require_clean_world(world, args.allow_dirty_world)
    args.out.mkdir(parents=True, exist_ok=True)

    previous_settings = _enter_sync(world, carla)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "station": "green_stop",
        "fps": FPS,
        "host": args.host,
        "port": args.port,
        "variants": [],
    }
    try:
        for name in VARIANTS:
            log.info("capturing %s", name)
            manifest["variants"].append(_capture_variant(world, carla, name, args.out))
    finally:
        world.apply_settings(previous_settings)

    with (args.out / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
    log.info("wrote %s", args.out / "manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
