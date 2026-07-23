"""Collect the full-sweep results from per-episode artifacts.

Both strict and MARSHAL-Graded are RE-SCORED from each episode's recorded
strict_telemetry.json with the CURRENT scorers (the scorers are pure functions
over telemetry), so a sweep whose episodes were recorded under different
scorer revisions is still reported under one uniform ruleset. The stored
run-time verdict is kept alongside for audit; episodes without telemetry fall
back to the stored verdict. Prints a models x scenarios matrix + per-model
summary and writes outputs/full_sweep_results.json.

Run from the marshal env:  python scripts/_collect_sweep.py
"""
from __future__ import annotations
import hashlib, os, sys, json

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))
sys.path.insert(0, THIS); sys.path.insert(0, ROOT)
import _run_vlm_test as vlm
from _run_full_sweep import MODELS, SCEN
from marshal_bench.criteria.graded_episode_scoring import (
    score_episode_from_telemetry, aggregate_graded_scores)
from marshal_bench.criteria.strict_episode_scoring import (
    score_episode_from_telemetry as score_strict_episode)
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


def _is_privileged(result_blob):
    """True when the episode artifact says the controller got the E-tuple.

    Checks both the top-level result and the nested ``result`` blob; either
    the runner-recorded flag or the controller's own opt-in counts.
    """
    if not isinstance(result_blob, dict):
        return False
    blobs = [result_blob]
    nested = result_blob.get("result")
    if isinstance(nested, dict):
        blobs.append(nested)
    for blob in blobs:
        if blob.get("privileged_ground_truth_provided") or blob.get(
                "controller_requests_privileged_gt"):
            return True
    return False


def _extract_condition(result_blob):
    if not isinstance(result_blob, dict):
        return None
    if isinstance(result_blob.get("condition"), dict):
        return result_blob["condition"]
    nested = result_blob.get("result")
    if isinstance(nested, dict) and isinstance(nested.get("condition"), dict):
        return nested["condition"]
    return None


def _condition_key(condition):
    weather = (condition or {}).get("weather")
    if not isinstance(weather, dict):
        return None
    canonical = json.dumps(weather, sort_keys=True, separators=(",", ":"))
    return "weather-sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def main():
    matrix = {}      # model -> scenario -> {strict, credit}
    summary = {}
    for label, out_root, epid_fn, _ in MODELS:
        matrix[label] = {}
        ep_scores = []
        ep_metrics = []
        conditions_seen = set()
        npass = nfail = ninvalid = nmissing = 0
        for sc in SCEN:
            epdir = os.path.join(out_root, epid_fn(sc))
            result_blob = read_result(epdir)
            condition_key = _condition_key(_extract_condition(result_blob))
            if condition_key is not None:
                conditions_seen.add(condition_key)
            # Defense in depth: episode-id naming keeps privileged diagnostic
            # runs (oracle-assist ablation) out of these paths, but the
            # artifact itself also carries a durable flag. Refuse any
            # NON-oracle episode that received the privileged E-tuple — a
            # leaked env var or a plug-in controller that silently sets
            # requests_privileged_gt must never score as Track-B/C.
            if label != "oracle" and _is_privileged(result_blob):
                print(f"!! {label}/{sc}: episode received privileged ground "
                      "truth - EXCLUDED from leaderboard scoring")
                matrix[label][sc] = {"strict": "PRIVILEGED-EXCLUDED",
                                     "credit": None}
                nmissing += 1
                continue
            strict = read_strict(epdir)
            if strict is None:
                matrix[label][sc] = {"strict": "MISSING", "credit": None}
                nmissing += 1
                continue
            stored_verdict = strict.get("verdict") or ("INVALID" if strict.get("invalid") else "?")
            rows = read_tel(epdir)
            result_dict = result_blob
            if not isinstance(result_dict, dict):
                result_dict = {"scenario": sc, "strict_scoring": strict}
            inner = result_dict.get("result", result_dict)
            if not isinstance(inner, dict):
                inner = {"scenario": sc}
            inner.setdefault("scenario", sc)
            inner.setdefault("strict_scoring", strict)
            budget = (inner.get("ground_truth") or {}).get("max_reaction_time_sec")
            # Re-score strict under the CURRENT scorer whenever telemetry
            # exists, so old and new episodes are graded by one ruleset.
            rescored = None
            if rows:
                try:
                    rescored = score_strict_episode(
                        inner, rows, scenario=sc,
                        expected_action=vlm.SCENARIOS[sc]["expect"],
                        max_reaction_time=budget)
                except Exception:
                    rescored = None
            if rescored is not None:
                verdict = rescored.get("verdict") or "?"
                inner["strict_scoring"] = rescored
            else:
                verdict = stored_verdict
            is_invalid = (rescored or strict).get("invalid")
            if is_invalid:
                ninvalid += 1
            elif verdict == "PASS":
                npass += 1
            elif verdict == "FAIL":
                nfail += 1
            ep_metrics.append(compute_episode_metrics(
                inner, scenario=sc, telemetry_rows=rows))
            credit = None
            if rows:
                try:
                    gs = score_episode_from_telemetry(
                        inner, rows, scenario=sc,
                        expected_action=vlm.SCENARIOS[sc]["expect"],
                        max_reaction_time=budget)
                    credit = gs.get("credit")
                    ep_scores.append(gs)
                except Exception:
                    credit = None
            matrix[label][sc] = {
                "strict": verdict,
                "credit": credit,
                "stored_strict": stored_verdict,
                "rescored": rescored is not None,
                "changed": (rescored is not None and verdict != stored_verdict) or None,
            }
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
            "conditions_seen": sorted(conditions_seen),
            # audit trail: scenarios whose verdict changed under the current
            # scorer vs the verdict stored at run time
            "rescored_changed": sorted(
                sc for sc in SCEN if matrix[label][sc].get("changed")),
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
