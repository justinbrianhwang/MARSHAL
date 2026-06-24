"""Shared helpers for legacy CARLA vision-policy adapters."""
from __future__ import annotations

import csv
import logging
import math
import os
import sys
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np

from marshal_bench.controllers.base import EpisodeController
from marshal_bench.controllers.lane_route import (
    build_lane_follow_plan,
    location_to_gps,
    read_latlon_ref,
)
from marshal_bench.utils.carla_api_compat import ensure_agents_on_path

log = logging.getLogger("marshal_bench.controllers.legacy_vision")

_GPS_SCALE = np.array([111324.60662786, 111319.490945], dtype=np.float64)


def find_workspace_root(marker: Iterable[str]) -> Path:
    here = Path(__file__).resolve()
    rel = Path(*marker)
    for parent in (here.parent, *here.parents):
        if (parent / rel).exists():
            return parent
    return here.parents[2]


def extract_state_dict(obj: Any) -> Any:
    if isinstance(obj, OrderedDict):
        return obj
    if isinstance(obj, dict):
        for key in ("state_dict", "model", "net", "encoder", "decoder"):
            value = obj.get(key)
            if isinstance(value, (dict, OrderedDict)):
                return value
    return obj


def strip_module_prefix(state: Any) -> Any:
    if not isinstance(state, (dict, OrderedDict)):
        return state
    if state and all(str(k).startswith("module.") for k in state.keys()):
        return OrderedDict((str(k)[7:], v) for k, v in state.items())
    return state


def load_checkpoint_with_counts(torch: Any, module: Any, checkpoint: str, device: Any) -> Dict[str, Any]:
    try:
        raw = torch.load(checkpoint, map_location=device, weights_only=False)
    except TypeError:
        raw = torch.load(checkpoint, map_location=device)
    state = strip_module_prefix(extract_state_dict(raw))
    incompatible = module.load_state_dict(state, strict=False)
    missing = list(getattr(incompatible, "missing_keys", []) or [])
    unexpected = list(getattr(incompatible, "unexpected_keys", []) or [])
    return {
        "checkpoint": str(checkpoint),
        "state_dict_keys": len(state) if isinstance(state, (dict, OrderedDict)) else None,
        "missing": len(missing),
        "unexpected": len(unexpected),
        "missing_keys": missing[:8],
        "unexpected_keys": unexpected[:8],
        "full_load": len(missing) == 0 and len(unexpected) == 0,
    }


def patch_torchvision_pretrained_download(model_names: Iterable[str]) -> Dict[str, bool]:
    import torchvision.models as tv_models

    patched: Dict[str, bool] = {}
    for name in model_names:
        original = getattr(tv_models, name, None)
        if original is None:
            patched[name] = False
            continue

        def _make_wrapper(fn: Any) -> Any:
            def _wrapper(pretrained: bool = False, progress: bool = True, **kwargs: Any) -> Any:
                kwargs.pop("weights", None)
                try:
                    return fn(pretrained=False, progress=progress, **kwargs)
                except TypeError:
                    return fn(weights=None, progress=progress, **kwargs)

            return _wrapper

        setattr(tv_models, name, _make_wrapper(original))
        patched[name] = True
    return patched


