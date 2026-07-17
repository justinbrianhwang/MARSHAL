from __future__ import annotations

import json
from pathlib import Path

import pytest

from marshal_bench.criteria.marshal_metrics import SCENARIO_SPEC
from marshal_bench.utils.station_search import (
    candidate_rejections,
    candidate_score,
    compare_station_tolerance,
    select_best_candidate,
    validate_stations_payload,
    witness_violations,
)


ROOT = Path(__file__).resolve().parents[1]


def candidate(**overrides):
    value = {
        "id": "base",
        "signalized": True,
        "forward_traffic_light_distance_m": 10.0,
        "junction_approach": True,
        "runup_m": 40.0,
        "initial_stopline_distance_m": 28.0,
        "geometric_margin_m": 5.0,
        "officer_offroad": True,
        "adjacent_same_road_lane": True,
        "detour_clearance_m": 3.5,
        "offroad_shoulder": True,
        "spawn_clear": True,
    }
    value.update(overrides)
    return value


def requirements(**overrides):
    flat = {
        "needs_traffic_light": True,
        "needs_junction_approach": True,
        "min_runup_m": 28.0,
        "min_initial_stopline_m": 20.0,
        "max_initial_stopline_m": 40.0,
        "needs_sidewalk_point": True,
        "prefers_sidewalk_point": False,
        "needs_adjacent_same_road_lane": False,
        "needs_detour_room": False,
        "min_detour_clearance_m": 0.0,
        "needs_offroad_shoulder": False,
        "officer_lateral_offset_m": 3.2,
        "notes": "synthetic",
    }
    flat.update(overrides)
    return {
        "hard": {
            name: flat[name]
            for name in (
                "needs_traffic_light",
                "needs_sidewalk_point",
                "needs_adjacent_same_road_lane",
                "needs_detour_room",
                "min_detour_clearance_m",
                "needs_offroad_shoulder",
            )
        },
        "generation": {
            name: flat[name]
            for name in (
                "needs_junction_approach",
                "min_runup_m",
                "min_initial_stopline_m",
                "max_initial_stopline_m",
                "prefers_sidewalk_point",
                "officer_lateral_offset_m",
            )
        },
        "notes": flat["notes"],
    }


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            {"forward_traffic_light_distance_m": None},
            "forward traffic light within 75 m required",
        ),
        ({"junction_approach": False}, "junction approach required"),
        ({"runup_m": 27.9}, "run-up 27.9 m is below 28.0 m"),
        (
            {"initial_stopline_distance_m": 19.9},
            "initial stopline distance 19.9 m is below 20.0 m",
        ),
        (
            {"initial_stopline_distance_m": 40.1},
            "initial stopline distance 40.1 m exceeds 40.0 m",
        ),
        ({"spawn_clear": False}, "spawn transform is statically blocked"),
    ],
)
def test_candidate_requirement_filtering(change, expected):
    assert expected in candidate_rejections(candidate(**change), requirements())


def test_specialised_requirements_are_hard_filters():
    req = requirements(
        needs_adjacent_same_road_lane=True,
        needs_detour_room=True,
        min_detour_clearance_m=1.5,
        needs_offroad_shoulder=True,
    )
    rejected = candidate_rejections(
        candidate(
            adjacent_same_road_lane=False,
            detour_clearance_m=1.4,
            offroad_shoulder=False,
        ),
        req,
    )
    assert rejected == [
        "adjacent same-road driving lane required",
        "detour clearance 1.4 m is below 1.5 m",
        "off-road shoulder required",
    ]


def test_scoring_and_selection_order_is_runup_plus_margin():
    long_runup = candidate(id="long", runup_m=55.0, geometric_margin_m=2.0)
    wide_margin = candidate(id="wide", runup_m=40.0, geometric_margin_m=20.0)
    assert candidate_score(wide_margin) == 60.0
    chosen, reason = select_best_candidate([long_runup, wide_margin], requirements())
    assert chosen["id"] == "wide"
    assert "2/2 satisfying" in reason


