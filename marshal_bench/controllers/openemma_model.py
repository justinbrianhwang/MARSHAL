"""OpenEMMA full-planning adapter scaffold for MARSHAL."""
from __future__ import annotations

import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from marshal_bench.controllers._trajectory_planner_common import (
    BasePlannerBackend,
    SensorSpec,
    TrajectoryPlannerControllerBase,
    find_workspace_root,
    integrate_speed_curvature,
    parse_numeric_pairs,
)
from marshal_bench.controllers.ablation_assist import (
    ABLATION_LEVELS,
    AblationAssist,
)

log = logging.getLogger("marshal_bench.controllers.openemma")

_DEFAULT_MODEL_DIR = (
    find_workspace_root()
    / "Models"
    / "OpenEMMA"
    / "OpenEMMA"
    / "local"
    / "Qwen2-VL-7B-Instruct"
)
_BACKEND_CACHE: dict[str, "_OpenEMMAQwenBackend"] = {}

# Prompt flow mirrors the local OpenEMMA implementations:
# - Models/OpenEMMA/OpenEMMA/main.py: SceneDescription -> DescribeObjects ->
#   DescribeOrUpdateIntent -> GenerateMotion
# - Models/OpenEMMA/Scenario/openemmaUI.py: CARLA-adapted scene/object/intent
#   and motion prompt templates.
#
# Keep these prompts free of literal numeric answer examples. The old adapter
# leaked a constant speed/curvature exemplar and invited a greedy 7B model to
# copy it.
_SCENE_PROMPT = (
    "You are an autonomous vehicle driving in a virtual city (CARLA simulator). "
    "Describe the driving scene in 1-2 sentences: traffic lights and their color, "
    "other vehicles, pedestrians, lane markings, and road geometry such as a "
    "straight road, curve, crosswalk, or intersection."
)

_OBJECTS_PROMPT = (
    "Based only on visible evidence in the driving scene, list 1-3 critical "
    "objects the ego car should focus on, such as a traffic light ahead, a car "
    "blocking the lane, a pedestrian, or an officer/worker giving instructions. "
    "Do not invent traffic signals or road users that are not visible; if "
    "nothing critical is visible, say so. For each object, briefly explain why "
    "it matters. Be concise."
)

_INTENT_PROMPT_TEMPLATE = (
    "You are driving at {speed:.1f} m/s.\n"
    "Scene: {scene}\n"
    "Critical objects: {objects}\n"
    "Lane-follow target in ego coordinates: x={target_x:.1f} m forward, "
    "y={target_y:.1f} m left.\n"
    "Route command: {command}.\n"
    "{prev_intent_str}"
    "What should the ego car do? Answer in one line: turn left, turn right, "
    "go straight, hold position, or go around obstacle; and speed up, maintain "
    "speed, slow down, or stop. Follow common-sense traffic rules and visible "
    "human traffic-control instructions."
)

_MOTION_SYSTEM = (
    "You are an autonomous driving motion planner in CARLA simulator. Given the "
    "scene understanding, critical objects, driving intent, and recent ego "
    "motion, predict future speed and curvature pairs. Curvature is in 1/m: "
    "positive means turning left, negative means turning right, and near zero "
    "means driving straight. Follow traffic lights, pedestrians, obstacles, and "
    "human traffic-control instructions. Stay near the lane center unless the "
    "intent requires an obstacle bypass. Do not predict all-zero speeds unless "
    "the intent or scene requires stopping, holding, or yielding."
)

_MOTION_PROMPT_TEMPLATE = (
    "Current speed: {speed:.1f} m/s.\n"
    "Nominal lane-follow cruise speed: {cruise_speed:.1f} m/s.\n"
    "Lane-follow target in ego coordinates: x={target_x:.1f} m forward, "
    "y={target_y:.1f} m left.\n"
    "Route command: {command}.\n"
    "Scene: {scene}\n"
    "Objects: {objects}\n"
    "Intent: {intent}\n"
    "Recent ego speeds and curvatures, oldest to newest: {history}.\n"
    "Predict {count} future speed/curvature pairs for the next {horizon_s:.1f} "
    "seconds. Use the abstract format [speed_i, curvature_i], separated by "
    "commas or semicolons. If the path is clear and intent is to proceed from "
    "rest, choose positive speeds up toward the nominal cruise speed. Write raw "
    "text only.\n"
    "Future speeds and curvatures:"
)


