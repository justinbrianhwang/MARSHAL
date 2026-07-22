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


def test_no_stopline_hazard_overshoot_fails():
    # Adversarial-review hardening: "no stop line" must not mean "no conflict
    # boundary". Swerving past the fallen person and stopping beyond it (no
    # collision) is a violation anchored on hazard_forward_m.
    speeds, forwards = [], []
    forward = 0.0
    for t in _TIMES:
        speed = 15.0 if t <= 3.0 else 0.0
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
    rows = make_rows(
        times=_TIMES,
        speeds=speeds,
        ego_forward_m=forwards,          # reaches ~14.6 m
        hazard_forward_m=10.0,           # hazard sits at 10 m
        distance_to_stopline_m=None,
        stopline_forward_m=None,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )
    verdict = _strict(rows, scenario="fallen_person")
    assert verdict["passed"] is False
    assert "past the hazard" in verdict["reason"]


def test_approach_requirement_lateral_escape_fails():
    # Adversarial-review hardening: satisfying the handoff band's longitudinal
    # coordinate while parked sideways inside the junction must fail.
    from tests.test_w2_review_fixes import BUDGET_S, SCENARIO, _compliant_transit_rows

    rows = _compliant_transit_rows()
    for row in rows:
        if row["sim_time"] >= 8.0:
            row["ego_lateral_m"] = 5.0
    verdict = strict.score_episode_from_telemetry(
        {"scenario": SCENARIO},
        rows,
        scenario=SCENARIO,
        expected_action="STOP",
        max_reaction_time=BUDGET_S,
    )
    assert verdict["passed"] is False
    assert "lateral escape" in verdict["reason"]


def test_signed_dropout_row_in_junction_is_conservative():
    # A junction row that lost its signed sample inside an otherwise-signed
    # episode counts as the conflict zone (the crossing could have happened
    # exactly there).
    speeds, forwards, stopline_fwd, in_junction = _hold_short_profile()
    signed = list(stopline_fwd)
    victim = next(
        i for i, flag in enumerate(in_junction) if flag and _TIMES[i] >= 1.0
    )
    signed[victim] = None
    rows = make_rows(
        times=_TIMES,
        speeds=speeds,
        ego_forward_m=forwards,
        in_junction=in_junction,
        distance_to_stopline_m=[max(v, 0.0) for v in stopline_fwd],
        stopline_forward_m=signed,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )
    verdict = _strict(rows)
    assert verdict["passed"] is False
    assert "conflict zone" in verdict["reason"]


def test_graded_shortcut_denied_when_signed_proves_crossing():
    # Lateral crossing: euclidean minimum stays above the clearance while the
    # signed column goes negative — the strict-compliant engagement shortcut
    # must not fire.
    speeds, forwards, stopline_fwd, euclid = [], [], [], []
    forward = 0.0
    for t in _TIMES:
        speed = 20.0 if t <= 2.0 else 0.0
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
        remaining = 8.0 - forward
        stopline_fwd.append(round(remaining, 3))
        euclid.append(round((remaining**2 + 2.5**2) ** 0.5, 3))
    rows = make_rows(
        times=_TIMES,
        speeds=speeds,
        ego_forward_m=forwards,
        ego_lateral_m=2.5,
        in_junction=False,
        distance_to_stopline_m=euclid,
        stopline_forward_m=stopline_fwd,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )
    outcome = _graded(rows)
    assert outcome["evidence"]["engagement"]["strict_stop_hold_compliant"] is False


def test_no_stopline_blip_then_park_far_upstream_fails():
    # Kimi review demo F: blip to 6 km/h (satisfies the generic engagement
    # gate), park 3.3 m from spawn with the hazard 30 m away. The hazard
    # engagement gate must reject it in strict, and the graded shortcut must
    # not certify it.
    speeds, forwards = [], []
    forward = 0.0
    for t in _TIMES:
        speed = 6.0 if t <= 1.5 else 0.0
        forward += (speed / 3.6) * 0.5
        speeds.append(speed)
        forwards.append(round(forward, 3))
    rows = make_rows(
        times=_TIMES,
        speeds=speeds,
        ego_forward_m=forwards,          # parks ~3.3 m from spawn
        hazard_forward_m=30.0,
        distance_to_stopline_m=None,
        stopline_forward_m=None,
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )
    verdict = _strict(rows, scenario="fallen_person")
    assert verdict["passed"] is False
    assert "never engaged the hazard" in verdict["reason"]
    outcome = _graded(rows, scenario="fallen_person")
    assert outcome["evidence"]["engagement"]["strict_stop_hold_compliant"] is False


def test_handoff_band_yoyo_dip_fails():
    # Kimi review finding 11: enter the band, dip back upstream out of it,
    # then return and settle before the enforcement window. Symmetric to the
    # overshoot rule, the dip itself is a violation.
    from tests.test_w2_review_fixes import BUDGET_S, SCENARIO, _compliant_transit_rows

    rows = _compliant_transit_rows()
    # Compliant profile holds ~21 m (band [17.5, 25.0]). Inject a dip to
    # 14 m at t in [6.5, 7.5] — after first band entry, before enforcement.
    for row in rows:
        if 6.5 <= row["sim_time"] <= 7.5:
            row["ego_forward_m"] = 14.0
    verdict = strict.score_episode_from_telemetry(
        {"scenario": SCENARIO},
        rows,
        scenario=SCENARIO,
        expected_action="STOP",
        max_reaction_time=BUDGET_S,
    )
    assert verdict["passed"] is False
    assert "fell back out of the handoff band" in verdict["reason"]


def test_row_missing_stopline_key_is_invalid():
    # Kimi review finding 5: a row that lacks even the distance_to_stopline_m
    # KEY is malformed telemetry (every recorder writes the column, current
    # code as an explicit None) and must stay INVALID instead of sliding into
    # the no-stopline exemption.
    rows = _rows_no_stopline()
    del rows[3]["distance_to_stopline_m"]
    verdict = _strict(rows, scenario="fallen_person")
    assert verdict["verdict"] == "INVALID"


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
