"""Regression tests for the v2.2 checks (run from the workspace root).

    python -m pytest -q <audit>/credit_v22/checks/test_checks.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import checks as K  # noqa: E402
import final_leaderboard as FL  # noqa: E402

X, V, CF = K.X, K.V, K.CF
HAVE_RUNS = (V.RUNS / "oracle").is_dir()
KW = dict(scenario="green_stop", expected_action="STOP", max_reaction_time=3.0, protocol="v2")


def rows(speeds, dt=0.05):
    out, x = [], 0.0
    for i, v in enumerate(speeds):
        x += v / 3.6 * dt
        out.append({"sim_time": round((i + 1) * dt, 4), "ego_speed_kmh": v, "ego_forward_m": x})
    return out


def test_f6h_time_point_is_hold_start_and_final_hold_variant():
    held_then_roll = rows([10.0] * 60 + [0.0] * 60 + [3.0] * 40)
    assert X.held_stop(held_then_roll, 1.0)[0] == pytest.approx(2.05)    # latency taken at the START of the 2 s hold
    assert K.held_final(held_then_roll, 1.0)[0] is None                   # strict-aligned variant: not held to the end
    held = rows([10.0] * 60 + [0.0] * 100)
    assert X.held_stop(held, 1.0)[0] == pytest.approx(K.held_final(held, 1.0)[0])


def score(fn, name):
    return K.syn("green_stop", "STOP", CF.synth("green_stop", fn), name)[0]


@pytest.mark.skipif(not HAVE_RUNS, reason="needs the v2 run tree (station templates)")
def test_adopted_fixes():
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    over = [score(CF.ramp_to_stop(25, 0.2, -o, F0, a=4.0), K.ADOPTED) for o in (0.1, 0.5, 2.0)]
    assert over[0] >= over[1] >= over[2]                                  # item 3a: past-line travel is not approach
    assert score(CF.ramp_to_stop(25, 0.2, -0.1, F0, a=4.0), "v2.2") < score(CF.ramp_to_stop(25, 0.2, -0.5, F0, a=4.0), "v2.2")
    slow = CF.ramp_to_stop(5, 0.2, 0.0, F0, a=1.0)                         # item 2: equal overshoot AND equal stop time
    fast = CF.ramp_to_stop(25, K.stop_time(slow) - K.stop_time(CF.ramp_to_stop(25, 0.0, 0.0, F0, a=4.0)), 0.0, F0, a=4.0)
    assert score(fast, K.ADOPTED) <= score(slow, K.ADOPTED)
    assert score(fast, "v2.2") > score(slow, "v2.2")
    assert score(CF.ramp_to_stop(15, 0.2, 2.0, F0), K.ADOPTED) == 1.0      # strict PASS keeps 1.0
    # documented residual: a timely 25 km/h stop at the line outranks a 5 km/h stop reached ~4 s after the deadline
    assert score(CF.ramp_to_stop(25, 0.2, 0.0, F0, a=4.0), K.ADOPTED) > score(slow, K.ADOPTED)


@pytest.mark.skipif(not HAVE_RUNS, reason="needs the v2 run tree")
def test_final_leaderboard_capture_reproduces_v2_interfuser_row():
    eps, cov = FL.capture_dir(V.RUNS / "interfuser", "interfuser")
    assert cov[0]["complete"] and len(eps) == 25
    cells = [{"model": "interfuser", "seed": e["seed"], "scenario": e["scenario"], "exp": e["kw"]["expected_action"],
              "strict": "", "credit": X.scored(e["res"], e["rows"], e["kw"], V.event_ids(e["ep"]), "v2.2", e["forced_invalid"])[0]}
             for e in eps]
    assert round(V.board(cells)["interfuser"]["graded"], 2) == 40.93
