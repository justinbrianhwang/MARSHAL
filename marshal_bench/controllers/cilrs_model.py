"""Track-B CILRS controller adapter for MARSHAL."""
from __future__ import annotations

import logging
import os
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from marshal_bench.controllers._legacy_vision_common import (
    LegacyVisionControllerBase,
    find_workspace_root,
    load_checkpoint_with_counts,
    patch_torchvision_pretrained_download,
    scale_and_crop_rgb,
)

log = logging.getLogger("marshal_bench.controllers.cilrs")

_WORKSPACE_ROOT = find_workspace_root(("Models", "CILRS", "cilrs", "best_model.pth"))
_DEFAULT_SRC = _WORKSPACE_ROOT / "Models" / "CILRS" / "src"
_DEFAULT_CKPT = _WORKSPACE_ROOT / "Models" / "CILRS" / "cilrs" / "best_model.pth"


class CILRSController(LegacyVisionControllerBase):
    name = "cilrs"
    track = "B"
    camera_keys = ("rgb",)
    use_imu = False

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self.input_buffer: deque[Any] = deque()
        self.model_config = None
        self.scale = 1
        self.crop = 256

    def setup(self, world: Any, ego: Any, ground_truth: Dict[str, Any], carla: Any) -> None:
        del ground_truth
        self._setup_common(world, ego, carla)
        self._prepare_import_paths(self.mcfg.get("src_root") or _DEFAULT_SRC)
        self._load_model()
        self._refresh_route(sim_time=0.0)
        self._attach_sensors()
        self._log_event(
            "cilrs_setup",
            checkpoint=str(self._resolve_path(self.mcfg.get("ckpt_path") or _DEFAULT_CKPT)),
            src_root=str(self._resolve_path(self.mcfg.get("src_root") or _DEFAULT_SRC)),
            device=str(self.device),
            precision=self.precision,
            load_info=self.load_info,
            sensor_count=len(self._sensor_actors),
            route_waypoints=len(self._route),
            route_source="map_waypoints_lane_follow",
            camera_specs=self._camera_specs_for_log(),
        )
        log.info("CILRS controller ready: device=%s load=%s", self.device, self.load_info)

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        del dt
        if self.carla is None:
            return None
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
                error="missing_or_stale_camera",
            )
            return control

        input_frames = self._input_frames(samples)
        stale = self._stale(frame, input_frames)
        command = self._route_command()
        speed_mps = self._ego_speed_mps()
        rgb_crop = scale_and_crop_rgb(samples["rgb"][1], scale=self.scale, crop=self.crop)
        image_tensor = self._tensor_from_crop(rgb_crop)

        if len(self.input_buffer) < int(getattr(self.model_config, "seq_len", 1)):
            self.input_buffer.append(image_tensor)
            control = self._fallback_control()
            self._write_trace(
                sim_time=sim_time,
                frame=frame,
                mode="warmup",
                control=control,
                speed=speed_mps,
                input_frames=input_frames,
                stale=stale,
                command=command,
            )
            return control

        error = ""
        control = None
        try:
            self.input_buffer.popleft()
            self.input_buffer.append(image_tensor)
            velocity = self.torch.tensor([speed_mps], device=self.device, dtype=self.torch.float32)
            command_tensor = self.torch.tensor([float(command)], device=self.device, dtype=self.torch.float32)
            with self.torch.no_grad():
                feature = self.net.encoder(list(self.input_buffer))
                steer, throttle, brake, velocity_pred = self.net([feature], velocity, command_tensor)
            steer_f = float(steer.squeeze(0).item())
            throttle_f = float(throttle.squeeze(0).item())
            brake_f = float(brake.squeeze(0).item())
            if brake_f < 0.05:
                brake_f = 0.0
            if throttle_f > brake_f:
                brake_f = 0.0
            control = self._make_control(steer_f, throttle_f, brake_f)
            self._last_metadata = {
                "command": command,
                "velocity_pred": float(velocity_pred.squeeze().item()),
            }
            if not self._control_is_finite(control):
                error = "nonfinite_control"
                control = self._invalid_control()
            else:
                self._last_control = self._copy_control(control)
        except Exception as exc:  # noqa: BLE001
            log.exception("CILRS inference failed at t=%.2f", sim_time)
            error = repr(exc)
            control = self._invalid_control()

        self._inference_count += 1
        self._maybe_save_debug_images(
            {"rgb": rgb_crop},
            sim_time=sim_time,
            frame=frame,
            control=control,
        )
        self._write_trace(
            sim_time=sim_time,
            frame=frame,
            mode="infer",
            control=control,
            speed=speed_mps,
            input_frames=input_frames,
            stale=stale,
            command=command,
            error=error,
        )
        return control

    def _load_model(self) -> None:
        import torch

        self.torch = torch
        requested = str(self.mcfg.get("device") or "cuda")
        if requested.startswith("cuda") and not torch.cuda.is_available():
            requested = "cpu"
        self.device = torch.device(requested)
        patched = patch_torchvision_pretrained_download(("resnet18",))

        from cilrs.config import GlobalConfig
        from cilrs.model import CILRS

        self.model_config = GlobalConfig()
        self.scale = int(getattr(self.model_config, "scale", 1))
        self.crop = int(getattr(self.model_config, "input_resolution", 256))
        self.net = CILRS(self.model_config, self.device).to(self.device)
        ckpt_path = self._resolve_path(self.mcfg.get("ckpt_path") or _DEFAULT_CKPT)
        self.load_info = load_checkpoint_with_counts(torch, self.net, ckpt_path, self.device)
        self.load_info["torchvision_pretrained_download_patched"] = patched
        if not self.load_info.get("full_load"):
            raise RuntimeError(f"CILRS checkpoint did not fully load: {self.load_info}")
        self.net.eval()

    @property
    def workspace_root(self) -> Path:
        return _WORKSPACE_ROOT


__all__ = ["CILRSController"]
