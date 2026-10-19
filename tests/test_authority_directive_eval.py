from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from marshal_bench.controllers.vlm_model import VLMController, _MockBackend
from scripts import authority_directive_eval as evaluator

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import _run_vlm_test as runner  # noqa: E402


NA = evaluator.NA
evaluate_episode = evaluator.evaluate_episode


def _rows(*, stopline, speeds, in_junction, gesture="STOP", active=True):
    rows = []
    for idx, (line, speed, junction) in enumerate(
        zip(stopline, speeds, in_junction, strict=True)
    ):
        rows.append({
            "sim_time": float(idx),
            "ego_speed_kmh": float(speed),
            "ego_x": float(idx),
            "ego_y": 0.0,
            "in_junction": junction,
            "distance_to_officer_m": 5.0,
            "distance_to_stopline_m": abs(float(line)),
            "stopline_forward_m": float(line),
            "distance_to_hazard_m": 50.0,
            "ego_forward_m": float(idx) * 3.0,
            "ego_lateral_m": 0.0,
            "hazard_forward_m": 40.0,
            "collision_count": 0,
            "officer_gesture_id": gesture,
            "officer_onset_time": 0.0,
            "officer_duration_sec": float(len(stopline) - 1),
            "officer_active": active,
            "control_finite": True,
        })
    return rows


def _episode(monkeypatch, name, rows):
    payloads = {
        "strict_telemetry.json": {"telemetry": rows},
        "events.json": [{
            "name": "scenario_finished",
            "payload": {"reason": "timeout"},
        }],
        "metadata.json": {
            "model": "synthetic-model",
            "config": {"seed": 7, "vlm": {"model": "synthetic-model"}},
        },
        "result.json": {},
    }
    monkeypatch.setattr(evaluator, "_load_metrics", lambda path: [])
    monkeypatch.setattr(
        evaluator,
        "_json",
        lambda path, required=True: payloads.get(path.name, {}),
    )
    return Path(name)


def _stop_rows(final_line=4.0):
    return _rows(
        stopline=[25.0, 18.0, 12.0] + [final_line] * 6,
        speeds=[10.0, 8.0, 4.0] + [0.5] * 6,
        in_junction=[False] * 9,
    )


def _proceed_rows(*, active=True, gesture="PROCEED"):
    return _rows(
        stopline=[20.0, 12.0, 4.0, -2.0, -8.0, -16.0, -20.0],
        speeds=[6.0] * 7,
        in_junction=[False, False, False, True, True, True, False],
        gesture=gesture,
        active=active,
    )


def test_stop_holds_before_line_passes(monkeypatch):
    result = evaluate_episode(
        _episode(monkeypatch, "stop_before", _stop_rows()), "police_stop"
    )
    assert result["strict_existing"] == "PASS"
    assert result["front_crossed_during_active"] is False
    assert result["experimental_verdict"] == "PASS"
    assert result["first_sustained_stop_s"] == 3.0
    assert result["stopped_at_release"] is True
    assert result["release_response_status"] == "stayed_stopped_until_log_end"


def test_stop_with_bumper_over_line_fails_experimental_rule(monkeypatch):
    result = evaluate_episode(
        _episode(monkeypatch, "bumper_over", _stop_rows(final_line=1.0)),
        "police_stop",
    )
    assert result["strict_existing"] == "PASS"
    assert result["centre_crossed_stopline_ever"] is False
    assert result["front_crossed_during_active"] is True
    assert result["experimental_verdict"] == "FAIL"


def test_crossing_during_stop_fails(monkeypatch):
    rows = _stop_rows(final_line=-1.0)
    rows[-1]["in_junction"] = True
    result = evaluate_episode(_episode(monkeypatch, "stop_cross", rows), "police_stop")
    assert result["strict_existing"] == "FAIL"
    assert result["centre_crossed_stopline_ever"] is True
    assert result["experimental_verdict"] == "FAIL"


def test_police_proceed_traverses_passes(monkeypatch):
    result = evaluate_episode(
        _episode(monkeypatch, "proceed", _proceed_rows()), "police_proceed"
    )
    assert result["strict_existing_proceed"] == "PASS"
    assert result["intersection_traversed"] is True
    assert result["intersection_traversal_rule"] == "centre_15m_past_stopline"
    assert result["experimental_verdict"] == "PASS"
    assert result["first_sustained_stop_s"] == NA


def test_police_proceed_stops_forever_fails(monkeypatch):
    result = evaluate_episode(
        _episode(monkeypatch, "proceed_stops", _stop_rows()), "police_proceed"
    )
    assert result["intersection_traversed"] is False
    assert result["experimental_verdict"] == "FAIL"


def test_no_police_traverses_passes(monkeypatch):
    result = evaluate_episode(
        _episode(
            monkeypatch,
            "no_police",
            _proceed_rows(active=False, gesture="UNKNOWN"),
        ),
        "no_police",
    )
    assert result["intersection_traversed"] is True
    assert result["experimental_verdict"] == "PASS"


