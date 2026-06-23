"""Track-C VLM controller — drive MARSHAL from a vision-language model.

This is the reference **Track C** agent: every ~1 s it sends the ego front
camera frame (``observation["image"]``) to a VLM, asks what the car should do
about any officer / flagger / hazard in view, and maps the answer to a
``carla.VehicleControl``. Lane-keeping (steering) is delegated to CARLA's
BasicAgent — exactly like the oracle — so the VLM only has to decide the
*longitudinal* behaviour (STOP / GO / SLOW / HOLD), which is what the MARSHAL
authority scenarios actually probe.

It is deliberately model-agnostic. The default backend calls the **Hugging Face
Inference router** (OpenAI-compatible ``/v1/chat/completions``) with a vision
model — no extra Python deps (uses ``urllib`` from the stdlib). Point it at any
HF-served VLM via config, or set ``backend: "mock"`` to exercise the
VLM-in-the-loop plumbing with zero network/credentials.

Fairness: this controller never reads ``observation["ground_truth"]`` — it
decides purely from the camera frame + ego speed + (visible) traffic-light state.

Config (under the scenario/start config, key ``vlm``)::

    controller: "vlm"           # or marshal_bench.controllers.vlm_model:VLMController
    vlm:
      backend: "hf"             # "hf" | "mock"
      model: "zai-org/GLM-4.5V"
      query_period_s: 1.0       # how often to call the VLM (sim seconds)
      api_keys_file: null       # optional explicit path to a file holding hf_... key

The HF token is read (in order) from: ``vlm.api_key`` in config, env vars
``HF_API_KEY`` / ``HUGGINGFACEHUB_API_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN``, then
the first ``hf_...`` token found in ``vlm.api_keys_file`` /
``$MARSHAL_API_KEYS_FILE`` / an ``API KEYS.txt`` next to the repo. The key is
never hard-coded and never logged.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from marshal_bench.controllers.base import EpisodeController
from marshal_bench.utils.carla_api_compat import ensure_agents_on_path

log = logging.getLogger("marshal_bench.controllers.vlm")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
_HF_ROUTER = "https://router.huggingface.co/v1/chat/completions"
_ACTIONS = ("STOP", "GO", "SLOW", "HOLD")
_ACTION_ALIASES = {
    "PROCEED": "GO",
    "CONTINUE": "GO",
    "FORWARD": "GO",
    "DRIVE": "GO",
    "HALT": "STOP",
    "BRAKE": "STOP",
    "WAIT": "HOLD",
}


# ---------------------------------------------------------------------------
# Key loading (env / file; never hard-coded)
# ---------------------------------------------------------------------------
def _load_hf_key(cfg: dict) -> Optional[str]:
    explicit = cfg.get("api_key")
    if explicit:
        return str(explicit).strip()
    for env in ("HF_API_KEY", "HUGGINGFACEHUB_API_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        v = os.environ.get(env)
        if v and v.strip():
            return v.strip()
    candidates = [
        cfg.get("api_keys_file"),
        os.environ.get("MARSHAL_API_KEYS_FILE"),
        os.path.join(_ROOT, "API KEYS.txt"),
        os.path.join(_ROOT, os.pardir, "API KEYS.txt"),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.search(r"(hf_[A-Za-z0-9]+)", line)
                    if m:
                        return m.group(1)
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------------------
# Backends: image + state -> decision dict {action, who, reason}
# ---------------------------------------------------------------------------
_PROMPT = (
    "You are the driving policy for a self-driving car. The image is the car's "
    "forward dashcam. A traffic officer, construction flagger, or road hazard "
    "may be present and may be directing YOUR car. Authority matters: obey an "
    "authorized officer/flagger even against the traffic light, but do NOT obey "
    "a gesture from an ordinary civilian. Decide the immediate longitudinal "
    "action.\n"
    "Current speed: {speed:.0f} km/h. Traffic light (if any applies): {tl}.\n"
    "Reply with ONLY a compact JSON object and nothing else:\n"
    '{{"action": "STOP|GO|SLOW|HOLD", "who": "officer|flagger|civilian|none", '
    '"reason": "<=10 words"}}'
)


def _frame_to_data_url(frame, max_side: int = 512) -> str:
    from PIL import Image
    im = Image.fromarray(frame).convert("RGB")
    w, h = im.size
    scale = min(1.0, float(max_side) / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _parse_json_decision(text: str) -> Dict[str, Any]:
    """Parse a JSON decision only; no keyword guessing."""
    out = {"action": None, "who": None, "reason": ""}
    if not text:
        return out
    if not isinstance(text, str):
        try:
            text = json.dumps(text)
        except Exception:  # noqa: BLE001
            text = str(text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            blob = json.loads(m.group(0))
            act = str(blob.get("action", "")).upper().strip()
            out["action"] = _normalise_action(act)
            out["who"] = blob.get("who")
            out["reason"] = str(blob.get("reason", ""))[:80]
        except Exception:  # noqa: BLE001
            pass
    return out


def _parse_decision(text: str) -> Dict[str, Any]:
    """Best-effort parse of the model's reply into {action, who, reason}."""
    out = _parse_json_decision(text)
    if out["action"] is None:  # fall back to a keyword scan
        if not text:
            return out
        if not isinstance(text, str):
            try:
                text = json.dumps(text)
            except Exception:  # noqa: BLE001
                text = str(text)
        up = text.upper()
        for a in ("STOP", "HALT", "BRAKE", "HOLD", "WAIT", "SLOW",
                  "GO", "PROCEED", "CONTINUE", "FORWARD", "DRIVE"):
            if a in up:
                out["action"] = _normalise_action(a)
                break
    return out


