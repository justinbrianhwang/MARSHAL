"""Collect the full-sweep results from per-episode artifacts.

Reads each (model, scenario) episode's strict_scoring.json (PASS/FAIL/invalid)
and re-scores MARSHAL-Graded from strict_telemetry.json, then prints a 14x21
matrix + per-model summary and writes outputs/full_sweep_results.json.

Run from the marshal env:  python scripts/_collect_sweep.py
"""
from __future__ import annotations
import os, sys, json

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))
sys.path.insert(0, THIS); sys.path.insert(0, ROOT)
import _run_vlm_test as vlm
from _run_full_sweep import MODELS, SCEN
from marshal_bench.criteria.graded_episode_scoring import (
    score_episode_from_telemetry, aggregate_graded_scores)
from marshal_bench.criteria.marshal_metrics import (
    CONFLICT_TYPE, CONFLICT_TYPE_ORDER, REASONING_TIER,
    compute_episode_metrics, aggregate)

TRACK_C = {"glm-4.5v", "qwen2.5-vl", "qwen3-vl"}


def read_strict(epdir):
    p = os.path.join(epdir, "strict_scoring.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def read_tel(epdir):
    p = os.path.join(epdir, "strict_telemetry.json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
        return d.get("telemetry") if isinstance(d, dict) else d
    except Exception:
        return None


def read_result(epdir):
    p = os.path.join(epdir, "result.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def main():
    matrix = {}      # model -> scenario -> {strict, credit}
    summary = {}
    for label, out_root, epid_fn, _ in MODELS:
        matrix[label] = {}
        ep_scores = []
        ep_metrics = []
        npass = nfail = ninvalid = nmissing = 0
        for sc in SCEN:
            epdir = os.path.join(out_root, epid_fn(sc))
            strict = read_strict(epdir)
            if strict is None:
                matrix[label][sc] = {"strict": "MISSING", "credit": None}
                nmissing += 1
                continue
            verdict = strict.get("verdict") or ("INVALID" if strict.get("invalid") else "?")
            if strict.get("invalid"):
                ninvalid += 1
            elif verdict == "PASS":
                npass += 1
            elif verdict == "FAIL":
                nfail += 1
            credit = None
            rows = read_tel(epdir)
            result_dict = read_result(epdir)
            if not isinstance(result_dict, dict):
                result_dict = {"scenario": sc, "strict_scoring": strict}
            else:
                result_dict.setdefault("scenario", sc)
                result_dict.setdefault("strict_scoring", strict)
            ep_metrics.append(compute_episode_metrics(
                result_dict, scenario=sc, telemetry_rows=rows))
            if rows:
                try:
                    gs = score_episode_from_telemetry(
                        {"scenario": sc, "expected_action": vlm.SCENARIOS[sc]["expect"]},
                        rows, scenario=sc,
                        expected_action=vlm.SCENARIOS[sc]["expect"])
                    credit = gs.get("credit")
                    ep_scores.append(gs)
                except Exception as e:
                    credit = None
            matrix[label][sc] = {"strict": verdict, "credit": credit}
        graded = aggregate_graded_scores(ep_scores)["marshal_graded"] if ep_scores else None
        marshal_agg = aggregate(ep_metrics)
        scored = npass + nfail + ninvalid
        conflict_profile = {}
        for conflict_type in CONFLICT_TYPE_ORDER:
            scenarios = [sc for sc in SCEN if CONFLICT_TYPE.get(sc) == conflict_type]
            conflict_profile[conflict_type] = {
                "passed": sum(
                    1 for sc in scenarios
                    if matrix[label][sc]["strict"] == "PASS"
                ),
                "total": len(scenarios),
            }
        summary[label] = {
            "track": "C" if label in TRACK_C else "A/B",
            "strict_pass": npass, "strict_fail": nfail, "invalid": ninvalid,
            "missing": nmissing, "scored": scored,
            "pass_rate": round(100.0 * npass / scored, 1) if scored else None,
            "graded": graded,
            "marshal_score": marshal_agg["marshal_score_partial"],
            "r_scores": marshal_agg["r_scores"],
            "suite": marshal_agg["suite"],
            "conflict_profile": conflict_profile,
        }

    # print
    print(f"{'model':14s} {'track':5s} {'pass':>4s}/{'tot':<3s} {'rate%':>6s} {'graded':>7s} {'mscore':>7s} {'miss':>4s}")
    for label, _, _, _ in MODELS:
        s = summary[label]
        print(f"{label:14s} {s['track']:5s} {s['strict_pass']:>4d}/{s['scored']:<3d} "
              f"{str(s['pass_rate']):>6s} {str(s['graded']):>7s} "
              f"{str(s['marshal_score']):>7s} {s['missing']:>4d}")

    out = {"summary": summary, "matrix": matrix, "scenarios": SCEN,
           "conflict_type": {sc: CONFLICT_TYPE.get(sc) for sc in SCEN},
           "tier": {sc: REASONING_TIER.get(sc) for sc in SCEN}}
    op = os.path.join(ROOT, "outputs", "full_sweep_results.json")
    json.dump(out, open(op, "w", encoding="utf-8"), indent=2)
    print(f"\nwrote {op}")


if __name__ == "__main__":
    main()