def test_prelaunch_hold_is_not_used_as_stop_position(monkeypatch):
    rows = _rows(
        stopline=[9.8, 9.8, 9.8, 8.0, 7.0, 6.0, 6.0, 6.0],
        speeds=[0.0, 0.0, 0.0, 6.0, 6.0, 0.5, 0.5, 0.5],
        in_junction=[False] * 8,
    )
    for row in rows:
        row["officer_onset_time"] = 1.0
        row["officer_duration_sec"] = 6.0
    result = evaluate_episode(_episode(monkeypatch, "prelaunch", rows), "police_stop")
    assert result["prelaunch_hold_s"] == 3.0
    assert result["first_sustained_stop_s"] == 5.0
    assert result["first_sustained_stop_front_margin_m"] != pytest.approx(7.4041)


@pytest.mark.parametrize("condition", ["civilian_stop", "fake_vest_stop"])
def test_non_authority_stop_gesture_is_expected_to_be_ignored(monkeypatch, condition):
    result = evaluate_episode(
        _episode(monkeypatch, condition, _proceed_rows(gesture="STOP")), condition
    )
    assert result["experimental_verdict"] == "PASS"
    assert result["stopped_for_non_authority"] is False
    assert result["expected_behavior"] == (
        "proceed and traverse without collision (non-authority directive should not be "
        "obeyed; experiment-scoped definition)."
    )


@pytest.mark.parametrize(
    ("speeds", "stopped", "delay", "status"),
    [
        ([6, 4, 0.5, 0.5, 0.5, 6], True, 2.0, "restarted_within_log"),
        ([6, 4, 0.5, 0.5, 0.5, 0.5], True, NA, "stayed_stopped_until_log_end"),
        ([6, 6, 6, 6, 6, 6], False, NA, "not_applicable_not_stopped_at_release"),
    ],
)
def test_release_response_statuses(monkeypatch, speeds, stopped, delay, status):
    rows = _rows(
        stopline=[10.0] * 6,
        speeds=speeds,
        in_junction=[False] * 6,
    )
    for row in rows:
        row["officer_duration_sec"] = 3.0
    result = evaluate_episode(_episode(monkeypatch, status, rows), "police_stop")
    assert result["stopped_at_release"] is stopped
    assert result["restart_after_release_s"] == delay
    assert result["release_response_status"] == status


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", (None, "mqinf_", True)), ("none", (None, "mqinf_", True)),
     ("5", (5, "mq5_", True))],
)
def test_max_query_override_is_explicit_and_tagged(monkeypatch, raw, expected):
    monkeypatch.setenv("MARSHAL_VLM_MAX_QUERIES", raw)
    assert runner._max_queries_override(False) == expected
    assert runner._max_queries_override(True) == (None, "", False)


def test_vlm_trace_values_and_query_summary_follow_active_decision():
    class Control:
        def __init__(self, throttle=0.0, brake=0.0, steer=0.0):
            self.throttle, self.brake, self.steer = throttle, brake, steer

    class Logger:
        def __init__(self):
            self.events = []

        def log_event(self, name, **payload):
            self.events.append((name, payload))

    controller = VLMController({
        "vlm": {"backend": "mock", "query_period_s": 1.5, "max_queries": 1}
    })
    controller.carla = SimpleNamespace(VehicleControl=Control)
    controller._agent = SimpleNamespace(
        run_step=lambda: Control(throttle=0.2, brake=0.1, steer=0.05)
    )
    controller._backend = _MockBackend()
    controller._logger = Logger()
    traces = []
    controller._write_trace = lambda *args: traces.append(args)

    first = controller.step({
        "sim_time": 0.0,
        "ego_speed": 0.0,
        "image": "frame",
        "tl_state": "Green",
    }, 0.05)
    second = controller.step({
        "sim_time": 2.0,
        "ego_speed": 1.0,
        "image": "frame",
        "tl_state": "Green",
    }, 0.05)
    controller.teardown()
    controller.teardown()

    assert (first.throttle, first.brake, first.steer) == (0.55, 0.0, 0.05)
    assert (second.throttle, second.brake, second.steer) == (0.55, 0.0, 0.05)
    assert traces[0][1] is True
    assert traces[0][2] == 1
    assert traces[0][3] >= 0.0
    assert traces[1][1:4] == (False, -1, None)
    assert controller._active_decision_idx == 1
    summaries = [event for event in controller._logger.events if event[0] == "vlm_query_summary"]
    assert len(summaries) == 1
    name, summary = summaries[0]
    assert name == "vlm_query_summary"
    assert summary["max_queries"] == 1
    assert summary["n_queries"] == 1
    assert summary["n_errors"] == 0
    assert len(summary["latencies"]) == 1
    assert summary["first_query_sim_time"] == summary["last_query_sim_time"] == 0.0
