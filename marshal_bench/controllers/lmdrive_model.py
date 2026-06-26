"""LMDrive adapter scaffold for MARSHAL."""
from __future__ import annotations

from typing import Optional

from marshal_bench.controllers._trajectory_planner_common import (
    BasePlannerBackend,
    SensorSpec,
    TrajectoryPlannerControllerBase,
    find_workspace_root,
)

_LMD_ROOT = find_workspace_root() / "Models" / "LMDrive"


class LMDriveController(TrajectoryPlannerControllerBase):
    """Sensor scaffold for LMDrive's native CARLA planner.

    Native execution is intentionally blocked on this py3.12/cu128 stack until
    LMDrive's torch_scatter/OpenMMLab-era dependency set is available.
    """

    name = "lmdrive"
    config_key = "lmdrive"

    def sensor_specs(self) -> tuple[SensorSpec, ...]:
        return (
            SensorSpec("rgb_front", x=1.3, y=0.0, z=2.3, width=1200, height=900, fov=100.0),
            SensorSpec("rgb_left", x=1.3, y=-0.0, z=2.3, yaw=-60.0, width=400, height=300, fov=100.0),
            SensorSpec("rgb_right", x=1.3, y=0.0, z=2.3, yaw=60.0, width=400, height=300, fov=100.0),
            SensorSpec("rgb_rear", x=1.3, y=0.0, z=2.3, yaw=180.0, width=400, height=300, fov=100.0),
            SensorSpec("lidar", kind="lidar", x=1.3, y=0.0, z=2.5, yaw=-90.0),
            SensorSpec("imu", kind="imu"),
            SensorSpec("gnss", kind="gnss"),
        )

    def _load_backend(self) -> BasePlannerBackend:
        allow_native = bool(self.mcfg.get("allow_native", False))
        if not allow_native:
            raise RuntimeError(
                "LMDrive native backend is blocked on this stack. Code and weights are present "
                f"under {_LMD_ROOT}, but import failed offline at torch_scatter; set "
                "lmdrive.allow_native=true only after providing the original dependency stack."
            )
        return _LMDriveNativeBackend()


class _LMDriveNativeBackend(BasePlannerBackend):
    name = "lmdrive_native"

    def __init__(self) -> None:
        raise RuntimeError(
            "LMDrive native backend needs its Python 3.8/torch/cu102-era dependency stack "
            "including torch_scatter; it is not runnable in transfuser_ui today."
        )


__all__ = ["LMDriveController"]
