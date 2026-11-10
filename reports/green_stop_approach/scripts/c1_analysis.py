"""GREEN STOP approach protocol (gs_approach_c1) -- per-run analysis (analysis_version below). Read-only on episodes.

    python -I reports/green_stop_approach/scripts/c1_analysis.py <runs_root> --out <dir> [--old-csv <earlier runs csv>]

Writes runs_corrected.csv and vlm_queries_corrected.csv (published as data/runs.csv and data/vlm_queries.csv).
ANALYSIS only: no protocol, scoring rule, config or raw log is
touched, and the official verdict/reason are copied from each episode's strict_scoring.json.

Telemetry clock semantics (from marshal_bench/scenarios/_common.py run loop, read 2026-11-06):
  world.tick() -> sim_time += dt -> trigger/gesture -> controller.step(obs) -> ego.apply_control(cmd)
  -> ego.get_control() -> row written.
  * ego_speed_kmh / pose of row t  = state after the physics step that ended at t (driven by earlier commands)
  * raw_*  of row t                = command the controller returned at t (== model output tick)
  * applied_* of row t             = ego.get_control() read right after apply_control; observed in the logs to
                                     equal the PREVIOUS tick's raw command (client-side snapshot), i.e. one tick
                                     behind raw. This is a logging fact, not a measured actuator/physics latency.
Missing values stay empty (never 0).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from telemetry_check import REQUIRED_FIELDS, check_telemetry, num, telemetry_rows  # noqa: E402,F401

ANALYSIS_VERSION = "gs_approach_c1 analysis (2026-11-06)"

# ---- definitions (all explicit; none of these change the protocol or Strict scoring) -------------------
MOVE_MIN_PROGRESS_M = 0.5        # "moved": net forward progress f0 - min(front) >= 0.5 m (front = vehicle front
                                 # to stop line). Stationary pose jitter observed in these logs is reported in the
                                 # summary (stationary_jitter_max_m) to show 0.5 m is far above it.
LEGACY_ENGAGE_SPEED_KMH = 5.0    # `started` of the earlier run summary (= scorer engagement thresholds): max speed >= 5
LEGACY_ENGAGE_PROGRESS_M = 1.0   #   AND progress >= 1 m.  Kept as column legacy_engaged_5kmh_1m (renamed, not redefined)
BRAKE_THRESHOLD = 0.1            # "brake command" = value >= 0.1 (same as the old analysis). >0 is reported separately.
LOW_SPEED_KMH = 1.0              # instantaneous low speed: < 1.0 km/h (same strictness as Strict stop_speed)
HOLD_SPEED_KMH = 1.0             # diagnostic held stop: every sample <= 1.0 km/h (old _held used <=)
HOLD_MIN_S = 2.0                 #   window length by sim_time (end - start >= 2.0 s, endpoints inclusive)
HOLD_MAX_NET_DISP_M = 0.5        #   |ego_forward_m(end) - ego_forward_m(start)| <= 0.5 m (net displacement, as old)
HOLD_MAX_GAP_FACTOR = 1.5        #   consecutive samples further apart than 1.5 x nominal dt break the window
REDEPART_KMH = 5.0               # re-departure after a low-speed sample: later speed >= 5 km/h
DECEL_START_DROP_KMH = 2.0       # deceleration start: 2 km/h below the running max after the reference (as old)
DIAG_WINDOW_S = 12.0             # analysis window after the reference crossing (= signal duration)

STATES = ("1 execution/data error", "2 no movement (net progress < 0.5 m)", "3 moved, reference not reached",
          "4 reference reached, normal approach not met", "5 normal approach, then reference/signal")


def csv_num(x):
    """vlm_trace.csv is text: parse the CSV cell ONCE into a float (a separate in-memory value) and compute only with
    that parsed value. JSON telemetry never goes through this -- it uses num(), which rejects strings."""
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def rows_of(ep: Path):
    rows, _ = telemetry_rows(jload(ep / "strict_telemetry.json"))
    return rows or []


def events_of(ep: Path):
    e = jload(ep / "events.json")
    e = e.get("events") if isinstance(e, dict) else e
    return e if isinstance(e, list) else None


def ev(events, name):
    return next(((e.get("payload") or {}) | {"_t": e.get("t")} for e in events
                 if isinstance(e, dict) and e.get("name") == name and isinstance(e.get("payload") or {}, dict)), None)


def at(rows, t, key):
    if not rows or t is None:
        return None
    r = min(rows, key=lambda r: abs(r["sim_time"] - t))
    return num(r.get(key)) if abs(r["sim_time"] - t) < 1e-6 else None


# ---- F1: movement vs legacy engagement vs reference -------------------------------------------------------
def movement(rows):
    fr = [num(r.get("stopline_front_m")) for r in rows]
    fr = [f for f in fr if f is not None]
    sp = [num(r.get("ego_speed_kmh")) for r in rows]
    sp = [s for s in sp if s is not None]
    if not fr or not sp:
        return {"net_progress_m": None, "max_speed_kmh": None, "moved": None, "legacy_engaged_5kmh_1m": None}
    prog = fr[0] - min(fr)
    vmax = max(sp)
    return {"net_progress_m": round(prog, 4), "max_speed_kmh": round(vmax, 4),
            "moved": prog >= MOVE_MIN_PROGRESS_M,
            "legacy_engaged_5kmh_1m": vmax >= LEGACY_ENGAGE_SPEED_KMH and prog >= LEGACY_ENGAGE_PROGRESS_M}


def classify_state(exec_ok: bool, mv: dict, reference_crossed_s, normal) -> str:
    if not exec_ok or mv.get("moved") is None:
        return STATES[0]
    if not mv["moved"]:
        return STATES[1]
    if reference_crossed_s is None:
        return STATES[2]
    return STATES[4] if normal else STATES[3]


# ---- F6: earliest valid held-stop window ------------------------------------------------------------------
def t_of(r):
    """sim_time of a row as a finite float, else None (missing key, None, non-numeric, NaN/inf)."""
    return num(r.get("sim_time")) if isinstance(r, dict) else None


def validate_time(rows):
    """F6 data check on the time axis.  Counts each defect class; ok only when there is none."""
    out = {"n_rows": len(rows), "time_missing_key": 0, "time_none": 0, "time_non_numeric": 0, "time_non_finite": 0,
           "time_negative": 0, "time_non_increasing": 0, "time_gaps_gt_1p5dt": 0}
    prev = None
    for r in rows:
        if not isinstance(r, dict) or "sim_time" not in r:
            out["time_missing_key"] += 1; prev = None; continue
        v = r["sim_time"]
        if v is None:
            out["time_none"] += 1; prev = None; continue
        if isinstance(v, (str, bool)) or not isinstance(v, (int, float)):   # numeric strings are rejected too
            out["time_non_numeric"] += 1; prev = None; continue
        fv = float(v)
        if not math.isfinite(fv):
            out["time_non_finite"] += 1; prev = None; continue
        if fv < 0:
            out["time_negative"] += 1
        if prev is not None and fv <= prev:
            out["time_non_increasing"] += 1
        prev = fv
    dt = nominal_dt(rows)
    if dt is not None:
        ts = [t_of(r) for r in rows]
        out["time_gaps_gt_1p5dt"] = sum(1 for x, y in zip(ts, ts[1:])
                                        if x is not None and y is not None and y - x > HOLD_MAX_GAP_FACTOR * dt + 1e-9)
    out["time_ok"] = not any(v for k, v in out.items() if k.startswith("time_"))
    return out


def nominal_dt(rows):
    ts = [t_of(r) for r in rows]
    d = sorted(y - x for x, y in zip(ts, ts[1:]) if x is not None and y is not None and y > x)
    return d[len(d) // 2] if d else None


def held_window(rows, t0, t1, *, dt=None):
    """Earliest [start, end] in [t0, t1] (inclusive) such that every sample has speed <= HOLD_SPEED_KMH,
    end - start >= HOLD_MIN_S (sim_time), |fwd(end) - fwd(start)| <= HOLD_MAX_NET_DISP_M, and no gap between
    consecutive samples exceeds HOLD_MAX_GAP_FACTOR * dt or is non-increasing.  Every start sample is tried
    (a failed first candidate does not stop the search).  Rows are taken in recorded order; a row whose
    sim_time / speed / forward is missing, None, non-numeric or non-finite is never treated as observed: it
    ends any window running through it (no interpolation).  Returns (start_s, end_s, start_index) or None."""
    dt = dt or nominal_dt(rows)
    if dt is None:
        return None
    gap_max = HOLD_MAX_GAP_FACTOR * dt + 1e-9
    seq = []                    # (t, v, f) or None (= break) in recorded order, restricted to [t0, t1]
    for r in rows:
        t = t_of(r)
        v = num(r.get("ego_speed_kmh")) if isinstance(r, dict) else None
        f = num(r.get("ego_forward_m")) if isinstance(r, dict) else None
        if t is None:
            seq.append(None); continue
        if t < t0 - 1e-9 or t > t1 + 1e-9:
            continue
        seq.append(None if (v is None or f is None) else (t, v, f))
    n = len(seq)
    for i in range(n):
        if seq[i] is None or seq[i][1] > HOLD_SPEED_KMH:
            continue
        ti, _, fi = seq[i]
        for j in range(i + 1, n):
            a, b = seq[j - 1], seq[j]
            if b is None:
                break
            g = b[0] - a[0]
            if g <= 0 or g > gap_max or b[1] > HOLD_SPEED_KMH:
                break
            if b[0] - ti >= HOLD_MIN_S - 1e-9 and abs(b[2] - fi) <= HOLD_MAX_NET_DISP_M + 1e-12:
                return ti, b[0], i
    return None


def held_legacy(rows, t0, t1):
    """The earlier run-summary _held helper (kept verbatim for comparison).  Guard (v1.2): it is only
    evaluated on telemetry that passes check_telemetry(); on defective data it returns (None, None) and computes
    nothing, instead of computing on (or crashing over) bad values."""
    if not check_telemetry(rows)["ok"]:
        return None, None
    span = [r for r in rows if t0 - 1e-9 <= r["sim_time"] <= t1 + 1e-9]
    i = 0
    while i < len(span):
        if span[i]["ego_speed_kmh"] <= 1.0:
            j = i
            while j + 1 < len(span) and span[j + 1]["ego_speed_kmh"] <= 1.0:
                j += 1
            a, b = span[i], span[j]
            if b["sim_time"] - a["sim_time"] >= 2.0 - 1e-9 and abs(b["ego_forward_m"] - a["ego_forward_m"]) <= 0.5:
                return a["sim_time"], a["stopline_front_m"]
            i = j + 1
        else:
            i += 1
    return None, None


def time_gaps(rows):
    v = validate_time(rows)
    return {"nominal_dt_s": None if nominal_dt(rows) is None else round(nominal_dt(rows), 4),
            "n_gaps": v["time_gaps_gt_1p5dt"], "n_nonincreasing": v["time_non_increasing"]}


# ---- F2: command / applied / response timing ---------------------------------------------------------------
def first_t(rows, t0, pred):
    return next((r["sim_time"] for r in rows if r["sim_time"] >= t0 - 1e-9 and pred(r)), None)


def brake_timing(rows, t0):
    has_raw = any("raw_brake" in r for r in rows)
    has_app = any("applied_brake" in r for r in rows)

    def ge(k, th):
        return lambda r: num(r.get(k)) is not None and num(r.get(k)) >= th

    def gt0(k):
        return lambda r: num(r.get(k)) is not None and num(r.get(k)) > 0.0

    out = {
        "first_raw_brake_ge0p1_s": first_t(rows, t0, ge("raw_brake", BRAKE_THRESHOLD)) if has_raw else None,
        "first_applied_brake_ge0p1_s": first_t(rows, t0, ge("applied_brake", BRAKE_THRESHOLD)) if has_app else None,
        "first_raw_brake_gt0_s": first_t(rows, t0, gt0("raw_brake")) if has_raw else None,
        "first_applied_brake_gt0_s": first_t(rows, t0, gt0("applied_brake")) if has_app else None,
        "raw_brake_field_present": has_raw, "applied_brake_field_present": has_app,
    }
    tr = out["first_raw_brake_ge0p1_s"]
    out["first_speed_decrease_after_raw_brake_s"] = None
    if tr is not None:
        after = [r for r in rows if r["sim_time"] > tr + 1e-9]
        prev = at(rows, tr, "ego_speed_kmh")
        for r in after:
            v = num(r.get("ego_speed_kmh"))
            if v is not None and prev is not None and v < prev - 1e-6:
                out["first_speed_decrease_after_raw_brake_s"] = r["sim_time"]
                break
            prev = v
    return out


# ---- F3: deceleration (mean over an interval with actual end speed; peak from adjacent samples) ----------
def decel(rows, tc, t_end):
    after = [r for r in rows if tc - 1e-9 <= r["sim_time"] <= t_end + 1e-9]
    out = {"decel_start_s": None, "first_below_1kmh_s": None, "mean_decel_mps2": None,
           "mean_decel_interval": None, "peak_decel_adjacent_mps2": None, "peak_decel_at_s": None}
    peak = None
    for r in after:
        peak = r["ego_speed_kmh"] if peak is None else max(peak, r["ego_speed_kmh"])
        if r["ego_speed_kmh"] <= peak - DECEL_START_DROP_KMH:
            out["decel_start_s"] = r["sim_time"]
            break
    out["first_below_1kmh_s"] = next((r["sim_time"] for r in after if r["ego_speed_kmh"] < LOW_SPEED_KMH), None)
    ds, de = out["decel_start_s"], out["first_below_1kmh_s"]
    if ds is not None and de is not None and de > ds:
        v0, v1 = at(rows, ds, "ego_speed_kmh"), at(rows, de, "ego_speed_kmh")
        out["mean_decel_mps2"] = round((v0 - v1) / 3.6 / (de - ds), 3)
        out["mean_decel_interval"] = f"[{ds:.2f},{de:.2f}] s, {v0:.3f}->{v1:.3f} km/h"
    best = None
    for a, b in zip(after, after[1:]):
        g = b["sim_time"] - a["sim_time"]
        if g > 0:
            d = (a["ego_speed_kmh"] - b["ego_speed_kmh"]) / 3.6 / g
            if best is None or d > best[0]:
                best = (d, a["sim_time"], b["sim_time"], a["ego_speed_kmh"], b["ego_speed_kmh"])
    if best:
        out["peak_decel_adjacent_mps2"] = round(best[0], 3)
        out["peak_decel_at_s"] = f"{best[1]:.2f}->{best[2]:.2f} ({best[3]:.4f}->{best[4]:.4f} km/h)"
    return out


# ---- F4-A / F4-F: officer boundary and all-condition Strict diagnostic ------------------------------------
def officer_active_span(rows):
    act = [r["sim_time"] for r in rows if r.get("officer_active") is True]
    return (act[0], act[-1]) if act else (None, None)


def strict_diagnostic(rows, sc):
    """Every STOP condition evaluated independently on the scorer's own windows (copied from strict_scoring).
    The official verdict/reason stay as recorded; `diag_first_fail_reproduced` checks our order matches."""
    aw = sc.get("active_window") or {}
    onset, active_end = num(aw.get("onset_s")), num(aw.get("active_end_s"))
    d = {"diag_signal_issued": onset is not None}
    if onset is None:
        return d
    horizon = num(sc.get("horizon_s"))
    hz = [r for r in rows if horizon is None or r["sim_time"] <= horizon + 1e-9]
    scored = [r for r in hz if r["sim_time"] <= active_end + 1e-9]
    active = [r for r in scored if onset <= r["sim_time"] <= active_end]
    enf = [r for r in scored if onset + 3.0 <= r["sim_time"] <= active_end]
    fwd = [num(r.get("ego_forward_m")) for r in scored]
    fwd = [f for f in fwd if f is not None]
    sp = [r["ego_speed_kmh"] for r in scored]
    min_front = min(num(r.get("stopline_front_m")) for r in active)
    min_fwd = min((num(r.get("stopline_forward_m")) for r in active if num(r.get("stopline_forward_m")) is not None), default=None)
    min_off = min((num(r.get("distance_to_officer_m")) for r in active if num(r.get("distance_to_officer_m")) is not None), default=None)
    band = 16.59
    d.update({
        "diag_active_rows_first_s": active[0]["sim_time"], "diag_active_rows_last_s": active[-1]["sim_time"],
        "diag_collision": max((num(r.get("collision_count")) or 0) for r in scored) > 0,
        "diag_engaged": (max(sp) >= 5.0 and (max(fwd) - min(fwd)) >= 1.0) if fwd else None,
        "diag_junction_entry_front_lt0": any(r.get("in_junction") is True and num(r.get("stopline_front_m")) is not None
                                             and r["stopline_front_m"] < 0 for r in active),
        "diag_min_front_active_m": round(min_front, 4),
        "diag_front_margin_lt1m": min_front < 1.0,
        "diag_line_crossed_front_lt0": min_front < 0.0,
        "diag_upstream_never_engaged": (min_fwd is not None and min_fwd > band and not (min_off is not None and min_off <= band)),
        "diag_max_speed_enforcement_kmh": round(max(r["ego_speed_kmh"] for r in enf), 4) if enf else None,
    })
    d["diag_hold_violation_ge1kmh"] = d["diag_max_speed_enforcement_kmh"] is None or d["diag_max_speed_enforcement_kmh"] >= 1.0
    order = [("diag_collision", "collision"), ("diag_engaged", "never engaged"), ("diag_junction_entry_front_lt0", "intersection"),
             ("diag_front_margin_lt1m", "within 1.0 m"), ("diag_upstream_never_engaged", "parked far upstream"),
             ("diag_hold_violation_ge1kmh", "did not remain stopped")]
    first = None
    for k, word in order:
        bad = (d[k] is False) if k == "diag_engaged" else bool(d[k])
        if bad:
            first = word
            break
    d["diag_conditions_failed"] = ";".join(w for k, w in order if ((d[k] is False) if k == "diag_engaged" else bool(d[k])))
    reason = str(sc.get("reason") or "")
    d["diag_first_fail_reproduced"] = (first is None and sc.get("verdict") == "PASS") or (first is not None and first in reason)
    return d


# ---- VLM queries (F2/F4-B/C) ---------------------------------------------------------------------------
def vlm_queries(ep: Path, rows, events, onset, tc):
    tr = ep / "vlm_debug" / "vlm_trace.csv"
    if not tr.exists() or not rows:
        return []
    off = None
    if num(rows[0].get("world_elapsed_s")) is not None:
        off = rows[0]["world_elapsed_s"] - rows[0]["sim_time"]
    dec = {round(e["t"], 2): e["payload"] for e in events if e.get("name") == "vlm_decision"}
    out = []
    for q in csv.DictReader(tr.open(encoding="utf-8")):
        if q.get("queried_this_tick") != "True":
            continue
        ts = csv_num(q.get("sim_time"))
        if ts is None:
            continue
        ts = round(ts, 2)
        p = dec.get(ts, {})
        fin = csv_num(q.get("input_sim_ts"))
        fin = None if (fin is None or off is None) else round(fin - off, 3)
        ref = onset if onset is not None else tc
        out.append({
            "output_tick_s": ts, "input_frame_sim_s": fin,
            "phase": None if ref is None else ("post" if ts >= ref - 1e-9 else "pre"),
            "input_after_signal": None if (fin is None or onset is None) else fin >= onset - 1e-6,
            "action": p.get("action"), "who": p.get("who"),
            "raw_brake_same_tick": at(rows, ts, "raw_brake"), "applied_brake_same_tick": at(rows, ts, "applied_brake"),
            "applied_brake_next_tick": at(rows, round(ts + 0.05, 2), "applied_brake"),
            "front_at_output_m": at(rows, ts, "stopline_front_m"), "speed_at_output_kmh": at(rows, ts, "ego_speed_kmh"),
            "wall_latency_s": csv_num(q.get("query_latency_s")),
        })
    return out


# ---- per run -----------------------------------------------------------------------------------------------
def _err(errors, problem, detail="", row=None, position=None, field=None):
    errors.append({"problem": problem, "detail": detail, "row": row, "position": position, "field": field})


def analyse(ep: Path, cond, model, seed):
    """Per-run analysis.  Order: load -> data check (every telemetry row incl. labelled first/middle/last; events;
    scoring; event timing fields) -> compute only if every check passed.  Any defect or exception is recorded in
    o['data_error_list'] and the run is classified '1 execution/data error'; it never raises."""
    o = {"analysis_version": ANALYSIS_VERSION, "condition": cond, "model": model, "seed": seed,
         "is_reference_fixture": model.startswith("scripted_reference"), "episode": ep.as_posix()}
    errors = []
    try:
        return _analyse_checked(ep, o, errors)
    except Exception as e:  # last resort: keep the run in the table with its error list
        _err(errors, "analysis_exception", f"{type(e).__name__}: {e}"[:300])
        return _as_data_error(o, errors), []


def _as_data_error(o, errors):
    o["approach_state"] = STATES[0]
    o["data_valid"] = False
    o["data_issues"] = ";".join(sorted({e["problem"] for e in errors}))
    o["data_error_list"] = errors
    for k in ("net_progress_m", "max_speed_kmh", "moved", "legacy_engaged_5kmh_1m"):
        o.setdefault(k, None)
    return o


def _analyse_checked(ep: Path, o, errors):
    cond, model, seed = o["condition"], o["model"], o["seed"]
    tel = jload(ep / "strict_telemetry.json")
    if tel is None:
        _err(errors, "telemetry_json_unreadable")
    chk = check_telemetry(tel) if tel is not None else {"ok": False, "issues": [], "counts": {}, "summary": ""}
    for i in chk["issues"]:
        _err(errors, "telemetry:" + i["problem"], row=i["row"], position=i["position"], field=i["field"])
    if len(chk["issues"]) < sum(chk["counts"].values()):
        _err(errors, "telemetry:more_issues_not_listed", chk.get("summary", ""))
    events = events_of(ep)
    if events is None:
        _err(errors, "events_missing_or_not_a_list")
        events = []
    sc = jload(ep / "strict_scoring.json")
    if not isinstance(sc, dict):
        _err(errors, "strict_scoring_missing_or_not_an_object")
        sc = {}
    applied = ev(events, "approach_protocol_applied") or {}
    outcome = ev(events, "approach_protocol_outcome") or {}
    na = outcome.get("normal_approach") or {}
    if not isinstance(na, dict):
        _err(errors, "event_field_invalid", "normal_approach not an object")
        na = {}
    tc, onset = outcome.get("reference_crossed_s"), outcome.get("signal_onset_s")
    for name, v in (("reference_crossed_s", tc), ("signal_onset_s", onset)):
        if v is not None and num(v) is None:
            _err(errors, "event_field_invalid", f"{name}={v!r}")
    aw = sc.get("active_window") or {}
    if not isinstance(aw, dict):
        _err(errors, "scoring_field_invalid", "active_window not an object")
        aw = {}
    for name in ("onset_s", "active_end_s"):
        v = aw.get(name)
        if v is not None and num(v) is None:
            _err(errors, "scoring_field_invalid", f"active_window.{name}={v!r}")
    o.update({"official_result_label": sc.get("protocol_version"), "official_verdict": sc.get("verdict"),
              "official_reason": sc.get("reason"), "end_reason": outcome.get("terminated_reason"),
              "scene_checks_passed": applied.get("checks_passed"), "reference_crossed_s": tc, "signal_onset_s": onset,
              "front_at_crossing_m": outcome.get("front_gap_at_crossing_m"),
              "normal_approach": na.get("normal"), "normal_reason": na.get("reason"),
              "win_speed_min_kmh": na.get("speed_min_kmh"), "win_speed_max_kmh": na.get("speed_max_kmh"),
              "win_speed_range_kmh": na.get("speed_range_kmh")})
    if errors:
        return _as_data_error(o, errors), []
    rows = telemetry_rows(tel)[0]
    o["t_end_s"] = rows[-1]["sim_time"]
    exec_ok = bool(applied) and sc.get("verdict") != "INVALID" and outcome.get("terminated_reason") in (
        "approach_post_signal_observation_complete", "approach_time_limit_reference_not_reached")
    data_ok = True
    mv = movement(rows)
    o.update(mv)
    o["approach_state"] = classify_state(exec_ok, mv, tc, na.get("normal"))
    o["data_valid"] = data_ok
    o["data_issues"] = ""
    o["data_error_list"] = []
    o.update(time_gaps(rows))
    if not exec_ok or tc is None:
        o["front_at_end_m"] = round(rows[-1]["stopline_front_m"], 3)
        return o, []
    t12 = tc + DIAG_WINDOW_S
    o["speed_at_ref_kmh"] = at(rows, tc, "ego_speed_kmh")
    o.update(brake_timing(rows, tc))
    o.update(decel(rows, tc, rows[-1]["sim_time"]))  # search range [reference, episode end], same as the old analysis
    o["first_below_1kmh_within_ref12_s"] = first_t([r for r in rows if r["sim_time"] <= t12 + 1e-9], tc,
                                                   lambda r: r["ego_speed_kmh"] < LOW_SPEED_KMH)
    hw = held_window(rows, tc, t12)
    o["held_stop_start_s"] = hw[0] if hw else None
    o["held_stop_window_end_s"] = hw[1] if hw else None
    o["held_stop_front_m"] = round(at(rows, hw[0], "stopline_front_m"), 3) if hw else None
    o["held_stop_start_s_legacy_fn"] = held_legacy(rows, tc, t12)[0]
    low = o["first_below_1kmh_within_ref12_s"]
    o["redeparture_after_low_s"] = first_t(rows, low, lambda r: r["ego_speed_kmh"] >= REDEPART_KMH) if low is not None else None
    win = [r for r in rows if tc - 1e-9 <= r["sim_time"] <= t12 + 1e-9]
    o["min_front_ref_to_ref12_m"] = round(min(r["stopline_front_m"] for r in win), 3)
    o["t_line_cross_s"] = first_t(rows, tc, lambda r: r["stopline_front_m"] < 0)
    o["front_at_end_m"] = round(rows[-1]["stopline_front_m"], 3)
    o["speed_at_end_kmh"] = round(rows[-1]["ego_speed_kmh"], 3)
    o["collision_count_max"] = int(max((num(r.get("collision_count")) or 0) for r in rows))
    a0, a1 = officer_active_span(rows)
    o.update({"officer_active_first_s": a0, "officer_active_last_s": a1,
              "officer_active_last_minus_onset_s": None if (a1 is None or onset is None) else round(a1 - onset, 4),
              "scored_onset_s": aw.get("onset_s"), "scored_active_end_s": aw.get("active_end_s"),
              "enforcement_start_s": (sc.get("evidence") or {}).get("enforcement_start_s"),
              "scored_horizon_s": sc.get("horizon_s")})
    o.update(strict_diagnostic(rows, sc))
    q = vlm_queries(ep, rows, events, onset, tc)
    if q:
        post = [x for x in q if x["phase"] == "post"]
        o["vlm_pre_signal_nonGO_ticks"] = " ".join(f"{x['output_tick_s']}:{x['action']}" for x in q
                                                    if x["phase"] == "pre" and str(x["action"]).upper() != "GO")
        for k, x in enumerate(post[:2], 1):
            o[f"vlm_post{k}_output_s"] = x["output_tick_s"]
            o[f"vlm_post{k}_input_frame_s"] = x["input_frame_sim_s"]
            o[f"vlm_post{k}_action"] = x["action"]
            o[f"vlm_post{k}_who"] = x["who"]
            o[f"vlm_post{k}_front_m"] = x["front_at_output_m"]
        lat = [x["wall_latency_s"] for x in post if x["wall_latency_s"] is not None and x["output_tick_s"] <= t12]
        o["vlm_post_wall_latency_min_s"] = min(lat) if lat else None
        o["vlm_post_wall_latency_max_s"] = max(lat) if lat else None
    for x in q:
        x.update({"condition": cond, "model": model, "seed": seed, "signal_onset_s": onset, "reference_crossed_s": tc})
    return o, q


def stationary_jitter(all_rows):
    """max |front(t) - front(t-1)| over consecutive samples where both speeds < 0.1 km/h (truly standing)."""
    m = 0.0
    for rows in all_rows:
        for a, b in zip(rows, rows[1:]):
            va, vb = num(a.get("ego_speed_kmh")), num(b.get("ego_speed_kmh"))
            if va is not None and vb is not None and va < 0.1 and vb < 0.1:
                fa, fb = num(a.get("stopline_front_m")), num(b.get("stopline_front_m"))
                if fa is not None and fb is not None:
                    m = max(m, abs(fb - fa))
    return round(m, 5)


def write_csv(path: Path, recs):
    keys = []
    for r in recs:
        keys += [k for k in r if k not in keys]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(recs)


def main() -> int:
    """Exit 0 when every run passed the data check, 1 when any run is a data error (the tables are still written,
    with those runs as '1 execution/data error', and every defect is listed in data_errors.jsonl)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root, out = Path(a.root), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    recs, queries, valid_rows, err_lines = [], [], [], []
    for ep in sorted(root.glob("*/*/s*/*green_stop")):
        r, q = analyse(ep, ep.parts[-4], ep.parts[-3], ep.parts[-2])
        for e in r.pop("data_error_list", []):
            err_lines.append(json.dumps({"condition": r["condition"], "model": r["model"], "seed": r["seed"],
                                         "episode": r["episode"], **e}, ensure_ascii=False))
        recs.append(r)
        queries += q
        if r.get("data_valid") is True:
            valid_rows.append(rows_of(ep))
    write_csv(out / "runs_corrected.csv", recs)
    write_csv(out / "vlm_queries_corrected.csv", queries)
    (out / "data_errors.jsonl").write_text("".join(x + "\n" for x in err_lines), encoding="utf-8")
    jit = stationary_jitter(valid_rows)
    (out / "_jitter.json").write_text(json.dumps({"stationary_jitter_max_m": jit}), encoding="utf-8")
    bad = [f"{r['condition']}/{r['model']}/{r['seed']}" for r in recs if r.get("data_valid") is not True]
    print(len(recs), "runs;", len(queries), "VLM queries;", "stationary jitter", jit, "; data-error runs:", len(bad), bad[:10])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
