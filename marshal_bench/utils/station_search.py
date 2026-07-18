"""Pure station candidate filtering, scoring, and JSON contract validation.

This module intentionally has no CARLA import.  ``scripts/find_stations.py``
normalises CARLA waypoints and actors into the plain dictionaries consumed
here, which keeps the feasibility policy deterministic and offline-testable.
"""
from __future__ import annotations

import math
from numbers import Real
from typing import Any, Iterable, Mapping, Optional, Sequence


STATION_FIELDS = frozenset({"x", "y", "z", "yaw", "tl_id", "lanes"})
REUSE_PENALTY_FRACTION = 0.16
DIVERSITY_QUALITY_WINDOW_FRACTION = 0.15
HARD_REQUIREMENT_FIELDS = frozenset(
    {
        "needs_traffic_light",
        "needs_sidewalk_point",
        "needs_adjacent_same_road_lane",
        "needs_detour_room",
        "min_detour_clearance_m",
        "needs_offroad_shoulder",
    }
)
GENERATION_REQUIREMENT_FIELDS = frozenset(
    {
        "needs_junction_approach",
        "min_runup_m",
        "min_initial_stopline_m",
        "max_initial_stopline_m",
        "prefers_sidewalk_point",
        "officer_lateral_offset_m",
        "detour_hazard_start_m",
        "detour_staged_span_m",
        "detour_pass_margin_m",
        "detour_merge_taper_m",
        "min_detour_runout_m",
    }
)
REQUIREMENT_FIELDS = HARD_REQUIREMENT_FIELDS | GENERATION_REQUIREMENT_FIELDS
REQUIREMENT_CLASS_FIELDS = frozenset({"hard", "generation", "notes"})
TRAFFIC_LIGHT_PIN_RADIUS_M = 75.0


def _finite_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))


def detour_runout_m(
    hazard_start_m: float,
    staged_span_m: float,
    pass_margin_m: float,
    merge_taper_m: float,
) -> float:
    """Return the full straight, junction-free DETOUR staging envelope."""
    values = (hazard_start_m, staged_span_m, pass_margin_m, merge_taper_m)
    if any(not _finite_number(value) or float(value) < 0.0 for value in values):
        raise ValueError("detour run-out terms must be finite non-negative numbers")
    return sum(float(value) for value in values)


def validate_requirements(requirements: Mapping[str, Any]) -> list[str]:
    """Return human-readable errors for one requirements entry."""
    errors: list[str] = []
    missing = REQUIREMENT_CLASS_FIELDS - set(requirements)
    extra = set(requirements) - REQUIREMENT_CLASS_FIELDS
    if missing:
        errors.append(f"missing requirement classes/metadata: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected requirement classes/metadata: {sorted(extra)}")
    hard = requirements.get("hard", {})
    generation = requirements.get("generation", {})
    if not isinstance(hard, Mapping):
        errors.append("hard must be an object")
        hard = {}
    if not isinstance(generation, Mapping):
        errors.append("generation must be an object")
        generation = {}
    for class_name, values, expected in (
        ("hard", hard, HARD_REQUIREMENT_FIELDS),
        ("generation", generation, GENERATION_REQUIREMENT_FIELDS),
    ):
        class_missing = expected - set(values)
        class_extra = set(values) - expected
        if class_missing:
            errors.append(f"{class_name} missing fields: {sorted(class_missing)}")
        if class_extra:
            errors.append(f"{class_name} has unexpected fields: {sorted(class_extra)}")
    flattened = {**generation, **hard}
    for name in REQUIREMENT_FIELDS:
        if (
            name.startswith("needs_") or name.startswith("prefers_")
        ) and name in flattened and not isinstance(flattened[name], bool):
            errors.append(f"{name} must be boolean")
    for name in (
        "min_runup_m",
        "min_initial_stopline_m",
        "max_initial_stopline_m",
        "min_detour_clearance_m",
        "officer_lateral_offset_m",
        "detour_hazard_start_m",
        "detour_staged_span_m",
        "detour_pass_margin_m",
        "detour_merge_taper_m",
        "min_detour_runout_m",
    ):
        if name in flattened and (not _finite_number(flattened[name]) or float(flattened[name]) < 0.0):
            errors.append(f"{name} must be a finite non-negative number")
    if (
        _finite_number(flattened.get("min_initial_stopline_m"))
        and _finite_number(flattened.get("max_initial_stopline_m"))
        and float(flattened["min_initial_stopline_m"])
        > float(flattened["max_initial_stopline_m"])
    ):
        errors.append("min_initial_stopline_m must not exceed max_initial_stopline_m")
    runout_names = (
        "detour_hazard_start_m",
        "detour_staged_span_m",
        "detour_pass_margin_m",
        "detour_merge_taper_m",
    )
    if all(_finite_number(flattened.get(name)) for name in runout_names) and _finite_number(
        flattened.get("min_detour_runout_m")
    ):
        derived = detour_runout_m(*(float(flattened[name]) for name in runout_names))
        if not math.isclose(derived, float(flattened["min_detour_runout_m"]), abs_tol=1e-9):
            errors.append(
                f"min_detour_runout_m must equal its four terms ({derived:.1f} m)"
            )
    if "notes" in requirements and (not isinstance(requirements["notes"], str) or not requirements["notes"].strip()):
        errors.append("notes must be a non-empty string")
    return errors