def scale_and_crop_rgb(rgb: np.ndarray, *, scale: int = 1, crop: int = 256) -> np.ndarray:
    from PIL import Image

    image = Image.fromarray(rgb)
    width = int(image.width // max(1, int(scale)))
    height = int(image.height // max(1, int(scale)))
    if width != image.width or height != image.height:
        image = image.resize((width, height))
    arr = np.asarray(image)
    start_y = height // 2 - crop // 2
    start_x = width // 2 - crop // 2
    cropped = arr[start_y:start_y + crop, start_x:start_x + crop]
    return np.transpose(cropped, (2, 0, 1)).copy()


class LegacyVisionControllerBase(EpisodeController):
    """Common camera, route, trace, and debug plumbing for old AV models."""

    name = "legacy_vision"
    track = "B"
    camera_keys: tuple[str, ...] = ("rgb",)
    use_imu: bool = False

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.mcfg = dict(self.config.get(self.name) or {})

        self.world = None
        self.ego = None
        self.carla = None
        self._road_option = None
        self._lat_ref = 42.0
        self._lon_ref = 2.0
        self._route: list[tuple[Any, Any]] = []
        self._route_end = None
        self._last_route_update_t = -1e9

        self.torch = None
        self.device = None
        self.net = None
        self.load_info: dict[str, Any] = {}
        self.precision = "fp32"

        self._sensor_lock = threading.Lock()
        self._latest: dict[str, tuple[int, Any]] = {}
        self._sensor_actors: list[Any] = []

        self.sim_dt = float(self.mcfg.get("sim_dt", 0.05))
        self.sensor_timeout_s = float(self.mcfg.get("sensor_timeout_s", 0.75))
        self.route_horizon_m = float(self.mcfg.get("route_horizon_m", 120.0))
        self.route_step_m = float(self.mcfg.get("route_step_m", 1.0))
        self.route_refresh_distance_m = float(self.mcfg.get("route_refresh_distance_m", 30.0))
        self.route_refresh_period_s = float(self.mcfg.get("route_refresh_period_s", 1.0))
        self.lookahead_m = float(self.mcfg.get("lookahead_m", 4.0))

        self._step_count = 0
        self._inference_count = 0
        self._last_control = None
        self._last_metadata: dict[str, Any] = {}

        self._logger = self.config.get("_episode_logger")
        self._debug_dir = self._resolve_debug_dir()
        self._trace_fh = None
        self._trace_writer = None
        self._debug_saved = 0
        self._debug_save_every_n = int(self.mcfg.get("save_debug_every_n", 20))
        self._debug_max_frames = int(self.mcfg.get("max_debug_frames", 8))
        self._log_every_n = int(self.mcfg.get("log_every_n", 10))

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------
    def _setup_common(self, world: Any, ego: Any, carla: Any) -> None:
        self.world = world
        self.ego = ego
        self.carla = carla
        self._prepare_debug_outputs()
        ensure_agents_on_path()
        try:
            from agents.navigation.local_planner import RoadOption

            self._road_option = RoadOption.LANEFOLLOW
        except Exception:
            self._road_option = None
        self._lat_ref, self._lon_ref = read_latlon_ref(world)

    def _prepare_import_paths(self, src_root: str, *, include_leaderboard: bool = False) -> None:
        src = self._resolve_path(src_root)
        if src not in sys.path:
            sys.path.insert(0, src)
        if include_leaderboard:
            leaderboard = str(Path(src) / "leaderboard")
            if leaderboard not in sys.path:
                sys.path.insert(0, leaderboard)

    def teardown(self) -> None:
        for sensor in reversed(self._sensor_actors):
            try:
                sensor.stop()
            except Exception:
                pass
            try:
                sensor.destroy()
            except Exception as exc:  # noqa: BLE001
                log.debug("%s sensor destroy failed: %s", self.name, exc)
        self._sensor_actors.clear()
        with self._sensor_lock:
            self._latest.clear()
        if self._trace_fh is not None:
            try:
                self._trace_fh.close()
            except Exception:
                pass
        self._trace_fh = None
        self._trace_writer = None
        try:
            if self.torch is not None and str(self.device).startswith("cuda"):
                self.torch.cuda.empty_cache()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Route helpers
    # ------------------------------------------------------------------
    def _refresh_route(self, sim_time: float) -> None:
        plan = build_lane_follow_plan(
            self.world,
            self.ego,
            self.carla,
            road_option=self._road_option,
            horizon_m=self.route_horizon_m,
            step_m=self.route_step_m,
            lat_ref=self._lat_ref,
            lon_ref=self._lon_ref,
        )
        self._route = plan.world_plan
        self._route_end = plan.route_end
        self._last_route_update_t = float(sim_time)
        end = self._route_end
        self._log_event(
            f"{self.name}_route",
            t=sim_time,
            waypoints=len(self._route),
            end=(
                {"x": float(end.x), "y": float(end.y), "z": float(end.z)}
                if end is not None
                else None
            ),
            source="map_waypoints_lane_follow",
            road_option="LANEFOLLOW",
        )

    def _maybe_refresh_route(self, sim_time: float) -> None:
        if not self._route:
            self._refresh_route(sim_time)
            return
        if sim_time - self._last_route_update_t < self.route_refresh_period_s:
            return
        try:
            distance_to_end = self.ego.get_location().distance(self._route_end)
        except Exception:
            distance_to_end = 0.0
        if distance_to_end <= self.route_refresh_distance_m:
            try:
                self._refresh_route(sim_time)
            except Exception as exc:  # noqa: BLE001
                log.debug("%s route refresh failed: %s", self.name, exc)

    def _route_command(self) -> int:
        if not self._route:
            self._refresh_route(0.0)
        option = self._route[0][1] if self._route else self._road_option
        value = getattr(option, "value", None)
        try:
            return int(value)
        except Exception:
            return 4

    def _target_location(self) -> Any:
        if not self._route:
            self._refresh_route(0.0)
        ego_tf = self.ego.get_transform()
        loc = ego_tf.location
        fwd = ego_tf.get_forward_vector()
        target = None
        for transform, _option in self._route:
            tloc = transform.location
            dx = float(tloc.x - loc.x)
            dy = float(tloc.y - loc.y)
            ahead = dx * float(fwd.x) + dy * float(fwd.y)
            if ahead < -1.0:
                continue
            if math.hypot(dx, dy) >= self.lookahead_m:
                target = tloc
                break
        if target is None:
            target = self._route[-1][0].location
        return target

    def _target_point(self, compass: float) -> tuple[float, float]:
        ego_loc = self.ego.get_location()
        target_loc = self._target_location()
        pos = self._planner_position(ego_loc)
        next_wp = self._planner_position(target_loc)
        theta = float(compass) + np.pi / 2.0
        rot = np.array(
            [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]],
            dtype=np.float64,
        )
        local = rot.T.dot(next_wp - pos)
        return float(local[0]), float(local[1])

    def _planner_position(self, location: Any) -> np.ndarray:
        gps = location_to_gps(location, self._lat_ref, self._lon_ref)
        return np.array([gps["lat"], gps["lon"]], dtype=np.float64) * _GPS_SCALE

    # ------------------------------------------------------------------
    # Sensors
    # ------------------------------------------------------------------
    def _attach_sensors(self) -> None:
        world = self.world
        ego = self.ego
        carla = self.carla
        if world is None or ego is None or carla is None:
            raise RuntimeError("world, ego, and carla must be available before sensors")
        bp_lib = world.get_blueprint_library()
        for sensor_id, tf_values in self._camera_specs():
            bp = bp_lib.find("sensor.camera.rgb")
            bp.set_attribute("image_size_x", "400")
            bp.set_attribute("image_size_y", "300")
            bp.set_attribute("fov", "100")
            if bp.has_attribute("sensor_tick"):
                bp.set_attribute("sensor_tick", str(self.sim_dt))
            camera = world.spawn_actor(bp, self._transform_from_tuple(tf_values), attach_to=ego)
            camera.listen(self._make_camera_callback(sensor_id))
            self._sensor_actors.append(camera)
        if self.use_imu:
            bp = bp_lib.find("sensor.other.imu")
            if bp.has_attribute("sensor_tick"):
                bp.set_attribute("sensor_tick", str(self.sim_dt))
            imu = world.spawn_actor(
                bp,
                self.carla.Transform(self.carla.Location(0.0, 0.0, 0.0), self.carla.Rotation()),
                attach_to=ego,
            )
            imu.listen(self._imu_callback)
            self._sensor_actors.append(imu)

    def _camera_specs(self) -> tuple[tuple[str, tuple[float, float, float, float, float, float]], ...]:
        specs = {
            "rgb": (1.3, 0.0, 2.3, 0.0, 0.0, 0.0),
            "rgb_left": (1.3, 0.0, 2.3, 0.0, 0.0, -60.0),
            "rgb_right": (1.3, 0.0, 2.3, 0.0, 0.0, 60.0),
        }
        return tuple((key, specs[key]) for key in self.camera_keys)

    def _camera_specs_for_log(self) -> list[dict[str, Any]]:
        return [
            {
                "id": sensor_id,
                "transform": values,
                "width": 400,
                "height": 300,
                "fov": 100,
            }
            for sensor_id, values in self._camera_specs()
        ]

    def _make_camera_callback(self, sensor_id: str) -> Any:
        def _callback(image: Any) -> None:
            array = np.frombuffer(image.raw_data, dtype=np.uint8)
            bgra = array.reshape((image.height, image.width, 4))
            rgb = bgra[:, :, :3][:, :, ::-1].copy()
            with self._sensor_lock:
                self._latest[sensor_id] = (int(image.frame), rgb)

        return _callback

    def _imu_callback(self, measurement: Any) -> None:
        compass = float(getattr(measurement, "compass", 0.0) or 0.0)
        if not math.isfinite(compass):
            compass = 0.0
        with self._sensor_lock:
            self._latest["imu"] = (int(measurement.frame), compass)

    def _required_sensor_keys(self) -> tuple[str, ...]:
        return self.camera_keys + (("imu",) if self.use_imu else ())

    def _wait_for_synced_sensors(self, frame: Optional[int], timeout_s: float) -> None:
        if frame is None:
            return
        deadline = time.monotonic() + max(0.0, timeout_s)
        keys = self._required_sensor_keys()
        while True:
            with self._sensor_lock:
                synced = all(key in self._latest and self._latest[key][0] >= frame for key in keys)
            if synced or time.monotonic() >= deadline:
                return
            time.sleep(0.001)

    def _latest_synced_sensors(self, frame: Optional[int]) -> Optional[dict[str, tuple[int, Any]]]:
        keys = self._required_sensor_keys()
        with self._sensor_lock:
            if any(key not in self._latest for key in keys):
                return None
            if frame is not None and any(self._latest[key][0] < frame for key in keys):
                return None
            return {key: self._latest[key] for key in keys}

    # ------------------------------------------------------------------
    # Debug / trace
    # ------------------------------------------------------------------
    def _resolve_debug_dir(self) -> Optional[str]:
        raw = self.mcfg.get("debug_dir") or os.environ.get(f"MARSHAL_{self.name.upper()}_DEBUG_DIR")
        if not raw:
            return None
        return self._resolve_path(raw)

    def _prepare_debug_outputs(self) -> None:
        if not self._debug_dir:
            return
        os.makedirs(self._debug_dir, exist_ok=True)
        self._trace_fh = open(
            os.path.join(self._debug_dir, f"{self.name}_trace.csv"),
            "w",
            newline="",
            encoding="utf-8",
        )
        self._trace_writer = csv.DictWriter(
            self._trace_fh,
            fieldnames=[
                "sim_time",
                "world_frame",
                "mode",
                "speed_mps",
                "throttle",
                "brake",
                "steer",
                "rgb_frame",
                "rgb_left_frame",
                "rgb_right_frame",
                "imu_frame",
                "command",
                "target_x",
                "target_y",
                "stale",
                "error",
            ],
        )
        self._trace_writer.writeheader()
        self._trace_fh.flush()

    def _write_trace(
        self,
        *,
        sim_time: float,
        frame: Optional[int],
        mode: str,
        control: Any,
        speed: float,
        input_frames: dict[str, int],
        stale: bool,
        command: Optional[int] = None,
        target_point: Any = None,
        error: str = "",
    ) -> None:
        throttle = float(getattr(control, "throttle", 0.0) or 0.0)
        brake = float(getattr(control, "brake", 0.0) or 0.0)
        steer = float(getattr(control, "steer", 0.0) or 0.0)
        try:
            tx, ty = target_point if target_point is not None else (0.0, 0.0)
        except Exception:
            tx, ty = 0.0, 0.0
        if self._trace_writer is not None:
            self._trace_writer.writerow(
                {
                    "sim_time": round(float(sim_time), 3),
                    "world_frame": "" if frame is None else int(frame),
                    "mode": mode,
                    "speed_mps": round(float(speed), 4),
                    "throttle": round(throttle, 5),
                    "brake": round(brake, 5),
                    "steer": round(steer, 5),
                    "rgb_frame": input_frames.get("rgb", ""),
                    "rgb_left_frame": input_frames.get("rgb_left", ""),
                    "rgb_right_frame": input_frames.get("rgb_right", ""),
                    "imu_frame": input_frames.get("imu", ""),
                    "command": "" if command is None else int(command),
                    "target_x": round(float(tx), 4),
                    "target_y": round(float(ty), 4),
                    "stale": bool(stale),
                    "error": str(error)[:200],
                }
            )
            self._trace_fh.flush()
        if self._log_every_n > 0 and (
            self._step_count <= 5 or (self._step_count % self._log_every_n) == 0
        ):
            log.info(
                "%s t=%.2f frame=%s mode=%s v=%.2f thr=%.3f brk=%.3f steer=%.3f stale=%s err=%s",
                self.name,
                sim_time,
                frame,
                mode,
                speed,
                throttle,
                brake,
                steer,
                stale,
                error or "-",
            )

    def _maybe_save_debug_images(
        self,
        crops: dict[str, np.ndarray],
        *,
        sim_time: float,
        frame: Optional[int],
        control: Any,
        target_point: Any = None,
    ) -> None:
        del sim_time, frame, control, target_point
        if not self._debug_dir:
            return
        if self._debug_saved >= self._debug_max_frames:
            return
        if self._debug_save_every_n > 0 and (
            self._inference_count > 1
            and ((self._inference_count - 1) % self._debug_save_every_n) != 0
        ):
            return
        try:
            from PIL import Image

            front = crops.get("rgb")
            if front is not None:
                Image.fromarray(self._chw_to_hwc_uint8(front)).save(
                    os.path.join(self._debug_dir, f"front_{self._debug_saved:03d}.png")
                )
            if all(key in crops for key in ("rgb_left", "rgb", "rgb_right")):
                stitched = np.concatenate(
                    [
                        self._chw_to_hwc_uint8(crops["rgb_left"]),
                        self._chw_to_hwc_uint8(crops["rgb"]),
                        self._chw_to_hwc_uint8(crops["rgb_right"]),
                    ],
                    axis=1,
                )
                Image.fromarray(stitched).save(
                    os.path.join(self._debug_dir, f"input_{self._debug_saved:03d}.png")
                )
            elif front is not None:
                Image.fromarray(self._chw_to_hwc_uint8(front)).save(
                    os.path.join(self._debug_dir, f"input_{self._debug_saved:03d}.png")
                )
            self._debug_saved += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("%s debug artifact save failed: %s", self.name, exc)

    @staticmethod
    def _chw_to_hwc_uint8(chw: np.ndarray) -> np.ndarray:
        arr = np.asarray(chw)
        if arr.ndim == 3 and arr.shape[0] in (1, 3, 4):
            arr = np.transpose(arr[:3], (1, 2, 0))
        return np.clip(arr, 0, 255).astype(np.uint8)

    def _log_event(self, name: str, **payload: Any) -> None:
        logger = self._logger
        if logger is not None and hasattr(logger, "log_event"):
            try:
                logger.log_event(name, **payload)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def _resolve_path(self, path: Any) -> str:
        p = Path(os.fspath(path))
        if not p.is_absolute():
            p = self.workspace_root / p
        return str(p.resolve())

    def _transform_from_tuple(self, values: tuple[float, float, float, float, float, float]) -> Any:
        x, y, z, roll, pitch, yaw = values
        return self.carla.Transform(
            self.carla.Location(x=float(x), y=float(y), z=float(z)),
            self.carla.Rotation(roll=float(roll), pitch=float(pitch), yaw=float(yaw)),
        )

    def _current_world_frame(self) -> Optional[int]:
        try:
            return int(self.world.get_snapshot().frame)
        except Exception:
            return None

    def _ego_speed_mps(self) -> float:
        try:
            velocity = self.ego.get_velocity()
            return math.sqrt(velocity.x * velocity.x + velocity.y * velocity.y + velocity.z * velocity.z)
        except Exception:
            return 0.0

    def _make_control(self, steer: Any, throttle: Any, brake: Any) -> Any:
        control = self.carla.VehicleControl()
        control.steer = float(np.clip(float(steer), -1.0, 1.0))
        control.throttle = float(np.clip(float(throttle), 0.0, 1.0))
        control.brake = float(np.clip(float(brake), 0.0, 1.0))
        return control

    def _fallback_control(self, brake: float = 0.0) -> Any:
        control = self.carla.VehicleControl()
        control.throttle = 0.0
        control.brake = float(brake)
        control.steer = 0.0
        return control

    def _invalid_control(self) -> Any:
        control = self.carla.VehicleControl()
        control.throttle = float("nan")
        control.brake = 0.0
        control.steer = 0.0
        return control

    def _copy_control(self, control: Any) -> Any:
        if control is None or self.carla is None:
            return None
        out = self.carla.VehicleControl()
        out.throttle = float(getattr(control, "throttle", 0.0) or 0.0)
        out.brake = float(getattr(control, "brake", 0.0) or 0.0)
        out.steer = float(getattr(control, "steer", 0.0) or 0.0)
        out.hand_brake = bool(getattr(control, "hand_brake", False))
        out.reverse = bool(getattr(control, "reverse", False))
        return out

    @staticmethod
    def _control_is_finite(control: Any) -> bool:
        values = (
            getattr(control, "throttle", 0.0),
            getattr(control, "brake", 0.0),
            getattr(control, "steer", 0.0),
        )
        return all(math.isfinite(float(v)) for v in values)

    def _input_frames(self, samples: dict[str, tuple[int, Any]]) -> dict[str, int]:
        return {key: int(value[0]) for key, value in samples.items()}

    def _stale(self, frame: Optional[int], input_frames: dict[str, int]) -> bool:
        return bool(frame is not None and any(sample_frame < frame for sample_frame in input_frames.values()))

    def _tensor_from_crop(self, crop: np.ndarray) -> Any:
        tensor = self.torch.from_numpy(crop).unsqueeze(0)
        return tensor.to(self.device, dtype=self.torch.float32)

    @staticmethod
    def _metadata_jsonable(metadata: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in (metadata or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
            elif isinstance(value, (list, tuple)):
                out[key] = [v.item() if isinstance(v, np.generic) else v for v in value]
            elif isinstance(value, np.generic):
                out[key] = value.item()
            else:
                out[key] = str(value)
        return out

    @property
    def workspace_root(self) -> Path:
        return find_workspace_root(("Models",))


__all__ = [
    "LegacyVisionControllerBase",
    "find_workspace_root",
    "load_checkpoint_with_counts",
    "patch_torchvision_pretrained_download",
    "scale_and_crop_rgb",
]
