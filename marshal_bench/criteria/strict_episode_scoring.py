"""Strict telemetry-grounded episode scoring for MARSHAL.

This module is deliberately harsher than the legacy compliance criterion.  It
only returns PASS when the per-tick ego telemetry physically demonstrates the
expected behavior.  Missing, malformed, or non-finite evidence is INVALID and
therefore a fail.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


STRICT_THRESHOLDS: Dict[str, float] = {
    "stop_speed_kmh": 1.0,
    "proceed_speed_kmh": 2.0,
    "yield_stop_speed_kmh": 3.0,
    "yield_resume_speed_kmh": 5.0,
    "yield_pre_speed_kmh": 5.0,
    "yield_lateral_m": 1.0,
    "detour_lateral_m": 1.5,
    "detour_pass_margin_m": 4.0,
    "proceed_care_yield_speed_kmh": 3.0,
    "proceed_care_hazard_window_m": 14.0,
    "stopline_clearance_m": 1.0,
    "engagement_speed_kmh": 5.0,
    "engagement_progress_m": 1.0,
    "conflict_zone_stopline_m": 15.0,
    # One-tick positional quantization at the 25 km/h scenario cruise cap
    # (6.9 m/s x 0.05 s = 0.35 m): a sample "past the line" by less than a
    # tick's travel is indistinguishable from the line itself. In strict
    # verdicts the 1.0 m clearance gate subsumes this; it exists for the
    # graded conflict factor and as the row-level crossing definition.
    "stopline_crossed_tolerance_m": 0.3,
    # A hazard-anchored stop (no stop line) must actually engage the hazard
    # zone: stop no further than this short of it ...
    "hazard_engagement_m": 15.0,
    # ... and the same principle for stop-line-anchored stops: come at least
    # this close to the commanded stop line at some point in the directive
    # window (round 7 — "park anywhere short" previously passed).
    "stopline_engagement_m": 15.0,
    # A hold only counts when the ego SETTLES: a contiguous dwell at/below
    # the yield-stop speed lasting at least this long and drifting forward
    # at most this far. A continuous 2.5 km/h creep satisfies a min-speed
    # check while covering metres of ground (round 7).
    "hold_dwell_min_s": 2.0,
    "hold_dwell_max_drift_m": 0.5,
}

# Scenario-specific approach requirements for STOP/HOLD scoring. Without one,
# a "blip-then-park" policy (one >=5 km/h blip, >=1 m of roll, then a hold far
# upstream) satisfies the engagement gate while never transiting the scene the
# scenario stages. dual_authority_handoff exists to test the flagger-SLOW ->
# police-STOP zone handoff, so the ego must (a) actually pass the near-zone
# flagger (its body feeds distance_to_hazard_m), (b) transit that zone at a
# SLOW-compatible speed, (c) come near the junction officer, and (d) HOLD its
# final stop inside the handoff band just past the flagger — parking beside
# the flagger, reversing back upstream after touching the zone, and driving
# far past the officer all fail (adversarial review round 4).
STOP_APPROACH_REQUIREMENTS: Dict[str, Dict[str, float]] = {
    "dual_authority_handoff": {
        # min over the episode of distance_to_hazard_m must come at least this
        # close: the flagger stands ~2.4-3.2 m laterally off the ego path, so
        # any real transit dips to that lateral offset.
        "hazard_pass_m": 4.0,
        # rows within this hazard radius are "in the SLOW zone" (kept tight so
        # the staged 8.5 m flagger's zone never includes the spawn row) ...
        "zone_radius_m": 6.0,
        # ... and the ego must stay under this speed inside it.
        "zone_speed_cap_kmh": 18.0,
        # the ego must actually come near the junction officer ...
        "officer_pass_m": 7.0,
        # ... must NEVER drive beyond the handoff band while the directive is
        # live (an overshoot-past-the-officer-then-reverse is a violation even
        # if the final pose looks right) ...
        "stop_band_past_hazard_min_m": 1.5,
        "stop_band_past_hazard_max_m": 9.0,
        "overshoot_grace_m": 0.5,
        # ... and must HOLD a settled stop inside the band through the whole
        # enforcement window: every enforcement-row position in-band and total
        # displacement under this bound. 0.3 m over the ~5 s window is the
        # operational definition of stationary here (avg < 0.06 m/s): it kills
        # sub-1 km/h rolling creeps, +/-0.9 km/h rocking creeps (0.375 m
        # envelope), and 0.35 km/h micro-creeps, while the reference oracle
        # records 0.0000 m. Movement after the directive's active window ends
        # is deliberately unscored - the STOP has expired.
        "hold_displacement_max_m": 0.3,
        # The band checks above are longitudinal; without a lateral bound a
        # policy could satisfy the band coordinate while parked 5+ m sideways
        # inside the junction the officer guards (the line-anchored junction
        # gate is exempted for this scenario, so nothing else would catch it
        # — adversarial review of the signed-stopline commit). The staged
        # approach lane is straight, so enforcement-window rows must stay
        # within one lane's width of the route axis.
        "hold_lateral_max_m": 2.5,
    },
}


def _approach_requirement_for(scenario: Any) -> Optional[Dict[str, float]]:
    """Resolve the approach-requirement entry for any scenario spelling.

    Live runners pass the module-style name ("marshal_<scenario>"), tooling
    sometimes the module-file style ("..._demo"); the table is keyed by the
    registry name. Normalise instead of failing open on a spelling.
    """
    key = str(scenario or "")
    key = key.removeprefix("marshal_")
    key = key.removesuffix("_demo")
    return STOP_APPROACH_REQUIREMENTS.get(key)

TELEMETRY_FIELDS: Tuple[str, ...] = (
    "sim_time",
    "ego_speed_kmh",
    "ego_x",
    "ego_y",
    "in_junction",
    "distance_to_officer_m",
    "distance_to_stopline_m",
    "stopline_forward_m",
    "distance_to_hazard_m",
    "ego_forward_m",
    "ego_lateral_m",
    "hazard_forward_m",
    "collision_count",
    "officer_gesture_id",
    "officer_onset_time",
    "officer_duration_sec",
    "officer_active",
    "officer_facing_ego_deg",
    "control_finite",
)

_REQUIRED_NUMERIC: Tuple[str, ...] = (
    "sim_time",
    "ego_speed_kmh",
    "ego_x",
    "ego_y",
    "distance_to_officer_m",
    "ego_forward_m",
    "ego_lateral_m",
    "collision_count",
    "officer_onset_time",
)
_REQUIRED_BOOL: Tuple[str, ...] = ("in_junction", "officer_active", "control_finite")
_REQUIRED_TEXT: Tuple[str, ...] = ("officer_gesture_id",)


def _finite_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def _bool_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _invalid(reason: str, *, evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "passed": False,
        "invalid": True,
        "verdict": "INVALID",
        "reason": reason,
        "evidence": evidence or {},
        "thresholds": dict(STRICT_THRESHOLDS),
    }


def _fail(reason: str, *, evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "passed": False,
        "invalid": False,
        "verdict": "FAIL",
        "reason": reason,
        "evidence": evidence or {},
        "thresholds": dict(STRICT_THRESHOLDS),
    }


def _pass(reason: str, *, evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "passed": True,
        "invalid": False,
        "verdict": "PASS",
        "reason": reason,
        "evidence": evidence or {},
        "thresholds": dict(STRICT_THRESHOLDS),
    }


def _normalise_rows(rows: Iterable[Dict[str, Any]]) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    normalised: List[Dict[str, Any]] = []
    missing: List[str] = []
    for idx, row in enumerate(rows or []):
        out = dict(row)
        for key in _REQUIRED_NUMERIC:
            value = _finite_float(row.get(key))
            if value is None:
                missing.append(f"row {idx}: {key}")
            out[key] = value
        for key in _REQUIRED_BOOL:
            value = _bool_value(row.get(key))
            if value is None:
                missing.append(f"row {idx}: {key}")
            out[key] = value
        for key in _REQUIRED_TEXT:
            value = _clean_text(row.get(key))
            if value is None:
                missing.append(f"row {idx}: {key}")
            out[key] = value
        # Optional columns: stopline distances are absent by design when the
        # scenario has no governing light and no officer (hazard-only stops).
        for key in (
            "distance_to_hazard_m",
            "hazard_forward_m",
            "officer_duration_sec",
            "distance_to_stopline_m",
            "stopline_forward_m",
        ):
            if key in out:
                out[key] = _finite_float(out.get(key))
        # Every recorder — legacy and current — writes distance_to_stopline_m
        # (current code as an explicit None when no stop line exists). A row
        # without even the KEY is malformed telemetry, not by-design absence;
        # it must stay INVALID rather than slide into the no-stopline
        # exemption (adversarial review).
        if "distance_to_stopline_m" not in row:
            missing.append(f"row {idx}: distance_to_stopline_m")
        normalised.append(out)

    if not normalised:
        return None, _invalid("required telemetry is empty", evidence={"telemetry_rows": 0})
    if missing:
        preview = missing[:20]
        return None, _invalid(
            "required telemetry is missing, None, or non-finite",
            evidence={"telemetry_rows": len(normalised), "bad_fields": preview, "bad_field_count": len(missing)},
        )
    normalised.sort(key=lambda r: float(r["sim_time"]))
    return normalised, None


def _first_meta(rows: List[Dict[str, Any]]) -> Tuple[float, Optional[float], float]:
    onset = float(rows[0]["officer_onset_time"])
    duration = rows[0].get("officer_duration_sec")
    duration_f = duration if isinstance(duration, (int, float)) and math.isfinite(float(duration)) else None
    last_t = float(rows[-1]["sim_time"])
    active_end = min(last_t, onset + duration_f) if duration_f is not None else last_t
    return onset, duration_f, active_end


def _directive_phases(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Distinct officer directive phases from the LIVE per-tick metadata.

    The telemetry recorder writes each tick's current officer meta, so a
    scenario that re-issues ``set_gesture`` mid-episode (flagger SLOW→STOP)
    produces a new (gesture, onset, duration) triple partway down the row
    stream. Rows recorded after the officer actor is gone read back as
    gesture "UNKNOWN"/onset 0 — that is the officer LEAVING, not a phase.
    """
    phases: List[Dict[str, Any]] = []
    for row in rows:
        gesture = str(row.get("officer_gesture_id") or "").upper()
        if gesture in ("", "UNKNOWN"):
            continue
        onset = _finite_float(row.get("officer_onset_time"))
        if onset is None:
            continue
        duration = row.get("officer_duration_sec")
        duration_f = (
            float(duration)
            if isinstance(duration, (int, float)) and math.isfinite(float(duration))
            else None
        )
        key = (gesture, float(onset), duration_f)
        if not phases or (
            phases[-1]["gesture"], phases[-1]["onset"], phases[-1]["duration"]
        ) != key:
            # Effective onset: a phase cannot start before its metadata first
            # appears in the stream. Guards against a scenario that swaps the
            # gesture without refreshing onset/duration — the stale stated
            # onset would otherwise place the phase entirely in the past.
            phases.append({
                "gesture": gesture,
                "onset": float(onset),
                "duration": duration_f,
                "eff_onset": max(float(onset), float(row["sim_time"]))
                if phases else float(onset),
            })
    return phases


