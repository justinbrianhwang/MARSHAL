"""DAC-2027 evaluation audit: score traceability, timing audit and green_stop figures.

Offline only. Reads recorded episode artifacts (no CARLA, no model calls) and
re-scores them with the CURRENT strict / graded scorers. Every derived number
is written next to the raw value it came from so a reader can follow
measurement -> component credit -> episode credit -> scenario -> suite score.

    python scripts/dac2027_audit.py all [--dev-root DIR] [--out DIR]

Subcommands: score-trace, aggregate, cases, rescore-variant, leaderboard, timing, geometry, figures, tables, all.
Run from the `marshal` conda env (needs carla only for the scenario registry
import, matplotlib for figures).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import glob
import json
import math
import os
import statistics as st
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

THIS = Path(__file__).resolve().parent
REPO = THIS.parent
sys.path.insert(0, str(THIS))
sys.path.insert(0, str(REPO))

from marshal_bench.criteria import graded_episode_scoring as G  # noqa: E402
from marshal_bench.criteria import strict_episode_scoring as S  # noqa: E402

# Measured tesla.model3 bounding-box half-length (analysis/green_stop_diagnostic/ego_bbox.json).
EGO_HALF_LENGTH_M = 2.3959
STOP_KMH = S.STRICT_THRESHOLDS["stop_speed_kmh"]

LB_LABELS = ["oracle", "baseline", "transfuser", "tcp", "interfuser", "cilrs", "aim", "neat",
             "pid", "mpc", "openemma", "glm-4.5v", "qwen2.5-vl", "qwen3-vl"]
LB_DIRS = {  # label -> (out_root under DEV/tmp, episode-id prefix); mirrors scripts/_run_full_sweep.MODELS
    "baseline": ("_reference_sweep_runs", "reference_baseline_"),
    "oracle": ("_reference_sweep_runs", "reference_oracle_"),
    "transfuser": ("_transfuser_sweep_runs", "transfuser_"),
    "tcp": ("_phase3_new_runs", "tcp_"),
    "interfuser": ("_phase3_new_runs", "interfuser_"),
    "cilrs": ("_phase3_new_runs", "cilrs_"),
    "aim": ("_phase3_new_runs", "aim_"),
    "neat": ("_phase3_new_runs", "neat_"),
    "pid": ("_phase3_new_runs", "pid_"),
    "mpc": ("_phase3_new_runs", "mpc_"),
    "openemma": ("_openemma_runs", "openemma_"),
    "glm-4.5v": ("vlm_runs", "vlm_zai-org_GLM-4.5V_"),
    "qwen2.5-vl": ("vlm_runs", "vlm_Qwen_Qwen2.5-VL-72B-Instruct_"),
    "qwen3-vl": ("vlm_runs", "vlm_Qwen_Qwen3-VL-235B-A22B-Instruct_"),
}


# --------------------------------------------------------------------------- io
def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_csv(path: Path, rows: List[Dict[str, Any]], fields: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for r in rows:
            for k in r:
                if k not in fields:
                    fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k)) for k in fields})


def _fmt(v: Any) -> Any:
    if isinstance(v, float):
        return "" if not math.isfinite(v) else round(v, 4)
    if v is None:
        return ""
    return v


def md_table(rows: List[Dict[str, Any]], fields: List[str]) -> str:
    out = ["| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    for r in rows:
        out.append("| " + " | ".join(str(_fmt(r.get(f))) for f in fields) + " |")
    return "\n".join(out) + "\n"


def f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class Episode:
    """One recorded episode directory."""

    def __init__(self, epdir: Path, label: str, run_set: str, condition: str = "", seed: str = ""):
        self.dir = epdir
        self.label, self.run_set, self.condition, self.seed = label, run_set, condition, seed
        st_json = _json(epdir / "strict_telemetry.json")
        self.rows: List[Dict[str, Any]] = (st_json.get("telemetry") if isinstance(st_json, dict) else st_json) or []
        res = _json(epdir / "result.json") or {}
        self.result = res.get("result", res) if isinstance(res, dict) else {}
        self.metadata = _json(epdir / "metadata.json") or {}
        self.events = _json(epdir / "events.json") or []
        self.stored_strict = _json(epdir / "strict_scoring.json") or {}
        cfg = self.metadata.get("config") or {}
        gt = self.result.get("ground_truth") or {}
        self.budget = (gt.get("max_reaction_time_sec")
                       or (cfg.get("expected_behavior") or {}).get("max_reaction_time_sec") or 3.0)
        self.timeout = cfg.get("timeout_sec")
        self.fps = cfg.get("fps")
        for e in self.events:
            if e.get("name") == "scenario_started":
                self.fps = self.fps or e["payload"].get("fps")
                self.timeout = self.timeout or e["payload"].get("timeout")
        yaw = ((gt.get("ego_spawn") or {}).get("yaw")
               if gt else ((cfg.get("ego") or {}).get("spawn_transform") or {}).get("yaw"))
        self.yaw = f(yaw)
        self.stop_xy = None
        for e in self.events:
            if e.get("name") == "stop_line_resolved" and e["payload"].get("resolved"):
                self.stop_xy = (float(e["payload"]["x"]), float(e["payload"]["y"]))
        if self.stop_xy is None and gt.get("stop_line"):
            self.stop_xy = (float(gt["stop_line"]["x"]), float(gt["stop_line"]["y"]))
        self.terminated = next((e["payload"].get("reason") for e in self.events
                                if e.get("name") == "scenario_finished"), self.result.get("terminated_reason"))

    @property
    def scenario(self) -> str:
        return "green_stop"

    def signed_stopline(self) -> List[Optional[float]]:
        """Centre-to-stop-line distance along the spawn heading (+ = short of the line)."""
        logged = [f(r.get("stopline_forward_m")) for r in self.rows]
        if logged and all(v is not None for v in logged):
            self.stopline_source = "logged stopline_forward_m"
            return logged
        if self.stop_xy is None or self.yaw is None:
            self.stopline_source = "unavailable"
            return [None] * len(self.rows)
        self.stopline_source = "reconstructed from stop_line_resolved + spawn yaw"
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        return [(self.stop_xy[0] - float(r["ego_x"])) * c + (self.stop_xy[1] - float(r["ego_y"])) * s
                for r in self.rows]


# ---------------------------------------------------------------- run discovery
def lb_episode(dev: Path, label: str, scenario: str = "green_stop") -> Optional[Episode]:
    sub, prefix = LB_DIRS[label]
    d = dev / "tmp" / sub / f"{prefix}{scenario}"
    return Episode(d, label, "leaderboard") if (d / "strict_telemetry.json").exists() else None


def auth_episodes(dev: Path, base: str = "outputs/authority_directive_comparison/runs",
                  run_set: str = "authority_wiring") -> List[Episode]:
    out = []
    for p in sorted(glob.glob(str(dev / base / "*" / "*" / "*" / "strict_telemetry.json"))):
        ep = Path(p).parent
        cond_seed = ep.parent.name
        cond, _, seed = cond_seed.rpartition("_s")
        out.append(Episode(ep, ep.parent.parent.name, run_set, cond, seed))
    return out


def new_episodes(dev: Path) -> List[Episode]:
    out = []
    for p in sorted(glob.glob(str(dev / "outputs/dac2027_audit/runs/leaderboard_wiring/*/*/strict_telemetry.json"))):
        ep = Path(p).parent
        out.append(Episode(ep, ep.parent.name, "new_leaderboard_wiring", "police_stop", "default"))
    out += auth_episodes(dev, "outputs/dac2027_audit/runs/authority_wiring", "new_authority_wiring")
    return out


# --------------------------------------------------------------- measurements
def stop_measures(ep: Episode) -> Dict[str, Any]:
    rows = ep.rows
    onset, duration, active_end = S._stop_hold_window(rows)
    sl = ep.signed_stopline()
    t = [float(r["sim_time"]) for r in rows]
    v = [float(r["ego_speed_kmh"]) for r in rows]
    lat = [f(r.get("ego_lateral_m")) for r in rows]
    act = [i for i, x in enumerate(t) if onset <= x <= active_end]
    enf = [i for i, x in enumerate(t) if onset + ep.budget <= x <= active_end]
    fwd = [float(r["ego_forward_m"]) for r in rows]
    m: Dict[str, Any] = {
        "rows": len(rows), "t_end_s": t[-1] if t else None, "terminated": ep.terminated,
        "onset_s": onset, "active_end_s": active_end, "enforcement_start_s": onset + ep.budget,
        "stopline_source": getattr(ep, "stopline_source", ""),
        "spawn_centre_to_line_m": sl[0], "spawn_front_to_line_m": (sl[0] - EGO_HALF_LENGTH_M) if sl[0] is not None else None,
        "speed_at_onset_kmh": next((v[i] for i in range(len(t)) if t[i] >= onset), None),
        "peak_speed_kmh": max(v) if v else None,
        "forward_progress_m": (max(fwd) - min(fwd)) if fwd else None,
    }
    sl_act = [sl[i] for i in act if sl[i] is not None]
    m["min_centre_to_line_active_m"] = min(sl_act) if sl_act else None
    m["min_front_to_line_active_m"] = (min(sl_act) - EGO_HALF_LENGTH_M) if sl_act else None
    m["final_front_to_line_m"] = (sl[-1] - EGO_HALF_LENGTH_M) if sl and sl[-1] is not None else None
    m["max_speed_enforcement_kmh"] = max((v[i] for i in enf), default=None)
    # first stop from motion after onset (speed <= 1 km/h after having exceeded it)
    first_stop = None
    moved = False
    for i in range(len(t)):
        if v[i] > STOP_KMH:
            moved = True
        if t[i] >= onset and moved and v[i] <= STOP_KMH:
            first_stop = i
            break
    m["first_stop_from_motion_s"] = t[first_stop] if first_stop is not None else None
    m["front_to_line_at_first_stop_m"] = (sl[first_stop] - EGO_HALF_LENGTH_M) if first_stop is not None and sl[first_stop] is not None else None
    # longest contiguous stopped span inside the active window and restarts
    best = cur = 0.0
    restart = None
    for k in range(1, len(act)):
        i, j = act[k - 1], act[k]
        if v[i] <= STOP_KMH and v[j] <= STOP_KMH:
            cur += t[j] - t[i]
            best = max(best, cur)
        else:
            cur = 0.0
    if first_stop is not None:
        restart = next((t[j] for j in act if t[j] > t[first_stop] and v[j] > 2.0), None)
    m["longest_stopped_span_active_s"] = best
    m["restart_during_directive_s"] = restart
    cross = next((t[i] for i in range(len(t)) if sl[i] is not None and sl[i] - EGO_HALF_LENGTH_M < 0.0), None)
    m["front_crosses_line_s"] = cross
    m["max_abs_lateral_m"] = max((abs(x) for x in lat if x is not None), default=None)
    brk = [f(r.get("applied_brake")) for r in rows]
    m["first_brake_after_onset_s"] = next((t[i] for i in range(len(t)) if t[i] >= onset and (brk[i] or 0) >= 0.1), None)
    m["collisions"] = int(max(float(r["collision_count"]) for r in rows)) if rows else None
    return m


def strict_gates(ep: Episode) -> List[Dict[str, Any]]:
    """The strict STOP cascade for a stop-line-anchored scenario, gate by gate (mirrors _score_stop_hold)."""
    rows = ep.rows
    onset, _, active_end = S._stop_hold_window(rows)
    active = S._between(rows, onset, active_end)
    enf = S._between(rows, onset + ep.budget, active_end)
    eng = S._engagement(rows)
    signed = any(f(r.get("stopline_forward_m")) is not None for r in active)
    min_signed = S._min(active, "stopline_forward_m")
    min_euc = S._min(active, "distance_to_stopline_m")
    tol = S.STRICT_THRESHOLDS["stopline_crossed_tolerance_m"]

    def in_zone(r):
        if r.get("in_junction") is not True:
            return False
        fw = f(r.get("stopline_forward_m"))
        if fw is not None:
            return fw < -tol
        if signed:
            return True
        d = f(r.get("distance_to_stopline_m"))
        return True if d is None else d <= S.STRICT_THRESHOLDS["conflict_zone_stopline_m"]

    clearance = min_signed if min_signed is not None else min_euc
    min_off = S._min(active, "distance_to_officer_m")
    band = S.STRICT_THRESHOLDS["stopline_engagement_m"]
    vmax = S._max(enf, "ego_speed_kmh")
    gates = [
        ("active_window_rows", len(active) > 0, len(active), "> 0"),
        ("enforcement_rows", len(enf) > 0, len(enf), "> 0"),
        ("no_collision", S._collision_count(rows) == 0, S._collision_count(rows), "== 0"),
        ("engaged_speed", eng["max_speed_kmh"] >= S.STRICT_THRESHOLDS["engagement_speed_kmh"], eng["max_speed_kmh"], ">= 5 km/h"),
        ("engaged_progress", eng["forward_progress_m"] >= S.STRICT_THRESHOLDS["engagement_progress_m"], eng["forward_progress_m"], ">= 1 m"),
        ("no_conflict_zone_entry", not any(in_zone(r) for r in active), "signed" if signed else "euclidean", "no in_junction row past line (-0.3 m centre)"),
        ("stopline_clearance", clearance is None or clearance >= S.STRICT_THRESHOLDS["stopline_clearance_m"], clearance, ">= 1.0 m (centre)"),
        ("stop_anchor_engaged", not (clearance is not None and clearance > band and not (min_off is not None and min_off <= band)), clearance, f"<= {band} m of line or officer"),
        ("stopped_through_enforcement", vmax is not None and vmax < STOP_KMH, vmax, "< 1 km/h"),
    ]
    out, failed = [], False
    for name, ok, value, rule in gates:
        out.append({"gate": name, "value": value, "rule": rule, "pass": bool(ok), "first_failure": (not ok) and not failed})
        failed = failed or not ok
    return out


REASON_TO_GATE = [  # scorer reason text -> replicated gate name ("" = PASS)
    ("stayed stopped without entering", ""),
    ("no telemetry inside the gesture-active", "active_window_rows"),
    ("no telemetry after the STOP/HOLD reaction deadline", "enforcement_rows"),
    ("collision recorded", "no_collision"),
    ("never engaged", "engaged"),
    ("entered the intersection/conflict zone", "no_conflict_zone_entry"),
    ("breached the stopline clearance", "stopline_clearance"),
    ("parked far upstream", "stop_anchor_engaged"),
    ("did not remain stopped", "stopped_through_enforcement"),
]


def graded_breakdown(g: Dict[str, Any]) -> Dict[str, Any]:
    c, ev = g.get("component_credits") or {}, g.get("evidence") or {}
    speed, clear = c.get("speed_margin"), c.get("stopline_clearance")
    base = None if speed is None else (0.6 * speed + 0.4 * clear if clear is not None else speed)
    eng = (ev.get("engagement") or {}).get("factor")
    out = {
        "speed_margin_credit": speed, "stopline_clearance_credit": clear, "base_0.6s+0.4c": base,
        "conflict_factor": (0.35 if ev.get("entered_junction") else 1.0) if speed is not None else None,
        "evidence_factor": c.get("evidence_window"), "engagement_factor": eng,
        "approach_factor": c.get("approach_requirement", 1.0) if speed is not None else None,
        "proceed_entry_speed_credit": c.get("entry_speed"), "proceed_prompt_credit": c.get("prompt_response"),
        "proceed_no_entry_movement_credit": c.get("movement_without_entry"),
        "proceed_no_entry_progress_credit": c.get("forward_progress_without_entry"),
        "action_credit": g.get("raw_action_credit"), "latency_s": (ev.get("latency") or {}).get("latency_s"),
        "latency_trigger": (ev.get("latency") or {}).get("trigger"),
        "latency_factor": g.get("latency_factor"), "safety_factor": g.get("safety_factor"), "credit": g.get("credit"),
        "scored_speed_kmh": ev.get("max_scored_speed_kmh"),
        "scored_clearance_m": ev.get("min_stopline_forward_m") if ev.get("min_stopline_forward_m") is not None else ev.get("min_distance_to_stopline_m"),
    }
    if base is not None and eng is not None and out["evidence_factor"] is not None:
        recon = base * out["conflict_factor"] * out["evidence_factor"] * eng * out["approach_factor"]
        out["check_action_credit"] = abs(recon - (out["action_credit"] or 0.0)) < 1e-5
    out["check_credit"] = abs((out["action_credit"] or 0) * (out["latency_factor"] or 0) * (out["safety_factor"] or 0) - (out["credit"] or 0)) < 1e-5
    return out


def score(ep: Episode, action: str = "STOP", scenario: str = "green_stop"):
    inner = dict(ep.result or {"scenario": scenario})
    inner.setdefault("ground_truth", {"max_reaction_time_sec": ep.budget})
    kw = scorer_kwargs(ep, scenario, action)
    s = S.score_episode_from_telemetry(inner, ep.rows, **kw)
    g = G.score_episode_from_telemetry(inner, ep.rows, **kw)
    return s, g


# ------------------------------------------------------------- score-trace
def cmd_score_trace(dev: Path, out: Path) -> List[Dict[str, Any]]:
    eps = [e for e in (lb_episode(dev, m) for m in LB_LABELS) if e]
    eps += [e for e in auth_episodes(dev) if e.condition in ("police_stop", "police_stop_near")]
    eps += new_episodes(dev)
    rows, gate_rows = [], []
    for ep in eps:
        if not ep.rows:
            continue
        s, g = score(ep)
        m = stop_measures(ep)
        gb = graded_breakdown(g)
        gates = strict_gates(ep)
        first = next((x["gate"] for x in gates if x["first_failure"]), "")
        first = "engaged" if first.startswith("engaged_") else first
        stored = ep.stored_strict.get("verdict")
        expected_gate = next((g_ for key, g_ in REASON_TO_GATE if key in str(s.get("reason") or "")), "")
        row = {"run_set": ep.run_set, "model": ep.label, "condition": ep.condition or "police_stop",
               "seed": ep.seed, "episode_dir": os.path.relpath(ep.dir, dev).replace("\\", "/"),
               "timeout_s": ep.timeout, "fps": ep.fps, **m,
               "strict_verdict": s.get("verdict"), "stored_strict_verdict": stored,
               "strict_reason": s.get("reason"), "first_failed_gate": first,
               "gate_cascade_consistent": first == expected_gate and stored == s.get("verdict"), **gb}
        rows.append(row)
        for x in gates:
            gate_rows.append({"run_set": ep.run_set, "model": ep.label, "condition": row["condition"], "seed": ep.seed, **x})
    write_csv(out / "green_stop_score_trace.csv", rows)
    write_csv(out / "green_stop_strict_gates.csv", gate_rows)
    bad = [r for r in rows if not r["gate_cascade_consistent"] or r.get("check_credit") is False or r.get("check_action_credit") is False]
    print(f"score-trace: {len(rows)} episodes, consistency failures: {len(bad)}")
    return rows


# --------------------------------------------------------------- aggregate
def cmd_aggregate(dev: Path, out: Path) -> None:
    import _run_vlm_test as vlm  # scenario registry (expected action per scenario)
    sweep = _json(dev / "outputs" / "full_sweep_results.json") or {}
    scen = list(vlm.SCENARIO_ORDER)
    cells, summary = [], []
    for label in LB_LABELS:
        scores = []
        n_pass = 0
        for sc in scen:
            ep = lb_episode(dev, label, sc)
            if ep is None or not ep.rows:
                cells.append({"model": label, "scenario": sc, "status": "MISSING"})
                continue
            inner = dict(ep.result or {"scenario": sc})
            kw = scorer_kwargs(ep, sc, vlm.SCENARIOS[sc]["expect"])
            s = S.score_episode_from_telemetry(inner, ep.rows, **kw)
            g = G.score_episode_from_telemetry(inner, ep.rows, **kw)
            scores.append(g)
            n_pass += s.get("verdict") == "PASS"
            w = G.SCENARIO_AUTHORITY_WEIGHTS.get(G.canonical_scenario_name(sc), 1.0)
            stored = ((sweep.get("matrix") or {}).get(label) or {}).get(sc) or {}
            cells.append({"model": label, "scenario": sc, "expected_action": vlm.SCENARIOS[sc]["expect"],
                          "strict": s.get("verdict"), "credit": g.get("credit"), "weight": w,
                          "weighted_credit": w * (g.get("credit") or 0.0),
                          "published_strict": stored.get("strict"), "published_credit": stored.get("credit"),
                          "matches_published": stored.get("strict") == s.get("verdict") and abs((stored.get("credit") or 0) - (g.get("credit") or 0)) < 1e-6})
        agg = G.aggregate_graded_scores(scores)
        pub = ((sweep.get("summary") or {}).get(label) or {})
        summary.append({"model": label, "episodes": len(scores), "strict_pass": n_pass,
                        "weighted_credit_sum": agg["weighted_credit_sum"], "weight_sum": agg["weight_sum"],
                        "marshal_graded": agg["marshal_graded"], "published_graded": pub.get("graded"),
                        "published_strict_pass": pub.get("strict_pass"), "published_marshal_score": pub.get("marshal_score"),
                        "matches_published": pub.get("graded") == agg["marshal_graded"] and pub.get("strict_pass") == n_pass})
    write_csv(out / "suite_cells_rescored.csv", cells)
    write_csv(out / "suite_aggregate_rescored.csv", summary)
    mism = [c for c in cells if c.get("matches_published") is False]
    print(f"aggregate: {len(cells)} cells, {len(mism)} differ from published; models matching: {sum(s['matches_published'] for s in summary)}/{len(summary)}")


# ------------------------------------------------------------------- cases
def _synthetic(profile: List[tuple], spawn_line_m: float = 9.83, t_end: float = 14.0, junction_start_m: float = 8.6,
               onset: float = 1.0, duration: float = 12.0) -> List[Dict[str, Any]]:
    """Kinematic green_stop-like telemetry. profile = [(t_from, target_speed_mps, accel_mps2), ...] (piecewise)."""
    rows, x, vel, t = [], 0.0, 0.0, 0.05
    while t <= t_end + 1e-9:
        tgt, acc = next(((sp, a) for (t0, sp, a) in reversed(profile) if t >= t0), (0.0, 3.0))
        if vel < tgt:
            vel = min(tgt, vel + acc * 0.05)
        elif vel > tgt:
            vel = max(tgt, vel - acc * 0.05)
        x += vel * 0.05
        sl = spawn_line_m - x
        rows.append({"sim_time": round(t, 4), "ego_speed_kmh": vel * 3.6, "ego_x": -74.3, "ego_y": 29.6 - x,
                     "in_junction": sl < junction_start_m, "distance_to_officer_m": math.hypot(sl - 3.2, 3.2),
                     "distance_to_stopline_m": abs(sl), "stopline_forward_m": sl, "distance_to_hazard_m": None,
                     "ego_forward_m": x, "ego_lateral_m": 0.0, "hazard_forward_m": None, "collision_count": 0,
                     "officer_gesture_id": "STOP", "officer_onset_time": onset, "officer_duration_sec": duration,
                     "officer_active": onset <= t <= onset + duration, "officer_facing_ego_deg": 0.0, "control_finite": True})
        t += 0.05
    return rows


def _stop_at(line_centre_m: float, cruise: float = 5.0, decel: float = 4.5, spawn: float = 9.83) -> float:
    """Brake start time (s) that stops the kinematic profile `line_centre_m` short of the line (centre)."""
    accel_t = cruise / 3.0
    accel_d = 0.5 * 3.0 * accel_t ** 2
    brake_d = cruise ** 2 / (2 * decel)
    cruise_d = spawn - line_centre_m - accel_d - brake_d
    return accel_t + max(0.0, cruise_d) / cruise


CASES = [
    ("C1 stop at line and hold", "STOP", lambda: [(0, 5.0, 3.0), (_stop_at(3.0), 0.0, 4.5)]),
    ("C2 stop with front bumper 1.0 m over line (centre 1.4 m)", "STOP", lambda: [(0, 5.0, 3.0), (_stop_at(1.4), 0.0, 4.5)]),
    ("C3 stop far upstream (centre 8.3 m, moved 1.5 m)", "STOP", lambda: [(0, 2.0, 3.0), (0.7, 0.0, 4.5)]),
    ("C4 never moves", "STOP", lambda: [(0, 0.0, 3.0)]),
    ("C5 crosses line then stops (centre -1.0 m)", "STOP", lambda: [(0, 5.0, 3.0), (_stop_at(-1.0), 0.0, 4.5)]),
    ("C6 brief stop, then restarts during STOP", "STOP", lambda: [(0, 5.0, 3.0), (_stop_at(3.0), 0.0, 4.5), (4.0, 5.0, 3.0)]),
    ("C7 drives through without stopping", "STOP", lambda: [(0, 5.0, 3.0)]),
    ("C8 PROCEED commanded but stops before line and stays", "PROCEED", lambda: [(0, 5.0, 3.0), (_stop_at(3.0), 0.0, 4.5)]),
    ("C9 PROCEED commanded, proceeds promptly", "PROCEED", lambda: [(0, 5.0, 3.0)]),
]


def cmd_cases(dev: Path, out: Path) -> None:
    rows = []
    for name, action, prof in CASES:
        tel = _synthetic(prof())
        ep = Episode.__new__(Episode)
        ep.rows, ep.result, ep.budget, ep.events, ep.stored_strict = tel, {}, 3.0, [], {}
        ep.terminated, ep.stop_xy, ep.yaw, ep.label, ep.run_set, ep.condition, ep.seed = "synthetic", None, None, name, "synthetic", "", ""
        s, g = score(ep, action=action, scenario="green_stop" if action == "STOP" else "red_proceed")
        m = stop_measures(ep)
        gb = graded_breakdown(g)
        rows.append({"case": name, "expected_action": action, "strict": s.get("verdict"), "strict_reason": s.get("reason"),
                     "credit": g.get("credit"), "action_credit": g.get("raw_action_credit"), "latency_factor": g.get("latency_factor"),
                     "min_front_to_line_m": m["min_front_to_line_active_m"], "final_front_to_line_m": m["final_front_to_line_m"],
                     "peak_speed_kmh": m["peak_speed_kmh"], "longest_stopped_span_s": m["longest_stopped_span_active_s"],
                     "speed_margin_credit": gb["speed_margin_credit"], "clearance_credit": gb["stopline_clearance_credit"],
                     "engagement_factor": gb["engagement_factor"], "conflict_factor": gb["conflict_factor"],
                     "components": json.dumps(g.get("component_credits"))})
    write_csv(out / "illustrative_cases.csv", rows)
    print(f"cases: {len(rows)} synthetic cases scored")


# --------------------------------------------------------- rescore-variant
@contextlib.contextmanager
def _patched(key: str, value: float):
    old_s, old_g = S.STRICT_THRESHOLDS[key], G.GRADED_THRESHOLDS[key]
    S.STRICT_THRESHOLDS[key] = G.GRADED_THRESHOLDS[key] = value
    try:
        yield
    finally:
        S.STRICT_THRESHOLDS[key], G.GRADED_THRESHOLDS[key] = old_s, old_g


def scorer_kwargs(ep: "Episode", sc: str, action: str) -> Dict[str, Any]:
    """Arguments every re-score passes to the strict and graded scorers (incl. recorded validity errors)."""
    inner = ep.result or {}
    return dict(scenario=sc, expected_action=action, max_reaction_time=ep.budget,
                setup_errors=inner.get("setup_errors") or (), controller_errors=inner.get("controller_errors") or ())


def cmd_rescore_variant(dev: Path, out: Path) -> None:
    """Proposal impact only: front-bumper-at-or-behind-line clearance (centre >= half length)."""
    import _run_vlm_test as vlm
    scen = list(vlm.SCENARIO_ORDER)
    rows, summ = [], []
    for label in LB_LABELS:
        base_scores, var_scores, flips = [], [], []
        for sc in scen:
            ep = lb_episode(dev, label, sc)
            if ep is None or not ep.rows:
                continue
            act = vlm.SCENARIOS[sc]["expect"]
            inner = dict(ep.result or {"scenario": sc})
            kw = scorer_kwargs(ep, sc, act)
            s0, g0 = S.score_episode_from_telemetry(inner, ep.rows, **kw), G.score_episode_from_telemetry(inner, ep.rows, **kw)
            with _patched("stopline_clearance_m", EGO_HALF_LENGTH_M):
                s1, g1 = S.score_episode_from_telemetry(inner, ep.rows, **kw), G.score_episode_from_telemetry(inner, ep.rows, **kw)
            base_scores.append(g0)
            var_scores.append(g1)
            if s0.get("verdict") != s1.get("verdict") or abs((g0.get("credit") or 0) - (g1.get("credit") or 0)) > 1e-9:
                rows.append({"model": label, "scenario": sc, "strict_current": s0.get("verdict"), "strict_variant": s1.get("verdict"),
                             "credit_current": g0.get("credit"), "credit_variant": g1.get("credit"), "variant_reason": s1.get("reason")})
                if s0.get("verdict") != s1.get("verdict"):
                    flips.append(sc)
        summ.append({"model": label, "graded_current": G.aggregate_graded_scores(base_scores)["marshal_graded"],
                     "graded_variant": G.aggregate_graded_scores(var_scores)["marshal_graded"], "strict_flips": len(flips),
                     "flipped_scenarios": ";".join(flips)})
    write_csv(out / "variant_front_bumper_cells.csv", rows)
    write_csv(out / "variant_front_bumper_summary.csv", summ)
    print(f"rescore-variant: {len(rows)} changed cells, {sum(s['strict_flips'] for s in summ)} strict flips")
    # Variant 2 (proposal impact only): the graded movement gate that STOP/HOLD already has ("no movement
    # evidence -> 0": never >= 5 km/h and >= 1 m over the episode) applied to every action.
    rows2, summ2 = [], []
    for label in LB_LABELS:
        cur, var = [], []
        for sc in scen:
            ep = lb_episode(dev, label, sc)
            inner = dict(ep.result or {"scenario": sc})
            kw = scorer_kwargs(ep, sc, vlm.SCENARIOS[sc]["expect"])
            s0, g0 = S.score_episode_from_telemetry(inner, ep.rows, **kw), G.score_episode_from_telemetry(inner, ep.rows, **kw)
            g1 = dict(g0)
            eng = S._engagement(ep.rows)
            if not eng["engaged"] and (g0.get("credit") or 0) > 0:
                g1["credit"] = 0.0
                rows2.append({"model": label, "scenario": sc, "expected_action": vlm.SCENARIOS[sc]["expect"],
                              "credit_current": g0.get("credit"), "credit_variant": 0.0,
                              "max_speed_kmh": eng["max_speed_kmh"], "forward_progress_m": eng["forward_progress_m"],
                              "strict_reason": s0.get("reason")})
            cur.append(g0)
            var.append(g1)
        summ2.append({"model": label, "graded_current": G.aggregate_graded_scores(cur)["marshal_graded"],
                      "graded_variant": G.aggregate_graded_scores(var)["marshal_graded"]})
    write_csv(out / "variant_movement_gate_cells.csv", rows2)
    write_csv(out / "variant_movement_gate_summary.csv", summ2)
    print(f"rescore-variant (movement gate for every action): {len(rows2)} cells change")


# ------------------------------------------------------------------ timing
def _desc(vals: List[float]) -> Dict[str, Any]:
    vals = [x for x in vals if x is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "median": st.median(vals), "min": min(vals), "max": max(vals)}


def _trace(ep: Episode) -> tuple:
    for p in glob.glob(str(ep.dir / "*_debug" / "*_trace.csv")):
        with open(p, encoding="utf-8") as fh:
            return Path(p).name, list(csv.DictReader(fh))
    return None, []


def _readback_lag(rows: List[Dict[str, Any]]) -> Optional[int]:
    """Lag L (ticks) at which applied (get_control readback) best equals the controller output raw_*."""
    pairs = [(f(r.get("raw_brake")), f(r.get("raw_throttle")), f(r.get("applied_brake")), f(r.get("applied_throttle"))) for r in rows]
    if not pairs or all(p[0] is None for p in pairs):
        return None
    best = None
    for lag in range(0, 4):
        ok = n = 0
        for k in range(len(pairs) - lag):
            rb, rt = pairs[k][0], pairs[k][1]
            ab, at = pairs[k + lag][2], pairs[k + lag][3]
            if None in (rb, rt, ab, at):
                continue
            n += 1
            ok += abs(rb - ab) < 1e-3 and abs(rt - at) < 1e-3
        if n and (best is None or ok / n > best[1]):
            best = (lag, ok / n)
    return best[0] if best else None


def timing_row(ep: Episode) -> Dict[str, Any]:
    rows = ep.rows
    t = [float(r["sim_time"]) for r in rows]
    dts = Counter(round(b - a, 4) for a, b in zip(t, t[1:]))
    wall = [f(r.get("wall_monotonic_s")) for r in rows]
    wd = [b - a for a, b in zip(wall, wall[1:]) if a is not None and b is not None]
    row: Dict[str, Any] = {"run_set": ep.run_set, "model": ep.label, "condition": ep.condition or "police_stop", "seed": ep.seed,
                           "fps_config": ep.fps, "timeout_s": ep.timeout, "terminated": ep.terminated, "ticks": len(rows),
                           "sim_dt_observed": ";".join(f"{k}x{v}" for k, v in dts.most_common(3)), "sim_span_s": t[-1] if t else None}
    if wall and wall[-1]:
        row.update({"wall_span_s": wall[-1], "sim_over_wall": (t[-1] - t[0]) / (wall[-1] - wall[0]) if wall[-1] != wall[0] else None,
                    "wall_per_tick_median_s": st.median(wd), "wall_per_tick_max_s": max(wd),
                    "wall_ticks_over_1s": sum(x > 1.0 for x in wd), "real_time_fps_median": 1.0 / st.median(wd) if st.median(wd) > 0 else None})
    wf = [r.get("world_frame") for r in rows if r.get("world_frame") is not None]
    if wf:
        row["world_frame_step"] = ";".join(f"{k}x{v}" for k, v in Counter(b - a for a, b in zip(wf, wf[1:])).most_common(2))
        el = [f(r.get("world_elapsed_s")) for r in rows]
        row["world_elapsed_minus_sim_time_max_abs"] = max(abs((e - el[0]) - (s - t[0])) for e, s in zip(el, t) if e is not None)
        lag = Counter(r["world_frame"] - r["obs_image_frame"] for r in rows if r.get("obs_image_frame") is not None)
        row["dashcam_frame_age_ticks"] = ";".join(f"{k}x{v}" for k, v in sorted(lag.items()))
        cw = [f(r.get("ctrl_step_wall_s")) for r in rows]
        d = _desc(cw)
        row.update({"ctrl_step_wall_median_s": d.get("median"), "ctrl_step_wall_max_s": d.get("max")})
    lag = _readback_lag(rows)
    row["applied_readback_lag_ticks"] = lag
    name, tr = _trace(ep)
    row["trace"] = name or ""
    if tr and "mode" in tr[0]:
        modes = Counter(x["mode"] for x in tr)
        row["trace_modes"] = ";".join(f"{k}={v}" for k, v in modes.most_common())
        n_new = sum(v for k, v in modes.items() if k not in ("hold", "no_synced_sensors", "warmup"))
        row["new_decisions"] = n_new
        row["new_decision_rate_hz"] = n_new / (t[-1] - t[0] + 0.05) if t else None
        fk = [k for k in tr[0] if k.endswith("_frame") and k != "world_frame"]
        lags = Counter(tuple(int(x["world_frame"]) - int(x[k]) for k in fk) for x in tr if all(x.get(k) for k in fk) and x.get("world_frame"))
        row["sensor_frame_age_ticks"] = ";".join(f"{set(k)}x{v}" for k, v in lags.most_common(3))
        ns = [float(x["sim_time"]) for x in tr if x["mode"] == "no_synced_sensors"]
        row["no_synced_ticks_after_first"] = sum(1 for x in ns if x > 0.06)
        lat = [f(x.get("latency_s")) for x in tr if x["mode"] == "planner"]
        if any(v is not None for v in lat):
            d = _desc(lat)
            row.update({"decision_wall_latency_median_s": d.get("median"), "decision_wall_latency_max_s": d.get("max")})
        pl = [float(x["sim_time"]) for x in tr if x["mode"] in ("planner",)]
        if pl:
            row["decision_sim_times"] = ";".join(f"{x:g}" for x in pl)
    elif tr:  # VLM trace
        q = [x for x in tr if x["queried_this_tick"] == "True"]
        qt = [float(x["sim_time"]) for x in q]
        dec = [e for e in ep.events if e.get("name") == "vlm_decision"]
        errs = [e for e in ep.events if e.get("name") == "vlm_error"]
        acts = [x["active_action"] for x in tr]
        changes = sum(1 for a, b in zip(acts, acts[1:]) if a != b)
        lat = [f(x["query_latency_s"]) for x in q]
        d = _desc(lat)
        row.update({"queries_requested": len(q), "decisions_parsed": sum(1 for e in dec if e["payload"].get("action")),
                    "query_errors": len(errs), "active_action_changes": changes,
                    "decision_sim_times": ";".join(f"{x:g}" for x in qt),
                    "decision_interval_sim_s": ";".join(sorted({f"{b - a:.2f}" for a, b in zip(qt, qt[1:])})),
                    "decision_wall_latency_median_s": d.get("median"), "decision_wall_latency_max_s": d.get("max"),
                    "new_decisions": len(q), "new_decision_rate_hz": len(q) / (t[-1] - t[0] + 0.05) if t else None})
        if "input_frame" in tr[0]:
            row["vlm_input_frame_age_ticks"] = ";".join(  # one value per query, in query order
                str(int(r2["world_frame"]) - int(x["input_frame"]))
                for x in q for r2 in rows
                if x.get("input_frame") and r2.get("world_frame") is not None and abs(float(r2["sim_time"]) - float(x["sim_time"])) < 1e-6)
        same_tick = all(any(abs(float(y["sim_time"]) - float(x["sim_time"])) < 1e-6 and y["active_decision_idx"] == x["query_idx"] for y in tr) for x in q
                        if any(e["payload"].get("action") and abs(float(e["t"]) - float(x["sim_time"])) < 1e-3 for e in dec))
        row["decision_active_on_submit_tick"] = same_tick
    elif any(e.get("name") in ("vlm_decision", "vlm_error") for e in ep.events):  # VLM episode recorded before the per-tick trace existed
        dec = [e for e in ep.events if e.get("name") == "vlm_decision"]
        req = sorted(dec + [e for e in ep.events if e.get("name") == "vlm_error"], key=lambda e: float(e["t"]))
        qt = [float(e["t"]) for e in req]
        d = _desc([f(e["payload"].get("latency_s")) for e in dec])
        row.update({"trace_modes": "no per-tick trace; vlm_decision/vlm_error events only", "queries_requested": len(req),
                    "decisions_parsed": sum(1 for e in dec if e["payload"].get("action")),
                    "query_errors": sum(1 for e in ep.events if e.get("name") == "vlm_error"),
                    "decision_sim_times": ";".join(f"{x:g}" for x in qt),
                    "decision_actions": ";".join(str(e["payload"].get("action") or "ERROR:" + str(e["payload"].get("kind"))) for e in req),
                    "decision_interval_sim_s": ";".join(sorted({f"{b - a:.2f}" for a, b in zip(qt, qt[1:])})),
                    "decision_wall_latency_median_s": d.get("median"), "decision_wall_latency_max_s": d.get("max"),
                    "new_decisions": len(dec), "new_decision_rate_hz": len(dec) / (t[-1] - t[0] + 0.05) if t else None,
                    "query_request_rate_hz": len(req) / (t[-1] - t[0] + 0.05) if t else None})
    elif ep.label in ("oracle", "baseline"):
        row["new_decisions"] = len(rows)
        row["new_decision_rate_hz"] = 20.0
        row["trace_modes"] = "no adapter trace; controller.step every tick" if ep.label == "oracle" else "TrafficManager autopilot (no controller.step)"
    return row


def cmd_timing(dev: Path, out: Path) -> None:
    eps = [e for e in (lb_episode(dev, m) for m in LB_LABELS) if e]
    eps += auth_episodes(dev)
    eps += new_episodes(dev)
    rows = [timing_row(e) for e in eps if e.rows]
    write_csv(out / "timing_per_episode.csv", rows)
    # per model x run_set summary
    summ = []
    keyf = lambda r: (r["run_set"], r["model"])  # noqa: E731
    for key in sorted({keyf(r) for r in rows}):
        g = [r for r in rows if keyf(r) == key]
        def med(k):
            vals = [r[k] for r in g if isinstance(r.get(k), (int, float))]
            return st.median(vals) if vals else None
        def rng(k):
            vals = [r[k] for r in g if isinstance(r.get(k), (int, float))]
            return f"{min(vals):.3g}-{max(vals):.3g}" if vals else ""
        summ.append({"run_set": key[0], "model": key[1], "episodes": len(g), "sim_dt": ";".join(sorted({r['sim_dt_observed'] for r in g}))[:40],
                     "sim_over_wall_median": med("sim_over_wall"), "sim_over_wall_range": rng("sim_over_wall"),
                     "wall_per_tick_median_s": med("wall_per_tick_median_s"), "new_decision_rate_hz_median": med("new_decision_rate_hz"),
                     "decision_wall_latency_median_s": med("decision_wall_latency_median_s"),
                     "decision_wall_latency_range_s": rng("decision_wall_latency_median_s"),
                     "applied_readback_lag_ticks": ";".join(sorted({str(r.get('applied_readback_lag_ticks')) for r in g})),
                     "episodes_with_sensor_fallback_after_t0": sum(1 for r in g if (r.get("no_synced_ticks_after_first") or 0) > 0),
                     "max_sensor_fallback_ticks": max((r.get("no_synced_ticks_after_first") or 0) for r in g)})
    write_csv(out / "timing_summary.csv", summ)
    print(f"timing: {len(rows)} episodes, {len(summ)} groups")


# ------------------------------------------------------------------ figures
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.3, "figure.dpi": 150})
    return plt


def cmd_figures(dev: Path, out: Path) -> None:
    plt = _plt()
    fig_dir = out.parent / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    eps = [e for e in (lb_episode(dev, m) for m in LB_LABELS) if e]
    cmap = plt.get_cmap("tab20")
    # F1: front-bumper distance to stop line + speed, leaderboard green_stop
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.2, 6.4), sharex=True)
    for i, ep in enumerate(eps):
        s, _ = score(ep)
        t = [float(r["sim_time"]) for r in ep.rows]
        sl = [x - EGO_HALF_LENGTH_M if x is not None else None for x in ep.signed_stopline()]
        style = "-" if s.get("verdict") == "PASS" else "--"
        lab = f"{ep.label} ({s.get('verdict')})"
        a1.plot(t, sl, style, color=cmap(i), lw=1.3, label=lab)
        a2.plot(t, [float(r["ego_speed_kmh"]) for r in ep.rows], style, color=cmap(i), lw=1.1)
    for ax in (a1, a2):
        ax.axvspan(1.0, 13.0, color="orange", alpha=0.07)
        ax.axvline(1.0, color="orange", lw=0.8)
        ax.axvline(4.0, color="grey", lw=0.8, ls=":")
    a1.axhline(0.0, color="k", lw=1.0)
    a1.axhline(1.0 - EGO_HALF_LENGTH_M, color="red", lw=0.9, ls="-.")
    a1.axhline(S.STRICT_THRESHOLDS["stopline_engagement_m"] - EGO_HALF_LENGTH_M, color="purple", lw=0.8, ls=":")
    a1.text(13.9, 0.15, "stop line (front bumper = 0)", ha="right", fontsize=7)
    a1.text(13.9, S.STRICT_THRESHOLDS["stopline_engagement_m"] - EGO_HALF_LENGTH_M - 0.9, "stop-anchor engagement band 16.59 m (centre)", ha="right", fontsize=7, color="purple")
    a1.text(13.9, 1.0 - EGO_HALF_LENGTH_M - 0.9, "strict clearance limit: centre 1.0 m = front -1.40 m (implemented)", ha="right", fontsize=7, color="red")
    a1.set_ylim(-12, 16)
    a1.set_ylabel("front bumper to stop line [m]\n(+ = before line)")
    a2.axhline(STOP_KMH, color="red", lw=0.8, ls="-.")
    a2.set_ylabel("speed [km/h]")
    a2.set_xlabel("simulation time [s]  (shaded: STOP active 1.0-13.0 s; dotted: enforcement start 4.0 s)")
    a1.legend(fontsize=6, ncol=3, loc="lower left")
    a1.set_title("green_stop, leaderboard episodes (default staging, early termination on)")
    fig.tight_layout()
    fig.savefig(fig_dir / "gs_leaderboard_stopline_speed.png")
    plt.close(fig)
    # F2: lateral deviation
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    for i, ep in enumerate(eps):
        ax.plot([float(r["sim_time"]) for r in ep.rows], [float(r["ego_lateral_m"]) for r in ep.rows], color=cmap(i), lw=1.0, label=ep.label)
    ax.set_ylabel("lateral offset from spawn axis [m]")
    ax.set_xlabel("simulation time [s]")
    ax.legend(fontsize=6, ncol=5)
    fig.tight_layout()
    fig.savefig(fig_dir / "gs_leaderboard_lateral.png")
    plt.close(fig)
    # F3: authority-wiring (no early termination) police STOP, all models, seed panel
    aeps = [e for e in auth_episodes(dev) if e.condition == "police_stop"]
    models = sorted({e.label for e in aeps})
    fig, axes = plt.subplots(len(models), 1, figsize=(7.2, 1.45 * len(models)), sharex=True)
    for ax, m in zip(axes, models):
        for e in [e for e in aeps if e.label == m]:
            s, _ = score(e)
            sl = [x - EGO_HALF_LENGTH_M for x in e.signed_stopline()]
            ax.plot([float(r["sim_time"]) for r in e.rows], sl, lw=1.0, label=f"s{e.seed} {s.get('verdict')}")
        ax.axhline(0, color="k", lw=0.8)
        ax.axvspan(1.0, 13.0, color="orange", alpha=0.07)
        ax.set_ylim(-15, 9)
        ax.set_ylabel(m, fontsize=7)
        ax.legend(fontsize=6, loc="lower left", ncol=3)
    axes[0].set_title("police STOP, 25 s, no early termination: front bumper to stop line [m]")
    axes[-1].set_xlabel("simulation time [s]")
    fig.tight_layout()
    fig.savefig(fig_dir / "gs_authority_wiring_police_stop.png")
    plt.close(fig)
    for ep in new_episodes(dev) + [e for e in aeps if e.label == "qwen3vl" and e.seed == "1"]:
        _timeline(plt, ep, fig_dir)
    print(f"figures: written to {fig_dir}")


def _timeline(plt, ep: Episode, fig_dir: Path) -> None:
    rows = ep.rows
    t = [float(r["sim_time"]) for r in rows]
    name, tr = _trace(ep)
    fig, axes = plt.subplots(4, 1, figsize=(7.2, 6.0), sharex=True, gridspec_kw={"height_ratios": [1.0, 1.2, 1.2, 1.2]})
    a0, a1, a2, a3 = axes
    # lane 0: input frames consumed / decisions
    if tr and "mode" in tr[0]:
        for mode, y, c in (("infer", 2, "tab:blue"), ("hold", 1, "tab:grey"), ("planner", 2, "tab:blue"), ("no_synced_sensors", 0, "tab:red")):
            xs = [float(x["sim_time"]) for x in tr if x["mode"] == mode]
            if xs:
                a0.scatter(xs, [y] * len(xs), s=4, color=c, label=mode)
        a0.set_yticks([0, 1, 2], ["stale/no input", "hold", "new decision"])
    elif tr:
        q = [x for x in tr if x["queried_this_tick"] == "True"]
        for x in q:
            a0.axvline(float(x["sim_time"]), color="tab:blue", lw=1.0)
            wf = next((r2.get("world_frame") for r2 in rows if abs(float(r2["sim_time"]) - float(x["sim_time"])) < 1e-6), None)
            age = f"\ninput age {wf - int(x['input_frame'])} tick" if wf is not None and x.get("input_frame") else ""
            a0.text(float(x["sim_time"]), 0.25, f"{x['active_action']}\n{float(x['query_latency_s']):.1f}s wall{age}", fontsize=6, rotation=0)
        a0.set_yticks([])
        a0.set_ylim(0, 1.2)
    else:
        a0.text(0.5, 0.5, "per-tick controller (no adapter trace)", transform=a0.transAxes, ha="center")
        a0.set_yticks([])
    a0.set_title(f"{ep.label} | {ep.run_set} | {ep.condition} s{ep.seed} | terminated: {ep.terminated}", fontsize=8)
    a0.legend(fontsize=6, loc="upper right") if tr and "mode" in tr[0] else None
    a1.plot(t, [f(r.get("raw_throttle")) for r in rows], color="tab:green", lw=1, label="throttle (controller output)")
    a1.plot(t, [f(r.get("raw_brake")) for r in rows], color="tab:red", lw=1, label="brake (controller output)")
    a1.plot(t, [f(r.get("applied_brake")) for r in rows], color="tab:red", lw=0.8, ls=":", label="brake (vehicle readback)")
    a1.set_ylabel("control [0-1]")
    a1.legend(fontsize=6, loc="upper right")
    a2.plot(t, [float(r["ego_speed_kmh"]) for r in rows], color="k", lw=1.1)
    a2.set_ylabel("speed [km/h]")
    sl = [x - EGO_HALF_LENGTH_M if x is not None else None for x in ep.signed_stopline()]
    a3.plot(t, sl, color="tab:purple", lw=1.1)
    a3.axhline(0, color="k", lw=0.8)
    a3.set_ylabel("front to line [m]")
    a3.set_xlabel("simulation time [s]")
    m = stop_measures(ep)
    for ax in axes:
        ax.axvline(m["onset_s"], color="orange", lw=1.0)
        ax.axvline(m["active_end_s"], color="orange", lw=1.0, ls="--")
        for key, c in (("first_brake_after_onset_s", "tab:red"), ("first_stop_from_motion_s", "k"), ("front_crosses_line_s", "tab:purple")):
            if m.get(key) is not None:
                ax.axvline(m[key], color=c, lw=0.6, ls=":")
    a3.text(0.01, 0.05, "orange: STOP onset / window end; red dotted: first brake>=0.1; black dotted: first stop; purple dotted: front crosses line",
            transform=a3.transAxes, fontsize=6)
    fig.tight_layout()
    safe = f"timeline_{ep.run_set}_{ep.label}_{ep.condition}_s{ep.seed}".replace(".", "")
    fig.savefig(fig_dir / f"{safe}.png")
    plt.close(fig)



# --------------------------------------------------------------- geometry
EGO_HALF_WIDTH_M = 1.0817  # measured tesla.model3 bounding-box half-width (same source as the half-length)
MODEL_INPUTS = {  # as wired in MARSHAL (02_evaluation_spec_green_stop.md section 2)
    "oracle": "privileged ground truth (no sensor)", "baseline": "TrafficManager autopilot (officer-blind)",
    "transfuser": "3 RGB + LiDAR + IMU + GNSS", "tcp": "3 RGB 1600x900", "interfuser": "3 RGB + LiDAR + IMU",
    "cilrs": "1 RGB 400x300 + IMU", "aim": "1 RGB 400x300 + IMU", "neat": "3 RGB 400x300 + IMU",
    "pid": "route + speed (no camera)", "mpc": "route + speed (no camera)", "openemma": "1 RGB 800x600 + IMU + GNSS",
    "glm-4.5v": "1 dash-cam RGB (512 px JPEG)", "qwen2.5-vl": "1 dash-cam RGB (512 px JPEG)",
    "qwen3-vl": "1 dash-cam RGB (512 px JPEG)",
}


class _Geometry:
    """Offline Town03 lane and signal geometry from an exported OpenDRIVE file (no simulator needed)."""

    def __init__(self, map_dir: Path):
        import carla  # the carla package builds a Map from OpenDRIVE text without a server
        self.carla = carla
        self.map = carla.Map("Town03", (map_dir / "Town03.xodr").read_text(encoding="utf-8"))
        self.lights = (_json(map_dir / "town03_traffic_lights.json") or {}).get("traffic_lights", [])

    def light_for_stop_line(self, stop_xy):
        """The traffic light whose stop waypoint is the episode's stop line (CARLA get_stop_waypoints)."""
        best = None
        for tl in self.lights:
            for s in tl["stop_waypoints"]:
                d = math.hypot(s["x"] - stop_xy[0], s["y"] - stop_xy[1])
                if best is None or d < best[0]:
                    best = (d, tl)
        return best[1] if best and best[0] < 0.5 else None

    def lane_margins(self, x, y, yaw_deg):
        """(left, right, waypoint): clearance in m between the ego body box and each lane boundary; + = inside."""
        wp = self.map.get_waypoint(self.carla.Location(x=float(x), y=float(y), z=0.0), project_to_road=True)
        if wp is None:
            return None, None, None
        c = wp.transform.location
        lane_yaw = math.radians(wp.transform.rotation.yaw)
        # signed lateral offset of the ego centre from the lane centre (+ = right of the lane direction)
        off = (float(x) - c.x) * -math.sin(lane_yaw) + (float(y) - c.y) * math.cos(lane_yaw)
        dpsi = math.radians((yaw_deg if yaw_deg is not None else wp.transform.rotation.yaw) - wp.transform.rotation.yaw)
        half_extent = EGO_HALF_WIDTH_M * abs(math.cos(dpsi)) + EGO_HALF_LENGTH_M * abs(math.sin(dpsi))
        half_lane = wp.lane_width / 2.0
        return half_lane + off - half_extent, half_lane - off - half_extent, wp


