"""Aggregate N full-sweep runs into mean +/- std, with per-cell pass probability.

Reads outputs/multirun/run_*.json (each the output of _collect_sweep.py) and
reports, per model: mean/std of pass_rate, graded, and marshal_score across runs,
plus per (model, scenario) strict-PASS probability so borderline cells surface as
fractional. Writes outputs/multirun_aggregate.json.

Run from the marshal env:  python scripts/_aggregate_multirun.py
"""
from __future__ import annotations
import os, sys, json, glob, math

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))


def _mean_std(vals):
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    if not vals:
        return None, None
    m = sum(vals) / len(vals)
    if len(vals) == 1:
        return round(m, 2), 0.0
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return round(m, 2), round(math.sqrt(var), 2)


def main():
    run_paths = sorted(glob.glob(os.path.join(ROOT, "outputs", "multirun", "run_*.json")))
    if not run_paths:
        print("no runs found in outputs/multirun/")
        return 1
    runs = [json.load(open(p, encoding="utf-8")) for p in run_paths]
    n = len(runs)
    print(f"aggregating {n} runs: {[os.path.basename(p) for p in run_paths]}\n")

    models = list(runs[0]["summary"].keys())
    scenarios = runs[0].get("scenarios", [])
    out = {"n_runs": n, "runs": [os.path.basename(p) for p in run_paths], "models": {}}

    hdr = f"{'model':14s} {'pass_rate mean+/-std':>22s} {'graded mean+/-std':>20s} {'mscore mean+/-std':>20s}"
    print(hdr)
    for m in models:
        prm, prs = _mean_std([r["summary"][m].get("pass_rate") for r in runs])
        grm, grs = _mean_std([r["summary"][m].get("graded") for r in runs])
        msm, mss = _mean_std([r["summary"][m].get("marshal_score") for r in runs])
        # per-cell PASS probability
        cell_pass = {}
        for sc in scenarios:
            verdicts = [r.get("matrix", {}).get(m, {}).get(sc, {}).get("strict") for r in runs]
            npass = sum(1 for v in verdicts if v == "PASS")
            cell_pass[sc] = round(npass / n, 3)
        borderline = {sc: p for sc, p in cell_pass.items() if 0.0 < p < 1.0}
        out["models"][m] = {
            "pass_rate": {"mean": prm, "std": prs},
            "graded": {"mean": grm, "std": grs},
            "marshal_score": {"mean": msm, "std": mss},
            "cell_pass_prob": cell_pass,
            "borderline_cells": borderline,
        }
        print(f"{m:14s} {str(prm)+' +/- '+str(prs):>22s} "
              f"{str(grm)+' +/- '+str(grs):>20s} {str(msm)+' +/- '+str(mss):>20s}"
              + (f"   borderline: {list(borderline)}" if borderline else ""))

    op = os.path.join(ROOT, "outputs", "multirun_aggregate.json")
    json.dump(out, open(op, "w", encoding="utf-8"), indent=2)
    print(f"\nwrote {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
