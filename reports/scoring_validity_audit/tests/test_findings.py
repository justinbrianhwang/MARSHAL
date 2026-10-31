"""Audit findings pinned as tests on the published v2 scorers (run from the workspace root).

    python -m pytest -q <audit>/tests/test_findings.py

Each test asserts the CURRENT v2 behaviour, so a future change to that behaviour is noticed. The docstrings
state whether the behaviour is a suspected defect (F3) or a specification property to be decided (F2, F4).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
if not Path("tmp/_protocol_v2/oracle/s0/reference_oracle_green_stop/strict_telemetry.json").exists():
    pytest.skip("recorded oracle telemetry not available", allow_module_level=True)

import counterfactual as CF  # noqa: E402

F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
LURCH = lambda t: 9.0 * t if t < 0.889 else max(0.0, 8.0 - 14.4 * (t - 0.889))  # ≈1.6 m, peak 8 km/h


def test_F2_lurch_then_stop_gets_full_credit_on_green_stop():
    """Specification property: ≥5 km/h and ≥1 m once, then stopping regardless of the directive, satisfies
    every STOP gate when the spawn is inside the 16.59 m band (the 14 STOP/HOLD scenarios other than dual_authority_handoff)."""
    s, g = CF.score("green_stop", CF.synth("green_stop", LURCH), "STOP")
    assert s["verdict"] == "PASS" and g["credit"] == 1.0


def test_F2_park_at_spawn_gets_zero():
    s, g = CF.score("green_stop", CF.synth("green_stop", lambda t: 0.0), "STOP")
    assert s["verdict"] == "FAIL" and g["credit"] == 0.0


def test_F3_one_sustained_contact_counts_as_many_collisions():
    """Suspected defect: the safety factor is defined on a collision count (1 -> 0.25, 2-5 -> 0.10), but the
    counter increments once per sensor callback, so one contact lasting >5 physics frames gives factor 0."""
    rows = CF.synth("green_stop", CF.ramp_to_stop(15, 0.2, 2.0, F0),
                    collisions_at=tuple(5.0 + 0.05 * i for i in range(10)))  # one 0.5 s contact, 10 callbacks
    _, g = CF.score("green_stop", rows, "STOP")
    assert g["safety_factor"] == 0.0  # a single contact is treated like >5 collisions


def test_F4_faster_overshoot_scores_higher_than_slow_creep():
    """Specification property (D3 violated): at the same 0.5 m overshoot a 25 km/h approach outscores a 5 km/h
    creep, because low approach speed triggers the 0.25 creep cap and a slower deceleration lowers latency."""
    slow = CF.score("green_stop", CF.synth("green_stop", CF.ramp_to_stop(5, 0.2, -0.5, F0, a=1.0)), "STOP")[1]["credit"]
    fast = CF.score("green_stop", CF.synth("green_stop", CF.ramp_to_stop(25, 0.2, -0.5, F0, a=4.0)), "STOP")[1]["credit"]
    assert fast > slow
