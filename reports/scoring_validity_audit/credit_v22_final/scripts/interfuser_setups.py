"""InterFuser runs of both adapter setups re-scored with the frozen Credit v2.2-final (recorded telemetry only).

    python <audit>/credit_v22_final/scripts/interfuser_setups.py      # from the audit checkout root

Runs: v2 setup = leaderboard s0 (protocol-v2 tree) + re-runs s1, s2 (tmp/_remeasure_interfuser/v2cfg);
fixed = s0, s1, s2 (tmp/_remeasure_interfuser/fixed) + earlier seed-0 run `a` (tmp/_validation_interfuser/authority).
Outputs (credit_v22_final/data/): interfuser_setups_runs.csv, interfuser_setups_cells.csv.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import final_leaderboard as L  # noqa: E402

F, V, S = L.F, L.V, L.S
ROOT = HERE.parents[3]
SOURCES = [("v2 setup", "leaderboard", V.RUNS / "interfuser"),
           ("v2 setup", "re-run", ROOT / "tmp/_remeasure_interfuser/v2cfg/interfuser_v2cfg"),
           ("fixed", "a", ROOT / "tmp/_validation_interfuser/authority/interfuser"),
           ("fixed", "re-run", ROOT / "tmp/_remeasure_interfuser/fixed/interfuser")]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cells, runs = [], []
    for setup, run, d in SOURCES:
        eps, cov = L.capture_dir(d, "interfuser")
        if run == "leaderboard":
            eps = [e for e in eps if e["seed"] == 0]
        assert eps, f"no complete seed under {d}: {cov}"
        for e in eps:
            ids = V.event_ids(e["ep"])
            strict = "INVALID" if e["forced_invalid"] else S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"]).get("verdict")
            cr = {v: F.scored(e["res"], e["rows"], e["kw"], ids, v, e["forced_invalid"])[0] for v in ("v2", "final")}
            cells.append({"setup": setup, "run": run, "seed": e["seed"], "scenario": e["scenario"], "strict": strict,
                          "credit_v2": round(cr["v2"], 4), "credit_v22_final": round(cr["final"], 4)})
        for seed in sorted({e["seed"] for e in eps}):
            cs = [c for c in cells if (c["setup"], c["run"], c["seed"]) == (setup, run, seed)]
            assert len(cs) == len(V.vlm.SCENARIO_ORDER)
            g = lambda k: round(100 * sum(F.W[c["scenario"]] * c[k] for c in cs) / sum(F.W[c["scenario"]] for c in cs), 2)  # noqa: E731
            runs.append({"setup": setup, "run": run, "seed": seed, "graded_v2": g("credit_v2"), "graded_v22_final": g("credit_v22_final"),
                         "strict_pass": sum(c["strict"] == "PASS" for c in cs),
                         "green_stop_v22_final": next(c["credit_v22_final"] for c in cs if c["scenario"] == "green_stop")})
    F.wr("interfuser_setups_runs.csv", runs)
    F.wr("interfuser_setups_cells.csv", cells)
    for r in runs:
        print(r)
    for setup in ("v2 setup", "fixed"):
        xs = [r["graded_v22_final"] for r in runs if r["setup"] == setup and r["run"] != "a"]
        print(setup, "seeds 0-2 v2.2-final mean %.2f sd %.2f min %.2f max %.2f" % (statistics.mean(xs), statistics.stdev(xs), min(xs), max(xs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
