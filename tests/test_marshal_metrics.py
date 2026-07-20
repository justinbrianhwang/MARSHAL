from dataclasses import fields

import pytest

from marshal_bench.criteria import marshal_metrics as mm


METRIC_NAMES = sorted(mm.METRIC_TO_R)


def result_for(
    scenario,
    *,
    passed=True,
    collision_count=0,
    crossed_stop_line=False,
    authority_valid=True,
    traffic_light_state="Green",
    latency_detected=True,
    latency=1.25,
    target_relation="ego",
):
    return {
        "episode_id": f"episode-{scenario}",
        "scenario": scenario,
        "strict_scoring": {
            "passed": passed,
            "verdict": "PASS" if passed else "FAIL",
            "invalid": False,
            "collision_count": collision_count,
        },
        "compliance": {
            "passed": passed,
            "collision": bool(collision_count),
            "crossed_stop_line": crossed_stop_line,
        },
        "latency": {
            "detected": latency_detected,
            "latency": latency if latency_detected else None,
        },
        "officer_metadata": {
            "authority_valid": authority_valid,
            "target_relation": target_relation,
        },
        "traffic_light_state": traffic_light_state,
    }


def metric_value(metrics, metric_name):
    return getattr(metrics, metric_name.lower())


@pytest.mark.parametrize("scenario", sorted(mm.SCENARIO_SPEC))
def test_applicability_matches_scenario_spec(scenario):
    episode = result_for(scenario, passed=True, traffic_light_state="Red")

    metrics = mm.compute_episode_metrics(episode, scenario=scenario)

    applicable = mm.SCENARIO_SPEC[scenario]["metrics"]
    for metric_name in METRIC_NAMES:
        value = metric_value(metrics, metric_name)
        if metric_name in applicable:
            assert value is not None, (scenario, metric_name)
        else:
            assert value is None, (scenario, metric_name, value)


@pytest.mark.parametrize("metric_name", ["AOC", "FOA", "APR", "DRM", "OCC"])
def test_passed_maps_to_pass_conditioned_metrics(metric_name):
    scenario = next(
        name for name, spec in mm.SCENARIO_SPEC.items() if metric_name in spec["metrics"]
    )

    passed = mm.compute_episode_metrics(result_for(scenario, passed=True), scenario=scenario)
    failed = mm.compute_episode_metrics(result_for(scenario, passed=False), scenario=scenario)

    assert metric_value(passed, metric_name) == 1.0
    assert metric_value(failed, metric_name) == 0.0


@pytest.mark.parametrize(
    ("metric_name", "scenario"),
    [
        ("SBO", "green_stop"),
        ("RHC", "rule_hierarchy"),
        ("AGI", "ambiguous_gesture"),
    ],
)
def test_collision_zeroes_safety_bounded_metrics_even_when_passed(metric_name, scenario):
    clean = mm.compute_episode_metrics(
        result_for(scenario, passed=True, collision_count=0),
        scenario=scenario,
    )
    collided = mm.compute_episode_metrics(
        result_for(scenario, passed=True, collision_count=1),
        scenario=scenario,
    )
    failed = mm.compute_episode_metrics(
        result_for(scenario, passed=False, collision_count=0),
        scenario=scenario,
    )

    assert metric_value(clean, metric_name) == 1.0
    assert metric_value(collided, metric_name) == 0.0
    assert metric_value(failed, metric_name) == 0.0


def test_taa_uses_explicit_target_prediction_when_available():
    episode = result_for(
        "adjacent_lane",
        passed=True,
        target_relation="adjacent_lane",
    )

    correct = mm.compute_episode_metrics(episode, scenario="adjacent_lane", target_pred="adjacent_lane")
    wrong = mm.compute_episode_metrics(episode, scenario="adjacent_lane", target_pred="ego")

    assert correct.taa == 1.0
    assert wrong.taa == 0.0


def test_cri_crossing_is_not_an_infraction_when_red_authority_proceed_is_justified():
    episode = result_for(
        "red_proceed",
        crossed_stop_line=True,
        authority_valid=True,
        traffic_light_state="Red",
    )

    metrics = mm.compute_episode_metrics(episode, scenario="red_proceed")

    assert metrics.cri == 0.0


