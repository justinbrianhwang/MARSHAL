import math

import pytest

from marshal_bench.criteria import strict_episode_scoring as strict
from tests._telemetry import (
    clean_detour_around_obstacle,
    clean_proceed_through_junction,
    clean_rule_hierarchy_proceed_with_care,
    clean_stop_before_line,
    clean_yield_then_resume,
    make_rows,
)


def score(rows, *, expected_action, scenario="unit", **kwargs):
    return strict.score_episode_from_telemetry(
        {"scenario": scenario, "expected_action": expected_action},
        rows,
        scenario=scenario,
        expected_action=expected_action,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("expected_action", "scenario", "rows"),
    [
        ("STOP", "green_stop", clean_stop_before_line()),
        ("HOLD", "sequential_directive", clean_stop_before_line()),
        ("PROCEED", "red_proceed", clean_proceed_through_junction()),
        ("DETOUR", "crash_detour", clean_detour_around_obstacle()),
        ("YIELD", "ambulance_yield", clean_yield_then_resume()),
        ("PROCEED", "rule_hierarchy", clean_rule_hierarchy_proceed_with_care()),
    ],
)
def test_golden_pass_traces(expected_action, scenario, rows):
    verdict = score(rows, expected_action=expected_action, scenario=scenario)

    assert verdict["verdict"] == "PASS"
    assert verdict["passed"] is True
    assert verdict["invalid"] is False
    assert verdict["expected_action"] == expected_action
    assert verdict["scenario"] == scenario


def _detour_without_lateral():
    rows = clean_detour_around_obstacle()
    for row in rows:
        row["ego_lateral_m"] = 0.0
    return rows


def _yield_without_slowdown():
    return make_rows(
        speeds=[8.0, 8.0, 8.0, 8.0, 8.0],
        ego_lateral_m=[0.0, 0.0, 0.0, 1.2, 1.2],
        distance_to_stopline_m=[40.0, 35.0, 30.0, 25.0, 20.0],
        officer_gesture_id="YIELD",
    )


def _rule_hierarchy_without_yield():
    return make_rows(
        speeds=[8.0, 8.0, 8.0, 8.0],
        in_junction=[False, False, True, True],
        distance_to_stopline_m=[25.0, 15.0, 5.0, -3.0],
        ego_forward_m=[0.0, 8.0, 16.0, 24.0],
        distance_to_hazard_m=[20.0, 14.0, 9.0, 18.0],
        hazard_forward_m=[18.0, 18.0, 18.0, 18.0],
        officer_gesture_id="PROCEED",
    )


@pytest.mark.parametrize(
    ("expected_action", "scenario", "rows"),
    [
        ("STOP", "green_stop", clean_proceed_through_junction()),
        ("HOLD", "sequential_directive", clean_proceed_through_junction()),
        ("PROCEED", "red_proceed", clean_stop_before_line()),
        ("DETOUR", "crash_detour", _detour_without_lateral()),
        ("YIELD", "ambulance_yield", _yield_without_slowdown()),
        ("PROCEED", "rule_hierarchy", _rule_hierarchy_without_yield()),
    ],
)
def test_golden_fail_traces(expected_action, scenario, rows):
    verdict = score(rows, expected_action=expected_action, scenario=scenario)

    assert verdict["verdict"] == "FAIL"
    assert verdict["passed"] is False
    assert verdict["invalid"] is False


def _valid_rows():
    return clean_stop_before_line()


@pytest.mark.parametrize(
    ("rows", "expected_action", "kwargs"),
    [
        ([], "STOP", {}),
        ([{k: v for k, v in _valid_rows()[0].items() if k != "ego_speed_kmh"}], "STOP", {}),
        (
            [
                {
                    **_valid_rows()[0],
                    "ego_speed_kmh": math.nan,
                }
            ],
            "STOP",
            {},
        ),
        (make_rows(control_finite=[True, True, False, True, True, True, True]), "STOP", {}),
        (_valid_rows(), "STOP", {"setup_errors": ["spawn failed"]}),
        (_valid_rows(), "STOP", {"controller_errors": ["adapter exploded"]}),
        (_valid_rows(), "DANCE", {}),
    ],
)
def test_invalid_inputs_return_invalid(rows, expected_action, kwargs):
    verdict = score(rows, expected_action=expected_action, **kwargs)

    assert verdict["verdict"] == "INVALID"
    assert verdict["passed"] is False
    assert verdict["invalid"] is True


def test_strict_threshold_values_are_pinned():
    assert strict.STRICT_THRESHOLDS == {
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
        "stopline_crossed_tolerance_m": 0.3,
        "hazard_engagement_m": 15.0,
        "stopline_engagement_m": 15.0,
        "hold_dwell_min_s": 2.0,
        "hold_dwell_max_drift_m": 0.5,
    }


def test_stop_ignores_unrelated_junction_far_from_stopline():
    # Curated Town03 green_stop: the spawn sits 1.2 m before an unrelated
    # junction polygon while the assigned stopline is 44 m ahead. A correctly
    # stopped ego standing on that polygon must still PASS.
    rows = clean_stop_before_line()
    for row in rows:
        if float(row["distance_to_stopline_m"]) > 35.0:
            row["in_junction"] = True

    verdict = score(rows, expected_action="STOP", scenario="green_stop")

    assert verdict["verdict"] == "PASS"


def test_stop_fails_inside_assigned_conflict_zone():
    rows = clean_stop_before_line()
    for row in rows[-2:]:
        row["in_junction"] = True
        row["distance_to_stopline_m"] = 5.0

    verdict = score(rows, expected_action="STOP", scenario="green_stop")

    assert verdict["verdict"] == "FAIL"
    assert "entered the intersection" in verdict["reason"]


