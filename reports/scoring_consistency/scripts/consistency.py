"""Cross-scenario scoring consistency audit (read-only over existing results).

Inputs (never modified):
  reports/protocol_v2/data/cells_v2.csv               official v2 cells (Strict, Graded credit + components)
  tmp/_protocol_v2/<model>/s<seed>/<ep>/        raw episodes (strict_telemetry.json)
  tmp/_remeasure_interfuser/final_forward/interfuser/ raw InterFuser final runs (the leaderboard InterFuser rows)
  reports/scoring_validity_audit/credit_quality/data/qc_episodes.csv   existing Q_cont values (392 STOP/HOLD)

Behaviour descriptors are computed from telemetry with the scorer's own thresholds (1 km/h, 1 m margin, junction
row with front < 0). They are therefore NOT an independent judgement of performance; they only restate what the
episode did in physical units so that equal credits can be compared across scenarios.

usage (from the repository root):  python -I reports/scoring_consistency/scripts/consistency.py
"""
import csv, json, statistics as st, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path.cwd()
DEV = ROOT
OUT = ROOT / "reports" / "scoring_consistency" / "data"
OUT.mkdir(parents=True, exist_ok=True)
STOP_LINE = {"STOP", "HOLD"}


def ep_dir(row):
    p = Path(row["episode"])
    if row["model"] == "interfuser":
        return ROOT / "tmp" / "_remeasure_interfuser" / "final_forward" / "interfuser" / p.parts[-2] / p.parts[-1]
    return DEV / p


def describe(row):
    """Physical behaviour summary of one episode from strict_telemetry.json (25 s horizon)."""
    d = ep_dir(row)
    f = d / "strict_telemetry.json"
    if not f.exists():
        return {"telemetry_found": False}
    j = json.loads(f.read_text(encoding="utf-8"))
    rows = [r for r in j["telemetry"] if r.get("sim_time") is not None and r["sim_time"] <= 25.0 + 1e-9]
    ss = j.get("strict_scoring") or {}
    aw = ss.get("active_window") or {}
    ev = ss.get("evidence") or {}
    onset = aw.get("onset_s")
    end = aw.get("active_end_s")
    budget = ev.get("reaction_budget_s", 3.0)
    out = {"telemetry_found": True, "onset_s": onset, "active_end_s": end,
           "max_speed_kmh": round(max((r["ego_speed_kmh"] or 0) for r in rows), 2),
           "collision_count_max": max((r.get("collision_count") or 0) for r in rows),
           "distinct_collision_partners": len({c.get("other_actor_id", c.get("other_id", i))
                                               for i, c in enumerate(j["metadata"].get("collisions") or [])})}
    xs = [r["ego_forward_m"] for r in rows if r.get("ego_forward_m") is not None]
    out["forward_progress_m"] = round(max(xs) - min(xs), 2) if xs else None
    lat = [abs(r["ego_lateral_m"]) for r in rows if r.get("ego_lateral_m") is not None and onset is not None and r["sim_time"] >= onset]
    out["max_abs_lateral_after_onset_m"] = round(max(lat), 2) if lat else None
    if onset is None:
        return out
    win = [r for r in rows if onset <= r["sim_time"] <= (end if end is not None else 25.0)]
    enf = [r for r in win if r["sim_time"] >= onset + budget]
    fr = [r["stopline_front_m"] for r in win if r.get("stopline_front_m") is not None]
    out["min_front_in_directive_window_m"] = round(min(fr), 2) if fr else None
    out["max_speed_enforcement_kmh"] = round(max(r["ego_speed_kmh"] for r in enf), 2) if enf else None
    out["speed_at_onset_kmh"] = round(win[0]["ego_speed_kmh"], 2) if win else None
    out["front_at_onset_m"] = round(win[0]["stopline_front_m"], 2) if win and win[0].get("stopline_front_m") is not None else None
    out["junction_entry_past_line_in_window"] = any(r.get("in_junction") and (r.get("stopline_front_m") is not None and r["stopline_front_m"] < 0) for r in win)
    # first held stop: 2.0 s with every tick <= 1 km/h (drift ignored here; descriptor only)
    held, run0 = None, None
    for r in win:
        if r["ego_speed_kmh"] <= 1.0:
            run0 = r["sim_time"] if run0 is None else run0
            if r["sim_time"] - run0 >= 2.0 - 1e-9 and held is None:
                held = run0
        else:
            run0 = None
    out["held_stop_start_s"] = held
    if held is not None:
        hr = [r for r in win if r["sim_time"] >= held]
        out["front_at_held_stop_m"] = round(hr[0]["stopline_front_m"], 2) if hr and hr[0].get("stopline_front_m") is not None else None
    return out


