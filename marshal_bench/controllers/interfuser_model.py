"""Track-B InterFuser controller adapter for MARSHAL.

The adapter runs the local InterFuser PyTorch checkpoint with the original
camera/LiDAR preprocessing and controller, while taking route targets only from
MARSHAL's non-privileged lane-follow helper.
"""
from __future__ import annotations

import csv
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
from marshal_bench.controllers.lane_route import (
    build_lane_follow_plan,
    location_to_gps,
    read_latlon_ref,
)
from marshal_bench.utils.carla_api_compat import ensure_agents_on_path

log = logging.getLogger("marshal_bench.controllers.interfuser")

_CAMERA_KEYS = ("rgb", "rgb_left", "rgb_right")
_SENSOR_KEYS = ("rgb", "rgb_left", "rgb_right", "lidar", "imu")
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


def _find_workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "Models" / "InterFuser_ckpt" / "interfuser.pth").is_file():
            return parent
    return here.parents[2]


_WORKSPACE_ROOT = _find_workspace_root()
_DEFAULT_INTERFUSER_ROOT = _WORKSPACE_ROOT / "Models" / "InterFuser"
_DEFAULT_CKPT = _WORKSPACE_ROOT / "Models" / "InterFuser_ckpt" / "interfuser.pth"


class _Resize2FixedSize:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size

    def __call__(self, pil_img: Any) -> Any:
        return pil_img.resize(self.size)


def _make_rgb_transform(input_size: int, *, need_scale: bool = True) -> Any:
    from torchvision import transforms

    img_size = (input_size, input_size)
    ops = []
    if need_scale:
        if input_size == 224:
            ops.append(_Resize2FixedSize((341, 256)))
        elif input_size == 128:
            ops.append(_Resize2FixedSize((195, 146)))
        else:
            raise ValueError(f"unsupported InterFuser input size {input_size}")
    ops.extend(
        [
            transforms.CenterCrop(img_size),
            transforms.ToTensor(),
            transforms.Normalize(mean=_IMAGENET_MEAN, std=_IMAGENET_STD),
        ]
    )
    return transforms.Compose(ops)