@pytest.mark.parametrize(
    ("scenario", "authority_valid", "traffic_light_state"),
    [
        ("red_proceed", False, "Red"),
        ("red_proceed", True, "Green"),
        ("unauthorized_go", True, "Red"),
    ],
)
def test_cri_crossing_otherwise_counts_as_infraction(scenario, authority_valid, traffic_light_state):
    episode = result_for(
        scenario,
        crossed_stop_line=True,
        authority_valid=authority_valid,
        traffic_light_state=traffic_light_state,
    )

    metrics = mm.compute_episode_metrics(episode, scenario=scenario)

    assert metrics.cri == 1.0


def test_rtl_is_reported_when_detected_and_none_when_missing():
    detected = mm.compute_episode_metrics(
        result_for("green_stop", latency_detected=True, latency=2.75),
        scenario="green_stop",
    )
    missing = mm.compute_episode_metrics(
        result_for("green_stop", latency_detected=False),
        scenario="green_stop",
    )

    assert detected.rtl == 2.75
    assert missing.rtl is None
    assert "RTL:no_reaction_detected" in missing.notes


def test_compute_comfort_constant_speed_is_full_credit():
    rows = [
        {"sim_time": 0.0, "ego_speed_kmh": 36.0},
        {"sim_time": 0.1, "ego_speed_kmh": 36.0},
        {"sim_time": 0.2, "ego_speed_kmh": 36.0},
        {"sim_time": 0.3, "ego_speed_kmh": 36.0},
    ]

    assert mm.compute_comfort(rows) == pytest.approx(1.0)


def test_compute_comfort_hard_brake_loses_brake_and_jerk_credit():
    rows = [
        {"sim_time": 0.0, "ego_speed_kmh": 40.0},
        {"sim_time": 0.1, "ego_speed_kmh": 0.0},
        {"sim_time": 0.2, "ego_speed_kmh": 0.0},
    ]

    cmf = mm.compute_comfort(rows)

    assert cmf == pytest.approx(0.25)
    assert cmf < 0.6


def test_compute_comfort_jerky_oscillation_loses_jerk_credit():
    rows = [
        {"sim_time": 0.0, "ego_speed_kmh": 36.0},
        {"sim_time": 0.1, "ego_speed_kmh": 36.9},
        {"sim_time": 0.2, "ego_speed_kmh": 36.0},
        {"sim_time": 0.3, "ego_speed_kmh": 36.9},
    ]

    assert mm.compute_comfort(rows) == pytest.approx(0.5)


def test_compute_comfort_requires_three_finite_rows():
    assert mm.compute_comfort([
        {"sim_time": 0.0, "ego_speed_kmh": 36.0},
        {"sim_time": 0.1, "ego_speed_kmh": 36.0},
    ]) is None
    assert mm.compute_comfort([
        {"sim_time": 0.0, "ego_speed_kmh": 36.0},
        {"sim_time": 0.1, "ego_speed_kmh": float("nan")},
        {"sim_time": 0.2, "ego_speed_kmh": 36.0},
    ]) is None


def test_compute_lane_consistency_holding_one_lane_is_full_credit():
    rows = [
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
    ]

    assert mm.compute_lane_consistency(rows) == pytest.approx(1.0)


def test_compute_lane_consistency_penalizes_three_same_road_lane_changes():
    rows = [
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": 2, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": 2, "ego_road_id": 10, "in_junction": False},
    ]

    assert mm.compute_lane_consistency(rows) == pytest.approx(0.4)


def test_compute_lane_consistency_ignores_lane_changes_across_road_ids():
    rows = [
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": 2, "ego_road_id": 11, "in_junction": False},
        {"ego_lane_id": 3, "ego_road_id": 12, "in_junction": False},
    ]

    assert mm.compute_lane_consistency(rows) == pytest.approx(1.0)


def test_compute_lane_consistency_ignores_junction_rows():
    rows = [
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": 2, "ego_road_id": 10, "in_junction": True},
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
    ]

    assert mm.compute_lane_consistency(rows) == pytest.approx(1.0)


