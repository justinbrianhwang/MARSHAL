#!/usr/bin/env python
"""Run additional Track-B controllers on the shared MARSHAL 14-scenario staging."""
from __future__ import annotations

import argparse
import csv
import hashlib
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

import _run_transfuser_sweep as transfuser_sweep  # noqa: E402
import _run_vlm_test as vlm  # noqa: E402
import _shared_staging as staging  # noqa: E402
from marshal_bench.criteria.marshal_metrics import (  # noqa: E402
    REASONING_TIER,
    compute_episode_metrics,
)
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.utils.logging_utils import EpisodeLogger, setup_root_logger  # noqa: E402

OUT_ROOT = ROOT / "tmp" / "_codex_phase3_new_runs"
SMOKE_OUT_ROOT = ROOT / "tmp" / "_codex_phase3_smoke_runs"
RESULTS_JSON = ROOT / "tmp" / "_codex_phase3_new_results.json"
REPORT_MD = ROOT / "tmp" / "_codex_phase3_new_report.md"
SMOKE_RESULTS_JSON = ROOT / "tmp" / "_codex_phase3_smoke_results.json"
SMOKE_REPORT_MD = ROOT / "tmp" / "_codex_phase3_smoke_report.md"
COMBINED_JSON = ROOT / "tmp" / "_codex_combined_results.json"
COMBINED_REPORT_MD = ROOT / "tmp" / "_codex_combined_report.md"
FRAME_CHECKS_JSON = ROOT / "tmp" / "_codex_more_e2e_frame_checks.json"
PHASE2_TRANSFUSER_JSON = ROOT / "tmp" / "_codex_phase2_transfuser_results.json"
PHASE2_EXISTING_JSON = ROOT / "tmp" / "_codex_phase2_existing_results.json"
PHASE2_VLM_JSON = ROOT / "tmp" / "_codex_phase2_vlm_results.json"
PHASE2_TRANSFUSER_REPLACEMENTS = (
    ROOT / "tmp" / "_codex_phase2_occluded_transfuser_results.json",
)
PHASE2_EXISTING_REPLACEMENTS = (
    ROOT / "tmp" / "_codex_phase2_occluded_pid_mpc_results.json",
    ROOT / "tmp" / "_codex_phase2_occluded_tcp_results.json",
    ROOT / "tmp" / "_codex_phase2_occluded_interfuser_results.json",
)
PHASE2_VLM_REPLACEMENTS = (
    ROOT / "tmp" / "_codex_phase2_occluded_vlm_results.json",
)
REFERENCE_JSON = ROOT / "tmp" / "_codex_reference_sweep.json"
DEFAULT_TCP_CKPT = ROOT / "Models" / "TCP" / "checkpoints" / "tcp_b2d.ckpt"
DEFAULT_INTERFUSER_CKPT = ROOT / "Models" / "InterFuser_ckpt" / "interfuser.pth"
DEFAULT_INTERFUSER_ROOT = ROOT / "Models" / "InterFuser"
DEFAULT_CILRS_CKPT = ROOT / "Models" / "CILRS" / "cilrs" / "best_model.pth"
DEFAULT_CILRS_SRC = ROOT / "Models" / "CILRS" / "src"
DEFAULT_AIM_CKPT = ROOT / "Models" / "AIM" / "aim" / "best_model.pth"
DEFAULT_AIM_SRC = ROOT / "Models" / "AIM" / "src"
DEFAULT_NEAT_ENCODER = ROOT / "Models" / "NEAT" / "neat" / "best_encoder.pth"
DEFAULT_NEAT_DECODER = ROOT / "Models" / "NEAT" / "neat" / "best_decoder.pth"
DEFAULT_NEAT_SRC = ROOT / "Models" / "NEAT" / "src"

MODEL_LABELS = {
    "tcp": "TCP",
    "pid": "PID",
    "mpc": "MPC",
    "interfuser": "InterFuser",
    "cilrs": "CILRS",
    "aim": "AIM",
    "neat": "NEAT",
}
MODEL_KEYS = {v: k for k, v in MODEL_LABELS.items()}
MORE_MODEL_ORDER = ("TCP", "InterFuser", "CILRS", "AIM", "NEAT", "PID", "MPC")
COMBINED_MODEL_ORDER = (
    "TransFuser",
    "TCP",
    "InterFuser",
    "CILRS",
    "AIM",
    "NEAT",
    "PID",
    "MPC",
    "Qwen2.5",
    "Qwen3",
    "GLM",
    "baseline",
    "oracle",
)
TIERS = ("low", "mid", "high")
REPRESENTATIVE_VISIBILITY = ("green_stop", "red_proceed", "unauthorized_go")
MIRROR_FILES = (
    "marshal_bench/actors/scene_actors.py",
    "marshal_bench/actors/traffic_officer.py",
    "marshal_bench/configs/stations.json",
    "marshal_bench/controllers/__init__.py",
    "marshal_bench/controllers/oracle.py",
    "marshal_bench/controllers/_legacy_vision_common.py",
    "marshal_bench/controllers/aim_model.py",
    "marshal_bench/controllers/cilrs_model.py",
    "marshal_bench/controllers/classical.py",
    "marshal_bench/controllers/interfuser_model.py",
    "marshal_bench/controllers/lane_route.py",
    "marshal_bench/controllers/neat_model.py",
    "marshal_bench/controllers/tcp_model.py",
    "marshal_bench/controllers/transfuser_model.py",
    "marshal_bench/criteria/marshal_metrics.py",
    "marshal_bench/criteria/strict_episode_scoring.py",
    "marshal_bench/scenarios/_common.py",
    "scripts/_run_more_e2e_sweep.py",
    "scripts/_run_reference_staging_sweep.py",
    "scripts/_run_transfuser_sweep.py",
    "scripts/_run_vlm_test.py",
)


def _model_key(model: str) -> str:
    key = str(model).strip().lower()
    if key not in MODEL_LABELS:
        raise ValueError(f"Unknown model/controller: {model}")
    return key


def _model_label(model: str) -> str:
    return MODEL_LABELS[_model_key(model)]


def _episode_id(model: str, scenario: str, smoke: bool = False) -> str:
    prefix = "smoke_" if smoke else ""
    return f"{prefix}{_model_key(model)}_{scenario}"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


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


