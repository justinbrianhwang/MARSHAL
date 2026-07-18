import json

import pytest

from scripts import calibrate_town
from tests._telemetry import clean_stop_before_line


def _result_with_artifact(tmp_path, *, passed=False):
    rows = clean_stop_before_line()
    rows[0]["distance_to_hazard_m"] = 12.0
    rows[1]["distance_to_hazard_m"] = 4.5
    artifact = tmp_path / "strict_telemetry.json"
    artifact.write_text(json.dumps({"telemetry": rows}), encoding="utf-8")
    return {
        "scenario": "green_stop",
        "expected_action": "STOP",
        "scene_setup": {"errors": []},
        "strict_scoring": {
            "passed": passed,
            "verdict": "PASS" if passed else "FAIL",
            "reason": "synthetic strict failure",
            "expected_action": "STOP",
            "artifacts": {"strict_telemetry_json": str(artifact)},
        },
    }, rows


def test_calibration_report_shapes_credit_engagement_and_failure_diagnosis(tmp_path):
    result, rows = _result_with_artifact(tmp_path, passed=False)

    report = calibrate_town.build_report(
        "Town01",
        {"green_stop": result},
        {"ambulance_yield": "off-road shoulder required"},
        timestamp="2026-01-01T00:00:00+00:00",
    )

    item = report["per_scenario"]["green_stop"]
    assert report["feasible_n"] == 1
    assert report["passed_n"] == 0
    assert report["masked"] == {"ambulance_yield": "off-road shoulder required"}
    assert 0.0 <= item["graded_credit"] <= 1.0
    assert item["engagement_factor"] == pytest.approx(1.0)
    assert item["diagnosis"] == {
        "strict_criterion": "STOP",
        "strict_verdict": "FAIL",
        "strict_reason": "synthetic strict failure",
        "final_ego_to_stopline_m": round(float(rows[-1]["distance_to_stopline_m"]), 4),
        "min_distance_to_hazard_m": 4.5,
        "min_distance_to_officer_m": min(float(row["distance_to_officer_m"]) for row in rows),
        "strict_telemetry_json": str(tmp_path / "strict_telemetry.json"),
    }


def test_no_result_is_a_diagnosable_gate_failure():
    item = calibrate_town.shape_episode_result("green_stop", None)

    assert item["passed"] is False
    assert item["diagnosis"]["strict_verdict"] == "NO_RESULT"
    assert item["diagnosis"]["strict_telemetry_json"] is None


def test_load_town_inputs_requires_and_shapes_explicit_mask(tmp_path):
    (tmp_path / "stations_town01.json").write_text(json.dumps({
        "stations": {"green_stop": {"x": 1, "y": 2, "yaw": 3}}
    }), encoding="utf-8")
    (tmp_path / "feasibility_town01.json").write_text(json.dumps({
        "green_stop": {"feasible": True, "reason": "ok"},
        "ambulance_yield": {"feasible": False, "reason": "no shoulder"},
    }), encoding="utf-8")

    stations, masked = calibrate_town._load_town_inputs("Town01", tmp_path)

    assert set(stations) == {"green_stop"}
    assert masked == {"ambulance_yield": "no shoulder"}
