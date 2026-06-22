"""Skeleton-driven traffic-officer gesture engine for MARSHAL.

This module implements the GestureEngine that converts a high-level GestureID
(STOP / PROCEED / LEFT / RIGHT / SLOW / IDLE) into a sequence of bone-relative
rotations applied to a CARLA walker actor via the WalkerBoneControlIn API.

Reference: CARLA 0.9.16 walker bone API
  - walker.get_bones() -> WalkerBoneControlOut with .bone_transforms entries
    exposing (name, world, component, relative) -- relative is the transform
    relative to the bone's parent and is what walker.set_bones() expects.
  - walker.set_bones(WalkerBoneControlIn([(name, Transform), ...]))
  - walker.blend_pose(1.0) / walker.show_pose() to display the custom pose
  - walker.hide_pose() to revert to the default animation.

If the skeleton API or bone-name inference fails, the caller (TrafficOfficer)
is expected to fall back to debug-marker visualization.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from marshal_bench.utils.carla_api_compat import (
    Capabilities,
    detect_capabilities,
    import_carla,
)

log = logging.getLogger("marshal_bench.actors.gesture_engine")


# ---------------------------------------------------------------------------
# Public enums & dataclasses
# ---------------------------------------------------------------------------
class GestureID(Enum):
    """High-level gesture identifiers used by the MARSHAL benchmark."""

    IDLE = "IDLE"
    STOP = "STOP"
    PROCEED = "PROCEED"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    SLOW = "SLOW"
    HOLD = "HOLD"   # palm-up "wait" — do not move yet, but not a full STOP


@dataclass
class GestureState:
    """Runtime state describing the gesture currently commanded on an officer."""

    gesture_id: GestureID
    onset_time: float
    duration: Optional[float] = None
    target_relation: str = "ego"
    target_lane_id: Optional[int] = None
    authority_valid: bool = True


@dataclass
class PoseKeyframe:
    """A single keyframe in a gesture animation.

    `t` is seconds into the gesture cycle. `bones` maps bone name -> the
    parent-relative carla.Transform that should be set for that bone at this
    keyframe (the engine will interpolate between successive keyframes).
    """

    t: float
    bones: dict[str, "Any"] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Bone-name inference
# ---------------------------------------------------------------------------
# Canonical key -> ordered list of substring patterns (case-insensitive). The
# first bone whose lowercased name contains ALL substrings in any single
# pattern wins. Patterns are tried in declared order. We support:
#   - CARLA 0.9.x:  crl_arm__R, crl_foreArm__R, crl_shoulder__R, crl_hand__R
#   - UE mannequin: upperarm_r, lowerarm_r, hand_r, clavicle_r
#   - Mixamo:       RightArm, RightForeArm, RightHand, RightShoulder
_BONE_PATTERNS: dict[str, list[tuple[str, ...]]] = {
    "r_shoulder":  [("crl_shoulder", "_r"), ("clavicle_r",), ("rightshoulder",), ("shoulder_r",)],
    "r_upper_arm": [("crl_arm", "_r"), ("upperarm_r",), ("rightarm",), ("upper_arm_r",)],
    "r_forearm":   [("crl_forearm", "_r"), ("lowerarm_r",), ("rightforearm",), ("forearm_r",)],
    "r_hand":      [("crl_hand", "_r"), ("hand_r",), ("righthand",)],
    "l_shoulder":  [("crl_shoulder", "_l"), ("clavicle_l",), ("leftshoulder",), ("shoulder_l",)],
    "l_upper_arm": [("crl_arm", "_l"), ("upperarm_l",), ("leftarm",), ("upper_arm_l",)],
    "l_forearm":   [("crl_forearm", "_l"), ("lowerarm_l",), ("leftforearm",), ("forearm_l",)],
    "l_hand":      [("crl_hand", "_l"), ("hand_l",), ("lefthand",)],
    "spine":       [("crl_spine01",), ("crl_spine",), ("spine_03",), ("spine03",), ("spine_02",), ("spine",)],
    "head":        [("crl_head",), ("head",)],
}


def infer_upper_limb_bones(bone_names: list[str]) -> dict[str, Optional[str]]:
    """Map canonical limb keys to actual bone names from `bone_names`.

    Returns a dict with all keys in _BONE_PATTERNS; value is the matched bone
    name or None when no candidate satisfied any pattern.
    """
    lowered = [(n, n.lower()) for n in bone_names]
    out: dict[str, Optional[str]] = {}
    for key, patterns in _BONE_PATTERNS.items():
        match: Optional[str] = None
        for pat in patterns:
            for original, low in lowered:
                if all(tok in low for tok in pat):
                    # avoid matching "crl_arm" pattern against "crl_foreArm" / "forearm"
                    if key.endswith("upper_arm") and "fore" in low:
                        continue
                    if key.endswith("shoulder") and "spine" in low:
                        continue
                    match = original
                    break
            if match is not None:
                break
        out[key] = match
    return out


# ---------------------------------------------------------------------------
# Helpers for extracting bone-transform attributes (the pyi shows tuples but
# the live API exposes objects with .name/.world/.component/.relative attrs;
# we support both).
# ---------------------------------------------------------------------------
def _bone_entry_name(entry: Any) -> Optional[str]:
    if hasattr(entry, "name"):
        return entry.name
    try:
        return entry[0]
    except Exception:
        return None


def _bone_entry_relative(entry: Any) -> Optional[Any]:
    if hasattr(entry, "relative"):
        return entry.relative
    try:
        return entry[3]
    except Exception:
        return None


def _clone_transform(tf: Any, carla: Any) -> Any:
    """Build a fresh Transform from another so we can mutate freely."""
    loc = carla.Location(x=tf.location.x, y=tf.location.y, z=tf.location.z)
    rot = carla.Rotation(pitch=tf.rotation.pitch, yaw=tf.rotation.yaw, roll=tf.rotation.roll)
    return carla.Transform(loc, rot)


def _make_rotation(carla: Any, pitch: float = 0.0, yaw: float = 0.0, roll: float = 0.0) -> Any:
    return carla.Transform(carla.Location(0.0, 0.0, 0.0), carla.Rotation(pitch=pitch, yaw=yaw, roll=roll))


# ---------------------------------------------------------------------------
# Gesture engine
# ---------------------------------------------------------------------------
class GestureEngine:
    """Apply MARSHAL gestures to a CARLA Walker via WalkerBoneControlIn.

    The engine inspects the walker's skeleton once, infers a canonical bone map,
    snapshots rest-pose relative rotations, then composes per-gesture local
    rotations on top of the baseline.
    """

    # cycle period (seconds) for cyclic gestures
    PROCEED_CYCLE_S: float = 1.6
    SLOW_CYCLE_S: float = 1.2

    def __init__(self, caps: Optional[Capabilities] = None):
        self._caps: Capabilities = caps or detect_capabilities()
        self.pose_baseline_rotations_: dict[int, dict[str, Any]] = {}
        self._bone_map_cache: dict[int, dict[str, Optional[str]]] = {}
        self._all_bones_cache: dict[int, list[str]] = {}

    # ------------------------------------------------------------------ caps
    def supports_skeleton_control(self, actor: Any) -> bool:
        """True iff CARLA exposes the bone-set API AND this actor returns bones."""
        if not (self._caps.has_walker_set_bones and self._caps.has_walker_get_bones):
            return False
        try:
            bones = actor.get_bones()
            entries = getattr(bones, "bone_transforms", None) or []
            return len(entries) > 0
        except Exception as e:
            log.debug("supports_skeleton_control: get_bones() failed: %s", e)
            return False

    # ----------------------------------------------------- baseline snapshot
    def _ensure_baseline(self, actor: Any) -> dict[str, Any]:
        key = id(actor)
        if key in self.pose_baseline_rotations_:
            return self.pose_baseline_rotations_[key]

        carla = import_carla()
        try:
            bones = actor.get_bones()
            entries = list(getattr(bones, "bone_transforms", []) or [])
        except Exception as e:
            log.warning("Cannot snapshot baseline pose: get_bones() failed: %s", e)
            entries = []

        baseline: dict[str, Any] = {}
        names: list[str] = []
        for entry in entries:
            name = _bone_entry_name(entry)
            rel = _bone_entry_relative(entry)
            if name is None or rel is None:
                continue
            names.append(name)
            baseline[name] = _clone_transform(rel, carla)
        self.pose_baseline_rotations_[key] = baseline
        self._all_bones_cache[key] = names
        self._bone_map_cache[key] = infer_upper_limb_bones(names)
        missing = [k for k, v in self._bone_map_cache[key].items() if v is None]
        if missing:
            log.warning("GestureEngine: could not infer bones for %s (have %d total)", missing, len(names))
        return baseline

    def _bone_map(self, actor: Any) -> dict[str, Optional[str]]:
        self._ensure_baseline(actor)
        return self._bone_map_cache.get(id(actor), {})

    # ------------------------------------------------------------ keyframes
    def build_pose_sequence(self, gesture_id: GestureID) -> list[PoseKeyframe]:
        """Return a deterministic list of (t, bone_rotation_dict) keyframes.

        These dicts use the *canonical* limb keys (r_upper_arm, ...) — the
        caller resolves them to actual bone names before pushing to CARLA.
        Each value is a (pitch, yaw, roll) tuple in degrees (LOCAL bone frame,
        layered on top of the rest-pose baseline).
        """
        # We return PoseKeyframe with .bones storing tuples for portability
        # at this layer; apply_gesture is what actually constructs carla.Transform.
        if gesture_id is GestureID.IDLE:
            return []

        if gesture_id is GestureID.STOP:
            return [
                PoseKeyframe(t=0.0, bones={
                    "r_upper_arm": (-90.0, 0.0, 0.0),   # raise right arm forward
                    "r_forearm":   (-10.0, 0.0, 0.0),   # slight up at elbow -> palm faces ego
                    "r_hand":      (0.0, 0.0, 0.0),
                }),
            ]

        if gesture_id is GestureID.PROCEED:
            return [
                PoseKeyframe(t=0.0, bones={
                    "r_upper_arm": (-70.0, -30.0, 0.0),
                    "r_forearm":   (-15.0, 0.0, 0.0),
                }),
                PoseKeyframe(t=0.5, bones={
                    "r_upper_arm": (-70.0, 30.0, 0.0),
                    "r_forearm":   (-15.0, 0.0, 0.0),
                }),
                PoseKeyframe(t=1.0, bones={
                    "r_upper_arm": (-70.0, -30.0, 0.0),
                    "r_forearm":   (-15.0, 0.0, 0.0),
                }),
            ]

        if gesture_id is GestureID.LEFT:
            return [
                PoseKeyframe(t=0.0, bones={
                    "l_upper_arm": (-30.0, 70.0, 0.0),
                    "l_forearm":   (-10.0, 0.0, 0.0),
                }),
            ]

        if gesture_id is GestureID.RIGHT:
            return [
                PoseKeyframe(t=0.0, bones={
                    "r_upper_arm": (-30.0, -70.0, 0.0),
                    "r_forearm":   (-10.0, 0.0, 0.0),
                }),
            ]

        if gesture_id is GestureID.HOLD:
            # "Wait": forearm raised at the elbow, palm up — distinct from the
            # full forward-extended STOP. A static hold.
            return [
                PoseKeyframe(t=0.0, bones={
                    "r_upper_arm": (-35.0, 0.0, 0.0),
                    "r_forearm":   (-95.0, 0.0, 0.0),   # bend elbow up, palm up
                    "r_hand":      (0.0, 0.0, 0.0),
                }),
            ]

        if gesture_id is GestureID.SLOW:
            return [
                PoseKeyframe(t=0.0, bones={
                    "r_upper_arm": (-30.0, 0.0, 0.0),
                    "r_forearm":   (-10.0, 0.0, -90.0),  # palm-down via roll
                }),
                PoseKeyframe(t=0.5, bones={
                    "r_upper_arm": (-60.0, 0.0, 0.0),
                    "r_forearm":   (-10.0, 0.0, -90.0),
                }),
                PoseKeyframe(t=1.0, bones={
                    "r_upper_arm": (-30.0, 0.0, 0.0),
                    "r_forearm":   (-10.0, 0.0, -90.0),
                }),
            ]

        return []

    # ---------------------------------------------------------- dispatcher
    def apply_gesture(self, actor: Any, gesture_state: GestureState, sim_time: float) -> bool:
        """Apply `gesture_state` to `actor` at the given simulation time.

        Returns True on success, False if skeleton control is unavailable or
        the underlying CARLA call raised. The caller can then trigger the
        debug-visualization fallback.
        """
        gid = gesture_state.gesture_id
        if gid is GestureID.IDLE:
            return self.apply_idle(actor)

        if gid is GestureID.STOP:
            # STOP is a static hold -> phase=1 if duration elapsed, else linear ramp
            dur = gesture_state.duration or 1.0
            phase = max(0.0, min(1.0, (sim_time - gesture_state.onset_time) / max(dur, 1e-6)))
            return self.apply_stop(actor, phase)

        if gid is GestureID.HOLD:
            dur = gesture_state.duration or 1.0
            phase = max(0.0, min(1.0, (sim_time - gesture_state.onset_time) / max(dur, 1e-6)))
            return self.apply_hold(actor, phase)

        if gid is GestureID.PROCEED:
            phase = ((sim_time - gesture_state.onset_time) % self.PROCEED_CYCLE_S) / self.PROCEED_CYCLE_S
            return self.apply_proceed(actor, phase)

        if gid is GestureID.SLOW:
            phase = ((sim_time - gesture_state.onset_time) % self.SLOW_CYCLE_S) / self.SLOW_CYCLE_S
            return self.apply_slow(actor, phase)

        if gid is GestureID.LEFT:
            dur = gesture_state.duration or 1.0
            phase = max(0.0, min(1.0, (sim_time - gesture_state.onset_time) / max(dur, 1e-6)))
            return self.apply_left(actor, phase)

        if gid is GestureID.RIGHT:
            dur = gesture_state.duration or 1.0
            phase = max(0.0, min(1.0, (sim_time - gesture_state.onset_time) / max(dur, 1e-6)))
            return self.apply_right(actor, phase)

        return False

    # -------------------------------------------------------- per-gesture
    def apply_idle(self, actor: Any) -> bool:
        try:
            if hasattr(actor, "hide_pose"):
                actor.hide_pose()
            elif hasattr(actor, "blend_pose"):
                actor.blend_pose(0.0)
            return True
        except Exception as e:
            log.debug("apply_idle: %s", e)
            return False

    def apply_stop(self, actor: Any, phase: float) -> bool:
        kf = self.build_pose_sequence(GestureID.STOP)
        return self._apply_keyframes(actor, kf, phase, cyclic=False)

    def apply_hold(self, actor: Any, phase: float) -> bool:
        kf = self.build_pose_sequence(GestureID.HOLD)
        return self._apply_keyframes(actor, kf, phase, cyclic=False)

    def apply_proceed(self, actor: Any, phase: float) -> bool:
        kf = self.build_pose_sequence(GestureID.PROCEED)
        return self._apply_keyframes(actor, kf, phase, cyclic=True)

    def apply_left(self, actor: Any, phase: float) -> bool:
        kf = self.build_pose_sequence(GestureID.LEFT)
        return self._apply_keyframes(actor, kf, phase, cyclic=False)

    def apply_right(self, actor: Any, phase: float) -> bool:
        kf = self.build_pose_sequence(GestureID.RIGHT)
        return self._apply_keyframes(actor, kf, phase, cyclic=False)

    def apply_slow(self, actor: Any, phase: float) -> bool:
        kf = self.build_pose_sequence(GestureID.SLOW)
        return self._apply_keyframes(actor, kf, phase, cyclic=True)

    # ---------------------------------------------------------- internals
    def _interpolate_keyframes(
        self, keyframes: list[PoseKeyframe], phase: float, cyclic: bool
    ) -> dict[str, tuple[float, float, float]]:
        """Linear interpolation between successive keyframes for the given phase."""
        if not keyframes:
            return {}
        if len(keyframes) == 1:
            return {k: tuple(v) for k, v in keyframes[0].bones.items()}  # type: ignore[misc]

        phase = phase % 1.0 if cyclic else max(0.0, min(1.0, phase))
        ts = [kf.t for kf in keyframes]
        # find span [i, i+1]
        for i in range(len(keyframes) - 1):
            if ts[i] <= phase <= ts[i + 1]:
                t0, t1 = ts[i], ts[i + 1]
                span = max(t1 - t0, 1e-6)
                alpha = (phase - t0) / span
                a, b = keyframes[i].bones, keyframes[i + 1].bones
                keys = set(a.keys()) | set(b.keys())
                out: dict[str, tuple[float, float, float]] = {}
                for k in keys:
                    ax = a.get(k, (0.0, 0.0, 0.0))
                    bx = b.get(k, (0.0, 0.0, 0.0))
                    out[k] = (
                        ax[0] + (bx[0] - ax[0]) * alpha,
                        ax[1] + (bx[1] - ax[1]) * alpha,
                        ax[2] + (bx[2] - ax[2]) * alpha,
                    )
                return out
        return {k: tuple(v) for k, v in keyframes[-1].bones.items()}  # type: ignore[misc]

    def _apply_keyframes(
        self,
        actor: Any,
        keyframes: list[PoseKeyframe],
        phase: float,
        cyclic: bool,
    ) -> bool:
        if not keyframes:
            return self.apply_idle(actor)

        carla = import_carla()
        try:
            baseline = self._ensure_baseline(actor)
            bone_map = self._bone_map(actor)
            interp = self._interpolate_keyframes(keyframes, phase, cyclic)

            # Resolve canonical gesture keys -> actual bone-name deltas.
            deltas: dict[str, tuple[float, float, float]] = {}
            for canonical_key, dpyr in interp.items():
                bone_name = bone_map.get(canonical_key)
                if bone_name:
                    deltas[bone_name] = dpyr

            # IMPORTANT: send EVERY snapshotted bone (at its baseline rotation),
            # overlaying the gesture deltas only on the signalling limb. If we
            # send only the gesturing bones, blend_pose(1.0) snaps every other
            # bone to the skeleton's bind/T-pose (arms splayed out) — which looks
            # like a shrug. Posing the whole skeleton at baseline keeps the body
            # in its natural standing pose while one arm performs the signal.
            payload: list[tuple[str, Any]] = []
            for bone_name, base_tf in baseline.items():
                dp, dy, dr = deltas.get(bone_name, (0.0, 0.0, 0.0))
                new_tf = _clone_transform(base_tf, carla)
                new_tf.rotation.pitch = base_tf.rotation.pitch + dp
                new_tf.rotation.yaw = base_tf.rotation.yaw + dy
                new_tf.rotation.roll = base_tf.rotation.roll + dr
                payload.append((bone_name, new_tf))

            if not payload:
                log.warning("No bones mapped — cannot apply gesture via skeleton.")
                return False

            control_in = carla.WalkerBoneControlIn(payload)
            actor.set_bones(control_in)
            if hasattr(actor, "blend_pose"):
                actor.blend_pose(1.0)
            elif hasattr(actor, "show_pose"):
                actor.show_pose()
            return True
        except Exception as e:
            log.warning("Failed to apply skeleton gesture: %s", e)
            return False
