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


def _readme(tmp_path, extra_rows=(), drop=None):
    lb = ["| model | track | MARSHAL-Graded | scenarios passed | override cells (12) | link |", "|---|---|---:|---:|---:|---|"]
    pf = ["| model | override (6) | stressed-override (6) | validity (5) | conflict (3) | scene (2) | safety (3) |", "|---|---|---|---|---|---|---|"]
    names = {v: k for k, v in A.README_NAMES.items()}
    for m in A.LB_LABELS:
        if m == drop:
            continue
        lb.append(f"| **{names[m]}** (x) | B | **1.0** | 1 / 25 | 0 / 12 | - |")
        pf.append(f"| {names[m]} | 0/6 | 0/6 | 0/5 | 0/3 | 0/2 | 0/3 |")
    p = tmp_path / "README.md"
    p.write_text("\n".join(lb + list(extra_rows)) + "\n\n" + "\n".join(pf) + "\n", encoding="utf-8")
    return p


def test_readme_parser_strict(tmp_path):
    import pytest
    pub = A.published_from_readme(_readme(tmp_path))
    assert pub["interfuser"] == {"graded": 1.0, "passed": "1/25", "override_cells": "0/12",
                                 "profile": "0/6;0/6;0/5;0/3;0/2;0/3"}
    assert A.published_from_readme(_readme(tmp_path, ["| AIMLESS experiment | B | 9.9 | 9 / 25 | 9 / 12 | - |"]))["aim"]["graded"] == 1.0
    with pytest.raises(ValueError):
        A.published_from_readme(_readme(tmp_path, drop="neat"))
    with pytest.raises(ValueError):
        A.published_from_readme(_readme(tmp_path, ["| InterFuser | B | 2.0 | 2 / 25 | 1 / 12 | - |"]))


def test_readme_parser_requires_every_field(tmp_path):
    import pytest
    p = _readme(tmp_path)
    text = p.read_text(encoding="utf-8").replace("| InterFuser | 0/6 | 0/6 | 0/5 | 0/3 | 0/2 | 0/3 |\n", "")
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        A.published_from_readme(p)


def test_scorer_kwargs_carry_validity_errors():
    ep = _episode([])
    ep.result = {"setup_errors": ["a"], "controller_errors": ["b"]}
    kw = A.scorer_kwargs(ep, "green_stop", "STOP")
    assert kw["setup_errors"] == ["a"] and kw["controller_errors"] == ["b"]
