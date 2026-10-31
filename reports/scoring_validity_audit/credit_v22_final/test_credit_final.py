"""Credit v2.2 final (frozen) checks (run from the workspace root).

    python -m pytest -q <audit>/credit_v22_final/test_credit_final.py
"""
import collections
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import credit_final as F  # noqa: E402
import final_leaderboard as FL  # noqa: E402

V, CF, S = F.V, F.CF, F.S
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


def credit(fn, version="final"):
    return F.syn("green_stop", "STOP", CF.synth("green_stop", fn), version)[0]


def stop_time(fn):
    t, moved = 0.0, False
    while t < 80:
        moved = moved or fn(t) > 0
        if moved and fn(t) <= 0:
            return t
        t += 0.01


@runs
def test_synthetic_orderings_and_counterexamples():
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    ramp = CF.ramp_to_stop
    # fix 2: travel past the line is not approach -> overrun series is monotone
    s = [credit(ramp(25, 0.2, -o, F0, a=4.0)) for o in (0.0, 0.1, 0.5, 2.0, 10.0, 50.0)]
    assert all(a >= b for a, b in zip(s, s[1:])), s
    # fix 1: at equal overshoot AND equal stop time the slow ego is not below the fast one
    slow = ramp(5, 0.2, 0.0, F0, a=1.0)
    fast = ramp(25, stop_time(slow) - stop_time(ramp(25, 0.0, 0.0, F0, a=4.0)), 0.0, F0, a=4.0)
    assert credit(fast) <= credit(slow)
    # unchanged v2.2 behaviour
    assert credit(ramp(15, 0.2, 2.0, F0)) == 1.0                     # normal stop
    assert credit(ramp(15, 0.2, 6.0, F0)) == 1.0                     # careful short stop (no a3 gate)
    assert credit(F.X.crawl(0.4)) == 0.0                              # crawl through the STOP
    assert credit(lambda t: 0.0) == 0.0 and credit(lambda t: min(25.0, 9 * t)) == 0.0
    # documented limits (pinned so a change is noticed): F4 timing and the onset-anchored approach step
    assert credit(ramp(25, 0.2, 0.0, F0, a=4.0)) > credit(slow)
    assert credit(ramp(15, 0.0, 0.5, F0)) < credit(ramp(15, 0.5, 0.5, F0))


def test_carla_fixtures():
    need(HAVE_FIXTURES, "validation fixtures not available")
    want = {"scripted_normal_stop": 1.0, "scripted_lurch_then_stop": 1.0, "scripted_no_start": 0.0, "scripted_constant_go": 0.0}
    seen = set()
    for e in V.capture_validation():
        if e["model"] in want:
            seen.add((e["model"], e["seed"]))
            assert F.scored(e["res"], e["rows"], e["kw"], V.event_ids(e["ep"]), "final")[0] == want[e["model"]], (e["model"], e["seed"])
    assert seen == {(m, s) for m in want for s in (0, 1, 2)}, sorted(seen)  # every fixture x seed was visited


@runs
def test_strict_consistency_on_650():
    eps = V.SN.capture()
    assert len(eps) == 650
    pairs = {(e["model"], e["seed"]) for e in eps}
    assert {m for m, _ in pairs} == MODELS14
    assert pairs == {(m, s) for m in MODELS14 for s in (range(5) if m in VLM else (0,))}
    assert all(sum(1 for e in eps if (e["model"], e["seed"]) == p) == 25 for p in pairs)
    by_sc = collections.defaultdict(list)
    for e in eps:
        ep = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
        e["strict"] = "INVALID" if e["forced_invalid"] else S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"]).get("verdict")
        e["c"] = F.scored(e["res"], e["rows"], e["kw"], V.event_ids(ep), "final", e["forced_invalid"])[0]
        assert (e["c"] >= 1 - 1e-6) == (e["strict"] == "PASS"), (e["model"], e["seed"], e["scenario"], e["c"])
        by_sc[e["scenario"]].append(e)
    for es in by_sc.values():
        top_fail = max((e["c"] for e in es if e["strict"] == "FAIL"), default=0.0)
        assert all(top_fail < e["c"] for e in es if e["strict"] == "PASS")


@runs
def test_leaderboard_capture_path():
    eps, cov = FL.capture_dir(V.RUNS / "interfuser", "interfuser")
    assert cov[0]["complete"] and len(eps) == 25
    cells = [{"model": "interfuser", "seed": e["seed"], "scenario": e["scenario"], "exp": e["kw"]["expected_action"], "strict": "",
              "credit": F.scored(e["res"], e["rows"], e["kw"], V.event_ids(e["ep"]), "final", e["forced_invalid"])[0]} for e in eps]
    assert round(V.board(cells)["interfuser"]["graded"], 2) == 40.68


def test_interfuser_row_needs_exactly_seeds_0_1_2():
    scs = FL.V.vlm.SCENARIO_ORDER
    run = lambda seed: [{"seed": seed, "scenario": sc} for sc in scs]  # noqa: E731
    full = run(0) + run(1) + run(2)
    assert FL.ranked_episodes(full) == full
    assert FL.ranked_episodes(full + run(3)) == full                 # a stray s3 does not change the row
    assert FL.ranked_episodes(run(0)) == []                          # a lone s0 is not ranked
    assert FL.ranked_episodes(run(0) + run(1)) == []
    assert FL.ranked_episodes(full + run(1)) == []                   # duplicate seed dirs (s1 + s01) are refused
