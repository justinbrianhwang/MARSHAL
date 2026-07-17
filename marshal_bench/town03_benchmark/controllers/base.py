"""Base interface for ego controllers in the MARSHAL Town03 benchmark.

Every controller (pure-pursuit baseline, TransFuser, VLM agent, …) must
expose two methods:

    setup(world, ego_vehicle, sensor_suite, route, stations)
        Called once before the loop starts. Use this to load model
        weights, set up subscribers, etc.

    step(observation, dt) -> carla.VehicleControl
        Called every simulation tick. `observation` is a dict with at
        minimum the sensor outputs (RGB, lidar, …) plus ego state
        (x, y, yaw, speed). Returns the control to apply this tick.

The benchmark runner is responsible for ticking the world, collecting
sensors, calling step(), and applying the control.
"""
from __future__ import annotations

from typing import Any, Dict


class BaseController:
    name: str = "base"

    def setup(self, world, ego, sensor_suite, route, stations) -> None:
        raise NotImplementedError

    def step(self, observation: Dict[str, Any], dt: float):
        """Return carla.VehicleControl."""
        raise NotImplementedError

    def teardown(self) -> None:
        """Optional cleanup."""
        pass
