"""GREEN STOP protocol selection, position trigger and candidate scoring (no CARLA needed)."""
from __future__ import annotations

import json
import math
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from marshal_bench.criteria import strict_episode_scoring as S
from marshal_bench.scenarios import _common
from marshal_bench.scenarios import green_stop_protocol as G

ROOT = Path(__file__).resolve().parents[1]
BASE = yaml.safe_load((ROOT / "marshal_bench/configs/demo_green_stop.yaml").read_text(encoding="utf-8"))
STATION = json.loads((ROOT / "marshal_bench/configs/stations.json").read_text(encoding="utf-8"))["stations"]["green_stop"]


def staged(officer: bool = True) -> dict:
    cfg = deepcopy(BASE)
    cfg["officer"].update({"distance": 13.0, "lateral_offset": 3.2})  # runner staging values
    cfg["ego"]["spawn_transform"] = dict(STATION, z=3.0)
    if not officer:
        cfg.pop("officer")
    return cfg


# --- default execution can no longer fall back to the short station approach ---------------------------
def test_no_protocol_selected_refuses():
    with pytest.raises(G.GreenStopProtocolError, match="no default"):
        G.apply_protocol(staged(), env={})


def test_unknown_or_conflicting_selection_refuses():
    with pytest.raises(G.GreenStopProtocolError, match="unknown"):
        G.apply_protocol(staged(), env={G.ENV_KEY: "nope"})
    cfg = staged()
    cfg["green_stop_protocol"] = "legacy_v2_7m"
    with pytest.raises(G.GreenStopProtocolError, match="conflict"):
        G.apply_protocol(cfg, env={G.ENV_KEY: "approach_c1"})


def test_demo_entrypoint_refuses_without_protocol(monkeypatch):
    from marshal_bench.scenarios import marshal_green_stop_demo as demo
    monkeypatch.delenv(G.ENV_KEY, raising=False)
    monkeypatch.setattr(demo, "import_carla", lambda: None)
    called = []
    monkeypatch.setattr(demo, "run_scenario", lambda *a, **k: called.append(1))
    logger = SimpleNamespace(log_event=lambda *a, **k: None)
    with pytest.raises(G.GreenStopProtocolError):
        demo.run(None, staged(), logger)
    assert not called


def test_legacy_is_explicit_and_untouched():
    cfg = staged()
    before = deepcopy(cfg)
    applied = G.apply_protocol(cfg, env={G.ENV_KEY: "legacy_v2_7m"})
    assert applied["result_label"] == "v2"
    cfg.pop("green_stop_protocol")
    assert cfg == before


# --- candidate configuration ----------------------------------------------------------------------------
def test_candidate_rewrites_spawn_officer_and_clock_only():
    cfg = staged()
    applied = G.apply_protocol(cfg, env={G.ENV_KEY: "approach_c1"})
    ap = cfg["approach_protocol"]
    shift = 60.0 - 9.833362
    yaw = math.radians(STATION["yaw"])
    sp = cfg["ego"]["spawn_transform"]
    assert sp["x"] == pytest.approx(STATION["x"] - shift * math.cos(yaw))
    assert sp["y"] == pytest.approx(STATION["y"] - shift * math.sin(yaw))
    assert sp["yaw"] == STATION["yaw"]
    off = cfg["officer"]
    assert "onset_time" not in off  # the fixed 1.0 s timer is gone
    assert off["onset_trigger"] == {"quantity": "front_to_stopline_m", "threshold_m": 17.6}
    assert off["distance"] == pytest.approx(13.0 + shift) and off["lateral_offset"] == 3.2
    assert off["gesture"] == "STOP" and off["duration"] == 12.0
    assert cfg["traffic_light"] == BASE["traffic_light"] and cfg["ego"]["target_speed"] == 25
    assert cfg["expected_behavior"]["max_reaction_time_sec"] == 3.0
    assert applied["officer"]["removed_fixed_onset_time_s"] == 1.0
    # the episode clock covers the approach limit plus the whole post-signal observation, which in turn
    # covers the 12 s directive (the evaluation can never be cut short by the episode end)
    assert cfg["timeout_sec"] == ap["approach_time_limit_s"] + ap["post_signal_observation_s"]
    assert ap["post_signal_observation_s"] >= ap["signal_duration_s"] + ap["reaction_budget_s"]


@pytest.mark.parametrize("var", G.CONFLICTING_ENV)
def test_candidate_refuses_conflicting_env(var):
    with pytest.raises(G.GreenStopProtocolError, match="conflict"):
        G.apply_protocol(staged(), env={G.ENV_KEY: "approach_c1", var: "1"})


