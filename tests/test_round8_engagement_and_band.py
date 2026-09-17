"""Round 8 pins: graded credit inherits strict's approach-evidence arm, and
the engagement band is physics-derived.

Decision source: FORMULATION_2026-08-12.md section 7 (adopted):
  1. no engagement -> graded 0 (a vehicle that never moves earns nothing,
     even when it happens to sit inside the engagement radius);
  2. engaged but outside the acceptable region -> partial credit stays;
  4. engagement distance derives from stopping physics
     d = v*t_r + v^2/(2a) instead of a hand-set constant.

The negative cases deliberately run PAST the enforcement deadline
(onset 1.0 s + 3.0 s budget) with settled sub-1 km/h telemetry inside the
old engagement radius: under the round-7 scorer both satisfied the
"strict-compliant" shortcut and scored credit 1.0 (this is the measured
41-cell leaderboard leak), so these tests fail on the round-7 scorer and
pin the round-8 surgery.
"""
from marshal_bench.criteria import graded_episode_scoring as graded
from marshal_bench.criteria.strict_episode_scoring import (
    STRICT_THRESHOLDS,
    engagement_band_m,
)
from tests._telemetry import clean_stop_before_line, make_rows


def _score(rows, expected_action="STOP", scenario="unit"):
    return graded.score_episode_from_telemetry(
        {"scenario": scenario, "expected_action": expected_action},
        rows,
        scenario=scenario,
        expected_action=expected_action,
    )


def _times(n):
    return [0.05 * (i + 1) for i in range(n)]


def test_stationary_vehicle_inside_band_gets_zero_credit():
    # The round-7 leak: parked at spawn 9.8 m from the line (inside the
    # engagement radius) with zero movement, recorded well past the
    # enforcement deadline -> the old strict-compliant shortcut certified
    # this at credit 1.0 while strict said "stationary ego never engaged".
    # Round 8: credit must be 0.
    n = 140  # 7.0 s >> onset(1.0) + budget(3.0)
    rows = make_rows(
        times=_times(n),
        speeds=[0.0] * n,
        ego_forward_m=[0.0] * n,
        distance_to_stopline_m=[9.8] * n,
        stopline_forward_m=[9.8] * n,
        officer_onset_time=1.0,
    )
    result = _score(rows)
    assert result["credit"] == 0.0


def test_slow_creep_below_engagement_speed_gets_zero_credit():
    # AIM-class behavior: 1.3 m of creep below walking pace, then settled
    # to a standstill before the deadline and held through the window.
    # Old scorer: strict-compliant shortcut -> 1.0. Round 8: no approach
    # evidence (needs >=5 km/h AND >=1 m) -> 0.
    n = 140
    speeds, fwd = [], []
    for i in range(n):
        t = 0.05 * (i + 1)
        if t < 2.0:
            speeds.append(min(4.0, 4.0 * t / 2.0))
        elif t < 3.5:
            speeds.append(max(0.0, 4.0 * (3.5 - t) / 1.5))
        else:
            speeds.append(0.0)
        fwd.append(min(1.3, 0.65 * max(0.0, t - 0.5)))
    rows = make_rows(
        times=_times(n),
        speeds=speeds,
        ego_forward_m=fwd,
        distance_to_stopline_m=[9.8 - f for f in fwd],
        stopline_forward_m=[9.8 - f for f in fwd],
        officer_onset_time=1.0,
    )
    result = _score(rows)
    assert result["credit"] == 0.0


def test_engaged_rolling_stop_keeps_partial_credit():
    # Formulation decision 2: engaged (>=5 km/h and >=1 m) but outside the
    # acceptable region (never settles) keeps PARTIAL credit — the movement
    # gate must not zero genuinely engaged near-misses.
    n = 140
    speeds, fwd, cum = [], [], 0.0
    for i in range(n):
        t = 0.05 * (i + 1)
        v = 6.0 if t < 3.0 else 2.5  # rolls, never settles below 1 km/h
        speeds.append(v)
        cum += (v / 3.6) * 0.05
        fwd.append(cum)
    rows = make_rows(
        times=_times(n),
        speeds=speeds,
        ego_forward_m=fwd,
        distance_to_stopline_m=[max(0.5, 9.8 - f) for f in fwd],
        stopline_forward_m=[max(0.5, 9.8 - f) for f in fwd],
        officer_onset_time=1.0,
    )
    result = _score(rows)
    assert result["credit"] > 0.0


def test_clean_approach_and_stop_keeps_full_credit():
    result = _score(clean_stop_before_line())
    assert result["credit"] == 1.0


def test_engagement_band_is_physics_derived_and_monotonic():
    v = 25.0 / 3.6
    expected = v * 1.0 + v * v / (2.0 * 2.5)
    assert abs(engagement_band_m(25.0) - expected) < 1e-9
    assert STRICT_THRESHOLDS["stopline_engagement_m"] == round(expected, 2)
    assert STRICT_THRESHOLDS["hazard_engagement_m"] == round(expected, 2)
    assert engagement_band_m(30.0) > engagement_band_m(25.0) > engagement_band_m(15.0)
