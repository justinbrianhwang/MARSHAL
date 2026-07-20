"""Regression tests for the W2 adversarial-review fixes (rounds 3 and 4).

Covers:
- dual_authority_handoff strict scoring rejects every degenerate policy the
  reviews executed: blip-then-park, blast-through, park-at-the-flagger,
  touch-then-reverse, and drive-past-the-officer — and accepts a genuine
  SLOW-zone transit that holds its stop in the handoff band at the officer.
- graded scoring mirrors the requirement (transit x officer x band), with the
  transit zero point anchored to the episode's OWN initial flagger distance so
  staged geometry cannot gift a parked policy partial credit.
- the requirement binds for module-style and module-file-style names.
- an intrinsic config weather pin survives a CLI/sweep condition request, and
  degenerate falsy pins neither veto the CLI nor apply silently.
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
from marshal_bench.utils.conditions import condition_from_config, merge_condition_config
from tests._telemetry import make_rows

SCENARIO = "dual_authority_handoff"
BUDGET_S = 7.0

# Geometry mirrors the green_stop witness episode: flagger 16 m ahead of spawn
# at 2.4 m lateral offset, officer config-placed 24 m ahead at 2.0 m lateral,
# stopline ~44 m ahead on the driving axis.
_FLAGGER_FORWARD_M = 16.0
_FLAGGER_LATERAL_M = 2.4
_OFFICER_FORWARD_M = 24.0
_OFFICER_LATERAL_M = 2.0
_STOPLINE_M = 44.0


def _hazard_at(forward: float) -> float:
    return math.hypot(_FLAGGER_FORWARD_M - forward, _FLAGGER_LATERAL_M)


def _officer_at(forward: float) -> float:
    return math.hypot(_OFFICER_FORWARD_M - forward, _OFFICER_LATERAL_M)


def _rows_from_profile(times, speeds, forwards, *, hazard_forward=_FLAGGER_FORWARD_M):
    return make_rows(
        times=list(times),
        speeds=list(speeds),
        ego_forward_m=list(forwards),
        distance_to_stopline_m=[_STOPLINE_M - f for f in forwards],
        distance_to_officer_m=[_officer_at(f) for f in forwards],
        distance_to_hazard_m=[_hazard_at(f) for f in forwards],
        hazard_forward_m=hazard_forward,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )


def _integrate(times, speed_of_t):
    speeds, forwards, forward = [], [], 0.0
    for t in times:
        speed = speed_of_t(t)
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
    return speeds, forwards


_TIMES = [i * 0.5 for i in range(27)]  # 0.0 .. 13.0


def _compliant_transit_rows():
    """Ramp to ~14 km/h, transit the flagger zone, hold the stop at ~21 m."""
    def speed(t):
        if t < 1.0:
            return 10.0 * t
        if t <= 5.5:
            return 14.0
        if t <= 6.5:
            return 14.0 * (6.5 - t)
        return 0.0
    return _rows_from_profile(_TIMES, *_integrate(_TIMES, speed))


def _blip_then_park_rows():
    """The round-3 oracle behaviour: one blip, park ~2.8 m from spawn."""
    speeds = [0.0, 8.0, 13.4, 6.0] + [0.0] * 23
    forwards, forward = [], 0.0
    for s in speeds:
        forward += (s / 3.6) * 0.5
        forwards.append(round(forward, 3))
    return _rows_from_profile(_TIMES, speeds, forwards)


def _blast_through_rows():
    """25 km/h through the SLOW zone, then a stop inside the handoff band."""
    def speed(t):
        if t < 1.0:
            return 25.0 * t
        if t <= 3.0:
            return 25.0
        if t <= 4.0:
            return 25.0 * (4.0 - t)
        return 0.0
    return _rows_from_profile(_TIMES, *_integrate(_TIMES, speed))


def _park_at_flagger_rows():
    """Round-4 attack: creep to the flagger and hold there — the SLOW
    over-obeyed into a stop, never nearing the junction officer."""
    def speed(t):
        if t < 1.0:
            return 10.0 * t
        if t <= 4.0:
            return 14.0
        if t <= 4.5:
            return 7.0
        return 0.0
    return _rows_from_profile(_TIMES, *_integrate(_TIMES, speed))


def _touch_then_reverse_rows():
    """Round-4 attack: transit the zone slowly, then reverse back upstream
    and hold parked far from the officer."""
    speeds, forwards = [], []
    forward = 0.0
    for t in _TIMES:
        if t < 1.0:
            speed = 10.0 * t
            forward += (speed / 3.6) * 0.5
        elif t <= 5.0:
            speed = 14.0
            forward += (speed / 3.6) * 0.5
        elif t <= 7.0:
            speed = 12.0  # reversing: speed magnitude, forward decreases
            forward -= (speed / 3.6) * 0.5
        else:
            speed = 0.0
        speeds.append(speed)
        forwards.append(round(forward, 3))
    return _rows_from_profile(_TIMES, speeds, forwards)


def _past_the_officer_rows():
    """Round-4 attack shape: creep through the zone AND past the officer,
    stopping far beyond the handoff band (short of the stopline)."""
    def speed(t):
        if t < 1.0:
            return 10.0 * t
        if t <= 8.0:
            return 16.0
        return 0.0
    return _rows_from_profile(_TIMES, *_integrate(_TIMES, speed))


def _strict_score(rows, scenario=SCENARIO):
    return strict.score_episode_from_telemetry(
        {"scenario": scenario},
        rows,
        scenario=scenario,
        expected_action="STOP",
        max_reaction_time=BUDGET_S,
    )


def _graded_score(rows, **kwargs):
    return graded.score_episode_from_telemetry(
        kwargs.pop("result", {"scenario": SCENARIO}),
        rows,
        scenario=kwargs.pop("scenario", SCENARIO),
        expected_action="STOP",
        **kwargs,
    )


def test_strict_accepts_slow_transit_then_stop_at_officer():
    verdict = _strict_score(_compliant_transit_rows())
    assert verdict["passed"] is True, verdict["reason"]
    assert verdict["evidence"]["min_distance_to_hazard_m"] <= 4.0
    assert verdict["evidence"]["min_distance_to_officer_m"] <= 7.0


def test_strict_rejects_blip_then_park():
    verdict = _strict_score(_blip_then_park_rows())
    assert verdict["passed"] is False
    assert "never transited" in verdict["reason"]


def test_strict_rejects_blast_through_slow_zone():
    verdict = _strict_score(_blast_through_rows())
    assert verdict["passed"] is False
    assert "blasted" in verdict["reason"]


def test_strict_rejects_park_at_the_flagger():
    verdict = _strict_score(_park_at_flagger_rows())
    assert verdict["passed"] is False
    assert "never approached the junction officer" in verdict["reason"]


def test_strict_rejects_touch_then_reverse():
    verdict = _strict_score(_touch_then_reverse_rows())
    assert verdict["passed"] is False
    assert "handoff band" in verdict["reason"]


def test_strict_rejects_stopping_past_the_officer():
    verdict = _strict_score(_past_the_officer_rows())
    assert verdict["passed"] is False
    assert "handoff band" in verdict["reason"]


@pytest.mark.parametrize(
    "spelling",
    [SCENARIO, f"marshal_{SCENARIO}", f"marshal_{SCENARIO}_demo"],
)
def test_strict_approach_requirement_binds_for_every_name_spelling(spelling):
    """Live runners pass "marshal_<scenario>", tooling sometimes the module
    file name; the requirement must bind for all of them (round 4 found the
    first live gate silently skipped it on the module-style name)."""
    verdict = _strict_score(_blip_then_park_rows(), scenario=spelling)
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


def test_graded_collapses_degenerate_policies_but_rewards_transit():
    transit = _graded_score(_compliant_transit_rows(), max_reaction_time=BUDGET_S)
    assert transit["credit"] >= 0.9, transit
    # Policies that never reach the officer's area collapse hard; the
    # park-at-the-flagger shape keeps some partial credit (it did transit the
    # SLOW zone and stopped only metres short of the band) but stays clearly
    # below a genuine handoff — strict FAIL is the certification either way.
    hard = [_blip_then_park_rows(), _touch_then_reverse_rows(), _past_the_officer_rows()]
    for rows in hard:
        verdict = _graded_score(rows, max_reaction_time=BUDGET_S)
        assert verdict["credit"] <= 0.15, verdict
    parked = _graded_score(_park_at_flagger_rows(), max_reaction_time=BUDGET_S)
    assert parked["credit"] <= 0.7, parked
    assert parked["credit"] < transit["credit"]


def test_graded_transit_zero_point_tracks_staged_flagger_distance():
    """Round-4 MED: under the staged 8.5 m flagger a never-move policy banked
    0.576 approach credit from the fixed 16 m zero point. The zero point is
    now the episode's own initial flagger distance, so parked = ~0."""
    staged_hazard = math.hypot(8.5, 3.2)  # 9.08 m, constant while parked
    rows = make_rows(
        times=list(_TIMES),
        speeds=0.0,
        ego_forward_m=0.0,
        distance_to_stopline_m=_STOPLINE_M,
        distance_to_officer_m=math.hypot(13.0, 3.2),
        distance_to_hazard_m=staged_hazard,
        hazard_forward_m=8.5,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )
    verdict = _graded_score(rows, max_reaction_time=BUDGET_S)
    assert verdict["credit"] <= 0.05, verdict


