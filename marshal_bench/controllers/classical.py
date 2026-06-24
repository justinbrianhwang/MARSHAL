"""Classical non-learned Track-B controllers for MARSHAL.

These controllers are lower bounds for Track B. They do not read the episode
ground truth and only use ego state plus a non-privileged CARLA map lane-follow
route, matching the route source used by the TransFuser adapter.
"""
from __future__ import annotations

import csv
import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, Optional

from marshal_bench.controllers.base import EpisodeController
from marshal_bench.controllers.lane_route import (
    angle_delta,
    build_lane_follow_plan,
    read_latlon_ref,
)
from marshal_bench.utils.carla_api_compat import ensure_agents_on_path

log = logging.getLogger("marshal_bench.controllers.classical")


class _LaneFollowController(EpisodeController):
    track = "B"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.ccfg: dict[str, Any] = {}
        self.world = None
        self.ego = None
        self.carla = None
        self._road_option = None
        self._lat_ref = 42.0
        self._lon_ref = 2.0
        self._route: list[tuple[Any, Any]] = []
        self._route_end = None
        self._last_route_update_t = -1e9

        self.target_speed_kmh = 25.0
        self.target_speed_mps = 25.0 / 3.6
        self.lookahead_m = 8.0
        self.route_horizon_m = 120.0
        self.route_step_m = 1.0
        self.route_refresh_distance_m = 25.0
        self.route_refresh_period_s = 1.0

        self._last_control = None
        self._last_lat_err = 0.0
        self._last_steer = 0.0
        self._step_count = 0

        self._logger = self.config.get("_episode_logger")
        self._debug_dir: Optional[str] = None
        self._trace_fh = None
        self._trace_writer = None
        self._debug_saved = 0
        self._debug_save_every_n = 25
        self._debug_max_frames = 4
        self._log_every_n = 20

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
        self._load_config()
        self._prepare_debug_outputs()
        try:
            ensure_agents_on_path()
            from agents.navigation.local_planner import RoadOption

            self._road_option = RoadOption.LANEFOLLOW
        except Exception:
            self._road_option = None
        self._lat_ref, self._lon_ref = read_latlon_ref(world)
        self._refresh_route(sim_time=0.0)
        self._log_event(
            "classical_setup",
            controller=self.name,
            target_speed_kmh=self.target_speed_kmh,
            lookahead_m=self.lookahead_m,
            route_waypoints=len(self._route),
            route_source="map_waypoints_lane_follow",
        )

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        if self.carla is None or self.ego is None:
            return None
        obs = observation or {}
        sim_time = float(obs.get("sim_time") or 0.0)
        self._step_count += 1
        self._maybe_refresh_route(sim_time)

        try:
            state = self._ego_state(obs)
            target_tf = self._rolling_target(self.lookahead_m)
            nearest_tf = self._nearest_route_transform(state["x"], state["y"])
            control, details = self._compute_control(state, target_tf, nearest_tf, dt)
            if not self._control_is_finite(control):
                raise RuntimeError("non-finite control")
        except Exception as exc:  # noqa: BLE001
            log.debug("%s control fallback: %s", self.name, exc)
            control = self._fallback_control(brake=0.35)
            details = {"error": str(exc), "mode": "fallback"}

        self._last_control = self._copy_control(control)
        self._maybe_save_debug_frame(obs, sim_time, control)
        self._write_trace(sim_time, obs, control, details)
        return control

    def teardown(self) -> None:
        if self._trace_fh is not None:
            try:
                self._trace_fh.close()
            except Exception:
                pass
        self._trace_fh = None
        self._trace_writer = None

    def _compute_control(
        self,
        state: dict[str, float],
        target_tf: Any,
        nearest_tf: Any,
        dt: float,
    ) -> tuple[Any, dict[str, Any]]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Configuration / route helpers
    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        common = dict(self.config.get("classical") or {})
        specific = dict(self.config.get(self.name) or {})
        common.update(specific)
        self.ccfg = common
        self.target_speed_kmh = float(
            common.get("target_speed_kmh")
            or (self.config.get("ego") or {}).get("target_speed")
            or 25.0
        )
        self.target_speed_mps = self.target_speed_kmh / 3.6
        self.lookahead_m = float(common.get("lookahead_m", self.lookahead_m))
        self.route_horizon_m = float(common.get("route_horizon_m", self.route_horizon_m))
        self.route_step_m = float(common.get("route_step_m", self.route_step_m))
        self.route_refresh_distance_m = float(
            common.get("route_refresh_distance_m", self.route_refresh_distance_m)
        )
        self.route_refresh_period_s = float(
            common.get("route_refresh_period_s", self.route_refresh_period_s)
        )
        self._debug_save_every_n = int(
            common.get("save_debug_every_n", self._debug_save_every_n)
        )
        self._debug_max_frames = int(common.get("max_debug_frames", self._debug_max_frames))
        self._log_every_n = int(common.get("log_every_n", self._log_every_n))
        raw_debug = common.get("debug_dir") or os.environ.get(
            f"MARSHAL_{self.name.upper()}_DEBUG_DIR"
        )
        self._debug_dir = str(Path(raw_debug).resolve()) if raw_debug else None

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
            "classical_route",
            controller=self.name,
            t=sim_time,
            waypoints=len(self._route),
            end=(
                {"x": float(end.x), "y": float(end.y), "z": float(end.z)}
                if end is not None
                else None
            ),
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

    def _rolling_target(self, lookahead_m: float) -> Any:
        if not self._route:
            self._refresh_route(0.0)
        ego_tf = self.ego.get_transform()
        loc = ego_tf.location
        fwd = ego_tf.get_forward_vector()
        best = None
        for transform, _option in self._route:
            tloc = transform.location
            dx = float(tloc.x - loc.x)
            dy = float(tloc.y - loc.y)
            ahead = dx * float(fwd.x) + dy * float(fwd.y)
            if ahead < -2.0:
                continue
            dist = math.hypot(dx, dy)
            if dist >= lookahead_m:
                return transform
            best = transform
        self._refresh_route(0.0)
        return best or self._route[-1][0]

    def _nearest_route_transform(self, x: float, y: float) -> Any:
        if not self._route:
            self._refresh_route(0.0)
        return min(
            (transform for transform, _option in self._route),
            key=lambda tf: (float(tf.location.x) - x) ** 2
            + (float(tf.location.y) - y) ** 2,
        )

    # ------------------------------------------------------------------
    # Control helpers
    # ------------------------------------------------------------------
    def _ego_state(self, obs: Dict[str, Any]) -> dict[str, float]:
        tf = self.ego.get_transform()
        vel = self.ego.get_velocity()
        speed = math.sqrt(vel.x * vel.x + vel.y * vel.y + vel.z * vel.z)
        return {
            "x": float(obs.get("ego_x", tf.location.x)),
            "y": float(obs.get("ego_y", tf.location.y)),
            "z": float(obs.get("ego_z", tf.location.z)),
            "yaw": math.radians(float(obs.get("ego_yaw", tf.rotation.yaw))),
            "yaw_deg": float(obs.get("ego_yaw", tf.rotation.yaw)),
            "speed": float(obs.get("ego_speed", speed)),
        }

    def _lane_errors(
        self,
        state: dict[str, float],
        target_tf: Any,
        nearest_tf: Any,
    ) -> dict[str, float]:
        lane_yaw = math.radians(float(nearest_tf.rotation.yaw))
        dx = float(state["x"] - nearest_tf.location.x)
        dy = float(state["y"] - nearest_tf.location.y)
        lateral_err = -math.sin(lane_yaw) * dx + math.cos(lane_yaw) * dy
        heading_err = math.radians(
            angle_delta(float(target_tf.rotation.yaw), float(state["yaw_deg"]))
        )
        tx = float(target_tf.location.x)
        ty = float(target_tf.location.y)
        target_heading_err = math.atan2(ty - state["y"], tx - state["x"]) - state["yaw"]
        target_heading_err = math.atan2(
            math.sin(target_heading_err), math.cos(target_heading_err)
        )
        return {
            "lateral_error_m": lateral_err,
            "heading_error_rad": heading_err,
            "target_heading_error_rad": target_heading_err,
            "target_x": tx,
            "target_y": ty,
        }

    def _speed_control(self, speed: float, dt: float) -> tuple[float, float, dict[str, float]]:
        raise NotImplementedError

    def _make_control(self, throttle: float, brake: float, steer: float) -> Any:
        ctrl = self.carla.VehicleControl()
        ctrl.throttle = self._clamp(throttle, 0.0, float(self.ccfg.get("max_throttle", 0.65)))
        ctrl.brake = self._clamp(brake, 0.0, float(self.ccfg.get("max_brake", 0.85)))
        ctrl.steer = self._clamp(steer, -1.0, 1.0)
        return ctrl

    def _fallback_control(self, brake: float = 0.0) -> Any:
        return self._make_control(0.0, brake, self._last_steer)

    def _copy_control(self, control: Any) -> Any:
        if control is None or self.carla is None:
            return None
        out = self.carla.VehicleControl()
        out.throttle = float(getattr(control, "throttle", 0.0) or 0.0)
        out.brake = float(getattr(control, "brake", 0.0) or 0.0)
        out.steer = float(getattr(control, "steer", 0.0) or 0.0)
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
    # Debug / telemetry
    # ------------------------------------------------------------------
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
                "target_speed_mps",
                "throttle",
                "brake",
                "steer",
                "target_x",
                "target_y",
                "lateral_error_m",
                "heading_error_rad",
                "stale",
                "error",
            ],
        )
        self._trace_writer.writeheader()
        self._trace_fh.flush()

    def _write_trace(
        self,
        sim_time: float,
        obs: Dict[str, Any],
        control: Any,
        details: Dict[str, Any],
    ) -> None:
        if self._trace_writer is not None:
            self._trace_writer.writerow(
                {
                    "sim_time": round(float(sim_time), 3),
                    "world_frame": self._world_frame(),
                    "mode": details.get("mode", "control"),
                    "speed_mps": round(float(details.get("speed", obs.get("ego_speed", 0.0))), 4),
                    "target_speed_mps": round(self.target_speed_mps, 4),
                    "throttle": round(float(getattr(control, "throttle", 0.0)), 5),
                    "brake": round(float(getattr(control, "brake", 0.0)), 5),
                    "steer": round(float(getattr(control, "steer", 0.0)), 5),
                    "target_x": round(float(details.get("target_x", 0.0)), 4),
                    "target_y": round(float(details.get("target_y", 0.0)), 4),
                    "lateral_error_m": round(float(details.get("lateral_error_m", 0.0)), 5),
                    "heading_error_rad": round(float(details.get("heading_error_rad", 0.0)), 5),
                    "stale": False,
                    "error": str(details.get("error", ""))[:200],
                }
            )
            self._trace_fh.flush()
        if self._log_every_n > 0 and (
            self._step_count <= 5 or self._step_count % self._log_every_n == 0
        ):
            log.info(
                "%s t=%.2f v=%.2f thr=%.3f brk=%.3f steer=%.3f lat=%.2f err=%s",
                self.name,
                sim_time,
                float(details.get("speed", obs.get("ego_speed", 0.0))),
                float(getattr(control, "throttle", 0.0)),
                float(getattr(control, "brake", 0.0)),
                float(getattr(control, "steer", 0.0)),
                float(details.get("lateral_error_m", 0.0)),
                details.get("error", "-") or "-",
            )

    def _maybe_save_debug_frame(
        self,
        obs: Dict[str, Any],
        sim_time: float,
        control: Any,
    ) -> None:
        if not self._debug_dir or self._debug_saved >= self._debug_max_frames:
            return
        if self._debug_save_every_n > 0 and (
            self._step_count > 1 and (self._step_count - 1) % self._debug_save_every_n != 0
        ):
            return
        image = obs.get("image")
        if image is None:
            return
        try:
            from PIL import Image, ImageDraw

            img = Image.fromarray(image)
            draw = ImageDraw.Draw(img)
            label = (
                f"{self.name} t={sim_time:.2f}s "
                f"thr={float(getattr(control, 'throttle', 0.0)):.2f} "
                f"brk={float(getattr(control, 'brake', 0.0)):.2f} "
                f"steer={float(getattr(control, 'steer', 0.0)):.2f}"
            )
            draw.rectangle((0, 0, min(img.width, 560), 30), fill=(0, 0, 0))
            draw.text((8, 8), label, fill=(255, 255, 255))
            img.save(os.path.join(self._debug_dir, f"input_{self._debug_saved:03d}.png"))
            self._debug_saved += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("%s debug frame save failed: %s", self.name, exc)

    def _world_frame(self) -> Optional[int]:
        try:
            return int(self.world.get_snapshot().frame)
        except Exception:
            return None

    def _log_event(self, name: str, **payload: Any) -> None:
        logger = self._logger
        if logger is not None and hasattr(logger, "log_event"):
            try:
                logger.log_event(name, **payload)
            except Exception:
                pass


