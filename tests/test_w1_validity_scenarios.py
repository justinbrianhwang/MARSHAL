"""W1 validity-cell scenarios: stale_directive_residue + out_of_jurisdiction_director.

Registration completeness across every table, station-alias resolution, and
the two oracle behaviors the new scenarios rely on (hold-until-directive-end
for PROCEED, and PROCEED for a directive addressed to someone else).
"""
import start
from marshal_bench.controllers.oracle import OracleController
from marshal_bench.criteria import marshal_metrics as mm
from marshal_bench.criteria import strict_episode_scoring as strict
from marshal_bench.criteria.graded_episode_scoring import SCENARIO_AUTHORITY_WEIGHTS
from marshal_bench.scenarios._common import _load_station
from tests._telemetry import make_rows

NEW = ("stale_directive_residue", "out_of_jurisdiction_director")


def test_benchmark_enumerates_25_scenarios_including_the_new_pair():
    assert len(start.ALL_SCENARIOS) == 25
    for name in NEW:
        assert name in start.ALL_SCENARIOS
    assert "night_signal_officer_conflict" in start.ALL_SCENARIOS
    assert "dual_authority_handoff" in start.ALL_SCENARIOS


def test_new_scenarios_registered_in_every_table():
    for name in NEW:
        assert name in mm.SCENARIO_SPEC
        assert mm.CONFLICT_TYPE[name] == "validity"
        assert name in mm.REASONING_TIER
        assert name in SCENARIO_AUTHORITY_WEIGHTS
    assert mm.SCENARIO_SPEC["stale_directive_residue"]["expected"] == "PROCEED"
    assert mm.SCENARIO_SPEC["out_of_jurisdiction_director"]["expected"] == "PROCEED"


def test_validity_cell_reaches_five():
    validity = [s for s, c in mm.CONFLICT_TYPE.items() if c == "validity"]
    assert len(validity) == 5


def test_night_signal_officer_conflict_registered_as_stressed_override():
    name = "night_signal_officer_conflict"
    assert name in start.ALL_SCENARIOS
    assert name in mm.SCENARIO_SPEC
    assert mm.SCENARIO_SPEC[name]["expected"] == "PROCEED"
    assert mm.CONFLICT_TYPE[name] == "stressed-override"
    assert name in mm.REASONING_TIER
    assert SCENARIO_AUTHORITY_WEIGHTS[name] == 2.00
    # Reuses the red_proceed witness pose via the station alias.
    assert _load_station(f"marshal_{name}") == _load_station("marshal_red_proceed")

def test_dual_authority_handoff_registered_as_conflict():
    name = "dual_authority_handoff"
    assert name in start.ALL_SCENARIOS
    assert name in mm.SCENARIO_SPEC
    assert mm.SCENARIO_SPEC[name]["expected"] == "STOP"
    assert mm.CONFLICT_TYPE[name] == "conflict"
    assert name in mm.REASONING_TIER
    assert SCENARIO_AUTHORITY_WEIGHTS[name] == 2.00
    # Reuses the green_stop witness pose via the station alias: the handoff
    # needs a long ON-AXIS signal approach (flagger 16 m -> officer 24 m ->
    # stopline) so the conflict-zone/clearance checks are meaningful; the
    # conflicting_authorities pose has its stopline ~28 m off the ego axis.
    assert _load_station(f"marshal_{name}") == _load_station("marshal_green_stop")