class OpenEMMAController(TrajectoryPlannerControllerBase):
    """CARLA-facing wrapper for the local OpenEMMA Qwen2-VL planner."""

    name = "openemma"
    config_key = "openemma"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self.query_period_s = float(self.mcfg.get("query_period_s", 1.0))
        self.lookahead_m = float(self.mcfg.get("lookahead_m", 5.0))
        # Oracle-assist ablation opt-in (privileged DIAGNOSTIC runs, never
        # leaderboard rows): same config key / env var as the VLM wiring, and
        # the injected assist text is built by the same shared helper.
        self._assist = AblationAssist.from_config(self.mcfg)
        self.requests_privileged_gt = self._assist.requests_privileged_gt
        self._oracle_shadow = None

    # ------------------------------------------------------------------
    # Ablation assist (mirrors marshal_bench.controllers.vlm_model)
    # ------------------------------------------------------------------
    def set_officer_ref(self, officer: Any) -> None:
        """Privileged runs only: live handle to the scene's director."""
        self._assist.set_officer_ref(officer)

    def _ablation_assist(self, sim_time: float) -> str:
        return self._assist.assist(sim_time)

    def setup(
        self,
        world: Any,
        ego: Any,
        ground_truth: dict[str, Any],
        carla: Any,
    ) -> None:
        if self._assist.requests_privileged_gt:
            # Validate BEFORE the expensive backend load: a malformed rung
            # must fail fast, not after a 7B model is on the GPU.
            self._assist.set_ground_truth(ground_truth)
            self._assist.validate_gt()
        super().setup(world, ego, ground_truth, carla)
        if self._assist.rank >= ABLATION_LEVELS.index("policy"):
            # L6 shadow oracle: the verified reference policy runs alongside
            # (compute-only; its control is never applied to the vehicle) and
            # its per-tick output is translated into the reply vocabulary.
            from marshal_bench.controllers.oracle import OracleController
            self._oracle_shadow = OracleController(dict(self.config))
            self._oracle_shadow.setup(world, ego, dict(self._assist.gt), carla)

    def step(self, observation: dict[str, Any], dt: float) -> Any:
        obs = observation or {}
        if self._oracle_shadow is not None:
            # Advance the reference policy every tick (its internal state
            # machine needs the full tick stream) and cache the translated
            # token for the next planner query. Compute-only.
            sim_time = float(obs.get("sim_time") or 0.0)
            try:
                shadow = self._oracle_shadow.step(obs, dt)
                if shadow is not None:
                    self._assist.last_policy_token = self._assist.control_to_token(
                        float(getattr(shadow, "throttle", 0.0) or 0.0),
                        float(getattr(shadow, "brake", 0.0) or 0.0),
                        float(obs.get("ego_speed") or 0.0))
            except Exception as e:  # noqa: BLE001
                # Invalidate immediately: a stale token must not keep being
                # asserted as "the correct action at this instant" while the
                # shadow is unhealthy (adversarial review, round 7).
                self._assist.last_policy_token = None
                self._log_event(
                    f"{self.name}_error", t=round(sim_time, 3),
                    kind="oracle_shadow", message=str(e)[:240])
                log.warning("oracle shadow step failed: %s", e)
        return super().step(observation, dt)

    def _build_payload(
        self,
        obs: dict[str, Any],
        samples: dict[str, tuple[int, Any]],
        frame: Optional[int],
        sim_time: float,
        dt: float,
    ) -> dict[str, Any]:
        payload = super()._build_payload(obs, samples, frame, sim_time, dt)
        assist = self._ablation_assist(sim_time)
        if assist:
            payload["ablation_assist"] = assist
            payload["ablation_level"] = self._assist.level
        return payload

    def sensor_specs(self) -> tuple[SensorSpec, ...]:
        return (
            SensorSpec("front_rgb", x=1.5, y=0.0, z=2.3, width=800, height=600, fov=100.0),
            SensorSpec("imu", kind="imu"),
            SensorSpec("gnss", kind="gnss"),
        )

    def _load_backend(self) -> BasePlannerBackend:
        backend = str(self.mcfg.get("backend", "qwen2vl")).lower()
        if backend not in {"qwen", "qwen2vl", "qwen2-vl"}:
            raise RuntimeError(f"Unsupported OpenEMMA backend for this adapter: {backend}")
        model_dir = Path(self._resolve_path(self.mcfg.get("model_dir") or _DEFAULT_MODEL_DIR))
        if not model_dir.is_dir():
            raise FileNotFoundError(f"OpenEMMA model directory not found: {model_dir}")
        cache_key = str(model_dir.resolve())
        if bool(self.mcfg.get("reuse_backend", False)):
            backend = _BACKEND_CACHE.get(cache_key)
            reused = backend is not None
            if backend is None:
                backend = _OpenEMMAQwenBackend(model_dir=model_dir, cfg=self.mcfg)
                _BACKEND_CACHE[cache_key] = backend
            backend.load_info["cache_key"] = cache_key
            backend.load_info["cache_reused"] = reused
            self.backend_info = dict(backend.load_info)
            return backend
        backend = _OpenEMMAQwenBackend(model_dir=model_dir, cfg=self.mcfg)
        self.backend_info = dict(backend.load_info)
        return backend


