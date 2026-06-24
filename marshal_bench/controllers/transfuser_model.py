"""Track-B TransFuser controller adapter for MARSHAL.

This wraps the existing live TransFuser inference stack under
``Models/TransFuser/TransFuser_UI_V2`` as a MARSHAL ``EpisodeController``.
The controller owns its extra RGB/LiDAR/IMU/GNSS sensors and feeds the
model's native ``HybridAgent`` input dict directly.
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
    angle_delta,
    build_lane_follow_plan,
    location_to_gps,
    read_latlon_ref,
    world_xy_to_latlon,
)
from marshal_bench.utils.carla_api_compat import ensure_agents_on_path

log = logging.getLogger("marshal_bench.controllers.transfuser")

_TRANSFUSER_KEYS = ("rgb_front", "rgb_left", "rgb_right", "lidar", "imu", "gps")


def _find_workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "Models" / "TransFuser" / "TransFuser_UI_V2" / "app" / "inference.py").is_file():
            return parent
    return here.parents[2]


_WORKSPACE_ROOT = _find_workspace_root()
_DEFAULT_UI_ROOT = _WORKSPACE_ROOT / "Models" / "TransFuser" / "TransFuser_UI_V2"
_DEFAULT_CKPT_DIR = (
    _WORKSPACE_ROOT
    / "Models"
    / "TransFuser"
    / "checkpoints"
    / "models_2022"
    / "transfuser"
)


class TransFuserController(EpisodeController):
    name = "transfuser"
    track = "B"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.tcfg = dict(self.config.get("transfuser") or {})

        self.world = None
        self.ego = None
        self.carla = None
        self.inference = None
        self._map = None
        self._road_option = None
        self._straight_option = None

        self._sensor_lock = threading.Lock()
        self._latest_transfuser: dict[str, tuple[int, Any]] = {}
        self._sensor_actors: list[Any] = []

        self._lat_ref = 42.0
        self._lon_ref = 2.0
        self._route_end = None
        self._last_route_update_t = -1e9

        self.action_repeat = int(self.tcfg.get("action_repeat", 2))
        self.sim_dt = float(self.tcfg.get("sim_dt", 0.05))
        self.sensor_timeout_s = float(self.tcfg.get("sensor_timeout_s", 0.5))
        self.route_horizon_m = float(self.tcfg.get("route_horizon_m", 160.0))
        self.route_step_m = float(self.tcfg.get("route_step_m", 1.0))
        self.route_refresh_distance_m = float(
            self.tcfg.get("route_refresh_distance_m", 45.0)
        )
        self.route_refresh_period_s = float(self.tcfg.get("route_refresh_period_s", 2.0))

        self._step_count = 0
        self._inference_count = 0
        self._repeat_left = 0
        self._last_control = None
        self._last_input_frames: dict[str, int] = {}
        self._setup_error: Optional[str] = None

        self._logger = self.config.get("_episode_logger")
        self._debug_dir = self._resolve_debug_dir()
        self._trace_fh = None
        self._trace_writer = None
        self._debug_saved = 0
        self._debug_save_every_n = int(self.tcfg.get("save_debug_every_n", 20))
        self._debug_max_frames = int(self.tcfg.get("max_debug_frames", 12))
        self._log_every_n = int(self.tcfg.get("log_every_n", 10))

    # ------------------------------------------------------------------
    def setup(
        self,
        world: Any,
        ego: Any,
        ground_truth: Dict[str, Any],
        carla: Any,
    ) -> None:
        del ground_truth  # Track B: route and control never use episode labels.
        self.world = world
        self.ego = ego
        self.carla = carla
        self._map = world.get_map() if world is not None else None

        try:
            self._prepare_debug_outputs()
            self._prepare_import_paths()
            from app.config import (
                ACTION_REPEAT,
                CAM_FOV,
                CAM_FRONT_TF,
                CAM_H,
                CAM_LEFT_TF,
                CAM_RIGHT_TF,
                CAM_W,
                LIDAR_CHANNELS,
                LIDAR_LOWER_FOV,
                LIDAR_PPS,
                LIDAR_RANGE,
                LIDAR_ROT_HZ,
                LIDAR_TF,
                LIDAR_UPPER_FOV,
                SIM_DT,
            )
            from app.inference import TransFuserInference
            from agents.navigation.local_planner import RoadOption

            self.action_repeat = int(self.tcfg.get("action_repeat", ACTION_REPEAT))
            self.sim_dt = float(self.tcfg.get("sim_dt", SIM_DT))
            self._road_option = RoadOption.LANEFOLLOW
            self._straight_option = getattr(RoadOption, "STRAIGHT", RoadOption.LANEFOLLOW)
            self._lat_ref, self._lon_ref = self._read_latlon_ref(world)

            ckpt_dir = self._resolve_path(
                self.tcfg.get("ckpt_dir")
                or os.environ.get("TRANSFUSER_CKPT_DIR")
                or str(_DEFAULT_CKPT_DIR)
            )
            if not os.path.isdir(ckpt_dir):
                raise FileNotFoundError(f"TransFuser checkpoint dir not found: {ckpt_dir}")
            if not any(name.endswith(".pth") for name in os.listdir(ckpt_dir)):
                raise FileNotFoundError(f"No .pth checkpoints found in {ckpt_dir}")

            log.info("Loading TransFuser ensemble from %s", ckpt_dir)
            self.inference = TransFuserInference(
                ckpt_dir,
                transfuser_src=self._resolve_path(
                    self.tcfg.get("transfuser_src")
                    or str(_DEFAULT_UI_ROOT / "transfuser" / "team_code_transfuser")
                ),
                leaderboard_root=self._resolve_path(
                    self.tcfg.get("leaderboard_root")
                    or str(_DEFAULT_UI_ROOT / "transfuser" / "leaderboard")
                ),
                scenario_runner_root=self._resolve_path(
                    self.tcfg.get("scenario_runner_root")
                    or str(_DEFAULT_UI_ROOT / "transfuser" / "scenario_runner")
                ),
            )
            self._attach_transfuser_sensors(
                camera_specs=(
                    ("rgb_front", CAM_FRONT_TF),
                    ("rgb_left", CAM_LEFT_TF),
                    ("rgb_right", CAM_RIGHT_TF),
                ),
                cam_w=CAM_W,
                cam_h=CAM_H,
                cam_fov=CAM_FOV,
                lidar_tf=LIDAR_TF,
                lidar_channels=LIDAR_CHANNELS,
                lidar_range=LIDAR_RANGE,
                lidar_pps=LIDAR_PPS,
                lidar_rot_hz=LIDAR_ROT_HZ,
                lidar_upper_fov=LIDAR_UPPER_FOV,
                lidar_lower_fov=LIDAR_LOWER_FOV,
            )
            gps_plan, world_plan = self._build_lane_follow_plan()
            self._apply_global_plan(gps_plan, world_plan, sim_time=0.0)
            load_info = self._checkpoint_load_info(ckpt_dir)

            sensors = None
            try:
                sensors = self.inference.agent.sensors()
            except Exception:
                pass
            self._log_event(
                "transfuser_setup",
                ckpt_dir=ckpt_dir,
                model_count=getattr(self.inference.agent, "model_count", None),
                checkpoint_files=load_info.get("checkpoint_files"),
                load_info=load_info,
                precision="fp32",
                backbone=getattr(self.inference.agent, "backbone", None),
                action_repeat=self.action_repeat,
                sim_dt=self.sim_dt,
                sensor_count=len(self._sensor_actors),
                model_sensors=sensors,
                route_waypoints=len(world_plan),
                route_source="map_waypoints_lane_follow",
            )
            log.info(
                "TransFuser controller ready: sensors=%d route_waypoints=%d",
                len(self._sensor_actors),
                len(world_plan),
            )
        except Exception as exc:  # noqa: BLE001
            self._setup_error = str(exc)
            log.exception("TransFuser controller setup failed")
            self.teardown()
            raise

    def _checkpoint_load_info(self, ckpt_dir: str) -> dict:
        try:
            import torch
        except Exception as exc:  # noqa: BLE001
            return {"error": f"torch import failed: {exc}", "checkpoint_files": []}

        try:
            files = [name for name in os.listdir(ckpt_dir) if name.endswith(".pth")]
        except Exception as exc:  # noqa: BLE001
            return {"error": f"checkpoint directory listing failed: {exc}", "checkpoint_files": []}

        nets = list(getattr(getattr(self.inference, "agent", None), "nets", []) or [])
        details = []
        total_missing = 0
        total_unexpected = 0
        total_shape_mismatch = 0
        for idx, file_name in enumerate(files):
            path = os.path.join(ckpt_dir, file_name)
            try:
                raw = torch.load(path, map_location="cpu")
                if isinstance(raw, dict):
                    state = raw.get("state_dict") or raw.get("model") or raw.get("net") or raw
                else:
                    state = raw
                if not isinstance(state, dict):
                    raise TypeError(f"checkpoint payload is {type(state).__name__}")
                state_dict = {
                    (str(key)[7:] if str(key).startswith("module.") else str(key)): value
                    for key, value in state.items()
                }
                net = nets[idx] if idx < len(nets) else (nets[0] if nets else None)
                model_state = net.state_dict() if net is not None else {}
                missing = [key for key in model_state.keys() if key not in state_dict]
                unexpected = [key for key in state_dict.keys() if key not in model_state]
                shape_mismatch = []
                for key in set(model_state.keys()).intersection(state_dict.keys()):
                    model_shape = tuple(getattr(model_state[key], "shape", ()))
                    ckpt_shape = tuple(getattr(state_dict[key], "shape", ()))
                    if model_shape != ckpt_shape:
                        shape_mismatch.append(key)
                total_missing += len(missing)
                total_unexpected += len(unexpected)
                total_shape_mismatch += len(shape_mismatch)
                details.append(
                    {
                        "checkpoint": path,
                        "state_dict_keys": len(state_dict),
                        "model_state_keys": len(model_state),
                        "missing": len(missing),
                        "unexpected": len(unexpected),
                        "shape_mismatch": len(shape_mismatch),
                        "missing_keys": missing[:8],
                        "unexpected_keys": unexpected[:8],
                        "shape_mismatch_keys": shape_mismatch[:8],
                        "full_load": (
                            len(missing) == 0
                            and len(unexpected) == 0
                            and len(shape_mismatch) == 0
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                details.append(
                    {
                        "checkpoint": path,
                        "error": repr(exc),
                        "full_load": False,
                    }
                )
        return {
            "checkpoint_dir": ckpt_dir,
            "checkpoint_files": files,
            "model_count": len(nets),
            "checkpoints": details,
            "missing": total_missing,
            "unexpected": total_unexpected,
            "shape_mismatch": total_shape_mismatch,
            "full_load": bool(details)
            and all(item.get("full_load") is True for item in details),
        }

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        del dt
        if self.carla is None:
            return None
        if self._setup_error or self.inference is None:
            return self._fallback_control(brake=0.7)

        obs = observation or {}
        sim_time = float(obs.get("sim_time") or 0.0)
        self._step_count += 1
        self._maybe_refresh_route(sim_time)

        frame = self._current_world_frame()
        self._wait_for_synced_sensors(frame, self.sensor_timeout_s)
        tf_input = self._latest_transfuser_input(frame)
        if tf_input is None:
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

        input_frames = {key: int(tf_input[key][0]) for key in _TRANSFUSER_KEYS}
        stale = any(sample_frame < frame for sample_frame in input_frames.values())

        if self._last_control is not None and self._repeat_left > 0:
            self._repeat_left -= 1
            control = self._copy_control(self._last_control)
            self._write_trace(
                sim_time=sim_time,
                frame=frame,
                mode="hold",
                control=control,
                speed=float(tf_input["speed"][1]["speed"]),
                input_frames=input_frames,
                stale=stale,
            )
            return control

        try:
            result = self.inference.step(tf_input, sim_time)
            self._inference_count += 1
            control = result["raw"]
            error = ""
            if not self._control_is_finite(control):
                error = "nonfinite_control"
                log.warning(
                    "TransFuser returned non-finite control at t=%.2f: %r",
                    sim_time,
                    result,
                )
                control = self._copy_control(self._last_control) or self._fallback_control(
                    brake=0.7
                )
            else:
                self._last_control = self._copy_control(control)
                self._repeat_left = max(0, self.action_repeat - 1)
        except Exception as exc:  # noqa: BLE001
            self._inference_count += 1
            log.exception("TransFuser inference failed at t=%.2f", sim_time)
            error = str(exc)
            control = self._copy_control(self._last_control) or self._fallback_control(
                brake=0.7
            )

        self._last_input_frames = input_frames
        self._maybe_save_debug_artifacts(tf_input, sim_time, frame, control)
        self._write_trace(
            sim_time=sim_time,
            frame=frame,
            mode="infer",
            control=control,
            speed=float(tf_input["speed"][1]["speed"]),
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
                log.debug("TransFuser sensor destroy failed: %s", exc)
        self._sensor_actors.clear()
        with self._sensor_lock:
            self._latest_transfuser.clear()
        if self._trace_fh is not None:
            try:
                self._trace_fh.close()
            except Exception:
                pass
        self._trace_fh = None
        self._trace_writer = None

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _prepare_import_paths(self) -> None:
        ensure_agents_on_path()
        ui_root = self._resolve_path(self.tcfg.get("ui_root") or str(_DEFAULT_UI_ROOT))
        if ui_root not in sys.path:
            sys.path.insert(0, ui_root)

    def _attach_transfuser_sensors(
        self,
        *,
        camera_specs: tuple[tuple[str, tuple[float, float, float, float, float, float]], ...],
        cam_w: int,
        cam_h: int,
        cam_fov: float,
        lidar_tf: tuple[float, float, float, float, float, float],
        lidar_channels: int,
        lidar_range: float,
        lidar_pps: int,
        lidar_rot_hz: float,
        lidar_upper_fov: float,
        lidar_lower_fov: float,
    ) -> None:
        world = self.world
        ego = self.ego
        carla = self.carla
        if world is None or ego is None or carla is None:
            raise RuntimeError("world, ego, and carla must be available before sensors")

        bp_lib = world.get_blueprint_library()
        for sensor_id, tf_values in camera_specs:
            bp = bp_lib.find("sensor.camera.rgb")
            bp.set_attribute("image_size_x", str(cam_w))
            bp.set_attribute("image_size_y", str(cam_h))
            bp.set_attribute("fov", str(cam_fov))
            camera = world.spawn_actor(
                bp,
                self._transform_from_tuple(tf_values),
                attach_to=ego,
            )
            camera.listen(self._make_camera_callback(sensor_id))
            self._sensor_actors.append(camera)

        lidar_bp = bp_lib.find("sensor.lidar.ray_cast")
        lidar_bp.set_attribute("channels", str(lidar_channels))
        lidar_bp.set_attribute("range", str(lidar_range))
        lidar_bp.set_attribute("points_per_second", str(lidar_pps))
        lidar_bp.set_attribute("rotation_frequency", str(lidar_rot_hz))
        lidar_bp.set_attribute("upper_fov", str(lidar_upper_fov))
        lidar_bp.set_attribute("lower_fov", str(lidar_lower_fov))
        lidar = world.spawn_actor(
            lidar_bp,
            self._transform_from_tuple(lidar_tf),
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

        gnss_bp = bp_lib.find("sensor.other.gnss")
        gnss = world.spawn_actor(gnss_bp, carla.Transform(), attach_to=ego)
        gnss.listen(self._gnss_callback)
        self._sensor_actors.append(gnss)

    def _build_lane_follow_plan(self) -> tuple[list, list]:
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
        self._route_end = plan.route_end
        return plan.gps_plan, plan.world_plan

    def _apply_global_plan(self, gps_plan: list, world_plan: list, sim_time: float) -> None:
        if self.inference is None:
            return
        self.inference.set_global_plan(gps_plan, world_plan)
        try:
            route_planner = self.inference.agent._route_planner
            route_planner.set_route(gps_plan, True)
            route_planner.is_last = False
        except Exception:
            pass
        self._last_route_update_t = float(sim_time)
        end = self._route_end
        self._log_event(
            "transfuser_route",
            t=sim_time,
            waypoints=len(world_plan),
            end=(
                {"x": float(end.x), "y": float(end.y), "z": float(end.z)}
                if end is not None
                else None
            ),
            source="map_waypoints_lane_follow",
            road_option="LANEFOLLOW",
        )

    def _maybe_refresh_route(self, sim_time: float) -> None:
        if self._route_end is None or self.ego is None or self.inference is None:
            return
        if sim_time - self._last_route_update_t < self.route_refresh_period_s:
            return
        try:
            distance_to_end = self.ego.get_location().distance(self._route_end)
        except Exception:
            distance_to_end = float("inf")
        route_planner_last = False
        try:
            route_planner_last = bool(self.inference.agent._route_planner.is_last)
        except Exception:
            pass
        if distance_to_end > self.route_refresh_distance_m and not route_planner_last:
            return
        try:
            gps_plan, world_plan = self._build_lane_follow_plan()
            self._apply_global_plan(gps_plan, world_plan, sim_time)
        except Exception as exc:  # noqa: BLE001
            log.debug("TransFuser route refresh failed: %s", exc)

    # ------------------------------------------------------------------
    # Sensor callbacks / input assembly
    # ------------------------------------------------------------------
    def _make_camera_callback(self, sensor_id: str) -> Any:
        def _callback(image: Any) -> None:
            array = np.frombuffer(image.raw_data, dtype=np.uint8)
            bgr = array.reshape((image.height, image.width, 4))[:, :, :3].copy()
            with self._sensor_lock:
                self._latest_transfuser[sensor_id] = (int(image.frame), bgr)

        return _callback

    def _lidar_callback(self, measurement: Any) -> None:
        points = np.frombuffer(measurement.raw_data, dtype=np.float32).reshape((-1, 4)).copy()
        with self._sensor_lock:
            self._latest_transfuser["lidar"] = (int(measurement.frame), points)

    def _imu_callback(self, measurement: Any) -> None:
        accel = measurement.accelerometer
        gyro = measurement.gyroscope
        imu = [
            float(accel.x),
            float(accel.y),
            float(accel.z),
            float(gyro.x),
            float(gyro.y),
            float(measurement.compass),
        ]
        with self._sensor_lock:
            self._latest_transfuser["imu"] = (int(measurement.frame), imu)

    def _gnss_callback(self, measurement: Any) -> None:
        if self.ego is not None:
            loc = self.ego.get_location()
            lat, lon = self._world_xy_to_latlon(loc.x, loc.y)
            gps = [lat, lon, float(loc.z)]
        else:
            gps = [
                float(measurement.latitude),
                float(measurement.longitude),
                float(getattr(measurement, "altitude", 0.0)),
            ]
        with self._sensor_lock:
            self._latest_transfuser["gps"] = (int(measurement.frame), gps)

    def _wait_for_synced_sensors(self, frame: Optional[int], timeout_s: float) -> None:
        if frame is None:
            return
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            with self._sensor_lock:
                synced = all(
                    key in self._latest_transfuser
                    and self._latest_transfuser[key][0] >= frame
                    for key in _TRANSFUSER_KEYS
                )
            if synced or time.monotonic() >= deadline:
                return
            time.sleep(0.001)

    def _latest_transfuser_input(self, frame: Optional[int]) -> Optional[dict[str, Any]]:
        with self._sensor_lock:
            if any(key not in self._latest_transfuser for key in _TRANSFUSER_KEYS):
                return None
            if frame is not None and any(
                self._latest_transfuser[key][0] < frame for key in _TRANSFUSER_KEYS
            ):
                return None
            data = {key: self._latest_transfuser[key] for key in _TRANSFUSER_KEYS}
        if frame is None:
            frame = max(sample_frame for sample_frame, _ in data.values())
        data["speed"] = (int(frame), {"speed": self._ego_speed_mps()})
        return data

    # ------------------------------------------------------------------
    # Debug / telemetry
    # ------------------------------------------------------------------
    def _resolve_debug_dir(self) -> Optional[str]:
        raw = self.tcfg.get("debug_dir") or os.environ.get("MARSHAL_TRANSFUSER_DEBUG_DIR")
        if not raw:
            return None
        return self._resolve_path(raw)

    def _prepare_debug_outputs(self) -> None:
        if not self._debug_dir:
            return
        os.makedirs(self._debug_dir, exist_ok=True)
        self._trace_fh = open(
            os.path.join(self._debug_dir, "transfuser_trace.csv"),
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
                "rgb_front_frame",
                "rgb_left_frame",
                "rgb_right_frame",
                "lidar_frame",
                "imu_frame",
                "gps_frame",
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
                    "rgb_front_frame": input_frames.get("rgb_front", ""),
                    "rgb_left_frame": input_frames.get("rgb_left", ""),
                    "rgb_right_frame": input_frames.get("rgb_right", ""),
                    "lidar_frame": input_frames.get("lidar", ""),
                    "imu_frame": input_frames.get("imu", ""),
                    "gps_frame": input_frames.get("gps", ""),
                    "stale": bool(stale),
                    "error": str(error)[:200],
                }
            )
            self._trace_fh.flush()
        if self._log_every_n > 0 and (
            self._step_count <= 5 or (self._step_count % self._log_every_n) == 0
        ):
            log.info(
                "TransFuser t=%.2f frame=%s mode=%s v=%.2f thr=%.3f brk=%.3f steer=%.3f stale=%s err=%s",
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
        tf_input: dict[str, Any],
        sim_time: float,
        frame: Optional[int],
        control: Any,
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

            rgb_front = tf_input["rgb_front"][1][:, :, ::-1]
            img = Image.fromarray(rgb_front)
            draw = ImageDraw.Draw(img)
            label = (
                f"t={sim_time:.2f}s frame={frame} "
                f"thr={float(getattr(control, 'throttle', 0.0)):.2f} "
                f"brk={float(getattr(control, 'brake', 0.0)):.2f} "
                f"steer={float(getattr(control, 'steer', 0.0)):.2f}"
            )
            draw.rectangle((0, 0, min(img.width, 680), 30), fill=(0, 0, 0))
            draw.text((8, 8), label, fill=(255, 255, 255))
            img.save(os.path.join(self._debug_dir, f"front_{self._debug_saved:03d}.png"))

            lidar_img = self._lidar_debug_image(tf_input["lidar"][1])
            lidar_img.save(os.path.join(self._debug_dir, f"lidar_{self._debug_saved:03d}.png"))
            self._debug_saved += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("TransFuser debug artifact save failed: %s", exc)

    @staticmethod
    def _lidar_debug_image(points: np.ndarray) -> Any:
        from PIL import Image, ImageDraw

        size = 512
        img = Image.new("RGB", (size, size), (5, 5, 5))
        draw = ImageDraw.Draw(img)
        if points.size:
            xy = points[:, :2]
            x = xy[:, 0]
            y = xy[:, 1]
            keep = (x > -25.0) & (x < 55.0) & (y > -35.0) & (y < 35.0)
            x = x[keep]
            y = y[keep]
            px = ((y + 35.0) / 70.0 * (size - 1)).astype(np.int32)
            py = ((55.0 - x) / 80.0 * (size - 1)).astype(np.int32)
            for ix, iy in zip(px[::2], py[::2]):
                if 0 <= ix < size and 0 <= iy < size:
                    img.putpixel((int(ix), int(iy)), (120, 220, 120))
        draw.line((size // 2, size - 1, size // 2, size - 40), fill=(220, 220, 220))
        draw.text((8, 8), "LiDAR topdown: forward up", fill=(255, 255, 255))
        return img

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
        out.manual_gear_shift = bool(getattr(control, "manual_gear_shift", False))
        try:
            out.gear = int(getattr(control, "gear", 0) or 0)
        except Exception:
            pass
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
    def _angle_delta(a: float, b: float) -> float:
        return angle_delta(a, b)

    def _read_latlon_ref(self, world: Any) -> tuple[float, float]:
        return read_latlon_ref(world)

    def _world_xy_to_latlon(self, world_x: float, world_y: float) -> tuple[float, float]:
        return world_xy_to_latlon(world_x, world_y, self._lat_ref, self._lon_ref)

    def _location_to_gps(self, location: Any) -> dict[str, float]:
        return location_to_gps(location, self._lat_ref, self._lon_ref)


__all__ = ["TransFuserController"]