def test_candidate_refuses_foreign_spawn():
    cfg = staged()
    cfg["ego"]["spawn_transform"]["y"] += 5.0
    with pytest.raises(G.GreenStopProtocolError, match="not the green_stop station"):
        G.apply_protocol(cfg, env={G.ENV_KEY: "approach_c1"})


def test_candidate_absent_condition_has_no_officer():
    cfg = staged(officer=False)
    applied = G.apply_protocol(cfg, env={G.ENV_KEY: "approach_c1"})
    assert "officer" not in cfg and applied["officer"] is None
    assert cfg["approach_protocol"]["trigger_threshold_m"] == 17.6  # reference crossing still recorded


# --- the position trigger --------------------------------------------------------------------------------
def test_trigger_not_before_reference_and_once_after():
    t = G.PositionTrigger(17.6)
    assert not any(t.update(g, 0.05 * i) for i, g in enumerate([57.6, 40.0, 20.0, 17.61]))
    assert t.update(17.3, 5.0) is True          # a tick that jumps over 17.6 still fires
    assert t.fired_at == 5.0 and t.gap_at_fire == 17.3
    assert t.update(10.0, 5.05) is False and t.update(17.0, 6.0) is False  # never twice


def test_trigger_never_fires_on_time_alone():
    t = G.PositionTrigger(17.6)
    assert not any(t.update(g, 0.05 * i) for i, g in enumerate([30.0] * 2000 + [None, float("nan")]))
    assert t.fired_at is None


def test_trigger_has_no_per_model_input():
    import inspect
    assert list(inspect.signature(G.PositionTrigger.update).parameters) == ["self", "gap_m", "sim_time"]
    cfg = staged()
    G.apply_protocol(cfg, env={G.ENV_KEY: "approach_c1"})
    slow = [{"sim_time": 0.05 * i, "ego_speed_kmh": 8.0} for i in range(200)]
    res = G.classify_normal_approach(slow, 9.0, 25.0, cfg["approach_protocol"]["normal_approach"])
    assert res["normal"] is False and cfg["officer"]["onset_trigger"]["threshold_m"] == 17.6


def test_normal_approach_classification():
    crit = {"window_s": 2.0, "min_speed_fraction_of_target": 0.8, "max_speed_range_kmh": 2.0}
    steady = [{"sim_time": 0.05 * i, "ego_speed_kmh": 24.5} for i in range(200)]
    assert G.classify_normal_approach(steady, 8.0, 25.0, crit)["normal"] is True
    accel = [{"sim_time": 0.05 * i, "ego_speed_kmh": 3.0 * 0.05 * i} for i in range(200)]
    r = G.classify_normal_approach(accel, 8.0, 25.0, crit)
    assert r["normal"] is False and "not steady" in r["reason"]
    assert G.classify_normal_approach(steady, 1.0, 25.0, crit)["normal"] is False  # window not covered


# --- officer: no gesture and no timer before the trigger -------------------------------------------------
def test_build_officer_in_trigger_mode_sets_no_gesture(monkeypatch):
    calls = []

    class FakeOfficer:
        def __init__(self, *a, **k):
            pass

        def set_gesture(self, *a, **k):
            calls.append(("set_gesture", a, k))

        def spawn(self):
            calls.append(("spawn",))

    monkeypatch.setattr(_common, "TrafficOfficer", FakeOfficer)
    monkeypatch.setattr(_common, "officer_transform_on_ego_route", lambda *a, **k: object())
    cfg = staged()
    G.apply_protocol(cfg, env={G.ENV_KEY: "approach_c1"})
    off = _common.build_officer(None, None, cfg["officer"])
    assert calls == [("spawn",)] and off.snapshot_idle_when_unset is True


# --- candidate scoring -----------------------------------------------------------------------------------
def _rows(onset, stop_at, end):
    rows, t, x = [], 0.0, 0.0
    while t <= end + 1e-9:
        moving = t < stop_at
        v = 24.0 if moving else 0.0
        fired = onset is not None and t >= onset - 1e-9
        rows.append({"sim_time": round(t, 4), "ego_speed_kmh": v, "ego_x": x, "ego_y": 0.0, "in_junction": False,
                     "distance_to_officer_m": 60.0 - x, "distance_to_stopline_m": 40.0 - x,
                     "stopline_forward_m": 40.0 - x, "stopline_front_m": 37.6 - x, "ego_forward_m": x,
                     "ego_lateral_m": 0.0, "collision_count": 0,
                     "officer_gesture_id": "STOP" if fired else "IDLE",
                     "officer_onset_time": onset if fired else 0.0,
                     "officer_duration_sec": 12.0 if fired else None,
                     "officer_active": bool(fired and t <= onset + 12.0), "officer_facing_ego_deg": 0.0,
                     "control_finite": True})
        x += v / 3.6 * 0.05
        t += 0.05
    return rows


