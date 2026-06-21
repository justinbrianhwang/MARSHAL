"""Reaction-latency criterion for the MARSHAL benchmark.

Measures the wall-clock delay between a traffic officer's gesture onset and
the first observable matching ego response. Unlike :class:`AuthorityComplianceCriterion`,
this criterion is purely a *measurement* tool: it has no STOP/PROCEED rule
verdict, only a latency value plus a "did we ever detect a reaction?" flag.

Trigger rules
-------------
The trigger that counts as "reaction" depends on the expected action:

* ``STOP``:    first sample after onset where
               ``ego_control.brake > brake_threshold`` OR
               instantaneous deceleration ``(v_prev - v) / dt > decel_threshold``.

* ``PROCEED``: depends on the ego's state at gesture onset:
                - If the ego was stopped (speed <= ``speed_eps``), trigger when
                  ``ego_control.throttle > throttle_threshold`` OR
                  ``ego_speed > speed_eps``.
                - If the ego was already moving, trigger the first instant the
                  ego enters the configured conflict zone.

Optional ``conflict_zone`` may be passed via :meth:`set_conflict_zone` (so the
criterion can be wired up before the scenario knows the zone geometry).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Any, List, Optional, Tuple

from marshal_bench.utils.carla_api_compat import import_carla

log = logging.getLogger("marshal_bench.criteria.reaction_latency")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class ReactionLatencyResult:
    """Outcome of the reaction-latency measurement."""

    latency: Optional[float]
    detected: bool
    trigger_kind: str  # "brake" / "decel" / "throttle" / "motion" / "zone_entry" / "none"
    notes: str = ""

    def to_json(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ego_speed_mps(ego: Any) -> float:
    if ego is None:
        return 0.0
    try:
        v = ego.get_velocity()
        return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
    except Exception:
        return 0.0


def _get_control(ego: Any) -> Tuple[float, float]:
    """Return ``(brake, throttle)`` from the ego's last control input, [0, 1].

    Returns ``(0.0, 0.0)`` if unavailable.
    """
    if ego is None:
        return 0.0, 0.0
    try:
        ctrl = ego.get_control()
    except Exception:
        return 0.0, 0.0
    brake = float(getattr(ctrl, "brake", 0.0) or 0.0)
    throttle = float(getattr(ctrl, "throttle", 0.0) or 0.0)
    return brake, throttle


def _point_in_bbox(location: Any, bbox: Any) -> bool:
    """Same containment check as :mod:`authority_compliance`."""
    if bbox is None or location is None:
        return False
    if hasattr(bbox, "contains"):
        try:
            carla = import_carla()
            return bool(bbox.contains(location, carla.Transform()))
        except Exception:
            pass
    if isinstance(bbox, dict) and "center" in bbox and "extent" in bbox:
        c = bbox["center"]
        ex = bbox["extent"]
        return (
            abs(location.x - c.x) <= ex[0]
            and abs(location.y - c.y) <= ex[1]
            and abs(location.z - c.z) <= ex[2]
        )
    if isinstance(bbox, tuple) and len(bbox) == 2:
        center, radius = bbox
        dx = location.x - center.x
        dy = location.y - center.y
        dz = location.z - center.z
        return math.sqrt(dx * dx + dy * dy + dz * dz) <= float(radius)
    return False


# ---------------------------------------------------------------------------
# Main criterion
# ---------------------------------------------------------------------------
class ReactionLatencyCriterion:
    """Measure ego reaction latency to an officer gesture."""

    # DETOUR/YIELD/HOLD are accepted and reduced to their measurable motion
    # class (latency is a pure measurement tool: DETOUR ~ PROCEED, YIELD/HOLD ~
    # STOP).
    VALID_ACTIONS = {"STOP", "PROCEED", "IGNORE", "DETOUR", "YIELD", "HOLD"}

    def __init__(
        self,
        ego_vehicle: Any,
        officer: Any,
        expected_action: str,
        brake_threshold: float = 0.2,
        throttle_threshold: float = 0.15,
        decel_threshold: float = 1.0,
        speed_eps: float = 0.3,
    ) -> None:
        if expected_action not in self.VALID_ACTIONS:
            raise ValueError(
                f"expected_action must be one of {self.VALID_ACTIONS}, "
                f"got {expected_action!r}"
            )
        self.ego = ego_vehicle
        self.officer = officer
        # Reduce contextual actions to the motion class this measurement tool
        # can time: DETOUR -> PROCEED (forward motion), YIELD -> STOP (decel).
        self.expected_action = {"DETOUR": "PROCEED", "YIELD": "STOP",
                                "HOLD": "STOP"}.get(
            expected_action, expected_action)
        self.brake_threshold = float(brake_threshold)
        self.throttle_threshold = float(throttle_threshold)
        self.decel_threshold = float(decel_threshold)
        self.speed_eps = float(speed_eps)

        # rolling samples (kept compact)
        self._samples: List[Tuple[float, float, float, float]] = []
        # baseline state captured at onset
        self._baseline_speed: Optional[float] = None
        self._baseline_captured: bool = False
        # detected trigger
        self._trigger_time: Optional[float] = None
        self._trigger_kind: str = "none"
        # last known sample (for delta computations)
        self._prev_sample: Optional[Tuple[float, float, float, float]] = None
        # optional conflict zone for PROCEED + moving ego
        self._conflict_zone: Any = None

    # ---- public API -------------------------------------------------------
    def set_conflict_zone(self, conflict_zone: Any) -> None:
        """Attach a conflict zone (carla.BoundingBox / dict / (center,radius))."""
        self._conflict_zone = conflict_zone

    def tick(self, timestamp: float) -> None:
        """Record one sample and look for the first trigger after onset."""
        if self._trigger_time is not None:
            # Still record samples for context but skip trigger search.
            self._record(timestamp)
            return

        speed = _ego_speed_mps(self.ego)
        brake, throttle = _get_control(self.ego)
        sample = (float(timestamp), speed, brake, throttle)
        self._samples.append(sample)

        onset_time = self._get_onset_time()

        if onset_time is None or timestamp < onset_time:
            self._prev_sample = sample
            return

        # Lazily capture baseline at the first sample at/after onset.
        if not self._baseline_captured:
            self._baseline_speed = speed
            self._baseline_captured = True

        triggered_kind = self._check_trigger(sample, onset_time)
        if triggered_kind is not None:
            self._trigger_time = timestamp
            self._trigger_kind = triggered_kind

        self._prev_sample = sample

    def evaluate(self) -> ReactionLatencyResult:
        """Compute the final latency and trigger metadata."""
        onset_time = self._get_onset_time()
        if onset_time is None:
            return ReactionLatencyResult(
                latency=None,
                detected=False,
                trigger_kind="none",
                notes="officer onset_time was never available",
            )
        if self._trigger_time is None:
            return ReactionLatencyResult(
                latency=None,
                detected=False,
                trigger_kind="none",
                notes=(
                    f"no {self.expected_action} reaction detected after "
                    f"onset (onset_time={onset_time})"
                ),
            )
        latency = self._trigger_time - onset_time
        return ReactionLatencyResult(
            latency=latency,
            detected=True,
            trigger_kind=self._trigger_kind,
            notes=(
                f"baseline_speed_at_onset={self._baseline_speed}, "
                f"expected_action={self.expected_action}"
            ),
        )

    # ---- internals --------------------------------------------------------
    def _record(self, timestamp: float) -> None:
        speed = _ego_speed_mps(self.ego)
        brake, throttle = _get_control(self.ego)
        self._samples.append((float(timestamp), speed, brake, throttle))

    def _get_onset_time(self) -> Optional[float]:
        try:
            meta = self.officer.get_metadata() or {}
        except Exception:
            return None
        v = meta.get("onset_time")
        return float(v) if v is not None else None

    def _check_trigger(
        self, sample: Tuple[float, float, float, float], onset_time: float
    ) -> Optional[str]:
        ts, speed, brake, throttle = sample

        if self.expected_action == "STOP":
            if brake > self.brake_threshold:
                return "brake"
            if self._prev_sample is not None:
                p_ts, p_speed, _, _ = self._prev_sample
                dt = ts - p_ts
                if dt > 1e-6:
                    decel = (p_speed - speed) / dt
                    if decel > self.decel_threshold:
                        return "decel"
            return None

        if self.expected_action == "PROCEED":
            baseline = self._baseline_speed if self._baseline_speed is not None else 0.0
            if baseline <= self.speed_eps:
                # Ego was stopped at onset: throttle or speed-up counts.
                if throttle > self.throttle_threshold:
                    return "throttle"
                if speed > self.speed_eps:
                    return "motion"
                return None
            # Ego was already moving: first conflict-zone entry counts.
            if self._conflict_zone is None:
                # Fall back to any further acceleration above baseline.
                if speed > baseline + self.speed_eps:
                    return "motion"
                return None
            try:
                ego_loc = self.ego.get_location()
            except Exception:
                return None
            if _point_in_bbox(ego_loc, self._conflict_zone):
                return "zone_entry"
            return None

        # IGNORE: there is no expected reaction; we still report 'no reaction'.
        return None

    # ---- serialization ----------------------------------------------------
    def to_json(self) -> dict:
        """Serialize the latency verdict and supporting state."""
        try:
            verdict = self.evaluate().to_json()
        except Exception as e:
            log.debug("evaluate() during to_json failed: %s", e)
            verdict = {"error": str(e)}
        return {
            "type": "ReactionLatencyCriterion",
            "expected_action": self.expected_action,
            "thresholds": {
                "brake": self.brake_threshold,
                "throttle": self.throttle_threshold,
                "decel": self.decel_threshold,
                "speed_eps": self.speed_eps,
            },
            "verdict": verdict,
            "baseline_speed": self._baseline_speed,
            "trigger_time": self._trigger_time,
            "trigger_kind": self._trigger_kind,
            "n_samples": len(self._samples),
        }