def test_compute_lane_consistency_requires_two_valid_rows():
    rows = [
        {"ego_lane_id": 1, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": None, "ego_road_id": 10, "in_junction": False},
        {"ego_lane_id": 2, "ego_road_id": 10, "in_junction": True},
    ]

    assert mm.compute_lane_consistency(rows) is None


def test_compute_pedestrian_safety_full_credit_when_slows_near_pedestrian():
    rows = [
        {"ego_speed_kmh": 20.0, "distance_to_pedestrian_m": 12.0},
        {"ego_speed_kmh": 5.0, "distance_to_pedestrian_m": 9.0},
        {"ego_speed_kmh": 3.0, "distance_to_pedestrian_m": 8.0},
    ]

    assert mm.compute_pedestrian_safety(rows) == pytest.approx(1.0)


def test_compute_pedestrian_safety_zero_credit_when_fast_near_pedestrian():
    rows = [
        {"ego_speed_kmh": 18.0, "distance_to_pedestrian_m": 9.0},
        {"ego_speed_kmh": 15.0, "distance_to_pedestrian_m": 8.0},
    ]

    assert mm.compute_pedestrian_safety(rows) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "rows",
    [
        [
            {"ego_speed_kmh": 20.0, "distance_to_pedestrian_m": 12.0},
            {"ego_speed_kmh": 18.0, "distance_to_pedestrian_m": 11.0},
        ],
        [
            {"ego_speed_kmh": 20.0, "distance_to_pedestrian_m": None},
            {"ego_speed_kmh": 18.0},
        ],
    ],
)
def test_compute_pedestrian_safety_returns_none_without_close_pedestrian(rows):
    assert mm.compute_pedestrian_safety(rows) is None


def test_compute_episode_metrics_populates_telemetry_metrics_only_with_rows():
    rows = [
        {
            "sim_time": 0.0,
            "ego_speed_kmh": 3.0,
            "ego_lane_id": 1,
            "ego_road_id": 10,
            "in_junction": False,
            "distance_to_pedestrian_m": 9.0,
        },
        {
            "sim_time": 0.1,
            "ego_speed_kmh": 3.0,
            "ego_lane_id": 1,
            "ego_road_id": 10,
            "in_junction": False,
            "distance_to_pedestrian_m": 9.0,
        },
        {
            "sim_time": 0.2,
            "ego_speed_kmh": 3.0,
            "ego_lane_id": 1,
            "ego_road_id": 10,
            "in_junction": False,
            "distance_to_pedestrian_m": 9.0,
        },
    ]

    with_telemetry = mm.compute_episode_metrics(
        result_for("green_stop"),
        scenario="green_stop",
        telemetry_rows=rows,
    )
    without_telemetry = mm.compute_episode_metrics(
        result_for("green_stop"),
        scenario="green_stop",
    )

    assert with_telemetry.cmf == pytest.approx(1.0)
    assert with_telemetry.lnc == pytest.approx(1.0)
    assert with_telemetry.psi == pytest.approx(1.0)
    assert without_telemetry.cmf is None
    assert without_telemetry.lnc is None
    assert without_telemetry.psi is None


def test_rtl_is_excluded_from_requirement_scores():
    fast = mm.compute_episode_metrics(
        result_for("green_stop", latency_detected=True, latency=0.25),
        scenario="green_stop",
    )
    slow = mm.compute_episode_metrics(
        result_for("green_stop", latency_detected=True, latency=99.0),
        scenario="green_stop",
    )

    fast_aggregate = mm.aggregate([fast])
    slow_aggregate = mm.aggregate([slow])

    assert fast_aggregate["suite"]["RTL"] != slow_aggregate["suite"]["RTL"]
    assert fast_aggregate["r_scores"] == slow_aggregate["r_scores"]
    assert fast_aggregate["marshal_score_partial"] == slow_aggregate["marshal_score_partial"]