def outcome(row, b):
    """Telemetry outcome class against the scenario's own requirement (uses scorer thresholds)."""
    a = row["expected_action"]
    if not b.get("telemetry_found"):
        return "TELEMETRY_NOT_FOUND"
    if row["movement_gate"] == "0":
        return "NO_MOVEMENT"
    if a in STOP_LINE and b.get("min_front_in_directive_window_m") is not None:
        fm, vm = b["min_front_in_directive_window_m"], b.get("max_speed_enforcement_kmh")
        held = b.get("held_stop_start_s") is not None
        if fm >= 1.0 and held and vm is not None and vm < 1.0:
            return "STOP_HELD_BEFORE_MARGIN"
        if fm >= 1.0 and held:
            return "STOP_BEFORE_MARGIN_NOT_HELD_THROUGH_ENFORCEMENT"
        if fm >= 1.0:
            return "NO_HELD_STOP_UPSTREAM"
        if fm >= 0 and held:
            return "STOP_INSIDE_1M_MARGIN"
        fh = b.get("front_at_held_stop_m")
        # held stop past the line (front in (-3, 0]) and no further advance of more than 0.5 m after it
        if held and fh is not None and -3 < fh <= 0 and fm >= fh - 0.5:
            return "STOP_PAST_LINE_LE_3M"
        if fm < -3:
            return "DROVE_THROUGH_LINE_GT_3M"
        return "CROSSED_LINE_LE_3M_NO_HOLD"
    return f"{a}_{row['strict']}"


