"""Audit regression: STOP/HOLD engagement under the v2 rest start.

Run from the workspace root (needs the recorded oracle green_stop telemetry under tmp/_protocol_v2):
    python -m pytest -q <audit>/tests/test_engagement_timing.py

Intended behaviour (audit specification): the same physical approach-and-stop must receive the same engagement
factor whether its motion begins just before or just after the directive onset.

- test_v2_engagement_depends_on_onset_relative_start PASSES on the published v2 scorer: it pins the observed
  defect (approach started at/after onset is scored as a creep). It will fail once v2 itself is corrected.
- test_engagement_is_timing_invariant: strict xfail on v2 (must fail) and passes on the experimental correction A'.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

pytest.importorskip("marshal_bench")
if not Path("tmp/_protocol_v2/oracle/s0/reference_oracle_green_stop/strict_telemetry.json").exists():
    pytest.skip("recorded oracle telemetry not available", allow_module_level=True)

import published_v2  # noqa: E402,F401  (pins the published v2 graded scorer; see published_v2.py)
import counterfactual as CF  # noqa: E402
from marshal_bench.criteria import graded_episode_scoring as G  # noqa: E402

KW = dict(scenario="green_stop", expected_action="STOP", max_reaction_time=3.0, protocol="v2")
F0 = float(CF.template("green_stop")[0]["stopline_front_m"])


def _e(t0, engagement=None):
    rows = CF.synth("green_stop", CF.ramp_to_stop(15, t0, 0.5, F0))
    orig = G._stop_hold_engagement_factor
    if engagement is not None:
        G._stop_hold_engagement_factor = engagement
    try:
        return G.score_episode_from_telemetry({"scenario": "green_stop"}, rows, **KW)["component_credits"]["engagement_gate"]
    finally:
        G._stop_hold_engagement_factor = orig


def test_v2_engagement_depends_on_onset_relative_start():
    before, after = _e(0.5), _e(1.5)
    assert before > 0.5 and after == pytest.approx(0.25)  # the observed v2 behaviour


def _anchored():
    import alternatives as ALT
    return ALT._engagement_motion_anchored


@pytest.mark.parametrize("variant", [
    pytest.param("v2", marks=pytest.mark.xfail(strict=True, reason="v2 scores an approach begun at/after onset as a creep")),
    "A_prime",
])
def test_engagement_is_timing_invariant(variant):
    eng = _anchored() if variant == "A_prime" else None
    vals = [_e(t0, eng) for t0 in (1.0, 1.5, 3.0)]
    assert max(vals) - min(vals) < 0.05, vals
    assert min(vals) > 0.25, vals