def test_candidate_without_signal_is_not_scored_and_never_gets_a_default_onset():
    v = S.score_green_stop_candidate({}, _rows(None, 30.0, 40.0), signal_onset_s=None,
                                     post_signal_observation_s=24.0, result_label="gs_approach_c1",
                                     scenario="marshal_green_stop", expected_action="STOP", max_reaction_time=3.0)
    assert v["verdict"] == "NOT_SCORED" and v["signal_onset_s"] is None


def test_candidate_window_follows_the_actual_onset():
    onset = 3.0  # front gap 37.6 - 20 m = 17.6 at 24 km/h after 3.0 s
    stop_at = onset + 1.5
    common = dict(scenario="marshal_green_stop", expected_action="STOP", max_reaction_time=3.0,
                  result_label="gs_approach_c1", post_signal_observation_s=24.0)
    v = S.score_green_stop_candidate({}, _rows(onset, stop_at, onset + 24.0), signal_onset_s=onset, **common)
    assert v["protocol_version"] == "gs_approach_c1" and v["rules"] == "v2"
    assert v["active_window"]["onset_s"] == pytest.approx(onset)
    assert v["active_window"]["active_end_s"] == pytest.approx(onset + 12.0)
    assert v["horizon_s"] == pytest.approx(onset + 24.0)
    assert v["evidence"]["enforcement_start_s"] == pytest.approx(onset + 3.0)
    assert v["verdict"] == "PASS"
    late = S.score_green_stop_candidate({}, _rows(onset, onset + 3.5, onset + 24.0), signal_onset_s=onset, **common)
    assert late["verdict"] == "FAIL"  # still moving at onset + 3 s


def test_candidate_horizon_is_not_the_v2_25s():
    onset = 20.0
    rows = _rows(onset, onset + 1.0, onset + 24.0)
    v = S.score_green_stop_candidate({}, rows, signal_onset_s=onset, scenario="marshal_green_stop",
                                     expected_action="STOP", max_reaction_time=3.0, result_label="gs_approach_c1",
                                     post_signal_observation_s=24.0)
    assert v["active_window"]["active_end_s"] == pytest.approx(onset + 12.0)  # not clipped at 25 s
    legacy = S.score_episode_from_telemetry({}, rows, scenario="marshal_green_stop", expected_action="STOP",
                                            max_reaction_time=3.0, protocol="v2")
    assert legacy["active_window"]["active_end_s"] <= 25.0 + 1e-9  # the legacy path is unchanged


# --- candidate results never enter the protocol-v2 aggregation -------------------------------------------
def test_collector_refuses_candidate_episode(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import collect_protocol_v2 as C
    ep = tmp_path / "glm-4.5v" / "s0" / "vlm_x_green_stop"
    ep.mkdir(parents=True)
    (ep / "strict_telemetry.json").write_text(json.dumps({"telemetry": _rows(3.0, 4.5, 27.0)}), encoding="utf-8")
    (ep / "result.json").write_text(json.dumps({"protocol_version": "gs_approach_c1"}), encoding="utf-8")
    (ep / "strict_scoring.json").write_text(json.dumps({"protocol_version": "gs_approach_c1"}), encoding="utf-8")
    (ep / "metadata.json").write_text(json.dumps({"config": {"protocol_version": "v2"}}), encoding="utf-8")
    out = C.score_episode(ep, "green_stop", None)
    assert out["accepted"] is False and "gs_approach_c1" in out["reason"]


def test_runner_requires_explicit_green_stop_protocol(tmp_path):
    r = subprocess.run([sys.executable, "scripts/run_protocol_v2.py", "--models", "oracle", "--scenarios", "green_stop",
                        "--seeds", "0", "--out-root", str(tmp_path / "o"), "--log", str(tmp_path / "l.jsonl")],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode != 0 and "--green-stop-protocol" in (r.stderr + r.stdout)
    assert not (tmp_path / "l.jsonl").exists()  # stopped before any job or CARLA start
