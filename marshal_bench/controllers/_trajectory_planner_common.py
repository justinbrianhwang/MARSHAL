"""Shared scaffolding for slow trajectory-planner adapters.

The controllers in this module are Track-B wrappers: they ignore scenario
ground truth, own their CARLA sensors, build a non-privileged lane-follow route
from the map, and convert predicted ego-frame waypoints to VehicleControl.
"""
from __future__ import annotations

import csv
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from marshal_bench.controllers.base import EpisodeController
from marshal_bench.controllers.lane_route import build_lane_follow_plan, read_latlon_ref
from marshal_bench.utils.carla_api_compat import ensure_agents_on_path

log = logging.getLogger("marshal_bench.controllers.trajectory_planner")


@dataclass(frozen=True)
class SensorSpec:
    sensor_id: str
    kind: str = "camera"
    x: float = 1.3
    y: float = 0.0
    z: float = 2.3
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    width: int = 800
    height: int = 600
    fov: float = 100.0
    range_m: float = 85.0
    channels: int = 32
    points_per_second: int = 600000
    rotation_frequency: float = 10.0
    upper_fov: float = 10.0
    lower_fov: float = -30.0


class BasePlannerBackend:
    """Small backend interface used by the CARLA-facing controllers."""

    name = "base"

    def predict_waypoints(self, payload: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:
        """Release model/session resources."""


def find_workspace_root(markers: Iterable[str] = ("Models",)) -> Path:
    here = Path(__file__).resolve()
    marker = Path(*markers)
    for parent in (here.parent, *here.parents):
        if (parent / marker).exists():
            return parent
    return here.parents[2]


def parse_numeric_pairs(text: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for a, b in re.findall(r"([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)", text or ""):
        try:
            x = float(a)
            y = float(b)
        except ValueError:
            continue
        if math.isfinite(x) and math.isfinite(y):
            pairs.append((x, y))
    return pairs


def integrate_speed_curvature(
    pairs: Iterable[tuple[float, float]],
    *,
    dt: float = 0.5,
    max_points: int = 10,
) -> np.ndarray:
    """Integrate speed/curvature pairs into ego-frame x-forward/y-left waypoints."""

    x = 0.0
    y = 0.0
    yaw = 0.0
    out: list[tuple[float, float]] = []
    for speed, curvature in pairs:
        if len(out) >= max_points:
            break
        speed = float(np.clip(speed, -5.0, 35.0))
        curvature = float(np.clip(curvature, -0.8, 0.8))
        yaw += speed * curvature * dt
        x += speed * dt * math.cos(yaw)
        y += speed * dt * math.sin(yaw)
        out.append((x, y))
    return np.asarray(out, dtype=np.float32)


def finite_waypoints(value: Any) -> np.ndarray:
    arr = np.asarray(value, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(f"waypoints must be [T,2+], got shape {arr.shape}")
    arr = arr[:, :2]
    arr = arr[np.isfinite(arr).all(axis=1)]
    if arr.size == 0:
        raise ValueError("no finite waypoints")
    return arr.astype(np.float32, copy=False)


class TrajectoryPlannerControllerBase(EpisodeController):
    """Common route, sensor, trace, and waypoint-control plumbing."""

    name = "trajectory_planner"
    track = "B"
    config_key = "trajectory_planner"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.mcfg = dict(self.config.get(self.config_key) or {})

        self.world = None
        self.ego = None
        self.carla = None
        self._road_option = None
        self._lat_ref = 42.0
        self._lon_ref = 2.0
        self._route: list[tuple[Any, Any]] = []
        self._route_end = None
        self._last_route_update_t = -1e9

        self.backend: Optional[BasePlannerBackend] = None
        self.backend_info: dict[str, Any] = {}

        self._sensor_lock = threading.Lock()
        self._latest: dict[str, tuple[int, Any]] = {}
        self._sensor_actors: list[Any] = []

        self.sim_dt = float(self.mcfg.get("sim_dt", 0.05))
        self.sensor_timeout_s = float(self.mcfg.get("sensor_timeout_s", 0.75))
        self.route_horizon_m = float(self.mcfg.get("route_horizon_m", 160.0))
        self.route_step_m = float(self.mcfg.get("route_step_m", 1.0))
        self.route_refresh_distance_m = float(self.mcfg.get("route_refresh_distance_m", 35.0))
        self.route_refresh_period_s = float(self.mcfg.get("route_refresh_period_s", 1.0))
        self.lookahead_m = float(self.mcfg.get("lookahead_m", 5.0))
        self.query_period_s = float(self.mcfg.get("query_period_s", 1.0))
        self.target_speed_mps = float(
            self.mcfg.get("target_speed_mps")
            or (float(self.mcfg.get("target_speed_kmh", 25.0)) / 3.6)
        )
        self.max_throttle = float(self.mcfg.get("max_throttle", 0.65))
        self.max_brake = float(self.mcfg.get("max_brake", 0.85))
        self.max_steer = float(self.mcfg.get("max_steer", 0.9))
        self.wheelbase_m = float(self.mcfg.get("wheelbase_m", 2.7))
        self.max_steer_angle_rad = float(self.mcfg.get("max_steer_angle_rad", 0.7))

        self._step_count = 0
        self._inference_count = 0
        self._last_query_t = -1e9
        self._last_waypoints: Optional[np.ndarray] = None
        self._last_control = None
        self._last_metadata: dict[str, Any] = {}
        self._last_steer = 0.0
        self._setup_error: Optional[str] = None

        self._logger = self.config.get("_episode_logger")
        self._debug_dir = self._resolve_debug_dir()
        self._trace_fh = None
        self._trace_writer = None
        self._log_every_n = int(self.mcfg.get("log_every_n", 10))
        self._save_debug_every_n = int(self.mcfg.get("save_debug_every_n", 0))
        self._max_debug_frames = int(self.mcfg.get("max_debug_frames", 0))
        self._saved_debug_frames = 0
        self._saved_debug_frame_keys: set[tuple[str, int]] = set()
        self._close_backend_on_teardown = bool(self.mcfg.get("close_backend_on_teardown", True))
        self._raise_on_planner_error = bool(self.mcfg.get("raise_on_planner_error", False))
        self._sensor_startup_grace_s = float(self.mcfg.get("sensor_startup_grace_s", 0.5))

    def setup(
        self,
        world: Any,
        ego: Any,
        ground_truth: dict[str, Any],
        carla: Any,
    ) -> None:
        del ground_truth
        self.world = world
        self.ego = ego
        self.carla = carla
        self._prepare_debug_outputs()
        try:
            ensure_agents_on_path()
            try:
                from agents.navigation.local_planner import RoadOption

                self._road_option = RoadOption.LANEFOLLOW
            except Exception:
                self._road_option = None
            self._lat_ref, self._lon_ref = read_latlon_ref(world)
            self._refresh_route(sim_time=0.0)
            self.backend = self._load_backend()
            self._attach_sensors()
            self._log_event(
                f"{self.name}_setup",
                backend=getattr(self.backend, "name", None),
                backend_info=self.backend_info,
                checkpoint=self.backend_info.get("model_dir") or self.backend_info.get("checkpoint"),
                precision=self.backend_info.get("dtype") or self.mcfg.get("precision") or "fp32",
                load_info=self.backend_info,
                sensor_count=len(self._sensor_actors),
                sensor_specs=[spec.__dict__ for spec in self.sensor_specs()],
                route_waypoints=len(self._route),
                route_source="map_waypoints_lane_follow",
                query_period_s=self.query_period_s,
            )
            log.info(
                "%s controller ready: backend=%s sensors=%d route_waypoints=%d",
                self.name,
                getattr(self.backend, "name", None),
                len(self._sensor_actors),
                len(self._route),
            )
        except Exception as exc:  # noqa: BLE001
            self._setup_error = str(exc)
            log.exception("%s controller setup failed", self.name)
            self.teardown()
            raise

    def step(self, observation: dict[str, Any], dt: float) -> Any:
        if self.carla is None:
            return None
        if self.backend is None:
            return self._fallback_control(brake=0.7)

        obs = observation or {}
        sim_time = float(obs.get("sim_time") or 0.0)
        self._step_count += 1
        self._maybe_refresh_route(sim_time)

        frame = self._current_world_frame()
        self._wait_for_synced_sensors(frame, self.sensor_timeout_s)
        samples = self._latest_synced_sensors(frame)
        if samples is not None:
            self._maybe_save_debug_frames(samples, frame)
        input_frames = self._input_frames(samples or {})
        stale = self._stale(frame, input_frames)
        mode = "hold"
        error = ""
        latency_s = 0.0

        if samples is None and self._last_waypoints is None:
            control = self._copy_control(self._last_control) or self._fallback_control(brake=0.2)
            self._write_trace(
                sim_time=sim_time,
                frame=frame,
                mode="no_synced_sensors",
                control=control,
                speed=self._ego_speed_mps(obs),
                input_frames=input_frames,
                stale=True,
                waypoint_count=0,
                latency_s=0.0,
                target_point=(0.0, 0.0),
                error="missing_or_stale_sensor",
            )
            if self._raise_on_planner_error and sim_time >= self._sensor_startup_grace_s:
                raise RuntimeError("missing_or_stale_sensor")
            return control

        planner_exc: Optional[Exception] = None
        try:
            if self._should_query(sim_time) and samples is not None:
                payload = self._build_payload(obs, samples, frame, sim_time, dt)
                t0 = time.perf_counter()
                waypoints, metadata = self.backend.predict_waypoints(payload)
                latency_s = time.perf_counter() - t0
                self._last_waypoints = finite_waypoints(waypoints)
                self._last_metadata = dict(metadata or {})
                self._last_metadata["latency_s"] = latency_s
                self._last_metadata["input_frames"] = input_frames
                self._last_query_t = sim_time
                self._inference_count += 1
                mode = "planner"
                self._log_planner_query(sim_time, frame, self._last_waypoints, self._last_metadata)
            elif self._last_waypoints is None:
                raise RuntimeError("no planner waypoints available")

            control, ctrl_meta = self._control_from_waypoints(
                self._last_waypoints,
                speed_mps=self._ego_speed_mps(obs),
                dt=dt,
                desired_speed_override=self._last_metadata.get("planned_speed_mps"),
            )
            self._last_metadata.update(ctrl_meta)
            if not self._control_is_finite(control):
                raise RuntimeError("non-finite control")
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            planner_exc = exc
            control = self._copy_control(self._last_control) or self._fallback_control(brake=0.45)
            mode = "fallback"

        self._last_control = self._copy_control(control)
        self._write_trace(
            sim_time=sim_time,
            frame=frame,
            mode=mode,
            control=control,
            speed=self._ego_speed_mps(obs),
            input_frames=input_frames,
            stale=stale,
            waypoint_count=0 if self._last_waypoints is None else len(self._last_waypoints),
            latency_s=latency_s or float(self._last_metadata.get("latency_s") or 0.0),
            target_point=self._last_metadata.get("aim", (0.0, 0.0)),
            error=error,
        )
        if planner_exc is not None and self._raise_on_planner_error:
            raise RuntimeError(error) from planner_exc
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
                log.debug("%s sensor destroy failed: %s", self.name, exc)
        self._sensor_actors.clear()
        with self._sensor_lock:
            self._latest.clear()
        if self.backend is not None and self._close_backend_on_teardown:
            try:
                self.backend.close()
            except Exception:
                pass
        self.backend = None
        if self._trace_fh is not None:
            try:
                self._trace_fh.close()
            except Exception:
                pass
        self._trace_fh = None
        self._trace_writer = None

    def sensor_specs(self) -> tuple[SensorSpec, ...]:
        return (SensorSpec("front_rgb"),)

    def required_sensor_ids(self) -> tuple[str, ...]:
        return tuple(spec.sensor_id for spec in self.sensor_specs() if spec.kind != "speed")

    def _load_backend(self) -> BasePlannerBackend:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Route and payload helpers
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
        self._log_event(
            f"{self.name}_route",
            t=sim_time,
            waypoints=len(self._route),
            source="map_waypoints_lane_follow",
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
            self._refresh_route(sim_time)

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
        return target or self._route[-1][0].location

    def _target_point_ego(self) -> tuple[float, float]:
        ego_tf = self.ego.get_transform()
        loc = ego_tf.location
        target = self._target_location()
        dx = float(target.x - loc.x)
        dy = float(target.y - loc.y)
        yaw = math.radians(float(ego_tf.rotation.yaw))
        fwd_x, fwd_y = math.cos(yaw), math.sin(yaw)
        right_x, right_y = math.cos(yaw + math.pi / 2.0), math.sin(yaw + math.pi / 2.0)
        x_forward = dx * fwd_x + dy * fwd_y
        y_left = -(dx * right_x + dy * right_y)
        return float(x_forward), float(y_left)

    def _route_command(self) -> str:
        if not self._route:
            self._refresh_route(0.0)
        option = self._route[0][1] if self._route else self._road_option
        name = str(getattr(option, "name", "") or "").upper()
        if "LEFT" in name:
            return "TURN_LEFT"
        if "RIGHT" in name:
            return "TURN_RIGHT"
        if "STRAIGHT" in name:
            return "GO_STRAIGHT"
        return "LANE_FOLLOW"

    def _build_payload(
        self,
        obs: dict[str, Any],
        samples: dict[str, tuple[int, Any]],
        frame: Optional[int],
        sim_time: float,
        dt: float,
    ) -> dict[str, Any]:
        images = {
            key: value
            for key, (_sample_frame, value) in samples.items()
            if isinstance(value, np.ndarray) and value.ndim == 3
        }
        front = images.get("front_rgb")
        if front is None:
            front = images.get("rgb")
        if front is None:
            front = images.get("CAM_FRONT")
        return {
            "frame": frame,
            "sim_time": sim_time,
            "dt": dt,
            "images": images,
            "front_rgb": front,
            "lidar": samples.get("lidar", (None, None))[1],
            "imu": samples.get("imu", (None, None))[1],
            "gnss": samples.get("gnss", (None, None))[1],
            "speed_mps": self._ego_speed_mps(obs),
            "target_point": self._target_point_ego(),
            "route_command": self._route_command(),
            "ego_pose": self._ego_pose_dict(),
            "can_bus": self._can_bus(),
            "calibration": self._calibration_dict(),
            "meta": {"controller": self.name},
        }

    def _should_query(self, sim_time: float) -> bool:
        if self._last_waypoints is None:
            return True
        return (sim_time - self._last_query_t) >= self.query_period_s

    # ------------------------------------------------------------------
    # Sensors
    # ------------------------------------------------------------------
    def _attach_sensors(self) -> None:
        world = self.world
        ego = self.ego
        carla = self.carla
        if world is None or ego is None or carla is None:
            raise RuntimeError("world, ego, and carla are required before sensors")
        bp_lib = world.get_blueprint_library()
        for spec in self.sensor_specs():
            if spec.kind == "camera":
                bp = bp_lib.find("sensor.camera.rgb")
                bp.set_attribute("image_size_x", str(spec.width))
                bp.set_attribute("image_size_y", str(spec.height))
                bp.set_attribute("fov", str(spec.fov))
                if bp.has_attribute("sensor_tick"):
                    bp.set_attribute("sensor_tick", str(self.sim_dt))
                actor = world.spawn_actor(bp, self._transform_from_spec(spec), attach_to=ego)
                actor.listen(self._make_camera_callback(spec.sensor_id))
            elif spec.kind == "lidar":
                bp = bp_lib.find("sensor.lidar.ray_cast")
                attrs = {
                    "range": spec.range_m,
                    "channels": spec.channels,
                    "points_per_second": spec.points_per_second,
                    "rotation_frequency": spec.rotation_frequency,
                    "upper_fov": spec.upper_fov,
                    "lower_fov": spec.lower_fov,
                }
                for key, value in attrs.items():
                    if bp.has_attribute(key):
                        bp.set_attribute(key, str(value))
                if bp.has_attribute("sensor_tick"):
                    bp.set_attribute("sensor_tick", str(self.sim_dt))
                actor = world.spawn_actor(bp, self._transform_from_spec(spec), attach_to=ego)
                actor.listen(self._make_lidar_callback(spec.sensor_id))
            elif spec.kind == "imu":
                bp = bp_lib.find("sensor.other.imu")
                if bp.has_attribute("sensor_tick"):
                    bp.set_attribute("sensor_tick", str(self.sim_dt))
                actor = world.spawn_actor(bp, self._transform_from_spec(spec), attach_to=ego)
                actor.listen(self._make_imu_callback(spec.sensor_id))
            elif spec.kind == "gnss":
                bp = bp_lib.find("sensor.other.gnss")
                if bp.has_attribute("sensor_tick"):
                    bp.set_attribute("sensor_tick", str(self.sim_dt))
                actor = world.spawn_actor(bp, self._transform_from_spec(spec), attach_to=ego)
                actor.listen(self._make_gnss_callback(spec.sensor_id))
            else:
                continue
            self._sensor_actors.append(actor)

    def _make_camera_callback(self, sensor_id: str) -> Any:
        def _callback(image: Any) -> None:
            array = np.frombuffer(image.raw_data, dtype=np.uint8)
            bgra = array.reshape((image.height, image.width, 4))
            rgb = bgra[:, :, :3][:, :, ::-1].copy()
            with self._sensor_lock:
                self._latest[sensor_id] = (int(image.frame), rgb)

        return _callback

    def _make_lidar_callback(self, sensor_id: str) -> Any:
        def _callback(points: Any) -> None:
            arr = np.frombuffer(points.raw_data, dtype=np.float32).reshape((-1, 4)).copy()
            with self._sensor_lock:
                self._latest[sensor_id] = (int(points.frame), arr)

        return _callback

    def _make_imu_callback(self, sensor_id: str) -> Any:
        def _callback(measurement: Any) -> None:
            compass = float(getattr(measurement, "compass", 0.0) or 0.0)
            if not math.isfinite(compass):
                compass = 0.0
            gyro = getattr(measurement, "gyroscope", None)
            accel = getattr(measurement, "accelerometer", None)
            data = {
                "compass": compass,
                "gyro": [
                    float(getattr(gyro, "x", 0.0) or 0.0),
                    float(getattr(gyro, "y", 0.0) or 0.0),
                    float(getattr(gyro, "z", 0.0) or 0.0),
                ],
                "accel": [
                    float(getattr(accel, "x", 0.0) or 0.0),
                    float(getattr(accel, "y", 0.0) or 0.0),
                    float(getattr(accel, "z", 0.0) or 0.0),
                ],
            }
            with self._sensor_lock:
                self._latest[sensor_id] = (int(measurement.frame), data)

        return _callback

    def _make_gnss_callback(self, sensor_id: str) -> Any:
        def _callback(measurement: Any) -> None:
            data = {
                "lat": float(getattr(measurement, "latitude", 0.0) or 0.0),
                "lon": float(getattr(measurement, "longitude", 0.0) or 0.0),
                "alt": float(getattr(measurement, "altitude", 0.0) or 0.0),
            }
            with self._sensor_lock:
                self._latest[sensor_id] = (int(measurement.frame), data)

        return _callback

    def _wait_for_synced_sensors(self, frame: Optional[int], timeout_s: float) -> None:
        if frame is None:
            return
        deadline = time.monotonic() + max(0.0, timeout_s)
        keys = self.required_sensor_ids()
        while True:
            with self._sensor_lock:
                synced = all(key in self._latest and self._latest[key][0] >= frame for key in keys)
            if synced or time.monotonic() >= deadline:
                return
            time.sleep(0.001)

    def _latest_synced_sensors(self, frame: Optional[int]) -> Optional[dict[str, tuple[int, Any]]]:
        keys = self.required_sensor_ids()
        with self._sensor_lock:
            if any(key not in self._latest for key in keys):
                return None
            if frame is not None and any(self._latest[key][0] < frame for key in keys):
                return None
            return {key: self._latest[key] for key in keys}

    def _maybe_save_debug_frames(
        self,
        samples: dict[str, tuple[int, Any]],
        frame: Optional[int],
    ) -> None:
        if not self._debug_dir or self._max_debug_frames <= 0:
            return
        should_save = self._saved_debug_frames == 0
        if not should_save and self._save_debug_every_n > 0:
            should_save = (self._step_count % self._save_debug_every_n) == 0
        if not should_save:
            return
        if self._saved_debug_frames >= self._max_debug_frames:
            return
        try:
            from PIL import Image
        except Exception:
            return
        os.makedirs(self._debug_dir, exist_ok=True)
        saved_this_tick = False
        for sensor_id, (sample_frame, value) in samples.items():
            if self._saved_debug_frames >= self._max_debug_frames:
                break
            if not isinstance(value, np.ndarray) or value.ndim != 3:
                continue
            key = (sensor_id, int(sample_frame))
            if key in self._saved_debug_frame_keys:
                continue
            name = "front" if sensor_id in {"front_rgb", "rgb_front", "CAM_FRONT"} else f"input_{sensor_id}"
            path = os.path.join(self._debug_dir, f"{name}_{self._saved_debug_frames:03d}.png")
            try:
                Image.fromarray(np.asarray(value, dtype=np.uint8)).save(path)
            except Exception as exc:  # noqa: BLE001
                log.debug("%s debug frame save failed: %s", self.name, exc)
                continue
            self._saved_debug_frame_keys.add(key)
            self._saved_debug_frames += 1
            saved_this_tick = True
            self._log_event(
                f"{self.name}_debug_frame",
                frame=frame,
                sensor_id=sensor_id,
                sensor_frame=int(sample_frame),
                path=path,
            )
        if saved_this_tick:
            log.info("%s saved debug RGB frame(s) in %s", self.name, self._debug_dir)

    # ------------------------------------------------------------------
    # Control helpers
    # ------------------------------------------------------------------
    def _control_from_waypoints(
        self,
        waypoints: np.ndarray,
        *,
        speed_mps: float,
        dt: float,
        desired_speed_override: Any = None,
    ) -> tuple[Any, dict[str, Any]]:
        arr = finite_waypoints(waypoints)
        forward = arr[arr[:, 0] > 0.2]
        if len(forward) == 0:
            forward = arr
        target = forward[-1]
        for candidate in forward:
            if float(np.linalg.norm(candidate)) >= self.lookahead_m:
                target = candidate
                break

        tx = float(target[0])
        ty = float(target[1])
        lah = max(0.5, math.hypot(tx, ty))
        alpha = math.atan2(ty, tx)
        steer_angle = math.atan2(2.0 * self.wheelbase_m * math.sin(alpha), lah)
        steer = steer_angle / max(0.1, self.max_steer_angle_rad)
        steer = self._clamp(steer, -self.max_steer, self.max_steer)
        smoothing = self._clamp(float(self.mcfg.get("steer_smoothing", 0.35)), 0.0, 0.95)
        steer = smoothing * self._last_steer + (1.0 - smoothing) * steer
        self._last_steer = steer

        desired_speed_source = "waypoints"
        try:
            override_speed = float(desired_speed_override)
        except Exception:
            override_speed = float("nan")
        if math.isfinite(override_speed):
            desired_speed = float(np.clip(override_speed, 0.0, 35.0))
            desired_speed_source = "planner"
        else:
            desired_speed = self._desired_speed_from_waypoints(arr)
            if desired_speed <= 0.1:
                desired_speed = self.target_speed_mps
                desired_speed_source = "default"
        err = desired_speed - float(speed_mps)
        throttle = self._clamp(float(self.mcfg.get("speed_kp", 0.35)) * err, 0.0, self.max_throttle)
        brake = 0.0
        if err < -0.5:
            throttle = 0.0
            brake = self._clamp(float(self.mcfg.get("brake_gain", 0.35)) * (-err), 0.0, self.max_brake)
        control = self._make_control(throttle=throttle, brake=brake, steer=steer)
        return control, {
            "aim": (tx, ty),
            "desired_speed_mps": desired_speed,
            "desired_speed_source": desired_speed_source,
            "speed": float(speed_mps),
            "steer_angle_rad": steer_angle,
        }

    def _desired_speed_from_waypoints(self, arr: np.ndarray) -> float:
        if len(arr) < 2:
            return self.target_speed_mps
        diffs = np.diff(arr[: min(len(arr), 6)], axis=0)
        dists = np.linalg.norm(diffs, axis=1)
        return float(np.clip(float(np.mean(dists) / max(self.query_period_s, 0.5)), 0.0, 35.0))

    def _make_control(self, *, throttle: float, brake: float, steer: float) -> Any:
        control = self.carla.VehicleControl()
        control.throttle = self._clamp(throttle, 0.0, self.max_throttle)
        control.brake = self._clamp(brake, 0.0, self.max_brake)
        control.steer = self._clamp(steer, -1.0, 1.0)
        return control

    def _fallback_control(self, brake: float = 0.0) -> Any:
        return self._make_control(throttle=0.0, brake=brake, steer=self._last_steer)

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
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(value)))

    # ------------------------------------------------------------------
    # Trace, metadata, and generic state
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
                "waypoint_count",
                "latency_s",
                "target_x",
                "target_y",
                "input_frames",
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
        waypoint_count: int,
        latency_s: float,
        target_point: Any,
        error: str = "",
    ) -> None:
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
                    "throttle": round(float(getattr(control, "throttle", 0.0) or 0.0), 5),
                    "brake": round(float(getattr(control, "brake", 0.0) or 0.0), 5),
                    "steer": round(float(getattr(control, "steer", 0.0) or 0.0), 5),
                    "waypoint_count": int(waypoint_count),
                    "latency_s": round(float(latency_s), 4),
                    "target_x": round(float(tx), 4),
                    "target_y": round(float(ty), 4),
                    "input_frames": dict(input_frames),
                    "stale": bool(stale),
                    "error": str(error)[:200],
                }
            )
            self._trace_fh.flush()
        if self._log_every_n > 0 and (
            self._step_count <= 5 or (self._step_count % self._log_every_n) == 0
        ):
            log.info(
                "%s t=%.2f frame=%s mode=%s v=%.2f thr=%.3f brk=%.3f steer=%.3f "
                "wps=%d lat=%.2fs stale=%s err=%s",
                self.name,
                sim_time,
                frame,
                mode,
                speed,
                float(getattr(control, "throttle", 0.0) or 0.0),
                float(getattr(control, "brake", 0.0) or 0.0),
                float(getattr(control, "steer", 0.0) or 0.0),
                waypoint_count,
                latency_s,
                stale,
                error or "-",
            )

    def _log_planner_query(
        self,
        sim_time: float,
        frame: Optional[int],
        waypoints: np.ndarray,
        metadata: dict[str, Any],
    ) -> None:
        payload = dict(metadata or {})
        payload["waypoints"] = np.asarray(waypoints, dtype=float).round(4).tolist()
        payload["waypoint_count"] = int(len(waypoints))
        payload["query_index"] = int(self._inference_count)
        payload["sim_time"] = round(float(sim_time), 3)
        payload["frame"] = frame
        self._log_event(f"{self.name}_planner_query", **self._json_safe(payload))

    def _json_safe(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, np.generic):
            return self._json_safe(value.item())
        if isinstance(value, np.ndarray):
            return self._json_safe(value.tolist())
        if isinstance(value, dict):
            return {str(k): self._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._json_safe(v) for v in value]
        return str(value)

    def _log_event(self, name: str, **payload: Any) -> None:
        logger = self._logger
        if logger is not None and hasattr(logger, "log_event"):
            try:
                logger.log_event(name, **payload)
            except Exception:
                pass

    def _resolve_path(self, path: Any) -> str:
        p = Path(os.fspath(path))
        if not p.is_absolute():
            p = find_workspace_root() / p
        return str(p.resolve())

    def _transform_from_spec(self, spec: SensorSpec) -> Any:
        return self.carla.Transform(
            self.carla.Location(x=float(spec.x), y=float(spec.y), z=float(spec.z)),
            self.carla.Rotation(
                roll=float(spec.roll),
                pitch=float(spec.pitch),
                yaw=float(spec.yaw),
            ),
        )

    def _current_world_frame(self) -> Optional[int]:
        try:
            return int(self.world.get_snapshot().frame)
        except Exception:
            return None

    def _ego_speed_mps(self, obs: Optional[dict[str, Any]] = None) -> float:
        obs = obs or {}
        if "ego_speed" in obs:
            try:
                return float(obs["ego_speed"])
            except Exception:
                pass
        try:
            velocity = self.ego.get_velocity()
            return math.sqrt(velocity.x * velocity.x + velocity.y * velocity.y + velocity.z * velocity.z)
        except Exception:
            return 0.0

    def _ego_pose_dict(self) -> dict[str, Any]:
        tf = self.ego.get_transform()
        loc = tf.location
        rot = tf.rotation
        return {
            "translation": [float(loc.x), float(loc.y), float(loc.z)],
            "rotation_euler_deg": [float(rot.roll), float(rot.pitch), float(rot.yaw)],
        }

    def _can_bus(self) -> list[float]:
        speed = self._ego_speed_mps()
        yaw = 0.0
        try:
            yaw = math.radians(float(self.ego.get_transform().rotation.yaw))
        except Exception:
            pass
        return [speed, 0.0, 0.0, 0.0, yaw, 0.0]

    def _calibration_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for spec in self.sensor_specs():
            if spec.kind != "camera":
                continue
            f = spec.width / (2.0 * math.tan(math.radians(spec.fov) / 2.0))
            out[spec.sensor_id] = {
                "intrinsic": [
                    [f, 0.0, spec.width / 2.0],
                    [0.0, f, spec.height / 2.0],
                    [0.0, 0.0, 1.0],
                ],
                "extrinsic": {
                    "x": spec.x,
                    "y": spec.y,
                    "z": spec.z,
                    "roll": spec.roll,
                    "pitch": spec.pitch,
                    "yaw": spec.yaw,
                },
            }
        return out

    def _input_frames(self, samples: dict[str, tuple[int, Any]]) -> dict[str, int]:
        return {key: int(value[0]) for key, value in samples.items()}

    def _stale(self, frame: Optional[int], input_frames: dict[str, int]) -> bool:
        return bool(frame is not None and any(sample_frame < frame for sample_frame in input_frames.values()))


__all__ = [
    "BasePlannerBackend",
    "SensorSpec",
    "TrajectoryPlannerControllerBase",
    "find_workspace_root",
    "finite_waypoints",
    "integrate_speed_curvature",
    "parse_numeric_pairs",
]