def classify_requirements(
    requirements: Mapping[str, Any],
    criterion_classes: Mapping[str, Any],
    criterion_defaults: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Convert the compact JSON representation into explicit policy classes."""
    defaults = criterion_defaults or {}
    hard_values = {**defaults.get("hard", {}), **requirements}
    generation_values = {**defaults.get("generation", {}), **requirements}
    hard_names = tuple(criterion_classes.get("hard", ()))
    generation_names = tuple(criterion_classes.get("generation", ()))
    return {
        "hard": {name: hard_values[name] for name in hard_names if name in hard_values},
        "generation": {
            name: generation_values[name]
            for name in generation_names
            if name in generation_values
        },
        "notes": requirements.get("notes", ""),
    }


def hard_requirements(requirements: Mapping[str, Any]) -> Mapping[str, Any]:
    return requirements.get("hard", {})


def generation_requirements(requirements: Mapping[str, Any]) -> Mapping[str, Any]:
    return requirements.get("generation", {})


def _selection_requirements(requirements: Mapping[str, Any]) -> dict[str, Any]:
    return {**generation_requirements(requirements), **hard_requirements(requirements)}


def _requirement_violations(
    topology_facts: Mapping[str, Any], requirements: Mapping[str, Any]
) -> list[str]:
    """Evaluate a selected set of boolean/bound criteria."""
    reasons: list[str] = []
    if requirements.get("needs_traffic_light"):
        distance = topology_facts.get("forward_traffic_light_distance_m")
        if distance is None or float(distance) > TRAFFIC_LIGHT_PIN_RADIUS_M + 1e-9:
            reasons.append(
                f"forward traffic light within {TRAFFIC_LIGHT_PIN_RADIUS_M:.0f} m required"
            )
    if requirements.get("needs_junction_approach") and not topology_facts.get("junction_approach", False):
        reasons.append("junction approach required")
    runup = float(topology_facts.get("runup_m", 0.0) or 0.0)
    minimum = float(requirements.get("min_runup_m", 0.0) or 0.0)
    if runup + 1e-9 < minimum:
        reasons.append(f"run-up {runup:.1f} m is below {minimum:.1f} m")
    initial_distance = topology_facts.get("initial_stopline_distance_m")
    if initial_distance is not None:
        initial_distance = float(initial_distance)
        minimum_initial = float(requirements.get("min_initial_stopline_m", 0.0) or 0.0)
        maximum_initial = float(requirements.get("max_initial_stopline_m", math.inf))
        if initial_distance + 1e-9 < minimum_initial:
            reasons.append(
                f"initial stopline distance {initial_distance:.1f} m is below "
                f"{minimum_initial:.1f} m"
            )
        if initial_distance > maximum_initial + 1e-9:
            reasons.append(
                f"initial stopline distance {initial_distance:.1f} m exceeds "
                f"{maximum_initial:.1f} m"
            )
    if requirements.get("needs_sidewalk_point") and not topology_facts.get("officer_offroad", False):
        reasons.append("officer sidewalk/shoulder point required")
    if requirements.get("needs_adjacent_same_road_lane") and not topology_facts.get("adjacent_same_road_lane", False):
        reasons.append("adjacent same-road driving lane required")
    minimum_detour = float(requirements.get("min_detour_clearance_m", 0.0) or 0.0)
    detour_clearance = float(topology_facts.get("detour_clearance_m", 0.0) or 0.0)
    if requirements.get("needs_detour_room") and detour_clearance + 1e-9 < minimum_detour:
        reasons.append(
            f"detour clearance {detour_clearance:.1f} m is below {minimum_detour:.1f} m"
        )
    minimum_runout = float(requirements.get("min_detour_runout_m", 0.0) or 0.0)
    junction_free_forward = float(
        topology_facts.get("junction_free_forward_m", 0.0) or 0.0
    )
    if junction_free_forward + 1e-9 < minimum_runout:
        reasons.append(
            f"junction-free straight detour run-out {junction_free_forward:.1f} m is below "
            f"{minimum_runout:.1f} m"
        )
    if requirements.get("needs_offroad_shoulder") and not topology_facts.get("offroad_shoulder", False):
        reasons.append("off-road shoulder required")
    return reasons


def witness_violations(
    topology_facts: Mapping[str, Any], requirements: Mapping[str, Any]
) -> list[str]:
    """Return requirement failures for one witness's plain topology facts.

    This intentionally excludes transient spawn occupancy: witnesses validate
    the extracted staging requirements, not whether another actor happens to
    occupy the transform during a particular mining run.
    """
    return _requirement_violations(topology_facts, hard_requirements(requirements))


def generation_violations(
    topology_facts: Mapping[str, Any], requirements: Mapping[str, Any]
) -> list[str]:
    """Return candidate-generation failures without promoting them to witness-hard."""
    return _requirement_violations(topology_facts, generation_requirements(requirements))


def candidate_rejections(candidate: Mapping[str, Any], requirements: Mapping[str, Any]) -> list[str]:
    """Explain hard and generation criteria a generated candidate fails."""
    reasons = _requirement_violations(candidate, _selection_requirements(requirements))
    if candidate.get("spawn_clear") is False:
        reasons.append("spawn transform is statically blocked")
    return reasons


def candidate_score(candidate: Mapping[str, Any]) -> float:
    """Policy score required by P2: usable run-up plus geometric margin."""
    return float(candidate.get("runup_m", 0.0) or 0.0) + float(
        candidate.get("geometric_margin_m", 0.0) or 0.0
    )


def select_best_candidate(
    candidates: Iterable[Mapping[str, Any]],
    requirements: Mapping[str, Any],
    *,
    station_use_counts: Optional[Mapping[str, int]] = None,
) -> tuple[Optional[dict[str, Any]], str]:
    """Return the best feasible candidate with bounded soft reuse diversity.

    Diversity is considered only inside 15% of the raw optimum, so a materially
    lower-quality station cannot win. Within that window each prior use costs
    16% of the current optimum, making an unused station up to roughly 15%
    below a once-used optimum win. Hard and generation feasibility are always
    applied before scoring.
    """
    valid: list[tuple[float, str, dict[str, Any]]] = []
    rejection_counts: dict[str, int] = {}
    total = 0
    for raw in candidates:
        total += 1
        candidate = dict(raw)
        rejected = candidate_rejections(candidate, requirements)
        if rejected:
            for reason in rejected:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue
        valid.append((candidate_score(candidate), str(candidate.get("id", "")), candidate))
    if valid:
        # Surface class is a preference, not a staging precondition: the
        # runtime applies a raw lateral transform and can spawn an officer on
        # an unmapped verge. Prefer the old surface-qualified pool whenever it
        # exists, but fall back to all hard-feasible candidates when it does
        # not. This preserves P2 selection/diversity for towns with qualified
        # candidates while avoiding a false infeasibility elsewhere.
        preferred = [row for row in valid if row[2].get("officer_offroad", False)]
        ranking_pool = (
            preferred
            if generation_requirements(requirements).get("prefers_sidewalk_point", False) and preferred
            else valid
        )
        best_raw = max(row[0] for row in ranking_pool)
        quality_floor = best_raw * (1.0 - DIVERSITY_QUALITY_WINDOW_FRACTION)
        near_best = [row for row in ranking_pool if row[0] + 1e-9 >= quality_floor]
        uses = station_use_counts or {}
        penalty_unit = best_raw * REUSE_PENALTY_FRACTION
        ranked = [
            (score - penalty_unit * int(uses.get(candidate_id, 0)), candidate_id, candidate)
            for score, candidate_id, candidate in near_best
        ]
        # Stable deterministic tie-break: lexical candidate id.
        ranked.sort(key=lambda row: (-row[0], row[1]))
        chosen = ranked[0][2]
        reuse_count = int(uses.get(str(chosen.get("id", "")), 0))
        surface_note = (
            ", sidewalk/shoulder preference applied"
            if ranking_pool is preferred
            else ", sidewalk/shoulder preference unavailable; used hard-feasible fallback"
            if generation_requirements(requirements).get("prefers_sidewalk_point", False)
            else ""
        )
        return chosen, (
            f"selected {chosen.get('id', '<unnamed>')} from {len(valid)}/{total} satisfying candidates; "
            f"reuse count {reuse_count}, 15% quality window, 16% reuse penalty{surface_note}"
        )
    if total == 0:
        return None, "no topology candidates were mined"
    summary = "; ".join(
        f"{reason} ({count}/{total})" for reason, count in sorted(rejection_counts.items())
    )
    return None, f"no candidate satisfies all requirements: {summary}"


def station_from_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Project rich candidate metadata onto the exact legacy station schema."""
    spawn = candidate.get("spawn") or {}
    return {
        "x": round(float(spawn["x"]), 3),
        "y": round(float(spawn["y"]), 3),
        "z": round(float(spawn.get("z", 0.5)), 3),
        "yaw": round(float(spawn["yaw"]), 3),
        "tl_id": int(candidate.get("tl_id", -1)),
        "lanes": int(candidate.get("lanes", 1)),
    }


def validate_stations_payload(
    payload: Mapping[str, Any], expected_scenarios: Optional[Iterable[str]] = None
) -> list[str]:
    """Validate both hand-curated and generated ``stations*.json`` payloads."""
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["payload must be an object"]
    if not isinstance(payload.get("map"), str) or not str(payload.get("map", "")).strip():
        errors.append("map must be a non-empty string")
    stations = payload.get("stations")
    if not isinstance(stations, Mapping):
        return errors + ["stations must be an object"]
    if expected_scenarios is not None:
        expected = set(expected_scenarios)
        actual = set(stations)
        if actual != expected:
            errors.append(
                f"station keys differ: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
    for scenario, station in stations.items():
        prefix = f"stations.{scenario}"
        if not isinstance(station, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        missing = STATION_FIELDS - set(station)
        extra = set(station) - STATION_FIELDS
        if missing:
            errors.append(f"{prefix} missing fields: {sorted(missing)}")
        if extra:
            errors.append(f"{prefix} unexpected fields: {sorted(extra)}")
        for field in ("x", "y", "z", "yaw"):
            if field in station and not _finite_number(station[field]):
                errors.append(f"{prefix}.{field} must be a finite number")
        if "tl_id" in station and (not isinstance(station["tl_id"], int) or isinstance(station["tl_id"], bool)):
            errors.append(f"{prefix}.tl_id must be an integer")
        if "lanes" in station and (
            not isinstance(station["lanes"], int)
            or isinstance(station["lanes"], bool)
            or station["lanes"] < 1
        ):
            errors.append(f"{prefix}.lanes must be an integer >= 1")
    return errors


def _xy_distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def compare_station_tolerance(
    generated: Mapping[str, Any],
    curated: Mapping[str, Any],
    *,
    generated_stopline: Optional[Mapping[str, Any]] = None,
    curated_stopline: Optional[Mapping[str, Any]] = None,
    spawn_tolerance_m: float = 35.0,
    stopline_tolerance_m: float = 12.0,
) -> dict[str, Any]:
    """Comparison used by ``--self-test`` (pure and independently testable)."""
    spawn_distance = _xy_distance(generated, curated)
    stopline_distance: Optional[float] = None
    if generated_stopline is not None and curated_stopline is not None:
        stopline_distance = _xy_distance(generated_stopline, curated_stopline)
    within = spawn_distance <= spawn_tolerance_m and (
        stopline_distance is None or stopline_distance <= stopline_tolerance_m
    )
    return {
        "within_tolerance": within,
        "spawn_distance_m": round(spawn_distance, 3),
        "spawn_tolerance_m": float(spawn_tolerance_m),
        "stopline_distance_m": None if stopline_distance is None else round(stopline_distance, 3),
        "stopline_tolerance_m": float(stopline_tolerance_m),
    }


__all__ = [
    "GENERATION_REQUIREMENT_FIELDS",
    "HARD_REQUIREMENT_FIELDS",
    "REQUIREMENT_FIELDS",
    "DIVERSITY_QUALITY_WINDOW_FRACTION",
    "REUSE_PENALTY_FRACTION",
    "STATION_FIELDS",
    "candidate_rejections",
    "candidate_score",
    "classify_requirements",
    "compare_station_tolerance",
    "generation_requirements",
    "hard_requirements",
    "select_best_candidate",
    "station_from_candidate",
    "validate_requirements",
    "validate_stations_payload",
    "witness_violations",
]
