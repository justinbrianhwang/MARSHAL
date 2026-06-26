"""Run OpenEMMA/LMDrive full-planner controllers on shared MARSHAL staging."""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

THIS = Path(__file__).resolve().parent
ROOT = THIS.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(THIS) not in sys.path:
    sys.path.insert(0, str(THIS))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _run_vlm_test as vlm  # noqa: E402
import _shared_staging as staging  # noqa: E402
from marshal_bench.criteria.marshal_metrics import (  # noqa: E402
    REASONING_TIER,
    compute_episode_metrics,
)
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.utils.logging_utils import EpisodeLogger, setup_root_logger  # noqa: E402

CONTROLLER_LABELS = {
    "openemma": "OpenEMMA",
    "lmdrive": "LMDrive",
}

DEFAULT_RESULTS = {
    "openemma": ROOT / "tmp" / "_codex_openemma_results.json",
    "lmdrive": ROOT / "tmp" / "_codex_lmdrive_results.json",
}
DEFAULT_REPORTS = {
    "openemma": ROOT / "tmp" / "_codex_openemma_report.md",
    "lmdrive": ROOT / "tmp" / "_codex_lmdrive_report.md",
}
DEFAULT_OUT_ROOTS = {
    "openemma": ROOT / "tmp" / "_codex_openemma_runs",
    "lmdrive": ROOT / "tmp" / "_codex_lmdrive_runs",
}
SMOKE_RESULTS = {
    "openemma": ROOT / "tmp" / "_codex_openemma_smoke.json",
    "lmdrive": ROOT / "tmp" / "_codex_lmdrive_smoke.json",
}
SMOKE_REPORTS = {
    "openemma": ROOT / "tmp" / "_codex_openemma_smoke.md",
    "lmdrive": ROOT / "tmp" / "_codex_lmdrive_smoke.md",
}
SMOKE_OUT_ROOTS = {
    "openemma": ROOT / "tmp" / "_openemma_smoke_runs",
    "lmdrive": ROOT / "tmp" / "_lmdrive_smoke_runs",
}
SMOKE_DEBUG_DIRS = {
    "openemma": ROOT / "tmp" / "_openemma_smoke",
    "lmdrive": ROOT / "tmp" / "_lmdrive_smoke",
}


def _controller_key(value: str) -> str:
    key = str(value or "").strip().lower()
    if key not in CONTROLLER_LABELS:
        raise ValueError(f"unsupported full-planner controller: {value}")
    return key


def _episode_id(controller: str, scenario: str, *, smoke: bool) -> str:
    prefix = "smoke_" if smoke else ""
    return f"{prefix}{controller}_{scenario}"


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else str(obj)
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


def _clear_smoke_debug_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    root = (ROOT / "tmp").resolve()
    if root not in path.resolve().parents:
        raise RuntimeError(f"refusing to clear debug dir outside tmp: {path}")
    for pattern in ("front_*.png", "input_*.png", "*_trace.csv"):
        for item in path.glob(pattern):
            try:
                item.unlink()
            except FileNotFoundError:
                pass


def _debug_dir(controller: str, scenario: str, out_root: Path, *, smoke: bool) -> Path:
    if smoke:
        return SMOKE_DEBUG_DIRS[controller]
    return out_root / _episode_id(controller, scenario, smoke=smoke) / f"{controller}_debug"


def _trace_path(controller: str, debug_dir: Path) -> Path:
    return debug_dir / f"{controller}_trace.csv"


def _read_metrics(path: Path) -> Dict[str, Any]:
    speeds: List[float] = []
    rows = 0
    if not path.is_file():
        return {"metrics_exists": False, "rows": 0, "moved": False}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            if row.get("key") != "speed_kmh":
                continue
            try:
                speed = float(row.get("value") or 0.0)
            except Exception:
                continue
            if math.isfinite(speed):
                speeds.append(speed)
    max_speed = max(speeds) if speeds else 0.0
    return {
        "metrics_exists": True,
        "rows": rows,
        "speed_rows": len(speeds),
        "max_speed_kmh": max_speed,
        "last_speed_kmh": speeds[-1] if speeds else None,
        "moved": max_speed > 1.0,
    }


