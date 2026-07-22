"""Signed stop-line geometry for the STOP/HOLD conflict-zone gates.

The 2026-07 mega re-sweep exposed a geometry regression: once the stop line
became lane-correct (bcc6627), the ego-lane line at several Town03 stations
sits INSIDE the map's oversized junction polygon, so the euclidean
``in_junction AND distance_to_stopline_m <= 15`` heuristic failed an oracle
correctly holding 4.3 m short of the line. The fix records a signed
``stopline_forward_m`` (route-axis projection; negative once the ego is past
the line) and makes it authoritative when present, while legacy rows keep the
euclidean heuristic so old telemetry re-scores unchanged.
"""

from marshal_bench.criteria import graded_episode_scoring as graded
from marshal_bench.criteria import strict_episode_scoring as strict
from tests._telemetry import make_rows

_TIMES = [i * 0.5 for i in range(27)]  # 0.0 .. 13.0


def _strict(rows, scenario="green_stop", expected_action="STOP"):
    return strict.score_episode_from_telemetry(
        {"scenario": scenario, "expected_action": expected_action},
        rows,
        scenario=scenario,
        expected_action=expected_action,
    )


def _graded(rows, scenario="green_stop", expected_action="STOP"):
    return graded.score_episode_from_telemetry(
        {"scenario": scenario, "expected_action": expected_action},
        rows,
        scenario=scenario,
        expected_action=expected_action,
    )


def _hold_short_profile():
    """Approach at 15 km/h, stop 4.25 m short of the line, hold to the end."""
    speeds, forwards, stopline_fwd, in_junction = [], [], [], []
    forward = 0.0
    for t in _TIMES:
        speed = 15.0 if t <= 1.0 else 0.0
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
        remaining = 10.5 - forward
        stopline_fwd.append(round(remaining, 3))
        # The junction polygon starts ~8 m before the stop line, so the ego is
        # "in junction" for the whole hold despite never crossing the line.
        in_junction.append(remaining < 8.0)
    return speeds, forwards, stopline_fwd, in_junction


def _rows_hold_short(*, signed: bool):
    speeds, forwards, stopline_fwd, in_junction = _hold_short_profile()
    return make_rows(
        times=_TIMES,
        speeds=speeds,
        ego_forward_m=forwards,
        in_junction=in_junction,
        distance_to_stopline_m=[max(v, 0.0) for v in stopline_fwd],
        stopline_forward_m=stopline_fwd if signed else None,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )


def test_signed_hold_short_of_line_inside_junction_polygon_passes():
    verdict = _strict(_rows_hold_short(signed=True))
    assert verdict["passed"] is True, verdict["reason"]


def test_signed_hold_short_earns_full_graded_conflict_credit():
    outcome = _graded(_rows_hold_short(signed=True))
    assert outcome["evidence"]["entered_junction"] is False
    assert outcome["credit"] >= 0.9, outcome


def test_legacy_rows_without_signed_column_keep_euclidean_heuristic():
    # Same trajectory, no signed column: the pre-fix behavior (junction row
    # within 15 m euclidean of the line counts as the zone) must be preserved
    # so historical telemetry re-scores identically.
    verdict = _strict(_rows_hold_short(signed=False))
    assert verdict["passed"] is False
    assert "conflict zone" in verdict["reason"]


def test_signed_lateral_blast_through_fails():
    # Ego crosses the stop line offset ~2.5 m laterally: the euclidean
    # distance never dips under the 1.0 m clearance, so only the signed
    # column can see the crossing.
    speeds, forwards, stopline_fwd, euclid, in_junction = [], [], [], [], []
    forward = 0.0
    for t in _TIMES:
        speed = 25.0
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
        remaining = 7.3 - forward
        stopline_fwd.append(round(remaining, 3))
        euclid.append(round((remaining**2 + 2.5**2) ** 0.5, 3))
        in_junction.append(remaining < 8.0)
    rows = make_rows(
        times=_TIMES,
        speeds=speeds,
        ego_forward_m=forwards,
        ego_lateral_m=2.5,
        in_junction=in_junction,
        distance_to_stopline_m=euclid,
        stopline_forward_m=stopline_fwd,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )
    verdict = _strict(rows)
    assert verdict["passed"] is False
    assert ("conflict zone" in verdict["reason"]) or ("clearance" in verdict["reason"])


def _rows_no_stopline(*, stopline=None):
    """Hazard stop with no light and no officer: stopline telemetry is None.

    ``stopline`` lets the same trajectory carry a far, uncontested stop line
    so the None case can be compared against a clearance-neutral baseline.
    """
    speeds, forwards = [], []
    forward = 0.0
    for t in _TIMES:
        speed = 15.0 if t <= 3.0 else 0.0
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
    return make_rows(
        times=_TIMES,
        speeds=speeds,
        ego_forward_m=forwards,
        in_junction=True,
        distance_to_stopline_m=stopline,
        stopline_forward_m=stopline,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )


def test_no_stopline_telemetry_skips_stopline_anchored_gates():
    verdict = _strict(_rows_no_stopline(), scenario="fallen_person")
    assert verdict["passed"] is True, verdict["reason"]


def test_approach_requirement_scenario_may_stop_past_the_signal_line():
    # dual_authority_handoff commands its stop inside the band past the
    # flagger — beyond the signal's stop line by design. The band/overshoot/
    # hold checks pin the geometry; the line-anchored gates must not veto the
    # scenario's own directive. Reuse the certified compliant-transit profile
    # and overlay a stop line the ego lawfully crosses.
    from tests.test_w2_review_fixes import BUDGET_S, SCENARIO, _compliant_transit_rows

    rows = _compliant_transit_rows()
    for row in rows:
        fwd = row["ego_forward_m"]
        row["stopline_forward_m"] = 10.5 - fwd
        row["in_junction"] = (10.5 - fwd) < 8.0
    verdict = strict.score_episode_from_telemetry(
        {"scenario": SCENARIO},
        rows,
        scenario=SCENARIO,
        expected_action="STOP",
        max_reaction_time=BUDGET_S,
    )
    assert verdict["passed"] is True, verdict["reason"]


def test_no_stopline_telemetry_graded_base_is_speed_only():
    # The clearance term is inapplicable, not zero: the None-stopline episode
    # must earn no less than the identical trajectory scored against a far,
    # clearance-neutral stop line.
    outcome = _graded(_rows_no_stopline(), scenario="fallen_person")
    baseline = _graded(_rows_no_stopline(stopline=30.0), scenario="fallen_person")
    assert outcome["evidence"]["min_distance_to_stopline_m"] is None
    assert outcome["evidence"]["entered_junction"] is False
    assert baseline["credit"] > 0.0, baseline
    assert outcome["credit"] >= baseline["credit"] - 1e-6, (outcome, baseline)
