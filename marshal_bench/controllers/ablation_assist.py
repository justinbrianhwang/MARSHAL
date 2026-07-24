"""Oracle-assist ablation ladder machinery, shared by every assisted wiring.

The ladder is a set of privileged DIAGNOSTIC runs (never leaderboard rows):
each level cumulatively injects one more link of the ground-truth reasoning
chain into the controller's prompt, so the score jump between adjacent levels
attributes the failure to that link. The levels:

    none       - the plain controller (no assist)
    perception - what a perfect perception module would report (who/where/
                 which gesture/light), with NO interpretation
    authority  - + whether each director is a legally valid traffic authority
                 (the bare classification; what to DO with it is the model's)
    semantics  - + what the gesture means for THIS vehicle
    temporal   - + whether the directive is currently active or expired
    action     - + the episode-level ground-truth expected action (an answer
                 key at episode granularity, NOT a per-tick policy)
    policy     - + the per-tick output of the verified oracle policy,
                 translated into the reply vocabulary (tests whether the
                 interface can execute when the plan is external)

The injected text must be IDENTICAL across wirings (per-tick VLM, trajectory
planners, ...) or the ladder is not comparable, so every controller builds it
through :class:`AblationAssist` and nothing else. The truthfulness rules are
pinned by ``tests/test_vlm_ablation_assist.py``; cross-wiring identity is
pinned by ``tests/test_openemma_ablation_assist.py``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

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


class AblationAssist:
    """Level parsing, GT storage/validation, live-officer tracking, gesture
    window logic, assist text construction, and policy-token state for one
    ablated controller instance."""

    def __init__(self, level: str) -> None:
        level = str(level or "none").lower()
        if level not in ABLATION_LEVELS:
            raise ValueError(
                f"ablation={level!r} is not one of {ABLATION_LEVELS}")
        self.level = level
        self.rank = ABLATION_LEVELS.index(level)
        # Ablation runs read the privileged E-tuple by design; they are
        # diagnostics, not leaderboard entries.
        self.requests_privileged_gt = level != "none"
        self.gt: Dict[str, Any] = {}
        self.officer_ref: Any = None
        self.last_policy_token: Optional[str] = None

    @classmethod
    def from_config(cls, cfg: Optional[dict],
                    env_var: str = "MARSHAL_VLM_ABLATION") -> "AblationAssist":
        """Build from a controller config section (``ablation`` key), falling
        back to the shared env var so runners can select the rung for every
        wiring at once."""
        cfg = cfg or {}
        return cls(str(cfg.get("ablation", os.environ.get(env_var, "none"))).lower())

    # ------------------------------------------------------------------
    # Ground truth
    # ------------------------------------------------------------------
    def set_ground_truth(self, ground_truth: Optional[dict]) -> None:
        self.gt = dict(ground_truth or {})

    def validate_gt(self) -> None:
        """Fail LOUDLY on malformed privileged data.

        The ladder is only interpretable if every rung injects true
        information; a silently-degraded assist (missing key rendered as
        prose, None validity rendered as "NOT valid") would give nominally
        identical rung labels different actual assists.
        """
        gt = self.gt
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
        if self.rank >= ABLATION_LEVELS.index("action"):
            y = str(gt.get("Y_expected_action") or "").upper()
            if y not in _EXPECTED_ACTION_DESC:
                problems.append(
                    f"Y_expected_action {y!r} has no honest description")
        if problems:
            raise ValueError(
                "ablation ground truth is malformed (refusing to run a "
                "silently-degraded rung): " + "; ".join(problems))

    # ------------------------------------------------------------------
    # Live officer tracking
    # ------------------------------------------------------------------
    def set_officer_ref(self, officer: Any) -> None:
        """Privileged runs only: live handle to the scene's director.

        The setup-time GT snapshot goes stale when a scenario re-issues a
        gesture mid-episode (flagger SLOW→STOP) or removes the officer
        (sequential_directive); the assist must describe the scene as it IS
        at the query instant (adversarial review, round 7).
        """
        self.officer_ref = officer

    def live_officer_state(self) -> Optional[Dict[str, Any]]:
        """Current director state, or None when no live handle exists."""
        ref = self.officer_ref
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

    def gesture_window(self):
        """(gesture, onset, end) of the primary directive at query time.

        Prefers the LIVE officer metadata (phase switches, mid-episode
        re-issues); falls back to the setup-time snapshot. The active window
        is CLOSED [onset, onset + duration] to match the telemetry
        recorder's officer_active definition — at exactly onset + duration
        the gesture is still showing. A None onset means the gesture runs
        for the whole episode.
        """
        gt = self.gt
        gesture = str(gt.get("G_gesture") or "IDLE").upper()
        onset = gt.get("G_gesture_onset_sec")
        duration = gt.get("G_gesture_duration_sec")
        live = self.live_officer_state()
        if live is not None and live.get("present") and live.get("gesture"):
            gesture = live["gesture"]
            onset = live.get("onset", onset)
            duration = live.get("duration")
        onset_f = float(onset) if onset is not None else None
        end_f = (onset_f + float(duration)
                 if onset_f is not None and duration is not None else None)
        return gesture, onset_f, end_f

    # ------------------------------------------------------------------
    # Assist text
    # ------------------------------------------------------------------
    def assist(self, sim_time: float) -> str:
        """Cumulative ground-truth assist blocks for the ablation ladder."""
        if self.rank <= 0:
            return ""
        gt = self.gt
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
        gesture, onset_f, end_f = self.gesture_window()
        live = self.live_officer_state()
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
        if self.rank >= 2 and atype:
            verdict = "IS" if authority.get("valid") else "is NOT"
            lines.append(
                f"- Authority: this {atype} {verdict} a legally valid "
                "traffic authority.")
        if self.rank >= 2 and second is not None:
            verdict = "IS" if second.get("valid") else "is NOT"
            lines.append(
                f"- Authority: the second person ({second.get('type')}) "
                f"{verdict} a legally valid traffic authority.")
        # L3 directive semantics for THIS vehicle.
        if self.rank >= 3 and atype:
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
        if self.rank >= 3 and second is not None:
            g2 = str(second.get("gesture") or "IDLE").upper()
            lines.append(
                f"- Semantics: the second person's {g2} gesture, when given, "
                f"means: {_GESTURE_MEANING.get(g2, 'unclear')}.")
        # L4 temporal state of the primary directive at THIS query. (The
        # E-tuple carries timing for the primary director only; the second
        # director's timing is deliberately not claimed.)
        if self.rank >= 4 and atype:
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
        if self.rank >= 5:
            y = str(gt.get("Y_expected_action") or "").upper()
            desc, _token = _EXPECTED_ACTION_DESC.get(y, ("", None))
            lines.append(
                f"- Expected outcome for this episode (ground truth): {y} "
                f"— {desc}.")
        # L6 per-tick oracle policy, translated into the reply vocabulary.
        if (self.rank >= ABLATION_LEVELS.index("policy")
                and self.last_policy_token):
            lines.append(
                f"- Policy (per-tick oracle): the correct action at this "
                f"instant is {self.last_policy_token}.")
        return "\n".join(lines) + "\n"

    @staticmethod
    def control_to_token(throttle: float, brake: float,
                         speed_mps: float) -> str:
        """Translate a continuous oracle control into the reply vocabulary."""
        if brake >= 0.3:
            return "HOLD" if speed_mps < 0.3 else "STOP"
        if throttle <= 0.25:
            return "SLOW"
        return "GO"


__all__ = ["ABLATION_LEVELS", "AblationAssist"]
