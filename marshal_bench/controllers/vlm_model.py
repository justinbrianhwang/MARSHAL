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
    "{assist}"
    "Reply with ONLY a compact JSON object and nothing else:\n"
    '{{"action": "STOP|GO|SLOW|HOLD", "who": "officer|flagger|civilian|none", '
    '"reason": "<=10 words"}}'
)

# Oracle-assist ablation ladder (privileged DIAGNOSTIC runs, never leaderboard
# rows): each level cumulatively injects one more link of the ground-truth
# reasoning chain into the prompt, so the score jump between adjacent levels
# attributes the failure to that link.
#   none       - the plain Track-C controller (no assist)
#   perception - what a perfect perception module would report (who/where/
#                which gesture/light), with NO interpretation
#   authority  - + whether each director is a legally valid traffic authority
#                (the bare classification; what to DO with it is the model's)
#   semantics  - + what the gesture means for THIS vehicle
#   temporal   - + whether the directive is currently active or expired
#   action     - + the episode-level ground-truth expected action (an answer
#                key at episode granularity, NOT a per-tick policy)
#   policy     - + the per-tick output of the verified oracle policy,
#                translated into the reply vocabulary (tests whether the
#                token interface can execute when the plan is external)
ABLATION_LEVELS = ("none", "perception", "authority", "semantics", "temporal",
                   "action", "policy")

_GESTURE_MEANING = {
    "STOP": "stop your vehicle and hold before the person/junction",
    "PROCEED": "drive through, even if the traffic light shows red",
    "GO": "drive through, even if the traffic light shows red",
    "SLOW": "reduce to a crawl and continue with care",
    "HOLD": "remain stopped until released",
    "LEFT": "detour into the left lane around the scene",
    "RIGHT": "detour into the right lane around the scene",
    "IDLE": "no directive is being given",
}

# L5 answer key: the episode-level expected action in the taxonomy vocabulary
# (STOP/PROCEED/HOLD/YIELD/DETOUR), which is WIDER than the reply vocabulary
# (STOP/GO/SLOW/HOLD). Each entry gives the honest description plus, where an
# exact reply token exists, that token — so the assist never asks the model to
# answer with a word its schema forbids.
_EXPECTED_ACTION_DESC = {
    "STOP": ("stop before the scene and hold", "STOP"),
    "HOLD": ("remain stopped until released", "HOLD"),
    "PROCEED": ("drive through (in your reply vocabulary: GO)", "GO"),
    "GO": ("drive through", "GO"),
    "YIELD": ("give way to the emergency vehicle; no single reply token "
              "expresses a pull-aside", None),
    "DETOUR": ("go around the blockage via the adjacent lane; no reply token "
               "expresses a lane change", None),
    "SLOW": ("reduce speed and continue with care", "SLOW"),
}

# What the director LOOKS like, by legal class — the perception rung must
# report appearance, not the legal verdict ("a civilian is standing..." for
# the fake-vest director leaks the L2 authority answer into L1; adversarial
# review, round 7). Per-scenario override: officer config key "appearance"
# (carried into the E-tuple as A_appearance).
_APPEARANCE = {
    "police": "in a police uniform",
    "officer": "in a police uniform",
    "flagger": "in a high-visibility vest and hard hat",
    "crossing_guard": "in a crossing-guard uniform",
    "civilian": "in ordinary clothes",
    "pedestrian": "in ordinary clothes",
}

