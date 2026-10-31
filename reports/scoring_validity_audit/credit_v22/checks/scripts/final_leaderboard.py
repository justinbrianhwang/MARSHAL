"""Final candidate leaderboard under Credit v2.2 (recorded telemetry only; strict verdicts as published).

    python <audit>/credit_v22/checks/scripts/final_leaderboard.py [--remeasure DIR] [--runs tmp/_protocol_v2]
    # from the workspace root (the checkout that holds tmp/_protocol_v2)

- Every model keeps its protocol-v2 leaderboard runs, except InterFuser: its row is replaced by the re-measured runs of
  the fixed adapter (default DIR: <audit checkout>/tmp/_remeasure_interfuser/fixed, layout interfuser/s<seed>/...).
  Only seeds with all 25 scenarios accepted are used; the row reports the mean, SD and min-max over those seeds. With
  no complete seed the v2 InterFuser run is kept and the row is flagged "remeasure pending".
- Columns: v2 (reference), v2.2, v2.2 + adopted minimal fixes (front-capped approach distance, creep cap only far
  upstream; see ../checks.md), strict k/n.
- Sensitivity rows: F3 identity bounds (all contacts past the 20th event distinct; v2.2 without F3) and the
  two-geometry-scenario exclusion (occluded_officer, sequential_directive).
Outputs: checks/data/final_leaderboard.csv, final_leaderboard_sensitivity.csv, final_leaderboard_coverage.csv.
"""
from __future__ import annotations

import argparse
import collections
import copy
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checks as K  # noqa: E402

X, V, G, S = K.X, K.V, K.G, K.S
OUT = HERE.parent / "data"
GEOMETRY = ("occluded_officer", "sequential_directive")
MODEL = "interfuser"