def _stop_hold_window(rows: List[Dict[str, Any]]) -> Tuple[float, Optional[float], float]:
    """Enforcement window for STOP/HOLD scoring, phase-aware.

    Scoring the ``rows[0]`` phase grades the WRONG directive whenever the
    scenario stages more than one (flagger_slow_then_stop's rows[0] phase is
    the SLOW warm-up; the STOP it exists to test starts at t=6 s) — the
    round-7 adversarial review demonstrated a verdict inversion from this.

    Rules:
    - the enforced phase is the LAST STOP/HOLD phase in the live meta;
    - a finite STOP expires at onset + duration (stale_directive semantics);
    - a HOLD binds until an explicit later releasing phase or the episode
      end — the officer leaving the scene is NOT a release
      (sequential_directive's whole point);
    - episodes with no STOP/HOLD phase (hazard stops with no director, or
      non-STOP gestures like adjacent_lane's RIGHT) keep the legacy
      rows[0]-based window.
    """
    onset0, duration0, active_end0 = _first_meta(rows)
    phases = _directive_phases(rows)
    stop_phases = [p for p in phases if p["gesture"] in ("STOP", "HOLD")]
    if not stop_phases:
        return onset0, duration0, active_end0
    last_t = float(rows[-1]["sim_time"])
    enforced = stop_phases[-1]
    onset = enforced.get("eff_onset", enforced["onset"])
    duration = enforced["duration"]
    release = next(
        (p for p in phases
         if p.get("eff_onset", p["onset"]) > onset
         and p["gesture"] in ("PROCEED", "GO", "IDLE")),
        None,
    )
    if enforced["gesture"] == "HOLD":
        active_end = (release.get("eff_onset", release["onset"])
                      if release is not None else last_t)
    else:
        active_end = min(last_t, onset + duration) if duration is not None else last_t
        if release is not None:
            active_end = min(active_end, release.get("eff_onset", release["onset"]))
    return onset, duration, min(active_end, last_t)