def _clear_episode_outputs(out_root: Path, episode_id: str) -> None:
    episode_dir = out_root / episode_id
    if episode_dir.exists():
        _safe_rmtree(episode_dir, out_root)


def _debug_dir(out_root: Path, model: str, scenario: str, smoke: bool = False) -> Path:
    return out_root / _episode_id(model, scenario, smoke=smoke) / f"{_model_key(model)}_debug"


def _trace_path(out_root: Path, model: str, scenario: str, smoke: bool = False) -> Path:
    key = _model_key(model)
    return _debug_dir(out_root, key, scenario, smoke=smoke) / f"{key}_trace.csv"


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
        "control_rows": len(controls),
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
    return transfuser_sweep._read_metrics(path)


def _read_setup_integrity(episode_dir: Path, controller: str) -> Dict[str, Any]:
    path = episode_dir / "events.json"
    if not path.is_file():
        return {"events_path": str(path), "events_exists": False}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            events = json.load(fh) or []
    except Exception as exc:  # noqa: BLE001
        return {"events_path": str(path), "events_exists": False, "error": repr(exc)}
    setup_name = f"{controller}_setup"
    for event in events:
        if not isinstance(event, dict) or event.get("name") != setup_name:
            continue
        payload = event.get("payload") or {}
        return {
            "events_path": str(path),
            "events_exists": True,
            "setup_event": setup_name,
            "checkpoint": payload.get("checkpoint")
            or payload.get("encoder_checkpoint")
            or payload.get("ckpt_dir"),
            "precision": payload.get("precision") or "fp32",
            "load_info": payload.get("load_info"),
            "model_count": payload.get("model_count"),
            "sensor_count": payload.get("sensor_count"),
            "route_waypoints": payload.get("route_waypoints"),
        }
    return {"events_path": str(path), "events_exists": True, "setup_event": None}


def _debug_frames(debug_dir: Path) -> List[str]:
    patterns = ("input_*.png", "front_*.png", "rgb_front_*.png")
    out: List[str] = []
    for pattern in patterns:
        out.extend(str(p) for p in sorted(debug_dir.glob(pattern)))
    return out


