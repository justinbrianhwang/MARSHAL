"""Protocol v2 scoring rules (front-bumper clearance, directive-window movement, PROCEED start deadline)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dac2027_audit as A  # noqa: E402
from marshal_bench.criteria import graded_episode_scoring as G  # noqa: E402
from marshal_bench.criteria import strict_episode_scoring as S  # noqa: E402

HALF = 2.3959


def _rows(profile, **kw):
    rows = A._synthetic(profile, **kw)
    for r in rows:
        r["stopline_front_m"] = r["stopline_forward_m"] - HALF
    return rows


def _s(rows, action="STOP", scenario="green_stop", **kw):
    return S.score_episode_from_telemetry({}, rows, scenario=scenario, expected_action=action,
                                          max_reaction_time=3.0, protocol="v2", **kw)


def test_front_bumper_clearance():
    ok = _rows([(0, 2.0, 3.0), (1.5, 0.0, 4.5)])  # 7.2 km/h approach, front ~4.8 m before the line
    assert _s(ok)["verdict"] == "PASS"
    short = _rows([(0, 5.0, 3.0), (A._stop_at(1.4), 0.0, 4.5)])  # centre 1.4 m: passes v1, front past line
    assert S.score_episode_from_telemetry({}, short, scenario="green_stop", expected_action="STOP",
                                          max_reaction_time=3.0)["verdict"] == "PASS"
    assert _s(short)["verdict"] == "FAIL"


def test_v2_requires_front_column():
    rows = A._synthetic([(0, 5.0, 3.0), (A._stop_at(4.0), 0.0, 4.5)])
    assert _s(rows)["verdict"] == "INVALID"


def test_movement_after_directive_does_not_count():
    parked_then_go = _rows([(0, 0.0, 3.0), (13.5, 5.0, 3.0)], t_end=25.0)  # moves only after the 13 s release
    assert S.score_episode_from_telemetry({}, parked_then_go, scenario="green_stop", expected_action="STOP",
                                          max_reaction_time=3.0)["verdict"] == "PASS"  # v1: whole episode
    v2 = _s(parked_then_go)
    assert v2["verdict"] == "FAIL" and "never engaged" in v2["reason"]
    assert G.score_episode_from_telemetry({}, parked_then_go, scenario="green_stop", expected_action="STOP",
                                          max_reaction_time=3.0, protocol="v2")["credit"] == 0.0


def test_release_followup_reported_separately():
    rows = _rows([(0, 5.0, 3.0), (A._stop_at(HALF + 1.5), 0.0, 4.5), (13.2, 5.0, 3.0)], t_end=25.0)
    v = _s(rows)
    assert v["verdict"] in ("PASS", "FAIL")
    assert v["release_followup"]["started_within_3s"] is True


def test_proceed_start_deadline():
    prompt = _rows([(0, 0.0, 3.0), (1.5, 5.0, 3.0)], t_end=25.0)
    assert _s(prompt, action="PROCEED", scenario="red_proceed")["verdict"] == "PASS"
    late = _rows([(0, 0.0, 3.0), (5.0, 5.0, 3.0)], t_end=25.0)  # starts 4 s after the PROCEED onset (1.0 s)
    v = _s(late, action="PROCEED", scenario="red_proceed")
    assert v["verdict"] == "FAIL" and v["evidence"]["failure_class"] == "late_start"
    v = _s(late, action="PROCEED", scenario="red_proceed", decision_times=[0.05, 10.0])
    assert v["evidence"]["failure_class"] == "no_decision_opportunity"
    assert S.score_episode_from_telemetry({}, late, scenario="red_proceed", expected_action="PROCEED",
                                          max_reaction_time=3.0)["verdict"] == "PASS"  # v1: no deadline
    parked = _rows([(0, 0.0, 3.0)], t_end=25.0)
    v = _s(parked, action="PROCEED", scenario="red_proceed")
    assert "never engaged" in v["reason"] and v["evidence"]["failure_class"] == "late_start"
    assert "proceed_start" in _s(prompt, action="PROCEED", scenario="red_proceed")["evidence"]


def test_safe_time_for_crossing_pedestrian():
    rows = _rows([(0, 0.0, 3.0)], t_end=10.0)
    for r in rows:  # pedestrian 10 m ahead walking right-to-left at 1.2 m/s from +3.0 m
        t = r["sim_time"]
        r["hazard_forward_m"], r["hazard_lateral_m"] = 10.0, 3.0 - 1.2 * t
    t_safe, basis = S.safe_to_proceed_time(rows, "rule_hierarchy", 1.0)
    assert abs(t_safe - (3.0 + 2.8) / 1.2) < 0.1 and "pedestrian" in basis


def test_horizon_and_post_release_collision():
    # completes only at 25.05 s -> outside the 25 s horizon
    late = _rows([(0, 0.0, 3.0), (2.0, 5.0, 3.0)], t_end=25.05)
    for r in late:
        r["in_junction"] = float(r["sim_time"]) >= 25.04
    assert _s(late, action="PROCEED", scenario="red_proceed")["verdict"] == "FAIL"
    # a collision after the STOP release reports in release_followup, not in the STOP verdict
    rows = _rows([(0, 2.0, 3.0), (1.5, 0.0, 4.5), (13.2, 5.0, 3.0)], t_end=25.0)
    assert _s(rows)["verdict"] == "PASS"
    for r in rows:
        r["collision_count"] = 1 if float(r["sim_time"]) >= 20.0 else 0
    v = _s(rows)
    assert v["verdict"] == "PASS" and v["release_followup"]["collision_after_release"] is True


def test_missing_pedestrian_geometry_sets_no_deadline():
    rows = _rows([(0, 0.0, 3.0)], t_end=10.0)
    assert S.safe_to_proceed_time(rows, "rule_hierarchy", 1.0)[0] is None


def test_v2_windows_edge_cases():
    rows = _rows([(0, 2.0, 3.0), (1.5, 0.0, 4.5), (13.2, 5.0, 3.0)], t_end=25.0)
    for r in rows:  # a bad control sample after the release does not touch the STOP verdict
        r["control_finite"] = float(r["sim_time"]) < 20.0
    assert _s(rows)["verdict"] == "PASS"
    late = _rows([(0, 0.0, 3.0)], t_end=1.0)
    for r in late:
        r["sim_time"] += 30.0
    assert _s(late, action="PROCEED", scenario="red_proceed")["verdict"] == "INVALID"


def test_rule_hierarchy_entry_before_pedestrian_clears():
    rows = _rows([(0, 0.0, 3.0), (2.0, 4.0, 3.0)], t_end=25.0)
    for r in rows:
        t = r["sim_time"]
        r["hazard_forward_m"], r["hazard_lateral_m"] = 10.0, 3.0 - 0.3 * t  # clears at ~19 s
        r["distance_to_hazard_m"] = 10.0
        r["in_junction"] = t >= 6.0
    v = _s(rows, action="PROCEED", scenario="rule_hierarchy")
    assert v["verdict"] == "FAIL" and "before the crossing pedestrian" in v["reason"]
