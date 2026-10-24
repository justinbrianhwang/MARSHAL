"""A never-moving ego earns no graded credit for any expected action, not only STOP/HOLD."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dac2027_audit as A  # noqa: E402
from marshal_bench.criteria import graded_episode_scoring as G  # noqa: E402


def test_stationary_ego_gets_zero_for_every_action():
    rows = A._synthetic([(0, 0.0, 3.0)])  # never moves; 9.8 m before the line
    for scen, action in [("green_stop", "STOP"), ("red_proceed", "PROCEED"), ("crash_detour", "DETOUR"),
                         ("ambulance_yield", "YIELD"), ("sequential_directive", "HOLD")]:
        g = G.score_episode_from_telemetry({}, rows, scenario=scen, expected_action=action, max_reaction_time=3.0)
        assert g["credit"] == 0.0, (scen, g.get("credit"))


def test_moving_ego_is_not_gated():
    rows = A._synthetic([(0, 5.0, 3.0)])  # drives through
    g = G.score_episode_from_telemetry({}, rows, scenario="red_proceed", expected_action="PROCEED", max_reaction_time=3.0)
    assert g["credit"] > 0.9 and g["evidence"]["movement_gate"]["engaged"]