def test_aggregate_means_partial_score_unmeasured_requirements_and_tier_rates():
    episodes = [
        mm.EpisodeMetrics("low-pass", "green_stop", aoc=1.0, sbo=1.0, rtl=2.0, cmf=0.8, passed=True),
        mm.EpisodeMetrics("low-fail", "signal_off", aoc=0.0, sbo=0.0, cmf=0.8, passed=False),
        mm.EpisodeMetrics("mid-pass", "red_proceed", aoc=1.0, sbo=1.0, cri=0.0, rtl=4.0, cmf=0.8, passed=True),
        mm.EpisodeMetrics("mid-fail", "crash_detour", aoc=0.0, sbo=0.0, cmf=0.8, passed=False),
        mm.EpisodeMetrics("high-pass", "occluded_officer", aoc=1.0, occ=1.0, cmf=0.8, passed=True),
        mm.EpisodeMetrics("high-fail", "adjacent_lane", foa=0.0, taa=0.0, cmf=0.8, passed=False),
        mm.EpisodeMetrics("high-pass-2", "ambiguous_gesture", agi=1.0, cmf=0.8, passed=True),
    ]

    aggregate = mm.aggregate(episodes)

    assert aggregate["suite"]["AOC"] == 0.6
    assert aggregate["suite"]["SBO"] == 0.5
    assert aggregate["suite"]["RTL"] == 3.0
    assert aggregate["suite"]["CRI"] == 0.0
    assert aggregate["suite"]["CMF"] == 0.8
    assert aggregate["r_scores"] == {
        "R3": 0.5333,
        "R2": 0.5,
        "R1": 1.0,
        "R5": 0.8,
        "R7": 0.5,
    }
    assert aggregate["r_unmeasured"] == ["R4", "R6", "R8", "R9"]
    # Measured R weights (R1=0.10, R2=0.12, R3=0.28, R5=0.03, R7=0.22)
    # renormalized over their 0.75 sum:
    # 100*(1.0*0.10 + 0.5*0.12 + 0.5333*0.28 + 0.8*0.03 + 0.5*0.22)/0.75.
    assert aggregate["marshal_score_partial"] == 59.11
    assert 0.0 <= aggregate["marshal_score_partial"] <= 100.0
    assert aggregate["tier_pass_rate"] == {
        "low": {"n": 2, "pass_rate": 0.5},
        "mid": {"n": 2, "pass_rate": 0.5},
        "high": {"n": 3, "pass_rate": 0.6667},
    }
    assert aggregate["conflict_type_profile"] == {
        "override": {"passed": 2, "total": 4, "pass_rate": 0.5},
        "stressed-override": {"passed": 2, "total": 3, "pass_rate": 0.6667},
        "validity": {"passed": 0, "total": 0, "pass_rate": 0.0},
        "conflict": {"passed": 0, "total": 0, "pass_rate": 0.0},
        "scene": {"passed": 0, "total": 0, "pass_rate": 0.0},
        "safety": {"passed": 0, "total": 0, "pass_rate": 0.0},
    }
    assert len(aggregate["per_episode"]) == len(episodes)


def test_aggregate_uses_lnc_and_psi_for_r4_and_r8_when_present():
    episodes = [
        mm.EpisodeMetrics(
            "instrumented",
            "occluded_officer",
            aoc=1.0,
            taa=0.75,
            sbo=0.9,
            cmf=0.8,
            lnc=0.4,
            psi=1.0,
            occ=1.0,
            passed=True,
        ),
    ]

    aggregate = mm.aggregate(episodes)

    assert aggregate["suite"]["LNC"] == 0.4
    assert aggregate["suite"]["PSI"] == 1.0
    assert aggregate["r_scores"]["R4"] == 0.4
    assert aggregate["r_scores"]["R8"] == 1.0
    assert aggregate["r_unmeasured"] == ["R6", "R9"]


def test_every_scenario_has_an_authority_weight():
    """Regression guard: every scored scenario must have an explicit graded
    authority weight. The 7 expansion scenarios were originally omitted and
    silently defaulted to 1.0, under-weighting the hardest authority cases in
    the headline MARSHAL-Graded aggregate.
    """
    from marshal_bench.criteria.graded_episode_scoring import SCENARIO_AUTHORITY_WEIGHTS
    missing = sorted(set(mm.SCENARIO_SPEC) - set(SCENARIO_AUTHORITY_WEIGHTS))
    assert missing == [], f"scenarios missing an authority weight: {missing}"


def test_metric_and_scenario_tables_are_internally_consistent():
    episode_fields = {field.name for field in fields(mm.EpisodeMetrics)}

    for metric_name in mm.METRIC_TO_R:
        assert metric_name.lower() in episode_fields

    assert set(mm.CONFLICT_TYPE) == set(mm.SCENARIO_SPEC) == set(mm.REASONING_TIER)
    assert {
        conflict_type: sum(value == conflict_type for value in mm.CONFLICT_TYPE.values())
        for conflict_type in mm.CONFLICT_TYPE_ORDER
    } == {
        "override": 6,
        # +night_signal_officer_conflict (night / low gesture visibility).
        "stressed-override": 6,
        # W1 validity-cell reinforcement: stale_directive_residue (temporal)
        # + out_of_jurisdiction_director (spatial).
        "validity": 5,
        "conflict": 2,
        "scene": 2,
        "safety": 3,
    }
    assert set(mm.R_WEIGHTS) == {f"R{i}" for i in range(1, 10)}
    assert sum(mm.R_WEIGHTS.values()) == pytest.approx(1.0)
