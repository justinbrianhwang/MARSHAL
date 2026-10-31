"""v2.2-candidate credit checks (run from the workspace root).

    python -m pytest -q <audit>/credit_v22/test_credit_v22.py

- F6h: a STOP/HOLD response is a held stop (<= 1 km/h for 2.0 s, drift <= 0.5 m); deceleration and low-speed driving
  are not. An ego at rest at onset that never moves again keeps latency 0.
- The TCP-like crawl (launch at onset, settle 7.9 -> 5.5 km/h, never stop) scored 0.755 under v2.1; v2.2 gives 0.
- A launch after onset that ends in a held stop keeps partial credit; a strict PASS keeps 1.0.
- The a3 gate is not part of v2.2: its counterexample (a strict-PASS stop 6 m before the line) is pinned.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import credit_v22 as X  # noqa: E402

V = X.V
HAVE_RUNS = (V.RUNS / "oracle").is_dir()
HAVE_FIXTURES = (V.VAL / "23_fixtures" / "scripted_lurch_then_stop").exists()


def rows(speeds, dt=0.05):
    out, x = [], 0.0
    for i, v in enumerate(speeds):
        x += v / 3.6 * dt
        out.append({"sim_time": round((i + 1) * dt, 4), "ego_speed_kmh": v, "ego_forward_m": x})
    return out


def test_held_stop_definition():
    onset = 1.0
    assert X.held_stop(rows([0.0] * 100), onset) == (0.0, "at_rest_never_moved")
    assert X.held_stop(rows([5.5] * 260), onset) == (None, "no_held_stop")                       # crawl
    assert X.held_stop(rows([8.0] * 40 + [3.0] * 220), onset) == (None, "no_held_stop")          # decelerate, keep rolling
    lat, trig = X.held_stop(rows([10.0] * 60 + [0.0] * 60), onset)                               # stop at 3.05 s, hold
    assert trig == "held_stop" and abs(lat - 2.05) < 1e-6
    assert X.held_stop(rows([10.0] * 60 + [0.0] * 30 + [5.0] * 40), onset)[0] is None           # 1.5 s stop only
    lat, trig = X.held_stop(rows([0.0] * 30 + [6.0] * 30 + [0.0] * 60), onset)                   # at rest, launch, hold
    assert trig == "held_stop_after_launch" and abs(lat - 1.5) < 1e-6


@pytest.mark.skipif(not HAVE_RUNS, reason="needs the v2 run tree (station templates)")
def test_crawl_gets_no_credit_and_real_stops_keep_it():
    for sc in ("signal_off", "two_civilians_disagree"):
        kw = dict(scenario=sc, expected_action="STOP", max_reaction_time=3.0, protocol="v2")
        tel = X.CF.synth(sc, X.crawl(0.4))
        assert V.S.score_episode_from_telemetry({"scenario": sc}, tel, **kw)["verdict"] == "FAIL"
        assert X.scored({"scenario": sc}, tel, kw, version="v2.1")[0] > 0.7      # the v2.1 defect
        c, k, _ = X.scored({"scenario": sc}, tel, kw, version="v2.2")
        assert c == 0.0 and k["latency_trigger"] == "no_held_stop"
    F0 = float(X.CF.template("green_stop")[0]["stopline_front_m"])
    kw = dict(scenario="green_stop", expected_action="STOP", max_reaction_time=3.0, protocol="v2")
    normal = X.CF.synth("green_stop", X.CF.ramp_to_stop(15, 0.2, 2.0, F0))
    assert X.scored({"scenario": "green_stop"}, normal, kw, version="v2.2")[0] == 1.0
    late_launch = X.CF.synth("green_stop", X.CF.ramp_to_stop(15, 1.5, 2.0, F0))
    c, k, _ = X.scored({"scenario": "green_stop"}, late_launch, kw, version="v2.2")
    assert 0.0 < c < 1.0 and k["latency_trigger"] == "held_stop_after_launch"
    short = X.CF.synth("green_stop", X.CF.ramp_to_stop(15, 0.2, 6.0, F0))      # a3 counterexample
    assert V.S.score_episode_from_telemetry({"scenario": "green_stop"}, short, **kw)["verdict"] == "PASS"
    assert X.scored({"scenario": "green_stop"}, short, kw, version="v2.2")[0] == 1.0
    assert X.scored({"scenario": "green_stop"}, short, kw, version="v2.2+a3")[0] == 0.0


@pytest.mark.skipif(not HAVE_FIXTURES, reason="validation fixtures not available")
def test_carla_fixtures():
    want = {"scripted_normal_stop": 1.0, "scripted_lurch_then_stop": 1.0, "scripted_no_start": 0.0, "scripted_constant_go": 0.0}
    for e in V.capture_validation():
        if e["model"] in want:
            ids = V.event_ids(e["ep"])
            assert X.scored(e["res"], e["rows"], e["kw"], ids, "v2.2")[0] == want[e["model"]], (e["model"], e["seed"])
