"""Pure-pursuit baseline controller for the MARSHAL Town03 benchmark.

A simple geometric tracker:
  - Locks on to a look-ahead waypoint a fixed distance ahead on the route
  - Computes steering via the pure-pursuit formula (arc to reach the LAH
    point given current heading and wheelbase)
  - Throttle is a P controller on target speed

This is NOT a scenario-aware driver — it ignores everything outside the
route waypoints. It exists only as a sanity check that the route is
drivable and as the lower bound for benchmark scoring.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from .base import BaseController


class PurePursuitController(BaseController):
    name = "pure_pursuit"

    def __init__(self, target_speed_kmh: float = 30.0,
                 lookahead_m: float = 6.0, wheelbase_m: float = 2.6):
        self.target_v = target_speed_kmh / 3.6
        self.lookahead = lookahead_m
        self.L = wheelbase_m
        self.route: List[Tuple[float, float, float, float]] = []
        self._wp_idx = 0

    def setup(self, world, ego, sensor_suite, route, stations) -> None:
        self.route = route["waypoints"]  # list of (x, y, z, yaw)
        self._wp_idx = self._nearest_index(ego.get_transform().location.x,
                                            ego.get_transform().location.y)

    def _nearest_index(self, ex: float, ey: float) -> int:
        best, best_d = 0, 1e18
        for i, (x, y, _z, _yaw) in enumerate(self.route):
            d = (ex - x) ** 2 + (ey - y) ** 2
            if d < best_d:
                best, best_d = i, d
        return best

    def step(self, observation: Dict[str, Any], dt: float):
        import carla
        ex = observation["ego_x"]; ey = observation["ego_y"]
        eyaw = math.radians(observation["ego_yaw"])
        speed = observation["ego_speed"]

        # Advance wp_idx to the closest forward point
        n = len(self.route)
        for _ in range(n):
            x, y, _z, _yaw = self.route[self._wp_idx]
            d = math.hypot(x - ex, y - ey)
            if d < self.lookahead * 0.7:
                self._wp_idx = (self._wp_idx + 1) % n
            else:
                break

        # Pick the lookahead waypoint
        for k in range(n):
            i = (self._wp_idx + k) % n
            x, y, _z, _yaw = self.route[i]
            if math.hypot(x - ex, y - ey) >= self.lookahead:
                tx, ty = x, y
                break
        else:
            tx, ty, *_ = self.route[(self._wp_idx + 1) % n]

        # Heading-frame target
        dx, dy = tx - ex, ty - ey
        alpha = math.atan2(dy, dx) - eyaw
        # Wrap to [-pi, pi]
        alpha = math.atan2(math.sin(alpha), math.cos(alpha))
        # Pure pursuit curvature
        lah = max(0.5, math.hypot(dx, dy))
        steer = math.atan2(2.0 * self.L * math.sin(alpha), lah)
        steer = max(-1.0, min(1.0, steer / 0.7))  # normalise to [-1, 1]

        # Throttle: P-controller on target speed
        v_err = self.target_v - speed
        throttle = max(0.0, min(0.8, 0.4 * v_err))
        brake = 0.0
        if v_err < -1.0:
            throttle = 0.0; brake = min(0.5, -0.2 * v_err)

        return carla.VehicleControl(throttle=throttle, steer=steer, brake=brake)