def main():
    cells = list(csv.DictReader(open(ROOT / "reports/protocol_v2/data/cells_v2.csv", encoding="utf-8")))
    lb = [c for c in cells if not c["model"].endswith("_cap3")]
    assert len(lb) == 700, len(lb)
    rows = []
    for c in lb:
        b = describe(c)
        r = {k: c[k] for k in ("model", "seed", "scenario", "family", "expected_action", "weight", "strict", "strict_reason",
                               "credit", "action_credit", "latency_factor", "safety_factor", "movement_gate",
                               "failure_class", "episode")}
        r["credit_x100"] = round(100 * float(c["credit"]), 2)
        r.update(b)
        r["telemetry_outcome"] = outcome(c, b)
        rows.append(r)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(OUT / "episode_behaviour.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)

    # per scenario
    by = defaultdict(list)
    for r in rows:
        by[r["scenario"]].append(r)
    with open(OUT / "per_scenario.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "expected_action", "weight", "episodes", "strict_pass", "strict_fail", "not_scored",
                    "credit_min", "credit_median", "credit_max", "credit_eq_0", "credit_between", "credit_eq_1",
                    "first_zero_movement", "first_zero_action", "first_zero_latency", "first_zero_safety",
                    "latency_factor_eq_1_share", "safety_factor_eq_1_share", "telemetry_outcomes"])
        for sc, rs in by.items():
            cr = [float(r["credit"]) for r in rs]
            z = Counter()
            for r in rs:
                if float(r["credit"]) == 0:
                    z["mov" if r["movement_gate"] == "0" else "act" if float(r["action_credit"]) == 0
                      else "lat" if float(r["latency_factor"]) == 0 else "saf"] += 1
            w.writerow([sc, rs[0]["expected_action"], rs[0]["weight"], len(rs), sum(r["strict"] == "PASS" for r in rs),
                        sum(r["strict"] == "FAIL" for r in rs), 0, min(cr), round(st.median(cr), 4), max(cr),
                        sum(x == 0 for x in cr), sum(0 < x < 1 for x in cr), sum(x == 1 for x in cr),
                        z["mov"], z["act"], z["lat"], z["saf"],
                        round(sum(float(r["latency_factor"]) == 1 for r in rs) / len(rs), 3),
                        round(sum(float(r["safety_factor"]) == 1 for r in rs) / len(rs), 3),
                        "; ".join(f"{k}={v}" for k, v in Counter(r["telemetry_outcome"] for r in rs).most_common())])

    # score bands
    bands = {"20": (0.15, 0.25), "50": (0.45, 0.55), "80": (0.75, 0.85)}
    band_rows = []
    for name, (lo, hi) in bands.items():
        for r in rows:
            x = float(r["credit"])
            if lo <= x <= hi:
                band_rows.append({"band": name, **{k: r.get(k) for k in (
                    "scenario", "expected_action", "weight", "model", "seed", "strict", "strict_reason", "credit_x100",
                    "action_credit", "latency_factor", "safety_factor", "movement_gate", "telemetry_outcome",
                    "min_front_in_directive_window_m", "front_at_held_stop_m", "max_speed_enforcement_kmh",
                    "junction_entry_past_line_in_window", "held_stop_start_s", "distinct_collision_partners",
                    "max_abs_lateral_after_onset_m", "forward_progress_m", "failure_class", "episode")}})
    keys = list(band_rows[0])
    with open(OUT / "score_bands.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(band_rows)

    # verdict vs score
    deciles = defaultdict(Counter)
    for r in rows:
        x = float(r["credit"])
        k = "0" if x == 0 else "1" if x == 1 else f"({int(x*10)/10:.1f},{int(x*10)/10+0.1:.1f})"
        deciles[k][r["strict"]] += 1
    with open(OUT / "verdict_by_credit_bin.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["credit_bin", "PASS", "FAIL"])
        for k in sorted(deciles, key=lambda s: -1 if s == "0" else 2 if s == "1" else float(s[1:4])):
            w.writerow([k, deciles[k]["PASS"], deciles[k]["FAIL"]])
    summary = {
        "leaderboard_episodes": len(rows),
        "telemetry_found": sum(bool(r.get("telemetry_found")) for r in rows),
        "pass": sum(r["strict"] == "PASS" for r in rows),
        "pass_credit_lt_1": [(r["model"], r["seed"], r["scenario"], r["credit"]) for r in rows if r["strict"] == "PASS" and float(r["credit"]) < 1],
        "pass_credit_le_0p5": sum(r["strict"] == "PASS" and float(r["credit"]) <= 0.5 for r in rows),
        "fail_credit_ge_0p9": [(r["model"], r["seed"], r["scenario"], r["credit"], r["strict_reason"]) for r in rows if r["strict"] == "FAIL" and float(r["credit"]) >= 0.9],
        "fail_max_credit": max(float(r["credit"]) for r in rows if r["strict"] == "FAIL"),
        "pass_min_credit": min(float(r["credit"]) for r in rows if r["strict"] == "PASS"),
        "credit_eq_1_all_pass": all(r["strict"] == "PASS" for r in rows if float(r["credit"]) == 1),
        "fail_credit_eq_0": sum(r["strict"] == "FAIL" and float(r["credit"]) == 0 for r in rows),
        "fail_total": sum(r["strict"] == "FAIL" for r in rows),
        "band_counts": {b: Counter((x["strict"]) for x in band_rows if x["band"] == b) for b in bands},
        "band_scenarios": {b: Counter(x["scenario"] for x in band_rows if x["band"] == b) for b in bands},
        "band_outcomes": {b: Counter(x["telemetry_outcome"] for x in band_rows if x["band"] == b) for b in bands},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