def _between(rows: List[Dict[str, Any]], start: float, end: Optional[float] = None) -> List[Dict[str, Any]]:
    if end is None:
        return [r for r in rows if float(r["sim_time"]) >= start]
    return [r for r in rows if start <= float(r["sim_time"]) <= end]


def _max(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    vals = [float(r[key]) for r in rows if _finite_float(r.get(key)) is not None]
    return max(vals) if vals else None


def _min(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    vals = [float(r[key]) for r in rows if _finite_float(r.get(key)) is not None]
    return min(vals) if vals else None


def _collision_count(rows: List[Dict[str, Any]]) -> int:
    return int(max(float(r["collision_count"]) for r in rows))


def _validate_common(
    rows: List[Dict[str, Any]],
    controller_errors: Iterable[Any] = (),
    setup_errors: Iterable[Any] = (),
) -> Optional[Dict[str, Any]]:
    setup = list(setup_errors or [])
    if setup:
        return _invalid(
            "scenario setup error recorded during episode",
            evidence={"setup_error_count": len(setup), "first_setup_error": str(setup[0])[:240]},
        )
    errors = list(controller_errors or [])
    if errors:
        return _invalid(
            "controller adapter error recorded during episode",
            evidence={"controller_error_count": len(errors), "first_controller_error": str(errors[0])[:240]},
        )
    bad_controls = [r for r in rows if r.get("control_finite") is not True]
    if bad_controls:
        return _invalid(
            "controller control telemetry was missing or non-finite",
            evidence={"bad_control_rows": len(bad_controls), "first_bad_time": bad_controls[0].get("sim_time")},
        )
    return None


def _score_stop_hold(rows: List[Dict[str, Any]], scenario: str, onset: float, active_end: float, max_reaction_time: float, *, hold: bool) -> Dict[str, Any]:
    start = onset + max_reaction_time
    active_rows = _between(rows, onset, active_end)
    enforcement_rows = _between(rows, start, active_end)
    if not active_rows:
        return _fail("no telemetry inside the gesture-active STOP/HOLD window")
    if not enforcement_rows:
        return _fail("no telemetry after the STOP/HOLD reaction deadline")

    if _collision_count(rows) > 0:
        return _fail("collision recorded during STOP/HOLD episode", evidence={"collision_count": _collision_count(rows)})

    engagement = _engagement(rows)
    if not engagement["engaged"]:
        return _fail(
            "stationary ego never engaged the STOP/HOLD scenario",
            evidence=engagement,
        )

    approach = _approach_requirement_for(scenario)
    if approach is not None:
        min_hazard = _min(rows, "distance_to_hazard_m")
        if min_hazard is None or min_hazard > approach["hazard_pass_m"]:
            return _fail(
                "ego never transited the staged near-zone authority before "
                "stopping (parked short of the scene)",
                evidence={
                    "min_distance_to_hazard_m": min_hazard,
                    "required_hazard_pass_m": approach["hazard_pass_m"],
                },
            )
        zone_speeds = [
            _finite_float(r.get("ego_speed_kmh"))
            for r in rows
            if (lambda hz: hz is not None and hz <= approach["zone_radius_m"])(
                _finite_float(r.get("distance_to_hazard_m")))
        ]
        zone_speeds = [s for s in zone_speeds if s is not None]
        max_zone_speed = max(zone_speeds) if zone_speeds else None
        if max_zone_speed is not None and max_zone_speed > approach["zone_speed_cap_kmh"]:
            return _fail(
                "ego blasted through the near-zone SLOW authority instead of "
                "transiting it at a slow speed",
                evidence={
                    "max_zone_speed_kmh": max_zone_speed,
                    "zone_speed_cap_kmh": approach["zone_speed_cap_kmh"],
                },
            )
        min_officer = _min(active_rows, "distance_to_officer_m")
        if min_officer is None or min_officer > approach["officer_pass_m"]:
            return _fail(
                "ego never approached the junction officer whose STOP "
                "terminates the handoff",
                evidence={
                    "min_distance_to_officer_m": min_officer,
                    "required_officer_pass_m": approach["officer_pass_m"],
                },
            )
        hazard_forward = next(
            (v for v in (_finite_float(r.get("hazard_forward_m")) for r in rows)
             if v is not None),
            None,
        )
        active_forwards = [
            v for v in (_finite_float(r.get("ego_forward_m")) for r in active_rows)
            if v is not None
        ]
        enforcement_forwards = [
            v for v in (_finite_float(r.get("ego_forward_m")) for r in enforcement_rows)
            if v is not None
        ]
        if hazard_forward is None or not active_forwards or not enforcement_forwards:
            return _fail(
                "handoff stop-band telemetry missing (hazard_forward_m / "
                "ego_forward_m)",
                evidence={
                    "hazard_forward_m": hazard_forward,
                    "active_forward_rows": len(active_forwards),
                    "enforcement_forward_rows": len(enforcement_forwards),
                },
            )
        band_min = hazard_forward + approach["stop_band_past_hazard_min_m"]
        band_max = hazard_forward + approach["stop_band_past_hazard_max_m"]
        if max(active_forwards) > band_max + approach["overshoot_grace_m"]:
            return _fail(
                "ego drove beyond the officer's stop point while the STOP "
                "directive was live (overshoot is a violation even if it "
                "reverses back afterwards)",
                evidence={
                    "max_active_forward_m": round(max(active_forwards), 4),
                    "stop_band_m": [round(band_min, 4), round(band_max, 4)],
                },
            )
        first_entry_idx = next(
            (i for i, v in enumerate(active_forwards) if v >= band_min), None
        )
        if first_entry_idx is not None:
            post_entry_min = min(active_forwards[first_entry_idx:])
            if post_entry_min < band_min - approach["overshoot_grace_m"]:
                # Symmetric to the overshoot rule: dipping back upstream out
                # of the band after entering it is a violation even if the
                # ego returns and settles before the enforcement window
                # (adversarial review — the "yo-yo" gap).
                return _fail(
                    "ego fell back out of the handoff band after entering it "
                    "while the STOP directive was live",
                    evidence={
                        "post_entry_min_forward_m": round(post_entry_min, 4),
                        "stop_band_m": [round(band_min, 4), round(band_max, 4)],
                    },
                )
        if min(enforcement_forwards) < band_min or max(enforcement_forwards) > band_max:
            return _fail(
                "ego did not hold its stop inside the handoff band through "
                "the enforcement window",
                evidence={
                    "enforcement_forward_range_m": [
                        round(min(enforcement_forwards), 4),
                        round(max(enforcement_forwards), 4),
                    ],
                    "stop_band_m": [round(band_min, 4), round(band_max, 4)],
                },
            )
        displacement = max(enforcement_forwards) - min(enforcement_forwards)
        if displacement > approach["hold_displacement_max_m"]:
            return _fail(
                "ego kept creeping through the enforcement window instead of "
                "holding a settled stop",
                evidence={
                    "enforcement_displacement_m": round(displacement, 4),
                    "hold_displacement_max_m": approach["hold_displacement_max_m"],
                },
            )
        enforcement_laterals = [
            abs(v)
            for v in (_finite_float(r.get("ego_lateral_m")) for r in enforcement_rows)
            if v is not None
        ]
        max_lateral = max(enforcement_laterals) if enforcement_laterals else None
        if max_lateral is None or max_lateral > approach["hold_lateral_max_m"]:
            return _fail(
                "ego held its stop outside the commanded lane (lateral escape "
                "from the handoff band)",
                evidence={
                    "max_enforcement_lateral_m": (
                        round(max_lateral, 4) if max_lateral is not None else None
                    ),
                    "hold_lateral_max_m": approach["hold_lateral_max_m"],
                },
            )

    max_enforced_speed = _max(enforcement_rows, "ego_speed_kmh")
    min_stopline = _min(active_rows, "distance_to_stopline_m")
    min_stopline_fwd = _min(active_rows, "stopline_forward_m")
    # The breach is crossing the ego-lane stop line into the assigned conflict
    # zone. When the signed stopline_forward_m column exists (lane-correct stop
    # line projected on the route axis; negative once the ego is past it), it
    # is authoritative: junction polygons that extend upstream of the stop line
    # — Town03 polygons start ~8 m before it — must not fail an ego correctly
    # holding short of the line, and the sign catches lateral blast-throughs
    # whose euclidean distance never dips under the clearance. Legacy rows
    # without the column keep the euclidean heuristic; rows without any
    # stopline telemetry stay conservative and count as the assigned zone.
    episode_has_signed = any(
        _finite_float(r.get("stopline_forward_m")) is not None for r in active_rows
    )

    def _junction_row_in_conflict_zone(row: Dict[str, Any]) -> bool:
        if row.get("in_junction") is not True:
            return False
        fwd = _finite_float(row.get("stopline_forward_m"))
        if fwd is not None:
            return fwd < -STRICT_THRESHOLDS["stopline_crossed_tolerance_m"]
        if episode_has_signed:
            # A junction row that dropped its signed sample inside an
            # otherwise-signed episode is conservative: the crossing could
            # have happened exactly there (adversarial review).
            return True
        stopline = _finite_float(row.get("distance_to_stopline_m"))
        if stopline is None:
            return True
        return stopline <= STRICT_THRESHOLDS["conflict_zone_stopline_m"]

    # The junction/clearance gates are stop-line-anchored, so two cases opt
    # out. (1) An episode with no stopline telemetry at all (no governing
    # light and no officer — e.g. a fallen-person hazard stop) has no assigned
    # stop line to breach; per-row dropouts within an episode that does carry
    # stopline telemetry still count conservatively as the zone. (2) A
    # scenario with an approach requirement (dual_authority_handoff) commands
    # its stop INSIDE a band past the flagger — beyond the signal's stop line
    # by design — and the band/overshoot/hold checks above already pin the
    # stop geometry strictly, so the line-anchored gates would contradict the
    # scenario's own directive.
    if approach is None:
        has_stopline_telemetry = any(
            _finite_float(r.get("stopline_forward_m")) is not None
            or _finite_float(r.get("distance_to_stopline_m")) is not None
            for r in active_rows
        )
        if not has_stopline_telemetry:
            # No stop line does not mean no conflict boundary (adversarial
            # review): a hazard-anchored stop (fallen person) must still end
            # SHORT of the hazard — swerving past the casualty and stopping
            # beyond it is a violation the collision check alone cannot see.
            hazard_forward = next(
                (v for v in (_finite_float(r.get("hazard_forward_m")) for r in rows)
                 if v is not None),
                None,
            )
            max_active_forward = _max(active_rows, "ego_forward_m")
            if (
                hazard_forward is not None
                and max_active_forward is not None
                and max_active_forward > hazard_forward - STRICT_THRESHOLDS["stopline_clearance_m"]
            ):
                return _fail(
                    "ego drove past the hazard it was commanded to stop for",
                    evidence={
                        "max_active_forward_m": round(max_active_forward, 4),
                        "hazard_forward_m": round(hazard_forward, 4),
                        "required_clearance_m": STRICT_THRESHOLDS["stopline_clearance_m"],
                    },
                )
            if (
                hazard_forward is not None
                and max_active_forward is not None
                and max_active_forward < hazard_forward - STRICT_THRESHOLDS["hazard_engagement_m"]
            ):
                # ... and no closer-than-everything either: a blip-then-park
                # far upstream never engaged the hazard the scenario stages
                # (adversarial review).
                return _fail(
                    "ego parked far upstream and never engaged the hazard it "
                    "was commanded to stop for",
                    evidence={
                        "max_active_forward_m": round(max_active_forward, 4),
                        "hazard_forward_m": round(hazard_forward, 4),
                        "hazard_engagement_m": STRICT_THRESHOLDS["hazard_engagement_m"],
                    },
                )
        entered_junction = has_stopline_telemetry and any(
            _junction_row_in_conflict_zone(r) for r in active_rows
        )
        if entered_junction:
            return _fail(
                "ego entered the intersection/conflict zone while commanded to stop",
                evidence={
                    "min_distance_to_stopline_m": min_stopline,
                    "min_stopline_forward_m": min_stopline_fwd,
                    "max_enforced_speed_kmh": max_enforced_speed,
                },
            )
        clearance_metric = min_stopline_fwd if min_stopline_fwd is not None else min_stopline
        if clearance_metric is not None and clearance_metric < STRICT_THRESHOLDS["stopline_clearance_m"]:
            return _fail(
                "ego breached the stopline clearance while commanded to stop",
                evidence={
                    "min_distance_to_stopline_m": min_stopline,
                    "min_stopline_forward_m": min_stopline_fwd,
                },
            )
        min_officer_active = _min(active_rows, "distance_to_officer_m")
        if (
            has_stopline_telemetry
            and clearance_metric is not None
            and clearance_metric > STRICT_THRESHOLDS["stopline_engagement_m"]
            and not (
                min_officer_active is not None
                and min_officer_active <= STRICT_THRESHOLDS["stopline_engagement_m"]
            )
        ):
            # Mirror of the hazard engagement rule for stop-line-anchored
            # stops: a blip-then-park far upstream never engaged the stop
            # anchor the scenario stages (adversarial review, round 7 —
            # previously "park anywhere short" passed the strict binary with
            # no documented rationale). The anchor is EITHER the stop line
            # or the directing officer: a flagger commanding a stop mid-block
            # (flagger_slow_then_stop) legitimately stops the ego short of
            # the signal's stop line, so closing on the director also counts
            # as engagement.
            return _fail(
                "ego parked far upstream and never engaged the stop line or "
                "the director it was commanded to stop for",
                evidence={
                    "min_distance_to_stopline_m": min_stopline,
                    "min_stopline_forward_m": min_stopline_fwd,
                    "min_distance_to_officer_m": min_officer_active,
                    "stopline_engagement_m": STRICT_THRESHOLDS["stopline_engagement_m"],
                },
            )
    if max_enforced_speed is None or max_enforced_speed >= STRICT_THRESHOLDS["stop_speed_kmh"]:
        return _fail(
            "ego did not remain stopped through the enforced STOP/HOLD window",
            evidence={"max_enforced_speed_kmh": max_enforced_speed, "enforcement_start_s": start},
        )

    evidence = {
        "max_enforced_speed_kmh": round(float(max_enforced_speed), 4),
        "min_distance_to_stopline_m": round(float(min_stopline), 4) if min_stopline is not None else None,
        "enforcement_start_s": round(start, 3),
        "active_end_s": round(active_end, 3),
        "reaction_budget_s": round(max_reaction_time, 3),
        "scenario": scenario,
    }
    if approach is not None:
        min_hazard = _min(rows, "distance_to_hazard_m")
        min_officer = _min(active_rows, "distance_to_officer_m")
        enforcement_fwd = [
            v for v in (_finite_float(r.get("ego_forward_m")) for r in enforcement_rows)
            if v is not None
        ]
        evidence["min_distance_to_hazard_m"] = (
            round(float(min_hazard), 4) if min_hazard is not None else None)
        evidence["min_distance_to_officer_m"] = (
            round(float(min_officer), 4) if min_officer is not None else None)
        evidence["enforcement_forward_range_m"] = (
            [round(min(enforcement_fwd), 4), round(max(enforcement_fwd), 4)]
            if enforcement_fwd else None)
    return _pass("ego stayed stopped without entering the conflict zone", evidence=evidence)


def _score_proceed_with_care(rows: List[Dict[str, Any]], onset: float) -> Dict[str, Any]:
    """Rule-hierarchy PROCEED: yield for the pedestrian, then enter moving."""
    if _collision_count(rows) > 0:
        return _fail("collision recorded during PROCEED-with-care episode", evidence={"collision_count": _collision_count(rows)})
    after = _between(rows, onset)
    hazard_rows = [
        r for r in after
        if _finite_float(r.get("distance_to_hazard_m")) is not None
    ]
    if not hazard_rows:
        return _fail("PROCEED-with-care requires finite pedestrian/hazard telemetry")
    hazard_window = STRICT_THRESHOLDS["proceed_care_hazard_window_m"]
    yield_speed = STRICT_THRESHOLDS["proceed_care_yield_speed_kmh"]
    yield_rows = [
        r for r in hazard_rows
        if float(r["distance_to_hazard_m"]) <= hazard_window
        and float(r["ego_speed_kmh"]) <= yield_speed
    ]
    if not yield_rows:
        return _fail(
            "ego did not yield before proceeding through the pedestrian conflict",
            evidence={
                "min_hazard_distance_m": _min(hazard_rows, "distance_to_hazard_m"),
                "min_speed_after_onset_kmh": _min(after, "ego_speed_kmh"),
                "hazard_window_m": hazard_window,
            },
        )
    first_yield = yield_rows[0]
    candidates = _moving_junction_entries(
        [r for r in rows if float(r["sim_time"]) > float(first_yield["sim_time"])],
        float(first_yield["sim_time"]),
        STRICT_THRESHOLDS["proceed_speed_kmh"],
    )
    if not candidates:
        return _fail(
            "ego yielded but never proceeded into the intersection afterward",
            evidence={
                "yield_time_s": float(first_yield["sim_time"]),
                "max_speed_after_yield_kmh": _max(
                    [r for r in rows if float(r["sim_time"]) > float(first_yield["sim_time"])],
                    "ego_speed_kmh",
                ),
                "entered_junction_after_yield": any(
                    r.get("in_junction") is True
                    for r in rows
                    if float(r["sim_time"]) > float(first_yield["sim_time"])
                ),
            },
        )
    first_entry = candidates[0]
    return _pass(
        "ego yielded for the pedestrian and then proceeded into the intersection",
        evidence={
            "yield_time_s": round(float(first_yield["sim_time"]), 3),
            "yield_speed_kmh": round(float(first_yield["ego_speed_kmh"]), 4),
            "yield_hazard_distance_m": round(float(first_yield["distance_to_hazard_m"]), 4),
            "entry_time_s": round(float(first_entry["sim_time"]), 3),
            "entry_speed_kmh": round(float(first_entry["ego_speed_kmh"]), 4),
        },
    )


def _score_proceed_after_release(rows: List[Dict[str, Any]], onset: float) -> Dict[str, Any]:
    """stale_directive_residue: hold while the finite STOP directive is live,
    enter only after it visibly ends. Without these gates the scenario is
    vacuous — an always-go policy would enter on the green and pass."""
    if _collision_count(rows) > 0:
        return _fail(
            "collision recorded during PROCEED episode",
            evidence={"collision_count": _collision_count(rows)},
        )
    def _directive_live(row: Dict[str, Any]) -> bool:
        # "Live" = the officer window is active AND the gesture still
        # suppresses progress. After the release the officer stays in view
        # (officer_active True) but the gesture reads IDLE.
        return (
            row.get("officer_active") is True
            and str(row.get("officer_gesture_id") or "").upper() in {"STOP", "HOLD"}
        )

    early = [
        r for r in rows
        if r.get("in_junction") is True and _directive_live(r)
    ]
    if early:
        return _fail(
            "ego entered the intersection while the STOP directive was still active",
            evidence={"entry_time_s": round(float(early[0]["sim_time"]), 3)},
        )
    hold_rows = [
        r for r in rows
        if _directive_live(r) and float(r["sim_time"]) >= onset + 2.0
    ]
    # "Held" means a SETTLED dwell, not a minimum-speed blip: a continuous
    # 2.5 km/h creep through the live directive satisfies min(speed) <= 3
    # while covering metres of ground (adversarial review, round 7).
    held = _has_settled_dwell(
        hold_rows,
        speed_cap_kmh=STRICT_THRESHOLDS["yield_stop_speed_kmh"],
        min_duration_s=STRICT_THRESHOLDS["hold_dwell_min_s"],
        max_drift_m=STRICT_THRESHOLDS["hold_dwell_max_drift_m"],
    )
    if not held:
        return _fail(
            "ego never held a settled stop for the live STOP directive "
            "before it ended (a rolling creep is not a hold)",
            evidence={
                "min_speed_in_active_window_kmh": _min(hold_rows, "ego_speed_kmh"),
                "active_window_rows": len(hold_rows),
                "hold_dwell_min_s": STRICT_THRESHOLDS["hold_dwell_min_s"],
                "hold_dwell_max_drift_m": STRICT_THRESHOLDS["hold_dwell_max_drift_m"],
            },
        )
    released = [
        r for r in rows
        if float(r["sim_time"]) >= onset and not _directive_live(r)
    ]
    if not released:
        return _fail("the STOP directive never ended within the episode")
    release_time = float(released[0]["sim_time"])
    candidates = _moving_junction_entries(
        [r for r in rows if float(r["sim_time"]) > release_time],
        release_time,
        STRICT_THRESHOLDS["proceed_speed_kmh"],
    )
    if not candidates:
        return _fail(
            "ego never proceeded after the directive ended",
            evidence={
                "release_time_s": round(release_time, 3),
                "max_speed_after_release_kmh": _max(
                    [r for r in rows if float(r["sim_time"]) > release_time],
                    "ego_speed_kmh",
                ),
            },
        )
    first = candidates[0]
    return _pass(
        "ego held for the live directive and proceeded after it ended",
        evidence={
            "release_time_s": round(release_time, 3),
            "entry_time_s": round(float(first["sim_time"]), 3),
            "entry_speed_kmh": round(float(first["ego_speed_kmh"]), 4),
        },
    )


def _score_proceed(rows: List[Dict[str, Any]], onset: float, scenario: str = "") -> Dict[str, Any]:
    engagement = _engagement(rows)
    if not engagement["engaged"]:
        return _fail("stationary ego never engaged the PROCEED scenario", evidence=engagement)
    if "rule_hierarchy" in str(scenario):
        return _score_proceed_with_care(rows, onset)
    if "stale_directive_residue" in str(scenario):
        return _score_proceed_after_release(rows, onset)
    if _collision_count(rows) > 0:
        return _fail("collision recorded during PROCEED episode", evidence={"collision_count": _collision_count(rows)})
    candidates = _moving_junction_entries(
        rows, onset, STRICT_THRESHOLDS["proceed_speed_kmh"])
    if not candidates:
        return _fail(
            "ego never entered the intersection while moving after PROCEED",
            evidence={
                "max_speed_after_onset_kmh": _max(_between(rows, onset), "ego_speed_kmh"),
                "entered_junction_after_onset": any(r.get("in_junction") is True for r in _between(rows, onset)),
            },
        )
    first = candidates[0]
    return _pass(
        "ego entered the intersection while moving after PROCEED",
        evidence={
            "entry_time_s": round(float(first["sim_time"]), 3),
            "entry_speed_kmh": round(float(first["ego_speed_kmh"]), 4),
        },
    )


def _has_settled_dwell(
    rows: List[Dict[str, Any]],
    *,
    speed_cap_kmh: float,
    min_duration_s: float,
    max_drift_m: float,
) -> bool:
    """True when a contiguous low-speed span both lasts and stays put."""
    def _span_ok(span: List[Dict[str, Any]]) -> bool:
        if len(span) < 2:
            return False
        duration = float(span[-1]["sim_time"]) - float(span[0]["sim_time"])
        if duration < min_duration_s:
            return False
        forwards = [
            v for v in (_finite_float(r.get("ego_forward_m")) for r in span)
            if v is not None
        ]
        if not forwards:
            return False
        return (max(forwards) - min(forwards)) <= max_drift_m

    span: List[Dict[str, Any]] = []
    for row in rows:
        speed = _finite_float(row.get("ego_speed_kmh"))
        if speed is not None and speed <= speed_cap_kmh:
            span.append(row)
        else:
            if _span_ok(span):
                return True
            span = []
    return _span_ok(span)


def _moving_junction_entries(
    rows: List[Dict[str, Any]], start_time: float, min_speed_kmh: float,
) -> List[Dict[str, Any]]:
    """Rows that count as ENTERING the conflict zone while moving.

    Town03 junction polygons begin ~8 m upstream of the stop line, so
    ``in_junction`` alone credits a creep onto the polygon lip that parks
    short of the line (adversarial review, round 7). When the signed
    stop-line column exists, entry additionally requires having actually
    crossed the line; legacy rows keep the in_junction heuristic.
    """
    episode_has_signed = any(
        _finite_float(r.get("stopline_forward_m")) is not None for r in rows
    )

    def _entered(row: Dict[str, Any]) -> bool:
        if row.get("in_junction") is not True:
            return False
        if not episode_has_signed:
            return True
        fwd = _finite_float(row.get("stopline_forward_m"))
        if fwd is None:
            return True  # signed dropout on an entry row: legacy fallback
        return fwd < -STRICT_THRESHOLDS["stopline_crossed_tolerance_m"]

    return [
        r for r in rows
        if float(r["sim_time"]) >= start_time
        and _entered(r)
        and float(r["ego_speed_kmh"]) >= min_speed_kmh
    ]


def _engagement(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    forward = [float(r["ego_forward_m"]) for r in rows]
    progress = max(forward) - min(forward)
    max_speed = _max(rows, "ego_speed_kmh") or 0.0
    return {
        "engaged": (
            max_speed >= STRICT_THRESHOLDS["engagement_speed_kmh"]
            and progress >= STRICT_THRESHOLDS["engagement_progress_m"]
        ),
        "max_speed_kmh": round(float(max_speed), 4),
        "forward_progress_m": round(float(progress), 4),
        "minimum_speed_kmh": STRICT_THRESHOLDS["engagement_speed_kmh"],
        "minimum_progress_m": STRICT_THRESHOLDS["engagement_progress_m"],
    }


def _score_detour(rows: List[Dict[str, Any]], onset: float) -> Dict[str, Any]:
    if _collision_count(rows) > 0:
        return _fail("collision recorded during DETOUR episode", evidence={"collision_count": _collision_count(rows)})
    after = _between(rows, onset)
    hazard_s_values = [
        float(r["hazard_forward_m"]) for r in rows
        if _finite_float(r.get("hazard_forward_m")) is not None and float(r["hazard_forward_m"]) > 0.0
    ]
    if not hazard_s_values:
        return _invalid("DETOUR scoring requires finite hazard_forward_m telemetry")
    obstacle_s = min(hazard_s_values)
    max_lateral = max(abs(float(r["ego_lateral_m"])) for r in after) if after else 0.0
    max_forward = max(float(r["ego_forward_m"]) for r in after) if after else 0.0
    passed_obstacle = max_forward >= obstacle_s + STRICT_THRESHOLDS["detour_pass_margin_m"]
    left_lane = max_lateral >= STRICT_THRESHOLDS["detour_lateral_m"]
    if not left_lane or not passed_obstacle:
        return _fail(
            "ego did not both leave the blocked lane and pass the obstacle",
            evidence={
                "max_abs_lateral_m": round(max_lateral, 4),
                "max_forward_m": round(max_forward, 4),
                "obstacle_forward_m": round(obstacle_s, 4),
                "left_lane": left_lane,
                "passed_obstacle": passed_obstacle,
            },
        )
    return _pass(
        "ego laterally left the blocked lane and passed the obstacle",
        evidence={
            "max_abs_lateral_m": round(max_lateral, 4),
            "max_forward_m": round(max_forward, 4),
            "obstacle_forward_m": round(obstacle_s, 4),
        },
    )


def _score_yield(rows: List[Dict[str, Any]], onset: float, active_end: float) -> Dict[str, Any]:
    if _collision_count(rows) > 0:
        return _fail("collision recorded during YIELD episode", evidence={"collision_count": _collision_count(rows)})
    before = [r for r in rows if float(r["sim_time"]) <= onset + 1.0]
    active = _between(rows, onset, active_end)
    if not active:
        return _fail("no telemetry inside the YIELD active window")
    pre_max = _max(before, "ego_speed_kmh")
    min_active = _min(active, "ego_speed_kmh")
    if pre_max is None or pre_max < STRICT_THRESHOLDS["yield_pre_speed_kmh"]:
        return _fail(
            "ego never established approach motion before YIELD",
            evidence={"max_speed_before_yield_kmh": pre_max},
        )
    low_index = next(
        (
            idx for idx, r in enumerate(rows)
            if float(r["sim_time"]) >= onset and float(r["ego_speed_kmh"]) <= STRICT_THRESHOLDS["yield_stop_speed_kmh"]
        ),
        None,
    )
    if low_index is None:
        return _fail(
            "ego did not slow/stop for the emergency vehicle",
            evidence={"min_active_speed_kmh": min_active},
        )
    resumed_rows = [
        r for r in rows[low_index + 1:]
        if float(r["ego_speed_kmh"]) >= STRICT_THRESHOLDS["yield_resume_speed_kmh"]
    ]
    if not resumed_rows:
        return _fail(
            "ego slowed/stopped but did not resume after yielding",
            evidence={"min_active_speed_kmh": min_active, "low_speed_time_s": rows[low_index].get("sim_time")},
        )
    max_lateral_after = max(abs(float(r["ego_lateral_m"])) for r in rows[low_index + 1:]) if rows[low_index + 1:] else 0.0
    if max_lateral_after < STRICT_THRESHOLDS["yield_lateral_m"]:
        return _fail(
            "ego did not laterally clear the lane while yielding",
            evidence={
                "max_abs_lateral_after_yield_m": round(float(max_lateral_after), 4),
                "yield_lateral_threshold_m": STRICT_THRESHOLDS["yield_lateral_m"],
                "low_speed_time_s": rows[low_index].get("sim_time"),
                "resume_time_s": resumed_rows[0].get("sim_time"),
            },
        )
    return _pass(
        "ego slowed/stopped for the emergency vehicle, cleared laterally, and resumed",
        evidence={
            "max_speed_before_yield_kmh": round(float(pre_max), 4),
            "min_active_speed_kmh": round(float(min_active), 4) if min_active is not None else None,
            "low_speed_time_s": round(float(rows[low_index]["sim_time"]), 3),
            "resume_time_s": round(float(resumed_rows[0]["sim_time"]), 3),
            "resume_speed_kmh": round(float(resumed_rows[0]["ego_speed_kmh"]), 4),
            "max_abs_lateral_after_yield_m": round(float(max_lateral_after), 4),
        },
    )


def score_episode_from_telemetry(
    result: Dict[str, Any],
    telemetry_rows: Iterable[Dict[str, Any]],
    *,
    scenario: Optional[str] = None,
    expected_action: Optional[str] = None,
    max_reaction_time: Optional[float] = None,
    controller_errors: Iterable[Any] = (),
    setup_errors: Iterable[Any] = (),
) -> Dict[str, Any]:
    """Return the strict PASS/FAIL/INVALID verdict for one episode."""
    rows, invalid = _normalise_rows(telemetry_rows)
    if invalid is not None:
        invalid.update({"scenario": scenario or result.get("scenario"), "expected_action": expected_action or result.get("expected_action")})
        return invalid
    assert rows is not None

    common_invalid = _validate_common(
        rows,
        controller_errors=controller_errors,
        setup_errors=setup_errors,
    )
    if common_invalid is not None:
        common_invalid.update({"scenario": scenario or result.get("scenario"), "expected_action": expected_action or result.get("expected_action")})
        return common_invalid

    action = str(expected_action or result.get("expected_action") or "").upper()
    scen = str(scenario or result.get("scenario") or "")
    if not action:
        return _invalid("expected action is missing from strict scoring inputs", evidence={"scenario": scen})
    onset, duration, active_end = _first_meta(rows)
    if action in ("STOP", "HOLD"):
        # Phase-aware window: score the directive the scenario enforces, not
        # whatever phase happened to be live at rows[0].
        onset, duration, active_end = _stop_hold_window(rows)
    try:
        reaction_budget = float(max_reaction_time if max_reaction_time is not None else 3.0)
    except Exception:
        reaction_budget = 3.0

    if action == "STOP":
        verdict = _score_stop_hold(rows, scen, onset, active_end, reaction_budget, hold=False)
    elif action == "HOLD":
        verdict = _score_stop_hold(rows, scen, onset, active_end, reaction_budget, hold=True)
    elif action == "PROCEED":
        verdict = _score_proceed(rows, onset, scen)
    elif action == "DETOUR":
        verdict = _score_detour(rows, onset)
    elif action == "YIELD":
        verdict = _score_yield(rows, onset, active_end)
    else:
        verdict = _invalid(f"unknown expected action for strict scoring: {action!r}")

    verdict.update(
        {
            "scenario": scen,
            "expected_action": action,
            "telemetry_rows": len(rows),
            "active_window": {
                "onset_s": onset,
                "duration_s": duration,
                "active_end_s": active_end,
            },
            "max_speed_kmh": _max(rows, "ego_speed_kmh"),
            "final_speed_kmh": float(rows[-1]["ego_speed_kmh"]),
            "collision_count": _collision_count(rows),
        }
    )
    return verdict


def write_strict_artifacts(
    episode_dir: str,
    telemetry_rows: List[Dict[str, Any]],
    strict_score: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Write wide telemetry CSV plus JSON score artifacts into an episode dir."""
    root = Path(episode_dir)
    root.mkdir(parents=True, exist_ok=True)
    csv_path = root / "strict_telemetry.csv"
    json_path = root / "strict_telemetry.json"
    score_path = root / "strict_scoring.json"

    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(TELEMETRY_FIELDS), extrasaction="ignore")
        writer.writeheader()
        for row in telemetry_rows:
            writer.writerow({key: row.get(key) for key in TELEMETRY_FIELDS})

    payload = {
        "metadata": metadata or {},
        "telemetry": telemetry_rows,
        "strict_scoring": strict_score,
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    with open(score_path, "w", encoding="utf-8") as fh:
        json.dump(strict_score, fh, indent=2, default=str)
    return {
        "strict_telemetry_csv": str(csv_path),
        "strict_telemetry_json": str(json_path),
        "strict_scoring_json": str(score_path),
    }
