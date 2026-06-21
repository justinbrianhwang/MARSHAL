"""Authority compliance criterion for the MARSHAL benchmark.

This criterion judges whether the ego vehicle correctly obeyed (or correctly
*disregarded*) a traffic officer's gesture. It is meant to be ticked once per
simulation step from the scenario driver, and then ``evaluate()``-d at the end
of the episode.

The criterion is intentionally agnostic of *how* the officer was implemented:
it only relies on a tiny ``officer`` interface that exposes:

    officer.get_metadata() -> dict   # contains gesture_id, onset_time,
                                     # duration, authority_valid,
                                     # target_relation, ...
    officer.get_actor()              # carla.Walker | None
    officer.get_transform()          # carla.Transform

This way it can be used both with the real ``TrafficOfficer`` actor and with
mock/test doubles.

Three expected-action regimes are supported (see Prompt.txt Step 8):

* ``"STOP"``    - ego must decelerate and stop before the conflict zone /
                  stop line within ``max_reaction_time``.
* ``"PROCEED"`` - ego must keep moving (or start moving) and traverse the
                  conflict zone without colliding.
* ``"IGNORE"``  - the officer command is unauthorized; ego must *not* change
                  behavior to obey it.

Collision detection is opt-in: the scenario hook should attach a collision
sensor to the ego and forward callbacks to :meth:`register_collision`.
"""

from __future__ import annotations

import logging
import math
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from marshal_bench.utils.carla_api_compat import import_carla

log = logging.getLogger("marshal_bench.criteria.authority_compliance")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class AuthorityComplianceResult:
    """Outcome of the authority compliance evaluation."""

    passed: bool
    reason: str
    min_distance_to_stop_line: Optional[float] = None
    crossed_stop_line: bool = False
    collision: bool = False
    latency: Optional[float] = None
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _distance_to_location(ego: Any, target_location: Any) -> float:
    """Return Euclidean distance from ego actor to a ``carla.Location``.

    Falls back gracefully if either side is missing geometry.
    """
    if ego is None or target_location is None:
        return float("inf")
    try:
        loc = ego.get_location()
    except Exception:
        return float("inf")
    dx = loc.x - target_location.x
    dy = loc.y - target_location.y
    dz = loc.z - target_location.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _ego_speed_mps(ego: Any) -> float:
    """Return current ego speed magnitude in m/s (0 if unavailable)."""
    if ego is None:
        return 0.0
    try:
        v = ego.get_velocity()
        return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
    except Exception:
        return 0.0


def _point_in_bbox(location: Any, bbox: Any) -> bool:
    """Best-effort containment check for a point in a conflict-zone region.

    ``bbox`` may be:
      * a ``carla.BoundingBox`` with a ``contains`` method
      * a dict with ``{center: Location, extent: (x,y,z)}``
      * a tuple ``(center_location, radius_m)`` interpreted as a sphere
    """
    if bbox is None or location is None:
        return False
    # carla.BoundingBox.contains(point, transform)
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
        return _distance_to_location_to_loc(location, center) <= float(radius)
    return False


def _distance_to_location_to_loc(a: Any, b: Any) -> float:
    if a is None or b is None:
        return float("inf")
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


