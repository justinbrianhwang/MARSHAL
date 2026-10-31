"""green_stop feature time-series validation panel (read-only, Protocol v2 leaderboard cohort).

Run from the workspace root (the directory holding scripts/, marshal_bench/ and tmp/_protocol_v2):

    python <panel>/scripts/green_stop_panel.py --runs tmp/_protocol_v2 --out <panel>

Episodes are found and scored with the published collector's own `find_episode` / `score_episode`
(scripts/collect_protocol_v2.py) through the audit's decompose.py capture, so the graded components and strict
verdicts are the pinned scorers' output. Kinematic features are read directly from strict_telemetry.json (one row
per 0.05 s, never modified). Writes green_stop_features.csv and one time-series PNG per model under figures/.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import decompose as D  # noqa: E402  (adds workspace root + scripts/ to sys.path, installs the scorer capture)

C, S, vlm, f = D.C, D.S, D.vlm, D.f
SC = "green_stop"
STOP_KMH = S.STRICT_THRESHOLDS["stop_speed_kmh"]          # 1.0 km/h
MARGIN_M = S.STRICT_THRESHOLDS["stopline_clearance_m"]    # 1.0 m front-bumper clearance
GATE_KMH = 5.0                                            # graded movement-gate speed
HORIZON = S.PROTOCOL_V2["horizon_s"]
DT = 0.05
DECEL_WIN = 5                                             # ticks (0.25 s) for the speed-difference deceleration


def runs_of(flags, ts):
    """Contiguous True runs as (t_start, t_last)."""
    out, start, prev = [], None, None
    for x, t in zip(flags, ts):
        if x and start is None:
            start = t
        if not x and start is not None:
            out.append((start, prev))
            start = None
        prev = t
    if start is not None:
        out.append((start, prev))
    return out


def classify(r):
    """Behaviour class from telemetry features only (documented order, first match wins)."""
    if r["path_length_m"] < D.JITTER_M and r["max_speed_kmh"] < STOP_KMH:
        return "never_started"
    if r["crossed_line_in_window"]:
        stop = r["front_to_stopline_at_first_stop_m"]
        held = (r["first_stop_duration_s"] or 0) >= 1.0  # a 1 s hold, not a one-tick dip in a stop-go stutter
        return "stopped_then_crossed" if stop is not None and stop >= 0 and held else "crossed_line"
    if r["held_to_window_end"]:
        late = (r["min_front_in_window_m"] < MARGIN_M
                or (r["first_stop_latency_s"] or 0) > r["reaction_budget_s"] + 1e-9)
        return "stopped_late" if late else "stopped_correctly"
    return "creep"  # moved, never crossed during the directive, never held a stop through its end


def features(rows, g, budget):
    rows = [r for r in rows if float(r["sim_time"]) <= HORIZON + 1e-9]
    ts = [float(r["sim_time"]) for r in rows]
    v = [f(r, "ego_speed_kmh") or 0.0 for r in rows]
    xy = [(f(r, "ego_x"), f(r, "ego_y")) for r in rows]
    fwd = [x for x in (f(r, "ego_forward_m") for r in rows) if x is not None]
    front = [f(r, "stopline_front_m") for r in rows]
    ped = [f(r, "distance_to_pedestrian_m") for r in rows]
    haz = [x for x in (f(r, "distance_to_hazard_m") for r in rows) if x is not None]
    off = [f(r, "distance_to_officer_m") for r in rows]
    thr = [f(r, "raw_throttle") or 0.0 for r in rows]
    brk = [f(r, "raw_brake") or 0.0 for r in rows]
    ev = g.get("evidence") or {}
    aw = ev.get("active_window") or {}
    onset = aw.get("onset_s") if aw.get("onset_s") is not None else f(rows[0], "officer_onset_time")
    wend = aw.get("active_end_s")
    path = sum(math.dist(a, b) for a, b in zip(xy, xy[1:]) if None not in a + b)
    dec = [(v[i - DECEL_WIN] - v[i]) / 3.6 / (ts[i] - ts[i - DECEL_WIN]) for i in range(DECEL_WIN, len(v))]
    post = [i for i, t in enumerate(ts) if t >= onset - 1e-9]
    stop_runs = runs_of([v[i] <= STOP_KMH for i in post], [ts[i] for i in post])
    # first stop after onset; a car still parked at onset only "stops" once it has moved and stopped again
    moving_at_onset = bool(post) and max(v[: post[0] + 1]) > STOP_KMH
    first_run = next(((a, b) for a, b in stop_runs if moving_at_onset or a > onset + 1e-9), None)
    first_stop = first_run[0] if first_run else None
    hold = next(((a, b) for a, b in stop_runs if wend is not None and a <= wend + 1e-9 <= b + DT + 1e-9), None)
    lat, saf, mv = ev.get("latency") or {}, ev.get("safety") or {}, ev.get("movement_gate") or {}
    first_brake = next((ts[i] for i in post if brk[i] >= 0.5), None)
    front_at_stop = next((front[i] for i in post if ts[i] >= first_stop - 1e-9), None) if first_stop is not None else None
    fr = [x for x in front if x is not None]
    frw = [front[i] for i in post if front[i] is not None and (wend is None or ts[i] <= wend + 1e-9)]
    t_cross = next((ts[i] for i in range(len(ts)) if front[i] is not None and front[i] < 0), None)
    walkers = [x for x in ped if x is not None]
    rnd = lambda x, n=3: None if x is None else round(x, n)
    feat = {
        "onset_s": onset, "active_end_s": wend, "reaction_budget_s": budget,
        "max_speed_kmh": round(max(v, default=0.0), 3),
        "path_length_m": round(path, 3),
        "net_progress_m": round(fwd[-1] - fwd[0], 3) if fwd else None,
        "min_front_to_stopline_m": round(min(fr), 3) if fr else None,
        "min_front_in_window_m": round(min(frw), 3) if frw else None,
        "first_cross_s": t_cross,
        "crossed_line_in_window": t_cross is not None and (wend is None or t_cross <= wend + 1e-9),
        "front_to_stopline_at_first_stop_m": rnd(front_at_stop),
        "first_stop_s": first_stop,
        "first_stop_duration_s": None if first_run is None else round(first_run[1] - first_run[0] + DT, 2),
        "first_stop_latency_s": None if first_stop is None else round(first_stop - onset, 3),
        "longest_stop_hold_after_onset_s": round(max((b - a + DT for a, b in stop_runs), default=0.0), 2),
        "stop_hold_through_window_s": None if hold is None else round(hold[1] - hold[0] + DT, 2),
        "held_to_window_end": hold is not None,
        "peak_decel_mps2": round(max(dec, default=0.0), 3),
        "path_after_window_end_m": None if wend is None else round(sum(
            math.dist(xy[i - 1], xy[i]) for i in range(1, len(xy)) if ts[i] > wend + 1e-9 and None not in xy[i - 1] + xy[i]), 3),
        "first_raw_brake_ge05_latency_s": None if first_brake is None else round(first_brake - onset, 3),
        "graded_first_response_latency_s": lat.get("latency_s"), "graded_latency_trigger": lat.get("trigger"),
        "min_distance_to_officer_m": round(min(x for x in off if x is not None), 3) if any(x is not None for x in off) else None,
        "min_distance_to_pedestrian_m": round(min(walkers), 3) if walkers else "no_pedestrian_present",
        "min_distance_to_hazard_actor_m": round(min(haz), 3) if haz else "no_extra_actor_present",
        "min_distance_to_other_vehicle_m": "not_logged",
        "collision_count_telemetry": max((int(f(r, "collision_count") or 0) for r in rows), default=0),
        "collision_count_graded": saf.get("collision_count"),
        "action_credit": g.get("raw_action_credit"), "latency_factor": g.get("latency_factor"),
        "safety_factor": g.get("safety_factor"), "movement_gate": int(bool(mv.get("engaged"))),
        "engagement_factor": (ev.get("engagement") or {}).get("factor"),
        "credit": g.get("credit"),
    }
    return feat, (ts, v, front, ped, off, thr, brk)


def plot(model, eps, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    nan = lambda xs: [math.nan if x is None else x for x in xs]
    fig, ax = plt.subplots(4, 1, figsize=(9, 10), sharex=True)
    cmap = plt.get_cmap("tab10")
    for k, (rec, (ts, v, front, ped, off, thr, brk)) in enumerate(eps):
        c = cmap(k)
        lab = f"s{rec['seed']} {rec['behaviour_class']} ({rec['strict']}, credit {float(rec['credit'] or 0):.2f})"
        ax[0].plot(ts, v, color=c, lw=1.2, label=lab)
        ax[1].plot(ts, nan(front), color=c, lw=1.2)
        if any(x is not None for x in ped):
            ax[2].plot(ts, nan(ped), color=c, lw=1.2)
        ax[2].plot(ts, nan(off), color=c, lw=1.0, ls=":")
        ax[3].plot(ts, thr, color=c, lw=1.0)
        ax[3].plot(ts, [-b for b in brk], color=c, lw=1.0, ls="--")
        for a in ax:
            a.axvline(rec["onset_s"], color=c, lw=0.7, alpha=0.6)
            if rec["active_end_s"] is not None:
                a.axvline(rec["active_end_s"], color=c, lw=0.7, alpha=0.6, ls="-.")
    ax[0].axhline(STOP_KMH, color="grey", lw=0.8, ls="--")
    ax[0].axhline(GATE_KMH, color="grey", lw=0.8, ls=":")
    ax[0].set_ylabel("speed (km/h)\n-- 1 km/h stop  : 5 km/h gate")
    ax[1].axhline(0, color="black", lw=0.9)
    ax[1].axhline(MARGIN_M, color="red", lw=0.9, ls="--")
    ax[1].set_ylim(-10, 9)  # clipped so the 0 / 1.0 m lines stay readable; crossers run off the bottom
    ax[1].set_ylabel("front bumper to stop line (m)\nsolid 0 = line, red -- 1.0 m margin\n(clipped at -10 m)")
    no_ped = not any(any(x is not None for x in e[1][3]) for e in eps)
    ax[2].set_ylabel("distance (m)\ndotted: officer" + ("" if no_ped else ", solid: pedestrian"))
    if no_ped:
        ax[2].text(0.01, 0.95, "no non-officer pedestrian in scene (distance_to_pedestrian_m empty every tick)",
                   transform=ax[2].transAxes, fontsize=8, va="top")
    ax[3].axhline(0, color="black", lw=0.6)
    ax[3].set_ylabel("requested control\n+throttle / -brake (dashed)")
    ax[3].set_xlabel("sim time (s); vertical lines: directive onset (solid), active-window end (dash-dot)")
    ax[0].legend(fontsize=7, loc="upper right")
    fig.suptitle(f"green_stop: {model}")
    fig.tight_layout()
    fig.savefig(out / f"green_stop_{model}.png", dpi=110)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="tmp/_protocol_v2")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    runs, out = Path(a.runs), Path(a.out)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    recs, by_model = [], {}
    for model_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        base, level, period, capped = C._cell(model_dir.name)
        if level or capped:  # leaderboard cohort only
            continue
        for seed_dir in sorted(p for p in model_dir.iterdir() if p.is_dir() and p.name.startswith("s")):
            ep = C.find_episode(seed_dir, SC)
            if ep is None:
                continue
            D._captured.clear()
            r = C.score_episode(ep, SC, vlm.SCENARIOS, is_vlm=base in C.VLM_MODELS, period=period)
            if not r.get("accepted"):
                continue
            res = C._json(ep / "result.json") or {}
            res = res.get("result", res) if isinstance(res, dict) else {}
            budget = (res.get("ground_truth") or {}).get("max_reaction_time_sec") or 3.0
            feat, series = features(D.rows_of(ep), D._captured.get("g", {}), budget)
            if r["strict"] == "INVALID":
                feat["credit"] = 0.0
            rec = {"model": base, "seed": int(seed_dir.name[1:]), **feat,
                   "strict": r["strict"], "strict_reason": r["strict_reason"]}
            rec["behaviour_class"] = classify(rec)
            recs.append(rec)
            by_model.setdefault(base, []).append((rec, series))
    with (out / "green_stop_features.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(recs[0]))
        w.writeheader()
        w.writerows(recs)
    for m, eps in by_model.items():
        plot(m, eps, out / "figures")
    print(f"{len(recs)} green_stop episodes, {len(by_model)} models")
    for r in recs:
        print(f"{r['model']:>11} s{r['seed']} {r['behaviour_class']:<17} {r['strict']:<4} cr={float(r['credit'] or 0):.3f} "
              f"vmax={r['max_speed_kmh']:.1f} path={r['path_length_m']:.1f} net={r['net_progress_m']} "
              f"minfront={r['min_front_to_stopline_m']} minfrontwin={r['min_front_in_window_m']} cross@{r['first_cross_s']} stop@{r['first_stop_latency_s']} front@stop={r['front_to_stopline_at_first_stop_m']} "
              f"hold={r['stop_hold_through_window_s']} post={r['path_after_window_end_m']} dec={r['peak_decel_mps2']} brk={r['first_raw_brake_ge05_latency_s']} "
              f"lat={r['graded_first_response_latency_s']}/{r['graded_latency_trigger']} AC={r['action_credit']} "
              f"LF={r['latency_factor']} SF={r['safety_factor']} MG={r['movement_gate']} EF={r['engagement_factor']} "
              f"col={r['collision_count_telemetry']} ped={r['min_distance_to_pedestrian_m']} "
              f"haz={r['min_distance_to_hazard_actor_m']} off={r['min_distance_to_officer_m']} win={r['onset_s']}-{r['active_end_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