class PIDController(_LaneFollowController):
    """Speed PID plus lane-center/heading PID controller."""

    name = "pid"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self._speed_integral = 0.0
        self._last_speed_error: Optional[float] = None

    def _compute_control(
        self,
        state: dict[str, float],
        target_tf: Any,
        nearest_tf: Any,
        dt: float,
    ) -> tuple[Any, dict[str, Any]]:
        errors = self._lane_errors(state, target_tf, nearest_tf)
        dt = max(float(dt), 1e-3)
        lat_err = errors["lateral_error_m"]
        lat_deriv = (lat_err - self._last_lat_err) / dt
        self._last_lat_err = lat_err

        k_heading = float(self.ccfg.get("heading_kp", 0.95))
        k_target = float(self.ccfg.get("target_heading_kp", 0.35))
        k_lat = float(self.ccfg.get("lateral_kp", 0.16))
        k_lat_d = float(self.ccfg.get("lateral_kd", 0.025))
        steer_raw = (
            k_heading * errors["heading_error_rad"]
            + k_target * errors["target_heading_error_rad"]
            - k_lat * lat_err
            - k_lat_d * lat_deriv
        )
        steer_limit = float(self.ccfg.get("steer_limit", 0.75))
        steer = self._clamp(steer_raw, -steer_limit, steer_limit)
        smoothing = self._clamp(float(self.ccfg.get("steer_smoothing", 0.45)), 0.0, 0.95)
        steer = smoothing * self._last_steer + (1.0 - smoothing) * steer
        self._last_steer = steer

        throttle, brake, speed_info = self._speed_control(state["speed"], dt)
        control = self._make_control(throttle, brake, steer)
        details = {
            **errors,
            **speed_info,
            "mode": "pid",
            "speed": state["speed"],
        }
        return control, details

    def _speed_control(self, speed: float, dt: float) -> tuple[float, float, dict[str, float]]:
        err = self.target_speed_mps - float(speed)
        self._speed_integral = self._clamp(
            self._speed_integral + err * dt,
            -float(self.ccfg.get("speed_integral_limit", 8.0)),
            float(self.ccfg.get("speed_integral_limit", 8.0)),
        )
        if self._last_speed_error is None:
            deriv = 0.0
        else:
            deriv = (err - self._last_speed_error) / max(dt, 1e-3)
        self._last_speed_error = err

        kp = float(self.ccfg.get("speed_kp", 0.30))
        ki = float(self.ccfg.get("speed_ki", 0.025))
        kd = float(self.ccfg.get("speed_kd", 0.015))
        cmd = kp * err + ki * self._speed_integral + kd * deriv
        if cmd >= 0.0:
            throttle = cmd
            brake = 0.0
        else:
            throttle = 0.0
            brake = -float(self.ccfg.get("brake_gain", 0.55)) * cmd
        return throttle, brake, {"speed_error": err, "speed_cmd": cmd}


