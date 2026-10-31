"""InterFuser final re-measure (forward speed input) vs the earlier setups, scored with Credit v2.2-final (unchanged)
and the published v2 credit. Offline; run from the audit checkout root.

    python reports/scoring_validity_audit/interfuser_remeasure/scripts/final_forward.py --v2-runs <protocol-v2 run tree>

Setups (speed input checked against the episode metadata where recorded, else by directory):
  v2 setup     <v2-runs>/interfuser/s0 + tmp/_remeasure_interfuser/v2cfg/interfuser_v2cfg/s{1,2}   (3d)
  fixed-3d     tmp/_remeasure_interfuser/fixed/interfuser/s{0,1,2}                                (3d)
  final        tmp/_remeasure_interfuser/final_forward/interfuser/s{0,1,2}                        (forward)
  speedfix     tmp/_remeasure_interfuser/speedfix/interfuser/s{0,1,2}, 9 scenarios                (forward)
Writes ../data/final_forward_*.csv.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUD = HERE.parents[1]
sys.path[:0] = [str(AUD / "credit_v22_final" / "scripts"), "scripts/validation"]
import final_leaderboard as L  # noqa: E402
import summarize_validation as SV  # noqa: E402

_spec = importlib.util.spec_from_file_location("remeasure_analyze", HERE / "analyze.py")
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)
F, V = L.F, L.V
SCEN = list(V.vlm.SCENARIO_ORDER)
W = {s: F.W[s] for s in SCEN}
SW = sum(W.values())
OUT = HERE.parent / "data"
FLIPS = [("signal_off", 0), ("signal_off", 1), ("two_civilians_disagree", 2), ("conflicting_authorities", 0),
         ("fake_vest_director", 2), ("flagger_slow_then_stop", 2)]


def score(setup, seed, run_dir: Path, speed, scenarios=SCEN):
    out = []
    for sc in scenarios:
        ep = V.C.find_episode(run_dir, sc)
        if ep is None or not (ep / "strict_scoring.json").exists():
            return None
        si = A.check_speed_input(ep, speed)
        res, rows, kw, r = A.capture(ep, sc)
        fi = r["strict"] == "INVALID"
        m = SV.ab_metrics(rows)
        out.append({"setup": setup, "seed": seed, "scenario": sc, "weight": W[sc], "strict": r["strict"],
                    "credit_v22_final": round(F.scored(res, rows, kw, V.event_ids(ep), "final", fi)[0], 4),
                    "credit_v2": round(float(r["credit"] or 0), 4), "start_s": m["t_start_s"], "travel_m": m["travel_m"],
                    "v_max_kmh": m["max_speed_kmh"], "final_front_to_line_m": m["final_front_to_line_m"],
                    "min_front_to_line_m": m["min_front_to_line_m"], "strict_reason": r["strict_reason"],
                    "brake_at_rest": A.brake_cause(ep), "speed_input": si})
    return out


def wr(name, rows):
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def msd(xs):
    return {"mean": round(st.mean(xs), 2), "sd": round(st.stdev(xs), 2) if len(xs) > 1 else None,
            "min": round(min(xs), 2), "max": round(max(xs), 2)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-runs", required=True)
    a = ap.parse_args()
    rm = Path("tmp/_remeasure_interfuser")
    plan = [("v2 setup", 0, Path(a.v2_runs) / "interfuser" / "s0", "3d")]
    plan += [("v2 setup", s, rm / "v2cfg" / "interfuser_v2cfg" / f"s{s}", "3d") for s in (1, 2)]
    plan += [("fixed-3d", s, rm / "fixed" / "interfuser" / f"s{s}", "3d") for s in (0, 1, 2)]
    plan += [("final-forward", s, rm / "final_forward" / "interfuser" / f"s{s}", "forward") for s in (0, 1, 2)]
    cells, incomplete = [], []
    for setup, seed, d, speed in plan:
        got = score(setup, seed, d, speed)
        if got is None:
            incomplete.append(f"{setup} s{seed}")
        else:
            cells += got
    spf = []
    for s in (0, 1, 2):
        d = rm / "speedfix" / "interfuser" / f"s{s}"
        got = score("speedfix", s, d, "forward", sorted({sc for sc, _ in FLIPS} | {
            "occluded_officer", "dual_authority_handoff", "school_crossing_guard", "green_stop"}))
        spf += got or []
    wr("final_forward_cells.csv", cells + spf)

    # 1. suite per seed and per setup
    suite, setups = [], []
    for setup in ("v2 setup", "fixed-3d", "final-forward"):
        per = []
        for seed in (0, 1, 2):
            cs = [c for c in cells if c["setup"] == setup and c["seed"] == seed]
            if len(cs) == len(SCEN):
                per.append({"setup": setup, "seed": seed, "strict_pass": sum(c["strict"] == "PASS" for c in cs),
                            "graded_v22_final": round(100 * sum(c["weight"] * c["credit_v22_final"] for c in cs) / SW, 2),
                            "graded_v2": round(100 * sum(c["weight"] * c["credit_v2"] for c in cs) / SW, 2)})
        suite += per
        if per:
            setups.append({"setup": setup, "seeds": len(per),
                           **{f"strict_{k}": v for k, v in msd([p["strict_pass"] for p in per]).items()},
                           **{f"v22_final_{k}": v for k, v in msd([p["graded_v22_final"] for p in per]).items()},
                           **{f"v2_{k}": v for k, v in msd([p["graded_v2"] for p in per]).items()}})
    wr("final_forward_suite_per_seed.csv", suite)
    wr("final_forward_suite_per_setup.csv", setups)

    # 2. final-forward vs fixed-3d per scenario (range-overlap test)
    cmp_rows = []
    for sc in SCEN:
        o = sorted((c for c in cells if c["setup"] == "fixed-3d" and c["scenario"] == sc), key=lambda c: c["seed"])
        n = sorted((c for c in cells if c["setup"] == "final-forward" and c["scenario"] == sc), key=lambda c: c["seed"])
        if len(o) != 3 or len(n) != 3:
            continue
        ov, nv = [c["credit_v22_final"] for c in o], [c["credit_v22_final"] for c in n]
        cmp_rows.append({"scenario": sc, "weight": W[sc], "fixed_3d_strict": "".join(c["strict"][0] for c in o),
                         "final_strict": "".join(c["strict"][0] for c in n),
                         "fixed_3d_v22f": ";".join(f"{x:.2f}" for x in ov), "final_v22f": ";".join(f"{x:.2f}" for x in nv),
                         "d_mean": round(st.mean(nv) - st.mean(ov), 3), "pts": round(100 * W[sc] * (st.mean(nv) - st.mean(ov)) / SW, 3),
                         "separated": max(nv) < min(ov) or min(nv) > max(ov),
                         "fixed_3d_travel": ";".join(f"{c['travel_m']:.0f}" for c in o),
                         "final_travel": ";".join(f"{c['travel_m']:.0f}" for c in n)})
    if cmp_rows:
        wr("final_forward_vs_fixed3d.csv", cmp_rows)

    # 3. the six speedfix flips: 3d vs speedfix vs final
    fl = []
    for sc, s in FLIPS:
        row = {"scenario": sc, "seed": s}
        for setup in ("fixed-3d", "speedfix", "final-forward"):
            c = next((c for c in cells + spf if c["setup"] == setup and c["scenario"] == sc and c["seed"] == s), None)
            row[f"{setup}_strict"] = c["strict"] if c else ""
            row[f"{setup}_v22f"] = c["credit_v22_final"] if c else ""
            row[f"{setup}_travel"] = c["travel_m"] if c else ""
        fl.append(row)
    wr("final_forward_flips.csv", fl)
    # repeatability on the 9 overlapping cells: speedfix vs final, same seeds
    rep = []
    for c in spf:
        n = next((x for x in cells if x["setup"] == "final-forward" and x["scenario"] == c["scenario"] and x["seed"] == c["seed"]), None)
        if n:
            rep.append({"scenario": c["scenario"], "seed": c["seed"], "speedfix_strict": c["strict"], "final_strict": n["strict"],
                        "speedfix_v22f": c["credit_v22_final"], "final_v22f": n["credit_v22_final"],
                        "speedfix_travel": c["travel_m"], "final_travel": n["travel_m"]})
    if rep:
        wr("final_forward_repeatability_9.csv", rep)

    # 4. green_stop
    gs = [{k: c[k] for k in ("setup", "seed", "strict", "credit_v22_final", "start_s", "travel_m", "v_max_kmh",
                             "final_front_to_line_m", "brake_at_rest", "strict_reason")}
          for c in cells if c["scenario"] == "green_stop"]
    wr("final_forward_green_stop.csv", gs)

    print("incomplete:", incomplete)
    for r in setups:
        print(r)
    if rep:
        print("repeatability (9 cells x 3 seeds): strict agree", sum(r["speedfix_strict"] == r["final_strict"] for r in rep), "/", len(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
