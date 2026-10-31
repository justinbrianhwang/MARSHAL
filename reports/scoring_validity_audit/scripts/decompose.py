"""Episode manifest + full score decomposition for the Protocol v2 leaderboard (audit, read-only).

Run from the workspace root (the directory holding scripts/, marshal_bench/ and tmp/_protocol_v2):

    python <audit>/scripts/decompose.py --runs tmp/_protocol_v2 --out <audit>/data

It calls the published collector's own `score_episode` (scripts/collect_protocol_v2.py) and captures the
strict and graded verdict dicts it computes, so every number here comes from the pinned scorers, not from a
re-implementation. Raw telemetry is only read. Outputs:

    baseline_manifest.csv      one row per episode directory found (cohort, duplicates, hashes, acceptance)
    episode_decomposition.csv  one row per accepted episode: strict, every graded component, motion and control
    partial_credit_episodes.csv  the 0 < credit < 1 subset
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import published_v2  # noqa: E402,F401  (pins the published v2 graded scorer; see published_v2.py)
import collect_protocol_v2 as C  # noqa: E402  (the published collector)
import _run_vlm_test as vlm  # noqa: E402  (scenario registry)
from marshal_bench.criteria import strict_episode_scoring as S  # noqa: E402

JITTER_KMH, JITTER_M = 0.5, 0.2  # "no meaningful motion" tolerance (simulation jitter)
EPS = 1e-6  # endpoint tolerance on credit (stored credit is rounded to 6 decimals)

_captured = {}
_orig_s, _orig_g = C.S.score_episode_from_telemetry, C.G.score_episode_from_telemetry


def _cap_s(*a, **k):
    _captured["s"] = r = _orig_s(*a, **k)
    return r


def _cap_g(*a, **k):
    _captured["g"] = r = _orig_g(*a, **k)
    return r


C.S.score_episode_from_telemetry, C.G.score_episode_from_telemetry = _cap_s, _cap_g


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else ""


def rows_of(ep: Path):
    t = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
    return t.get("telemetry") if isinstance(t, dict) else t


def f(r, k):
    try:
        v = float(r.get(k))
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def motion(rows, t_end=None):
    """Kinematic summary over rows with sim_time <= t_end (None = whole 25 s horizon)."""
    rs = [r for r in rows if float(r["sim_time"]) <= S.PROTOCOL_V2["horizon_s"] + 1e-9
          and (t_end is None or float(r["sim_time"]) <= t_end + 1e-9)]
    v = [f(r, "ego_speed_kmh") or 0.0 for r in rs]
    x = [f(r, "ego_forward_m") for r in rs]
    x = [a for a in x if a is not None]
    y = [abs(f(r, "ego_lateral_m") or 0.0) for r in rs]
    first = lambda thr: next((float(r["sim_time"]) for r, s in zip(rs, v) if s >= thr), None)
    return {"max_v": max(v, default=0.0), "progress": (max(x) - min(x)) if x else 0.0,
            "net_fwd": (x[-1] - x[0]) if x else 0.0, "min_fwd_rel": (min(x) - x[0]) if x else 0.0,
            "max_abs_lat": max(y, default=0.0), "t_v05": first(0.5), "t_v1": first(1.0), "t_v5": first(5.0)}


def control(rows):
    g = lambda k: [f(r, k) or 0.0 for r in rows]
    rt, at, rb, ab = g("raw_throttle"), g("applied_throttle"), g("raw_brake"), g("applied_brake")
    n = max(len(rows), 1)
    return {"max_raw_throttle": max(rt, default=0.0), "max_applied_throttle": max(at, default=0.0),
            "frac_raw_brake_ge05": sum(b >= 0.5 for b in rb) / n,
            "frac_applied_brake_ge05": sum(b >= 0.5 for b in ab) / n,
            "ticks_raw_ne_applied": sum(abs(a - b) > 1e-3 or abs(c - d) > 1e-3 for a, b, c, d in zip(rt, at, rb, ab)),
            "ticks_throttle_and_brake": sum(a > 0.1 and b > 0.1 for a, b in zip(at, ab)),
            "hand_brake_ticks": sum(bool(r.get("applied_hand_brake")) for r in rows),
            "gears": ";".join(sorted({str(r.get("applied_gear")) for r in rows})),
            "ticks_throttle_no_motion": sum(a >= 0.3 and (f(r, "ego_speed_kmh") or 0) < 0.5 and b < 0.1
                                            for a, b, r in zip(at, ab, rows)),
            "max_contiguous_drive_s": round(0.05 * longest_run([a >= 0.3 and b < 0.1 for a, b in zip(at, ab)]), 2),
            # applied control at tick t equals the requested control at tick t-1 (one-tick actuation lag)
            "frac_applied_eq_prev_raw": round(sum(abs(at[i] - rt[i - 1]) < 1e-3 and abs(ab[i] - rb[i - 1]) < 1e-3
                                                  for i in range(1, len(rows))) / max(len(rows) - 1, 1), 4),
            "frac_applied_eq_same_raw": round(sum(abs(at[i] - rt[i]) < 1e-3 and abs(ab[i] - rb[i]) < 1e-3
                                                  for i in range(len(rows))) / n, 4)}


def longest_run(flags):
    best = cur = 0
    for x in flags:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def motion_class(expected, valid, win, whole, ctl):
    """Exclusive motion label (documented order). Window = the graded movement-gate window."""
    if not valid:
        return "invalid_telemetry"
    if whole["max_v"] < JITTER_KMH and whole["progress"] < JITTER_M:
        base = "no_meaningful_motion"
        if ctl["max_raw_throttle"] < 0.05:
            return base + ":no_throttle_requested"
        if ctl["max_applied_throttle"] < 0.05:
            return base + ":throttle_requested_not_applied"
        if ctl["max_contiguous_drive_s"] < 0.5:
            return base + ":brief_throttle_pulses_then_brake"
        return base + ":sustained_throttle_no_response"
    gate = win["max_v"] >= 5.0 and win["progress"] >= 1.0
    if gate:
        if win["net_fwd"] < -0.5:
            return "gate_met_but_net_reverse"
        return "gate_met"
    if whole["max_v"] >= 5.0 and whole["progress"] >= 1.0:
        return "motion_only_outside_window"
    if win["net_fwd"] < -0.5 or (win["progress"] >= 1.0 and win["max_abs_lat"] > win["progress"]):
        return "reverse_or_lateral_motion"
    if win["max_v"] < 5.0 and win["progress"] >= 1.0:
        return "motion_below_speed_threshold"
    if win["max_v"] >= 5.0 and win["progress"] < 1.0:
        return "motion_below_progress_threshold"
    return "motion_below_both_thresholds"


def vlm_trace(ep: Path):
    p = ep / "vlm_debug" / "vlm_trace.csv"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as fh:
        q = [r for r in csv.DictReader(fh) if r.get("queried_this_tick") == "True"]
    ev = json.loads((ep / "events.json").read_text(encoding="utf-8")) if (ep / "events.json").exists() else []
    dec = [e["payload"] for e in ev if e.get("name") == "vlm_decision"]
    summ = next((e["payload"] for e in ev if e.get("name") == "vlm_query_summary"), {})
    acts = [d.get("action") for d in dec]
    return {"queries": len(q), "query_errors": summ.get("n_errors"),
            "actions": "".join((a or "?")[0] for a in acts),
            "n_go": sum(a == "GO" for a in acts), "n_stop_hold": sum(a in ("STOP", "HOLD") for a in acts),
            "n_slow": sum(a == "SLOW" for a in acts),
            "n_unparsed": sum(d.get("parse_source") not in ("content", "reasoning", "json") for d in dec),
            "first_action": acts[0] if acts else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="tmp/_protocol_v2")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = Path(a.runs)
    manifest, decomp = [], []
    for model_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        base, level, period, capped = C._cell(model_dir.name)
        cohort = "query_cap_comparison" if capped else ("ablation" if level else "leaderboard")
        for seed_dir in sorted(p for p in model_dir.iterdir() if p.is_dir() and p.name.startswith("s")):
            seed = int(seed_dir.name[1:])
            eps = [p for p in seed_dir.iterdir() if p.is_dir() and (p / "strict_telemetry.json").exists()]
            for sc in vlm.SCENARIO_ORDER:
                cands = sorted(p for p in eps if p.name.endswith(sc) or p.name.rsplit("_s", 1)[0].endswith(sc))
                chosen = C.find_episode(seed_dir, sc)
                for p in cands or ([None] if cohort == "leaderboard" else []):
                    manifest.append({"episode_id": f"{model_dir.name}/s{seed}/{sc}", "cohort": cohort,
                                     "model": base, "seed": seed, "scenario": sc,
                                     "episode_dir": str(p.relative_to(runs)) if p else "",
                                     "selected_by_collector": bool(p and chosen and p.resolve() == chosen.resolve()),
                                     "n_candidate_dirs": len(cands),
                                     "telemetry_sha256_16": sha(p / "strict_telemetry.json") if p else "",
                                     "has_metadata": bool(p and (p / "metadata.json").exists())})
                if chosen is None:
                    continue
                _captured.clear()
                r = C.score_episode(chosen, sc, vlm.SCENARIOS, is_vlm=base in C.VLM_MODELS, is_cap=capped,
                                    period=period)
                manifest[-1]["accepted"] = r.get("accepted")
                manifest[-1]["refusal_reason"] = r.get("reason", "")
                if not r.get("accepted"):
                    continue
                s, g = _captured.get("s", {}), _captured.get("g", {})
                rows = rows_of(chosen)
                exp = vlm.SCENARIOS[sc]["expect"]
                ge = g.get("evidence") or {}
                win_end = (ge.get("active_window") or {}).get("active_end_s") if exp in ("STOP", "HOLD") else None
                win, whole, ctl = motion(rows, win_end), motion(rows), control(rows)
                lat, saf, mv, eng = ge.get("latency") or {}, ge.get("safety") or {}, ge.get("movement_gate") or {}, ge.get("engagement") or {}
                cr = float(g.get("credit") or 0.0) if r["strict"] != "INVALID" else 0.0
                rec = {"episode_id": f"{model_dir.name}/s{seed}/{sc}", "cohort": cohort, "model": base, "seed": seed,
                       "scenario": sc, "expected_action": exp, "weight": C.G.SCENARIO_AUTHORITY_WEIGHTS[sc],
                       "strict": r["strict"], "strict_reason": r["strict_reason"],
                       "credit": cr, "credit_class": "full" if cr >= 1 - EPS else ("zero" if cr <= EPS else "partial"),
                       "graded_invalid": bool(g.get("invalid")),
                       "action_credit": g.get("raw_action_credit"), "latency_factor": g.get("latency_factor"),
                       "safety_factor": g.get("safety_factor"), "movement_gate": int(bool(mv.get("engaged"))),
                       "latency_s": lat.get("latency_s"), "latency_trigger": lat.get("trigger"),
                       "collision_count": saf.get("collision_count"),
                       "components": json.dumps(g.get("component_credits") or {}, sort_keys=True),
                       "engagement_factor": eng.get("factor"),
                       "onset_s": (ge.get("active_window") or {}).get("onset_s"),
                       "active_end_s": (ge.get("active_window") or {}).get("active_end_s"),
                       "gate_max_v_kmh": mv.get("max_speed_kmh"), "gate_progress_m": mv.get("forward_progress_m"),
                       "graded_reason": g.get("reason"), "curve": ge.get("curve"),
                       "min_front_to_stopline_m": ge.get("min_front_to_stopline_m"),
                       "max_scored_speed_kmh": ge.get("max_scored_speed_kmh"),
                       "entered_junction": ge.get("entered_junction"),
                       "failure_class": r.get("failure_class"),
                       **{f"win_{k}": v for k, v in win.items()}, **{f"all_{k}": v for k, v in whole.items()},
                       **ctl, **{f"vlm_{k}": v for k, v in vlm_trace(chosen).items()}}
                rec["motion_class"] = motion_class(exp, not rec["graded_invalid"], win, whole, ctl)
                decomp.append(rec)
    keys = sorted({k for r in decomp for k in r}, key=lambda k: list(decomp[0]).index(k) if k in decomp[0] else 999)
    for name, rows in (("baseline_manifest.csv", manifest), ("episode_decomposition.csv", decomp),
                       ("partial_credit_episodes.csv", [r for r in decomp if r["credit_class"] == "partial"
                                                        and r["cohort"] == "leaderboard"])):
        ks = keys if name != "baseline_manifest.csv" else sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 999)
        with (out / name).open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=ks, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    lb = [r for r in decomp if r["cohort"] == "leaderboard"]
    print(f"manifest rows {len(manifest)}; accepted {len(decomp)} (leaderboard {len(lb)}); "
          f"partial {sum(r['credit_class'] == 'partial' for r in lb)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