def test_selection_tie_break_is_deterministic():
    chosen, _ = select_best_candidate(
        [candidate(id="z"), candidate(id="a")], requirements()
    )
    assert chosen["id"] == "a"


def test_infeasible_detection_is_explicit():
    chosen, reason = select_best_candidate(
        [candidate(id="short", runup_m=5.0)], requirements()
    )
    assert chosen is None
    assert reason.startswith("no candidate satisfies all requirements")
    assert "run-up 5.0 m is below 28.0 m" in reason


def test_witness_validation_accepts_and_rejects_synthetic_topology_facts():
    assert witness_violations(candidate(), requirements()) == []
    # Run-up is a candidate-generation criterion, not a witness requirement.
    assert witness_violations(candidate(runup_m=1.0), requirements()) == []
    assert witness_violations(
        candidate(detour_clearance_m=1.4),
        requirements(needs_detour_room=True, min_detour_clearance_m=1.5),
    ) == ["detour clearance 1.4 m is below 1.5 m"]


def test_witness_hard_forward_light_criterion_still_fails():
    assert witness_violations(
        candidate(forward_traffic_light_distance_m=75.1), requirements()
    ) == ["forward traffic light within 75 m required"]


def _assign_three(candidates):
    uses = {}
    assigned = []
    for _scenario in range(3):
        chosen, _ = select_best_candidate(
            candidates, requirements(), station_use_counts=uses
        )
        assigned.append(chosen["id"])
        uses[chosen["id"]] = uses.get(chosen["id"], 0) + 1
    return assigned


def test_soft_diversity_spreads_equal_station_assignments():
    assigned = _assign_three([candidate(id="a"), candidate(id="b")])
    assert assigned == ["a", "b", "a"]


def test_soft_diversity_never_sacrifices_feasibility():
    assert _assign_three([candidate(id="only")]) == ["only", "only", "only"]


def test_sidewalk_preference_preserves_qualified_pool_but_has_feasible_fallback():
    req = requirements(needs_sidewalk_point=False, prefers_sidewalk_point=True)
    preferred, reason = select_best_candidate(
        [candidate(id="surface", officer_offroad=True), candidate(id="verge", officer_offroad=False)],
        req,
    )
    assert preferred["id"] == "surface"
    assert "preference applied" in reason

    fallback, reason = select_best_candidate(
        [candidate(id="verge", officer_offroad=False)], req
    )
    assert fallback["id"] == "verge"
    assert "hard-feasible fallback" in reason


def test_self_test_tolerance_comparison_logic():
    within = compare_station_tolerance(
        {"x": 3.0, "y": 4.0},
        {"x": 0.0, "y": 0.0},
        generated_stopline={"x": 10.0, "y": 0.0},
        curated_stopline={"x": 16.0, "y": 0.0},
        spawn_tolerance_m=5.0,
        stopline_tolerance_m=6.0,
    )
    assert within["within_tolerance"] is True
    outside = compare_station_tolerance(
        {"x": 3.01, "y": 4.0},
        {"x": 0.0, "y": 0.0},
        spawn_tolerance_m=5.0,
    )
    assert outside["within_tolerance"] is False


def test_hand_curated_station_file_matches_shared_schema():
    payload = json.loads(
        (ROOT / "marshal_bench" / "configs" / "stations.json").read_text(encoding="utf-8")
    )
    assert validate_stations_payload(payload, expected_scenarios=SCENARIO_SPEC) == []


def test_generated_station_schema_rejects_degraded_or_extended_entries():
    payload = {
        "map": "TownXX",
        "stations": {
            "demo": {"x": 1.0, "y": 2.0, "z": 0.5, "yaw": 90.0, "tl_id": 4, "lanes": 2}
        },
    }
    assert validate_stations_payload(payload, expected_scenarios={"demo"}) == []
    payload["stations"]["demo"]["stopline"] = {"x": 5.0, "y": 6.0}
    assert any("unexpected fields" in error for error in validate_stations_payload(payload))