def _read_trace(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"trace_exists": False, "trace_path": str(path)}
    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rows.extend(csv.DictReader(fh))
    modes: Dict[str, int] = {}
    nonfinite = 0
    errors = []
    latencies = []
    controls = 0
    for row in rows:
        mode = str(row.get("mode") or "")
        modes[mode] = modes.get(mode, 0) + 1
        if row.get("error"):
            errors.append(str(row.get("error")))
        try:
            vals = [float(row.get(k) or 0.0) for k in ("throttle", "brake", "steer")]
            controls += 1
            if not all(math.isfinite(v) for v in vals):
                nonfinite += 1
        except Exception:
            nonfinite += 1
        try:
            lat = float(row.get("latency_s") or 0.0)
            if math.isfinite(lat) and lat > 0:
                latencies.append(lat)
        except Exception:
            pass
    return {
        "trace_exists": True,
        "trace_path": str(path),
        "rows": len(rows),
        "modes": modes,
        "control_rows": controls,
        "finite_controls": controls > 0 and nonfinite == 0,
        "nonfinite_rows": nonfinite,
        "error_rows": len(errors),
        "first_error": errors[0] if errors else "",
        "latency_s": {
            "count": len(latencies),
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
            "mean": sum(latencies) / len(latencies) if latencies else None,
        },
    }


def _debug_frames(path: Path) -> List[str]:
    frames: List[str] = []
    for pattern in ("front_*.png", "input_*.png", "rgb_front_*.png"):
        frames.extend(str(p) for p in sorted(path.glob(pattern)))
    return frames


