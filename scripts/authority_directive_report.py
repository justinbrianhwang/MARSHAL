"""Build authority-directive experiment tables, plots, and tidy telemetry."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import authority_directive_eval as evaluator


NA = evaluator.NA
CONDITIONS = evaluator.CONDITIONS
MODEL_DISPLAY_NAMES = {
    "tcp": "TCP",
    "neat": "NEAT",
    "interfuser": "InterFuser",
    "qwen3vl": "Qwen3-VL-235B",
    "qwen25vl": "Qwen2.5-VL-72B",
    "glm45v": "GLM-4.5V",
    "oracle": "Oracle",
}
MODEL_ORDER = tuple(MODEL_DISPLAY_NAMES)
GROUP_RE = re.compile(
    rf"^({'|'.join(map(re.escape, CONDITIONS))})_s([123])$"
)
BASELINE_CONDITIONS = {
    "gstop": "police_stop",
    "gnonstop": "police_proceed",
    "gabsent": "no_police",
}
EXPECTED_BEHAVIOR = evaluator.EXPECTED_BEHAVIOR
COLORS = {
    "police_stop": "#d62728",
    "police_stop_near": "#ff9896",
    "police_proceed": "#2ca02c",
    "police_proceed_near": "#98df8a",
    "no_police": "#1f77b4",
    "civilian_stop": "#9467bd",
    "civilian_proceed": "#c5b0d5",
}
LINESTYLES = {1: "-", 2: "--", 3: ":"}

PER_EPISODE_COLUMNS = [
    "model", "model_display_name", "is_reference", "condition", "seed",
    "episode_id", "episode_dir",
    "config_id", "config_max_queries", "config_timeout_sec", "config_seed",
    "officer_condition", "officer_authority_type", "officer_gesture",
    "officer_blueprint", "officer_distance_m", "max_speed_kmh", "stop_position_m",
    "front_crossed_during_active", "front_crossed_ever",
    "centre_crossed_stopline_ever", "intersection_traversed",
    "expected_behavior", "experimental_verdict", "experimental_reason",
    "strict_existing", "initial_speed_kmh", "end_reason", "collisions",
    "time_to_cross_stop_line_s", "min_front_margin_m",
    "first_sustained_stop_time_s", "prelaunch_hold_s", "stopped_for_non_authority",
    "stopped_at_release", "restart_after_release_s", "release_response_status",
    "n_queries", "mean_latency_s",
    "median_latency_s", "max_latency_s", "action_sequence", "who_sequence",
    "missing_reason",
]

SUMMARY_COLUMNS = [
    "model", "model_display_name", "is_reference", "condition", "successes",
    "expected_trials", "successes_of_3",
    "rate", "max_speed_mean_kmh", "max_speed_sd_kmh", "max_speed_mean_plus_minus_sd",
    "mean_stop_position_m", "stop_line_crossing_count", "mean_time_to_cross_s",
    "vlm_query_count_mean",
    "vlm_latency_mean_s", "vlm_latency_median_s", "missing_episodes",
]

SENSITIVITY_COLUMNS = [
    "model", "model_display_name", "comparison",
    "time_to_cross_difference_s", "min_front_margin_difference_m",
    "max_speed_difference_kmh", "success_difference",
]

AUDIT_COLUMNS = [
    "model", "condition", "seed", "episode_id", "episode_dir", "n_queries",
    "first_query_time_s", "last_query_time_s", "effective_max_queries", "n_errors",
    "distinct_active_decisions_applied", "active_decision_advanced_after_3_1_s",
    "ticks_control_differs_from_base", "missing_reason",
]

TELEMETRY_COLUMNS = [
    "sim_time", "speed_kmh", "throttle", "brake", "steer", "x", "y", "yaw",
    "stopline_forward_m", "front_margin_m", "officer_active", "officer_gesture_id",
    "tl_state", "in_junction", "collision_count", "vlm_active_action",
]


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 4) -> Any:
    number = _finite(value)
    return round(number, digits) if number is not None else NA


def _nested(mapping: dict[str, Any], *keys: str, default: Any = NA) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return default if value is None else value


def _event_payload(events: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("name") == name and isinstance(event.get("payload"), dict):
            return event["payload"]
    return {}


def _model_sort_key(model: str) -> tuple[int, str]:
    return (
        MODEL_ORDER.index(model) if model in MODEL_ORDER else len(MODEL_ORDER),
        model,
    )


def _model_display_name(model: str) -> str:
    return MODEL_DISPLAY_NAMES.get(model, model)


def _is_reference(model: str) -> bool:
    return model == "oracle"


def _model_name(path: Path, metadata: dict[str, Any] | None = None) -> str | None:
    name = path.name.lower()
    raw = str((metadata or {}).get("model", "")).lower()
    aliases = {
        "qwen25vl": ("qwen25vl", "qwen2.5", "qwen-2.5"),
        "qwen3vl": ("qwen3vl", "qwen3-vl", "qwen/qwen3"),
        "glm45v": ("glm45v", "glm-4.5v", "glm_4.5v"),
        "interfuser": ("interfuser",),
        "oracle": ("oracle",),
        "neat": ("neat",),
        "tcp": ("tcp",),
    }
    for model, needles in aliases.items():
        if any(needle in name or needle in raw for needle in needles):
            return model
    return name or None


def _discover_model_roots(run_roots: Iterable[Path]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = defaultdict(list)
    for root in run_roots:
        if not root.is_dir():
            raise FileNotFoundError(f"runs directory does not exist: {root}")
        children = [child for child in root.iterdir() if child.is_dir()]
        if any(GROUP_RE.match(child.name) for child in children):
            model = _model_name(root)
            if model is None:
                episode = next(
                    (ep for group in children for ep in group.iterdir() if ep.is_dir()),
                    None,
                )
                metadata = _read_json(episode / "metadata.json", {}) if episode else {}
                model = _model_name(root, metadata)
            if model is None:
                raise ValueError(f"cannot infer model for runs directory: {root}")
            found[model].append(root)
            continue
        for child in children:
            groups = [group for group in child.iterdir() if group.is_dir()]
            if any(GROUP_RE.match(group.name) for group in groups):
                found[child.name.lower()].append(child)
    if not found:
        raise ValueError("no authority-directive model run directories were found")
    return dict(found)


def _episode_dirs(group_dir: Path) -> list[Path]:
    return sorted(
        path for path in group_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ) if group_dir.is_dir() else []


def _iter_experiment_slots(
    model_roots: dict[str, list[Path]],
) -> Iterable[tuple[str, str, int, Path | None]]:
    for model in sorted(model_roots, key=_model_sort_key):
        roots = model_roots.get(model, [])
        if not roots:
            continue
        for condition in CONDITIONS:
            for seed in (1, 2, 3):
                episodes: list[Path] = []
                for root in roots:
                    episodes.extend(_episode_dirs(root / f"{condition}_s{seed}"))
                if episodes:
                    for episode in episodes:
                        yield model, condition, seed, episode
                else:
                    yield model, condition, seed, None


def _load_episode_artifacts(episode_dir: Path) -> dict[str, Any]:
    metadata = _read_json(episode_dir / "metadata.json", {})
    events_raw = _read_json(episode_dir / "events.json", [])
    events = events_raw if isinstance(events_raw, list) else []
    strict = _read_json(episode_dir / "strict_telemetry.json", {})
    rows = strict.get("telemetry", []) if isinstance(strict, dict) else []
    rows = rows if isinstance(rows, list) else []
    trace = _read_csv(episode_dir / "vlm_debug" / "vlm_trace.csv")
    metrics = _read_csv(episode_dir / "metrics.csv")
    return {
        "metadata": metadata if isinstance(metadata, dict) else {},
        "events": events,
        "strict_rows": rows,
        "trace": trace,
        "metrics": metrics,
    }


def _vlm_details(events: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = [
        event for event in events
        if event.get("name") == "vlm_decision" and isinstance(event.get("payload"), dict)
    ]
    summary = _event_payload(events, "vlm_query_summary")
    latencies = summary.get("latencies")
    if not isinstance(latencies, list):
        latencies = [event["payload"].get("latency_s") for event in decisions]
    clean_latencies = [value for item in latencies for value in [_finite(item)] if value is not None]
    query_times = [value for event in decisions for value in [_finite(event.get("t"))] if value is not None]
    n_queries = summary.get("n_queries", len(decisions))
    effective_max_queries = summary.get("max_queries", NA)
    if effective_max_queries is None:
        effective_max_queries = "unlimited"
    raw_errors = summary.get("n_errors", sum(event.get("name") == "vlm_error" for event in events))
    return {
        "n_queries": int(n_queries) if _finite(n_queries) is not None else len(decisions),
        "latencies": clean_latencies,
        "mean_latency_s": _round(statistics.fmean(clean_latencies)) if clean_latencies else NA,
        "median_latency_s": _round(statistics.median(clean_latencies)) if clean_latencies else NA,
        "max_latency_s": _round(max(clean_latencies)) if clean_latencies else NA,
        "action_sequence": ",".join(str(event["payload"].get("action", NA)) for event in decisions) or NA,
        "who_sequence": ",".join(str(event["payload"].get("who", NA)) for event in decisions) or NA,
        "first_query_time_s": _round(summary.get("first_query_sim_time", min(query_times) if query_times else None)),
        "last_query_time_s": _round(summary.get("last_query_sim_time", max(query_times) if query_times else None)),
        "effective_max_queries": effective_max_queries,
        "n_errors": int(raw_errors) if _finite(raw_errors) is not None else NA,
        "decisions": decisions,
    }


def _trace_audit(trace: list[dict[str, str]]) -> dict[str, Any]:
    active: list[tuple[float, int]] = []
    different = 0
    for row in trace:
        sim_time = _finite(row.get("sim_time"))
        index = _finite(row.get("active_decision_idx"))
        if sim_time is not None and index is not None and index > 0:
            active.append((sim_time, int(index)))
        pairs = (
            ("applied_throttle", "base_throttle"),
            ("applied_brake", "base_brake"),
            ("applied_steer", "base_steer"),
        )
        comparable = [
            (a, b) for applied, base in pairs
            for a, b in [(_finite(row.get(applied)), _finite(row.get(base)))]
            if a is not None and b is not None
        ]
        if any(not math.isclose(a, b, abs_tol=1e-9) for a, b in comparable):
            different += 1
    before = max((index for time, index in active if time <= 3.1), default=0)
    advanced = any(time > 3.1 and index > before for time, index in active)
    return {
        "distinct_active_decisions_applied": len({index for _, index in active}),
        "active_decision_advanced_after_3_1_s": advanced if trace else NA,
        "ticks_control_differs_from_base": different if trace else NA,
    }


def _episode_times(rows: list[dict[str, Any]]) -> dict[str, Any]:
    margins = [
        (float(row["sim_time"]), evaluator._front_margin(row))
        for row in rows
        if _finite(row.get("sim_time")) is not None
    ]
    margins = [(time, margin) for time, margin in margins if margin is not None]
    crossing = next((time for time, margin in margins if margin < 0.0), None)
    return {
        "front_crossed_ever": any(margin < 0.0 for _, margin in margins),
        "time_to_cross_stop_line_s": _round(crossing),
        "min_front_margin_m": _round(min((margin for _, margin in margins), default=None)),
    }


def _config_fields(
    metadata: dict[str, Any], events: list[dict[str, Any]], *, baseline: bool
) -> dict[str, Any]:
    config = metadata.get("config", {}) if isinstance(metadata.get("config"), dict) else {}
    vlm = config.get("vlm", {}) if isinstance(config.get("vlm"), dict) else {}
    officer = config.get("officer", {}) if isinstance(config.get("officer"), dict) else {}
    spawned = _event_payload(events, "officer_spawned")
    max_queries = 3 if baseline else vlm.get("max_queries", NA)
    if max_queries is None:
        max_queries = "unlimited"
    timeout = 14 if baseline else config.get("timeout_sec", NA)
    config_id = f"mq={max_queries};timeout={timeout}s" if vlm or baseline else f"timeout={timeout}s"
    if baseline:
        config_id += ";early_termination=true;baseline_3query"
    return {
        "config_id": config_id,
        "config_max_queries": max_queries,
        "config_timeout_sec": timeout,
        "config_seed": config.get("seed", NA),
        "officer_authority_type": officer.get("authority_type", spawned.get("authority_type", NA)),
        "officer_gesture": officer.get("gesture", spawned.get("gesture_id", NA)),
        "officer_blueprint": officer.get("blueprint", spawned.get("blueprint_id", NA)),
        "officer_distance_m": _round(officer.get("distance")),
    }


def _missing_record(model: str, condition: str, seed: int, reason: str) -> dict[str, Any]:
    record = {column: NA for column in PER_EPISODE_COLUMNS}
    record.update({
        "model": model,
        "model_display_name": _model_display_name(model),
        "is_reference": _is_reference(model),
        "condition": condition,
        "seed": seed,
        "officer_condition": condition,
        "expected_behavior": EXPECTED_BEHAVIOR[condition],
        "missing_reason": reason,
    })
    return record


def _evaluate(
    model: str,
    condition: str,
    seed: int,
    episode_dir: Path | None,
    *,
    baseline: bool = False,
) -> dict[str, Any]:
    if episode_dir is None:
        return {
            "record": _missing_record(model, condition, seed, "condition/seed episode directory is missing"),
            "metadata": {}, "events": [], "strict_rows": [], "trace": [], "metrics": [], "vlm": {},
        }
    artifacts = _load_episode_artifacts(episode_dir)
    metadata, events, rows = artifacts["metadata"], artifacts["events"], artifacts["strict_rows"]
    config = _config_fields(metadata, events, baseline=baseline)
    vlm = _vlm_details(events)
    metadata_config = metadata.get("config", {})
    if not isinstance(metadata_config, dict):
        metadata_config = {}
    has_vlm = bool(metadata_config.get("vlm")) or bool(vlm.get("decisions"))
    if not has_vlm:
        vlm = {}
    missing_notes = []
    if not baseline and not (episode_dir / "metadata.json").exists():
        missing_notes.append("metadata.json is missing")
    if has_vlm and not (episode_dir / "vlm_debug" / "vlm_trace.csv").exists():
        missing_notes.append("vlm_debug/vlm_trace.csv is missing")
    try:
        result = evaluator.evaluate_episode(episode_dir, condition)
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        record = _missing_record(model, condition, seed, f"{type(exc).__name__}: {exc}")
    else:
        times = _episode_times(rows)
        record = {
            "model": model,
            "model_display_name": _model_display_name(model),
            "is_reference": _is_reference(model),
            "condition": condition,
            "seed": seed,
            "episode_id": _nested(metadata, "config", "episode_id", default=metadata.get("episode_id", episode_dir.name)),
            "episode_dir": str(episode_dir),
            **config,
            "officer_condition": condition,
            "max_speed_kmh": result["max_speed_kmh"],
            "stop_position_m": result["first_sustained_stop_front_margin_m"],
            "front_crossed_during_active": result["front_crossed_during_active"],
            **times,
            "centre_crossed_stopline_ever": result["centre_crossed_stopline_ever"],
            "intersection_traversed": result["intersection_traversed"],
            "expected_behavior": EXPECTED_BEHAVIOR[condition],
            "experimental_verdict": result["experimental_verdict"],
            "experimental_reason": result["experimental_reason"],
            "strict_existing": (
                result["strict_existing"] if condition in evaluator.STOP_CONDITIONS
                else result["strict_existing_proceed"]
            ),
            "initial_speed_kmh": result["initial_speed_kmh"],
            "end_reason": result["end_reason"],
            "collisions": result["collisions"],
            "first_sustained_stop_time_s": result["first_sustained_stop_s"],
            "prelaunch_hold_s": result["prelaunch_hold_s"],
            "stopped_for_non_authority": result["stopped_for_non_authority"],
            "stopped_at_release": result["stopped_at_release"],
            "restart_after_release_s": result["restart_after_release_s"],
            "release_response_status": result["release_response_status"],
            "n_queries": vlm.get("n_queries", NA),
            "mean_latency_s": vlm.get("mean_latency_s", NA),
            "median_latency_s": vlm.get("median_latency_s", NA),
            "max_latency_s": vlm.get("max_latency_s", NA),
            "action_sequence": vlm.get("action_sequence", NA),
            "who_sequence": vlm.get("who_sequence", NA),
            "missing_reason": "; ".join(missing_notes),
        }
    if baseline:
        record.update({"panel": "baseline_3query", "early_termination": True, "not_pooled": True})
    return {"record": record, **artifacts, "vlm": vlm, "has_vlm": has_vlm}


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)


def _mean(values: Iterable[Any]) -> Any:
    clean = [value for item in values for value in [_finite(item)] if value is not None]
    return _round(statistics.fmean(clean)) if clean else NA


def _summary(
    records: list[dict[str, Any]], *, include_reference: bool = False
) -> list[dict[str, Any]]:
    output = []
    models = sorted({str(row["model"]) for row in records}, key=_model_sort_key)
    for model in models:
        if _is_reference(model) and not include_reference:
            continue
        for condition in CONDITIONS:
            rows = [row for row in records if row["model"] == model and row["condition"] == condition]
            if not rows:
                continue
            successes = sum(row["experimental_verdict"] == "PASS" for row in rows)
            valid = sum(row["experimental_verdict"] in {"PASS", "FAIL"} for row in rows)
            speeds = [value for row in rows for value in [_finite(row["max_speed_kmh"])] if value is not None]
            sd = statistics.stdev(speeds) if len(speeds) > 1 else (0.0 if speeds else None)
            mean_speed = statistics.fmean(speeds) if speeds else None
            stop_positions = [
                value for row in rows for value in [_finite(row["stop_position_m"])] if value is not None
            ]
            latencies = [
                value for row in rows for value in [_finite(row["mean_latency_s"])] if value is not None
            ]
            medians = [
                value for row in rows for value in [_finite(row["median_latency_s"])] if value is not None
            ]
            crossing_times = [
                value
                for row in rows
                for value in [_finite(row["time_to_cross_stop_line_s"])]
                if value is not None
            ]
            output.append({
                "model": model,
                "model_display_name": _model_display_name(model),
                "is_reference": _is_reference(model),
                "condition": condition, "successes": successes,
                # slots that were never run are "not run", not failures
                "expected_trials": 3,
                "successes_of_3": "not run" if valid == 0 else f"{successes}/{valid}",
                "rate": _round(successes / valid) if valid else NA,
                "max_speed_mean_kmh": _round(mean_speed), "max_speed_sd_kmh": _round(sd),
                "max_speed_mean_plus_minus_sd": (
                    f"{mean_speed:.4f} +/- {sd:.4f}" if mean_speed is not None and sd is not None else NA
                ),
                "mean_stop_position_m": _round(statistics.fmean(stop_positions)) if stop_positions else NA,
                "stop_line_crossing_count": sum(row["front_crossed_ever"] is True for row in rows),
                "mean_time_to_cross_s": (
                    _round(statistics.fmean(crossing_times)) if crossing_times else NA
                ),
                "vlm_query_count_mean": _mean(row["n_queries"] for row in rows),
                "vlm_latency_mean_s": _round(statistics.fmean(latencies)) if latencies else NA,
                "vlm_latency_median_s": _round(statistics.median(medians)) if medians else NA,
                "missing_episodes": 3 - valid,
            })
    return output


def _directive_sensitivity(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    comparisons = (
        ("STOP-vs-PROCEED", "police_stop", "police_proceed"),
        ("STOP-vs-no-police", "police_stop", "no_police"),
    )
    models = sorted({str(row["model"]) for row in records}, key=_model_sort_key)
    for model in models:
        if _is_reference(model):
            continue
        model_rows = [row for row in records if row["model"] == model]
        for label, left, right in comparisons:
            def difference(key: str) -> Any:
                left_mean = _mean(
                    row[key] for row in model_rows if row["condition"] == left
                )
                right_mean = _mean(
                    row[key] for row in model_rows if row["condition"] == right
                )
                if left_mean == NA or right_mean == NA:
                    return NA
                return _round(float(left_mean) - float(right_mean))

            def success_rate(condition: str) -> float | None:
                condition_rows = [
                    row for row in model_rows if row["condition"] == condition
                ]
                valid = [
                    row for row in condition_rows
                    if row["experimental_verdict"] in {"PASS", "FAIL"}
                ]
                if not valid:
                    return None
                return sum(row["experimental_verdict"] == "PASS" for row in valid) / 3

            left_success, right_success = success_rate(left), success_rate(right)
            output.append({
                "model": model,
                "model_display_name": _model_display_name(model),
                "comparison": label,
                "time_to_cross_difference_s": difference("time_to_cross_stop_line_s"),
                "min_front_margin_difference_m": difference("min_front_margin_m"),
                "max_speed_difference_kmh": difference("max_speed_kmh"),
                "success_difference": (
                    _round(left_success - right_success)
                    if left_success is not None and right_success is not None else NA
                ),
            })
    return output


PAIRWISE_METRICS = [
    ("max_speed_kmh", "max speed (km/h)"),
    ("time_to_cross_stop_line_s", "time to cross stop line (s)"),
    ("min_front_margin_m", "minimum front margin (m)"),
    ("first_sustained_stop_time_s", "first sustained stop time (s)"),
]


def _pairwise_markdown(records: list[dict[str, Any]]) -> str:
    pairs = [
        ("police_stop", "police_proceed"),
        ("police_stop", "no_police"),
        ("police_proceed", "no_police"),
    ]
    lines = [
        "# Pairwise descriptive differences",
        "",
        "Values are condition-A mean minus condition-B mean. They are descriptive only; no causal claim is made.",
        "",
        "| Model | Condition A | Condition B | Metric | A mean | B mean | Difference |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for model in sorted({str(row["model"]) for row in records}, key=_model_sort_key):
        if _is_reference(model):
            continue
        if not any(row["model"] == model for row in records):
            continue
        for left, right in pairs:
            for key, label in PAIRWISE_METRICS:
                left_mean = _mean(row[key] for row in records if row["model"] == model and row["condition"] == left)
                right_mean = _mean(row[key] for row in records if row["model"] == model and row["condition"] == right)
                difference = (
                    _round(float(left_mean) - float(right_mean))
                    if left_mean != NA and right_mean != NA else NA
                )
                lines.append(f"| {model} | {left} | {right} | {label} | {left_mean} | {right_mean} | {difference} |")
    return "\n".join(lines) + "\n"


def _metrics_by_time(metrics: list[dict[str, str]]) -> dict[float, dict[str, str]]:
    output: dict[float, dict[str, str]] = defaultdict(dict)
    for row in metrics:
        time = _finite(row.get("t"))
        if time is not None:
            output[round(time, 6)][str(row.get("key"))] = row.get("value", "")
    return output


def _action_by_time(
    trace: list[dict[str, str]], decisions: list[dict[str, Any]]
) -> list[tuple[float, str]]:
    traced = [
        (time, str(row.get("active_action")))
        for row in trace for time in [_finite(row.get("sim_time"))]
        if time is not None and row.get("active_action")
    ]
    if traced:
        return traced
    return [
        (time, str(event["payload"].get("action", NA)))
        for event in decisions for time in [_finite(event.get("t"))]
        if time is not None
    ]


def _tidy_telemetry(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = _metrics_by_time(artifact["metrics"])
    actions = _action_by_time(artifact["trace"], artifact["vlm"].get("decisions", []))
    output = []
    last_action = NA
    action_idx = 0
    for row in sorted(artifact["strict_rows"], key=lambda item: float(item["sim_time"])):
        time = float(row["sim_time"])
        while action_idx < len(actions) and actions[action_idx][0] <= time + 1e-9:
            last_action = actions[action_idx][1]
            action_idx += 1
        metric = metrics.get(round(time, 6), {})
        margin = evaluator._front_margin(row)
        output.append({
            "sim_time": time,
            "speed_kmh": row.get("ego_speed_kmh", metric.get("speed_kmh", NA)),
            "throttle": row.get("applied_throttle", metric.get("throttle", NA)),
            "brake": row.get("applied_brake", metric.get("brake", NA)),
            "steer": row.get("applied_steer", metric.get("steer", NA)),
            "x": row.get("ego_x", metric.get("ego_x", NA)),
            "y": row.get("ego_y", metric.get("ego_y", NA)),
            "yaw": row.get("ego_yaw_deg", NA),
            "stopline_forward_m": row.get("stopline_forward_m", metric.get("stopline_forward_m", NA)),
            "front_margin_m": _round(margin),
            "officer_active": row.get("officer_active", metric.get("officer_active", NA)),
            "officer_gesture_id": row.get("officer_gesture_id", metric.get("officer_gesture_id", NA)),
            "tl_state": metric.get("tl_state", NA),
            "in_junction": row.get("in_junction", metric.get("in_junction", NA)),
            "collision_count": row.get("collision_count", metric.get("collision_count", NA)),
            "vlm_active_action": last_action,
        })
    return output


def _plot_series(ax: Any, artifacts: list[dict[str, Any]], key: str, ylabel: str, model: str) -> None:
    plotted = False
    onset_end: set[tuple[float, float]] = set()
    for artifact in artifacts:
        record, rows = artifact["record"], artifact["strict_rows"]
        if record["model"] != model or not rows:
            continue
        x = [float(row["sim_time"]) for row in rows]
        if key == "speed":
            y = [_finite(row.get("ego_speed_kmh")) for row in rows]
        else:
            y = [evaluator._front_margin(row) for row in rows]
        ax.plot(
            x, y, color=COLORS[record["condition"]], linestyle=LINESTYLES.get(int(record["seed"]), "-"),
            linewidth=1.2, label=f"{record['condition']} s{record['seed']}",
        )
        onset = next((_finite(row.get("officer_onset_time")) for row in rows if _finite(row.get("officer_onset_time")) is not None), None)
        duration = next((_finite(row.get("officer_duration_sec")) for row in rows if _finite(row.get("officer_duration_sec")) is not None), None)
        if onset is not None:
            onset_end.add((onset, onset + duration if duration is not None else x[-1]))
        plotted = True
    for onset, end in sorted(onset_end):
        ax.axvline(onset, color="black", linewidth=0.7, alpha=0.35)
        ax.axvline(end, color="black", linewidth=0.7, alpha=0.35)
    if key == "margin":
        ax.axhline(0.0, color="black", linewidth=0.9, alpha=0.65)
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.2)
    if plotted:
        ax.legend(fontsize=7, ncol=3)
    else:
        ax.text(0.5, 0.5, "No telemetry", transform=ax.transAxes, ha="center", va="center")


def _config_title(artifacts: list[dict[str, Any]], model: str) -> str:
    configs = sorted({
        str(item["record"].get("config_id")) for item in artifacts
        if item["record"]["model"] == model and item["record"].get("config_id") not in {None, NA}
    })
    return "; ".join(configs) if configs else "config unavailable"


def _write_plots(plot_dir: Path, artifacts: list[dict[str, Any]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_dir.mkdir(parents=True, exist_ok=True)
    present = sorted(
        {str(item["record"]["model"]) for item in artifacts}, key=_model_sort_key
    )
    for model in present:
        for key, stem, ylabel in (
            ("speed", "speed_vs_time", "Speed (km/h)"),
            ("margin", "front_margin_vs_time", "Signed front-bumper distance (m)"),
        ):
            fig, ax = plt.subplots(figsize=(10, 5.5))
            _plot_series(ax, artifacts, key, ylabel, model)
            ax.set_title(
                f"{_model_display_name(model)}: {stem.replace('_', ' ')}\n"
                f"{_config_title(artifacts, model)}"
            )
            fig.tight_layout()
            fig.savefig(plot_dir / f"{model}_{stem}.png", dpi=150)
            plt.close(fig)
    if not present:
        return
    fig, axes = plt.subplots(
        len(present), 2, figsize=(15, max(5, 4.5 * len(present))), squeeze=False
    )
    for row_idx, model in enumerate(present):
        for col_idx, (key, ylabel) in enumerate((("speed", "Speed (km/h)"), ("margin", "Signed front-bumper distance (m)"))):
            ax = axes[row_idx, col_idx]
            _plot_series(ax, artifacts, key, ylabel, model)
            ax.set_title(
                f"{_model_display_name(model)} | {_config_title(artifacts, model)}"
            )
    fig.tight_layout()
    fig.savefig(plot_dir / "overview_grid.png", dpi=150)
    # Preserve the original deliverable name for downstream consumers.
    fig.savefig(plot_dir / "overview_2x2.png", dpi=150)
    plt.close(fig)


def _baseline_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts = []
    for tag, condition in BASELINE_CONDITIONS.items():
        for seed in (1, 2, 3):
            group = root / f"qwen3vl_{tag}_s{seed}"
            episodes = _episode_dirs(group)
            if episodes:
                artifacts.extend(_evaluate("qwen3vl", condition, seed, episode, baseline=True) for episode in episodes)
            else:
                artifacts.append(_evaluate("qwen3vl", condition, seed, None, baseline=True))
    return artifacts


def generate(run_roots: Iterable[Path], out_dir: Path, baseline_3q: Path | None = None) -> dict[str, int]:
    model_roots = _discover_model_roots(run_roots)
    artifacts = [
        _evaluate(model, condition, seed, episode)
        for model, condition, seed, episode in _iter_experiment_slots(model_roots)
    ]
    records = [item["record"] for item in artifacts]
    results_dir, plots_dir, telemetry_dir = out_dir / "results", out_dir / "plots", out_dir / "telemetry"
    _write_csv(results_dir / "per_episode.csv", records, PER_EPISODE_COLUMNS)
    _write_json(results_dir / "per_episode.json", records)
    summaries = _summary(records)
    _write_csv(results_dir / "summary.csv", summaries, SUMMARY_COLUMNS)
    _write_json(results_dir / "summary.json", summaries)
    summaries_by_model = _summary(records, include_reference=True)
    _write_csv(
        results_dir / "summary_by_model.csv", summaries_by_model, SUMMARY_COLUMNS
    )
    _write_csv(
        results_dir / "directive_sensitivity.csv",
        _directive_sensitivity(records),
        SENSITIVITY_COLUMNS,
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "pairwise.md").write_text(_pairwise_markdown(records), encoding="utf-8")

    audits = []
    for item in artifacts:
        record = item["record"]
        if not item.get("has_vlm"):
            continue
        audit = _trace_audit(item["trace"])
        audits.append({
            "model": record["model"], "condition": record["condition"], "seed": record["seed"],
            "episode_id": record["episode_id"], "episode_dir": record["episode_dir"],
            "n_queries": item["vlm"].get("n_queries", NA),
            "first_query_time_s": item["vlm"].get("first_query_time_s", NA),
            "last_query_time_s": item["vlm"].get("last_query_time_s", NA),
            "effective_max_queries": item["vlm"].get("effective_max_queries", record["config_max_queries"]),
            "n_errors": item["vlm"].get("n_errors", NA), **audit,
            "missing_reason": record["missing_reason"],
        })
    _write_csv(results_dir / "vlm_query_audit.csv", audits, AUDIT_COLUMNS)

    telemetry_dir.mkdir(parents=True, exist_ok=True)
    for item in artifacts:
        record = item["record"]
        rows = _tidy_telemetry(item)
        name = f"{record['model']}_{record['condition']}_s{record['seed']}.csv"
        _write_csv(telemetry_dir / name, rows, TELEMETRY_COLUMNS)
    _write_plots(plots_dir, artifacts)

    baseline_count = 0
    if baseline_3q is not None:
        baseline = _baseline_artifacts(baseline_3q)
        baseline_records = [item["record"] for item in baseline]
        columns = PER_EPISODE_COLUMNS + ["panel", "early_termination", "not_pooled"]
        _write_csv(results_dir / "comparison_3query.csv", baseline_records, columns)
        baseline_count = len(baseline_records)
    return {"episodes": len(records), "baseline_episodes": baseline_count, "missing": sum(bool(row["missing_reason"]) for row in records)}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True, help="Experiment root(s)")
    parser.add_argument("--out", type=Path, required=True, help="Output root")
    parser.add_argument("--baseline-3q", type=Path, help="Earlier 3-query Qwen3-VL panel")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    counts = generate(args.runs, args.out, args.baseline_3q)
    print(
        f"wrote {counts['episodes']} experiment rows ({counts['missing']} missing)"
        f" and {counts['baseline_episodes']} baseline rows under {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
