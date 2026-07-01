import math
import random

import pytest

from marshal_bench.criteria import graded_episode_scoring as graded
from tests._telemetry import (
    clean_detour_around_obstacle,
    clean_proceed_through_junction,
    clean_rule_hierarchy_proceed_with_care,
    clean_stop_before_line,
    clean_yield_then_resume,
    make_rows,
)


def score(rows, *, expected_action, scenario="unit", **kwargs):
    return graded.score_episode_from_telemetry(
        {"scenario": scenario, "expected_action": expected_action},
        rows,
        scenario=scenario,
        expected_action=expected_action,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-1.0, 0.0),
        (0.0, 0.0),
        (5.0, 0.5),
        (10.0, 1.0),
        (11.0, 1.0),
    ],
)
def test_linear_credit_increasing_direction(value, expected):
    assert graded._linear_credit(value, full_at=10.0, zero_at=0.0) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 1.0),
        (1.0, 1.0),
        (6.5, 0.5),
        (12.0, 0.0),
        (13.0, 0.0),
    ],
)
def test_linear_credit_decreasing_direction(value, expected):
    assert graded._linear_credit(value, full_at=1.0, zero_at=12.0) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4.999, 0.0),
        (5.0, 1.0),
        (5.001, 0.0),
        (None, 0.0),
    ],
)
def test_linear_credit_degenerate_anchor(value, expected):
    assert graded._linear_credit(value, full_at=5.0, zero_at=5.0) == pytest.approx(expected)


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
def test_credit_is_action_latency_safety_product(expected_action, scenario, rows):
    verdict = score(rows, expected_action=expected_action, scenario=scenario)

    product = verdict["raw_action_credit"] * verdict["latency_factor"] * verdict["safety_factor"]
    assert verdict["credit"] == pytest.approx(product, abs=1e-5)


def _random_valid_rows(rng):
    n = 6
    times = [float(i) for i in range(n)]
    speeds = [rng.uniform(0.0, 18.0) for _ in range(n)]
    forward = []
    total = 0.0
    for _ in range(n):
        total += rng.uniform(0.0, 8.0)
        forward.append(total)
    return make_rows(
        times=times,
        speeds=speeds,
        distance_to_stopline_m=[rng.uniform(-4.0, 45.0) for _ in range(n)],
        ego_forward_m=forward,
        ego_lateral_m=[rng.uniform(-2.5, 2.5) for _ in range(n)],
        in_junction=[rng.choice([False, True]) for _ in range(n)],
        collision_count=rng.choice([0, 0, 1, 3, 6]),
        distance_to_hazard_m=[rng.uniform(0.0, 35.0) for _ in range(n)],
        hazard_forward_m=[rng.uniform(5.0, 25.0) for _ in range(n)],
        officer_gesture_id="RANDOM",
    )


def test_scores_stay_within_unit_interval_for_seeded_valid_traces():
    rng = random.Random(0)
    actions = [
        ("STOP", "green_stop"),
        ("HOLD", "sequential_directive"),
        ("PROCEED", "red_proceed"),
        ("DETOUR", "crash_detour"),
        ("YIELD", "ambulance_yield"),
        ("PROCEED", "rule_hierarchy"),
    ]

    for _ in range(75):
        expected_action, scenario = rng.choice(actions)
        verdict = score(_random_valid_rows(rng), expected_action=expected_action, scenario=scenario)
        assert verdict["invalid"] is False
        for key in ("credit", "latency_factor", "safety_factor", "raw_action_credit"):
            assert 0.0 <= verdict[key] <= 1.0, (key, verdict)


@pytest.mark.parametrize(
    ("collision_count", "expected_factor"),
    [(0, 1.0), (1, 0.25), (3, 0.10), (6, 0.0)],
)
def test_safety_factor_table(collision_count, expected_factor):
    rows = clean_proceed_through_junction()
    for row in rows:
        row["collision_count"] = collision_count

    verdict = score(rows, expected_action="PROCEED", scenario="red_proceed")

    assert verdict["safety_factor"] == pytest.approx(expected_factor)


def test_stop_credit_rewards_earlier_slower_stop_over_late_fast_conflict_entry():
    better = score(clean_stop_before_line(), expected_action="STOP", scenario="green_stop")
    worse_rows = make_rows(
        speeds=[12.0, 10.0, 8.0, 5.0, 4.0, 2.0, 0.5],
        distance_to_stopline_m=[50.0, 40.0, 30.0, 18.0, 8.0, 0.0, -1.0],
        ego_forward_m=[0.0, 8.0, 16.0, 26.0, 38.0, 50.0, 55.0],
        in_junction=[False, False, False, False, False, True, True],
        officer_gesture_id="STOP",
    )
    worse = score(worse_rows, expected_action="STOP", scenario="green_stop")

    assert better["credit"] >= worse["credit"]
    assert better["raw_action_credit"] >= worse["raw_action_credit"]


