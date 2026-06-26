"""DriveLM adapter scaffold for MARSHAL."""
from __future__ import annotations

from typing import Optional

from marshal_bench.controllers._trajectory_planner_common import (
    BasePlannerBackend,
    SensorSpec,
    TrajectoryPlannerControllerBase,
    find_workspace_root,
)

_DRIVELM_ROOT = find_workspace_root() / "Models" / "DriveLM"


class DriveLMController(TrajectoryPlannerControllerBase):
    """Placeholder scaffold for a future DriveLM trajectory backend."""

    name = "drivelm"
    config_key = "drivelm"

    def sensor_specs(self) -> tuple[SensorSpec, ...]:
        return (
            SensorSpec("front_rgb", x=1.5, y=0.0, z=2.3, width=800, height=600, fov=100.0),
            SensorSpec("imu", kind="imu"),
            SensorSpec("gnss", kind="gnss"),
        )

    def _load_backend(self) -> BasePlannerBackend:
        raise RuntimeError(
            "DriveLM is blocked as a MARSHAL full-planning controller today. "
            f"The public repo at {_DRIVELM_ROOT} exposes DriveLM/VQA challenge code and notes "
            "DriveLM-CARLA inference as TODO, but no public CARLA trajectory checkpoint or "
            "ready planner entrypoint was found."
        )


__all__ = ["DriveLMController"]
