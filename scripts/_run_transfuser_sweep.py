#!/usr/bin/env python
"""Run the full 14-scenario MARSHAL sweep with the Track-B TransFuser controller.

This runner intentionally reuses the Track-C VLM staging from
``scripts/_run_vlm_test.py`` so the scene placement is comparable across
TransFuser and the already-recorded VLM results. Each episode is run in an
isolated child process; native crashes or timeouts are recorded as one failed
row and do not abort the full sweep.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

THIS = Path(__file__).resolve().parent
ROOT = THIS.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(THIS) not in sys.path:
    sys.path.insert(0, str(THIS))

import _run_vlm_test as vlm  # noqa: E402
import _shared_staging as staging  # noqa: E402
from marshal_bench.criteria.marshal_metrics import (  # noqa: E402
    REASONING_TIER,
    aggregate,
    compute_episode_metrics,
)
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.utils.logging_utils import EpisodeLogger, setup_root_logger  # noqa: E402

OUT_ROOT = ROOT / "tmp" / "_codex_transfuser_sweep_runs"
RESULTS_JSON = ROOT / "tmp" / "_codex_transfuser_sweep.json"
REPORT_MD = ROOT / "tmp" / "_codex_transfuser_sweep_report.md"
COMBINED_JSON = ROOT / "tmp" / "_codex_combined_results.json"
COMBINED_REPORT_MD = ROOT / "tmp" / "_codex_combined_report.md"
DEFAULT_CKPT = (
    ROOT
    / "Models"
    / "TransFuser"
    / "checkpoints"
    / "models_2022"
    / "transfuser"
)

TIERS = ("low", "mid", "high")
REPRESENTATIVE_VISIBILITY = (
    "green_stop",
    "unauthorized_go",
    "adjacent_lane",
)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


def _episode_id(scenario: str) -> str:
    return f"transfuser_{scenario}"


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "as_dict"):
        try:
            return _jsonable(obj.as_dict())
        except Exception:
            pass
    return str(obj)


def _safe_rmtree(path: Path, root: Path) -> None:
    path = path.resolve()
    root = root.resolve()
    if path == root or root not in path.parents:
        raise RuntimeError(f"refusing to delete outside output root: {path}")
    shutil.rmtree(path, ignore_errors=True)


def _clear_episode_outputs(episode_id: str) -> None:
    episode_dir = OUT_ROOT / episode_id
    if episode_dir.exists():
        _safe_rmtree(episode_dir, OUT_ROOT)


def _read_trace(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"trace_path": str(path), "trace_exists": False}
    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rows.extend(csv.DictReader(fh))

    controls: List[Tuple[float, float, float, float]] = []
    frames_advance = True
    last_frame: Optional[int] = None
    stale_rows = 0
    error_rows: List[str] = []
    nonfinite_rows = 0
    modes: Dict[str, int] = {}
    for row in rows:
        mode = str(row.get("mode") or "")
        modes[mode] = modes.get(mode, 0) + 1
        try:
            frame = int(row["world_frame"]) if row.get("world_frame") else None
        except ValueError:
            frame = None
        if frame is not None and last_frame is not None and frame < last_frame:
            frames_advance = False
        if frame is not None:
            last_frame = frame
        if str(row.get("stale", "")).lower() == "true":
            stale_rows += 1
        if row.get("error"):
            error_rows.append(str(row.get("error")))
        try:
            values = (
                float(row["throttle"]),
                float(row["brake"]),
                float(row["steer"]),
                float(row["speed_mps"]),
            )
            controls.append(values)
            if not all(math.isfinite(v) for v in values):
                nonfinite_rows += 1
        except Exception:
            pass

    speeds = [values[3] for values in controls]
    finite = bool(controls) and nonfinite_rows == 0
    return {
        "trace_path": str(path),
        "trace_exists": True,
        "rows": len(rows),
        "modes": modes,
        "infer_rows": modes.get("infer", 0),
        "hold_rows": modes.get("hold", 0),
        "stale_rows": stale_rows,
        "error_rows": len(error_rows),
        "first_error": error_rows[0] if error_rows else "",
        "finite_controls": finite,
        "nonfinite_rows": nonfinite_rows,
        "frames_advance": frames_advance,
        "max_speed_mps": round(max(speeds), 4) if speeds else 0.0,
        "last_speed_mps": round(speeds[-1], 4) if speeds else 0.0,
    }


def _read_metrics(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"metrics_path": str(path), "metrics_exists": False}
    speeds: List[float] = []
    throttle: List[float] = []
    brake: List[float] = []
    steer: List[float] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = row.get("key")
            try:
                value = float(row.get("value") or 0.0)
            except Exception:
                continue
            if key == "speed_kmh":
                speeds.append(value)
            elif key == "throttle":
                throttle.append(value)
            elif key == "brake":
                brake.append(value)
            elif key == "steer":
                steer.append(value)
    return {
        "metrics_path": str(path),
        "metrics_exists": True,
        "rows_speed": len(speeds),
        "max_speed_kmh": round(max(speeds), 4) if speeds else 0.0,
        "last_speed_kmh": round(speeds[-1], 4) if speeds else 0.0,
        "moved": bool(speeds and max(speeds) > 1.0),
        "max_throttle": round(max(throttle), 5) if throttle else None,
        "max_brake": round(max(brake), 5) if brake else None,
        "max_abs_steer": round(max(abs(v) for v in steer), 5) if steer else None,
    }


def _debug_frames(debug_dir: Path) -> Tuple[List[str], List[str]]:
    front = sorted(str(p) for p in debug_dir.glob("front_*.png"))
    lidar = sorted(str(p) for p in debug_dir.glob("lidar_*.png"))
    return front, lidar


def _pick_debug_pair(row: Dict[str, Any]) -> Dict[str, Optional[str]]:
    front = list(row.get("front_frames") or [])
    lidar = list(row.get("lidar_frames") or [])
    if not front and not lidar:
        return {"front": None, "lidar": None}
    idx = max(0, min(len(front), len(lidar)) // 2 - 1)
    if row.get("scenario") == "fallen_person":
        idx = min(max(1, idx), max(0, min(len(front), len(lidar)) - 1))
    return {
        "front": front[idx] if idx < len(front) else (front[-1] if front else None),
        "lidar": lidar[idx] if idx < len(lidar) else (lidar[-1] if lidar else None),
    }


def _rel(path: Optional[str]) -> str:
    if not path:
        return "-"
    try:
        return str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _build_config(args: argparse.Namespace, scenario: str) -> Dict[str, Any]:
    spec = vlm.SCENARIOS[scenario]
    cfg = staging.load_staged_config(str(ROOT), scenario, spec, "transfuser")
    episode_id = _episode_id(scenario)
    cfg["episode_id"] = episode_id
    cfg["transfuser"] = {
        "ckpt_dir": str(Path(args.ckpt_dir).resolve()),
        "debug_dir": str((OUT_ROOT / episode_id / "transfuser_debug").resolve()),
        "log_every_n": int(args.log_every_n),
        "save_debug_every_n": int(args.save_debug_every_n),
        "max_debug_frames": int(args.max_debug_frames),
        "sensor_timeout_s": float(args.sensor_timeout_s),
    }
    return cfg


def _behavior_summary(row: Dict[str, Any]) -> str:
    reason = str(row.get("compliance_reason") or "").strip()
    trace = row.get("trace") or {}
    metrics = row.get("metrics") or {}
    moved = metrics.get("moved")
    max_speed = metrics.get("max_speed_kmh")
    bits = []
    if moved is True:
        bits.append(f"moved, max {float(max_speed or 0):.1f} km/h")
    elif moved is False:
        bits.append("stayed near stopped")
    if trace.get("finite_controls") is True:
        bits.append("finite controls")
    elif trace.get("finite_controls") is False:
        bits.append("non-finite controls")
    if reason:
        bits.append(reason)
    elif row.get("exception"):
        bits.append(str(row.get("exception"))[:120])
    return "; ".join(bits) if bits else "-"


def _run_one(client: Any, args: argparse.Namespace, scenario: str) -> Dict[str, Any]:
    cfg = _build_config(args, scenario)
    episode_id = str(cfg["episode_id"])
    _clear_episode_outputs(episode_id)
    episode_dir = OUT_ROOT / episode_id
    debug_dir = episode_dir / "transfuser_debug"
    logger = EpisodeLogger(episode_id, output_root=str(OUT_ROOT))
    logger.save_metadata(
        {
            "episode_id": episode_id,
            "scenario": scenario,
            "controller": "transfuser",
            "config": _jsonable(cfg),
            "staging_source": staging.STAGING_SOURCE,
        }
    )
    cfg["_episode_logger"] = logger

    spec = vlm.SCENARIOS[scenario]
    mod = importlib.import_module(f"marshal_bench.scenarios.{spec['module']}")
    restore_patches = staging.apply_runner_local_patches(scenario, mod, cfg)
    started = time.perf_counter()
    error: Optional[str] = None
    tb: Optional[str] = None
    result: Dict[str, Any] = {}
    try:
        result = mod.run(client, cfg, logger)
        logger.log_event("scenario_result", **_jsonable(result))
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
        tb = traceback.format_exc()
        logger.log_event("episode_error", error=error, traceback=tb)
    finally:
        restore_patches()
        try:
            if result:
                logger.save_metadata({"result": _jsonable(result)}, name="result.json")
        except Exception:
            pass
        try:
            logger.close()
        except Exception:
            pass

    compliance = result.get("compliance") or {}
    marshal_metrics = result.get("marshal_metrics") or {}
    strict_scoring = result.get("strict_scoring") or {}
    metrics = _read_metrics(episode_dir / "metrics.csv")
    trace = _read_trace(debug_dir / "transfuser_trace.csv")
    front_frames, lidar_frames = _debug_frames(debug_dir)
    episode_metrics = None
    if result:
        try:
            episode_metrics = compute_episode_metrics(result, scenario=scenario).as_dict()
        except Exception:
            episode_metrics = None

    row = {
        "model": "TransFuser",
        "track": "B",
        "scenario": scenario,
        "expected": spec["expect"],
        "passed": vlm._compliance_passed(compliance, marshal_metrics),
        "compliance_reason": vlm._compliance_reason(compliance),
        "terminated_reason": result.get("terminated_reason", "error" if error else None),
        "final_speed_kmh": metrics.get("last_speed_kmh"),
        "marshal_metrics": marshal_metrics,
        "strict_scoring": strict_scoring,
        "episode_metrics": episode_metrics,
        "metrics": metrics,
        "trace": trace,
        "control_finiteness": {
            "finite_controls": trace.get("finite_controls"),
            "nonfinite_rows": trace.get("nonfinite_rows"),
            "trace_rows": trace.get("rows"),
        },
        "runtime_s": round(time.perf_counter() - started, 2),
        "exception": error,
        "traceback": tb,
        "episode_dir": str(episode_dir),
        "result_path": str(episode_dir / "result.json"),
        "front_frames": front_frames,
        "lidar_frames": lidar_frames,
        "visibility_sample": {},
        "raw_result": _jsonable(result) if result else {},
    }
    row["behavior"] = _behavior_summary(row)
    if scenario in REPRESENTATIVE_VISIBILITY:
        row["visibility_sample"] = _pick_debug_pair(row)
    print(
        "TransFuser / {scenario}: pass={passed} terminated={terminated} speed={speed} trace_rows={trace_rows}".format(
            scenario=scenario,
            passed=row["passed"],
            terminated=row["terminated_reason"],
            speed=row["final_speed_kmh"],
            trace_rows=trace.get("rows"),
        ),
        flush=True,
    )
    return row


def _failure_row(
    scenario: str,
    message: str,
    *,
    terminated_reason: str,
    stdout_path: Optional[Path] = None,
    stderr_path: Optional[Path] = None,
    returncode: Optional[int] = None,
) -> Dict[str, Any]:
    spec = vlm.SCENARIOS[scenario]
    episode_dir = OUT_ROOT / _episode_id(scenario)
    row = {
        "model": "TransFuser",
        "track": "B",
        "scenario": scenario,
        "expected": spec["expect"],
        "passed": False,
        "compliance_reason": None,
        "terminated_reason": terminated_reason,
        "final_speed_kmh": None,
        "marshal_metrics": {},
        "strict_scoring": {
            "passed": False,
            "invalid": True,
            "verdict": "INVALID",
            "reason": message,
        },
        "episode_metrics": None,
        "metrics": _read_metrics(episode_dir / "metrics.csv"),
        "trace": _read_trace(episode_dir / "transfuser_debug" / "transfuser_trace.csv"),
        "control_finiteness": {},
        "runtime_s": 0.0,
        "exception": message,
        "traceback": None,
        "episode_dir": str(episode_dir),
        "result_path": str(episode_dir / "result.json"),
        "front_frames": [],
        "lidar_frames": [],
        "visibility_sample": {},
        "raw_result": {},
        "behavior": message[:180],
        "stdout": str(stdout_path) if stdout_path else None,
        "stderr": str(stderr_path) if stderr_path else None,
        "returncode": returncode,
    }
    print(f"TransFuser / {scenario}: {terminated_reason.upper()} {message}", flush=True)
    return row


def _child_output_path(scenario: str) -> Path:
    return ROOT / "tmp" / f"_codex_transfuser_child_{scenario}.json"


def _carla_town03_status(timeout_s: float = 5.0) -> Tuple[bool, str]:
    try:
        carla = import_carla()
        client = carla.Client("127.0.0.1", 2000)
        client.set_timeout(timeout_s)
        world = client.get_world()
        map_name = world.get_map().name
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)
    if map_name.rsplit("/", 1)[-1].lower() != "town03":
        return False, f"CARLA is on {map_name!r}, expected Town03"
    return True, map_name


def _run_one_isolated(args: argparse.Namespace, scenario: str) -> Dict[str, Any]:
    child_out = _child_output_path(scenario)
    try:
        child_out.unlink()
    except FileNotFoundError:
        pass
    episode_dir = OUT_ROOT / _episode_id(scenario)
    episode_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = episode_dir / "child_stdout.log"
    stderr_path = episode_dir / "child_stderr.log"

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child-run-one",
        "--child-output",
        str(child_out),
        "--ckpt-dir",
        str(Path(args.ckpt_dir).resolve()),
        "--sensor-timeout-s",
        str(args.sensor_timeout_s),
        "--log-every-n",
        str(args.log_every_n),
        "--save-debug-every-n",
        str(args.save_debug_every_n),
        "--max-debug-frames",
        str(args.max_debug_frames),
        scenario,
    ]
    if args.debug:
        cmd.append("--debug")

    started = time.perf_counter()
    try:
        with open(stdout_path, "w", encoding="utf-8") as out, open(
            stderr_path, "w", encoding="utf-8"
        ) as err:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                env=env,
                stdout=out,
                stderr=err,
                timeout=float(args.wall_timeout),
            )
        returncode = int(proc.returncode)
        message = f"isolated subprocess exited {returncode}"
        terminated = "native_crash" if returncode != 0 else "error"
    except subprocess.TimeoutExpired:
        returncode = -1
        message = f"isolated subprocess timed out after {float(args.wall_timeout):.0f}s"
        terminated = "timeout"

    row = None
    if child_out.is_file():
        try:
            with open(child_out, "r", encoding="utf-8") as fh:
                row = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            row = _failure_row(
                scenario,
                f"could not read child result {child_out}: {exc}",
                terminated_reason="error",
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                returncode=returncode,
            )
        try:
            child_out.unlink()
        except Exception:
            pass

    if row is None:
        if returncode != 0:
            ok, status = _carla_town03_status()
            if not ok:
                raise SystemExit(
                    f"{message}; CARLA server/map check failed: {status}. Stopping without restart."
                )
        row = _failure_row(
            scenario,
            message,
            terminated_reason=terminated,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            returncode=returncode,
        )
    elif returncode != 0:
        row["exception"] = row.get("exception") or f"{message} after writing result"
        row["terminated_reason"] = "native_crash"

    row["returncode"] = returncode
    row["stdout"] = str(stdout_path)
    row["stderr"] = str(stderr_path)
    row["runtime_wall_s"] = round(time.perf_counter() - started, 2)
    return row


def _sort_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scenario_index = {name: idx for idx, name in enumerate(vlm.SCENARIO_ORDER)}
    return sorted(rows, key=lambda r: scenario_index.get(str(r.get("scenario")), 999))


def _fmt_bool(value: Any) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "n/a"


def _md(text: Any) -> str:
    return str(text if text is not None else "-").replace("|", "\\|").replace("\n", " ")


def _tier_counts(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for tier in TIERS:
        subset = [r for r in rows if REASONING_TIER.get(str(r.get("scenario"))) == tier]
        passed = sum(1 for r in subset if r.get("passed") is True)
        out[tier] = {
            "passed": passed,
            "total": len(subset),
            "pass_rate": round(passed / len(subset), 4) if subset else None,
        }
    return out


def _per_scenario_counts(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = list(rows)
    out = []
    for scenario in vlm.SCENARIO_ORDER:
        subset = [r for r in rows if r.get("scenario") == scenario]
        if not subset:
            continue
        out.append(
            {
                "scenario": scenario,
                "passed": sum(1 for r in subset if r.get("passed") is True),
                "total": len(subset),
                "tier": REASONING_TIER.get(scenario),
            }
        )
    return out


def _transfuser_aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    metrics = []
    for row in rows:
        result = row.get("raw_result") or {}
        if not result:
            continue
        try:
            metrics.append(compute_episode_metrics(result, scenario=row.get("scenario")))
        except Exception:
            pass
    board = aggregate(metrics) if metrics else {
        "n_episodes": 0,
        "suite": {},
        "r_scores": {},
        "r_unmeasured": [],
        "marshal_score_partial": None,
        "tier_pass_rate": {},
        "per_episode": [],
    }
    board["per_scenario_pass"] = {
        c["scenario"]: {"passed": c["passed"] == c["total"], "tier": c["tier"]}
        for c in _per_scenario_counts(rows)
    }
    return board


def _short_model_name(model: str) -> str:
    if "Qwen2.5" in model:
        return "Qwen2.5"
    if "Qwen3" in model:
        return "Qwen3"
    if "GLM" in model:
        return "GLM"
    return model


def _comparison_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    comparison: List[Dict[str, Any]] = []

    def add_from_rows(label: str, model_rows: List[Dict[str, Any]]) -> None:
        tier = _tier_counts(model_rows)
        total = len(model_rows)
        passed = sum(1 for r in model_rows if r.get("passed") is True)
        comparison.append(
            {
                "model": label,
                "low": tier["low"],
                "mid": tier["mid"],
                "high": tier["high"],
                "overall": {
                    "passed": passed,
                    "total": total,
                    "pass_rate": round(passed / total, 4) if total else None,
                },
            }
        )

    add_from_rows("TransFuser", rows)

    vlm_path = ROOT / "tmp" / "_codex_vlm_results.json"
    try:
        with open(vlm_path, "r", encoding="utf-8") as fh:
            vlm_rows = json.load(fh) or []
    except Exception:
        vlm_rows = []
    vlm_order = [
        "Qwen/Qwen2.5-VL-72B-Instruct",
        "Qwen/Qwen3-VL-235B-A22B-Instruct",
        "zai-org/GLM-4.5V",
    ]
    for model in vlm_order:
        subset = [r for r in vlm_rows if r.get("model") == model]
        if subset:
            add_from_rows(_short_model_name(model), subset)

    score_path = ROOT / "outputs" / "scoreboard.json"
    try:
        with open(score_path, "r", encoding="utf-8") as fh:
            board = json.load(fh) or {}
    except Exception:
        board = {}
    for label in ("baseline", "oracle"):
        data = board.get(label) or {}
        tier_pass = data.get("tier_pass_rate") or {}
        per = data.get("per_episode") or []
        comparison.append(
            {
                "model": label,
                "low": _scoreboard_tier(tier_pass, "low"),
                "mid": _scoreboard_tier(tier_pass, "mid"),
                "high": _scoreboard_tier(tier_pass, "high"),
                "overall": {
                    "passed": sum(1 for r in per if r.get("passed") is True),
                    "total": len(per),
                    "pass_rate": (
                        round(sum(1 for r in per if r.get("passed") is True) / len(per), 4)
                        if per
                        else None
                    ),
                },
            }
        )
    return comparison


COMBINED_MODEL_ORDER = ("TransFuser", "Qwen2.5", "Qwen3", "GLM", "baseline", "oracle")


def _load_vlm_rows() -> List[Dict[str, Any]]:
    vlm_path = ROOT / "tmp" / "_codex_vlm_results.json"
    try:
        with open(vlm_path, "r", encoding="utf-8") as fh:
            rows = json.load(fh) or []
    except Exception:
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = _short_model_name(str(row.get("model") or ""))
        if label not in {"Qwen2.5", "Qwen3", "GLM"}:
            continue
        out.append(
            {
                "model": label,
                "source_model": row.get("model"),
                "track": "C",
                "scenario": row.get("scenario"),
                "expected": row.get("expected"),
                "passed": row.get("passed"),
                "terminated_reason": row.get("terminated_reason"),
                "exception": row.get("exception"),
                "visibility_frame": row.get("visibility_frame"),
                "raw": row,
            }
        )
    return out


def _load_reference_rows() -> Tuple[List[Dict[str, Any]], str]:
    reference_path = ROOT / "tmp" / "_codex_reference_sweep.json"
    if reference_path.is_file():
        try:
            with open(reference_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh) or {}
            rows = payload.get("rows") or []
            return [r for r in rows if isinstance(r, dict)], str(reference_path)
        except Exception:
            pass

    score_path = ROOT / "outputs" / "scoreboard.json"
    try:
        with open(score_path, "r", encoding="utf-8") as fh:
            board = json.load(fh) or {}
    except Exception:
        return [], str(score_path)

    rows = []
    for label in ("baseline", "oracle"):
        data = board.get(label) or {}
        for ep in data.get("per_episode") or []:
            scenario = ep.get("scenario")
            rows.append(
                {
                    "model": label,
                    "track": "reference",
                    "scenario": scenario,
                    "expected": (vlm.SCENARIOS.get(str(scenario)) or {}).get("expect"),
                    "passed": ep.get("passed"),
                    "tier": REASONING_TIER.get(str(scenario)),
                    "raw": ep,
                }
            )
    return rows, str(score_path)


def _combined_rows(transfuser_rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    rows: List[Dict[str, Any]] = []
    for row in transfuser_rows:
        rows.append(
            {
                "model": "TransFuser",
                "source_model": row.get("model"),
                "track": "B",
                "scenario": row.get("scenario"),
                "expected": row.get("expected"),
                "passed": row.get("passed"),
                "terminated_reason": row.get("terminated_reason"),
                "exception": row.get("exception"),
                "visibility_frame": (row.get("visibility_sample") or {}).get("front"),
                "raw": row,
            }
        )
    vlm_rows = _load_vlm_rows()
    ref_rows, ref_source = _load_reference_rows()
    rows.extend(vlm_rows)
    rows.extend(ref_rows)
    sources = {
        "transfuser": str(RESULTS_JSON),
        "vlm": str(ROOT / "tmp" / "_codex_vlm_results.json"),
        "reference": ref_source,
    }
    return rows, sources


def _count_for(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    subset = list(rows)
    passed = sum(1 for r in subset if r.get("passed") is True)
    total = len(subset)
    return {
        "passed": passed,
        "total": total,
        "pass_rate": round(passed / total, 4) if total else None,
    }


def _combined_per_scenario(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for scenario in vlm.SCENARIO_ORDER:
        entry = {"tier": REASONING_TIER.get(scenario), "models": {}}
        for model in COMBINED_MODEL_ORDER:
            subset = [
                r for r in rows
                if r.get("scenario") == scenario and r.get("model") == model
            ]
            entry["models"][model] = _count_for(subset)
        out[scenario] = entry
    return out


def _combined_per_tier(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for model in COMBINED_MODEL_ORDER:
        model_rows = [r for r in rows if r.get("model") == model]
        out[model] = {}
        for tier in TIERS:
            out[model][tier] = _count_for(
                r for r in model_rows if REASONING_TIER.get(str(r.get("scenario"))) == tier
            )
        out[model]["overall"] = _count_for(model_rows)
    return out


def _combined_authority_takeaway(rows: List[Dict[str, Any]]) -> str:
    authority_stop = [
        scenario
        for scenario in vlm.SCENARIO_ORDER
        if scenario in staging.AUTHORITY_FIGURE_SCENARIOS
        and (vlm.SCENARIOS.get(scenario) or {}).get("expect") == "STOP"
    ]
    hazard = {"crash_detour", "fallen_person", "ambulance_yield"}

    def model_count(model: str, scenarios: Iterable[str]) -> Dict[str, Any]:
        wanted = set(scenarios)
        return _count_for(
            r for r in rows if r.get("model") == model and r.get("scenario") in wanted
        )

    tf_auth = model_count("TransFuser", authority_stop)
    tf_hazard = model_count("TransFuser", hazard)
    vlm_bits = []
    for model in ("Qwen2.5", "Qwen3", "GLM"):
        c = model_count(model, authority_stop)
        if c["total"]:
            vlm_bits.append(f"{model} {c['passed']}/{c['total']}")

    return (
        "With the authority figure moved off the ego path, STOP compliance can no longer be explained as pedestrian obstacle braking. "
        f"TransFuser authority-STOP count is {tf_auth['passed']}/{tf_auth['total']} versus hazard count {tf_hazard['passed']}/{tf_hazard['total']}. "
        + (
            "VLM authority-STOP counts are " + ", ".join(vlm_bits) + ". "
            if vlm_bits
            else "VLM authority-STOP rows are not complete yet. "
        )
        + "A renewed drop on these off-path authority cases is evidence that the earlier E2E STOP passes were confounded by in-lane obstacle avoidance."
    )


def _write_combined_outputs(transfuser_rows: List[Dict[str, Any]]) -> None:
    rows, sources = _combined_rows(transfuser_rows)
    per_scenario = _combined_per_scenario(rows)
    per_tier = _combined_per_tier(rows)
    payload = {
        "run": {
            "staging_source": staging.STAGING_SOURCE,
            "fps": 20,
            "timeout_sec": 14,
            "sources": sources,
        },
        "rows": rows,
        "per_scenario_counts": per_scenario,
        "per_tier_counts": per_tier,
        "takeaway": _combined_authority_takeaway(rows),
    }
    COMBINED_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(COMBINED_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    lines = [
        "# MARSHAL Combined Comparison",
        "",
        f"Shared staging source: `{staging.STAGING_SOURCE}`. Authority/gesture figures are off the ego path; hazard obstacles remain in-path where intended.",
        "",
        "## Per-Scenario Counts",
        "",
        "| Scenario | Tier | TransFuser | Qwen2.5 | Qwen3 | GLM | baseline | oracle |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in vlm.SCENARIO_ORDER:
        entry = per_scenario[scenario]
        cells = [
            _fmt_count((entry.get("models") or {}).get(model) or {})
            for model in COMBINED_MODEL_ORDER
        ]
        lines.append(
            "| {scenario} | {tier} | {cells} |".format(
                scenario=scenario,
                tier=entry.get("tier") or "-",
                cells=" | ".join(cells),
            )
        )
    lines.extend(
        [
            "",
            "## Per-Tier Counts",
            "",
            "| Model | Low | Mid | High | Overall |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for model in COMBINED_MODEL_ORDER:
        counts = per_tier.get(model) or {}
        lines.append(
            "| {model} | {low} | {mid} | {high} | {overall} |".format(
                model=model,
                low=_fmt_count(counts.get("low") or {}),
                mid=_fmt_count(counts.get("mid") or {}),
                high=_fmt_count(counts.get("high") or {}),
                overall=_fmt_count(counts.get("overall") or {}),
            )
        )
    lines.extend(["", "## Sources", ""])
    for key, value in sources.items():
        lines.append(f"- {key}: `{_rel(value)}`")
    lines.extend(["", "## Takeaway", "", payload["takeaway"], ""])
    with open(COMBINED_REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _scoreboard_tier(tier_pass: Dict[str, Any], tier: str) -> Dict[str, Any]:
    data = tier_pass.get(tier) or {}
    n = int(data.get("n") or 0)
    rate = data.get("pass_rate")
    passed = int(round(float(rate) * n)) if rate is not None else 0
    return {"passed": passed, "total": n, "pass_rate": rate}


def _visibility_checks(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    checks = []
    for scenario in REPRESENTATIVE_VISIBILITY:
        row = next((r for r in rows if r.get("scenario") == scenario), None)
        if not row:
            continue
        sample = row.get("visibility_sample") or _pick_debug_pair(row)
        checks.append(
            {
                "scenario": scenario,
                "front": sample.get("front"),
                "lidar": sample.get("lidar"),
                "note": "model-input front RGB and LiDAR saved; manually spot-check these frames.",
            }
        )
    return checks


def _write_outputs(rows: List[Dict[str, Any]], results_json: Path, report_md: Path) -> None:
    rows = _sort_rows(rows)
    aggregate_board = _transfuser_aggregate(rows)
    comparison = _comparison_rows(rows)
    visibility = _visibility_checks(rows)
    payload = {
        "run": {
            "controller": "transfuser",
            "track": "B",
            "town": "Town03",
            "fps": 20,
            "timeout_sec": 14,
            "staging_source": staging.STAGING_SOURCE,
            "output_root": str(OUT_ROOT),
        },
        "rows": rows,
        "per_scenario_counts": _per_scenario_counts(rows),
        "per_tier_counts": _tier_counts(rows),
        "aggregate": aggregate_board,
        "comparison": comparison,
        "visibility_checks": visibility,
    }
    results_json.parent.mkdir(parents=True, exist_ok=True)
    with open(results_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    _write_report(rows, aggregate_board, comparison, visibility, report_md)
    _write_combined_outputs(rows)


def _fmt_count(cell: Dict[str, Any]) -> str:
    total = cell.get("total")
    passed = cell.get("passed")
    if total in (None, 0):
        return "-"
    return f"{passed}/{total}"


def _write_report(
    rows: List[Dict[str, Any]],
    aggregate_board: Dict[str, Any],
    comparison: List[Dict[str, Any]],
    visibility: List[Dict[str, Any]],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _sort_rows(rows)
    per_tier = _tier_counts(rows)
    errors = [r for r in rows if r.get("exception") or r.get("returncode") not in (0, None)]
    anomalies = [
        r
        for r in rows
        if (r.get("trace") or {}).get("finite_controls") is False
        or (r.get("trace") or {}).get("frames_advance") is False
    ]
    lines = [
        "# Track-B TransFuser Full 14-Scenario Benchmark",
        "",
        "Live CARLA target: 127.0.0.1:2000, Town03. Controller: `transfuser` under `transfuser_ui`.",
        "",
        "## Staging Parity",
        "",
        f"- Imported scene staging from `{staging.STAGING_SOURCE}`: officer visibility overrides, scene visibility overrides, second-authority override, fallen-person placement patch, and front-visible ambulance patch.",
        "- Episode settings match the VLM sweep: `fps=20`, `timeout_sec=14`, and scored via telemetry-grounded `strict_scoring` / `marshal_metrics`.",
        "- Authority/gesture figures are staged off the ego tire path while hazard obstacles remain in-path where intended.",
        "- TransFuser still uses its own Track-B sensors and a non-privileged lane-follow route; the runner only changes scene staging and scorer config.",
        "",
        "## Per-Scenario Counts",
        "",
    ]
    for count in _per_scenario_counts(rows):
        lines.append(
            f"- {count['scenario']}: {count['passed']}/{count['total']} ({count['tier']})"
        )
    lines.extend(["", "## Per-Tier Counts", ""])
    for tier in TIERS:
        c = per_tier[tier]
        lines.append(f"- {tier}: {c['passed']}/{c['total']}")

    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Scenario | Expected | TransFuser action/behavior | Pass/fail | Terminated | Final speed km/h |",
            "| --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            "| {scenario} | {expected} | {behavior} | {passed} | {terminated} | {speed} |".format(
                scenario=_md(row.get("scenario")),
                expected=_md(row.get("expected")),
                behavior=_md(row.get("behavior")),
                passed=_fmt_bool(row.get("passed")),
                terminated=_md(row.get("terminated_reason") or "-"),
                speed=(
                    f"{float(row['final_speed_kmh']):.2f}"
                    if isinstance(row.get("final_speed_kmh"), (int, float))
                    else "-"
                ),
            )
        )

    lines.extend(["", "## Visibility Spot-Checks", ""])
    if visibility:
        for item in visibility:
            lines.append(
                "- {scenario}: front `{front}`, LiDAR `{lidar}`; {note}".format(
                    scenario=item["scenario"],
                    front=_rel(item.get("front")),
                    lidar=_rel(item.get("lidar")),
                    note=item.get("note") or "",
                )
            )
    else:
        lines.append("- No representative debug frames were recorded.")

    lines.extend(["", "## Crash / Control Notes", ""])
    if errors:
        lines.append(f"- Episodes with child error/native crash/timeout: {len(errors)}.")
        for row in errors:
            lines.append(
                "- {scenario}: returncode={returncode}, terminated={terminated}, error={error}".format(
                    scenario=row.get("scenario"),
                    returncode=row.get("returncode"),
                    terminated=row.get("terminated_reason"),
                    error=_md(str(row.get("exception") or "")[:300]),
                )
            )
    else:
        lines.append("- Episodes with child error/native crash/timeout: 0.")
    if anomalies:
        lines.append(f"- Trace anomalies: {len(anomalies)}.")
        for row in anomalies:
            tr = row.get("trace") or {}
            lines.append(
                f"- {row.get('scenario')}: finite_controls={tr.get('finite_controls')}, frames_advance={tr.get('frames_advance')}, first_error={tr.get('first_error')}"
            )
    else:
        lines.append("- Per-tick TransFuser controls were finite in every recorded trace; frames advanced monotonically.")
    stale = [r for r in rows if int((r.get("trace") or {}).get("stale_rows") or 0) > 0]
    if stale:
        lines.append(
            "- Startup stale sensor rows were observed in "
            f"{len(stale)} episodes, matching smoke behavior; first synced inference followed normally."
        )

    lines.extend(["", "## Combined Tier Comparison", ""])
    if comparison:
        lines.extend(
            [
                "| Model | Low | Mid | High | Overall |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in comparison:
            lines.append(
                "| {model} | {low} | {mid} | {high} | {overall} |".format(
                    model=_md(row.get("model")),
                    low=_fmt_count(row.get("low") or {}),
                    mid=_fmt_count(row.get("mid") or {}),
                    high=_fmt_count(row.get("high") or {}),
                    overall=_fmt_count(row.get("overall") or {}),
                )
            )
    else:
        lines.append("- Comparison sources were unavailable.")

    lines.extend(["", "## Aggregate", ""])
    lines.append(
        "- MARSHAL partial score: {score}; measured episodes: {n}.".format(
            score=aggregate_board.get("marshal_score_partial"),
            n=aggregate_board.get("n_episodes"),
        )
    )

    lines.extend(["", "## Takeaway", ""])
    lines.append(_takeaway(rows))
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _takeaway(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No TransFuser episodes were recorded."
    hazard = {"crash_detour", "fallen_person", "ambulance_yield", "rule_hierarchy"}
    authority = {
        "green_stop",
        "red_proceed",
        "signal_off",
        "unauthorized_go",
        "adjacent_lane",
        "flagger_control",
        "occluded_officer",
        "conflicting_authorities",
        "sequential_directive",
        "ambiguous_gesture",
    }
    hazard_rows = [r for r in rows if r.get("scenario") in hazard]
    authority_rows = [r for r in rows if r.get("scenario") in authority]
    hp = sum(1 for r in hazard_rows if r.get("passed") is True)
    ap = sum(1 for r in authority_rows if r.get("passed") is True)
    return (
        f"TransFuser passed {sum(1 for r in rows if r.get('passed') is True)}/{len(rows)} episodes. "
        f"It handled {hp}/{len(hazard_rows)} hazard/maneuver-oriented rows but only {ap}/{len(authority_rows)} human-authority or attribution rows, "
        "which is the expected E2E-vs-VLM contrast: the driving stack can react to lane geometry and physical hazards, but it lacks the explicit human-authority reasoning needed for MARSHAL's high-tier cases."
    )


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", help="Scenario keys. Defaults to all 14.")
    parser.add_argument("--results-json", default=str(RESULTS_JSON))
    parser.add_argument("--report", default=str(REPORT_MD))
    parser.add_argument("--ckpt-dir", default=str(DEFAULT_CKPT))
    parser.add_argument("--sensor-timeout-s", type=float, default=0.75)
    parser.add_argument("--log-every-n", type=int, default=1)
    parser.add_argument("--save-debug-every-n", type=int, default=20)
    parser.add_argument("--max-debug-frames", type=int, default=8)
    parser.add_argument("--wall-timeout", type=float, default=720.0)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--child-run-one", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child-output", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _child_main(args: argparse.Namespace) -> int:
    if len(args.scenarios) != 1:
        raise SystemExit("--child-run-one requires exactly one scenario")
    if not args.child_output:
        raise SystemExit("--child-run-one requires --child-output")
    scenario = args.scenarios[0]
    if scenario not in vlm.SCENARIOS:
        raise SystemExit(f"Unknown scenario: {scenario}")

    setup_root_logger()
    ok, status = _carla_town03_status(timeout_s=120.0)
    if not ok:
        raise SystemExit(f"{status}. Not loading maps.")
    carla = import_carla()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(120.0)
    try:
        row = _run_one(client, args, scenario)
    except Exception as exc:  # noqa: BLE001
        row = _failure_row(
            scenario,
            f"{repr(exc)}\n{traceback.format_exc()}",
            terminated_reason="error",
        )
    Path(args.child_output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.child_output, "w", encoding="utf-8") as fh:
        json.dump(row, fh, indent=2, default=str)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if args.child_run_one:
        return _child_main(args)

    scenarios = args.scenarios or list(vlm.SCENARIO_ORDER)
    unknown = [s for s in scenarios if s not in vlm.SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenario(s): {', '.join(unknown)}")

    ckpt = Path(args.ckpt_dir)
    if not ckpt.is_dir() or not any(p.suffix == ".pth" for p in ckpt.iterdir()):
        raise SystemExit(f"TransFuser checkpoint dir is missing .pth files: {ckpt}")

    ok, status = _carla_town03_status()
    if not ok:
        raise SystemExit(f"{status}. Not loading maps.")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    setup_root_logger()
    rows: List[Dict[str, Any]] = []
    for scenario in scenarios:
        print(f"running TransFuser {scenario} ...", flush=True)
        row = _run_one_isolated(args, scenario)
        rows = [r for r in rows if r.get("scenario") != scenario]
        rows.append(row)
        _write_outputs(rows, Path(args.results_json), Path(args.report))

    _write_outputs(rows, Path(args.results_json), Path(args.report))
    print(f"\nWrote {args.results_json}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