def geometry_rows(ep: Episode, geo: _Geometry) -> List[Dict[str, Any]]:
    light = geo.light_for_stop_line(ep.stop_xy) if ep.stop_xy else None
    ctrl: Dict[float, Dict[str, float]] = {}  # applied controls; July logs keep them only in metrics.csv (t,key,value)
    if (ep.dir / "metrics.csv").exists():
        with (ep.dir / "metrics.csv").open(encoding="utf-8") as fh:
            for m in csv.DictReader(fh):
                if m.get("key") in ("throttle", "brake") and f(m.get("value")) is not None:
                    ctrl.setdefault(round(float(m["t"]), 3), {})[m["key"]] = float(m["value"])
    out = []
    for r, d in zip(ep.rows, ep.signed_stopline()):
        c = ctrl.get(round(float(r["sim_time"]), 3), {})
        yaw = f(r.get("ego_yaw_deg"))
        left, right, wp = geo.lane_margins(r["ego_x"], r["ego_y"], yaw)
        out.append({
            "sim_time": float(r["sim_time"]), "speed_kmh": float(r["ego_speed_kmh"]),
            "front_to_stop_line_m": (d - EGO_HALF_LENGTH_M) if d is not None else None,
            "centre_to_stop_line_m": d,
            "left_lane_margin_m": left, "right_lane_margin_m": right,
            "lane_width_m": wp.lane_width if wp else None, "in_junction_lane": bool(wp.is_junction) if wp else None,
            "yaw_source": "logged ego_yaw_deg" if yaw is not None else "lane-aligned (yaw not logged)",
            "dist_to_traffic_light_m": (math.hypot(float(r["ego_x"]) - light["x"], float(r["ego_y"]) - light["y"])
                                        if light else None),
            "dist_to_officer_m": f(r.get("distance_to_officer_m")),
            "officer_gesture": r.get("officer_gesture_id"), "officer_active": r.get("officer_active"),
            "applied_brake": f(r.get("applied_brake")) if f(r.get("applied_brake")) is not None else c.get("brake"),
            "applied_throttle": (f(r.get("applied_throttle")) if f(r.get("applied_throttle")) is not None
                                 else c.get("throttle")),
        })
    return out


