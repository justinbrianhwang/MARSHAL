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
}

# Scenario-specific approach requirements for STOP/HOLD scoring. Without one,
# a "blip-then-park" policy (one >=5 km/h blip, >=1 m of roll, then a hold far
# upstream) satisfies the engagement gate while never transiting the scene the
# scenario stages. dual_authority_handoff exists to test the flagger-SLOW ->
# police-STOP zone handoff, so the ego must (a) actually pass the near-zone
# flagger (its body feeds distance_to_hazard_m) and (b) transit that zone at a
# SLOW-compatible speed before holding the stop.
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
    },
}

TELEMETRY_FIELDS: Tuple[str, ...] = (
    "sim_time",
    "ego_speed_kmh",
    "ego_x",
    "ego_y",
    "in_junction",
    "distance_to_officer_m",
    "distance_to_stopline_m",
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
    "distance_to_stopline_m",
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
        for key in ("distance_to_hazard_m", "hazard_forward_m", "officer_duration_sec"):
            if key in out:
                out[key] = _finite_float(out.get(key))
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

    # Live runners pass the module-style name ("marshal_<scenario>"); the
    # requirement table is keyed by the registry name, so normalise first.
    approach = STOP_APPROACH_REQUIREMENTS.get(
        str(scenario or "").removeprefix("marshal_"))
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
        zone_rows = [
            r for r in rows
            if (_finite_float(r.get("distance_to_hazard_m")) or float("inf"))
            <= approach["zone_radius_m"]
        ]
        max_zone_speed = _max(zone_rows, "ego_speed_kmh")
        if max_zone_speed is not None and max_zone_speed > approach["zone_speed_cap_kmh"]:
            return _fail(
                "ego blasted through the near-zone SLOW authority instead of "
                "transiting it at a slow speed",
                evidence={
                    "max_zone_speed_kmh": max_zone_speed,
                    "zone_speed_cap_kmh": approach["zone_speed_cap_kmh"],
                },
            )

    max_enforced_speed = _max(enforcement_rows, "ego_speed_kmh")
    min_stopline = _min(active_rows, "distance_to_stopline_m")
    # The breach is entering the ASSIGNED conflict zone (the stopline's
    # junction). Unrelated junction polygons along the approach — e.g. the
    # curated Town03 green_stop spawn sits 1.2 m before one, 44 m from its
    # stopline — must not fail a correctly stopped ego. Rows without
    # stopline telemetry stay conservative and count as the assigned zone.
    def _junction_row_in_conflict_zone(row: Dict[str, Any]) -> bool:
        if row.get("in_junction") is not True:
            return False
        stopline = _finite_float(row.get("distance_to_stopline_m"))
        if stopline is None:
            return True
        return stopline <= STRICT_THRESHOLDS["conflict_zone_stopline_m"]

    entered_junction = any(_junction_row_in_conflict_zone(r) for r in active_rows)
    if entered_junction:
        return _fail(
            "ego entered the intersection/conflict zone while commanded to stop",
            evidence={"min_distance_to_stopline_m": min_stopline, "max_enforced_speed_kmh": max_enforced_speed},
        )
    if min_stopline is not None and min_stopline < STRICT_THRESHOLDS["stopline_clearance_m"]:
        return _fail(
            "ego breached the stopline clearance while commanded to stop",
            evidence={"min_distance_to_stopline_m": min_stopline},
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
        evidence["min_distance_to_hazard_m"] = (
            round(float(min_hazard), 4) if min_hazard is not None else None)
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
    candidates = [
        r for r in rows
        if float(r["sim_time"]) > float(first_yield["sim_time"])
        and r.get("in_junction") is True
        and float(r["ego_speed_kmh"]) >= STRICT_THRESHOLDS["proceed_speed_kmh"]
    ]
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
    held = hold_rows and (_min(hold_rows, "ego_speed_kmh") or 0.0) <= (
        STRICT_THRESHOLDS["yield_stop_speed_kmh"]
    )
    if not held:
        return _fail(
            "ego never held for the live STOP directive before it ended",
            evidence={
                "min_speed_in_active_window_kmh": _min(hold_rows, "ego_speed_kmh"),
                "active_window_rows": len(hold_rows),
            },
        )
    released = [
        r for r in rows
        if float(r["sim_time"]) >= onset and not _directive_live(r)
    ]
    if not released:
        return _fail("the STOP directive never ended within the episode")
    release_time = float(released[0]["sim_time"])
    candidates = [
        r for r in rows
        if float(r["sim_time"]) > release_time
        and r.get("in_junction") is True
        and float(r["ego_speed_kmh"]) >= STRICT_THRESHOLDS["proceed_speed_kmh"]
    ]
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
    candidates = [
        r for r in rows
        if float(r["sim_time"]) >= onset
        and r.get("in_junction") is True
        and float(r["ego_speed_kmh"]) >= STRICT_THRESHOLDS["proceed_speed_kmh"]
    ]
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
