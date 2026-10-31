"""Speed-input re-measure: before (3-D speed, tmp/_remeasure_interfuser/fixed) vs after (forward speed,
tmp/_remeasure_interfuser/speedfix) on the 9 selected scenarios x seeds 0-2. Offline; run from the audit checkout root.

    python reports/scoring_validity_audit/interfuser_speedfix/scripts/compare.py

Scoring: published strict verdict and Credit v2.2-final (credit_v22_final/scripts/credit_final.py, unchanged), plus the
published v2 credit for reference. Writes ../data/cells_before_after.csv and ../data/suite9.csv.
"""
import csv
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUD = HERE.parents[1]
sys.path[:0] = [str(AUD / "credit_v22_final" / "scripts"), "scripts/validation"]
import final_leaderboard as L  # noqa: E402
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("remeasure_analyze", AUD / "interfuser_remeasure" / "scripts" / "analyze.py")
A = importlib.util.module_from_spec(_spec)  # capture(), brake_cause() of the re-measure analysis
_spec.loader.exec_module(A)
import summarize_validation as SV  # noqa: E402

F, V, S = L.F, L.V, L.S
SCEN9 = ["signal_off", "conflicting_authorities", "two_civilians_disagree", "occluded_officer", "fake_vest_director",
         "flagger_slow_then_stop", "dual_authority_handoff", "school_crossing_guard", "green_stop"]
RUNS = {"before (3-D speed)": Path("tmp/_remeasure_interfuser/fixed/interfuser"),
        "after (forward speed)": Path("tmp/_remeasure_interfuser/speedfix/interfuser")}
EXPECTED = {"before (3-D speed)": "3d", "after (forward speed)": "forward"}
W_ALL = sum(F.W[s] for s in V.vlm.SCENARIO_ORDER)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cells = []
    for label, root in RUNS.items():
        for seed in (0, 1, 2):
            for sc in SCEN9:
                ep = V.C.find_episode(root / f"s{seed}", sc)
                if ep is None or not (ep / "strict_scoring.json").exists():
                    continue
                speed_input = A.check_speed_input(ep, EXPECTED[label])  # refuses a mixed configuration
                res, rows, kw, r = A.capture(ep, sc)
                fi = r["strict"] == "INVALID"
                ids = V.event_ids(ep)
                m = SV.ab_metrics(rows)
                cells.append({"run": label, "seed": seed, "scenario": sc, "weight": F.W[sc], "strict": r["strict"],
                              "credit_v22_final": round(F.scored(res, rows, kw, ids, "final", fi)[0], 4),
                              "credit_v2": round(float(r["credit"] or 0), 4), "start_s": m["t_start_s"],
                              "travel_m": m["travel_m"], "v_max_kmh": m["max_speed_kmh"],
                              "min_front_to_line_m": m["min_front_to_line_m"], "final_front_to_line_m": m["final_front_to_line_m"],
                              "collisions": m["collisions"], "strict_reason": r["strict_reason"],
                              "brake_at_rest": A.brake_cause(ep), "speed_input": speed_input})
    out = HERE.parent / "data"
    with (out / "cells_before_after.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cells[0]))
        w.writeheader()
        w.writerows(cells)
    suite = []
    for label in RUNS:
        for seed in (0, 1, 2):
            cs = [c for c in cells if c["run"] == label and c["seed"] == seed]
            if len(cs) != len(SCEN9):
                continue
            suite.append({"run": label, "seed": seed, "pts_v22_final": round(100 * sum(c["weight"] * c["credit_v22_final"] for c in cs) / W_ALL, 3),
                          "pts_v2": round(100 * sum(c["weight"] * c["credit_v2"] for c in cs) / W_ALL, 3),
                          "strict_pass": sum(c["strict"] == "PASS" for c in cs)})
    with (out / "suite9.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(suite[0]))
        w.writeheader()
        w.writerows(suite)
    for label in RUNS:
        xs = [s["pts_v22_final"] for s in suite if s["run"] == label]
        if len(xs) > 1:
            print(f"{label}: 9-cell points (v2.2-final, share of the 25-scenario suite) per seed {xs} "
                  f"mean {st.mean(xs):.3f} sd {st.stdev(xs):.3f} range {max(xs) - min(xs):.3f}; strict "
                  f"{[s['strict_pass'] for s in suite if s['run'] == label]}")
    for c in cells:
        print(c["run"][:6], c["scenario"][:22].ljust(22), c["seed"], c["strict"], c["credit_v22_final"], c["start_s"],
              c["travel_m"], c["final_front_to_line_m"], c["brake_at_rest"][:30], (c["strict_reason"] or "")[:50])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
