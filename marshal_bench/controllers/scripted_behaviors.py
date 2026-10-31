"""Scripted behaviour controllers: PRIVILEGED TEST FIXTURES, not benchmark entries.

Each controller drives one fixed, instruction-independent longitudinal profile
so the scorer can be checked against known behaviour in real CARLA physics
(scoring-validity audit, credit-discrimination check). They read the
privileged stop-line location from the episode ground truth
(``requests_privileged_gt``) and must never appear on a leaderboard.

Select with ``--controller scripted_<behavior>``:

* ``normal_stop``     approach at ~10 km/h, stop with the front >= 1 m before the line (target 3.0 m)
* ``late_stop``       approach at ~8 km/h, stop with the front 0-1 m before the line (target 0.5 m)
* ``overshoot``       approach at ~10 km/h, stop with the front ~2.5 m past the line
* ``no_start``        full brake from t = 0
* ``lurch_then_stop`` ~8 km/h for ~1.6 m of travel, then hold
* ``constant_go``     hold ~20 km/h for the whole horizon

Lateral control is the lane-follow PID of :class:`PIDController`; only the
speed command is scripted. Calibration knobs (``config["scripted"]``):
``decel_mps2`` (assumed full-brake deceleration, default 6.0) and
``latency_s`` (control latency added to the stopping distance, default 0.1),
``lurch_m`` (travel before the lurch fixture holds, default 1.6).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from marshal_bench.controllers.classical import PIDController

# behavior -> (cruise km/h, front-gap stop target m or None, lurch travel m or None)
BEHAVIORS: Dict[str, tuple] = {
    "normal_stop": (10.0, 3.0, None),
    "late_stop": (8.0, 0.5, None),
    "overshoot": (10.0, -2.5, None),
    "no_start": (0.0, None, None),
    "lurch_then_stop": (8.0, None, 1.6),
    "constant_go": (20.0, None, None),
}


class BehaviorPolicy:
    """Pure longitudinal state machine. ``command`` returns a target speed in
    km/h, or ``None`` for HOLD (full brake). HOLD is latched once entered."""

    def __init__(self, behavior: str, decel_mps2: float = 6.0, latency_s: float = 0.1,
                 lurch_m: Optional[float] = None) -> None:
        if behavior not in BEHAVIORS:
            raise ValueError(f"unknown scripted behavior {behavior!r}; choose from {sorted(BEHAVIORS)}")
        self.behavior = behavior
        self.cruise_kmh, self.stop_gap_m, self.lurch_m = BEHAVIORS[behavior]
        if lurch_m is not None and self.lurch_m is not None:
            self.lurch_m = float(lurch_m)
        self.decel = float(decel_mps2)
        self.latency = float(latency_s)
        self.holding = False

    def command(self, speed_mps: float, front_gap_m: Optional[float], travelled_m: float) -> Optional[float]:
        if not self.holding:
            if self.cruise_kmh <= 0.0:
                self.holding = True
            elif self.lurch_m is not None and travelled_m >= self.lurch_m:
                self.holding = True
            elif self.stop_gap_m is not None and front_gap_m is not None:
                v = max(float(speed_mps), 0.0)
                stopping = v * v / (2.0 * self.decel) + v * self.latency
                if front_gap_m - stopping <= self.stop_gap_m:
                    self.holding = True
        return None if self.holding else self.cruise_kmh


class ScriptedBehaviorController(PIDController):
    track = "fixture"
    requests_privileged_gt = True  # reads the stop-line location; run is tagged privileged

    def __init__(self, behavior: str, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self.name = f"scripted_{behavior}"
        scfg = dict((config or {}).get("scripted") or {})
        self.policy = BehaviorPolicy(behavior, float(scfg.get("decel_mps2", 6.0)),
                                     float(scfg.get("latency_s", 0.1)), scfg.get("lurch_m"))
        self._origin = None
        self._fwd = (1.0, 0.0)
        self._stop_xy = None
        self._front_offset = 2.4
        self._gap: Optional[float] = None
        self._travelled = 0.0

    def setup(self, world: Any, ego: Any, ground_truth: Dict[str, Any], carla: Any) -> None:
        stop = (ground_truth or {}).get("stop_line")
        self._stop_xy = (float(stop["x"]), float(stop["y"])) if stop else None
        tf = ego.get_transform()
        self._origin = (float(tf.location.x), float(tf.location.y))
        yaw = math.radians(float(tf.rotation.yaw))
        self._fwd = (math.cos(yaw), math.sin(yaw))
        try:
            bb = ego.bounding_box
            self._front_offset = float(bb.location.x) + float(bb.extent.x)
        except Exception:
            pass
        super().setup(world, ego, ground_truth, carla)
        self.ccfg["max_brake"] = 1.0
        self._log_event("scripted_fixture_setup", controller=self.name, behavior=self.policy.behavior,
                        stop_line=stop, front_offset_m=self._front_offset, privileged=True)

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        obs = observation or {}
        if self._origin is not None and obs.get("ego_x") is not None:
            self._gap, self._travelled = front_gap_and_travel(
                (float(obs["ego_x"]), float(obs["ego_y"])), self._origin, self._fwd,
                self._stop_xy, self._front_offset)
        return super().step(observation, dt)

    def _speed_control(self, speed: float, dt: float):
        target = self.policy.command(speed, self._gap, self._travelled)
        info = {"scripted_gap_m": self._gap, "scripted_travelled_m": self._travelled}
        if target is None:
            return 0.0, 1.0, {**info, "scripted_mode": "hold"}
        self.target_speed_mps = target / 3.6
        throttle, brake, pid = super()._speed_control(speed, dt)
        return throttle, brake, {**pid, **info, "scripted_mode": "cruise"}


def front_gap_and_travel(ego_xy, origin_xy, fwd, stop_xy, front_offset_m):
    """(front bumper to stop line along the spawn heading, forward travel since setup).
    Same convention as telemetry ``stopline_front_m``: positive = line still ahead."""
    ex, ey = ego_xy[0] - origin_xy[0], ego_xy[1] - origin_xy[1]
    travelled = ex * fwd[0] + ey * fwd[1]
    gap = None
    if stop_xy is not None:
        sx, sy = stop_xy[0] - origin_xy[0], stop_xy[1] - origin_xy[1]
        gap = (sx * fwd[0] + sy * fwd[1]) - travelled - front_offset_m
    return gap, travelled


__all__ = ["BEHAVIORS", "BehaviorPolicy", "ScriptedBehaviorController", "front_gap_and_travel"]