def _pick_frame(row: Dict[str, Any]) -> Optional[str]:
    frames = list(row.get("front_frames") or [])
    if not frames:
        return None
    return frames[min(len(frames) // 2, len(frames) - 1)]


def _rel(path: Optional[str]) -> str:
    if not path:
        return "-"
    try:
        return str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _build_config(
    args: argparse.Namespace,
    model: str,
    scenario: str,
    out_root: Path,
    *,
    smoke: bool = False,
) -> Dict[str, Any]:
    key = _model_key(model)
    spec = vlm.SCENARIOS[scenario]
    cfg = staging.load_staged_config(str(ROOT), scenario, spec, key)
    episode_id = _episode_id(key, scenario, smoke=smoke)
    cfg["episode_id"] = episode_id
    dbg = str((_debug_dir(out_root, key, scenario, smoke=smoke)).resolve())
    if key in {"pid", "mpc"}:
        classical_cfg = {
            "debug_dir": dbg,
            "target_speed_kmh": float(args.target_speed_kmh),
            "log_every_n": int(args.log_every_n),
            "save_debug_every_n": int(args.save_debug_every_n),
            "max_debug_frames": int(args.max_debug_frames),
        }
        cfg["classical"] = classical_cfg
        cfg[key] = dict(classical_cfg)
    elif key == "tcp":
        cfg["tcp"] = {
            "ckpt_path": str(Path(args.tcp_ckpt).resolve()),
            "debug_dir": dbg,
            "log_every_n": int(args.log_every_n),
            "save_debug_every_n": int(args.save_debug_every_n),
            "max_debug_frames": int(args.max_debug_frames),
            "sensor_timeout_s": float(args.sensor_timeout_s),
        }
    elif key == "interfuser":
        cfg["interfuser"] = {
            "ckpt_path": str(Path(args.interfuser_ckpt).resolve()),
            "interfuser_root": str(Path(args.interfuser_root).resolve()),
            "debug_dir": dbg,
            "log_every_n": int(args.log_every_n),
            "save_debug_every_n": int(args.save_debug_every_n),
            "max_debug_frames": int(args.max_debug_frames),
            "sensor_timeout_s": float(args.sensor_timeout_s),
        }
    elif key == "cilrs":
        cfg["cilrs"] = {
            "ckpt_path": str(Path(args.cilrs_ckpt).resolve()),
            "src_root": str(Path(args.cilrs_src).resolve()),
            "debug_dir": dbg,
            "device": str(args.torch_device),
            "precision": "fp32",
            "log_every_n": int(args.log_every_n),
            "save_debug_every_n": int(args.save_debug_every_n),
            "max_debug_frames": int(args.max_debug_frames),
            "sensor_timeout_s": float(args.sensor_timeout_s),
        }
    elif key == "aim":
        cfg["aim"] = {
            "ckpt_path": str(Path(args.aim_ckpt).resolve()),
            "src_root": str(Path(args.aim_src).resolve()),
            "debug_dir": dbg,
            "device": str(args.torch_device),
            "precision": "fp32",
            "log_every_n": int(args.log_every_n),
            "save_debug_every_n": int(args.save_debug_every_n),
            "max_debug_frames": int(args.max_debug_frames),
            "sensor_timeout_s": float(args.sensor_timeout_s),
        }
    elif key == "neat":
        cfg["neat"] = {
            "encoder_ckpt": str(Path(args.neat_encoder).resolve()),
            "decoder_ckpt": str(Path(args.neat_decoder).resolve()),
            "src_root": str(Path(args.neat_src).resolve()),
            "debug_dir": dbg,
            "device": str(args.torch_device),
            "precision": "fp32",
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
    bits = []
    if metrics.get("moved") is True:
        bits.append(f"moved, max {float(metrics.get('max_speed_kmh') or 0):.1f} km/h")
    elif metrics.get("moved") is False:
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


def _run_one(
    client: Any,
    args: argparse.Namespace,
    model: str,
    scenario: str,
    out_root: Path,
    *,
    smoke: bool = False,
) -> Dict[str, Any]:
    key = _model_key(model)
    label = MODEL_LABELS[key]
    cfg = _build_config(args, key, scenario, out_root, smoke=smoke)
    episode_id = str(cfg["episode_id"])
    _clear_episode_outputs(out_root, episode_id)
    episode_dir = out_root / episode_id
    debug_dir = _debug_dir(out_root, key, scenario, smoke=smoke)
    logger = EpisodeLogger(episode_id, output_root=str(out_root))
    logger.save_metadata(
        {
            "episode_id": episode_id,
            "scenario": scenario,
            "controller": key,
            "model": label,
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
    trace = _read_trace(_trace_path(out_root, key, scenario, smoke=smoke))
    integrity = _read_setup_integrity(episode_dir, key)
    frames = _debug_frames(debug_dir)
    episode_metrics = None
    if result:
        try:
            episode_metrics = compute_episode_metrics(result, scenario=scenario).as_dict()
        except Exception:
            episode_metrics = None

    row = {
        "model": label,
        "controller": key,
        "track": "B",
        "scenario": scenario,
        "expected": spec["expect"],
        "status": "ok" if result and error is None else "error",
        "passed": (
            vlm._compliance_passed(compliance, marshal_metrics) if result else False
        ),
        "compliance_reason": vlm._compliance_reason(compliance) if result else None,
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
        "integrity": integrity,
        "runtime_s": round(time.perf_counter() - started, 2),
        "exception": error,
        "traceback": tb,
        "episode_dir": str(episode_dir),
        "result_path": str(episode_dir / "result.json"),
        "front_frames": frames,
        "visibility_sample": {"front": _pick_frame({"front_frames": frames})},
        "raw_result": _jsonable(result) if result else {},
    }
    row["behavior"] = _behavior_summary(row)
    print(
        "{model} / {scenario}: pass={passed} terminated={terminated} speed={speed} trace_rows={trace_rows}".format(
            model=label,
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
    model: str,
    scenario: str,
    message: str,
    *,
    out_root: Path,
    smoke: bool = False,
    terminated_reason: str,
    stdout_path: Optional[Path] = None,
    stderr_path: Optional[Path] = None,
    returncode: Optional[int] = None,
) -> Dict[str, Any]:
    key = _model_key(model)
    label = MODEL_LABELS[key]
    spec = vlm.SCENARIOS[scenario]
    episode_dir = out_root / _episode_id(key, scenario, smoke=smoke)
    row = {
        "model": label,
        "controller": key,
        "track": "B",
        "scenario": scenario,
        "expected": spec["expect"],
        "status": "error",
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
        "trace": _read_trace(_trace_path(out_root, key, scenario, smoke=smoke)),
        "control_finiteness": {},
        "integrity": _read_setup_integrity(episode_dir, key),
        "runtime_s": 0.0,
        "exception": message,
        "traceback": None,
        "episode_dir": str(episode_dir),
        "result_path": str(episode_dir / "result.json"),
        "front_frames": _debug_frames(_debug_dir(out_root, key, scenario, smoke=smoke)),
        "visibility_sample": {},
        "raw_result": {},
        "behavior": message[:180],
        "stdout": str(stdout_path) if stdout_path else None,
        "stderr": str(stderr_path) if stderr_path else None,
        "returncode": returncode,
    }
    print(f"{label} / {scenario}: {terminated_reason.upper()} {message}", flush=True)
    return row


def _child_output_path(model: str, scenario: str, smoke: bool = False) -> Path:
    prefix = "smoke_" if smoke else ""
    return ROOT / "tmp" / f"_codex_more_e2e_child_{prefix}{_model_key(model)}_{scenario}.json"


def _run_one_isolated(
    args: argparse.Namespace,
    model: str,
    scenario: str,
    out_root: Path,
    *,
    smoke: bool = False,
) -> Dict[str, Any]:
    key = _model_key(model)
    child_out = _child_output_path(key, scenario, smoke=smoke)
    try:
        child_out.unlink()
    except FileNotFoundError:
        pass
    episode_dir = out_root / _episode_id(key, scenario, smoke=smoke)
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
        "--model",
        key,
        "--target-speed-kmh",
        str(args.target_speed_kmh),
        "--tcp-ckpt",
        str(args.tcp_ckpt),
        "--interfuser-ckpt",
        str(args.interfuser_ckpt),
        "--interfuser-root",
        str(args.interfuser_root),
        "--cilrs-ckpt",
        str(args.cilrs_ckpt),
        "--cilrs-src",
        str(args.cilrs_src),
        "--aim-ckpt",
        str(args.aim_ckpt),
        "--aim-src",
        str(args.aim_src),
        "--neat-encoder",
        str(args.neat_encoder),
        "--neat-decoder",
        str(args.neat_decoder),
        "--neat-src",
        str(args.neat_src),
        "--torch-device",
        str(args.torch_device),
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
    if smoke:
        cmd.append("--smoke")

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
                key,
                scenario,
                f"could not read child result {child_out}: {exc}",
                out_root=out_root,
                smoke=smoke,
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
            key,
            scenario,
            message,
            out_root=out_root,
            smoke=smoke,
            terminated_reason=terminated,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            returncode=returncode,
        )
    elif returncode != 0:
        row["exception"] = row.get("exception") or f"{message} after writing result"
        row["terminated_reason"] = "native_crash"
        row["status"] = "error"

    row["returncode"] = returncode
    row["stdout"] = str(stdout_path)
    row["stderr"] = str(stderr_path)
    row["runtime_wall_s"] = round(time.perf_counter() - started, 2)
    return row


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


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh) or {}
    except Exception:
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [r for r in (rows or []) if isinstance(r, dict)]


def _load_rows_with_replacements(
    path: Path,
    replacements: Iterable[Path],
) -> List[Dict[str, Any]]:
    rows = _load_rows(path)
    for replacement_path in replacements:
        for row in _load_rows(replacement_path):
            model = row.get("model")
            scenario = row.get("scenario")
            rows = [
                kept for kept in rows
                if not (kept.get("model") == model and kept.get("scenario") == scenario)
            ]
            rows.append(row)
    return rows


def _sort_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scenario_index = {name: idx for idx, name in enumerate(vlm.SCENARIO_ORDER)}
    model_index = {name: idx for idx, name in enumerate(MORE_MODEL_ORDER)}
    return sorted(
        rows,
        key=lambda r: (
            model_index.get(str(r.get("model")), 999),
            scenario_index.get(str(r.get("scenario")), 999),
        ),
    )


def _replace_row(
    rows: List[Dict[str, Any]],
    row: Dict[str, Any],
) -> List[Dict[str, Any]]:
    model = row.get("model")
    scenario = row.get("scenario")
    kept = [
        r for r in rows
        if not (r.get("model") == model and r.get("scenario") == scenario)
    ]
    kept.append(row)
    return _sort_rows(kept)


def _count_for(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    subset = list(rows)
    n_na = sum(1 for r in subset if r.get("status") == "not_applicable")
    applicable = [r for r in subset if r.get("status") != "not_applicable"]
    passed = sum(1 for r in applicable if r.get("passed") is True)
    total = len(applicable)
    return {
        "passed": passed,
        "total": total,
        "not_applicable": n_na,
        "pass_rate": round(passed / total, 4) if total else None,
    }


def _per_tier_counts(rows: List[Dict[str, Any]], model_order: Iterable[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for model in model_order:
        model_rows = [r for r in rows if r.get("model") == model]
        out[model] = {}
        for tier in TIERS:
            out[model][tier] = _count_for(
                r for r in model_rows if REASONING_TIER.get(str(r.get("scenario"))) == tier
            )
        out[model]["overall"] = _count_for(model_rows)
    return out


def _fmt_count(cell: Dict[str, Any]) -> str:
    total = int(cell.get("total") or 0)
    n_na = int(cell.get("not_applicable") or 0)
    if total == 0 and n_na > 0:
        return "n/a"
    if total == 0:
        return "-"
    return f"{int(cell.get('passed') or 0)}/{total}"


def _fmt_bool(value: Any) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "n/a"


def _md(text: Any) -> str:
    return str(text if text is not None else "-").replace("|", "\\|").replace("\n", " ")


def _mirror_identity() -> Dict[str, Any]:
    rows = []
    all_match = True
    for rel in MIRROR_FILES:
        dev = ROOT / rel
        repo = ROOT / "MARSHAL" / rel
        if not dev.exists() and not repo.exists():
            continue
        if not dev.exists() or not repo.exists():
            rows.append({"file": rel, "match": False, "reason": "missing_one_copy"})
            all_match = False
            continue
        dev_bytes = dev.read_bytes()
        repo_bytes = repo.read_bytes()
        match = dev_bytes == repo_bytes
        rows.append(
            {
                "file": rel,
                "match": match,
                "sha256": hashlib.sha256(dev_bytes).hexdigest()[:16],
            }
        )
        all_match = all_match and match
    return {"all_match": all_match, "files": rows}


def _load_frame_checks() -> List[Dict[str, Any]]:
    if not FRAME_CHECKS_JSON.is_file():
        return []
    try:
        with open(FRAME_CHECKS_JSON, "r", encoding="utf-8") as fh:
            rows = json.load(fh) or []
    except Exception:
        return []
    return [r for r in rows if isinstance(r, dict)]


def _load_smoke_rows() -> List[Dict[str, Any]]:
    return _load_rows(SMOKE_RESULTS_JSON)


def _write_outputs(rows: List[Dict[str, Any]], results_json: Path, report_md: Path, *, smoke: bool) -> None:
    rows = _sort_rows(rows)
    per_tier = _per_tier_counts(rows, MORE_MODEL_ORDER)
    mirror = _mirror_identity()
    payload = {
        "run": {
            "controllers": sorted({r.get("controller") for r in rows if r.get("controller")}),
            "track": "B",
            "town": "Town03",
            "fps": 20,
            "timeout_sec": 14,
            "staging_source": staging.STAGING_SOURCE,
            "output_root": str(SMOKE_OUT_ROOT if smoke else OUT_ROOT),
            "smoke": smoke,
            "env": {
                "TCP": "transfuser_ui",
                "CILRS": "transfuser_ui",
                "AIM": "transfuser_ui",
                "NEAT": "transfuser_ui",
                "PID": "marshal",
                "MPC": "marshal",
                "InterFuser": "interfuser",
            },
        },
        "rows": rows,
        "per_tier_counts": per_tier,
        "smoke_rows": _load_smoke_rows() if not smoke else rows,
        "frame_checks": _load_frame_checks(),
        "byte_identical": mirror,
    }
    results_json.parent.mkdir(parents=True, exist_ok=True)
    with open(results_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    _write_report(payload, report_md)
    if not smoke:
        _write_combined_outputs(payload)


def _write_report(payload: Dict[str, Any], path: Path) -> None:
    rows = _sort_rows(payload.get("rows") or [])
    per_tier = payload.get("per_tier_counts") or {}
    smoke_rows = _sort_rows(payload.get("smoke_rows") or [])
    frame_checks = payload.get("frame_checks") or []
    mirror = payload.get("byte_identical") or {}
    errors = [
        r for r in rows
        if r.get("status") == "error" or r.get("returncode") not in (0, None)
    ]
    anomalies = [
        r for r in rows
        if (r.get("trace") or {}).get("finite_controls") is False
        or (r.get("trace") or {}).get("frames_advance") is False
    ]
    lines = [
        "# Track-B Phase 3 Controllers Benchmark",
        "",
        "Live CARLA target: 127.0.0.1:2000, Town03. No map lifecycle operations are performed by this runner.",
        "",
        "## Staging Parity",
        "",
        f"- Shared staging source: `{staging.STAGING_SOURCE}`.",
        "- Authority/gesture figures are off the ego path; hazards remain in-path where intended.",
        "- Episode settings: `fps=20`, `timeout_sec=14`; scoring reads telemetry-grounded `strict_scoring` / `marshal_metrics`.",
        "- PID/MPC use non-privileged ego state plus the shared map lane-follow route helper.",
        "",
        "## Environments",
        "",
        "- TCP: `transfuser_ui` env.",
        "- CILRS: `transfuser_ui` env.",
        "- AIM: `transfuser_ui` env.",
        "- NEAT: `transfuser_ui` env.",
        "- PID: `marshal` env.",
        "- MPC: `marshal` env.",
        "- InterFuser: `interfuser` env when run.",
        "",
        "## Per-Model Per-Tier",
        "",
        "| Model | Low | Mid | High | Overall |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for model in MORE_MODEL_ORDER:
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

    lines.extend(["", "## Smoke Validation", ""])
    if smoke_rows:
        lines.extend(
            [
                "| Model | Scenario | Moved | Finite controls | Max speed km/h | Sample frame |",
                "| --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for row in smoke_rows:
            metrics = row.get("metrics") or {}
            trace = row.get("trace") or {}
            lines.append(
                "| {model} | {scenario} | {moved} | {finite} | {speed} | `{frame}` |".format(
                    model=_md(row.get("model")),
                    scenario=_md(row.get("scenario")),
                    moved="yes" if metrics.get("moved") else "no",
                    finite="yes" if trace.get("finite_controls") else "no",
                    speed=(
                        f"{float(metrics.get('max_speed_kmh') or 0):.2f}"
                        if metrics.get("metrics_exists")
                        else "-"
                    ),
                    frame=_rel(_pick_frame(row)),
                )
            )
    else:
        lines.append("- No smoke rows recorded yet.")
    if frame_checks:
        lines.extend(["", "Frame checks:"])
        for item in frame_checks:
            lines.append(
                "- {model}/{scenario}: `{frame}` - {note}".format(
                    model=item.get("model"),
                    scenario=item.get("scenario"),
                    frame=_rel(item.get("frame")),
                    note=_md(item.get("note")),
                )
            )

    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Model | Scenario | Expected | Pass/fail | Terminated | Final speed km/h | Behavior |",
            "| --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in rows:
        lines.append(
            "| {model} | {scenario} | {expected} | {passed} | {terminated} | {speed} | {behavior} |".format(
                model=_md(row.get("model")),
                scenario=_md(row.get("scenario")),
                expected=_md(row.get("expected")),
                passed=_fmt_bool(row.get("passed")),
                terminated=_md(row.get("terminated_reason") or row.get("status") or "-"),
                speed=(
                    f"{float(row['final_speed_kmh']):.2f}"
                    if isinstance(row.get("final_speed_kmh"), (int, float))
                    else "-"
                ),
                behavior=_md(row.get("behavior")),
            )
        )

    lines.extend(["", "## Crash / Control Notes", ""])
    if errors:
        lines.append(f"- Episodes with child error/native crash/timeout: {len(errors)}.")
        for row in errors:
            lines.append(
                "- {model}/{scenario}: returncode={returncode}, terminated={terminated}, error={error}".format(
                    model=row.get("model"),
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
            trace = row.get("trace") or {}
            lines.append(
                f"- {row.get('model')}/{row.get('scenario')}: finite_controls={trace.get('finite_controls')}, frames_advance={trace.get('frames_advance')}, first_error={trace.get('first_error')}"
            )
    else:
        lines.append("- Recorded controller traces have finite controls and monotonically advancing frames.")

    lines.extend(["", "## Dev/Repo Byte Identity", ""])
    lines.append(
        "- dev `marshal_bench/...` and repo `MARSHAL/marshal_bench/...`: "
        + ("byte-identical." if mirror.get("all_match") else "mismatch detected.")
    )
    for item in mirror.get("files") or []:
        lines.append(
            "- {file}: {status}".format(
                file=item.get("file"),
                status="match" if item.get("match") else item.get("reason", "mismatch"),
            )
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _short_vlm_name(model: str) -> str:
    if "Qwen2.5" in model:
        return "Qwen2.5"
    if "Qwen3" in model:
        return "Qwen3"
    if "GLM" in model:
        return "GLM"
    return model


def _load_more_combined_rows(more_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in more_rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "model": row.get("model"),
                "source_model": row.get("controller"),
                "track": "B",
                "scenario": row.get("scenario"),
                "expected": row.get("expected"),
                "status": row.get("status", "ok"),
                "passed": row.get("passed"),
                "terminated_reason": row.get("terminated_reason"),
                "exception": row.get("exception"),
                "visibility_frame": (row.get("visibility_sample") or {}).get("front"),
                "raw": row,
            }
        )
    return out


def _load_e2e_combined_rows(
    path: Path,
    replacements: Iterable[Path] = (),
) -> List[Dict[str, Any]]:
    out = []
    for row in _load_rows_with_replacements(path, replacements):
        model = row.get("model")
        if not model:
            continue
        out.append(
            {
                "model": model,
                "source_model": row.get("controller") or row.get("model"),
                "track": row.get("track", "B"),
                "scenario": row.get("scenario"),
                "expected": row.get("expected"),
                "status": row.get("status", "ok"),
                "passed": row.get("passed"),
                "terminated_reason": row.get("terminated_reason"),
                "exception": row.get("exception"),
                "visibility_frame": (row.get("visibility_sample") or {}).get("front"),
                "raw": row,
            }
        )
    return out


def _load_vlm_combined_rows(
    path: Path,
    replacements: Iterable[Path] = (),
) -> List[Dict[str, Any]]:
    out = []
    for row in _load_rows_with_replacements(path, replacements):
        if not isinstance(row, dict):
            continue
        label = _short_vlm_name(str(row.get("model") or ""))
        if label not in {"Qwen2.5", "Qwen3", "GLM"}:
            continue
        out.append(
            {
                "model": label,
                "source_model": row.get("model"),
                "track": "C",
                "scenario": row.get("scenario"),
                "expected": row.get("expected"),
                "status": row.get("status", "ok"),
                "passed": row.get("passed"),
                "terminated_reason": row.get("terminated_reason"),
                "exception": row.get("exception"),
                "visibility_frame": row.get("visibility_frame"),
                "raw": row,
            }
        )
    return out


def _load_reference_combined_rows() -> Tuple[List[Dict[str, Any]], str]:
    if REFERENCE_JSON.is_file():
        out = []
        for row in _load_rows(REFERENCE_JSON):
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "model": row.get("model"),
                    "track": "reference",
                    "scenario": row.get("scenario"),
                    "expected": row.get("expected"),
                    "status": row.get("status", "ok"),
                    "passed": row.get("passed"),
                    "tier": row.get("tier") or REASONING_TIER.get(str(row.get("scenario"))),
                    "terminated_reason": row.get("terminated_reason"),
                    "exception": row.get("exception"),
                    "raw": row,
                }
            )
        return out, str(REFERENCE_JSON)

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
                    "status": "ok",
                    "passed": ep.get("passed"),
                    "tier": REASONING_TIER.get(str(scenario)),
                    "raw": ep,
                }
            )
    return rows, str(score_path)


def _dedupe_combined_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        model = str(row.get("model") or "")
        scenario = str(row.get("scenario") or "")
        if not model or not scenario:
            continue
        deduped[(model, scenario)] = row
    scenario_index = {name: idx for idx, name in enumerate(vlm.SCENARIO_ORDER)}
    model_index = {name: idx for idx, name in enumerate(COMBINED_MODEL_ORDER)}
    return sorted(
        deduped.values(),
        key=lambda r: (
            model_index.get(str(r.get("model")), 999),
            scenario_index.get(str(r.get("scenario")), 999),
        ),
    )


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
    return _per_tier_counts(rows, COMBINED_MODEL_ORDER)


def _raw_for_integrity(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("raw")
    return raw if isinstance(raw, dict) else row


def _strict_for_row(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = _raw_for_integrity(row)
    strict = raw.get("strict_scoring")
    return strict if isinstance(strict, dict) else {}


def _row_max_speed_kmh(row: Dict[str, Any]) -> Optional[float]:
    raw = _raw_for_integrity(row)
    candidates = []
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    strict = raw.get("strict_scoring") if isinstance(raw.get("strict_scoring"), dict) else {}
    for value in (
        metrics.get("max_speed_kmh"),
        strict.get("max_speed_kmh"),
        raw.get("final_speed_kmh"),
        row.get("final_speed_kmh"),
    ):
        try:
            f = float(value)
        except Exception:
            continue
        if math.isfinite(f):
            candidates.append(f)
    return max(candidates) if candidates else None


def _setup_integrity_for_raw(raw: Dict[str, Any], model: str) -> Dict[str, Any]:
    direct = raw.get("integrity")
    if isinstance(direct, dict) and direct:
        return direct
    controller = raw.get("controller") or MODEL_KEYS.get(model) or str(model).lower()
    episode_dir = raw.get("episode_dir")
    if episode_dir:
        return _read_setup_integrity(Path(str(episode_dir)), str(controller))
    return {}


def _load_counts_text(load_info: Any, setup: Dict[str, Any], model: str) -> str:
    if isinstance(load_info, dict):
        if "checkpoints" in load_info:
            checkpoints = [
                item for item in (load_info.get("checkpoints") or [])
                if isinstance(item, dict)
            ]
            key_counts = [
                item.get("state_dict_keys")
                for item in checkpoints
                if item.get("state_dict_keys") is not None
            ]
            return (
                "{count} checkpoints, keys={keys}, {missing} missing, "
                "{unexpected} unexpected, {shape} shape mismatches"
            ).format(
                count=len(checkpoints) or load_info.get("model_count", "?"),
                keys="+".join(str(v) for v in key_counts) if key_counts else "?",
                missing=load_info.get("missing", "?"),
                unexpected=load_info.get("unexpected", "?"),
                shape=load_info.get("shape_mismatch", "?"),
            )
        if "encoder" in load_info or "decoder" in load_info:
            parts = []
            for key in ("encoder", "decoder"):
                info = load_info.get(key) or {}
                if isinstance(info, dict):
                    parts.append(
                        "{key} {keys} keys, {missing} missing, {unexpected} unexpected".format(
                            key=key,
                            keys=info.get("state_dict_keys", "?"),
                            missing=info.get("missing", "?"),
                            unexpected=info.get("unexpected", "?"),
                        )
                    )
            return "; ".join(parts) if parts else "checkpoint counts unavailable"
        if "state_dict_keys" in load_info:
            return "{keys} keys, {missing} missing, {unexpected} unexpected".format(
                keys=load_info.get("state_dict_keys", "?"),
                missing=load_info.get("missing", "?"),
                unexpected=load_info.get("unexpected", "?"),
            )
    if setup.get("model_count") is not None:
        return f"ensemble model_count={setup.get('model_count')}; key counts not exposed in Phase 2 artifact"
    if model in {"PID", "MPC", "baseline", "oracle"}:
        return "n/a (no neural checkpoint)"
    if model in {"Qwen2.5", "Qwen3", "GLM"}:
        return "n/a (VLM provider/HF checkpoint not loaded by MARSHAL runner)"
    return "checkpoint counts unavailable"


def _combined_integrity(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for model in COMBINED_MODEL_ORDER:
        model_rows = [r for r in rows if r.get("model") == model]
        max_speeds = [_row_max_speed_kmh(r) for r in model_rows]
        max_speeds = [v for v in max_speeds if v is not None]
        moved = any(float(v) > 1.0 for v in max_speeds)
        invalid = sum(1 for r in model_rows if _strict_for_row(r).get("invalid") is True)
        setup = {}
        for row in model_rows:
            raw = _raw_for_integrity(row)
            setup = _setup_integrity_for_raw(raw, model)
            if setup:
                break
        load_info = setup.get("load_info") if isinstance(setup, dict) else None
        precision = setup.get("precision") if isinstance(setup, dict) else None
        if not precision:
            if model in {"TransFuser", "TCP", "InterFuser", "CILRS", "AIM", "NEAT"}:
                precision = "fp32"
            elif model in {"PID", "MPC", "baseline", "oracle"}:
                precision = "n/a"
            else:
                precision = "provider/default"
        out[model] = {
            "episodes": len(model_rows),
            "checkpoint_load": _load_counts_text(load_info, setup, model),
            "precision": precision,
            "moved": moved if model_rows else None,
            "max_speed_kmh": round(max(max_speeds), 4) if max_speeds else None,
            "invalid_episodes": invalid,
            "setup": setup,
        }
    return out


def _combined_takeaway(rows: List[Dict[str, Any]]) -> str:
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

    e2e_models = ("TransFuser", "TCP", "InterFuser", "CILRS", "AIM", "NEAT", "PID", "MPC")
    vlm_models = ("Qwen2.5", "Qwen3", "GLM")
    e2e_bits = []
    for model in e2e_models:
        c = model_count(model, authority_stop)
        if c["total"] or c["not_applicable"]:
            e2e_bits.append(f"{model} {_fmt_count(c)}")
    vlm_bits = []
    for model in vlm_models:
        c = model_count(model, authority_stop)
        if c["total"] or c["not_applicable"]:
            vlm_bits.append(f"{model} {_fmt_count(c)}")
    hazard_bits = []
    for model in e2e_models:
        c = model_count(model, hazard)
        if c["total"] or c["not_applicable"]:
            hazard_bits.append(f"{model} {_fmt_count(c)}")
    return (
        "Authority-STOP cases use off-path human figures, so STOP compliance is separated from in-lane obstacle braking. "
        + "Track-B/E2E authority-STOP counts: "
        + (", ".join(e2e_bits) if e2e_bits else "none")
        + ". VLM authority-STOP counts: "
        + (", ".join(vlm_bits) if vlm_bits else "none")
        + ". Hazard counts for Track-B/E2E controllers: "
        + ", ".join(hazard_bits)
        + ". The authority gap is the consistent pattern: E2E stacks can move and sometimes handle physical hazards, but they do not reliably treat an off-path human directive as a higher-priority traffic authority."
    )


def _combined_calibration_note(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    oracle = [r for r in rows if r.get("model") == "oracle"]
    passed = sum(1 for r in oracle if r.get("passed") is True)
    invalid = sum(1 for r in oracle if _strict_for_row(r).get("invalid") is True)
    return {
        "oracle_final_score": f"{passed}/{len(oracle)}",
        "oracle_invalid": invalid,
        "thresholds": {
            "STOP/HOLD": "after reaction budget, speed < 1.0 km/h, no junction entry, stopline clearance >= 1.0 m",
            "DETOUR": "max |lateral| >= 1.5 m and forward progress >= obstacle_forward + 4.0 m",
            "YIELD": "pre-yield speed >= 5.0 km/h, slow/stop <= 3.0 km/h, resume >= 5.0 km/h, post-yield |lateral| >= 1.0 m",
            "PROCEED": "junction entry while moving >= 2.0 km/h; rule_hierarchy also requires <= 3.0 km/h yield within 14.0 m of pedestrian before entry",
            "INVALID": "reserved for malformed/missing telemetry fields, setup errors, or controller/runtime errors; evidence-window misses are FAIL",
        },
        "capture_note": "Reference runs use strict telemetry fields for baseline and oracle; planar speed excludes spawn-settling vertical velocity, and self/static-road settling contacts are filtered from collision_count.",
    }


def _write_combined_outputs(payload: Dict[str, Any]) -> None:
    more_rows = payload.get("rows") or []
    ref_rows, ref_source = _load_reference_combined_rows()
    rows: List[Dict[str, Any]] = []
    rows.extend(_load_e2e_combined_rows(PHASE2_TRANSFUSER_JSON, PHASE2_TRANSFUSER_REPLACEMENTS))
    rows.extend(_load_e2e_combined_rows(PHASE2_EXISTING_JSON, PHASE2_EXISTING_REPLACEMENTS))
    rows.extend(_load_more_combined_rows(more_rows))
    rows.extend(_load_vlm_combined_rows(PHASE2_VLM_JSON, PHASE2_VLM_REPLACEMENTS))
    rows.extend(ref_rows)
    rows = _dedupe_combined_rows(rows)
    per_scenario = _combined_per_scenario(rows)
    per_tier = _combined_per_tier(rows)
    integrity = _combined_integrity(rows)
    mirror = _mirror_identity()
    calibration_note = _combined_calibration_note(rows)
    sources = {
        "phase2_transfuser": str(PHASE2_TRANSFUSER_JSON),
        "phase2_transfuser_replacements": [str(p) for p in PHASE2_TRANSFUSER_REPLACEMENTS],
        "phase2_existing_e2e": str(PHASE2_EXISTING_JSON),
        "phase2_existing_replacements": [str(p) for p in PHASE2_EXISTING_REPLACEMENTS],
        "phase2_vlm": str(PHASE2_VLM_JSON),
        "phase2_vlm_replacements": [str(p) for p in PHASE2_VLM_REPLACEMENTS],
        "phase3_new_e2e": str(RESULTS_JSON),
        "reference": ref_source,
    }
    combined = {
        "run": {
            "staging_source": staging.STAGING_SOURCE,
            "fps": 20,
            "timeout_sec": 14,
            "sources": sources,
        },
        "rows": rows,
        "per_scenario_counts": per_scenario,
        "per_tier_counts": per_tier,
        "integrity": integrity,
        "byte_identical": mirror,
        "calibration_note": calibration_note,
        "takeaway": _combined_takeaway(rows),
    }
    with open(COMBINED_JSON, "w", encoding="utf-8") as fh:
        json.dump(combined, fh, indent=2, default=str)

    lines = [
        "# MARSHAL Combined Comparison",
        "",
        f"Shared staging source: `{staging.STAGING_SOURCE}`. Authority/gesture figures are off the ego path; hazard obstacles remain in-path where intended.",
        "",
        "## Calibration Note",
        "",
        f"- Oracle final strict score: {calibration_note['oracle_final_score']} (INVALID={calibration_note['oracle_invalid']}).",
        f"- STOP/HOLD: {calibration_note['thresholds']['STOP/HOLD']}.",
        f"- DETOUR: {calibration_note['thresholds']['DETOUR']}.",
        f"- YIELD: {calibration_note['thresholds']['YIELD']}.",
        f"- PROCEED: {calibration_note['thresholds']['PROCEED']}.",
        f"- INVALID policy: {calibration_note['thresholds']['INVALID']}.",
        f"- Capture: {calibration_note['capture_note']}",
        "",
        "## Per-Scenario Counts",
        "",
        "| Scenario | Tier | " + " | ".join(COMBINED_MODEL_ORDER) + " |",
        "| --- | --- | " + " | ".join("---:" for _ in COMBINED_MODEL_ORDER) + " |",
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
    lines.extend(["", "## Integrity", ""])
    for model in COMBINED_MODEL_ORDER:
        info = integrity.get(model) or {}
        moved = info.get("moved")
        moved_text = "yes" if moved is True else ("no" if moved is False else "n/a")
        max_speed = info.get("max_speed_kmh")
        max_speed_text = f"{float(max_speed):.2f} km/h" if isinstance(max_speed, (int, float)) else "n/a"
        lines.append(
            "- {model}: checkpoint load={load}; precision={precision}; moved={moved}, max_speed={max_speed}; INVALID={invalid}.".format(
                model=model,
                load=_md(info.get("checkpoint_load")),
                precision=_md(info.get("precision")),
                moved=moved_text,
                max_speed=max_speed_text,
                invalid=int(info.get("invalid_episodes") or 0),
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
    lines.extend(["", "## Dev/Repo Byte Identity", ""])
    lines.append(
        "- dev `marshal_bench/...` and repo `MARSHAL/marshal_bench/...`: "
        + ("byte-identical." if mirror.get("all_match") else "mismatch detected.")
    )
    for item in mirror.get("files") or []:
        lines.append(
            "- {file}: {status}".format(
                file=item.get("file"),
                status="match" if item.get("match") else item.get("reason", "mismatch"),
            )
        )
    lines.extend(["", "## Takeaway", "", combined["takeaway"], ""])
    with open(COMBINED_REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _na_rows(model: str, scenarios: Iterable[str], reason: str) -> List[Dict[str, Any]]:
    key = _model_key(model)
    label = MODEL_LABELS[key]
    rows = []
    for scenario in scenarios:
        spec = vlm.SCENARIOS[scenario]
        rows.append(
            {
                "model": label,
                "controller": key,
                "track": "B",
                "scenario": scenario,
                "expected": spec["expect"],
                "status": "not_applicable",
                "passed": None,
                "compliance_reason": None,
                "terminated_reason": "not_applicable",
                "final_speed_kmh": None,
                "marshal_metrics": {},
                "episode_metrics": None,
                "metrics": {},
                "trace": {},
                "control_finiteness": {},
                "integrity": {},
                "runtime_s": 0.0,
                "exception": reason,
                "traceback": None,
                "episode_dir": None,
                "result_path": None,
                "front_frames": [],
                "visibility_sample": {},
                "raw_result": {},
                "behavior": f"N/A: {reason}",
            }
        )
    return rows


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", help="Scenario keys. Defaults to all 14, or red_proceed for --smoke.")
    parser.add_argument("--models", nargs="+", default=["cilrs", "aim", "neat"])
    parser.add_argument("--results-json", default=str(RESULTS_JSON))
    parser.add_argument("--report", default=str(REPORT_MD))
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--target-speed-kmh", type=float, default=25.0)
    parser.add_argument("--tcp-ckpt", default=str(DEFAULT_TCP_CKPT))
    parser.add_argument("--interfuser-ckpt", default=str(DEFAULT_INTERFUSER_CKPT))
    parser.add_argument("--interfuser-root", default=str(DEFAULT_INTERFUSER_ROOT))
    parser.add_argument("--cilrs-ckpt", default=str(DEFAULT_CILRS_CKPT))
    parser.add_argument("--cilrs-src", default=str(DEFAULT_CILRS_SRC))
    parser.add_argument("--aim-ckpt", default=str(DEFAULT_AIM_CKPT))
    parser.add_argument("--aim-src", default=str(DEFAULT_AIM_SRC))
    parser.add_argument("--neat-encoder", default=str(DEFAULT_NEAT_ENCODER))
    parser.add_argument("--neat-decoder", default=str(DEFAULT_NEAT_DECODER))
    parser.add_argument("--neat-src", default=str(DEFAULT_NEAT_SRC))
    parser.add_argument("--torch-device", default="cuda")
    parser.add_argument("--sensor-timeout-s", type=float, default=0.75)
    parser.add_argument("--log-every-n", type=int, default=20)
    parser.add_argument("--save-debug-every-n", type=int, default=25)
    parser.add_argument("--max-debug-frames", type=int, default=4)
    parser.add_argument("--wall-timeout", type=float, default=300.0)
    parser.add_argument("--na-model", default=None)
    parser.add_argument("--na-reason", default=None)
    parser.add_argument("--child-run-one", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child-output", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--model", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _child_main(args: argparse.Namespace) -> int:
    if len(args.scenarios) != 1:
        raise SystemExit("--child-run-one requires exactly one scenario")
    if not args.child_output:
        raise SystemExit("--child-run-one requires --child-output")
    if not args.model:
        raise SystemExit("--child-run-one requires --model")
    scenario = args.scenarios[0]
    if scenario not in vlm.SCENARIOS:
        raise SystemExit(f"Unknown scenario: {scenario}")
    out_root = SMOKE_OUT_ROOT if args.smoke else OUT_ROOT

    setup_root_logger()
    ok, status = _carla_town03_status(timeout_s=120.0)
    if not ok:
        raise SystemExit(f"{status}. Not loading maps.")
    carla = import_carla()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(120.0)
    try:
        row = _run_one(client, args, args.model, scenario, out_root, smoke=args.smoke)
    except Exception as exc:  # noqa: BLE001
        row = _failure_row(
            args.model,
            scenario,
            f"{repr(exc)}\n{traceback.format_exc()}",
            out_root=out_root,
            smoke=args.smoke,
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

    if args.na_model and not args.na_reason:
        raise SystemExit("--na-model requires --na-reason")

    models = [_model_key(model) for model in (args.models or [])]
    scenarios = args.scenarios or (["red_proceed"] if args.smoke else list(vlm.SCENARIO_ORDER))
    unknown = [s for s in scenarios if s not in vlm.SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenario(s): {', '.join(unknown)}")

    results_json = Path(args.results_json)
    report_md = Path(args.report)
    out_root = SMOKE_OUT_ROOT if args.smoke else OUT_ROOT
    if args.smoke and str(results_json) == str(RESULTS_JSON):
        results_json = SMOKE_RESULTS_JSON
    if args.smoke and str(report_md) == str(REPORT_MD):
        report_md = SMOKE_REPORT_MD

    rows = _load_rows(results_json)
    if args.na_model:
        na_model = _model_key(args.na_model)
        for row in _na_rows(na_model, scenarios, str(args.na_reason)):
            rows = _replace_row(rows, row)
        _write_outputs(rows, results_json, report_md, smoke=args.smoke)
        print(f"Wrote {results_json}")
        print(f"Wrote {report_md}")
        return 0

    ok, status = _carla_town03_status()
    if not ok:
        raise SystemExit(f"{status}. Not loading maps.")

    out_root.mkdir(parents=True, exist_ok=True)
    setup_root_logger()
    for model in models:
        for scenario in scenarios:
            print(f"running {MODEL_LABELS[model]} {scenario} ...", flush=True)
            row = _run_one_isolated(args, model, scenario, out_root, smoke=args.smoke)
            rows = _replace_row(rows, row)
            _write_outputs(rows, results_json, report_md, smoke=args.smoke)

    _write_outputs(rows, results_json, report_md, smoke=args.smoke)
    print(f"\nWrote {results_json}")
    print(f"Wrote {report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