def cmd_geometry(dev: Path, out: Path) -> None:
    """Per-model summary and per-second tables of ego-to-environment distances (green_stop leaderboard)."""
    map_dir = dev / "outputs" / "dac2027_audit" / "map"
    if not (map_dir / "Town03.xodr").exists():
        print(f"geometry: skipped, {map_dir / 'Town03.xodr'} missing (export it with: python scripts/dac2027_carla_util.py export-map {map_dir})")
        return
    geo = _Geometry(map_dir)
    summary, series = [], []
    for label in LB_LABELS:
        ep = lb_episode(dev, label)
        if ep is None:
            continue
        s, g = score(ep)
        rows = geometry_rows(ep, geo)
        onset, _, active_end = S._stop_hold_window(ep.rows)
        act = [x for x in rows if onset <= x["sim_time"] <= active_end]
        deepest = min(act, key=lambda x: x["front_to_stop_line_m"])  # maximum incursion (or closest stop)
        nearest = min(act, key=lambda x: abs(x["front_to_stop_line_m"]))  # closest approach to the line itself
        light = geo.light_for_stop_line(ep.stop_xy)
        summary.append({
            "model": label, "input": MODEL_INPUTS.get(label, ""),
            "episode_end_s": rows[-1]["sim_time"], "terminated": ep.terminated,
            "front_to_line_at_spawn_m": rows[0]["front_to_stop_line_m"],
            "front_to_line_min_in_stop_window_m": deepest["front_to_stop_line_m"],
            "front_to_line_final_m": rows[-1]["front_to_stop_line_m"],
            "left_lane_margin_min_m": min(x["left_lane_margin_m"] for x in act),
            "right_lane_margin_min_m": min(x["right_lane_margin_m"] for x in act),
            "dist_to_traffic_light_at_closest_m": nearest["dist_to_traffic_light_m"],
            "time_of_closest_approach_s": nearest["sim_time"],
            "dist_to_officer_min_m": min(x["dist_to_officer_m"] for x in act if x["dist_to_officer_m"] is not None),
            "traffic_light": f"actor {light['actor_id']} (OpenDRIVE {light['opendrive_id']}), held Green" if light else "",
            "strict": s.get("verdict"), "graded_credit_x100": round(100 * (g.get("credit") or 0.0), 1),
            "yaw_source": rows[0]["yaw_source"],
        })
        whole = 0
        for i, x in enumerate(rows):  # first tick at or after every whole simulation second, plus the last tick
            last = i == len(rows) - 1
            if x["sim_time"] + 1e-9 >= whole or last:
                series.append({"model": label, "t_label_s": whole if x["sim_time"] + 1e-9 >= whole else "end", **x})
                whole = math.floor(x["sim_time"] + 1e-9) + 1
    write_csv(out / "green_stop_geometry_summary.csv", summary)
    write_csv(out / "green_stop_geometry_per_second.csv", series)
    print(f"geometry: {len(summary)} models, {len(series)} per-second rows")


