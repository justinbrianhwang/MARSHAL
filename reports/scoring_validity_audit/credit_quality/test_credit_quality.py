"""Q-gate checks (run from the workspace root).

    MARSHAL_AUDIT_REQUIRE_DATA=1 python -m pytest -q <audit>/credit_quality/test_credit_quality.py
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import q_gate_validation as QV  # noqa: E402

V, CF, FL = QV.V, QV.CF, QV.FL
HAVE_RUNS = (V.RUNS / "oracle").is_dir()
HAVE_FIXTURES = (V.VAL / "23_fixtures" / "scripted_lurch_then_stop").exists()
REMEASURE = Path(__file__).resolve().parents[3] / "tmp" / "_remeasure_interfuser" / "final_forward" / "interfuser"
REQUIRE_DATA = os.environ.get("MARSHAL_AUDIT_REQUIRE_DATA") == "1"
VLM = {"glm-4.5v", "qwen2.5-vl", "qwen3-vl"}
MODELS14 = {"oracle", "baseline", "transfuser", "tcp", "interfuser", "cilrs", "aim", "neat", "pid", "mpc", "openemma"} | VLM
ORDER = ["scripted_normal_stop", "scripted_late_stop", "scripted_overshoot", "scripted_constant_go"]


def need(available, reason):
    if not available:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(reason)


@pytest.fixture(scope="module")
def fixtures():
    need(HAVE_FIXTURES, "validation fixtures not available")
    fx = [QV.prep(e) for e in V.capture_validation() if e["phase"] == "23_fixtures"]
    for e in fx:
        e["id"] = f"{e['model']}/s{e['seed']}"
    assert {(e["model"], e["seed"]) for e in fx} == {(m, s) for m in ORDER + ["scripted_no_start", "scripted_lurch_then_stop"] for s in (0, 1, 2)}
    return fx


def test_drive_through_is_zero(fixtures):
    need(HAVE_RUNS, "needs the run tree")
    for fn in (lambda t: min(25.0, 9 * t), lambda t: min(5.0, 9 * t)):     # fast drive-through, crawl-through
        e = QV.synth_ep("green_stop", "STOP", CF.synth("green_stop", fn))
        assert e["x"]["Z"] and QV.qg(e) == 0.0
    assert all(QV.qg(e) == 0.0 for e in fixtures if e["model"] == "scripted_constant_go")


def test_fixture_order(fixtures):
    by = {m: [QV.qg(e) for e in fixtures if e["model"] == m] for m in ORDER}
    for a, b in zip(ORDER, ORDER[1:]):
        assert min(by[a]) > max(by[b]), (a, b, by[a], by[b])


def test_dominance_on_fixtures(fixtures):
    n, bad = QV.dominance(fixtures, QV.qg)
    assert n == 99
    assert [x for x in bad if x[0].split("/")[0] != x[1].split("/")[0]] == []   # across behaviours: none
    assert len(bad) == 3     # within-behaviour seed replicates, K differences <= 0.0011 (documented)


def test_exact_episode_set():
    need(HAVE_RUNS and REMEASURE.is_dir(), "needs the run tree and the InterFuser re-measure")
    eps = V.SN.capture()
    pairs = {(e["model"], e["seed"]) for e in eps}
    assert len(eps) == 650 and pairs == {(m, s) for m in MODELS14 for s in (range(5) if m in VLM else (0,))}
    re_eps = FL.check_speed(FL.ranked_episodes(FL.capture_dir(REMEASURE, "interfuser", FL.REQUIRED_SEEDS)[0]), "forward")
    assert {(e["seed"], e["scenario"]) for e in re_eps} == {(s, sc) for s in (0, 1, 2) for sc in V.vlm.SCENARIO_ORDER}
    scope = [sc for sc in V.vlm.SCENARIO_ORDER if QV.in_scope(sc, V.vlm.SCENARIOS[sc]["expect"])]
    assert len(scope) == 14 and "dual_authority_handoff" not in scope


# ------------------------------------------------------------------------------------------------ Q-cont
import q_cont as QC  # noqa: E402


@pytest.fixture(scope="module")
def qc_fixtures():
    need(HAVE_FIXTURES, "validation fixtures not available")
    fx = [QC.prep(e) for e in V.capture_validation() if e["phase"] == "23_fixtures"]
    for e in fx:
        e["id"] = f"{e['model']}/s{e['seed']}"
    assert len(fx) == 18
    return fx


def test_qcont_no_hard_zero_for_fail(qc_fixtures):
    need(HAVE_RUNS, "needs the run tree")
    assert all(QC.qcont(e) > 0.3 for e in qc_fixtures if e["strict"] == "FAIL")
    slow, fast = (QC.synth("green_stop", (lambda v: lambda t: min(float(v), 9 * t))(v)) for v in (5, 25))
    assert QC.qcont(slow) > QC.qcont(fast) > 0.0                      # different features -> different, non-zero credit
    f0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    shallow, deep = (QC.synth("green_stop", CF.ramp_to_stop(25, 0.2, -x, f0, a=4.0)) for x in (2.0, 30.0))
    assert QC.qcont(shallow) > QC.qcont(deep) > 0.0


def test_qcont_fixture_order(qc_fixtures):
    by = {m: [QC.qcont(e) for e in qc_fixtures if e["model"] == m] for m in ORDER}
    for a, b in zip(ORDER, ORDER[1:]):
        assert min(by[a]) > max(by[b]), (a, b, by[a], by[b])


def test_qcont_sv_flags(qc_fixtures):
    want = {"scripted_constant_go": {"SV-LINE", "SV-NOHOLD", "SV-SPEED"}, "scripted_overshoot": {"SV-LINE"},
            "scripted_normal_stop": set(), "scripted_late_stop": set(), "scripted_no_start": set(), "scripted_lurch_then_stop": set()}
    for e in qc_fixtures:
        fl = QC.flags(e)
        assert set(fl) == set(QC.SV)
        assert {k for k, v in fl.items() if v} == want[e["model"]], e["id"]


def test_qcont_dominance_on_fixtures(qc_fixtures):
    n, bad = QV.dominance(qc_fixtures, QC.qcont)
    assert n == 99 and bad == []


# ------------------------------------------------------------------------------------------------ Q-cont stability options
import q_cont_stability as QS  # noqa: E402

OPTION = {"RH_app": True, "C_cond": True, "P_cond": True}


def test_stability_reference_matches_qcont_and_options_keep_order(qc_fixtures):
    for e in qc_fixtures:
        e["r"]["moving_at_onset"] = QS.mov0(e)
        assert abs(QS.score(e) - QC.qcont(e)) < 1e-12                  # reference setting = Q-cont
    for opt in ({}, OPTION):
        by = {m: [QS.score(e, opt) for e in qc_fixtures if e["model"] == m] for m in ORDER}
        assert all(min(by[a]) > max(by[b]) for a, b in zip(ORDER, ORDER[1:])), (opt, by)
    assert all(QS.score(e, OPTION) > 0 for e in qc_fixtures if e["model"] == "scripted_constant_go")   # no hard zero
    assert all(any(QC.flags(e).values()) for e in qc_fixtures if e["model"] in ("scripted_constant_go", "scripted_overshoot"))