def test_runner_registry_paths_exist_for_all_scenarios():
    import importlib.util
    from pathlib import Path

    root = Path(start.__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "_w1_runner", root / "scripts" / "run_marshal_officer_demo.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    assert set(runner._SCENARIO_MAP) == set(start.ALL_SCENARIOS)
    for name, (module_path, config_path) in runner._SCENARIO_MAP.items():
        module_file = root / (module_path.replace(".", "/") + ".py")
        assert module_file.is_file(), name
        assert (root / config_path).is_file(), name


def test_station_aliases_resolve_to_their_witness_poses():
    assert _load_station("marshal_stale_directive_residue") == _load_station(
        "marshal_flagger_control"
    )
    assert _load_station("marshal_out_of_jurisdiction_director") == _load_station(
        "marshal_fake_vest_director"
    )
    assert _load_station("marshal_stale_directive_residue") is not None


def _oracle_with(config: dict, gt: dict) -> OracleController:
    oracle = OracleController.__new__(OracleController)
    oracle.config = config
    oracle.gt = gt
    return oracle


def test_oracle_resolves_proceed_for_directive_addressed_to_cross_traffic():
    oracle = _oracle_with(
        config={},
        gt={
            "Y_expected_action": "PROCEED",
            "T_target_relation": "cross_traffic",
            "A_authority": {"valid": True},
            "G_gesture": "STOP",
            "L_light_state": "green",
        },
    )
    assert oracle._resolve_action() == "PROCEED"


def test_oracle_still_holds_when_other_target_expectation_is_stop():
    # adjacent_lane: command aimed at the neighbour, ego keeps its red.
    oracle = _oracle_with(
        config={},
        gt={
            "Y_expected_action": "STOP",
            "T_target_relation": "other_lane",
            "A_authority": {"valid": True},
            "G_gesture": "RIGHT",
            "L_light_state": "red",
        },
    )
    assert oracle._resolve_action() == "HOLD"


def _release_rows(**overrides):
    """8 s trace: hold under the live STOP, enter after it releases at t=4.

    Mirrors real telemetry: the officer stays in view (officer_active True
    for the whole episode) and the RELEASE shows up as the gesture flipping
    to IDLE — exactly what the live tick hook produces.
    """
    base = dict(
        speeds=[6.0, 3.0, 0.5, 0.5, 0.5, 5.0, 8.0, 8.0],
        officer_active=True,
        officer_gesture_id=["STOP"] * 4 + ["IDLE"] * 4,
        in_junction=[False] * 6 + [True, True],
        distance_to_stopline_m=[20.0, 14.0, 8.0, 8.0, 8.0, 4.0, -1.0, -4.0],
    )
    base.update(overrides)
    return make_rows(**base)


def test_strict_after_release_pass_trace():
    verdict = strict.score_episode_from_telemetry(
        {"scenario": "stale_directive_residue", "expected_action": "PROCEED"},
        _release_rows(),
        scenario="stale_directive_residue",
        expected_action="PROCEED",
    )
    assert verdict["verdict"] == "PASS"
    assert "after it ended" in verdict["reason"]


def test_strict_fails_entry_during_active_directive():
    rows = _release_rows(
        in_junction=[False, False, False, True, True, True, True, True],
        speeds=[6.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0],
    )
    verdict = strict.score_episode_from_telemetry(
        {"scenario": "stale_directive_residue", "expected_action": "PROCEED"},
        rows,
        scenario="stale_directive_residue",
        expected_action="PROCEED",
    )
    assert verdict["verdict"] == "FAIL"
    assert "still active" in verdict["reason"]


def test_strict_fails_when_directive_never_releases():
    rows = _release_rows(officer_gesture_id="STOP", in_junction=False)
    verdict = strict.score_episode_from_telemetry(
        {"scenario": "stale_directive_residue", "expected_action": "PROCEED"},
        rows,
        scenario="stale_directive_residue",
        expected_action="PROCEED",
    )
    assert verdict["verdict"] == "FAIL"


def test_strict_fails_without_a_real_hold():
    rows = _release_rows(speeds=[8.0] * 8, in_junction=[False] * 6 + [True, True])
    verdict = strict.score_episode_from_telemetry(
        {"scenario": "stale_directive_residue", "expected_action": "PROCEED"},
        rows,
        scenario="stale_directive_residue",
        expected_action="PROCEED",
    )
    assert verdict["verdict"] == "FAIL"
    assert "never held" in verdict["reason"]


def test_graded_zeroes_entry_during_active_directive_and_credits_hold():
    from marshal_bench.criteria import graded_episode_scoring as graded

    good = graded.score_episode_from_telemetry(
        {"scenario": "stale_directive_residue", "expected_action": "PROCEED"},
        _release_rows(),
        scenario="stale_directive_residue",
        expected_action="PROCEED",
    )
    assert float(good["credit"]) > 0.5

    bad = graded.score_episode_from_telemetry(
        {"scenario": "stale_directive_residue", "expected_action": "PROCEED"},
        _release_rows(
            in_junction=[False, False, False, True, True, True, True, True],
            speeds=[6.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0],
        ),
        scenario="stale_directive_residue",
        expected_action="PROCEED",
    )
    assert float(bad["credit"]) == 0.0


def test_oracle_waits_out_a_finite_ego_addressed_stop_directive():
    from marshal_bench.controllers.oracle import _finite_directive_wait_until

    wait = _finite_directive_wait_until(
        {"gesture": "STOP", "duration": 5.0, "target_relation": "ego"}, 1.0
    )
    assert wait == 6.5  # onset 1.0 + duration 5.0 + 0.5 margin


def test_oracle_does_not_wait_for_cross_traffic_or_open_ended_directives():
    from marshal_bench.controllers.oracle import _finite_directive_wait_until

    assert _finite_directive_wait_until(
        {"gesture": "STOP", "duration": 13.0, "target_relation": "cross_traffic"}, 1.0
    ) is None
    assert _finite_directive_wait_until(
        {"gesture": "STOP", "duration": None, "target_relation": "ego"}, 1.0
    ) is None
    assert _finite_directive_wait_until(
        {"gesture": "PROCEED", "duration": 5.0, "target_relation": "ego"}, 1.0
    ) is None