@pytest.mark.parametrize("action", ["STOP", "HOLD", "PROCEED"])
def test_stationary_ego_cannot_strict_pass_any_action_family(action):
    rows = make_rows(
        speeds=0.0,
        ego_forward_m=0.0,
        in_junction=False,
        distance_to_stopline_m=30.0,
        officer_gesture_id=action,
    )
    verdict = score(rows, expected_action=action, scenario="stationary_regression")
    assert verdict["passed"] is False
    assert verdict["verdict"] == "FAIL"
    assert "never engaged" in verdict["reason"]


def test_stopline_clearance_threshold_is_inclusive():
    rows = clean_stop_before_line()
    for row in rows[-2:]:
        row["distance_to_stopline_m"] = strict.STRICT_THRESHOLDS["stopline_clearance_m"]

    verdict = score(rows, expected_action="STOP", scenario="green_stop")

    assert verdict["verdict"] == "PASS"


def test_stop_speed_threshold_is_exclusive_for_enforced_stop():
    rows = clean_stop_before_line()
    for row in rows:
        if row["sim_time"] >= 3.0:
            row["ego_speed_kmh"] = strict.STRICT_THRESHOLDS["stop_speed_kmh"]

    verdict = score(rows, expected_action="STOP", scenario="green_stop")

    assert verdict["verdict"] == "FAIL"
    assert verdict["invalid"] is False


def test_proceed_speed_threshold_is_inclusive():
    rows = make_rows(
        speeds=[strict.STRICT_THRESHOLDS["engagement_speed_kmh"], 1.0, strict.STRICT_THRESHOLDS["proceed_speed_kmh"]],
        in_junction=[False, False, True],
        distance_to_stopline_m=[8.0, 2.0, -1.0],
        officer_gesture_id="PROCEED",
    )

    verdict = score(rows, expected_action="PROCEED", scenario="red_proceed")

    assert verdict["verdict"] == "PASS"


def test_detour_lateral_and_pass_margin_thresholds_are_inclusive():
    obstacle_s = 12.0
    rows = make_rows(
        speeds=[5.0, 5.0, 5.0, 5.0],
        ego_forward_m=[0.0, 5.0, 10.0, obstacle_s + strict.STRICT_THRESHOLDS["detour_pass_margin_m"]],
        ego_lateral_m=[0.0, 0.5, strict.STRICT_THRESHOLDS["detour_lateral_m"], 1.0],
        hazard_forward_m=[obstacle_s] * 4,
        distance_to_hazard_m=[20.0, 15.0, 10.0, 5.0],
        officer_gesture_id="DETOUR",
    )

    verdict = score(rows, expected_action="DETOUR", scenario="crash_detour")

    assert verdict["verdict"] == "PASS"


def test_yield_thresholds_are_inclusive():
    rows = make_rows(
        speeds=[
            strict.STRICT_THRESHOLDS["yield_pre_speed_kmh"],
            4.0,
            strict.STRICT_THRESHOLDS["yield_stop_speed_kmh"],
            2.5,
            strict.STRICT_THRESHOLDS["yield_resume_speed_kmh"],
        ],
        ego_lateral_m=[0.0, 0.0, 0.2, strict.STRICT_THRESHOLDS["yield_lateral_m"], 1.0],
        officer_gesture_id="YIELD",
    )

    verdict = score(rows, expected_action="YIELD", scenario="ambulance_yield")

    assert verdict["verdict"] == "PASS"


def test_rule_hierarchy_yield_window_and_entry_thresholds_are_inclusive():
    rows = make_rows(
        speeds=[8.0, strict.STRICT_THRESHOLDS["proceed_care_yield_speed_kmh"], 2.5, strict.STRICT_THRESHOLDS["proceed_speed_kmh"]],
        in_junction=[False, False, False, True],
        distance_to_hazard_m=[20.0, strict.STRICT_THRESHOLDS["proceed_care_hazard_window_m"], 10.0, 20.0],
        distance_to_stopline_m=[25.0, 20.0, 10.0, -1.0],
        ego_forward_m=[0.0, 4.0, 8.0, 12.0],
        officer_gesture_id="PROCEED",
    )

    verdict = score(rows, expected_action="PROCEED", scenario="rule_hierarchy")

    assert verdict["verdict"] == "PASS"


def test_graded_stop_ignores_unrelated_junction_far_from_stopline():
    """Graded must share the strict scorer's assigned-conflict-zone rule.

    Regression for the curated Town03 green_stop: a strict-PASS textbook stop
    was graded 0.024 because rows crossing an unrelated junction polygon 44 m
    from the stopline triggered the bare any-junction conflict factor.
    """
    from marshal_bench.criteria import graded_episode_scoring as graded

    rows = clean_stop_before_line()
    for row in rows:
        if float(row["distance_to_stopline_m"]) > 35.0:
            row["in_junction"] = True

    verdict = graded.score_episode_from_telemetry(
        {"scenario": "green_stop", "expected_action": "STOP"},
        rows,
        scenario="green_stop",
        expected_action="STOP",
    )
    assert float(verdict["credit"]) == 1.0

    near = clean_stop_before_line()
    for row in near[-2:]:
        row["in_junction"] = True
        row["distance_to_stopline_m"] = 5.0
    verdict_near = graded.score_episode_from_telemetry(
        {"scenario": "green_stop", "expected_action": "STOP"},
        near,
        scenario="green_stop",
        expected_action="STOP",
    )
    assert float(verdict_near["credit"]) < 1.0