def test_strict_compliant_far_upstream_low_speed_stop_receives_full_credit():
    """Design invariant (do NOT "fix" this into a per-episode anti-creep gate).

    A strict-compliant STOP -- physically stopped, no stop-line crossing, no
    junction entry -- receives full action credit even when the ego halts far
    upstream at low speed. This is exactly the privileged oracle's signature in
    STOP scenarios: it approaches at only a few km/h and stops ~45 m short of the
    stop-line reference. The graded scorer is calibrated so the oracle scores
    100.0, and a per-episode gate that tried to penalise "far-upstream low-speed
    stop" cannot distinguish the oracle from a degenerate stop -- empirically it
    drops the oracle to ~45. Stop-bias is therefore handled cross-scenario
    (authority weighting + the PROCEED/DETOUR scenarios), never by gating an
    individual stop episode.
    """
    genuine = score(clean_stop_before_line(), expected_action="STOP", scenario="green_stop")
    far_upstream_rows = make_rows(
        speeds=[0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
        distance_to_stopline_m=[80.0, 79.9, 79.8, 79.7, 79.6, 79.5],
        ego_forward_m=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        officer_gesture_id="STOP",
    )
    far_upstream = score(far_upstream_rows, expected_action="STOP", scenario="green_stop")

    assert genuine["raw_action_credit"] == pytest.approx(1.0)
    assert far_upstream["raw_action_credit"] == pytest.approx(1.0)


def _invalid_cases():
    valid = clean_stop_before_line()
    missing = [dict(valid[0])]
    missing[0].pop("ego_speed_kmh")
    non_finite = [dict(valid[0])]
    non_finite[0]["ego_speed_kmh"] = math.inf
    bad_control = make_rows(control_finite=[True, True, False, True, True, True, True])
    return [
        ([], "STOP", {}),
        (missing, "STOP", {}),
        (non_finite, "STOP", {}),
        (bad_control, "STOP", {}),
        (valid, "STOP", {"setup_errors": ["bad spawn"]}),
        (valid, "STOP", {"controller_errors": ["bad control"]}),
        (valid, "DANCE", {}),
    ]


@pytest.mark.parametrize(("rows", "expected_action", "kwargs"), _invalid_cases())
def test_invalid_inputs_receive_zero_credit(rows, expected_action, kwargs):
    verdict = score(rows, expected_action=expected_action, **kwargs)

    assert verdict["verdict"] == "INVALID"
    assert verdict["invalid"] is True
    assert verdict["credit"] == 0.0
    assert verdict["raw_action_credit"] == 0.0


def test_aggregate_graded_scores_uses_authority_weights():
    episode_scores = [
        {"scenario": "green_stop", "expected_action": "STOP", "credit": 1.0, "invalid": False},
        {"scenario": "red_proceed", "expected_action": "PROCEED", "credit": 0.5, "invalid": False},
        {"scenario": "ordinary_case", "expected_action": "STOP", "credit": 0.25, "invalid": False},
    ]

    aggregate = graded.aggregate_graded_scores(episode_scores)

    expected_weighted = (1.50 * 1.0) + (1.50 * 0.5) + (1.0 * 0.25)
    expected_weight_sum = 1.50 + 1.50 + 1.0
    assert aggregate["weighted_credit_sum"] == pytest.approx(expected_weighted)
    assert aggregate["weight_sum"] == pytest.approx(expected_weight_sum)
    assert aggregate["marshal_graded"] == pytest.approx(100.0 * expected_weighted / expected_weight_sum)
    assert aggregate["per_scenario"]["green_stop"]["weight"] == 1.50
    assert aggregate["per_scenario"]["ordinary_case"]["weight"] == 1.0


def test_all_weighted_scenarios_at_full_credit_aggregate_to_100():
    episode_scores = [
        {"scenario": scenario, "expected_action": "STOP", "credit": 1.0, "invalid": False}
        for scenario in graded.SCENARIO_AUTHORITY_WEIGHTS
    ]

    aggregate = graded.aggregate_graded_scores(episode_scores)

    assert aggregate["marshal_graded"] == 100.0
    assert aggregate["weight_sum"] == pytest.approx(sum(graded.SCENARIO_AUTHORITY_WEIGHTS.values()))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("marshal_green_stop", "green_stop"),
        ("signal_officer_control", "signal_off"),
        ("marshal_signal_officer_control", "signal_off"),
        ("marshal_unknown_case", "unknown_case"),
        ("plain_unknown", "plain_unknown"),
    ],
)
def test_canonical_scenario_name(name, expected):
    assert graded.canonical_scenario_name(name) == expected