# ------------------------------------------------------------- leaderboard
README_NAMES = {  # README row label -> sweep label
    "oracle": "oracle", "InterFuser": "interfuser", "NEAT": "neat", "TCP": "tcp", "CILRS": "cilrs",
    "OpenEMMA-B": "openemma", "AIM": "aim", "baseline": "baseline", "TransFuser": "transfuser",
    "MPC": "mpc", "PID": "pid", "GLM-4.5V": "glm-4.5v", "Qwen2.5-VL-72B": "qwen2.5-vl",
    "Qwen3-VL-235B-A22B": "qwen3-vl",
}


def _readme_label(cell: str) -> Optional[str]:
    """Exact model name of a README table row: markup removed, qualifier after ' (' or ' —' dropped."""
    import re
    text = re.sub(r"[*_]|&[A-Za-z]+;", "", cell).strip()
    text = re.split(r" \(| —", text)[0].strip()
    return README_NAMES.get(text)


def published_from_readme(readme: Path) -> Dict[str, Dict[str, Any]]:
    """Graded, scenarios passed, override cells and failure profile per model, read from the README results tables.

    Only tables whose header carries the expected column names are read; values are taken by column
    position; a duplicate row or a missing field for any leaderboard model is an error.
    """
    import re
    pub: Dict[str, Dict[str, Any]] = {}
    seen: Dict[tuple, str] = {}
    header: Optional[List[str]] = None
    clean = lambda c: re.sub(r"[*_]|&[A-Za-z]+;", "", c).strip()  # noqa: E731
    for line in readme.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [clean(c).lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # separator row
        label = _readme_label(cells[0])
        if label is None:
            continue
        if {"marshal-graded", "scenarios passed", "override cells (12)"} <= set(header):
            kind = "leaderboard"
            val = {"graded": float(clean(cells[header.index("marshal-graded")])),
                   "passed": clean(cells[header.index("scenarios passed")]).replace(" ", ""),
                   "override_cells": clean(cells[header.index("override cells (12)")]).replace(" ", "")}
        elif header[1:] == ["override (6)", "stressed-override (6)", "validity (5)", "conflict (3)", "scene (2)", "safety (3)"]:
            kind = "profile"
            val = {"profile": ";".join(clean(c).replace(" ", "") for c in cells[1:7])}
        else:
            continue
        if (label, kind) in seen:
            raise ValueError(f"README: duplicate {kind} row for {label}")
        seen[(label, kind)] = line
        pub.setdefault(label, {}).update(val)
    missing = [m for m in LB_LABELS
               if set(pub.get(m, {})) != {"graded", "passed", "override_cells", "profile"}]
    if missing:
        raise ValueError(f"README: leaderboard or failure-profile fields missing for {missing}")
    return pub


def cmd_leaderboard(dev: Path, out: Path) -> None:
    """Every published leaderboard number decomposed into its 25 scenario cells and re-derived."""
    import _run_vlm_test as vlm
    from marshal_bench.criteria.marshal_metrics import (
        CONFLICT_TYPE, CONFLICT_TYPE_ORDER, aggregate, compute_episode_metrics)
    scen = list(vlm.SCENARIO_ORDER)
    pub = published_from_readme(REPO / "README.md")
    cells, summary = [], []
    for label in LB_LABELS:
        scores, metrics, rows_m = [], [], []
        for sc in scen:
            ep = lb_episode(dev, label, sc)
            inner = dict(ep.result or {"scenario": sc})
            inner.setdefault("scenario", sc)
            kw = scorer_kwargs(ep, sc, vlm.SCENARIOS[sc]["expect"])
            s = S.score_episode_from_telemetry(inner, ep.rows, **kw)
            g = G.score_episode_from_telemetry(inner, ep.rows, **kw)
            inner["strict_scoring"] = s
            scores.append(g)
            metrics.append(compute_episode_metrics(inner, scenario=sc, telemetry_rows=ep.rows))
            w = G.SCENARIO_AUTHORITY_WEIGHTS.get(G.canonical_scenario_name(sc), 1.0)
            row = {"model": label, "scenario": sc, "family": CONFLICT_TYPE.get(sc), "expected_action": vlm.SCENARIOS[sc]["expect"],
                   "weight": w, "strict": s.get("verdict"), "strict_reason": s.get("reason"), "credit": g.get("credit"),
                   "action_credit": g.get("raw_action_credit"), "latency_factor": g.get("latency_factor"),
                   "safety_factor": g.get("safety_factor"), "weighted_credit": w * (g.get("credit") or 0.0)}
            cells.append(row)
            rows_m.append(row)
        agg = G.aggregate_graded_scores(scores)
        mag = aggregate(metrics)
        prof = {fam: sum(1 for r in rows_m if r["family"] == fam and r["strict"] == "PASS") for fam in CONFLICT_TYPE_ORDER}
        size = {fam: sum(1 for r in rows_m if r["family"] == fam) for fam in CONFLICT_TYPE_ORDER}
        n_pass = sum(r["strict"] == "PASS" for r in rows_m)
        override = prof["override"] + prof["stressed-override"]
        p = pub.get(label, {})
        from decimal import ROUND_HALF_UP, Decimal  # README shows the 2-decimal score rounded half-up to 1 decimal
        graded_1dp = float(Decimal(str(agg["marshal_graded"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        derived = {"graded": graded_1dp, "passed": f"{n_pass}/{len(scen)}",
                   "override_cells": f"{override}/{size['override'] + size['stressed-override']}",
                   "profile": ";".join(f"{prof[f]}/{size[f]}" for f in CONFLICT_TYPE_ORDER)}
        summary.append({
            "model": label, "weighted_credit_sum": agg["weighted_credit_sum"], "weight_sum": agg["weight_sum"],
            "marshal_graded": agg["marshal_graded"], **{f"derived_{k}": v for k, v in derived.items()},
            **{f"readme_{k}": p.get(k) for k in derived},
            "readme_matches": all(p.get(k) == v for k, v in derived.items()),
            "marshal_score_partial": mag["marshal_score_partial"],
            "r_scores": json.dumps(mag["r_scores"]), "r_unmeasured": ";".join(mag["r_unmeasured"]),
        })
    write_csv(out / "leaderboard_cells.csv", cells)
    write_csv(out / "leaderboard_summary.csv", summary)
    bad = [s["model"] for s in summary if not s["readme_matches"]]
    print(f"leaderboard: {len(summary)} models, README numbers reproduced for {len(summary) - len(bad)}"
          + (f"; mismatch: {bad}" if bad else ""))


def leaderboard_tables(summary: List[Dict[str, Any]], cells: List[Dict[str, Any]]) -> List[str]:
    lines = ["", "## T6. Leaderboard numbers re-derived from the 350 episode cells", "",
             "MARSHAL-Graded = 100 x (sum of weight x credit) / (sum of weights); scenarios passed = strict PASS count; "
             "override cells = strict PASS on the 6 override + 6 stressed-override scenarios. "
             "README columns are parsed from README.md and compared automatically.", "",
             "| Model | Sum w x credit | Sum w | Graded | README graded | Passed | README passed | Override cells | README override | Failure profile (override / stressed / validity / conflict / scene / safety) | README profile | MARSHAL Score (partial) | All README numbers reproduced |",
             "|" + "---|" * 13]
    for x in summary:
        lines.append(f"| {x['model']} | {_r(x['weighted_credit_sum'], 3)} | {_r(x['weight_sum'], 1)} | {x['derived_graded']} | "
                     f"{x['readme_graded'] or '-'} | {x['derived_passed']} | {x['readme_passed'] or '-'} | {x['derived_override_cells']} | "
                     f"{x['readme_override_cells'] or '-'} | {x['derived_profile'].replace(';', ' / ')} | "
                     f"{(x['readme_profile'] or '-').replace(';', ' / ')} | {x['marshal_score_partial']} | {x['readme_matches']} |")
    models = [x["model"] for x in summary]
    scen = list(dict.fromkeys(c["scenario"] for c in cells))
    by = {(c["model"], c["scenario"]): c for c in cells}
    lines += ["", "## T7. Per-scenario credit (x 100) behind every leaderboard row; P = strict PASS", "",
              "| Scenario | Family | Action | Weight | " + " | ".join(models) + " |",
              "|" + "---|" * (4 + len(models))]
    for sc in scen:
        c0 = by[(models[0], sc)]
        vals = []
        for m in models:
            c = by[(m, sc)]
            vals.append(f"{100 * float(c['credit'] or 0):.0f}{' P' if c['strict'] == 'PASS' else ''}")
        lines.append(f"| {sc} | {c0['family']} | {c0['expected_action']} | {c0['weight']} | " + " | ".join(vals) + " |")
    lines.append("| **Graded** | | | **" + _r(sum(float(by[(models[0], s)]['weight']) for s in scen), 1) + "** | "
                 + " | ".join(f"**{x['derived_graded']}**" for x in summary) + " |")
    lines += ["", "## T8. Score chain per model: action x latency x safety = credit; weight x credit summed",
              "", "MARSHAL Score (partial) R-subscores per model are in `data/leaderboard_summary.csv` (`r_scores`)."]
    for x in summary:
        m = x["model"]
        lines += ["", f"### {m}: graded {x['derived_graded']} = 100 x {_r(x['weighted_credit_sum'], 3)} / {_r(x['weight_sum'], 1)}; "
                  f"strict {x['derived_passed']}; override {x['derived_override_cells']}", "",
                  "| Scenario | Family | Action | Strict | Strict reason | Action credit | Latency factor | Safety | Credit | Weight | Weight x credit |",
                  "|" + "---|" * 11]
        for sc in scen:
            c = by[(m, sc)]
            lines.append(f"| {sc} | {c['family']} | {c['expected_action']} | {c['strict']} | {c['strict_reason']} | "
                         f"{_r(c['action_credit'], 3)} | {_r(c['latency_factor'], 2)} | {_r(c['safety_factor'], 2)} | "
                         f"{_r(c['credit'], 3)} | {c['weight']} | {_r(c['weighted_credit'], 3)} |")
    return lines


# ------------------------------------------------------------------ tables
def _r(v: Any, n: int = 2) -> str:
    x = f(v)
    return "-" if x is None else f"{x:.{n}f}"


def geometry_tables(summary: List[Dict[str, Any]], series: List[Dict[str, Any]],
                    models: Optional[List[str]] = None) -> List[str]:
    """T4 (per model) and T5 (per second) ego-to-environment distance tables."""
    lines = ["", "## T4. green_stop: ego vs. environment per model (leaderboard episodes)", "",
             "Stop line = front bumper to stop line (+ before, - past); lane margin = body box to lane boundary "
             "(+ inside); traffic light = ego centre to the governing light pole (actor 69) at the closest approach "
             "to the line; officer = minimum ego-centre distance during the STOP window; score = graded credit x 100.", "",
             "| Model | Input | Stop line at spawn (m) | Stop line min in STOP window (m) | Stop line final (m) | "
             "Left lane margin min (m) | Right lane margin min (m) | Traffic light (m) | Officer min (m) | Strict | Score |",
             "|" + "---|" * 11,
             "| *Worst acceptable (strict rule)* | any | - | >= -1.40 (centre >= 1.0) | stopped (< 1 km/h from 4.0 s) | "
             "no rule; proposal >= 0 (body inside lane), met by all 14 | no rule; proposal >= 0, met by all 14 | "
             "no rule (the light's stop line is the scored anchor) | <= 16.59 (or line) | PASS | 100 |"]
    for x in summary:
        lines.append(f"| {x['model']} | {x['input']} | {_r(x['front_to_line_at_spawn_m'])} | "
                     f"{_r(x['front_to_line_min_in_stop_window_m'])} | {_r(x['front_to_line_final_m'])} | "
                     f"{_r(x['left_lane_margin_min_m'])} | {_r(x['right_lane_margin_min_m'])} | "
                     f"{_r(x['dist_to_traffic_light_at_closest_m'], 1)} | {_r(x['dist_to_officer_min_m'], 1)} | "
                     f"{x['strict']} | {x['graded_credit_x100']} |")
    for m in (models or [x["model"] for x in summary]):
        rows = [x for x in series if x["model"] == m]
        if not rows:
            continue
        lines += ["", f"## T5. green_stop per second: {m}", "",
                  "| t (s) | sim time (s) | Speed (km/h) | Stop line, front (m) | Left lane margin (m) | "
                  "Right lane margin (m) | Traffic light (m) | Officer (m) | STOP active | Throttle | Brake |",
                  "|" + "---|" * 11]
        for x in rows:
            lines.append(f"| {x['t_label_s']} | {_r(x['sim_time'])} | {_r(x['speed_kmh'], 1)} | "
                         f"{_r(x['front_to_stop_line_m'])} | {_r(x['left_lane_margin_m'])} | "
                         f"{_r(x['right_lane_margin_m'])} | {_r(x['dist_to_traffic_light_m'], 1)} | "
                         f"{_r(x['dist_to_officer_m'], 1)} | {x['officer_active']} | {_r(x['applied_throttle'])} | "
                         f"{_r(x['applied_brake'])} |")
    return lines


def cmd_tables(dev: Path, out: Path) -> None:
    """Markdown tables for the report, generated from the CSVs written by the other commands."""
    def rd(name):
        with (out / name).open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    t = rd("green_stop_score_trace.csv")
    lines = ["## T1. green_stop behaviour (raw measurements, front bumper = centre - 2.3959 m)", "",
             "| Model | Run set | Condition | Seed | End (s) | v at onset (km/h) | First stop (s) | Front at first stop (m) | Min front in window (m) | Longest stop in window (s) | Front crosses line (s) | Max v in enforcement (km/h) | Strict | First failed gate | Credit |",
             "|" + "---|" * 15]
    for x in t:
        end = f"{_r(x['t_end_s'])} {'early-term' if 'stopped' in (x['terminated'] or '') else 'timeout'}"
        lines.append(f"| {x['model']} | {x['run_set']} | {x['condition']} | {x['seed'] or '-'} | {end} | {_r(x['speed_at_onset_kmh'], 1)} | "
                     f"{_r(x['first_stop_from_motion_s'])} | {_r(x['front_to_line_at_first_stop_m'])} | {_r(x['min_front_to_line_active_m'])} | "
                     f"{_r(x['longest_stopped_span_active_s'])} | {_r(x['front_crosses_line_s'])} | {_r(x['max_speed_enforcement_kmh'], 1)} | "
                     f"{x['strict_verdict']} | {x['first_failed_gate'] or '-'} | {_r(x['credit'], 3)} |")
    lines += ["", "## T2. green_stop graded decomposition (credit = action x latency factor x safety; action = base x conflict x evidence x engagement)", "",
              "| Model | Run set | Condition | Seed | Scored speed (km/h) | Scored centre clearance (m) | Speed credit | Clearance credit | Base | Conflict | Evidence | Engagement | Action | Latency (s) | Latency factor | Safety | Credit |",
              "|" + "---|" * 17]
    for x in t:
        lines.append(f"| {x['model']} | {x['run_set']} | {x['condition']} | {x['seed'] or '-'} | {_r(x['scored_speed_kmh'], 1)} | {_r(x['scored_clearance_m'])} | "
                     f"{_r(x['speed_margin_credit'], 3)} | {_r(x['stopline_clearance_credit'], 3)} | {_r(x['base_0.6s+0.4c'], 3)} | {_r(x['conflict_factor'])} | "
                     f"{_r(x['evidence_factor'])} | {_r(x['engagement_factor'], 3)} | {_r(x['action_credit'], 3)} | {_r(x['latency_s'])} | "
                     f"{_r(x['latency_factor'])} | {_r(x['safety_factor'])} | {_r(x['credit'], 3)} |")
    a = rd("suite_aggregate_rescored.csv")
    lines += ["", "## T3. Suite aggregation, re-scored from telemetry with the current scorers", "",
              "| Model | Strict PASS / 25 | Sum w x credit | Sum w | MARSHAL-Graded | Published graded | MARSHAL Score (partial, published) | Reproduced |",
              "|" + "---|" * 8]
    for x in a:
        lines.append(f"| {x['model']} | {x['strict_pass']} | {_r(x['weighted_credit_sum'], 3)} | {_r(x['weight_sum'], 2)} | {x['marshal_graded']} | "
                     f"{x['published_graded']} | {x['published_marshal_score']} | {x['matches_published']} |")
    if (out / "leaderboard_summary.csv").exists():
        lines += leaderboard_tables(rd("leaderboard_summary.csv"), rd("leaderboard_cells.csv"))
    if (out / "green_stop_geometry_summary.csv").exists():
        lines += geometry_tables(rd("green_stop_geometry_summary.csv"), rd("green_stop_geometry_per_second.csv"))
    (out.parent / "tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"tables: {out.parent / 'tables.md'}")

# -------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["score-trace", "aggregate", "cases", "rescore-variant", "leaderboard", "timing", "geometry", "figures", "tables", "all"])
    ap.add_argument("--dev-root", default=str(REPO.parent), help="workspace that holds tmp/ and outputs/ episode artifacts")
    ap.add_argument("--out", default=str(REPO / "reports" / "dac2027_audit" / "data"))
    a = ap.parse_args(argv)
    dev, out = Path(a.dev_root), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cmds = {"score-trace": cmd_score_trace, "aggregate": cmd_aggregate, "cases": cmd_cases,
            "rescore-variant": cmd_rescore_variant, "leaderboard": cmd_leaderboard, "timing": cmd_timing, "geometry": cmd_geometry, "figures": cmd_figures,
            "tables": cmd_tables}
    for name in (cmds if a.command == "all" else [a.command]):
        cmds[name](dev, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