class InterFuserController(EpisodeController):
    name = "interfuser"
    track = "B"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.icfg = dict(self.config.get("interfuser") or {})

        self.world = None
        self.ego = None
        self.carla = None
        self._road_option = None
        self._lat_ref = 42.0
        self._lon_ref = 2.0
        self._route_planner = None
        self._route_end = None
        self._last_route_update_t = -1e9

        self.torch = None
        self.device = None
        self.net = None
        self.softmax = None
        self.model_config = None
        self.controller = None
        self.tracker = None
        self.load_info: dict[str, Any] = {}

        self.rgb_front_transform = None
        self.rgb_left_transform = None
        self.rgb_right_transform = None
        self.rgb_center_transform = None

        self._sensor_lock = threading.Lock()
        self._latest: dict[str, tuple[int, Any]] = {}
        self._sensor_actors: list[Any] = []

        self.sim_dt = float(self.icfg.get("sim_dt", 0.05))
        self.sensor_timeout_s = float(self.icfg.get("sensor_timeout_s", 0.75))
        self.route_horizon_m = float(self.icfg.get("route_horizon_m", 160.0))
        self.route_step_m = float(self.icfg.get("route_step_m", 1.0))
        self.route_refresh_distance_m = float(
            self.icfg.get("route_refresh_distance_m", 45.0)
        )
        self.route_refresh_period_s = float(self.icfg.get("route_refresh_period_s", 2.0))

        self._step_count = -1
        self._inference_count = 0
        self._last_control = None
        self._prev_lidar = None
        self._prev_control = None
        self._traffic_meta_moving_avg = np.zeros((400, 7), dtype=np.float32)
        self._last_debug: dict[str, Any] = {}

        self._logger = self.config.get("_episode_logger")
        self._debug_dir = self._resolve_debug_dir()
        self._trace_fh = None
        self._trace_writer = None
        self._debug_saved = 0
        self._debug_save_every_n = int(self.icfg.get("save_debug_every_n", 20))
        self._debug_max_frames = int(self.icfg.get("max_debug_frames", 8))
        self._log_every_n = int(self.icfg.get("log_every_n", 10))

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

        self._prepare_debug_outputs()
        self._prepare_import_paths()
        try:
            from agents.navigation.local_planner import RoadOption

            self._road_option = RoadOption.LANEFOLLOW
        except Exception:
            self._road_option = None
        self._lat_ref, self._lon_ref = read_latlon_ref(world)
        self._load_model_stack()
        self._refresh_route(sim_time=0.0)
        self._attach_sensors()
        self._log_event(
            "interfuser_setup",
            checkpoint=str(self._resolve_path(self.icfg.get("ckpt_path") or _DEFAULT_CKPT)),
            interfuser_root=str(
                self._resolve_path(self.icfg.get("interfuser_root") or _DEFAULT_INTERFUSER_ROOT)
            ),
            device=str(self.device),
            load_info=self.load_info,
            sensor_count=len(self._sensor_actors),
            route_source="map_waypoints_lane_follow",
        )
        log.info(
            "InterFuser controller ready: device=%s sensors=%d load=%s",
            self.device,
            len(self._sensor_actors),
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
        self._wait_for_synced_sensors(frame, self.sensor_timeout_s)
        samples = self._latest_synced_sensors(frame)
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
                error="missing_or_stale_sensor",
            )
            return control

        input_frames = {key: int(samples[key][0]) for key in _SENSOR_KEYS}
        stale = any(sample_frame < frame for sample_frame in input_frames.values()) if frame else False
        error = ""
        tick_data: Optional[dict[str, Any]] = None
        try:
            tick_data = self._build_tick_data(samples)
            model_input = self._model_input(tick_data)
            with self.torch.no_grad():
                (
                    traffic_meta,
                    pred_waypoints,
                    is_junction,
                    traffic_light_state,
                    stop_sign,
                    bev_feature,
                ) = self.net(model_input)
            del bev_feature
            traffic_meta_np = traffic_meta.detach().cpu().numpy()[0]
            pred_waypoints_np = pred_waypoints.detach().cpu().numpy()[0]
            is_junction_p = float(
                self.softmax(is_junction).detach().cpu().numpy().reshape(-1)[0]
            )
            traffic_light_p = float(
                self.softmax(traffic_light_state).detach().cpu().numpy().reshape(-1)[0]
            )
            stop_sign_p = float(self.softmax(stop_sign).detach().cpu().numpy().reshape(-1)[0])

            if self._step_count % 2 == 0 or self._step_count < 4:
                tracked = self.tracker.update_and_predict(
                    traffic_meta_np.reshape(20, 20, -1),
                    tick_data["gps"],
                    tick_data["compass"],
                    self._step_count // 2,
                ).reshape(400, -1)
                momentum = float(getattr(self.model_config, "momentum", 0.0))
                self._traffic_meta_moving_avg = (
                    momentum * self._traffic_meta_moving_avg
                    + (1.0 - momentum) * tracked
                )
            traffic_meta_smooth = self._traffic_meta_moving_avg

            steer, throttle, brake, meta_infos = self.controller.run_step(
                float(tick_data["speed"]),
                pred_waypoints_np,
                is_junction_p,
                traffic_light_p,
                stop_sign_p,
                traffic_meta_smooth,
            )
            if float(brake) < 0.05:
                brake = 0.0
            if float(brake) > 0.1:
                throttle = 0.0

            control = self._make_control(steer, throttle, brake)
            self._inference_count += 1
            self._last_debug = {
                "rgb": tick_data["rgb"],
                "rgb_left": tick_data["rgb_left"],
                "rgb_right": tick_data["rgb_right"],
                "lidar": tick_data["lidar"],
                "target_point": tick_data["target_point"],
                "pred_waypoints": pred_waypoints_np,
                "meta_infos": meta_infos,
                "junction": is_junction_p,
                "traffic_light": traffic_light_p,
                "stop_sign": stop_sign_p,
            }
            if not self._control_is_finite(control):
                error = "nonfinite_control"
                control = self._copy_control(self._last_control) or self._fallback_control(
                    brake=0.7
                )
            else:
                self._last_control = self._copy_control(control)
                self._prev_control = self._copy_control(control)
        except Exception as exc:  # noqa: BLE001
            self._inference_count += 1
            log.exception("InterFuser inference failed at t=%.2f", sim_time)
            error = str(exc)
            control = self._copy_control(self._last_control) or self._fallback_control(
                brake=0.7
            )

        if tick_data is not None:
            self._maybe_save_debug_artifacts(sim_time, frame, control)
        self._write_trace(
            sim_time=sim_time,
            frame=frame,
            mode="infer",
            control=control,
            speed=self._ego_speed_mps(),
            input_frames=input_frames,
            stale=stale,
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
                log.debug("InterFuser sensor destroy failed: %s", exc)
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
    # Setup helpers
    # ------------------------------------------------------------------
    def _prepare_import_paths(self) -> None:
        ensure_agents_on_path()
        root = Path(self._resolve_path(self.icfg.get("interfuser_root") or _DEFAULT_INTERFUSER_ROOT))
        for path in (
            root / "interfuser",
            root / "leaderboard",
            root / "scenario_runner",
        ):
            spath = str(path)
            if spath not in sys.path:
                sys.path.insert(0, spath)

    def _load_model_stack(self) -> None:
        import torch

        if not hasattr(np, "int"):
            np.int = int  # type: ignore[attr-defined]
        from timm.models import create_model
        from team_code.interfuser_config import GlobalConfig
        from team_code.interfuser_controller import InterfuserController as NativeController
        from team_code.tracker import Tracker

        self.torch = torch
        requested = str(self.icfg.get("device") or "cuda")
        if requested.startswith("cuda") and not torch.cuda.is_available():
            requested = "cpu"
        self.device = torch.device(requested)

        self.model_config = GlobalConfig()
        self.model_config.model_path = str(self._resolve_path(self.icfg.get("ckpt_path") or _DEFAULT_CKPT))
        self.net = create_model(self.model_config.model).to(self.device)
        try:
            ckpt = torch.load(self.model_config.model_path, map_location=self.device, weights_only=False)
        except TypeError:
            ckpt = torch.load(self.model_config.model_path, map_location=self.device)
        state = ckpt.get("state_dict", ckpt) if isinstance(ckpt, dict) else ckpt
        incompatible = self.net.load_state_dict(state, strict=False)
        self.net.eval()
        self.softmax = torch.nn.Softmax(dim=1)
        self.controller = NativeController(self.model_config)
        self.tracker = Tracker()
        self.rgb_front_transform = _make_rgb_transform(224)
        self.rgb_left_transform = _make_rgb_transform(128)
        self.rgb_right_transform = _make_rgb_transform(128)
        self.rgb_center_transform = _make_rgb_transform(128, need_scale=False)
        self.load_info = {
            "checkpoint": self.model_config.model_path,
            "model": self.model_config.model,
            "state_dict_keys": len(state),
            "missing": len(getattr(incompatible, "missing_keys", []) or []),
            "unexpected": len(getattr(incompatible, "unexpected_keys", []) or []),
            "missing_keys": list(getattr(incompatible, "missing_keys", []) or [])[:8],
            "unexpected_keys": list(getattr(incompatible, "unexpected_keys", []) or [])[:8],
            "openmmlab_import_required": False,
        }

    def _attach_sensors(self) -> None:
        world = self.world
        ego = self.ego
        carla = self.carla
        if world is None or ego is None or carla is None:
            raise RuntimeError("world, ego, and carla must be available before sensors")
        bp_lib = world.get_blueprint_library()

        for sensor_id, tf_values, width, height, fov in self._camera_specs():
            bp = bp_lib.find("sensor.camera.rgb")
            bp.set_attribute("image_size_x", str(width))
            bp.set_attribute("image_size_y", str(height))
            bp.set_attribute("fov", str(fov))
            if bp.has_attribute("sensor_tick"):
                bp.set_attribute("sensor_tick", str(self.sim_dt))
            camera = world.spawn_actor(
                bp,
                self._transform_from_tuple(tf_values),
                attach_to=ego,
            )
            camera.listen(self._make_camera_callback(sensor_id))
            self._sensor_actors.append(camera)

        lidar_bp = bp_lib.find("sensor.lidar.ray_cast")
        if lidar_bp.has_attribute("sensor_tick"):
            lidar_bp.set_attribute("sensor_tick", str(self.sim_dt))
        lidar = world.spawn_actor(
            lidar_bp,
            self._transform_from_tuple((1.3, 0.0, 2.5, 0.0, 0.0, -90.0)),
            attach_to=ego,
        )
        lidar.listen(self._lidar_callback)
        self._sensor_actors.append(lidar)

        imu_bp = bp_lib.find("sensor.other.imu")
        if imu_bp.has_attribute("sensor_tick"):
            imu_bp.set_attribute("sensor_tick", str(self.sim_dt))
        imu = world.spawn_actor(imu_bp, carla.Transform(), attach_to=ego)
        imu.listen(self._imu_callback)
        self._sensor_actors.append(imu)

    @staticmethod
    def _camera_specs() -> tuple[
        tuple[str, tuple[float, float, float, float, float, float], int, int, int],
        ...
    ]:
        return (
            ("rgb", (1.3, 0.0, 2.3, 0.0, 0.0, 0.0), 800, 600, 100),
            ("rgb_left", (1.3, 0.0, 2.3, 0.0, 0.0, -60.0), 400, 300, 100),
            ("rgb_right", (1.3, 0.0, 2.3, 0.0, 0.0, 60.0), 400, 300, 100),
        )

    def _refresh_route(self, sim_time: float) -> None:
        from team_code.planner import RoutePlanner

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
        if self._route_planner is None:
            self._route_planner = RoutePlanner(4.0, 50.0)
        self._route_planner.set_route(plan.gps_plan, True)
        self._route_end = plan.route_end
        self._last_route_update_t = float(sim_time)
        end = self._route_end
        self._log_event(
            "interfuser_route",
            t=sim_time,
            waypoints=len(plan.world_plan),
            end=(
                {"x": float(end.x), "y": float(end.y), "z": float(end.z)}
                if end is not None
                else None
            ),
            source="map_waypoints_lane_follow",
            road_option="LANEFOLLOW",
        )

    def _maybe_refresh_route(self, sim_time: float) -> None:
        if self._route_planner is None:
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
                log.debug("InterFuser route refresh failed: %s", exc)

    # ------------------------------------------------------------------
    # Sensor callbacks / input assembly
    # ------------------------------------------------------------------
    def _make_camera_callback(self, sensor_id: str) -> Any:
        def _callback(image: Any) -> None:
            array = np.frombuffer(image.raw_data, dtype=np.uint8)
            bgra = array.reshape((image.height, image.width, 4))
            rgb = bgra[:, :, :3][:, :, ::-1].copy()
            with self._sensor_lock:
                self._latest[sensor_id] = (int(image.frame), rgb)

        return _callback

    def _lidar_callback(self, measurement: Any) -> None:
        points = np.frombuffer(measurement.raw_data, dtype=np.float32).reshape((-1, 4)).copy()
        with self._sensor_lock:
            self._latest["lidar"] = (int(measurement.frame), points)

    def _imu_callback(self, measurement: Any) -> None:
        compass = float(getattr(measurement, "compass", 0.0) or 0.0)
        if math.isnan(compass):
            compass = 0.0
        with self._sensor_lock:
            self._latest["imu"] = (int(measurement.frame), compass)

    def _wait_for_synced_sensors(self, frame: Optional[int], timeout_s: float) -> None:
        if frame is None:
            return
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            with self._sensor_lock:
                synced = all(
                    key in self._latest and self._latest[key][0] >= frame
                    for key in _SENSOR_KEYS
                )
            if synced or time.monotonic() >= deadline:
                return
            time.sleep(0.001)

    def _latest_synced_sensors(self, frame: Optional[int]) -> Optional[dict[str, tuple[int, Any]]]:
        with self._sensor_lock:
            if any(key not in self._latest for key in _SENSOR_KEYS):
                return None
            if frame is not None and any(self._latest[key][0] < frame for key in _SENSOR_KEYS):
                return None
            return {key: self._latest[key] for key in _SENSOR_KEYS}

    def _build_tick_data(self, samples: dict[str, tuple[int, Any]]) -> dict[str, Any]:
        from team_code.utils import lidar_to_histogram_features, transform_2d_points

        rgb = samples["rgb"][1]
        rgb_left = samples["rgb_left"][1]
        rgb_right = samples["rgb_right"][1]
        compass = float(samples["imu"][1])
        speed = self._ego_speed_mps()
        gps = self._ego_gps()
        pos = self._planner_position(gps)

        lidar_data = samples["lidar"][1]
        lidar_unprocessed = lidar_data[:, :3].copy()
        lidar_unprocessed[:, 1] *= -1
        full_lidar = transform_2d_points(
            lidar_unprocessed,
            np.pi / 2 - compass,
            -pos[0],
            -pos[1],
            np.pi / 2 - compass,
            -pos[0],
            -pos[1],
        )
        lidar_processed = lidar_to_histogram_features(full_lidar, crop=224)
        if self._step_count % 2 == 0 or self._step_count < 4:
            self._prev_lidar = lidar_processed
        lidar_processed = self._prev_lidar if self._prev_lidar is not None else lidar_processed

        next_wp, next_cmd = self._route_planner.run_step(pos)
        theta = compass + np.pi / 2
        rot = np.array(
            [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]],
            dtype=np.float32,
        )
        local_command_point = np.array([next_wp[0] - pos[0], next_wp[1] - pos[1]])
        local_command_point = rot.T.dot(local_command_point).astype(np.float32)
        return {
            "rgb": rgb,
            "rgb_left": rgb_left,
            "rgb_right": rgb_right,
            "gps": pos,
            "speed": float(speed),
            "compass": float(compass),
            "lidar": lidar_processed,
            "target_point": local_command_point,
            "next_command": int(getattr(next_cmd, "value", 4)),
        }

    def _model_input(self, tick_data: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image

        device = self.device
        torch = self.torch
        command = int(tick_data["next_command"])
        cmd = max(0, min(5, command - 1))
        cmd_one_hot = [0.0] * 6
        cmd_one_hot[cmd] = 1.0
        cmd_one_hot.append(float(tick_data["speed"]))
        return {
            "rgb": self.rgb_front_transform(Image.fromarray(tick_data["rgb"]))
            .unsqueeze(0)
            .to(device, dtype=torch.float32),
            "rgb_left": self.rgb_left_transform(Image.fromarray(tick_data["rgb_left"]))
            .unsqueeze(0)
            .to(device, dtype=torch.float32),
            "rgb_right": self.rgb_right_transform(Image.fromarray(tick_data["rgb_right"]))
            .unsqueeze(0)
            .to(device, dtype=torch.float32),
            "rgb_center": self.rgb_center_transform(Image.fromarray(tick_data["rgb"]))
            .unsqueeze(0)
            .to(device, dtype=torch.float32),
            "measurements": torch.tensor([cmd_one_hot], device=device, dtype=torch.float32),
            "target_point": torch.from_numpy(tick_data["target_point"])
            .float()
            .to(device)
            .view(1, -1),
            "lidar": torch.from_numpy(tick_data["lidar"])
            .float()
            .to(device)
            .unsqueeze(0),
        }

    # ------------------------------------------------------------------
    # Debug / telemetry
    # ------------------------------------------------------------------
    def _resolve_debug_dir(self) -> Optional[str]:
        raw = self.icfg.get("debug_dir") or os.environ.get("MARSHAL_INTERFUSER_DEBUG_DIR")
        if not raw:
            return None
        return self._resolve_path(raw)

    def _prepare_debug_outputs(self) -> None:
        if not self._debug_dir:
            return
        os.makedirs(self._debug_dir, exist_ok=True)
        self._trace_fh = open(
            os.path.join(self._debug_dir, "interfuser_trace.csv"),
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
                "lidar_frame",
                "imu_frame",
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
        error: str = "",
    ) -> None:
        throttle = float(getattr(control, "throttle", 0.0) or 0.0)
        brake = float(getattr(control, "brake", 0.0) or 0.0)
        steer = float(getattr(control, "steer", 0.0) or 0.0)
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
                    "lidar_frame": input_frames.get("lidar", ""),
                    "imu_frame": input_frames.get("imu", ""),
                    "stale": bool(stale),
                    "error": str(error)[:200],
                }
            )
            self._trace_fh.flush()
        if self._log_every_n > 0 and (
            self._step_count <= 5 or (self._step_count % self._log_every_n) == 0
        ):
            log.info(
                "InterFuser t=%.2f frame=%s mode=%s v=%.2f thr=%.3f brk=%.3f steer=%.3f stale=%s err=%s",
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
        sim_time: float,
        frame: Optional[int],
        control: Any,
    ) -> None:
        if not self._debug_dir or not self._last_debug:
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

            front = Image.fromarray(self._last_debug["rgb"]).resize((533, 400))
            left = Image.fromarray(self._last_debug["rgb_left"]).resize((178, 134))
            right = Image.fromarray(self._last_debug["rgb_right"]).resize((178, 134))
            lidar_img = self._lidar_debug_image(self._last_debug["lidar"]).resize((267, 267))
            canvas = Image.new("RGB", (800, 430), (8, 8, 8))
            canvas.paste(front, (0, 30))
            canvas.paste(left, (540, 30))
            canvas.paste(right, (540, 166))
            canvas.paste(lidar_img, (533, 163))
            draw = ImageDraw.Draw(canvas)
            tp = self._last_debug.get("target_point", [0.0, 0.0])
            label = (
                f"InterFuser t={sim_time:.2f}s frame={frame} "
                f"thr={float(getattr(control, 'throttle', 0.0)):.2f} "
                f"brk={float(getattr(control, 'brake', 0.0)):.2f} "
                f"steer={float(getattr(control, 'steer', 0.0)):.2f} "
                f"target=({float(tp[0]):.1f},{float(tp[1]):.1f})"
            )
            draw.rectangle((0, 0, 800, 30), fill=(0, 0, 0))
            draw.text((8, 8), label, fill=(255, 255, 255))
            canvas.save(os.path.join(self._debug_dir, f"input_{self._debug_saved:03d}.png"))
            self._debug_saved += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("InterFuser debug artifact save failed: %s", exc)

    @staticmethod
    def _lidar_debug_image(lidar: np.ndarray) -> Any:
        from PIL import Image

        total = np.asarray(lidar[2], dtype=np.float32)
        if total.size and total.max() > 0:
            total = total / max(float(total.max()), 1e-6)
        img = (np.clip(total, 0.0, 1.0) * 255).astype(np.uint8)
        return Image.fromarray(img, mode="L").convert("RGB")

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

    def _ego_gps(self) -> np.ndarray:
        loc = self.ego.get_location()
        gps = location_to_gps(loc, self._lat_ref, self._lon_ref)
        return np.array([gps["lat"], gps["lon"]], dtype=np.float64)

    def _planner_position(self, gps: np.ndarray) -> np.ndarray:
        gps = np.asarray(gps, dtype=np.float64)
        gps = (gps - self._route_planner.mean) * self._route_planner.scale
        return gps

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


__all__ = ["InterFuserController"]