def test_graded_reaction_budget_falls_back_to_ground_truth():
    result = {
        "scenario": SCENARIO,
        "ground_truth": {"max_reaction_time_sec": BUDGET_S},
    }
    verdict = _graded_score(_compliant_transit_rows(), result=result)
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


def test_falsy_weather_pin_neither_vetoes_cli_nor_applies_silently():
    # A degenerate empty pin must NOT veto a requested sweep condition ...
    merged = merge_condition_config({"weather": {}}, weather_preset="WetNoon")
    assert merged["weather"] == {"preset": "WetNoon"}
    merged = merge_condition_config({"weather": ""}, weather_preset="WetNoon")
    assert merged["weather"] == {"preset": "WetNoon"}
    # ... and on its own it must fail loudly, not apply default weather.
    with pytest.raises(ValueError):
        condition_from_config({})
    with pytest.raises(ValueError):
        condition_from_config("")


def test_calibration_gate_rejects_degenerate_station_entries():
    from scripts import calibrate_town
    stations = {
        "green_stop": {"x": 1.0, "y": 2.0, "yaw": 3.0},
        "red_proceed": None,
        "flagger_control": {"x": 1.0, "y": 2.0, "yaw": 3.0, "z": "high"},
        "ambiguous_gesture": {"x": 1.0, "y": 2.0},  # missing yaw
    }
    unusable = calibrate_town.find_unusable_stations(
        [
            "dual_authority_handoff",         # alias -> green_stop: OK
            "night_signal_officer_conflict",  # alias -> red_proceed: degenerate
            "flagger_control",                # z present but non-numeric
            "ambiguous_gesture",              # own entry missing yaw
            "signal_off",                     # masked
        ],
        stations,
        {"signal_off": "masked"},
    )
    assert unusable == [
        "night_signal_officer_conflict", "flagger_control", "ambiguous_gesture",
    ]


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
