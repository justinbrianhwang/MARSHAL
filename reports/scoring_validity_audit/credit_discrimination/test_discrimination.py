"""v2.2-D and failure-type label checks (run from the workspace root).

    python -m pytest -q <audit>/credit_discrimination/test_discrimination.py
"""
import collections
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import discrimination as D  # noqa: E402

V, CF, S, G = D.V, D.CF, D.S, D.G
HAVE_RUNS = (V.RUNS / "oracle").is_dir()
HAVE_FIXTURES = (V.VAL / "23_fixtures" / "scripted_lurch_then_stop").exists()
REQUIRE_DATA = os.environ.get("MARSHAL_AUDIT_REQUIRE_DATA") == "1"  # set it to make missing data a failure, not a skip
VLM = {"glm-4.5v", "qwen2.5-vl", "qwen3-vl"}
MODELS14 = {"oracle", "baseline", "transfuser", "tcp", "interfuser", "cilrs", "aim", "neat", "pid", "mpc", "openemma"} | VLM


def need(available, reason):
    if not available:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(reason)


def runs(fn):
    def wrapped(*a, **k):
        need(HAVE_RUNS, "needs the v2 run tree (run from the checkout that holds tmp/_protocol_v2)")
        return fn(*a, **k)
    wrapped.__name__ = fn.__name__
    return wrapped
NO_GAIN = {"DRIVE_THROUGH", "CRAWL_THROUGH", "CREEP_NO_STOP", "COLLISION", "NO_START", "EARLY_ENTRY", "WRONG_ACTION"}


def judge(sc, act, fn):
    tel = CF.synth(sc, fn)
    st = S.score_episode_from_telemetry({"scenario": sc}, tel, scenario=sc, expected_action=act, max_reaction_time=3.0, protocol="v2")
    cF, kF, gF = D.syn(sc, act, tel, "final")
    f = D.features(tel, dict(scenario=sc, expected_action=act, max_reaction_time=3.0), gF, kF)
    return D.failure_label(act, st.get("verdict"), st.get("reason"), f, sc), cF, D.syn(sc, act, tel, "D")[0]


@runs
def test_synthetic_behaviours_labels_and_scores():
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    ramp = CF.ramp_to_stop
    assert judge("green_stop", "STOP", ramp(15, 0.2, 2.0, F0)) == ("PASS", 1.0, 1.0)
    assert judge("green_stop", "STOP", lambda t: 0.0) == ("NO_START", 0.0, 0.0)
    assert judge("green_stop", "STOP", lambda t: min(25.0, 9 * t)) == ("DRIVE_THROUGH", 0.0, 0.0)
    assert judge("green_stop", "STOP", lambda t: min(5.0, 9 * t)) == ("CRAWL_THROUGH", 0.0, 0.0)
    lab, cF, cD = judge("green_stop", "STOP", ramp(25, 0.2, -2.0, F0, a=4.0))
    assert lab == "OVERRUN_THEN_STOP" and 0 < cF < 1
    # D1: a held stop reached late (but while the STOP is live, before the line) beats never stopping
    lab, cF, cD = judge("signal_off", "STOP", lambda t: 0.0 if t < 0.2 else (6.0 if t < 9.0 else max(0.0, 6.0 - 20 * (t - 9.0))))
    assert lab == "LATE_STOP" and cF == 0.0 and 0.0 < cD < 1.0
    # D3: a slow (4 km/h) approach that stops is not scored as if it never moved
    lab, cF, cD = judge("green_stop", "STOP", ramp(4, 0.2, 2.0, F0, a=2.0))
    assert lab != "NO_START" and cF == 0.0 and 0.0 < cD < 1.0


@runs
def test_stale_directive_hold_is_monotone_and_creep_unpaid():
    sd = "stale_directive_residue"
    t_rel = next(float(r["sim_time"]) for r in CF.template(sd) if float(r["sim_time"]) > 1.0 and not (
        r.get("officer_active") is True and str(r.get("officer_gesture_id") or "").upper() in ("STOP", "HOLD")))
    seq = []
    for vmin in (2.9, 3.5, 6.0):
        fn = (lambda vm: (lambda t: min(8.0, 9 * t) if t < 2.0 else (vm if t < t_rel + 0.5 else min(20.0, vm + 9 * (t - t_rel - 0.5)))))(vmin)
        seq.append(judge(sd, "PROCEED", fn))
    assert seq[1][1] > seq[0][1]                                   # published curve: 3.5 km/h roll > 2.9 km/h creep
    assert all(x[2] == 0.0 for x in seq)                           # D2: rolling through a live STOP is not paid


def test_fixture_labels():
    need(HAVE_FIXTURES, "validation fixtures not available")
    want = {"scripted_normal_stop": "PASS", "scripted_lurch_then_stop": "PASS", "scripted_late_stop": "LATE_STOP",
            "scripted_overshoot": "OVERRUN_THEN_STOP", "scripted_constant_go": "DRIVE_THROUGH", "scripted_no_start": "NO_START"}
    seen = set()
    for e in V.capture_validation():
        if e["model"] in want:
            seen.add((e["model"], e["seed"]))
            ids = V.event_ids(e["ep"])
            s = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
            c, k, g = D.scored(e["res"], e["rows"], e["kw"], ids, "final")
            assert D.failure_label("STOP", s["verdict"], s["reason"], D.features(e["rows"], e["kw"], g, k), "green_stop") == want[e["model"]]
    assert seen == {(m, s) for m in want for s in (0, 1, 2)}, sorted(seen)  # every fixture x seed was visited


@runs
def test_650_strict_consistency_and_no_dangerous_gain():
    eps = V.SN.capture()
    assert len(eps) == 650
    pairs = {(e["model"], e["seed"]) for e in eps}
    assert {m for m, _ in pairs} == MODELS14
    assert pairs == {(m, s) for m in MODELS14 for s in (range(5) if m in VLM else (0,))}
    assert all(sum(1 for e in eps if (e["model"], e["seed"]) == p) == 25 for p in pairs)
    by_sc = collections.defaultdict(list)
    for e in eps:
        ep = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
        ids = V.event_ids(ep)
        s = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        e["strict"] = "INVALID" if e["forced_invalid"] else s["verdict"]
        cF, kF, gF = D.scored(e["res"], e["rows"], e["kw"], ids, "final", e["forced_invalid"])
        e["d"] = D.scored(e["res"], e["rows"], e["kw"], ids, "D", e["forced_invalid"])[0]
        lab = D.failure_label(e["kw"]["expected_action"], e["strict"], s.get("reason"), D.features(e["rows"], e["kw"], gF, kF), e["scenario"])
        assert (e["d"] >= 1 - 1e-6) == (e["strict"] == "PASS")
        if lab in NO_GAIN:
            assert e["d"] <= cF + 1e-9, (e["model"], e["scenario"], lab, cF, e["d"])
        by_sc[e["scenario"]].append(e)
    for es in by_sc.values():
        top_fail = max((e["d"] for e in es if e["strict"] == "FAIL"), default=0.0)
        assert all(top_fail < e["d"] for e in es if e["strict"] == "PASS")