# Natural phrasing for non-ego target relations in the L3 semantics block
# (raw config tokens like "other_lane" read as gibberish in prose).
_RELATION_PHRASE = {
    "other_lane": "vehicles in the other lane",
    "adjacent_lane": "vehicles in the adjacent lane",
    "opposite_lane": "oncoming traffic",
    "cross_traffic": "cross traffic",
    "pedestrians": "pedestrians",
}


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

    def decide(self, frame, speed_kmh: float, tl_state: str,
               assist: str = "") -> Dict[str, Any]:
        prompt = _PROMPT.format(speed=speed_kmh, tl=tl_state or "unknown",
                                assist=assist)
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

    def decide(self, frame, speed_kmh: float, tl_state: str,
               assist: str = "") -> Dict[str, Any]:
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
        ablation = str(vcfg.get(
            "ablation", os.environ.get("MARSHAL_VLM_ABLATION", "none"))).lower()
        if ablation not in ABLATION_LEVELS:
            raise ValueError(
                f"vlm.ablation={ablation!r} is not one of {ABLATION_LEVELS}")
        self.ablation = ablation
        self.ablation_rank = ABLATION_LEVELS.index(ablation)
        # Ablation runs read the privileged E-tuple by design; they are
        # diagnostics, not Track-C leaderboard entries.
        self.requests_privileged_gt = ablation != "none"
        self._gt: Dict[str, Any] = {}
        self._oracle_shadow = None
        self._last_policy_token: Optional[str] = None
        self._officer_ref: Any = None
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
        if self.requests_privileged_gt:
            self._gt = dict(ground_truth or {})
            self._validate_ablation_gt()
        self._agent = self._make_basic_agent()
        self._set_straight_plan()
        self._backend = self._make_backend()
        if self.ablation_rank >= ABLATION_LEVELS.index("policy"):
            # L6 shadow oracle: the verified reference policy runs alongside
            # (compute-only; its control is never applied to the vehicle) and
            # its per-tick output is translated into the reply vocabulary.
            from marshal_bench.controllers.oracle import OracleController
            self._oracle_shadow = OracleController(dict(self.config))
            self._oracle_shadow.setup(world, ego, dict(self._gt), carla)
        log.info("VLM controller ready: backend=%s model=%s period=%.1fs ablation=%s",
                 self.backend_name, self.model if self.backend_name == "hf" else "-",
                 self.query_period, self.ablation)

    # ------------------------------------------------------------------
    def _validate_ablation_gt(self) -> None:
        """Fail LOUDLY on malformed privileged data.

        The ladder is only interpretable if every rung injects true
        information; a silently-degraded assist (missing key rendered as
        prose, None validity rendered as "NOT valid") would give nominally
        identical rung labels different actual assists.
        """
        gt = self._gt
        problems = []
        for key in ("A_authority", "L_light_state", "ego_spawn"):
            if key not in gt:
                problems.append(f"missing key {key!r}")
        authority = gt.get("A_authority") or {}
        atype = authority.get("type")
        if atype in ("none", ""):
            atype = None
        if atype is not None and not isinstance(authority.get("valid"), bool):
            problems.append(
                f"A_authority.valid must be a real bool for a present "
                f"director, got {authority.get('valid')!r}")
        second = gt.get("A_second_authority")
        if second is not None:
            if not second.get("type"):
                problems.append("A_second_authority present but has no type")
            if not isinstance(second.get("valid"), bool):
                problems.append(
                    f"A_second_authority.valid must be a real bool, got "
                    f"{second.get('valid')!r}")
        if self.ablation_rank >= ABLATION_LEVELS.index("action"):
            y = str(gt.get("Y_expected_action") or "").upper()
            if y not in _EXPECTED_ACTION_DESC:
                problems.append(
                    f"Y_expected_action {y!r} has no honest description")
        if problems:
            raise ValueError(
                "ablation ground truth is malformed (refusing to run a "
                "silently-degraded rung): " + "; ".join(problems))

    # ------------------------------------------------------------------
    def set_officer_ref(self, officer: Any) -> None:
        """Privileged runs only: live handle to the scene's director.

        The setup-time GT snapshot goes stale when a scenario re-issues a
        gesture mid-episode (flagger SLOW→STOP) or removes the officer
        (sequential_directive); the assist must describe the scene as it IS
        at the query instant (adversarial review, round 7).
        """
        self._officer_ref = officer

    def _live_officer_state(self) -> Optional[Dict[str, Any]]:
        """Current director state, or None when no live handle exists."""
        ref = self._officer_ref
        if ref is None:
            return None
        try:
            actor = ref.get_actor()
        except Exception:  # noqa: BLE001
            actor = None
        if actor is None or not bool(getattr(actor, "is_alive", True)):
            return {"present": False}
        try:
            meta = ref.get_metadata() or {}
        except Exception:  # noqa: BLE001
            return {"present": True}
        gesture = str(meta.get("gesture_id") or "").upper()
        state: Dict[str, Any] = {"present": True}
        if gesture and gesture != "UNKNOWN":
            state["gesture"] = gesture
            if meta.get("onset_time") is not None:
                state["onset"] = float(meta["onset_time"])
            state["duration"] = meta.get("duration")
        return state

    def _gesture_window(self):
        """(gesture, onset, end) of the primary directive at query time.

        Prefers the LIVE officer metadata (phase switches, mid-episode
        re-issues); falls back to the setup-time snapshot. The active window
        is CLOSED [onset, onset + duration] to match the telemetry
        recorder's officer_active definition — at exactly onset + duration
        the gesture is still showing. A None onset means the gesture runs
        for the whole episode.
        """
        gt = self._gt
        gesture = str(gt.get("G_gesture") or "IDLE").upper()
        onset = gt.get("G_gesture_onset_sec")
        duration = gt.get("G_gesture_duration_sec")
        live = self._live_officer_state()
        if live is not None and live.get("present") and live.get("gesture"):
            gesture = live["gesture"]
            onset = live.get("onset", onset)
            duration = live.get("duration")
        onset_f = float(onset) if onset is not None else None
        end_f = (onset_f + float(duration)
                 if onset_f is not None and duration is not None else None)
        return gesture, onset_f, end_f

    def _ablation_assist(self, sim_time: float) -> str:
        """Cumulative ground-truth assist blocks for the ablation ladder."""
        if self.ablation_rank <= 0:
            return ""
        gt = self._gt
        lines = ["GROUND-TRUTH ASSISTS (diagnostic ablation study):"]
        authority = gt.get("A_authority") or {}
        atype = authority.get("type")
        # No-director scenes use a placeholder actor whose metadata says
        # authority_type="none" (a truthy STRING) — without this guard the
        # perception line would read "a none is standing ...".
        if atype in ("none", ""):
            atype = None
        second = gt.get("A_second_authority") or None
        # Live-at-this-instant directive state (falls back to the snapshot).
        gesture, onset_f, end_f = self._gesture_window()
        live = self._live_officer_state()
        officer_gone = (atype is not None and live is not None
                        and not live.get("present", True))
        # L1 perception reports APPEARANCE, not the legal class — "a
        # civilian is standing..." for the fake-vest director would leak the
        # L2 authority verdict into the perception rung.
        appearance = str(gt.get("A_appearance")
                         or _APPEARANCE.get(str(atype).lower(), "")) if atype else ""
        who_txt = f"a person {appearance}" if appearance else f"a {atype}"
        officer_xyz = gt.get("officer_transform")
        ego_xyz = gt.get("ego_spawn")
        dist_txt = ""
        try:
            dx = float(officer_xyz["x"]) - float(ego_xyz["x"])
            dy = float(officer_xyz["y"]) - float(ego_xyz["y"])
            # Euclidean distance, deliberately NOT "ahead": the assist must
            # stay true even if a scene ever places the director laterally.
            dist_txt = (f" about {(dx * dx + dy * dy) ** 0.5:.0f} m from "
                        "your spawn position")
        except (TypeError, KeyError, ValueError):
            pass
        if atype and officer_gone:
            lines.append(
                f"- Perception: {who_txt} was directing here earlier but is "
                "NO LONGER PRESENT (the person has left the scene). "
                f"Traffic light state: {gt.get('L_light_state') or 'unknown'}.")
        elif atype:
            # Perception reports what is visible AT THIS INSTANT — a gesture
            # that has not started yet (or has ended) shows an idle person.
            # Whether a past directive still binds is the L4 temporal link,
            # not perception.
            visible = f"making the {gesture} hand signal"
            if onset_f is not None and sim_time < onset_f:
                visible = "standing idle (not signalling at this moment)"
            elif end_f is not None and sim_time > end_f:
                visible = (f"standing idle now (was making the {gesture} "
                           "hand signal earlier)")
            lines.append(
                f"- Perception: {who_txt} is standing{dist_txt}, {visible}. "
                f"Traffic light state: "
                f"{gt.get('L_light_state') or 'unknown'}.")
        else:
            lines.append(
                "- Perception: no human director is present in this scene. "
                f"Traffic light state: {gt.get('L_light_state') or 'unknown'}.")
        if second is not None:
            # Second director: presence + appearance. Per-actor gesture
            # timing is not in the E-tuple, so the instant-state formula
            # ("when given") avoids false at-this-moment claims.
            app2 = _APPEARANCE.get(str(second.get("type")).lower(), "")
            who2 = f"a person {app2}" if app2 else f"a {second.get('type')}"
            lines.append(
                f"- Perception: a second person is also present near the "
                f"scene: {who2} (gesture when given: "
                f"{str(second.get('gesture') or 'IDLE').upper()}).")
        # L2 authority validity — the bare classification only; what to do
        # with an invalid director is left to the model at every rung.
        if self.ablation_rank >= 2 and atype:
            verdict = "IS" if authority.get("valid") else "is NOT"
            lines.append(
                f"- Authority: this {atype} {verdict} a legally valid "
                "traffic authority.")
        if self.ablation_rank >= 2 and second is not None:
            verdict = "IS" if second.get("valid") else "is NOT"
            lines.append(
                f"- Authority: the second person ({second.get('type')}) "
                f"{verdict} a legally valid traffic authority.")
        # L3 directive semantics for THIS vehicle.
        if self.ablation_rank >= 3 and atype:
            meaning = _GESTURE_MEANING.get(gesture, "unclear")
            relation = str(gt.get("T_target_relation") or "ego")
            if relation == "ego":
                lines.append(
                    f"- Semantics: this person's {gesture} gesture, when "
                    f"given, is directed at YOUR vehicle and means: "
                    f"{meaning}.")
            else:
                phrase = _RELATION_PHRASE.get(
                    relation, relation.replace("_", " "))
                lines.append(
                    f"- Semantics: this person's {gesture} gesture, when "
                    f"given, is directed at {phrase}, NOT at your "
                    "vehicle; it does not command you.")
        if self.ablation_rank >= 3 and second is not None:
            g2 = str(second.get("gesture") or "IDLE").upper()
            lines.append(
                f"- Semantics: the second person's {g2} gesture, when given, "
                f"means: {_GESTURE_MEANING.get(g2, 'unclear')}.")
        # L4 temporal state of the primary directive at THIS query. (The
        # E-tuple carries timing for the primary director only; the second
        # director's timing is deliberately not claimed.)
        if self.ablation_rank >= 4 and atype:
            if officer_gone:
                # The pure temporal fact after a director leaves: the
                # directive was given, and no release has been given. Whether
                # it still binds is exactly what the model must reason out.
                onset_txt = (f", given at t={onset_f:.1f}s"
                             if onset_f is not None else "")
                lines.append(
                    f"- Temporal: the person left the scene; their {gesture} "
                    f"directive{onset_txt} was never released (no release "
                    "signal was given before they left).")
            else:
                if onset_f is None:
                    state = "is ACTIVE right now (held for the whole episode)"
                    detail = f"t={sim_time:.1f}s"
                elif sim_time < onset_f:
                    state = "has NOT started yet"
                    detail = f"t={sim_time:.1f}s, onset={onset_f:.1f}s"
                elif end_f is not None and sim_time > end_f:
                    state = "has EXPIRED"
                    detail = (f"t={sim_time:.1f}s, onset={onset_f:.1f}s, "
                              f"ended={end_f:.1f}s")
                else:
                    state = "is ACTIVE right now"
                    detail = f"t={sim_time:.1f}s, onset={onset_f:.1f}s"
                    if end_f is not None:
                        detail += f", ends={end_f:.1f}s"
                lines.append(f"- Temporal: the primary directive {state} "
                             f"({detail}).")
        # L5 answer key — the EPISODE-level expected action. This is a label
        # at episode granularity, not a per-tick command (that is L6).
        if self.ablation_rank >= 5:
            y = str(gt.get("Y_expected_action") or "").upper()
            desc, _token = _EXPECTED_ACTION_DESC.get(y, ("", None))
            lines.append(
                f"- Expected outcome for this episode (ground truth): {y} "
                f"— {desc}.")
        # L6 per-tick oracle policy, translated into the reply vocabulary.
        if (self.ablation_rank >= ABLATION_LEVELS.index("policy")
                and self._last_policy_token):
            lines.append(
                f"- Policy (per-tick oracle): the correct action at this "
                f"instant is {self._last_policy_token}.")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _control_to_token(throttle: float, brake: float,
                          speed_mps: float) -> str:
        """Translate a continuous oracle control into the reply vocabulary."""
        if brake >= 0.3:
            return "HOLD" if speed_mps < 0.3 else "STOP"
        if throttle <= 0.25:
            return "SLOW"
        return "GO"

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

        # L6 shadow oracle: advance the reference policy every tick (its
        # internal state machine needs the full tick stream) and cache the
        # translated token for the next prompt. Compute-only — the shadow's
        # control is never applied to the vehicle.
        if self._oracle_shadow is not None:
            try:
                shadow = self._oracle_shadow.step(obs, dt)
                if shadow is not None:
                    self._last_policy_token = self._control_to_token(
                        float(getattr(shadow, "throttle", 0.0) or 0.0),
                        float(getattr(shadow, "brake", 0.0) or 0.0),
                        speed)
            except Exception as e:  # noqa: BLE001
                # Invalidate immediately: a stale token must not keep being
                # asserted as "the correct action at this instant" while the
                # shadow is unhealthy (adversarial review, round 7).
                self._last_policy_token = None
                self._record_error(sim_time, "oracle_shadow", str(e))
                log.warning("oracle shadow step failed: %s", e)

        # Query the VLM on a fixed cadence; reuse the last decision in between.
        image = obs.get("image")
        if (image is not None
                and sim_time - self._last_query_t >= self.query_period
                and (self.max_queries is None
                     or self._query_count < self.max_queries)):
            self._last_query_t = sim_time
            self._query_count += 1
            try:
                assist = self._ablation_assist(sim_time)
                dec = self._backend.decide(
                    image, speed_kmh, str(obs.get("tl_state") or ""),
                    assist=assist)
                self._record_decision(sim_time, speed_kmh,
                                      str(obs.get("tl_state") or ""), dec,
                                      assist=assist)
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
                         dec: Dict[str, Any], assist: str = "") -> None:
        record = {
            "t": round(float(sim_time), 3),
            "model": self.model,
            "backend": self.backend_name,
            "ablation": self.ablation,
            # Audit trail: the EXACT assist text injected at this query, so a
            # rung label can be checked against what was actually injected.
            "assist": assist,
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
