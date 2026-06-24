"""Track-B TCP controller adapter for MARSHAL.

This wraps the Bench2Drive TCP PyTorch checkpoint as a MARSHAL
``EpisodeController``. The controller owns its three front RGB cameras and
feeds TCP's native panorama/state/target tensors without reading privileged
episode labels.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from marshal_bench.controllers.base import EpisodeController
from marshal_bench.controllers.lane_route import build_lane_follow_plan, read_latlon_ref
from marshal_bench.utils.carla_api_compat import ensure_agents_on_path

log = logging.getLogger("marshal_bench.controllers.tcp")

_TCP_CAMERA_KEYS = ("CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT")
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _find_workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "Models" / "TCP" / "checkpoints" / "tcp_b2d.ckpt").is_file():
            return parent
    return here.parents[2]


_WORKSPACE_ROOT = _find_workspace_root()
_DEFAULT_TCP_ROOT = _WORKSPACE_ROOT / "Models" / "TCP" / "Bench2DriveZoo_tcp_admlp"
_DEFAULT_CKPT = _WORKSPACE_ROOT / "Models" / "TCP" / "checkpoints" / "tcp_b2d.ckpt"


class TCPController(EpisodeController):
    name = "tcp"
    track = "B"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.tcfg = dict(self.config.get("tcp") or {})

        self.world = None
        self.ego = None
        self.carla = None
        self._map = None
        self._road_option = None
        self._route: list[tuple[Any, Any]] = []
        self._route_end = None
        self._last_route_update_t = -1e9
        self._lat_ref = 42.0
        self._lon_ref = 2.0

        self.net = None
        self.torch = None
        self.device = None
        self.load_info: dict[str, Any] = {}

        self._sensor_lock = threading.Lock()
        self._latest_images: dict[str, tuple[int, np.ndarray]] = {}
        self._sensor_actors: list[Any] = []

        self.sim_dt = float(self.tcfg.get("sim_dt", 0.05))
        self.sensor_timeout_s = float(self.tcfg.get("sensor_timeout_s", 0.75))
        self.route_horizon_m = float(self.tcfg.get("route_horizon_m", 120.0))
        self.route_step_m = float(self.tcfg.get("route_step_m", 1.0))
        self.route_refresh_distance_m = float(
            self.tcfg.get("route_refresh_distance_m", 30.0)
        )
        self.route_refresh_period_s = float(self.tcfg.get("route_refresh_period_s", 1.0))
        self.lookahead_m = float(self.tcfg.get("lookahead_m", 4.0))
        self.action_repeat = int(self.tcfg.get("action_repeat", 1))
        self.postprocess_control = bool(self.tcfg.get("postprocess_control", True))

        self._step_count = 0
        self._inference_count = 0
        self._repeat_left = 0
        self._last_control = None
        self._last_metadata: dict[str, Any] = {}

        self._logger = self.config.get("_episode_logger")
        self._debug_dir = self._resolve_debug_dir()
        self._trace_fh = None
        self._trace_writer = None
        self._debug_saved = 0
        self._debug_save_every_n = int(self.tcfg.get("save_debug_every_n", 20))
        self._debug_max_frames = int(self.tcfg.get("max_debug_frames", 8))
        self._log_every_n = int(self.tcfg.get("log_every_n", 10))

    def setup(
        self,
        world: Any,
        ego: Any,
        ground_truth: Dict[str, Any],
        carla: Any,
    ) -> None:
        del ground_truth
        self.world = world
        self.ego = ego
        self.carla = carla
        self._map = world.get_map() if world is not None else None

        self._prepare_debug_outputs()
        self._prepare_import_paths()
        try:
            from agents.navigation.local_planner import RoadOption

            self._road_option = RoadOption.LANEFOLLOW
        except Exception:
            self._road_option = None
        self._lat_ref, self._lon_ref = read_latlon_ref(world)

        self._load_model()
        self._attach_tcp_cameras()
        self._refresh_route(sim_time=0.0)
        self._log_event(
            "tcp_setup",
            checkpoint=str(self._resolve_path(self.tcfg.get("ckpt_path") or _DEFAULT_CKPT)),
            tcp_root=str(self._resolve_path(self.tcfg.get("tcp_root") or _DEFAULT_TCP_ROOT)),
            device=str(self.device),
            load_info=self.load_info,
            sensor_count=len(self._sensor_actors),
            route_waypoints=len(self._route),
            route_source="map_waypoints_lane_follow",
            camera_specs=self._camera_specs_for_log(),
        )
        log.info(
            "TCP controller ready: device=%s sensors=%d route_waypoints=%d load=%s",
            self.device,
            len(self._sensor_actors),
            len(self._route),
            self.load_info,
        )

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        del dt
        if self.carla is None:
            return None
        if self.net is None or self.torch is None:
            return self._fallback_control(brake=0.7)

        obs = observation or {}
        sim_time = float(obs.get("sim_time") or 0.0)
        self._step_count += 1
        self._maybe_refresh_route(sim_time)

        frame = self._current_world_frame()
        self._wait_for_synced_images(frame, self.sensor_timeout_s)
        samples = self._latest_synced_images(frame)
        if samples is None:
            control = self._copy_control(self._last_control) or self._fallback_control()
            self._write_trace(
                sim_time=sim_time,
                frame=frame,
                mode="no_synced_sensors",
                control=control,
                speed=self._ego_speed_mps(),
                input_frames={},
                stale=True,
                target_point=(0.0, 0.0),
                error="missing_or_stale_camera",
            )
            return control

        input_frames = {key: int(samples[key][0]) for key in _TCP_CAMERA_KEYS}
        stale = any(sample_frame < frame for sample_frame in input_frames.values()) if frame else False
        if self._last_control is not None and self._repeat_left > 0:
            self._repeat_left -= 1
            control = self._copy_control(self._last_control)
            self._write_trace(
                sim_time=sim_time,
                frame=frame,
                mode="hold",
                control=control,
                speed=self._ego_speed_mps(),
                input_frames=input_frames,
                stale=stale,
                target_point=self._last_metadata.get("target_point", (0.0, 0.0)),
            )
            return control

        panorama = None
        target_point = (0.0, 0.0)
        error = ""
        try:
            panorama = self._build_panorama(samples)
            image_tensor = self._image_tensor(panorama)
            speed_mps = self._ego_speed_mps()
            target_point = self._target_point()
            target_tensor, state_tensor, velocity_tensor = self._state_tensors(
                speed_mps, target_point
            )
            with self.torch.no_grad():
                pred = self.net(image_tensor, state_tensor, target_tensor)
                steer, throttle, brake, metadata = self.net.control_pid(
                    pred["pred_wp"], velocity_tensor, target_tensor
                )
            control = self._make_control(steer, throttle, brake, speed_mps)
            self._inference_count += 1
            self._last_metadata = {
                **self._metadata_jsonable(metadata),
                "target_point": tuple(float(v) for v in target_point),
                "input_frames": input_frames,
            }
            if not self._control_is_finite(control):
                error = "nonfinite_control"
                control = self._copy_control(self._last_control) or self._fallback_control(
                    brake=0.7
                )
            else:
                self._last_control = self._copy_control(control)
                self._repeat_left = max(0, self.action_repeat - 1)
        except Exception as exc:  # noqa: BLE001
            self._inference_count += 1
            log.exception("TCP inference failed at t=%.2f", sim_time)
            error = str(exc)
            control = self._copy_control(self._last_control) or self._fallback_control(
                brake=0.7
            )

        if panorama is not None:
            self._maybe_save_debug_artifacts(panorama, sim_time, frame, control, target_point)
        self._write_trace(
            sim_time=sim_time,
            frame=frame,
            mode="infer",
            control=control,
            speed=self._ego_speed_mps(),
            input_frames=input_frames,
            stale=stale,
            target_point=target_point,
            error=error,
        )
        return control

    def teardown(self) -> None:
        for sensor in reversed(self._sensor_actors):
            try:
                sensor.stop()
            except Exception:
                pass
            try:
                sensor.destroy()
            except Exception as exc:  # noqa: BLE001
                log.debug("TCP sensor destroy failed: %s", exc)
        self._sensor_actors.clear()
        with self._sensor_lock:
            self._latest_images.clear()
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
    # Setup helpers
    # ------------------------------------------------------------------
    def _prepare_import_paths(self) -> None:
        ensure_agents_on_path()
        tcp_root = self._resolve_path(self.tcfg.get("tcp_root") or _DEFAULT_TCP_ROOT)
        if tcp_root not in sys.path:
            sys.path.insert(0, tcp_root)

    def _load_model(self) -> None:
        import torch

        self.torch = torch
        requested = str(self.tcfg.get("device") or "cuda")
        if requested.startswith("cuda") and not torch.cuda.is_available():
            requested = "cpu"
        self.device = torch.device(requested)

        import TCP.resnet as tcp_resnet

        original_resnet34 = tcp_resnet.resnet34

        def _resnet34_no_pretrain(pretrained: bool = False, progress: bool = True, **kwargs: Any):
            del pretrained
            return original_resnet34(pretrained=False, progress=progress, **kwargs)

        tcp_resnet.resnet34 = _resnet34_no_pretrain
        from TCP.config import GlobalConfig
        from TCP.model import TCP

        self.config_obj = GlobalConfig()
        self.net = TCP(self.config_obj).to(self.device)
        ckpt_path = self._resolve_path(self.tcfg.get("ckpt_path") or _DEFAULT_CKPT)
        try:
            ckpt = torch.load(ckpt_path, map_location=self.device, weights_only=False)
        except TypeError:
            ckpt = torch.load(ckpt_path, map_location=self.device)
        state = ckpt.get("state_dict", ckpt) if isinstance(ckpt, dict) else ckpt
        stripped = {
            str(k).replace("model.", "", 1): v
            for k, v in dict(state).items()
        }
        incompatible = self.net.load_state_dict(stripped, strict=False)
        self.net.eval()
        self.load_info = {
            "checkpoint": str(ckpt_path),
            "state_dict_keys": len(stripped),
            "missing": len(getattr(incompatible, "missing_keys", []) or []),
            "unexpected": len(getattr(incompatible, "unexpected_keys", []) or []),
            "missing_keys": list(getattr(incompatible, "missing_keys", []) or [])[:8],
            "unexpected_keys": list(getattr(incompatible, "unexpected_keys", []) or [])[:8],
            "resnet34_pretrained_patched": True,
        }

    def _attach_tcp_cameras(self) -> None:
        world = self.world
        ego = self.ego
        carla = self.carla
        if world is None or ego is None or carla is None:
            raise RuntimeError("world, ego, and carla must be available before sensors")

        bp_lib = world.get_blueprint_library()
        for sensor_id, tf_values in self._camera_specs():
            bp = bp_lib.find("sensor.camera.rgb")
            bp.set_attribute("image_size_x", "1600")
            bp.set_attribute("image_size_y", "900")
            bp.set_attribute("fov", "70")
            if bp.has_attribute("sensor_tick"):
                bp.set_attribute("sensor_tick", str(self.sim_dt))
            camera = world.spawn_actor(
                bp,
                self._transform_from_tuple(tf_values),
                attach_to=ego,
            )
            camera.listen(self._make_camera_callback(sensor_id))
            self._sensor_actors.append(camera)

    @staticmethod
    def _camera_specs() -> tuple[tuple[str, tuple[float, float, float, float, float, float]], ...]:
        return (
            ("CAM_FRONT", (0.80, 0.0, 1.60, 0.0, 0.0, 0.0)),
            ("CAM_FRONT_LEFT", (0.27, -0.55, 1.60, 0.0, 0.0, -55.0)),
            ("CAM_FRONT_RIGHT", (0.27, 0.55, 1.60, 0.0, 0.0, 55.0)),
        )

    def _camera_specs_for_log(self) -> list[dict[str, Any]]:
        return [
            {
                "id": sensor_id,
                "transform": values,
                "width": 1600,
                "height": 900,
                "fov": 70,
                "jpeg_quality": 20,
            }
            for sensor_id, values in self._camera_specs()
        ]

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
            "tcp_route",
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
                log.debug("TCP route refresh failed: %s", exc)

    # ------------------------------------------------------------------
    # Sensor callbacks / TCP input assembly
    # ------------------------------------------------------------------
    def _make_camera_callback(self, sensor_id: str) -> Any:
        def _callback(image: Any) -> None:
            array = np.frombuffer(image.raw_data, dtype=np.uint8)
            bgra = array.reshape((image.height, image.width, 4))
            rgb = bgra[:, :, :3][:, :, ::-1].copy()
            with self._sensor_lock:
                self._latest_images[sensor_id] = (int(image.frame), rgb)

        return _callback

    def _wait_for_synced_images(self, frame: Optional[int], timeout_s: float) -> None:
        if frame is None:
            return
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            with self._sensor_lock:
                synced = all(
                    key in self._latest_images and self._latest_images[key][0] >= frame
                    for key in _TCP_CAMERA_KEYS
                )
            if synced or time.monotonic() >= deadline:
                return
            time.sleep(0.001)

    def _latest_synced_images(
        self, frame: Optional[int]
    ) -> Optional[dict[str, tuple[int, np.ndarray]]]:
        with self._sensor_lock:
            if any(key not in self._latest_images for key in _TCP_CAMERA_KEYS):
                return None
            if frame is not None and any(
                self._latest_images[key][0] < frame for key in _TCP_CAMERA_KEYS
            ):
                return None
            return {key: self._latest_images[key] for key in _TCP_CAMERA_KEYS}

    def _build_panorama(self, samples: dict[str, tuple[int, np.ndarray]]) -> np.ndarray:
        front = self._jpeg_q20(samples["CAM_FRONT"][1])[:, 200:1400, :]
        left = self._jpeg_q20(samples["CAM_FRONT_LEFT"][1])[:, :1400, :]
        right = self._jpeg_q20(samples["CAM_FRONT_RIGHT"][1])[:, 200:, :]
        wide = np.concatenate((left, front, right), axis=1)
        from PIL import Image

        return np.asarray(
            Image.fromarray(wide).resize((900, 256), Image.Resampling.BILINEAR),
            dtype=np.uint8,
        )

    @staticmethod
    def _jpeg_q20(rgb: np.ndarray) -> np.ndarray:
        from PIL import Image

        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, format="JPEG", quality=20)
        buf.seek(0)
        return np.asarray(Image.open(buf).convert("RGB"), dtype=np.uint8)

    def _image_tensor(self, panorama: np.ndarray) -> Any:
        arr = panorama.astype(np.float32) / 255.0
        arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD
        tensor = self.torch.from_numpy(arr.transpose(2, 0, 1)).unsqueeze(0)
        return tensor.to(self.device, dtype=self.torch.float32)

    def _target_point(self) -> tuple[float, float]:
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

        dx = float(target.x - loc.x)
        dy = float(target.y - loc.y)
        yaw = math.radians(float(ego_tf.rotation.yaw))
        local_x = math.cos(yaw) * dx + math.sin(yaw) * dy
        local_y = -math.sin(yaw) * dx + math.cos(yaw) * dy
        return float(local_x), float(local_y)

    def _state_tensors(
        self, speed_mps: float, target_point: tuple[float, float]
    ) -> tuple[Any, Any, Any]:
        target = self.torch.tensor(
            [[float(target_point[0]), float(target_point[1])]],
            device=self.device,
            dtype=self.torch.float32,
        )
        speed = self.torch.tensor(
            [[float(speed_mps) / 12.0]], device=self.device, dtype=self.torch.float32
        )
        command = self.torch.zeros((1, 6), device=self.device, dtype=self.torch.float32)
        command[0, 3] = 1.0  # RoadOption.LANEFOLLOW value 4, shifted by -1.
        state = self.torch.cat([speed, target, command], dim=1)
        velocity = self.torch.tensor(
            [float(speed_mps)], device=self.device, dtype=self.torch.float32
        )
        return target, state, velocity

    # ------------------------------------------------------------------
    # Debug / telemetry
    # ------------------------------------------------------------------
    def _resolve_debug_dir(self) -> Optional[str]:
        raw = self.tcfg.get("debug_dir") or os.environ.get("MARSHAL_TCP_DEBUG_DIR")
        if not raw:
            return None
        return self._resolve_path(raw)

    def _prepare_debug_outputs(self) -> None:
        if not self._debug_dir:
            return
        os.makedirs(self._debug_dir, exist_ok=True)
        self._trace_fh = open(
            os.path.join(self._debug_dir, "tcp_trace.csv"),
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
                "cam_front_frame",
                "cam_left_frame",
                "cam_right_frame",
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
        target_point: Any,
        error: str = "",
    ) -> None:
        throttle = float(getattr(control, "throttle", 0.0) or 0.0)
        brake = float(getattr(control, "brake", 0.0) or 0.0)
        steer = float(getattr(control, "steer", 0.0) or 0.0)
        try:
            tx, ty = target_point
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
                    "cam_front_frame": input_frames.get("CAM_FRONT", ""),
                    "cam_left_frame": input_frames.get("CAM_FRONT_LEFT", ""),
                    "cam_right_frame": input_frames.get("CAM_FRONT_RIGHT", ""),
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
                "TCP t=%.2f frame=%s mode=%s v=%.2f thr=%.3f brk=%.3f steer=%.3f stale=%s err=%s",
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

    def _maybe_save_debug_artifacts(
        self,
        panorama: np.ndarray,
        sim_time: float,
        frame: Optional[int],
        control: Any,
        target_point: tuple[float, float],
    ) -> None:
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
            from PIL import Image, ImageDraw

            img = Image.fromarray(panorama)
            draw = ImageDraw.Draw(img)
            label = (
                f"TCP t={sim_time:.2f}s frame={frame} "
                f"thr={float(getattr(control, 'throttle', 0.0)):.2f} "
                f"brk={float(getattr(control, 'brake', 0.0)):.2f} "
                f"steer={float(getattr(control, 'steer', 0.0)):.2f} "
                f"target=({target_point[0]:.1f},{target_point[1]:.1f})"
            )
            draw.rectangle((0, 0, min(img.width, 860), 30), fill=(0, 0, 0))
            draw.text((8, 8), label, fill=(255, 255, 255))
            img.save(os.path.join(self._debug_dir, f"input_{self._debug_saved:03d}.png"))
            self._debug_saved += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("TCP debug artifact save failed: %s", exc)

    def _log_event(self, name: str, **payload: Any) -> None:
        logger = self._logger
        if logger is not None and hasattr(logger, "log_event"):
            try:
                logger.log_event(name, **payload)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _resolve_path(self, path: Any) -> str:
        p = Path(os.fspath(path))
        if not p.is_absolute():
            p = _WORKSPACE_ROOT / p
        return str(p.resolve())

    def _transform_from_tuple(
        self, values: tuple[float, float, float, float, float, float]
    ) -> Any:
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
            return math.sqrt(
                velocity.x * velocity.x
                + velocity.y * velocity.y
                + velocity.z * velocity.z
            )
        except Exception:
            return 0.0

    def _make_control(self, steer: Any, throttle: Any, brake: Any, speed_mps: float) -> Any:
        control = self.carla.VehicleControl()
        control.steer = float(np.clip(float(steer), -1.0, 1.0))
        control.throttle = float(np.clip(float(throttle), 0.0, 0.75))
        control.brake = float(np.clip(float(brake), 0.0, 1.0))
        if self.postprocess_control:
            speed_threshold = 1.0 if abs(control.steer) > 0.07 else 1.5
            max_throttle = 0.05 if float(speed_mps) > speed_threshold else 0.5
            control.throttle = float(np.clip(control.throttle, 0.0, max_throttle))
            if control.brake > 0.0:
                control.brake = 1.0
            if control.brake > 0.5:
                control.throttle = 0.0
        return control

    def _fallback_control(self, brake: float = 0.0) -> Any:
        control = self.carla.VehicleControl()
        control.throttle = 0.0
        control.brake = float(brake)
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

    @staticmethod
    def _metadata_jsonable(metadata: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in (metadata or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
            elif isinstance(value, (list, tuple)):
                out[key] = [float(v) if isinstance(v, np.generic) else v for v in value]
            elif isinstance(value, np.generic):
                out[key] = value.item()
            else:
                out[key] = str(value)
        return out


__all__ = ["TCPController"]