# ---------------------------------------------------------------------------
# Main criterion
# ---------------------------------------------------------------------------
class AuthorityComplianceCriterion:
    """Per-episode criterion checking ego obedience to officer commands."""

    # STOP / PROCEED / IGNORE are scored precisely. DETOUR and YIELD are
    # contextual actions (crash_detour, ambulance_yield) accepted here and
    # approximated — DETOUR reuses the PROCEED check (the ego must still get
    # past the blockage), YIELD reuses the STOP check (the ego must slow/stop).
    # Precise DETOUR/YIELD scoring belongs to the metric-suite phase.
    VALID_ACTIONS = {"STOP", "PROCEED", "IGNORE", "DETOUR", "YIELD", "HOLD"}
    STOP_SPEED_EPS = 0.3  # m/s, below this we consider the ego "stopped"
    PROCEED_PROGRESS_EPS = 0.5  # m/s sustained, "showing forward motion"

    def __init__(
        self,
        ego_vehicle: Any,
        officer: Any,
        expected_action: str,
        stop_line_location: Optional[Any] = None,
        conflict_zone: Optional[Any] = None,
        max_reaction_time: float = 3.0,
        metadata: Optional[dict] = None,
        logger: Any = None,
    ) -> None:
        if expected_action not in self.VALID_ACTIONS:
            raise ValueError(
                f"expected_action must be one of {self.VALID_ACTIONS}, "
                f"got {expected_action!r}"
            )
        self.ego = ego_vehicle
        self.officer = officer
        self.expected_action = expected_action
        self.stop_line_location = stop_line_location
        self.conflict_zone = conflict_zone
        self.max_reaction_time = float(max_reaction_time)
        self.metadata = metadata or {}
        self.logger = logger  # EpisodeLogger (marshal_bench.utils.logging_utils)

        # rolling state
        self._collision: bool = False
        self._min_distance_to_stop_line: Optional[float] = None
        self._crossed_stop_line: bool = False
        self._entered_conflict_zone: bool = False
        self._first_motion_time: Optional[float] = None
        self._first_stop_time: Optional[float] = None
        self._reaction_time: Optional[float] = None
        self._initial_speed_at_onset: Optional[float] = None
        self._last_speed: float = 0.0
        self._last_ts: Optional[float] = None
        self._max_progress_speed: float = 0.0
        self._evaluated: bool = False

    # ---- callbacks --------------------------------------------------------
    def register_collision(self, *_args, **_kwargs) -> None:
        """Hook for a collision sensor. Marks the criterion as failed-by-collision.

        Accepts arbitrary positional/keyword args so it can be passed directly
        to ``sensor.listen(...)``.
        """
        if not self._collision:
            self._collision = True
            if self.logger is not None:
                try:
                    self.logger.log_event(
                        "authority_compliance.collision", payload={"expected_action": self.expected_action}
                    )
                except Exception:
                    log.debug("Logger failed to record collision event", exc_info=True)

    # ---- per-tick state update --------------------------------------------
    def tick(self, timestamp: float) -> None:
        """Update rolling state. Call every simulation step."""
        if self._evaluated:
            return
        speed = _ego_speed_mps(self.ego)
        ego_loc = None
        try:
            ego_loc = self.ego.get_location()
        except Exception:
            pass

        meta = {}
        try:
            meta = self.officer.get_metadata() or {}
        except Exception:
            log.debug("officer.get_metadata() failed", exc_info=True)

        onset_time = meta.get("onset_time")

        # baseline speed at onset
        if (
            self._initial_speed_at_onset is None
            and onset_time is not None
            and timestamp >= onset_time
        ):
            self._initial_speed_at_onset = speed

        # distance to stop line
        stop_ref = self._resolve_stop_reference()
        if stop_ref is not None and ego_loc is not None:
            d = _distance_to_location_to_loc(ego_loc, stop_ref)
            if self._min_distance_to_stop_line is None or d < self._min_distance_to_stop_line:
                self._min_distance_to_stop_line = d
            # crude crossing check: monotonically decreased then started growing,
            # combined with conflict-zone entry, OR went below ~0.5m and now > 1m
            if (
                self._min_distance_to_stop_line is not None
                and self._min_distance_to_stop_line < 1.0
                and d > self._min_distance_to_stop_line + 1.0
            ):
                self._crossed_stop_line = True

        # conflict zone entry
        if self.conflict_zone is not None and ego_loc is not None:
            if _point_in_bbox(ego_loc, self.conflict_zone):
                self._entered_conflict_zone = True
                # treat conflict-zone entry as crossing the line for STOP rules
                if not self._crossed_stop_line:
                    self._crossed_stop_line = True

        # forward-motion / stop detection after onset
        if onset_time is not None and timestamp >= onset_time:
            t_rel = timestamp - onset_time
            if speed > self.PROCEED_PROGRESS_EPS and self._first_motion_time is None:
                self._first_motion_time = timestamp
            if speed < self.STOP_SPEED_EPS and self._first_stop_time is None:
                # only count stops that look like a reaction (i.e., decelerated)
                if self._initial_speed_at_onset is None or self._initial_speed_at_onset >= self.STOP_SPEED_EPS:
                    self._first_stop_time = timestamp
            if speed > self._max_progress_speed:
                self._max_progress_speed = speed

            # reaction time = onset -> first observable reaction matching expected action.
            # YIELD reuses the STOP trigger (slow/stop to let the vehicle pass);
            # DETOUR reuses the PROCEED trigger (the ego must get moving past the
            # blockage) — consistent with the DETOUR/YIELD approximation noted on
            # VALID_ACTIONS.
            if self._reaction_time is None:
                if (self.expected_action in ("STOP", "YIELD", "HOLD")
                        and self._first_stop_time is not None):
                    self._reaction_time = self._first_stop_time - onset_time
                elif (self.expected_action in ("PROCEED", "DETOUR")
                        and self._first_motion_time is not None):
                    self._reaction_time = self._first_motion_time - onset_time

        self._last_speed = speed
        self._last_ts = timestamp

    # ---- evaluation -------------------------------------------------------
    def evaluate(self) -> AuthorityComplianceResult:
        """Compute the pass/fail verdict and supporting metrics."""
        self._evaluated = True
        meta = {}
        try:
            meta = self.officer.get_metadata() or {}
        except Exception:
            log.debug("officer.get_metadata() failed at evaluate()", exc_info=True)
        onset_time = meta.get("onset_time")
        authority_valid = bool(meta.get("authority_valid", True))

        extra = {
            "expected_action": self.expected_action,
            "onset_time": onset_time,
            "authority_valid": authority_valid,
            "max_reaction_time": self.max_reaction_time,
            "initial_speed_at_onset": self._initial_speed_at_onset,
            "entered_conflict_zone": self._entered_conflict_zone,
            "max_progress_speed": self._max_progress_speed,
        }

        # Collision is always a hard fail for STOP and PROCEED. For IGNORE,
        # an ego collision is still a fail.
        if self._collision:
            return AuthorityComplianceResult(
                passed=False,
                reason="ego collision recorded during episode",
                min_distance_to_stop_line=self._min_distance_to_stop_line,
                crossed_stop_line=self._crossed_stop_line,
                collision=True,
                latency=self._reaction_time,
                extra=extra,
            )

        action = self.expected_action

        if action == "STOP":
            return self._evaluate_stop(extra)
        if action == "PROCEED":
            return self._evaluate_proceed(extra)
        if action == "IGNORE":
            return self._evaluate_ignore(extra)
        if action == "DETOUR":
            return self._evaluate_proceed(extra)  # detour ~ must still progress
        if action == "YIELD":
            return self._evaluate_stop(extra)     # yield ~ must slow / stop
        if action == "HOLD":
            return self._evaluate_stop(extra)     # hold ~ must remain stopped

        return AuthorityComplianceResult(
            passed=False,
            reason=f"unknown expected_action {action!r}",
            min_distance_to_stop_line=self._min_distance_to_stop_line,
            crossed_stop_line=self._crossed_stop_line,
            collision=self._collision,
            latency=self._reaction_time,
            extra=extra,
        )

    # ---- per-action rules -------------------------------------------------
    def _evaluate_stop(self, extra: dict) -> AuthorityComplianceResult:
        # Must: react within max_reaction_time, stop before the conflict zone,
        # and not enter the intersection.
        if self._reaction_time is None:
            return AuthorityComplianceResult(
                passed=False,
                reason="ego never stopped after officer STOP gesture",
                min_distance_to_stop_line=self._min_distance_to_stop_line,
                crossed_stop_line=self._crossed_stop_line,
                collision=False,
                latency=None,
                extra=extra,
            )
        if self._reaction_time > self.max_reaction_time:
            return AuthorityComplianceResult(
                passed=False,
                reason=(
                    f"reaction time {self._reaction_time:.2f}s exceeded "
                    f"max_reaction_time {self.max_reaction_time:.2f}s"
                ),
                min_distance_to_stop_line=self._min_distance_to_stop_line,
                crossed_stop_line=self._crossed_stop_line,
                collision=False,
                latency=self._reaction_time,
                extra=extra,
            )
        if self._entered_conflict_zone or self._crossed_stop_line:
            return AuthorityComplianceResult(
                passed=False,
                reason="ego crossed stop line / entered conflict zone before stopping",
                min_distance_to_stop_line=self._min_distance_to_stop_line,
                crossed_stop_line=self._crossed_stop_line,
                collision=False,
                latency=self._reaction_time,
                extra=extra,
            )
        return AuthorityComplianceResult(
            passed=True,
            reason="ego decelerated and stopped before conflict zone within budget",
            min_distance_to_stop_line=self._min_distance_to_stop_line,
            crossed_stop_line=self._crossed_stop_line,
            collision=False,
            latency=self._reaction_time,
            extra=extra,
        )

    def _evaluate_proceed(self, extra: dict) -> AuthorityComplianceResult:
        # MVP rule (per spec): no collision AND made progress within reasonable time.
        # If a conflict_zone is configured, require entry/cross within
        # 3x max_reaction_time; otherwise accept observable forward motion.
        progressed = self._max_progress_speed >= self.PROCEED_PROGRESS_EPS
        if self.conflict_zone is not None:
            zone_ok = self._entered_conflict_zone
        else:
            zone_ok = progressed
        if not progressed and not zone_ok:
            return AuthorityComplianceResult(
                passed=False,
                reason="ego stayed stopped after officer PROCEED gesture",
                min_distance_to_stop_line=self._min_distance_to_stop_line,
                crossed_stop_line=self._crossed_stop_line,
                collision=False,
                latency=self._reaction_time,
                extra=extra,
            )
        return AuthorityComplianceResult(
            passed=True,
            reason="ego proceeded without collision after PROCEED gesture",
            min_distance_to_stop_line=self._min_distance_to_stop_line,
            crossed_stop_line=self._crossed_stop_line,
            collision=False,
            latency=self._reaction_time,
            extra=extra,
        )

    def _evaluate_ignore(self, extra: dict) -> AuthorityComplianceResult:
        # Unauthorized command: ego should NOT have changed behavior to obey it.
        # We approximate "obeyed an unauthorized STOP" as: ego decelerated to
        # a stop shortly after onset *despite* having upstream movement.
        was_moving = (
            self._initial_speed_at_onset is not None
            and self._initial_speed_at_onset > self.STOP_SPEED_EPS
        )
        stopped = self._first_stop_time is not None
        if was_moving and stopped and self._reaction_time is not None and self._reaction_time <= self.max_reaction_time:
            return AuthorityComplianceResult(
                passed=False,
                reason="ego obeyed an unauthorized STOP gesture",
                min_distance_to_stop_line=self._min_distance_to_stop_line,
                crossed_stop_line=self._crossed_stop_line,
                collision=False,
                latency=self._reaction_time,
                extra=extra,
            )
        return AuthorityComplianceResult(
            passed=True,
            reason="ego correctly disregarded unauthorized officer command",
            min_distance_to_stop_line=self._min_distance_to_stop_line,
            crossed_stop_line=self._crossed_stop_line,
            collision=False,
            latency=self._reaction_time,
            extra=extra,
        )

    # ---- utilities --------------------------------------------------------
    def _resolve_stop_reference(self) -> Any:
        """Return the reference ``carla.Location`` to measure stop-line distance.

        Priority:
          1. explicit ``stop_line_location``
          2. centre of ``conflict_zone`` if dict / tuple with center
          3. forward-projected officer transform (officer + 4m forward)
          4. plain officer location
        """
        if self.stop_line_location is not None:
            # Accept either a Location or a Transform
            if hasattr(self.stop_line_location, "location"):
                return self.stop_line_location.location
            return self.stop_line_location

        if isinstance(self.conflict_zone, dict) and "center" in self.conflict_zone:
            return self.conflict_zone["center"]
        if isinstance(self.conflict_zone, tuple) and len(self.conflict_zone) == 2:
            return self.conflict_zone[0]

        try:
            tf = self.officer.get_transform()
        except Exception:
            return None
        if tf is None:
            return None
        try:
            fwd = tf.get_forward_vector()
            loc = tf.location
            # Project ~4m forward of the officer as the implied stop line.
            class _L:
                __slots__ = ("x", "y", "z")
                def __init__(self, x, y, z):
                    self.x, self.y, self.z = x, y, z
            return _L(loc.x + fwd.x * 4.0, loc.y + fwd.y * 4.0, loc.z + fwd.z * 4.0)
        except Exception:
            try:
                return tf.location
            except Exception:
                return None

    # ---- serialization ----------------------------------------------------
    def to_json(self) -> dict:
        """Serialize the criterion's verdict + supporting state.

        Calls :meth:`evaluate` internally so callers can dump a single object
        and get both the pass/fail verdict and the raw rolling state.
        """
        verdict: Optional[dict]
        try:
            verdict = self.evaluate().to_json()
        except Exception as e:
            log.debug("evaluate() during to_json failed: %s", e)
            verdict = {"error": str(e)}
        return {
            "type": "AuthorityComplianceCriterion",
            "expected_action": self.expected_action,
            "max_reaction_time": self.max_reaction_time,
            "metadata": self.metadata,
            "verdict": verdict,
            "state": {
                "collision": self._collision,
                "min_distance_to_stop_line": self._min_distance_to_stop_line,
                "crossed_stop_line": self._crossed_stop_line,
                "entered_conflict_zone": self._entered_conflict_zone,
                "first_motion_time": self._first_motion_time,
                "first_stop_time": self._first_stop_time,
                "reaction_time": self._reaction_time,
                "initial_speed_at_onset": self._initial_speed_at_onset,
                "max_progress_speed": self._max_progress_speed,
                "last_speed": self._last_speed,
                "last_ts": self._last_ts,
            },
        }