def _pick_frame(frames: Iterable[str]) -> Optional[str]:
    values = list(frames)
    if not values:
        return None
    return values[min(len(values) // 2, len(values) - 1)]


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
            "checkpoint": payload.get("checkpoint"),
            "precision": payload.get("precision"),
            "load_info": payload.get("load_info") or payload.get("backend_info"),
            "sensor_count": payload.get("sensor_count"),
            "route_waypoints": payload.get("route_waypoints"),
            "query_period_s": payload.get("query_period_s"),
        }
    return {"events_path": str(path), "events_exists": True, "setup_event": None}


def _planner_queries(episode_dir: Path, controller: str) -> List[Dict[str, Any]]:
    path = episode_dir / "events.json"
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            events = json.load(fh) or []
    except Exception:
        return []
    name = f"{controller}_planner_query"
    out = []
    for event in events:
        if isinstance(event, dict) and event.get("name") == name:
            payload = event.get("payload") or {}
            if isinstance(payload, dict):
                out.append(payload)
    return out


def _per_tier_counts(rows: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for tier in ("low", "mid", "high"):
        subset = [
            r for r in rows
            if r.get("model") == model and REASONING_TIER.get(str(r.get("scenario"))) == tier
        ]
        out[tier] = _count(subset)
    out["overall"] = _count([r for r in rows if r.get("model") == model])
    return out


def _count(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    values = list(rows)
    passed = sum(1 for r in values if r.get("passed") is True)
    return {
        "passed": passed,
        "total": len(values),
        "pass_rate": round(passed / len(values), 4) if values else None,
    }


def _fmt_count(cell: Dict[str, Any]) -> str:
    total = int(cell.get("total") or 0)
    if total == 0:
        return "-"
    return f"{int(cell.get('passed') or 0)}/{total}"


def _fmt_float(value: Any, suffix: str = "") -> str:
    try:
        f = float(value)
    except Exception:
        return "-"
    return f"{f:.2f}{suffix}" if math.isfinite(f) else "-"


def _build_config(args: argparse.Namespace, controller: str, scenario: str, out_root: Path, *, smoke: bool) -> Dict[str, Any]:
    spec = vlm.SCENARIOS[scenario]
    cfg = staging.load_staged_config(str(ROOT), scenario, spec, controller)
    cfg["episode_id"] = _episode_id(controller, scenario, smoke=smoke)
    dbg = _debug_dir(controller, scenario, out_root, smoke=smoke)
    if smoke:
        _clear_smoke_debug_dir(dbg)
    planner_cfg = {
        "debug_dir": str(dbg.resolve()),
        "query_period_s": float(args.query_period_s),
        "target_speed_kmh": float(args.target_speed_kmh),
        "sensor_timeout_s": float(args.sensor_timeout_s),
        "log_every_n": int(args.log_every_n),
        "save_debug_every_n": int(args.save_debug_every_n),
        "max_debug_frames": int(args.max_debug_frames),
        "close_backend_on_teardown": not bool(args.reuse_backend),
        "reuse_backend": bool(args.reuse_backend),
        "raise_on_planner_error": True,
    }
    if controller == "openemma":
        planner_cfg.update(
            {
                "backend": "qwen2vl",
                "max_new_tokens": int(args.max_new_tokens),
            }
        )
    elif controller == "lmdrive":
        planner_cfg.update({"allow_native": True})
    cfg[controller] = planner_cfg
    return cfg


def _run_one(client: Any, args: argparse.Namespace, controller: str, scenario: str, out_root: Path, *, smoke: bool) -> Dict[str, Any]:
    label = CONTROLLER_LABELS[controller]
    cfg = _build_config(args, controller, scenario, out_root, smoke=smoke)
    episode_id = str(cfg["episode_id"])
    episode_dir = out_root / episode_id
    if episode_dir.exists():
        _safe_rmtree(episode_dir, out_root)
    logger = EpisodeLogger(episode_id, output_root=str(out_root))
    logger.save_metadata(
        {
            "episode_id": episode_id,
            "scenario": scenario,
            "controller": controller,
            "model": label,
            "config": _jsonable(cfg),
            "staging_source": staging.STAGING_SOURCE,
        }
    )
    cfg["_episode_logger"] = logger

    spec = vlm.SCENARIOS[scenario]
    mod = importlib.import_module(f"marshal_bench.scenarios.{spec['module']}")
    restore = staging.apply_runner_local_patches(scenario, mod, cfg)
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
        restore()
        try:
            if result:
                logger.save_metadata({"result": _jsonable(result)}, name="result.json")
        except Exception:
            pass
        try:
            logger.close()
        except Exception:
            pass

    strict = result.get("strict_scoring") if isinstance(result, dict) else {}
    if not isinstance(strict, dict) or not strict:
        strict = {
            "passed": False,
            "invalid": True,
            "verdict": "INVALID",
            "reason": error or "missing strict_scoring in scenario result",
        }
    episode_metrics = None
    if result:
        try:
            episode_metrics = compute_episode_metrics(result, scenario=scenario).as_dict()
        except Exception:
            episode_metrics = None
    debug = _debug_dir(controller, scenario, out_root, smoke=smoke)
    metrics = _read_metrics(episode_dir / "metrics.csv")
    trace = _read_trace(_trace_path(controller, debug))
    frames = _debug_frames(debug)
    queries = _planner_queries(episode_dir, controller)
    row = {
        "model": label,
        "controller": controller,
        "track": "B",
        "scenario": scenario,
        "expected": spec["expect"],
        "status": "ok" if result and error is None else "error",
        "passed": bool(strict.get("passed")),
        "strict_scoring": strict,
        "episode_metrics": episode_metrics,
        "marshal_metrics": result.get("marshal_metrics") if isinstance(result, dict) else {},
        "terminated_reason": result.get("terminated_reason", "error" if error else None) if isinstance(result, dict) else "error",
        "metrics": metrics,
        "trace": trace,
        "integrity": _read_setup_integrity(episode_dir, controller),
        "query_period_s": float(args.query_period_s),
        "planner_queries": queries,
        "first_planner_query": queries[0] if queries else None,
        "runtime_s": round(time.perf_counter() - started, 2),
        "exception": error,
        "traceback": tb,
        "episode_dir": str(episode_dir),
        "result_path": str(episode_dir / "result.json"),
        "front_frames": frames,
        "visibility_sample": {"front": _pick_frame(frames)},
        "raw_result": _jsonable(result) if result else {},
    }
    print(
        "{model} / {scenario}: strict={verdict} moved={moved} max={speed} km/h trace_rows={trace_rows}".format(
            model=label,
            scenario=scenario,
            verdict=strict.get("verdict"),
            moved=metrics.get("moved"),
            speed=_fmt_float(metrics.get("max_speed_kmh")),
            trace_rows=trace.get("rows"),
        ),
        flush=True,
    )
    return row


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


def _replace_row(rows: List[Dict[str, Any]], row: Dict[str, Any]) -> List[Dict[str, Any]]:
    kept = [
        r for r in rows
        if not (r.get("model") == row.get("model") and r.get("scenario") == row.get("scenario"))
    ]
    kept.append(row)
    order = {name: idx for idx, name in enumerate(vlm.SCENARIO_ORDER)}
    return sorted(kept, key=lambda r: order.get(str(r.get("scenario")), 999))


def _write_outputs(rows: List[Dict[str, Any]], controller: str, results_json: Path, report_md: Path, out_root: Path, *, smoke: bool) -> None:
    label = CONTROLLER_LABELS[controller]
    payload = {
        "run": {
            "controller": controller,
            "model": label,
            "town": "Town03",
            "fps": 20,
            "timeout_sec": 14,
            "staging_source": staging.STAGING_SOURCE,
            "output_root": str(out_root),
            "smoke": smoke,
            "query_period_s": rows[-1].get("query_period_s") if rows else None,
            "env": "openemma" if controller == "openemma" else "lmdrive-compatible",
        },
        "rows": rows,
        "per_tier_counts": {label: _per_tier_counts(rows, label)},
    }
    results_json.parent.mkdir(parents=True, exist_ok=True)
    with open(results_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    counts = payload["per_tier_counts"][label]
    lines = [
        f"# {label} Full-Planner {'Smoke' if smoke else 'Strict Sweep'}",
        "",
        f"- Shared staging source: `{staging.STAGING_SOURCE}`.",
        "- Episode settings: `fps=20`, `timeout_sec=14`; strict scoring is read from telemetry-grounded `strict_scoring`.",
        f"- Query period: `{payload['run']['query_period_s']}` seconds.",
        "",
        "## Per-Tier",
        "",
        "| Model | Low | Mid | High | Overall |",
        "| --- | ---: | ---: | ---: | ---: |",
        "| {model} | {low} | {mid} | {high} | {overall} |".format(
            model=label,
            low=_fmt_count(counts.get("low") or {}),
            mid=_fmt_count(counts.get("mid") or {}),
            high=_fmt_count(counts.get("high") or {}),
            overall=_fmt_count(counts.get("overall") or {}),
        ),
        "",
        "## Episodes",
        "",
        "| Scenario | Expected | Strict | Moved | Max speed km/h | INVALID | Trace rows | Sample frame |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        strict = row.get("strict_scoring") or {}
        metrics = row.get("metrics") or {}
        trace = row.get("trace") or {}
        lines.append(
            "| {scenario} | {expected} | {verdict} | {moved} | {speed} | {invalid} | {trace_rows} | `{frame}` |".format(
                scenario=row.get("scenario"),
                expected=row.get("expected"),
                verdict=strict.get("verdict"),
                moved="yes" if metrics.get("moved") else "no",
                speed=_fmt_float(metrics.get("max_speed_kmh")),
                invalid=1 if strict.get("invalid") is True else 0,
                trace_rows=trace.get("rows") or 0,
                frame=_rel((row.get("visibility_sample") or {}).get("front")),
            )
        )
    lines.append("")
    report_md.parent.mkdir(parents=True, exist_ok=True)
    with open(report_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _rel(path: Optional[str]) -> str:
    if not path:
        return "-"
    try:
        return str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _carla_town03_status(timeout_s: float = 5.0) -> tuple[bool, str]:
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


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", help="Scenario keys. Defaults to all 14, or green_stop for --smoke.")
    parser.add_argument("--controller", default="openemma", choices=sorted(CONTROLLER_LABELS))
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--results-json", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--query-period-s", type=float, default=3.0)
    parser.add_argument("--target-speed-kmh", type=float, default=25.0)
    parser.add_argument("--sensor-timeout-s", type=float, default=1.0)
    parser.add_argument("--max-new-tokens", type=int, default=96)
    parser.add_argument("--log-every-n", type=int, default=20)
    parser.add_argument("--save-debug-every-n", type=int, default=20)
    parser.add_argument("--max-debug-frames", type=int, default=4)
    parser.add_argument("--reuse-backend", action="store_true", default=True)
    parser.add_argument("--no-reuse-backend", action="store_false", dest="reuse_backend")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    controller = _controller_key(args.controller)
    scenarios = args.scenarios or (["green_stop"] if args.smoke else list(vlm.SCENARIO_ORDER))
    unknown = [s for s in scenarios if s not in vlm.SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenario(s): {', '.join(unknown)}")

    results_json = Path(args.results_json) if args.results_json else (
        SMOKE_RESULTS[controller] if args.smoke else DEFAULT_RESULTS[controller]
    )
    report_md = Path(args.report) if args.report else (
        SMOKE_REPORTS[controller] if args.smoke else DEFAULT_REPORTS[controller]
    )
    out_root = Path(args.out_root) if args.out_root else (
        SMOKE_OUT_ROOTS[controller] if args.smoke else DEFAULT_OUT_ROOTS[controller]
    )

    ok, status = _carla_town03_status()
    if not ok:
        raise SystemExit(f"{status}. Not loading maps.")
    setup_root_logger()
    carla = import_carla()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(120.0)
    out_root.mkdir(parents=True, exist_ok=True)

    rows = _load_rows(results_json)
    for scenario in scenarios:
        row = _run_one(client, args, controller, scenario, out_root, smoke=args.smoke)
        rows = _replace_row(rows, row)
        _write_outputs(rows, controller, results_json, report_md, out_root, smoke=args.smoke)

    _write_outputs(rows, controller, results_json, report_md, out_root, smoke=args.smoke)
    print(f"Wrote {results_json}")
    print(f"Wrote {report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
