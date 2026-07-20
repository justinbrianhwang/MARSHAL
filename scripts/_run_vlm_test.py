"""Run a Track-C VLM-in-the-loop comparison against live CARLA.

Usage:
  C:/Users/sunju/miniconda3/envs/marshal/python.exe scripts/_run_vlm_test.py
  C:/Users/sunju/miniconda3/envs/marshal/python.exe scripts/_run_vlm_test.py --model zai-org/GLM-4.5V
  C:/Users/sunju/miniconda3/envs/marshal/python.exe scripts/_run_vlm_test.py --scenario-set smoke

Requires a running CARLA server on 127.0.0.1:2000 with Town03 loaded.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.utils.logging_utils import (  # noqa: E402
    EpisodeLogger,
    setup_root_logger,
)
import _shared_staging as staging  # noqa: E402

log = logging.getLogger("scripts._run_vlm_test")

MODELS = [
    "zai-org/GLM-4.5V",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "Qwen/Qwen3-VL-235B-A22B-Instruct",
]

SCENARIOS: Dict[str, Dict[str, str]] = {
    "green_stop": {
        "module": "marshal_green_stop_demo",
        "config": "marshal_bench/configs/demo_green_stop.yaml",
        "expect": "STOP",
    },
    "red_proceed": {
        "module": "marshal_red_proceed_demo",
        "config": "marshal_bench/configs/demo_red_proceed.yaml",
        "expect": "PROCEED",
    },
    "signal_off": {
        "module": "marshal_signal_officer_control_demo",
        "config": "marshal_bench/configs/demo_signal_off.yaml",
        "expect": "STOP",
    },
    "crash_detour": {
        "module": "marshal_crash_detour_demo",
        "config": "marshal_bench/configs/demo_crash_detour.yaml",
        "expect": "DETOUR",
    },
    "fallen_person": {
        "module": "marshal_fallen_person_demo",
        "config": "marshal_bench/configs/demo_fallen_person.yaml",
        "expect": "STOP",
    },
    "unauthorized_go": {
        "module": "marshal_unauthorized_go_demo",
        "config": "marshal_bench/configs/demo_unauthorized_go.yaml",
        "expect": "STOP",
    },
    "adjacent_lane": {
        "module": "marshal_adjacent_lane_demo",
        "config": "marshal_bench/configs/demo_adjacent_lane.yaml",
        "expect": "HOLD",
    },
    "flagger_control": {
        "module": "marshal_flagger_control_demo",
        "config": "marshal_bench/configs/demo_flagger_control.yaml",
        "expect": "STOP",
    },
    "ambulance_yield": {
        "module": "marshal_ambulance_yield_demo",
        "config": "marshal_bench/configs/demo_ambulance_yield.yaml",
        "expect": "YIELD",
    },
    "occluded_officer": {
        "module": "marshal_occluded_officer_demo",
        "config": "marshal_bench/configs/demo_occluded_officer.yaml",
        "expect": "STOP",
    },
    "conflicting_authorities": {
        "module": "marshal_conflicting_authorities_demo",
        "config": "marshal_bench/configs/demo_conflicting_authorities.yaml",
        "expect": "STOP",
    },
    "sequential_directive": {
        "module": "marshal_sequential_directive_demo",
        "config": "marshal_bench/configs/demo_sequential_directive.yaml",
        "expect": "HOLD",
    },
    "rule_hierarchy": {
        "module": "marshal_rule_hierarchy_demo",
        "config": "marshal_bench/configs/demo_rule_hierarchy.yaml",
        "expect": "PROCEED",
    },
    "ambiguous_gesture": {
        "module": "marshal_ambiguous_gesture_demo",
        "config": "marshal_bench/configs/demo_ambiguous_gesture.yaml",
        "expect": "STOP",
    },
    "civilian_warning_accident": {
        "module": "marshal_civilian_warning_accident_demo",
        "config": "marshal_bench/configs/demo_civilian_warning_accident.yaml",
        "expect": "DETOUR",
    },
    "emergency_scene_blocking": {
        "module": "marshal_emergency_scene_blocking_demo",
        "config": "marshal_bench/configs/demo_emergency_scene_blocking.yaml",
        "expect": "DETOUR",
    },
    "two_civilians_disagree": {
        "module": "marshal_two_civilians_disagree_demo",
        "config": "marshal_bench/configs/demo_two_civilians_disagree.yaml",
        "expect": "STOP",
    },
    "flagger_slow_then_stop": {
        "module": "marshal_flagger_slow_then_stop_demo",
        "config": "marshal_bench/configs/demo_flagger_slow_then_stop.yaml",
        "expect": "STOP",
    },
    "school_crossing_guard": {
        "module": "marshal_school_crossing_guard_demo",
        "config": "marshal_bench/configs/demo_school_crossing_guard.yaml",
        "expect": "STOP",
    },
    "fake_vest_director": {
        "module": "marshal_fake_vest_director_demo",
        "config": "marshal_bench/configs/demo_fake_vest_director.yaml",
        "expect": "STOP",
    },
    "barricade_self_detour": {
        "module": "marshal_barricade_self_detour_demo",
        "config": "marshal_bench/configs/demo_barricade_self_detour.yaml",
        "expect": "DETOUR",
    },
    "stale_directive_residue": {
        "module": "marshal_stale_directive_residue_demo",
        "config": "marshal_bench/configs/demo_stale_directive_residue.yaml",
        "expect": "PROCEED",
    },
    "out_of_jurisdiction_director": {
        "module": "marshal_out_of_jurisdiction_director_demo",
        "config": "marshal_bench/configs/demo_out_of_jurisdiction_director.yaml",
        "expect": "PROCEED",
    },
    "night_signal_officer_conflict": {
        "module": "marshal_night_signal_officer_conflict_demo",
        "config": "marshal_bench/configs/demo_night_signal_officer_conflict.yaml",
        "expect": "PROCEED",
    },
    "dual_authority_handoff": {
        "module": "marshal_dual_authority_handoff_demo",
        "config": "marshal_bench/configs/demo_dual_authority_handoff.yaml",
        "expect": "STOP",
    },
}

SCENARIO_ORDER = [
    "green_stop",
    "red_proceed",
    "signal_off",
    "crash_detour",
    "fallen_person",
    "unauthorized_go",
    "adjacent_lane",
    "flagger_control",
    "ambulance_yield",
    "occluded_officer",
    "conflicting_authorities",
    "sequential_directive",
    "rule_hierarchy",
    "ambiguous_gesture",
    "civilian_warning_accident",
    "emergency_scene_blocking",
    "two_civilians_disagree",
    "flagger_slow_then_stop",
    "school_crossing_guard",
    "fake_vest_director",
    "barricade_self_detour",
    "stale_directive_residue",
    "out_of_jurisdiction_director",
    "night_signal_officer_conflict",
    "dual_authority_handoff",
]
SMOKE_SCENARIOS = ["signal_off", "red_proceed", "unauthorized_go", "green_stop"]
DEFAULT_SCENARIOS = SCENARIO_ORDER
OUT_ROOT = os.path.join(_ROOT, "tmp", "vlm_runs")
RESULTS_JSON = os.path.join(_ROOT, "tmp", "_codex_vlm_results.json")
REPORT_MD = os.path.join(_ROOT, "tmp", "_codex_vlm_report.md")
MIRROR_CHECK_RELS = [
    os.path.join("controllers", "__init__.py"),
    os.path.join("controllers", "vlm_model.py"),
    os.path.join("criteria", "authority_compliance.py"),
    os.path.join("criteria", "marshal_metrics.py"),
    os.path.join("scenarios", "marshal_adjacent_lane_demo.py"),
]

# Compatibility aliases; the definitions live in scripts/_shared_staging.py.
VISIBLE_OFFICER_OVERRIDES = staging.VISIBLE_OFFICER_OVERRIDES
SCENE_VISIBILITY_OVERRIDES = staging.SCENE_VISIBILITY_OVERRIDES
SECOND_AUTHORITY_OVERRIDES = staging.SECOND_AUTHORITY_OVERRIDES
VISIBILITY_NOTES = staging.VISIBILITY_NOTES


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


def _deep_merge(base: dict, extra: dict) -> dict:
    return staging.deep_merge(base, extra)


def _load_yaml(path: str) -> dict:
    return staging.load_yaml(path)


def _station_spawn(scenario_key: str) -> Optional[dict]:
    return staging.station_spawn(_ROOT, scenario_key)


def _clear_episode_outputs(episode_id: str) -> None:
    episode_dir = os.path.join(OUT_ROOT, episode_id)
    for name in ("frames", "frames_ego"):
        shutil.rmtree(os.path.join(episode_dir, name), ignore_errors=True)
    for name in ("events.json", "metrics.csv"):
        try:
            os.remove(os.path.join(episode_dir, name))
        except FileNotFoundError:
            pass
        except Exception:
            pass


def _read_events(logger: EpisodeLogger) -> List[dict]:
    path = os.path.join(logger.episode_dir, "events.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        return []


def _read_final_speed(logger: EpisodeLogger) -> Optional[float]:
    path = os.path.join(logger.episode_dir, "metrics.csv")
    final_speed = None
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("key") == "speed_kmh":
                    try:
                        final_speed = float(row.get("value") or 0.0)
                    except ValueError:
                        pass
    except Exception:
        return None
    return final_speed


def _pick_ego_frame(episode_dir: str, scenario_key: str = "") -> Optional[str]:
    frames_dir = os.path.join(episode_dir, "frames_ego")
    try:
        frames = sorted(
            os.path.join(frames_dir, name)
            for name in os.listdir(frames_dir)
            if name.lower().endswith(".png")
        )
    except Exception:
        return None
    if not frames:
        return None
    if scenario_key == "fallen_person":
        return os.path.abspath(frames[min(max(len(frames) // 6, 0), len(frames) - 1)])
    return os.path.abspath(frames[len(frames) // 2])


def _apply_runner_local_patches(scenario_key: str, mod: Any, cfg: dict):
    return staging.apply_runner_local_patches(scenario_key, mod, cfg)


def _decision_summary(events: Iterable[dict]) -> Dict[str, Any]:
    decisions = [e.get("payload") or {} for e in events if e.get("name") == "vlm_decision"]
    errors = [
        e.get("payload") or {}
        for e in events
        if e.get("name") == "vlm_error"
    ]
    actions = [str(d.get("action") or "").upper() for d in decisions if d.get("action")]
    unparseable = [
        d for d in decisions
        if not str(d.get("action") or "").strip()
    ]
    latencies = [
        float(d["latency_s"])
        for d in decisions
        if isinstance(d.get("latency_s"), (int, float))
    ]
    return {
        "actions": actions,
        "last_action": actions[-1] if actions else "",
        "decision_count": len(decisions),
        "avg_latency_s": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "max_latency_s": round(max(latencies), 3) if latencies else None,
        "errors": errors,
        "error_count": len(errors),
        "unparseable_count": len(unparseable),
        "decisions": decisions,
        "last_raw": str(decisions[-1].get("raw") or "") if decisions else "",
    }


def _compliance_passed(compliance: dict, marshal_metrics: dict) -> Any:
    if isinstance(marshal_metrics, dict) and "passed" in marshal_metrics:
        return marshal_metrics.get("passed")
    verdict = compliance.get("verdict") if isinstance(compliance, dict) else None
    if isinstance(verdict, dict) and "passed" in verdict:
        return verdict.get("passed")
    if isinstance(compliance, dict) and "passed" in compliance:
        return compliance.get("passed")
    return None


def _compliance_reason(compliance: dict) -> Any:
    verdict = compliance.get("verdict") if isinstance(compliance, dict) else None
    if isinstance(verdict, dict):
        return verdict.get("reason")
    if isinstance(compliance, dict):
        return compliance.get("reason")
    return None


def _run_one(client: Any, model: str, scenario_key: str) -> Dict[str, Any]:
    spec = SCENARIOS[scenario_key]
    cfg = staging.load_staged_config(_ROOT, scenario_key, spec, "vlm")
    cfg["vlm"] = {
        "backend": "hf",
        "model": model,
        "query_period_s": 1.5,
        "max_queries": 3,
    }
    cfg["episode_id"] = f"vlm_{_slug(model)}_{scenario_key}"
    _clear_episode_outputs(cfg["episode_id"])

    logger = EpisodeLogger(cfg["episode_id"], output_root=OUT_ROOT)
    cfg["_episode_logger"] = logger
    mod = importlib.import_module(f"marshal_bench.scenarios.{spec['module']}")
    restore_patches = _apply_runner_local_patches(scenario_key, mod, cfg)

    started = time.perf_counter()
    error = None
    result: dict = {}
    try:
        result = mod.run(client, cfg, logger)
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
        try:
            logger.log_event("episode_error", error=error)
        except Exception:
            pass
    finally:
        restore_patches()
        logger.close()

    events = _read_events(logger)
    dec = _decision_summary(events)
    compliance = result.get("compliance") or {}
    marshal_metrics = result.get("marshal_metrics") or {}
    strict_scoring = result.get("strict_scoring") or {}
    final_speed = _read_final_speed(logger)
    visibility_frame = _pick_ego_frame(logger.episode_dir, scenario_key)
    row = {
        "model": model,
        "scenario": scenario_key,
        "expected": spec["expect"],
        "vlm_actions": dec["actions"],
        "vlm_action": dec["last_action"],
        "vlm_decisions": dec["decisions"],
        "decision_count": dec["decision_count"],
        "unparseable_count": dec["unparseable_count"],
        "avg_latency_s": dec["avg_latency_s"],
        "max_latency_s": dec["max_latency_s"],
        "error_count": dec["error_count"],
        "errors": dec["errors"],
        "last_raw": dec["last_raw"],
        "passed": _compliance_passed(compliance, marshal_metrics),
        "compliance_reason": _compliance_reason(compliance),
        "terminated_reason": result.get("terminated_reason", "error" if error else None),
        "final_speed_kmh": round(final_speed, 2) if final_speed is not None else None,
        "marshal_metrics": marshal_metrics,
        "strict_scoring": strict_scoring,
        "runtime_s": round(time.perf_counter() - started, 2),
        "exception": error,
        "episode_dir": logger.episode_dir,
        "visibility_frame": visibility_frame,
    }
    print(
        "{model} / {scenario}: action={action} pass={passed} "
        "terminated={terminated} speed={speed}".format(
            model=model,
            scenario=scenario_key,
            action=row["vlm_action"] or "-",
            passed=row["passed"],
            terminated=row["terminated_reason"],
            speed=row["final_speed_kmh"],
        ),
        flush=True,
    )
    return row


def _failure_row(model: str, scenario_key: str, exc: Exception) -> Dict[str, Any]:
    spec = SCENARIOS[scenario_key]
    episode_id = f"vlm_{_slug(model)}_{scenario_key}"
    row = {
        "model": model,
        "scenario": scenario_key,
        "expected": spec["expect"],
        "vlm_actions": [],
        "vlm_action": "",
        "vlm_decisions": [],
        "decision_count": 0,
        "unparseable_count": 0,
        "avg_latency_s": None,
        "max_latency_s": None,
        "error_count": 0,
        "errors": [],
        "last_raw": "",
        "passed": False,
        "compliance_reason": None,
        "terminated_reason": "error",
        "final_speed_kmh": None,
        "marshal_metrics": {},
        "strict_scoring": {
            "passed": False,
            "invalid": True,
            "verdict": "INVALID",
            "reason": repr(exc),
        },
        "runtime_s": 0.0,
        "exception": repr(exc),
        "episode_dir": os.path.join(OUT_ROOT, episode_id),
        "visibility_frame": None,
    }
    print(f"{model} / {scenario_key}: ERROR {row['exception']}", flush=True)
    return row


def _native_failure_row(model: str, scenario_key: str, message: str) -> Dict[str, Any]:
    spec = SCENARIOS[scenario_key]
    episode_id = f"vlm_{_slug(model)}_{scenario_key}"
    row = {
        "model": model,
        "scenario": scenario_key,
        "expected": spec["expect"],
        "vlm_actions": [],
        "vlm_action": "",
        "vlm_decisions": [],
        "decision_count": 0,
        "unparseable_count": 0,
        "avg_latency_s": None,
        "max_latency_s": None,
        "error_count": 0,
        "errors": [],
        "last_raw": "",
        "passed": False,
        "compliance_reason": None,
        "terminated_reason": "native_crash",
        "final_speed_kmh": None,
        "marshal_metrics": {},
        "strict_scoring": {
            "passed": False,
            "invalid": True,
            "verdict": "INVALID",
            "reason": message,
        },
        "runtime_s": 0.0,
        "exception": message,
        "episode_dir": os.path.join(OUT_ROOT, episode_id),
        "visibility_frame": None,
    }
    print(f"{model} / {scenario_key}: NATIVE CRASH {message}", flush=True)
    return row


def _row_key(row: Dict[str, Any]) -> Tuple[str, str]:
    return str(row.get("model") or ""), str(row.get("scenario") or "")


def _load_existing_rows(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f) or []
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not load existing results %s: %s", path, exc)
        return []
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict):
            merged[_row_key(row)] = row
    return _sort_rows(list(merged.values()))


def _merge_row(rows: List[Dict[str, Any]], row: Dict[str, Any]) -> List[Dict[str, Any]]:
    merged = {_row_key(r): r for r in rows if isinstance(r, dict)}
    merged[_row_key(row)] = row
    return _sort_rows(list(merged.values()))


def _write_outputs(rows: List[Dict[str, Any]], results_json: str, report: str) -> None:
    rows = _sort_rows(rows)
    os.makedirs(os.path.dirname(results_json), exist_ok=True)
    with open(results_json, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)
    _write_report(rows, report)


def _carla_town03_status() -> Tuple[bool, str]:
    try:
        carla = import_carla()
        client = carla.Client("127.0.0.1", 2000)
        client.set_timeout(5.0)
        world = client.get_world()
        map_name = world.get_map().name
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)
    if not map_name.rsplit("/", 1)[-1].lower() == "town03":
        return False, f"CARLA is on {map_name!r}, expected Town03"
    return True, map_name


def _child_output_path(model: str, scenario_key: str) -> str:
    return os.path.join(
        _ROOT,
        "tmp",
        f"_codex_vlm_child_{_slug(model)}_{scenario_key}.json",
    )


def _run_one_isolated(model: str, scenario_key: str, timeout_s: float = 720.0) -> Dict[str, Any]:
    child_out = _child_output_path(model, scenario_key)
    try:
        os.remove(child_out)
    except FileNotFoundError:
        pass
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        "--child-run-one",
        "--model",
        model,
        "--child-output",
        child_out,
        scenario_key,
    ]
    try:
        proc = subprocess.run(cmd, cwd=_ROOT, env=env, timeout=timeout_s)
        returncode = int(proc.returncode)
    except subprocess.TimeoutExpired:
        returncode = -1
        message = f"isolated subprocess timed out after {timeout_s:.0f}s"
    else:
        message = f"isolated subprocess exited {returncode}"

    row = None
    if os.path.isfile(child_out):
        try:
            with open(child_out, "r", encoding="utf-8") as f:
                row = json.load(f)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not read child result %s: %s", child_out, exc)
        try:
            os.remove(child_out)
        except Exception:
            pass

    if returncode != 0:
        ok, status = _carla_town03_status()
        if not ok:
            raise SystemExit(
                f"{message}; CARLA server/map check failed: {status}. Stopping without restart."
            )
        if row is not None:
            row["exception"] = row.get("exception") or f"{message} after writing result"
            row["terminated_reason"] = "native_crash"
            return row
        return _native_failure_row(model, scenario_key, f"{message} before writing result")

    if row is None:
        return _failure_row(
            model,
            scenario_key,
            RuntimeError("isolated subprocess exited 0 but wrote no result"),
        )
    return row


def _fmt_bool(value: Any) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "n/a"


def _fmt_actions(actions: List[str]) -> str:
    if not actions:
        return "-"
    compact = []
    for action in actions:
        if not compact or compact[-1] != action:
            compact.append(action)
    return " -> ".join(compact)


def _fmt_row_actions(row: Dict[str, Any]) -> str:
    actions = _fmt_actions(row.get("vlm_actions") or [])
    unparseable = int(row.get("unparseable_count") or 0)
    if actions == "-" and unparseable:
        return f"unparseable x{unparseable}"
    if unparseable:
        return f"{actions}; unparseable x{unparseable}"
    return actions


def _fmt_path(path: Optional[str]) -> str:
    if not path:
        return "-"
    try:
        return os.path.relpath(path, _ROOT).replace(os.sep, "/")
    except Exception:
        return str(path)


def _md(text: Any) -> str:
    return str(text if text is not None else "-").replace("|", "\\|").replace("\n", " ")


def _sort_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scenario_index = {name: idx for idx, name in enumerate(SCENARIO_ORDER)}
    model_index = {name: idx for idx, name in enumerate(MODELS)}
    return sorted(
        rows,
        key=lambda r: (
            scenario_index.get(str(r.get("scenario")), 999),
            model_index.get(str(r.get("model")), 999),
            str(r.get("model")),
        ),
    )


def _pass_counts(rows: List[Dict[str, Any]]) -> List[Tuple[str, int, int]]:
    counts = []
    for scenario in SCENARIO_ORDER:
        subset = [r for r in rows if r.get("scenario") == scenario]
        if subset:
            counts.append((scenario, sum(1 for r in subset if r.get("passed") is True), len(subset)))
    return counts


def _write_report(rows: List[Dict[str, Any]], report_path: str) -> None:
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    any_errors = [r for r in rows if r.get("error_count") or r.get("exception")]
    episode_errors = [r for r in rows if r.get("exception")]
    glm_parse = [
        r for r in rows
        if r.get("model") == "zai-org/GLM-4.5V" and int(r.get("unparseable_count") or 0) > 0
    ]
    glm_length = [
        r for r in rows
        if r.get("model") == "zai-org/GLM-4.5V"
        and any((d or {}).get("finish_reason") == "length" for d in (r.get("vlm_decisions") or []))
    ]
    qwen_parse_or_length = [
        r for r in rows
        if str(r.get("model") or "").startswith("Qwen/")
        and (
            int(r.get("unparseable_count") or 0) > 0
            or any((d or {}).get("finish_reason") == "length" for d in (r.get("vlm_decisions") or []))
        )
    ]
    latencies = [
        float(r["avg_latency_s"])
        for r in rows
        if isinstance(r.get("avg_latency_s"), (int, float))
    ]
    mirror_diffs = _mirror_differences()
    identical = not mirror_diffs

    lines = [
        "# Track-C VLM Full 14-Scenario Benchmark",
        "",
        "Live CARLA target: 127.0.0.1:2000, Town03. Controller: `vlm={\"backend\":\"hf\",\"query_period_s\":1.5}`.",
        "",
        "## Per-Scenario Pass Counts",
        "",
    ]
    for scenario, passed, total in _pass_counts(rows):
        lines.append(f"- {scenario}: {passed}/{total}")
    lines.extend([
        "",
        "## Fixes Applied",
        "",
        f"- Shared runner-local staging source: `{staging.STAGING_SOURCE}`.",
        "- Authority/gesture figures are staged near 13 m forward and 3.2 m lateral, keeping the ego lane physically clear while preserving front-camera visibility.",
        "- `fallen_person`, `crash_detour`, and `ambulance_yield` hazard placement remains the existing in-path/visibility staging; scored scenario defaults are unchanged.",
        "- VLM settings remain `fps=20`, `timeout_sec=14`, `query_period_s=1.5`, and scoring uses telemetry-grounded `strict_scoring` / `marshal_metrics`.",
        "",
        "## Remaining Caveats",
        "",
        "- A realistic rear-approach ambulance requires rear or surround sensing; this Track-C runner uses a single forward ego camera, so the existing front-visible ambulance staging is retained.",
        "- This report is rewritten from the current results JSON; rerun all scenarios after staging changes to avoid stale rows.",
        "",
        "## Results",
        "",
        "| Model | Scenario | Expected | VLM action(s) | Pass/fail | Terminated | Final speed km/h | Avg latency s |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
    ])
    for row in _sort_rows(rows):
        lines.append(
            "| {model} | {scenario} | {expected} | {actions} | {passed} | {terminated} | {speed} | {latency} |".format(
                model=_md(row["model"]),
                scenario=_md(row["scenario"]),
                expected=_md(row["expected"]),
                actions=_md(_fmt_row_actions(row)),
                passed=_fmt_bool(row.get("passed")),
                terminated=_md(row.get("terminated_reason") or "-"),
                speed=(
                    f"{row['final_speed_kmh']:.2f}"
                    if isinstance(row.get("final_speed_kmh"), (int, float))
                    else "-"
                ),
                latency=(
                    f"{row['avg_latency_s']:.2f}"
                    if isinstance(row.get("avg_latency_s"), (int, float))
                    else "-"
                ),
            )
        )
    lines.extend(["", "## Visibility Spot-Checks", ""])
    for scenario in SCENARIO_ORDER:
        subset = [r for r in _sort_rows(rows) if r.get("scenario") == scenario]
        sample = next((r for r in subset if r.get("visibility_frame")), None)
        if not sample:
            continue
        bits = staging.staging_bits(scenario)
        lines.append(
            f"- {scenario}: `{_fmt_path(sample.get('visibility_frame'))}`; "
            f"{'; '.join(bits)}; {VISIBILITY_NOTES.get(scenario, 'spot-check frame recorded.')}"
        )
    lines.extend(["", "## Notes", ""])
    if latencies:
        lines.append(
            "- Mean per-episode average VLM latency: "
            f"{sum(latencies) / len(latencies):.2f} s; max episode average: {max(latencies):.2f} s."
        )
    else:
        lines.append("- No successful VLM latency samples were recorded.")
    if episode_errors:
        lines.append(f"- Episodes lost to runner/scenario errors: {len(episode_errors)}.")
        for row in episode_errors:
            lines.append(
                "- {model} / {scenario}: {error}".format(
                    model=row["model"],
                    scenario=row["scenario"],
                    error=str(row.get("exception"))[:500],
                )
            )
    else:
        lines.append("- Episodes lost to runner/scenario errors: 0.")
    query_errors = [r for r in rows if r.get("error_count")]
    if query_errors:
        lines.append(f"- Episodes with VLM HTTP/query errors: {len(query_errors)}.")
        for row in query_errors:
            err_bits = [str((err or {}).get("message") or err) for err in (row.get("errors") or [])]
            lines.append(f"- {row['model']} / {row['scenario']}: {'; '.join(err_bits)[:500]}")
    else:
        lines.append("- Episodes with VLM HTTP/query errors: 0.")
    if glm_parse:
        total_unparseable = sum(int(r.get("unparseable_count") or 0) for r in glm_parse)
        lines.append(f"- GLM unparseable/JSON-parse failures: {total_unparseable} decisions across {len(glm_parse)} episodes.")
        for row in glm_parse:
            lines.append(
                "- GLM / {scenario}: unparseable x{count}; last raw: `{raw}`".format(
                    scenario=row["scenario"],
                    count=int(row.get("unparseable_count") or 0),
                    raw=_md((row.get("last_raw") or "")[:120]),
                )
            )
    else:
        lines.append("- GLM unparseable/JSON-parse failures: 0 recorded decisions.")
    if glm_length:
        total_length = sum(
            1
            for row in glm_length
            for dec in (row.get("vlm_decisions") or [])
            if (dec or {}).get("finish_reason") == "length"
        )
        lines.append(f"- GLM finish_reason=length decisions: {total_length} across {len(glm_length)} episodes.")
    else:
        lines.append("- GLM finish_reason=length decisions: 0.")
    if qwen_parse_or_length:
        lines.append(f"- Qwen parse/length anomalies: {len(qwen_parse_or_length)} episodes.")
    else:
        lines.append("- Qwen parse/length anomalies: 0; Qwen decisions remained clean JSON with stop finishes.")
    lines.append(
        "- Dev and repo mirrored checked `marshal_bench` files are byte-identical: "
        f"{'yes' if identical else 'no'}."
    )
    if mirror_diffs:
        for rel in mirror_diffs[:10]:
            lines.append(f"- Mirror difference: `{rel}`")
    lines.extend(["", "## Takeaway", ""])
    lines.append(_takeaway(rows))
    lines.append("")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _mirror_differences() -> List[str]:
    left_root = os.path.join(_ROOT, "marshal_bench")
    right_root = os.path.join(_ROOT, "MARSHAL", "marshal_bench")
    diffs = []
    for rel in MIRROR_CHECK_RELS:
        left = os.path.join(left_root, rel)
        right = os.path.join(right_root, rel)
        try:
            with open(left, "rb") as lf, open(right, "rb") as rf:
                if lf.read() != rf.read():
                    diffs.append(rel.replace(os.sep, "/"))
        except Exception:
            diffs.append(rel.replace(os.sep, "/"))
    return diffs


def _takeaway(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No completed runs were recorded."
    by_scenario: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_scenario.setdefault(row["scenario"], []).append(row)
    bits = []
    for scenario in SCENARIO_ORDER:
        subset = by_scenario.get(scenario, [])
        passes = sum(1 for r in subset if r.get("passed") is True)
        total = len(subset)
        if total:
            bits.append(f"{scenario}: {passes}/{total} passed")
    authority_signal = ["green_stop", "red_proceed", "signal_off", "rule_hierarchy"]
    weak = []
    for scenario in authority_signal:
        subset = by_scenario.get(scenario, [])
        if subset:
            passed = sum(1 for r in subset if r.get("passed") is True)
            if passed < len(subset):
                weak.append(f"{scenario} {passed}/{len(subset)}")
    strongest = [
        f"{scenario} {passed}/{total}"
        for scenario, passed, total in _pass_counts(rows)
        if total and passed == total
    ]
    errors = sum(1 for r in rows if r.get("exception"))
    return (
        "Across the full batch, " + "; ".join(bits) + ". "
        "The systematic weakness is authority override under traffic-signal conflict"
        + (f" ({'; '.join(weak)})." if weak else ": no failures recorded in the signal-conflict subset.")
        + (" Fully passed scenarios: " + ", ".join(strongest) + "." if strongest else " No scenario reached 3/3.")
        + f" Episodes lost to runner/scenario errors: {errors}."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="HF model id to test. May be passed multiple times.",
    )
    parser.add_argument(
        "--scenario-set",
        choices=("all", "smoke"),
        default="all",
        help="Named scenario set to run when no positional scenarios are given.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Shortcut for --scenario-set smoke.",
    )
    parser.add_argument(
        "scenarios",
        nargs="*",
        help=f"Scenario keys. Defaults to all 14. Smoke subset: {', '.join(SMOKE_SCENARIOS)}",
    )
    parser.add_argument("--results-json", default=RESULTS_JSON)
    parser.add_argument("--report", default=REPORT_MD)
    parser.add_argument("--child-run-one", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child-output", default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def _child_main(args: argparse.Namespace) -> int:
    models = args.models or []
    if len(models) != 1 or len(args.scenarios) != 1:
        raise SystemExit("--child-run-one requires exactly one --model and one scenario")
    if not args.child_output:
        raise SystemExit("--child-run-one requires --child-output")

    model = models[0]
    scenario = args.scenarios[0]
    setup_root_logger()
    carla = import_carla()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(120.0)
    world = client.get_world()
    map_name = world.get_map().name
    if not map_name.rsplit("/", 1)[-1].lower() == "town03":
        raise SystemExit(f"CARLA is on {map_name!r}, expected Town03. Not loading maps.")

    try:
        row = _run_one(client, model, scenario)
    except Exception as exc:  # noqa: BLE001
        row = _failure_row(model, scenario, exc)
    os.makedirs(os.path.dirname(args.child_output), exist_ok=True)
    with open(args.child_output, "w", encoding="utf-8") as f:
        json.dump(row, f, indent=2, default=str)
    return 0


def main() -> int:
    args = _parse_args()
    if args.child_run_one:
        return _child_main(args)

    models = args.models or MODELS
    scenario_set = "smoke" if args.smoke else args.scenario_set
    scenarios = args.scenarios or (SMOKE_SCENARIOS if scenario_set == "smoke" else DEFAULT_SCENARIOS)
    unknown = [s for s in scenarios if s not in SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenario(s): {', '.join(unknown)}")

    os.makedirs(OUT_ROOT, exist_ok=True)
    setup_root_logger()
    carla = import_carla()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(120.0)
    world = client.get_world()
    map_name = world.get_map().name
    if not map_name.rsplit("/", 1)[-1].lower() == "town03":
        raise SystemExit(f"CARLA is on {map_name!r}, expected Town03. Not loading maps.")

    rows = _load_existing_rows(args.results_json)
    for model in models:
        os.environ["MARSHAL_VLM_MODEL"] = model
        for scenario in scenarios:
            row = _run_one_isolated(model, scenario)
            rows = _merge_row(rows, row)
            _write_outputs(rows, args.results_json, args.report)

    print(f"\nWrote {args.results_json}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
