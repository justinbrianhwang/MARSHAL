"""OpenDriveVLA adapter scaffold for MARSHAL."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import numpy as np

from marshal_bench.controllers._trajectory_planner_common import (
    BasePlannerBackend,
    SensorSpec,
    TrajectoryPlannerControllerBase,
)

log = logging.getLogger("marshal_bench.controllers.opendrivevla")


class OpenDriveVLAController(TrajectoryPlannerControllerBase):
    """CARLA-facing client for the local OpenDriveVLA ZMQ inference server."""

    name = "opendrivevla"
    config_key = "opendrivevla"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self.query_period_s = float(self.mcfg.get("query_period_s", 0.5))
        self.lookahead_m = float(self.mcfg.get("lookahead_m", 5.0))

    def sensor_specs(self) -> tuple[SensorSpec, ...]:
        # CARLA y is right; these are the local nuScenes-style rig values with y flipped.
        return (
            SensorSpec("CAM_FRONT", x=1.72, y=0.0, z=1.50, yaw=0.0, width=1600, height=900, fov=70.0),
            SensorSpec("CAM_FRONT_LEFT", x=1.58, y=-0.49, z=1.51, yaw=-55.0, width=1600, height=900, fov=70.0),
            SensorSpec("CAM_FRONT_RIGHT", x=1.58, y=0.49, z=1.51, yaw=55.0, width=1600, height=900, fov=70.0),
            SensorSpec("CAM_BACK", x=-0.02, y=0.0, z=1.49, yaw=180.0, width=1600, height=900, fov=110.0),
            SensorSpec("CAM_BACK_LEFT", x=1.05, y=-0.48, z=1.49, yaw=-110.0, width=1600, height=900, fov=70.0),
            SensorSpec("CAM_BACK_RIGHT", x=1.05, y=0.48, z=1.49, yaw=110.0, width=1600, height=900, fov=70.0),
        )

    def _load_backend(self) -> BasePlannerBackend:
        endpoint = (
            self.mcfg.get("server")
            or self.mcfg.get("server_url")
            or os.environ.get("OPENDRIVEVLA_SERVER")
            or "tcp://127.0.0.1:5555"
        )
        timeout_ms = int(self.mcfg.get("timeout_ms", 10000))
        backend = _OpenDriveVLAZmqBackend(endpoint=str(endpoint), timeout_ms=timeout_ms)
        self.backend_info = {"server": str(endpoint), "timeout_ms": timeout_ms}
        return backend

    def _build_payload(
        self,
        obs: dict[str, Any],
        samples: dict[str, tuple[int, Any]],
        frame: Optional[int],
        sim_time: float,
        dt: float,
    ) -> dict[str, Any]:
        payload = super()._build_payload(obs, samples, frame, sim_time, dt)
        payload["tokens"] = {}
        return payload


class _OpenDriveVLAZmqBackend(BasePlannerBackend):
    name = "opendrivevla_zmq"

    def __init__(self, *, endpoint: str, timeout_ms: int) -> None:
        import msgpack
        import msgpack_numpy as mnp
        import zmq

        mnp.patch()
        self.msgpack = msgpack
        self.zmq = zmq
        self.endpoint = endpoint
        self.ctx = zmq.Context.instance()
        self.sock = self.ctx.socket(zmq.REQ)
        self.sock.setsockopt(zmq.RCVTIMEO, int(timeout_ms))
        self.sock.setsockopt(zmq.SNDTIMEO, int(timeout_ms))
        self.sock.connect(endpoint)

    def predict_waypoints(self, payload: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
        images = {
            key: np.asarray(value, dtype=np.uint8)
            for key, value in (payload.get("images") or {}).items()
            if str(key).startswith("CAM_")
        }
        if len(images) < 6:
            raise RuntimeError(f"OpenDriveVLA needs six cameras, got {sorted(images)}")
        req = {
            "v": "0.1.0",
            "kind": "obs",
            "frame_id": int(payload.get("frame") or 0),
            "timestamp": float(payload.get("sim_time") or time.time()),
            "images": images,
            "ego_pose": payload.get("ego_pose") or {},
            "calibration": payload.get("calibration") or {},
            "can_bus": list(payload.get("can_bus") or []),
            "command": payload.get("route_command") or "LANE_FOLLOW",
            "tokens": payload.get("tokens") or {},
            "meta": payload.get("meta") or {},
        }
        buf = self.msgpack.packb(req, use_bin_type=True)
        self.sock.send(buf)
        reply = self.msgpack.unpackb(self.sock.recv(), raw=False)
        traj = np.asarray(reply.get("trajectory"), dtype=np.float32)
        return traj, {
            "backend": self.name,
            "server_latency_ms": float(reply.get("latency_ms") or 0.0),
            "text": str(reply.get("text") or "")[:512],
            "reply_meta": reply.get("meta") or {},
        }

    def close(self) -> None:
        try:
            self.sock.close(0)
        except Exception:
            pass


__all__ = ["OpenDriveVLAController"]