class MPCController(_LaneFollowController):
    """Short-horizon sampling MPC over a kinematic bicycle model."""

    name = "mpc"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self.lookahead_m = 10.0
        self.route_step_m = 2.0
        self.route_horizon_m = 90.0

    def _compute_control(
        self,
        state: dict[str, float],
        target_tf: Any,
        nearest_tf: Any,
        dt: float,
    ) -> tuple[Any, dict[str, Any]]:
        del dt
        errors = self._lane_errors(state, target_tf, nearest_tf)
        steer_samples = self.ccfg.get(
            "steer_samples",
            [-0.60, -0.38, -0.20, -0.08, 0.0, 0.08, 0.20, 0.38, 0.60],
        )
        accel_samples = self.ccfg.get("accel_samples", [-2.0, -0.8, 0.0, 0.9, 1.6])
        horizon_steps = int(self.ccfg.get("horizon_steps", 12))
        sim_dt = float(self.ccfg.get("mpc_dt", 0.15))
        wheelbase = float(self.ccfg.get("wheelbase_m", 2.75))
        max_steer_rad = float(self.ccfg.get("max_steer_rad", 0.55))

        best_cost = float("inf")
        best = (0.0, 0.0)
        for steer_norm in steer_samples:
            steer_norm = float(steer_norm)
            delta = steer_norm * max_steer_rad
            for accel in accel_samples:
                accel = float(accel)
                cost = self._rollout_cost(
                    state,
                    steer_norm,
                    delta,
                    accel,
                    horizon_steps,
                    sim_dt,
                    wheelbase,
                    target_tf,
                )
                if cost < best_cost:
                    best_cost = cost
                    best = (steer_norm, accel)

        steer_norm, accel = best
        if accel >= 0.0:
            throttle = accel / max(float(self.ccfg.get("max_accel_mps2", 1.8)), 1e-3)
            brake = 0.0
        else:
            throttle = 0.0
            brake = -accel / max(float(self.ccfg.get("max_decel_mps2", 3.0)), 1e-3)
        smoothing = self._clamp(float(self.ccfg.get("steer_smoothing", 0.35)), 0.0, 0.95)
        steer = smoothing * self._last_steer + (1.0 - smoothing) * steer_norm
        self._last_steer = steer
        control = self._make_control(throttle, brake, steer)
        details = {
            **errors,
            "mode": "mpc",
            "speed": state["speed"],
            "mpc_cost": best_cost,
            "mpc_accel": accel,
        }
        return control, details

    def _speed_control(self, speed: float, dt: float) -> tuple[float, float, dict[str, float]]:
        del speed, dt
        return 0.0, 0.0, {}

    def _rollout_cost(
        self,
        state: dict[str, float],
        steer_norm: float,
        delta: float,
        accel: float,
        horizon_steps: int,
        sim_dt: float,
        wheelbase: float,
        target_tf: Any,
    ) -> float:
        x = float(state["x"])
        y = float(state["y"])
        yaw = float(state["yaw"])
        v = max(0.0, float(state["speed"]))
        cost = 0.0
        route_transforms = [transform for transform, _option in self._route]
        for k in range(max(1, horizon_steps)):
            x += v * math.cos(yaw) * sim_dt
            y += v * math.sin(yaw) * sim_dt
            yaw += (v / max(wheelbase, 1e-3)) * math.tan(delta) * sim_dt
            v = max(0.0, v + accel * sim_dt)
            lane_tf = min(
                route_transforms,
                key=lambda tf: (float(tf.location.x) - x) ** 2
                + (float(tf.location.y) - y) ** 2,
            )
            lane_yaw = math.radians(float(lane_tf.rotation.yaw))
            dx = x - float(lane_tf.location.x)
            dy = y - float(lane_tf.location.y)
            lat_err = -math.sin(lane_yaw) * dx + math.cos(lane_yaw) * dy
            heading_err = math.atan2(
                math.sin(lane_yaw - yaw),
                math.cos(lane_yaw - yaw),
            )
            speed_err = v - self.target_speed_mps
            cost += (
                float(self.ccfg.get("mpc_lateral_w", 1.6)) * lat_err * lat_err
                + float(self.ccfg.get("mpc_heading_w", 0.9)) * heading_err * heading_err
                + float(self.ccfg.get("mpc_speed_w", 0.04)) * speed_err * speed_err
            )
            cost += 0.015 * k * abs(steer_norm)

        tx = float(target_tf.location.x)
        ty = float(target_tf.location.y)
        final_dist = math.hypot(tx - x, ty - y)
        cost += float(self.ccfg.get("mpc_target_w", 0.35)) * final_dist * final_dist
        cost += float(self.ccfg.get("mpc_steer_w", 0.08)) * steer_norm * steer_norm
        cost += float(self.ccfg.get("mpc_accel_w", 0.03)) * accel * accel
        if v < 0.4 * self.target_speed_mps:
            cost += float(self.ccfg.get("mpc_low_speed_penalty", 2.0))
        return cost


__all__ = ["PIDController", "MPCController"]

