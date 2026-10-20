from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts import authority_directive_report as report


def _rows() -> list[dict[str, object]]:
    rows = []
    for idx in range(7):
        rows.append({
            "sim_time": float(idx), "ego_speed_kmh": 8.0 if idx < 3 else 0.5,
            "ego_x": float(idx), "ego_y": 0.0, "ego_yaw_deg": 0.0,
            "applied_throttle": 0.2, "applied_brake": 0.0, "applied_steer": 0.0,
            "in_junction": False, "distance_to_officer_m": 5.0,
            "distance_to_stopline_m": 8.0, "stopline_forward_m": 8.0,
            "distance_to_hazard_m": 50.0, "ego_forward_m": float(idx),
            "ego_lateral_m": 0.0, "hazard_forward_m": 40.0, "collision_count": 0,
            "officer_gesture_id": "STOP", "officer_onset_time": 0.0,
            "officer_duration_sec": 6.0, "officer_active": True, "control_finite": True,
        })
    return rows


def _write_episode(root: Path, model: str, *, vlm: bool = False) -> Path:
    episode = root / model / "police_stop_s1" / f"{model}_episode"
    episode.mkdir(parents=True)
    rows = _rows()
    metadata = {
        "model": "Qwen/Qwen3-VL" if vlm else model,
        "config": {
            "episode_id": episode.name, "seed": 1, "timeout_sec": 25,
            "officer": {
                "authority_type": "police", "gesture": "STOP", "distance": 13.0
            },
            "vlm": {"max_queries": None} if vlm else {},
        },
    }
    events = [{"t": 0.0, "name": "scenario_finished", "payload": {"reason": "timeout"}}]
    if vlm:
        events += [
            {"t": 0.0, "name": "vlm_decision", "payload": {"action": "GO", "who": "none", "latency_s": 1.0}},
            {"t": 4.0, "name": "vlm_decision", "payload": {"action": "STOP", "who": "police", "latency_s": 2.0}},
            {"t": 10.0, "name": "vlm_query_summary", "payload": {
                "max_queries": None, "n_queries": 2, "n_errors": 0,
                "latencies": [1.0, 2.0], "first_query_sim_time": 0.0, "last_query_sim_time": 4.0,
            }},
        ]
    (episode / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (episode / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (episode / "strict_telemetry.json").write_text(json.dumps({"telemetry": rows}), encoding="utf-8")
    (episode / "result.json").write_text("{}", encoding="utf-8")
    with (episode / "metrics.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["t", "key", "value"])
        for row in rows:
            writer.writerow([row["sim_time"], "tl_state", "Green"])
    if vlm:
        trace = episode / "vlm_debug" / "vlm_trace.csv"
        trace.parent.mkdir()
        trace.write_text(
            "sim_time,active_action,active_decision_idx,applied_throttle,applied_brake,base_throttle,base_brake\n"
            "0.0,GO,1,0.2,0.0,0.2,0.0\n4.0,STOP,2,0.0,1.0,0.2,0.0\n",
            encoding="utf-8",
        )
    return episode


def test_generate_writes_deliverables_and_marks_missing(tmp_path):
    runs, out = tmp_path / "runs", tmp_path / "out"
    _write_episode(runs, "tcp")
    _write_episode(runs, "qwen3vl", vlm=True)

    counts = report.generate([runs], out)

    assert counts == {"episodes": 42, "baseline_episodes": 0, "missing": 40}
    per_episode = list(csv.DictReader((out / "results" / "per_episode.csv").open(encoding="utf-8")))
    qwen = next(row for row in per_episode if row["model"] == "qwen3vl" and row["seed"] == "1")
    assert qwen["action_sequence"] == "GO,STOP"
    assert qwen["mean_latency_s"] == "1.5"
    assert next(row for row in per_episode if row["seed"] == "2")["missing_reason"]
    audit = list(csv.DictReader((out / "results" / "vlm_query_audit.csv").open(encoding="utf-8")))[0]
    assert audit["distinct_active_decisions_applied"] == "2"
    assert audit["active_decision_advanced_after_3_1_s"] == "True"
    assert (out / "plots" / "overview_2x2.png").is_file()
    assert (out / "plots" / "overview_grid.png").is_file()
    assert (out / "telemetry" / "qwen3vl_police_stop_s1.csv").is_file()
    assert (out / "results" / "summary_by_model.csv").is_file()
    assert (out / "results" / "directive_sensitivity.csv").is_file()
    assert qwen["officer_distance_m"] == "13.0"
    assert "descriptive only" in (out / "results" / "pairwise.md").read_text(encoding="utf-8")


def test_civilian_proceed_is_ordered_next_to_civilian_stop():
    assert report.CONDITIONS[-2:] == ("civilian_stop", "civilian_proceed")
    assert "civilian_proceed" in report.COLORS


def test_direct_model_root_is_accepted(tmp_path):
    runs = tmp_path / "_INVALID_tcp_fixture"
    episode = runs / "police_stop_s1" / "tcp_episode"
    episode.mkdir(parents=True)
    assert report._discover_model_roots([runs]) == {"tcp": [runs]}


def test_oracle_is_reference_only_and_excluded_from_comparisons(tmp_path):
    runs, out = tmp_path / "runs", tmp_path / "out"
    _write_episode(runs, "tcp")
    _write_episode(runs, "oracle")

    report.generate([runs], out)

    per_episode = list(
        csv.DictReader((out / "results" / "per_episode.csv").open(encoding="utf-8"))
    )
    oracle = next(row for row in per_episode if row["model"] == "oracle")
    assert oracle["model_display_name"] == "Oracle"
    assert oracle["is_reference"] == "True"
    summary = list(
        csv.DictReader((out / "results" / "summary.csv").open(encoding="utf-8"))
    )
    summary_by_model = list(
        csv.DictReader(
            (out / "results" / "summary_by_model.csv").open(encoding="utf-8")
        )
    )
    sensitivity = list(
        csv.DictReader(
            (out / "results" / "directive_sensitivity.csv").open(encoding="utf-8")
        )
    )
    assert all(row["model"] != "oracle" for row in summary)
    assert any(row["model"] == "oracle" and row["is_reference"] == "True" for row in summary_by_model)
    assert all(row["model"] != "oracle" for row in sensitivity)