def capture_dir(model_dir: Path, model: str):
    """Collector-scored episodes of every s<seed> under model_dir -> (episodes, coverage rows)."""
    cap, out, cov = {}, [], []
    orig = S.score_episode_from_telemetry

    def grab(res, rows, **kw):
        cap["a"] = (copy.deepcopy(res), list(rows), dict(kw))
        return orig(res, rows, **kw)
    S.score_episode_from_telemetry = grab
    try:
        for sd in sorted(p for p in model_dir.iterdir() if p.is_dir() and p.name.startswith("s") and p.name[1:].isdigit()):
            got = []
            for sc in V.vlm.SCENARIO_ORDER:
                ep = V.C.find_episode(sd, sc)
                if ep is None:
                    continue
                cap.clear()
                r = V.C.score_episode(ep, sc, V.vlm.SCENARIOS, is_vlm=False)
                if r.get("accepted") and "a" in cap:
                    res, rows, kw = cap["a"]
                    got.append({"model": model, "seed": int(sd.name[1:]), "scenario": sc, "res": res, "rows": rows, "kw": kw,
                                "forced_invalid": r["strict"] == "INVALID", "ep": ep})
            complete = len(got) == len(V.vlm.SCENARIO_ORDER)
            cov.append({"seed": sd.name, "scenarios_accepted": len(got), "complete": complete})
            if complete:
                out += got
    finally:
        S.score_episode_from_telemetry = orig
    return out, cov


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remeasure", type=Path, default=HERE.parents[4] / "tmp" / "_remeasure_interfuser" / "fixed")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    eps = V.SN.capture()
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
    re_eps, cov = capture_dir(a.remeasure / MODEL, MODEL) if (a.remeasure / MODEL).is_dir() else ([], [])
    pending = not re_eps
    if not pending:
        eps = [e for e in eps if e["model"] != MODEL] + re_eps
    for e in eps:
        e["ids"] = V.event_ids(e["ep"])
        e["strict"] = "INVALID" if e["forced_invalid"] else S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"]).get("verdict")
    OUT.mkdir(parents=True, exist_ok=True)
    K.wr("final_leaderboard_coverage.csv", cov or [{"seed": "", "scenarios_accepted": 0, "complete": False}])

    def cell(e, credit):
        return {"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"], "exp": e["kw"]["expected_action"], "credit": credit}

    cols = collections.OrderedDict()
    cols["v2"] = [cell(e, X.scored(e["res"], e["rows"], e["kw"], e["ids"], "v2", e["forced_invalid"])[0]) for e in eps]
    cols["v2.2"] = [cell(e, X.scored(e["res"], e["rows"], e["kw"], e["ids"], "v2.2", e["forced_invalid"])[0]) for e in eps]
    cols["v2.2+adopted"] = [cell(e, K.vscore(e["res"], e["rows"], e["kw"], e["ids"], K.ADOPTED, e["forced_invalid"])[0]) for e in eps]
    hi = [[(x if x is not None else ("hidden", k), t) for k, (x, t) in enumerate(e["ids"])] for e in eps]
    cols["v2.2, all hidden contacts distinct"] = [cell(e, X.scored(e["res"], e["rows"], e["kw"], h, "v2.2", e["forced_invalid"])[0])
                                                  for e, h in zip(eps, hi)]
    nof3 = []
    for e in eps:
        with V.patched(("F1", "F7"), list(e["ids"])):
            G._latency_factor = X.latency_v22
            g = G.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        nof3.append(cell(e, 0.0 if e["forced_invalid"] else round(min(max(float(g.get("credit") or 0), 0.0), 1.0), 6)))
    cols["v2.2 without F3"] = nof3
    cols["v2.2, without occluded_officer + sequential_directive"] = [c for c in cols["v2.2"] if c["scenario"] not in GEOMETRY]

    boards = {k: V.board(v) for k, v in cols.items()}
    models = sorted(boards["v2.2"], key=lambda m: -boards["v2.2"][m]["graded"])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    rank = {k: {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -b[m]["graded"]))} for k, b in boards.items()}
    spread = collections.defaultdict(list)
    for c in cols["v2.2"]:
        spread[(c["model"], c["seed"])].append(c)
    per_run = collections.defaultdict(list)
    for (m, _), cs in spread.items():
        per_run[m].append(100 * sum(V.W[c["scenario"]] * c["credit"] for c in cs) / sum(V.W[c["scenario"]] for c in cs))
    rows = []
    for m in models:
        b = boards["v2.2"][m]
        rows.append({"model": m, "runs": b["runs"], "source": ("remeasured, fixed adapter" if m == MODEL and not pending
                                                               else "v2 run, remeasure pending" if m == MODEL else "v2 run"),
                     "strict_pass": f"{b['k']}/{b['n']}", "graded_v2": round(boards["v2"][m]["graded"], 2),
                     "graded_v22": round(b["graded"], 2), "sd_v22": round(b["sd"], 2),
                     "min_max_v22": f"{min(per_run[m]):.2f}-{max(per_run[m]):.2f}",
                     "graded_v22_adopted": round(boards["v2.2+adopted"][m]["graded"], 2),
                     "rank_v22": rank["v2.2"][m], "rank_v22_adopted": rank["v2.2+adopted"][m]})
    K.wr("final_leaderboard.csv", rows)
    sens = []
    for k in cols:
        sens.append({"column": k, **{m: f"{boards[k][m]['graded']:.2f} (#{rank[k][m]})" for m in models},
                     "tau_vs_v22_all": round(V.SN.kendall_tau_b([boards["v2.2"][m]["graded"] for m in models], [boards[k][m]["graded"] for m in models]), 3),
                     "tau_vs_v22_nonpriv": round(V.SN.kendall_tau_b([boards["v2.2"][m]["graded"] for m in npm], [boards[k][m]["graded"] for m in npm]), 3)})
    lo, up = boards["v2.2, all hidden contacts distinct"], boards["v2.2"]
    sens.append({"column": "F3 pairs not separated", "tau_vs_v22_all": "", "tau_vs_v22_nonpriv": "",
                 **{models[0]: ";".join(f"{x}-{y}" for i, x in enumerate(models) for y in models[i + 1:]
                                         if not (lo[x]["graded"] > up[y]["graded"] or lo[y]["graded"] > up[x]["graded"]))}})
    K.wr("final_leaderboard_sensitivity.csv", sens)
    print("InterFuser source:", "remeasure pending (v2 run kept)" if pending else f"{len({e['seed'] for e in re_eps})} complete seed(s)")
    print("coverage:", cov)
    for r in rows:
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