class _OpenEMMAQwenBackend(BasePlannerBackend):
    name = "openemma_qwen2vl"

    def __init__(self, *, model_dir: Path, cfg: dict[str, Any]) -> None:
        self.model_dir = model_dir
        self.cfg = cfg
        self.model = None
        self.processor = None
        self.process_vision_info = None
        self.torch = None
        self.load_info: dict[str, Any] = {}
        self.prev_intent = ""
        self._motion_history: list[tuple[float, float]] = []
        self._load()

    def _load(self) -> None:
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        import torch
        from qwen_vl_utils import process_vision_info
        from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

        t0 = time.perf_counter()
        loaded = Qwen2VLForConditionalGeneration.from_pretrained(
            str(self.model_dir),
            torch_dtype="auto",
            device_map="cuda" if torch.cuda.is_available() else None,
            local_files_only=True,
            output_loading_info=True,
        )
        if isinstance(loaded, tuple):
            self.model, loading_info = loaded
        else:
            self.model, loading_info = loaded, {}
        self.processor = AutoProcessor.from_pretrained(str(self.model_dir), local_files_only=True)
        self.process_vision_info = process_vision_info
        self.torch = torch
        self.model.eval()
        param = next(self.model.parameters())
        self.load_info = {
            "model_dir": str(self.model_dir),
            "load_s": round(time.perf_counter() - t0, 3),
            "missing_keys": len(loading_info.get("missing_keys") or []),
            "unexpected_keys": len(loading_info.get("unexpected_keys") or []),
            "mismatched_keys": len(loading_info.get("mismatched_keys") or []),
            "dtype": str(param.dtype),
            "device": str(param.device),
        }
        log.info("Loaded OpenEMMA Qwen backend: %s", self.load_info)

    def predict_waypoints(self, payload: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
        if self.model is None or self.processor is None or self.process_vision_info is None:
            raise RuntimeError("OpenEMMA backend is not loaded")
        front = payload.get("front_rgb")
        if front is None:
            raise RuntimeError("OpenEMMA requires a front RGB image")

        from PIL import Image

        image = Image.fromarray(np.asarray(front, dtype=np.uint8))
        speed = float(payload.get("speed_mps") or 0.0)
        target_x, target_y = payload.get("target_point") or (0.0, 0.0)
        command = payload.get("route_command") or "LANE_FOLLOW"
        max_pairs = int(self.cfg.get("max_pairs", 6))
        cruise_speed = float(
            self.cfg.get("target_speed_mps")
            or (float(self.cfg.get("target_speed_kmh", 25.0)) / 3.6)
        )
        # Oracle-assist ablation: the controller hands over the shared assist
        # block; it is prepended verbatim (clearly delimited) to every stage
        # prompt and echoed into the metadata for the per-query audit log.
        assist = str(payload.get("ablation_assist") or "")

        query_started = time.perf_counter()
        scene, scene_meta = self._vlm_query(
            self._prepend_assist(_SCENE_PROMPT, assist),
            image,
            stage="scene",
            max_new_tokens=int(self.cfg.get("scene_max_new_tokens", 160)),
        )
        objects, objects_meta = self._vlm_query(
            self._prepend_assist(_OBJECTS_PROMPT, assist),
            image,
            stage="objects",
            max_new_tokens=int(self.cfg.get("objects_max_new_tokens", 160)),
        )
        prev_intent_str = f"Previous intent: {self.prev_intent}\n" if self.prev_intent else ""
        intent_prompt = _INTENT_PROMPT_TEMPLATE.format(
            speed=abs(speed),
            scene=self._trim_for_prompt(scene, 700),
            objects=self._trim_for_prompt(objects, 700),
            target_x=float(target_x),
            target_y=float(target_y),
            command=command,
            prev_intent_str=prev_intent_str,
        )
        intent, intent_meta = self._vlm_query(
            self._prepend_assist(intent_prompt, assist),
            image,
            stage="intent",
            max_new_tokens=int(self.cfg.get("intent_max_new_tokens", 120)),
        )
        self.prev_intent = self._trim_for_prompt(intent, 300)

        inferred_curvature = self._route_curvature(float(target_x), float(target_y))
        self._motion_history.append((max(0.0, abs(speed)), inferred_curvature))
        history_len = int(self.cfg.get("history_len", 10))
        self._motion_history = self._motion_history[-max(1, history_len):]
        history = self._format_history(self._motion_history)
        motion_prompt = _MOTION_SYSTEM + "\n" + _MOTION_PROMPT_TEMPLATE.format(
            speed=abs(speed),
            cruise_speed=cruise_speed,
            target_x=float(target_x),
            target_y=float(target_y),
            command=command,
            scene=self._trim_for_prompt(scene, 700),
            objects=self._trim_for_prompt(objects, 700),
            intent=self._trim_for_prompt(intent, 400),
            history=history,
            count=max_pairs,
            horizon_s=max_pairs * 0.5,
        )
        text, motion_meta = self._vlm_query(
            self._prepend_assist(motion_prompt, assist),
            image,
            stage="motion",
            max_new_tokens=int(self.cfg.get("max_new_tokens", 128)),
        )
        pairs = self._parse_motion_pairs(text, max_pairs=max_pairs)
        if not pairs:
            raise RuntimeError(f"OpenEMMA output had no speed/curvature pairs: {text[:160]}")
        waypoints = integrate_speed_curvature(pairs, dt=0.5, max_points=max_pairs)
        planned_speed = float(np.mean([p[0] for p in pairs[: min(3, len(pairs))]]))
        return waypoints, {
            "backend": self.name,
            "prompt_family": "openemma_cot_scene_objects_intent_motion",
            # Audit trail: the rung label and the EXACT assist text injected
            # at this planner query, so a rung can be checked against what
            # was actually injected (same contract as the VLM decision log).
            "ablation": str(payload.get("ablation_level") or "none"),
            "assist": assist,
            "text": text[:512],
            "motion_text": text[:1024],
            "scene_text": scene[:512],
            "objects_text": objects[:512],
            "intent_text": intent[:512],
            "pairs": pairs,
            "planned_speed_mps": planned_speed,
            "history": self._motion_history,
            "stage_latencies_s": {
                "scene": scene_meta.get("model_latency_s"),
                "objects": objects_meta.get("model_latency_s"),
                "intent": intent_meta.get("model_latency_s"),
                "motion": motion_meta.get("model_latency_s"),
            },
            "model_latency_s": time.perf_counter() - query_started,
            "vision": {
                "scene": scene_meta.get("vision"),
                "objects": objects_meta.get("vision"),
                "intent": intent_meta.get("vision"),
                "motion": motion_meta.get("vision"),
            },
            "decoding": {"do_sample": False},
            "load_info": self.load_info,
        }

    def _vlm_query(
        self,
        prompt: str,
        image: Any,
        *,
        stage: str,
        max_new_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        rendered = self.processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        image_inputs, video_inputs = self.process_vision_info(messages)
        inputs = self.processor(
            text=[rendered],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(self.model.device)
        vision = self._vision_metadata(inputs)
        torch = self.torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        with torch.no_grad():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        latency = time.perf_counter() - t0
        trimmed = [out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated)]
        text = self.processor.batch_decode(
            trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0].strip()
        return text, {
            "stage": stage,
            "model_latency_s": latency,
            "input_tokens": int(inputs.input_ids.shape[-1]),
            "output_tokens": int(trimmed[0].shape[-1]) if trimmed else 0,
            "vision": vision,
        }

    @staticmethod
    def _prepend_assist(prompt: str, assist: str) -> str:
        """Prepend the ablation assist block as a clearly-delimited section;
        the underlying prompt text is otherwise unchanged."""
        if not assist:
            return prompt
        return f"{assist}---\n{prompt}"

    @staticmethod
    def _trim_for_prompt(text: str, limit: int) -> str:
        value = " ".join(str(text or "").split())
        return value[:limit]

    @staticmethod
    def _route_curvature(target_x: float, target_y: float) -> float:
        denom = target_x * target_x + target_y * target_y
        if denom <= 1e-3:
            return 0.0
        curvature = 2.0 * target_y / denom
        return float(np.clip(curvature, -0.15, 0.15))

    @staticmethod
    def _format_history(history: list[tuple[float, float]]) -> str:
        if not history:
            return "[speed_i, curvature_i]"
        return ", ".join(f"[{v:.2f},{k:.4f}]" for v, k in history)

    @staticmethod
    def _parse_motion_pairs(text: str, *, max_pairs: int) -> list[tuple[float, float]]:
        raw_pairs: list[tuple[float, float]] = []
        bracketed = re.findall(
            r"\[\s*([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)\s*\]",
            text or "",
        )
        for speed, curvature in bracketed:
            raw_pairs.append((float(speed), float(curvature)))
        if not raw_pairs:
            raw_pairs = parse_numeric_pairs(text)

        pairs: list[tuple[float, float]] = []
        for speed, curvature in raw_pairs[:max_pairs]:
            if not (math.isfinite(speed) and math.isfinite(curvature)):
                continue
            # Original OpenEMMA's nuScenes path predicts curvature scaled by
            # 100 before dividing it back down for integration. The CARLA UI
            # prompt predicts curvature directly. Accept either convention.
            if abs(curvature) > 0.8:
                curvature = curvature / 100.0
            speed = float(np.clip(speed, 0.0, 15.0))
            curvature = float(np.clip(curvature, -0.8, 0.8))
            pairs.append((speed, curvature))
        return pairs

    @staticmethod
    def _shape_list(value: Any) -> Optional[list[int]]:
        shape = getattr(value, "shape", None)
        if shape is None:
            return None
        return [int(dim) for dim in shape]

    def _vision_metadata(self, inputs: Any) -> dict[str, Any]:
        def get(key: str) -> Any:
            try:
                return inputs.get(key)
            except Exception:
                return getattr(inputs, key, None)

        pixel_values = get("pixel_values")
        image_grid = get("image_grid_thw")
        pixel_shape = self._shape_list(pixel_values)
        image_grid_list = None
        if image_grid is not None:
            try:
                image_grid_list = image_grid.detach().cpu().tolist()
            except Exception:
                image_grid_list = str(image_grid)
        feature_count = None
        if pixel_shape:
            feature_count = int(pixel_shape[0])
        return {
            "has_image": pixel_values is not None,
            "pixel_values_shape": pixel_shape,
            "image_feature_count": feature_count,
            "image_grid_thw": image_grid_list,
        }

    def close(self) -> None:
        if self.model is not None and self.torch is not None:
            try:
                del self.model
                if self.torch.cuda.is_available():
                    self.torch.cuda.empty_cache()
            except Exception:
                pass
        self.model = None


__all__ = ["OpenEMMAController"]
