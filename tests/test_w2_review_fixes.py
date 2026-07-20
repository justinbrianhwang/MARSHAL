"""Regression tests for the W2 adversarial-review fixes.

Covers:
- dual_authority_handoff strict scoring rejects blip-then-park and
  blast-through policies, and accepts a genuine SLOW-zone transit + stop.
- graded scoring mirrors the approach requirement (blip-then-park collapses).
- an intrinsic config weather pin survives a CLI/sweep condition request.
- the calibration gate rejects degenerate station entries (alias-aware).
- Town03 own entries for alias-reusing scenarios cannot drift from their
  alias target's pose.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from marshal_bench.criteria import graded_episode_scoring as graded
from marshal_bench.criteria import strict_episode_scoring as strict
from marshal_bench.scenarios._common import STATION_ALIASES
from marshal_bench.utils.conditions import merge_condition_config
from tests._telemetry import make_rows

SCENARIO = "dual_authority_handoff"
BUDGET_S = 6.0

# Geometry mirrors the curated Town03 episode: flagger ~16 m ahead of spawn at
# 2.4 m lateral offset, officer ~20.7 m ahead, stopline ~34 m ahead.
_FLAGGER_FORWARD_M = 16.0
_FLAGGER_LATERAL_M = 2.4
_STOPLINE_M = 34.0
_OFFICER_M = 20.65


def _hazard_at(forward: float) -> float:
    return math.hypot(_FLAGGER_FORWARD_M - forward, _FLAGGER_LATERAL_M)


def _rows_from_profile(times, speeds, forwards):
    return make_rows(
        times=list(times),
        speeds=list(speeds),
        ego_forward_m=list(forwards),
        distance_to_stopline_m=[_STOPLINE_M - f for f in forwards],
        distance_to_officer_m=[max(_OFFICER_M - f, 0.5) for f in forwards],
        distance_to_hazard_m=[_hazard_at(f) for f in forwards],
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )


def _compliant_transit_rows():
    """Accelerate to ~14 km/h, transit the flagger zone, stop by ~6 s, hold."""
    times = [i * 0.5 for i in range(27)]  # 0.0 .. 13.0
    speeds, forwards = [], []
    forward = 0.0
    for t in times:
        if t < 1.0:
            speed = 10.0 * t
        elif t <= 5.0:
            speed = 14.0
        elif t <= 6.0:
            speed = max(0.0, 14.0 * (6.0 - t))
        else:
            speed = 0.0
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
    return _rows_from_profile(times, speeds, forwards)


def _blip_then_park_rows():
    """The pre-fix oracle behaviour: one blip, park ~2.8 m from spawn."""
    times = [i * 0.5 for i in range(27)]
    speeds = [0.0, 8.0, 13.4, 6.0] + [0.0] * 23
    forwards, forward = [], 0.0
    for speed in speeds:
        forward += (speed / 3.6) * 0.5
        forwards.append(round(forward, 3))
    return _rows_from_profile(times, speeds, forwards)


def _blast_through_rows():
    """Full-speed run through the SLOW zone, then a hard stop short of the line."""
    times = [i * 0.5 for i in range(27)]
    speeds, forwards = [], []
    forward = 0.0
    for t in times:
        if t < 1.0:
            speed = 25.0 * t
        elif t <= 3.0:
            speed = 25.0
        elif t <= 4.0:
            speed = max(0.0, 25.0 * (4.0 - t))
        else:
            speed = 0.0
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
    return _rows_from_profile(times, speeds, forwards)


def _strict_score(rows):
    return strict.score_episode_from_telemetry(
        {"scenario": SCENARIO},
        rows,
        scenario=SCENARIO,
        expected_action="STOP",
        max_reaction_time=BUDGET_S,
    )


def test_strict_rejects_blip_then_park():
    verdict = _strict_score(_blip_then_park_rows())
    assert verdict["passed"] is False
    assert "never transited" in verdict["reason"]


def test_strict_rejects_blast_through_slow_zone():
    verdict = _strict_score(_blast_through_rows())
    assert verdict["passed"] is False
    assert "blasted" in verdict["reason"]


def test_strict_accepts_slow_transit_then_stop():
    verdict = _strict_score(_compliant_transit_rows())
    assert verdict["passed"] is True, verdict["reason"]
    assert verdict["evidence"]["min_distance_to_hazard_m"] <= 4.0


def test_strict_approach_requirement_applies_to_module_style_name():
    """Live runners pass "marshal_<scenario>"; the requirement must still bind.

    Regression: the first live gate after the approach requirement landed
    silently skipped it because the module-style name missed the table key.
    """
    verdict = strict.score_episode_from_telemetry(
        {"scenario": f"marshal_{SCENARIO}"},
        _blip_then_park_rows(),
        scenario=f"marshal_{SCENARIO}",
        expected_action="STOP",
        max_reaction_time=BUDGET_S,
    )
    assert verdict["passed"] is False
    assert "never transited" in verdict["reason"]


def test_strict_other_stop_scenarios_have_no_approach_requirement():
    rows = _blip_then_park_rows()
    verdict = strict.score_episode_from_telemetry(
        {"scenario": "green_stop"},
        rows,
        scenario="green_stop",
        expected_action="STOP",
        max_reaction_time=3.0,
    )
    # green_stop has no staged near-zone authority: the engagement gate is the
    # only motion requirement, so this profile still scores on its own merits.
    assert verdict["passed"] is True, verdict["reason"]


def test_graded_collapses_blip_then_park_but_rewards_transit():
    parked = graded.score_episode_from_telemetry(
        {"scenario": SCENARIO},
        _blip_then_park_rows(),
        scenario=SCENARIO,
        expected_action="STOP",
        max_reaction_time=BUDGET_S,
    )
    transit = graded.score_episode_from_telemetry(
        {"scenario": SCENARIO},
        _compliant_transit_rows(),
        scenario=SCENARIO,
        expected_action="STOP",
        max_reaction_time=BUDGET_S,
    )
    assert parked["credit"] <= 0.35, parked
    assert transit["credit"] >= 0.9, transit
    assert transit["credit"] > parked["credit"]


def test_graded_reaction_budget_falls_back_to_ground_truth():
    result = {
        "scenario": SCENARIO,
        "ground_truth": {"max_reaction_time_sec": BUDGET_S},
    }
    verdict = graded.score_episode_from_telemetry(
        result,
        _compliant_transit_rows(),
        scenario=SCENARIO,
        expected_action="STOP",
    )
    assert verdict["credit"] >= 0.9, verdict


def test_intrinsic_weather_pin_survives_cli_condition():
    cfg = {"weather": "ClearNight"}
    merged = merge_condition_config(cfg, weather_preset="ClearNoon")
    assert merged["weather"] == "ClearNight"
    merged = merge_condition_config(cfg, weather_params={"fog_density": 80.0})
    assert merged["weather"] == "ClearNight"
    # Without a pin the requested condition is added as before.
    plain = merge_condition_config({}, weather_preset="WetNoon")
    assert plain["weather"] == {"preset": "WetNoon"}


def test_calibration_gate_rejects_degenerate_station_entries():
    from scripts import calibrate_town
    stations = {
        "conflicting_authorities": {"x": 1.0, "y": 2.0, "yaw": 3.0},
        "red_proceed": None,
        "green_stop": {"x": 1.0, "y": 2.0},  # missing yaw
    }
    unusable = calibrate_town.find_unusable_stations(
        [
            "dual_authority_handoff",       # alias -> conflicting_authorities: OK
            "night_signal_officer_conflict",  # alias -> red_proceed: degenerate
            "green_stop",                   # own entry missing yaw
            "signal_off",                   # masked
        ],
        stations,
        {"signal_off": "masked"},
    )
    assert unusable == ["night_signal_officer_conflict", "green_stop"]


def test_town03_alias_own_entries_do_not_drift_from_targets():
    path = _REPO_ROOT / "marshal_bench" / "configs" / "stations.json"
    stations = json.loads(path.read_text(encoding="utf-8"))["stations"]
    for scenario, target in STATION_ALIASES.items():
        if scenario not in stations:
            continue
        assert target in stations, (scenario, target)
        for key in ("x", "y", "z", "yaw"):
            assert stations[scenario].get(key) == stations[target].get(key), (
                f"{scenario} own Town03 entry drifted from alias target "
                f"{target} on {key!r}"
            )
