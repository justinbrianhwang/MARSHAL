"""Track-B NEAT controller adapter for MARSHAL."""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional

from marshal_bench.controllers._legacy_vision_common import (
    LegacyVisionControllerBase,
    find_workspace_root,
    load_checkpoint_with_counts,
    patch_torchvision_pretrained_download,
    scale_and_crop_rgb,
)

log = logging.getLogger("marshal_bench.controllers.neat")

_WORKSPACE_ROOT = find_workspace_root(("Models", "NEAT", "neat", "best_encoder.pth"))
_DEFAULT_SRC = _WORKSPACE_ROOT / "Models" / "NEAT" / "src"
_DEFAULT_ENCODER = _WORKSPACE_ROOT / "Models" / "NEAT" / "neat" / "best_encoder.pth"
_DEFAULT_DECODER = _WORKSPACE_ROOT / "Models" / "NEAT" / "neat" / "best_decoder.pth"


class NEATController(LegacyVisionControllerBase):
    name = "neat"
    track = "B"
    camera_keys = ("rgb", "rgb_left", "rgb_right")
    use_imu = True

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self.input_buffer: dict[str, deque[Any]] = {
            "rgb": deque(),
            "rgb_left": deque(),
            "rgb_right": deque(),
        }
        self.model_config = None
        self.plan_grid = None
        self.light_grid = None
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
            "neat_setup",
            encoder_checkpoint=str(self._resolve_path(self.mcfg.get("encoder_ckpt") or _DEFAULT_ENCODER)),
            decoder_checkpoint=str(self._resolve_path(self.mcfg.get("decoder_ckpt") or _DEFAULT_DECODER)),
            src_root=str(self._resolve_path(self.mcfg.get("src_root") or _DEFAULT_SRC)),
            device=str(self.device),
            precision=self.precision,
            load_info=self.load_info,
            sensor_count=len(self._sensor_actors),
            route_waypoints=len(self._route),
            route_source="map_waypoints_lane_follow",
            camera_specs=self._camera_specs_for_log(),
        )
        log.info("NEAT controller ready: device=%s load=%s", self.device, self.load_info)

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
                error="missing_or_stale_sensor",
            )
            return control

        input_frames = self._input_frames(samples)
        stale = self._stale(frame, input_frames)
        speed_mps = self._ego_speed_mps()
        compass = float(samples["imu"][1])
        target_point = self._target_point(compass)
        crops = {
            key: scale_and_crop_rgb(samples[key][1], scale=self.scale, crop=self.crop)
            for key in self.camera_keys
        }
        tensors = {key: self._tensor_from_crop(crop) for key, crop in crops.items()}

        if any(len(buf) < int(getattr(self.model_config, "seq_len", 1)) for buf in self.input_buffer.values()):
            for key, tensor in tensors.items():
                self.input_buffer[key].append(tensor)
            control = self._fallback_control()
            self._write_trace(
                sim_time=sim_time,
                frame=frame,
                mode="warmup",
                control=control,
                speed=speed_mps,
                input_frames=input_frames,
                stale=stale,
                target_point=target_point,
            )
            return control

        error = ""
        control = None
        try:
            for key, tensor in tensors.items():
                self.input_buffer[key].popleft()
                self.input_buffer[key].append(tensor)
            images = []
            for idx in range(int(getattr(self.model_config, "seq_len", 1))):
                images.append(self.input_buffer["rgb"][idx])
                if int(getattr(self.model_config, "num_camera", 3)) == 3:
                    images.append(self.input_buffer["rgb_left"][idx])
                    images.append(self.input_buffer["rgb_right"][idx])
            velocity = self.torch.tensor([speed_mps], device=self.device, dtype=self.torch.float32)
            target_tensor = self.torch.tensor(
                [[float(target_point[0])], [float(target_point[1])]],
                device=self.device,
                dtype=self.torch.float32,
            )
            with self.torch.no_grad():
                encoding = self.net.encoder(images, velocity)
                pred_waypoints, red_light_occ = self.net.plan(
                    target_tensor,
                    encoding,
                    self.plan_grid,
                    self.light_grid,
                    int(getattr(self.model_config, "plan_points", 1)),
                    int(getattr(self.model_config, "plan_iters", 1)),
                )
                steer, throttle, brake, metadata = self.net.control_pid(
                    pred_waypoints[:, int(getattr(self.model_config, "seq_len", 1)):],
                    velocity,
                    target_tensor,
                    red_light_occ,
                )
            brake_f = float(brake)
            throttle_f = float(throttle)
            steer_f = float(steer)
            if brake_f < 0.05:
                brake_f = 0.0
            if throttle_f > brake_f:
                brake_f = 0.0
            control = self._make_control(steer_f, throttle_f, brake_f)
            self._last_metadata = {
                **self._metadata_jsonable(metadata),
                "target_point": tuple(float(v) for v in target_point),
            }
            if not self._control_is_finite(control):
                error = "nonfinite_control"
                control = self._invalid_control()
            else:
                self._last_control = self._copy_control(control)
        except Exception as exc:  # noqa: BLE001
            log.exception("NEAT inference failed at t=%.2f", sim_time)
            error = repr(exc)
            control = self._invalid_control()

        self._inference_count += 1
        self._maybe_save_debug_images(
            crops,
            sim_time=sim_time,
            frame=frame,
            control=control,
            target_point=target_point,
        )
        self._write_trace(
            sim_time=sim_time,
            frame=frame,
            mode="infer",
            control=control,
            speed=speed_mps,
            input_frames=input_frames,
            stale=stale,
            target_point=target_point,
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
        patched = patch_torchvision_pretrained_download(("resnet34",))

        from neat.architectures import AttentionField
        from neat.config import GlobalConfig

        self.model_config = GlobalConfig()
        self.scale = int(getattr(self.model_config, "scale", 1))
        self.crop = int(getattr(self.model_config, "crop", 256))
        self.net = AttentionField(self.model_config, self.device).to(self.device)
        encoder_ckpt = self._resolve_path(self.mcfg.get("encoder_ckpt") or _DEFAULT_ENCODER)
        decoder_ckpt = self._resolve_path(self.mcfg.get("decoder_ckpt") or _DEFAULT_DECODER)
        encoder_info = load_checkpoint_with_counts(torch, self.net.encoder, encoder_ckpt, self.device)
        decoder_info = load_checkpoint_with_counts(torch, self.net.decoder, decoder_ckpt, self.device)
        self.load_info = {
            "encoder": encoder_info,
            "decoder": decoder_info,
            "torchvision_pretrained_download_patched": patched,
        }
        if not encoder_info.get("full_load") or not decoder_info.get("full_load"):
            raise RuntimeError(f"NEAT checkpoint did not fully load: {self.load_info}")
        self.plan_grid = self.net.create_plan_grid(
            float(getattr(self.model_config, "plan_scale", 0.1)),
            int(getattr(self.model_config, "plan_points", 1)),
            1,
        )
        self.light_grid = self.net.create_light_grid(
            int(getattr(self.model_config, "light_x_steps", 16)),
            int(getattr(self.model_config, "light_y_steps", 32)),
            1,
        )
        self.net.eval()

    @property
    def workspace_root(self) -> Path:
        return _WORKSPACE_ROOT


__all__ = ["NEATController"]
