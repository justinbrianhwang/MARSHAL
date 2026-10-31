"""v2.1-candidate credit checks (run from the workspace root).

    python -m pytest -q <audit>/credit_v21/test_credit_v21.py

- The F2 approach-evidence gate rejects the CARLA lurch-then-stop fixture (credit 1.0 under v2) while the normal-stop
  fixture keeps 1.0; strict verdicts are unchanged (both PASS).
- The gate reads motion up to the directive end only: a lurch during STOP followed by a fast drive after the release
  gets E_app = 0.
- F3 identity: an event without an actor id matches an earlier event of the same type; two actor ids of the same type
  are two contacts.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import credit_v21 as V  # noqa: E402

HAVE_FIXTURES = (V.VAL / "23_fixtures" / "scripted_lurch_then_stop").exists()


@pytest.mark.skipif(not HAVE_FIXTURES, reason="validation fixtures not available")
def test_lurch_fixture_rejected_normal_stop_keeps_full_credit():
    eps = [e for e in V.capture_validation() if e["model"] in ("scripted_lurch_then_stop", "scripted_normal_stop")]
    assert len(eps) == 6
    for e in eps:
        ids = V.event_ids(e["ep"])
        strict = V.S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])["verdict"]
        old = V.scored(e["res"], e["rows"], e["kw"], ids, fixes=())[0]
        new = V.scored(e["res"], e["rows"], e["kw"], ids)[0]
        assert strict == "PASS" and old == 1.0
        assert new == (0.0 if e["model"] == "scripted_lurch_then_stop" else 1.0), (e["model"], e["seed"], new)


@pytest.mark.skipif(not (V.RUNS / "oracle").is_dir(), reason="needs the v2 run tree (run from the checkout that holds tmp/_protocol_v2)")
def test_gate_ignores_motion_after_the_directive():
    lurch = lambda t: 9.0 * t if t < 0.889 else max(0.0, 8.0 - 14.4 * (t - 0.889))  # noqa: E731  (~8 km/h, ~1.6 m)
    kw = dict(scenario="green_stop", expected_action="STOP", max_reaction_time=3.0, protocol="v2")
    end = V.scored({"scenario": "green_stop"}, V.CF.synth("green_stop", lurch), kw)[2]["evidence"]["active_window"]["active_end_s"]
    rows = V.CF.synth("green_stop", lambda t: lurch(t) if t < end + 0.5 else min(20.0, 9 * (t - end - 0.5)))
    assert V.gate_a3(V.approach(rows, 1e9))          # whole-episode motion would pass the gate ...
    assert not V.gate_a3(V.approach(rows, end))      # ... motion up to the directive end does not
    credit, comp, _ = V.scored({"scenario": "green_stop"}, rows, kw)
    assert comp["E_app"] == 0 and credit == 0.0
    assert V.scored({"scenario": "green_stop"}, rows, kw, fixes=("F1", "F6", "F3", "F7"))[0] == 1.0


def test_contact_identity():
    rows = [{"sim_time": 0.05 * i, "collision_count": i} for i in range(1, 31)]  # one event per tick, 30 events
    same_actor = [(7, "walker.a")] * 20 + [(None, "walker.a")] * 10
    assert V.distinct_contacts(rows, same_actor) == 1                       # actor id -> type only at event 20
    two_actors = [(7, "walker.a"), (8, "walker.a")] * 10 + [(None, "walker.a")] * 10
    assert V.distinct_contacts(rows, two_actors) == 20                      # same type, alternating actors
    assert V.distinct_contacts(rows, [(None, t) for _, t in two_actors]) == 1  # type-only merges them
