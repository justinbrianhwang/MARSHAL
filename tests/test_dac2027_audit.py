"""The audit's gate replication and graded decomposition must agree with the real scorers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dac2027_audit as A  # noqa: E402


def _episode(tel):
    ep = A.Episode.__new__(A.Episode)
    ep.rows, ep.result, ep.budget, ep.events, ep.stored_strict = tel, {}, 3.0, [], {}
    ep.terminated, ep.stop_xy, ep.yaw = "synthetic", None, None
    ep.label = ep.run_set = ep.condition = ep.seed = ""
    return ep


def test_cases_match_scorer_and_cascade():
    expected = {"C1": "PASS", "C2": "PASS", "C3": "PASS", "C4": "FAIL", "C5": "FAIL", "C6": "FAIL", "C7": "FAIL"}
    for name, action, prof in A.CASES:
        if action != "STOP":
            continue
        ep = _episode(A._synthetic(prof()))
        s, g = A.score(ep)
        assert s["verdict"] == expected[name.split()[0]], name
        gates = A.strict_gates(ep)
        first = next((x["gate"] for x in gates if x["first_failure"]), "")
        assert (first == "") == (s["verdict"] == "PASS"), name
        gb = A.graded_breakdown(g)
        assert gb["check_credit"] and gb.get("check_action_credit", True), name


def test_front_bumper_offset_applied():
    ep = _episode(A._synthetic(A.CASES[0][2]()))
    m = A.stop_measures(ep)
    assert abs(m["spawn_centre_to_line_m"] - m["spawn_front_to_line_m"] - A.EGO_HALF_LENGTH_M) < 1e-9


def test_setup_error_makes_episode_invalid():
    ep = _episode(A._synthetic(A.CASES[0][2]()))
    ep.result = {"setup_errors": ["camera missing"]}
    s, g = A.score(ep)
    assert s["verdict"] == "INVALID" and g["credit"] == 0.0


def test_controller_error_makes_episode_invalid():
    ep = _episode(A._synthetic(A.CASES[0][2]()))
    ep.result = {"controller_errors": ["step@1.00: boom"]}
    s, g = A.score(ep)
    assert s["verdict"] == "INVALID" and g["credit"] == 0.0