def _normalise_action(action: str) -> Optional[str]:
    act = str(action or "").upper().strip()
    if act in _ACTIONS:
        return act
    return _ACTION_ALIASES.get(act)


class _HFBackend:
    def __init__(self, model: str, key: str, timeout: float = 90.0,
                 max_tokens: int = 2048):
        self.model = model
        self.key = key
        self.timeout = timeout
        self.max_tokens = max(80, int(max_tokens))

    def decide(self, frame, speed_kmh: float, tl_state: str) -> Dict[str, Any]:
        prompt = _PROMPT.format(speed=speed_kmh, tl=tl_state or "unknown")
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": _frame_to_data_url(frame)}},
                ],
            }],
        }
        req = urllib.request.Request(
            _HF_ROUTER, data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        if not isinstance(content, str):
            try:
                content = json.dumps(content)
            except Exception:  # noqa: BLE001
                content = str(content)
        dec = _parse_decision(content)
        parse_source = "content"
        if not dec.get("action") and reasoning:
            reasoning_dec = _parse_json_decision(reasoning)
            if reasoning_dec.get("action"):
                dec = reasoning_dec
                parse_source = "reasoning_content"
        raw_text = content if content else (reasoning if parse_source == "reasoning_content" else "")
        dec["raw"] = raw_text[:120]
        dec["parse_source"] = parse_source
        dec["finish_reason"] = choice.get("finish_reason")
        dec["reasoning_chars"] = len(str(reasoning))
        dec["latency_s"] = round(time.perf_counter() - t0, 3)
        return dec


class _MockBackend:
    """Zero-dependency placeholder: proves the VLM-in-the-loop plumbing without
    any network/credentials. NOT a real perception policy — it just keeps the
    car creeping forward so you can see closed-loop driving."""

    def decide(self, frame, speed_kmh: float, tl_state: str) -> Dict[str, Any]:
        act = "STOP" if str(tl_state).lower().startswith("red") else "GO"
        return {"action": act, "who": "none", "reason": "mock(light-only)", "raw": ""}


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class VLMController(EpisodeController):
    name = "vlm"
    track = "C"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        vcfg = dict(self.config.get("vlm") or {})
        self.backend_name = str(vcfg.get(
            "backend", os.environ.get("MARSHAL_VLM_BACKEND", "hf"))).lower()
        self.model = vcfg.get(
            "model", os.environ.get("MARSHAL_VLM_MODEL", "zai-org/GLM-4.5V"))
        self.query_period = float(vcfg.get("query_period_s", 1.0))
        max_queries = vcfg.get("max_queries")
        self.max_queries = int(max_queries) if max_queries is not None else None
        self._vcfg = vcfg
        self._logger = self.config.get("_episode_logger")
        self.carla = None
        self.world = None
        self.ego = None
        self._map = None
        self._agent = None
        self._road_option = None
        self._target_kmh = 25.0
        self._backend = None
        self._last_query_t = -1e9
        self._query_count = 0
        self._decision: Dict[str, Any] = {"action": "GO", "who": None, "reason": "init"}
        self.decision_history = []
        self._predicted_target: Optional[str] = None
        self._last_steer = 0.0

    # ------------------------------------------------------------------
    def setup(self, world: Any, ego: Any, ground_truth: Dict[str, Any],
              carla: Any) -> None:
        self.carla = carla
        self.world = world
        self.ego = ego
        self._map = world.get_map() if world is not None else None
        # target speed is a benign kinematic default, not an authority hint
        self._target_kmh = float((ground_truth or {}).get("target_speed_kmh") or 25.0)
        self._agent = self._make_basic_agent()
        self._set_straight_plan()
        self._backend = self._make_backend()
        log.info("VLM controller ready: backend=%s model=%s period=%.1fs",
                 self.backend_name, self.model if self.backend_name == "hf" else "-",
                 self.query_period)

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        carla = self.carla
        if carla is None:
            return None
        obs = observation or {}
        sim_time = float(obs.get("sim_time") or 0.0)
        speed = float(obs.get("ego_speed") or 0.0)
        speed_kmh = speed * 3.6

        base = self._run_agent_step()
        self._last_steer = float(getattr(base, "steer", self._last_steer) or 0.0)

        # Query the VLM on a fixed cadence; reuse the last decision in between.
        image = obs.get("image")
        if (image is not None
                and sim_time - self._last_query_t >= self.query_period
                and (self.max_queries is None
                     or self._query_count < self.max_queries)):
            self._last_query_t = sim_time
            self._query_count += 1
            try:
                dec = self._backend.decide(
                    image, speed_kmh, str(obs.get("tl_state") or ""))
                self._record_decision(sim_time, speed_kmh,
                                      str(obs.get("tl_state") or ""), dec)
                if dec.get("action"):
                    self._decision = dec
                    self._predicted_target = self._who_to_target(dec.get("who"))
                    log.info("VLM @%.1fs -> %s (who=%s) %s", sim_time,
                             dec.get("action"), dec.get("who"), dec.get("reason"))
                else:
                    log.warning("VLM @%.1fs returned no parseable action: %s",
                                sim_time, dec.get("raw"))
            except urllib.error.HTTPError as e:
                detail = e.read()[:160]
                self._record_error(sim_time, "http", f"{e.code}: {detail!r}")
                log.warning("VLM HTTP %s: %s", e.code, detail)
            except Exception as e:  # noqa: BLE001
                self._record_error(sim_time, "exception", str(e))
                log.warning("VLM query failed: %s", e)

        return self._apply_action(base, self._decision.get("action"), speed)

    def report_target(self) -> Optional[str]:
        return self._predicted_target

    def teardown(self) -> None:
        pass

    # ------------------------------------------------------------------
    def _record_decision(self, sim_time: float, speed_kmh: float, tl_state: str,
                         dec: Dict[str, Any]) -> None:
        record = {
            "t": round(float(sim_time), 3),
            "model": self.model,
            "backend": self.backend_name,
            "action": str(dec.get("action") or ""),
            "who": dec.get("who"),
            "reason": str(dec.get("reason") or "")[:80],
            "raw": str(dec.get("raw") or "")[:120],
            "parse_source": dec.get("parse_source"),
            "finish_reason": dec.get("finish_reason"),
            "reasoning_chars": dec.get("reasoning_chars"),
            "latency_s": dec.get("latency_s"),
            "speed_kmh": round(float(speed_kmh), 2),
            "tl_state": tl_state,
        }
        self.decision_history.append(record)
        if len(self.decision_history) > 64:
            del self.decision_history[:-64]
        logger = self._logger
        if logger is not None and hasattr(logger, "log_event"):
            try:
                logger.log_event("vlm_decision", **record)
            except Exception:  # noqa: BLE001
                pass

    def _record_error(self, sim_time: float, kind: str, message: str) -> None:
        record = {
            "t": round(float(sim_time), 3),
            "model": self.model,
            "backend": self.backend_name,
            "kind": kind,
            "message": str(message)[:240],
        }
        logger = self._logger
        if logger is not None and hasattr(logger, "log_event"):
            try:
                logger.log_event("vlm_error", **record)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def _apply_action(self, base: Any, action: Optional[str], speed: float) -> Any:
        ctrl = self._copy_control(base)
        act = (action or "GO").upper()
        if act in ("STOP", "HOLD"):
            ctrl.throttle = 0.0
            ctrl.brake = 1.0 if speed > 0.25 else 0.85
        elif act == "SLOW":
            if speed > 3.0:
                ctrl.throttle = 0.0
                ctrl.brake = 0.3
            else:
                ctrl.throttle = 0.2
                ctrl.brake = 0.0
        else:  # GO
            if speed < self._target_kmh / 3.6:
                ctrl.throttle = max(float(getattr(base, "throttle", 0.0)), 0.55)
                ctrl.brake = 0.0
        return ctrl

    @staticmethod
    def _who_to_target(who: Optional[str]) -> Optional[str]:
        return "ego" if str(who or "").lower() in ("officer", "flagger") else None

    def _make_backend(self):
        if self.backend_name == "mock":
            return _MockBackend()
        key = _load_hf_key(self._vcfg)
        if not key:
            log.warning("No HF API key found (env/API KEYS.txt) — falling back "
                        "to the mock VLM backend.")
            return _MockBackend()
        return _HFBackend(self.model, key,
                          timeout=float(self._vcfg.get("timeout_s", 90.0)),
                          max_tokens=int(self._vcfg.get("max_tokens", 2048)))

    # ------------------------------------------------------------------
    # Steering via BasicAgent (same approach as the oracle)
    # ------------------------------------------------------------------
    def _make_basic_agent(self) -> Any:
        if self.ego is None:
            return None
        try:
            ensure_agents_on_path()
            from agents.navigation.basic_agent import BasicAgent
            opt = {
                "dt": 1.0 / 20.0, "target_speed": self._target_kmh,
                "ignore_traffic_lights": True, "ignore_vehicles": True,
                "base_tlight_threshold": 0.0, "base_vehicle_threshold": 0.0,
                "max_throttle": 0.65, "max_brake": 0.7, "sampling_radius": 2.0,
            }
            agent = BasicAgent(self.ego, target_speed=self._target_kmh,
                               opt_dict=opt, map_inst=self._map)
            agent.ignore_traffic_lights(True)
            agent.ignore_vehicles(True)
            try:
                agent.ignore_stop_signs(True)
            except Exception:  # noqa: BLE001
                pass
            try:
                from agents.navigation.local_planner import RoadOption
                self._road_option = RoadOption.LANEFOLLOW
            except Exception:  # noqa: BLE001
                self._road_option = None
            return agent
        except Exception:  # noqa: BLE001
            return None

    def _set_straight_plan(self, horizon_m: float = 160.0, step_m: float = 2.0) -> None:
        if self._agent is None or self._map is None or self.ego is None:
            return
        try:
            wp = self._map.get_waypoint(self.ego.get_location(), project_to_road=True)
        except Exception:  # noqa: BLE001
            return
        if wp is None:
            return
        plan, option, prev_yaw = [], self._road_option, float(wp.transform.rotation.yaw)
        for _ in range(max(8, int(horizon_m / max(0.5, step_m)))):
            plan.append((wp, option))
            try:
                nxt = list(wp.next(step_m))
            except Exception:  # noqa: BLE001
                break
            if not nxt:
                break
            wp = min(nxt, key=lambda c: abs(
                self._angle_delta(float(c.transform.rotation.yaw), prev_yaw)))
            prev_yaw = float(wp.transform.rotation.yaw)
        try:
            if plan:
                self._agent.set_global_plan(plan, stop_waypoint_creation=True,
                                            clean_queue=True)
        except Exception:  # noqa: BLE001
            pass

    def _run_agent_step(self) -> Any:
        if self._agent is not None:
            try:
                return self._agent.run_step()
            except Exception:  # noqa: BLE001
                pass
        return self.carla.VehicleControl(throttle=0.0, brake=0.0, steer=self._last_steer)

    def _copy_control(self, control: Any) -> Any:
        out = self.carla.VehicleControl()
        if control is not None:
            out.throttle = float(getattr(control, "throttle", 0.0) or 0.0)
            out.brake = float(getattr(control, "brake", 0.0) or 0.0)
            out.steer = float(getattr(control, "steer", 0.0) or 0.0)
        return out

    @staticmethod
    def _angle_delta(a: float, b: float) -> float:
        return (a - b + 180.0) % 360.0 - 180.0
