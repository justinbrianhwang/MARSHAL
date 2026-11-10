"""Compute MARSHAL Score (candidate) for the 700 protocol-v2 leaderboard episodes (offline, read-only).

Needs data/observables.jsonl from extract_observables.py (scorer evidence + reproduction check).
Writes data/marshal_score_700.csv. usage (from the repository root):
    python -I reports/marshal_score/scripts/compute_marshal_score.py
"""
import csv, json, sys
from pathlib import Path

ROOT = Path.cwd()
W = ROOT / "reports" / "marshal_score"
sys.path.insert(0, str(W)); sys.path.insert(0, str(ROOT))
import marshal_score as MS  # noqa: E402
from marshal_bench.criteria import strict_episode_scoring as S  # noqa: E402

PROCEED_PLAIN = {"red_proceed", "night_signal_officer_conflict", "out_of_jurisdiction_director"}
DETOUR = {"crash_detour", "civilian_warning_accident", "emergency_scene_blocking", "barricade_self_detour"}


def requirement_elements(scenario, action, rows, gev, budget, x_entry=None):
    aw = gev.get("active_window") or {}
    onset, end = float(aw.get("onset_s") or 0.0), float(aw.get("active_end_s") or 25.0)
    if scenario == "dual_authority_handoff":
        return "handoff", *MS.handoff(rows, onset, end, budget, MS.CFG["handoff_strict_requirement"])
    if scenario == "fallen_person":  # hazard-anchored stop-line fields, exactly as the Strict gate reads them
        return "stop_hazard", *MS.stop_hold(rows, onset, end, budget, boundary="stopline")
    if action in ("STOP", "HOLD"):
        return "stop_line", *MS.stop_hold(rows, onset, end, budget, boundary="stopline")
    if scenario in PROCEED_PLAIN:
        t_safe, _ = S.safe_to_proceed_time(rows, scenario, onset)
        return "proceed", *MS.proceed(rows, t_safe if t_safe is not None else onset, budget, x_entry)
    if scenario == "rule_hierarchy":
        t_safe, _ = S.safe_to_proceed_time(rows, scenario, onset)
        el, obs = MS.proceed_with_care(rows, onset, t_safe if t_safe is not None else onset,
                                       gev.get("min_speed_in_hazard_window_kmh"), budget, x_entry)
        if t_safe is None:
            el["T"] = None  # no start deadline exists when the pedestrian never cleared (Strict rule)
        return "proceed_with_care", el, {**obs, "t_safe_s": t_safe}
    if scenario == "stale_directive_residue":
        rel, _ = S.safe_to_proceed_time(rows, scenario, onset)
        el, obs = MS.proceed_after_release(rows, onset, rel if rel is not None else end, budget, x_entry)
        return "proceed_after_release", el, {**obs, "release_s": rel}
    if scenario in DETOUR:
        return "detour", *MS.detour(rows, onset, gev.get("obstacle_forward_m"), budget)
    if action == "YIELD":
        lat = gev.get("latency") or {}
        t_slow = onset + lat["latency_s"] if lat.get("latency_s") is not None else None
        return "yield", *MS.yield_(gev.get("max_speed_before_yield_kmh"), gev.get("min_active_speed_kmh"),
                                   gev.get("max_abs_lateral_after_yield_m"), gev.get("max_resume_speed_kmh"),
                                   t_slow, onset, budget)
    raise ValueError(scenario)


def junction_entry_points(recs):
    """Scenario geometry: route position of the junction entry (first in-junction row), median over every episode that
    reached it. Map property, the same for every model; spread is reported."""
    pts = {}
    for d, rows in recs:
        r = next((r for r in rows if r.get("in_junction") and r["sim_time"] > 0.5), None)
        if r is not None:
            pts.setdefault(d["scenario"], []).append(r["ego_forward_m"])
    import statistics as st
    return {s: {"x_entry_m": st.median(v), "n": len(v), "min": min(v), "max": max(v)} for s, v in pts.items()}


def contact_partners(d, tel, scored_end):
    """Distinct collision partners inside the scored rows. 0 whenever the benchmark scorer counts no contact there."""
    pub = ((d["graded"].get("evidence") or {}).get("safety") or {}).get("collision_count") or 0
    if not pub:
        return 0
    ids = {c.get("other_actor_id") for c in (tel["metadata"].get("collisions") or [])
           if c.get("sim_time") is not None and c["sim_time"] <= scored_end + 1e-9 and c.get("other_actor_id") is not None}
    return max(1, len(ids))


def main():
    qc = {(r["model"], r["seed"], r["scenario"]): r for r in csv.DictReader(open(
        ROOT / "reports/scoring_validity_audit/credit_quality/data/qc_episodes.csv", encoding="utf-8"))}
    recs = []
    for line in open(W / "data" / "observables.jsonl", encoding="utf-8"):
        d = json.loads(line)
        tel = json.loads((ROOT / d["episode_dir"] / "strict_telemetry.json").read_text(encoding="utf-8"))
        recs.append((d, [r for r in tel["telemetry"] if r["sim_time"] <= 25.0 + 1e-9], tel))
    geo = junction_entry_points([(d, rows) for d, rows, _ in recs])
    (W / "data" / "junction_entry_points.json").write_text(json.dumps(geo, indent=1), encoding="utf-8")
    out = []
    for d, rows, tel in recs:
        gev = d["graded"].get("evidence") or {}
        xe = (geo.get(d["scenario"]) or {}).get("x_entry_m")
        req, el, obs = requirement_elements(d["scenario"], d["expected_action"], rows, gev, float(d["budget_s"]), xe)
        aw = gev.get("active_window") or {}
        scored_end = float(aw.get("active_end_s") or 25.0) if d["expected_action"] in ("STOP", "HOLD") else 25.0
        k = contact_partners(d, tel, scored_end)
        sc = MS.combine(el, k, d["strict"]["verdict"])
        q = qc.get((d["model"], d["seed"], d["scenario"]))
        out.append({
            "model": d["model"], "seed": d["seed"], "scenario": d["scenario"], "expected_action": d["expected_action"],
            "requirement_type": req, "included": True, "strict_verdict": d["strict"]["verdict"],
            "strict_reason": d["strict"].get("reason"), "previous_graded_credit_x100": round(100 * float(d["cells_credit"]), 2),
            "previous_qcont_x100": round(100 * float(q["Q_cont"]), 1) if q else "",
            "marshal_score": sc["score"], "raw_before_caps": round(sc["raw"], 2), "lowest_cap": round(sc["cap"], 2),
            "binding_rule": sc["binding"],
            "R_core": round(el["R"], 4), "B_boundary": "" if el["B"] is None else round(el["B"], 4),
            "T_timing": "" if el["T"] is None else round(el["T"], 4), "P_persistence": "" if el["P"] is None else round(el["P"], 4),
            "collision_partners": k,
            "observables": json.dumps({kk: (round(v, 3) if isinstance(v, float) else v) for kk, v in obs.items()}, default=str),
            "benchmark_scorer_reproduced": d["reproduced"], "episode_dir": d["episode_dir"]})
    keys = list(out[0])
    with open(W / "data" / "marshal_score_700.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(out)
    print("rows", len(out))


if __name__ == "__main__":
    main()
