"""Threshold sensitivity of the v2 leaderboard on the recorded telemetry (audit; scorer-only, no new rollouts).

    python <audit>/scripts/sensitivity.py --out <audit>      # from the workspace root

Captures the exact (rows, kwargs) the published collector passes to the scorers for each of the 650 leaderboard
episodes, then rescores them with one threshold scaled by 0.75 / 0.90 / 1.10 / 1.25 (dict-held thresholds only;
constants hard-coded in function bodies are listed as not perturbed). Reports changed strict verdicts, endpoint
counts, per-action effects, model ranking and Kendall tau-b / Spearman rho (local implementations, ties corrected/averaged).
"""
from __future__ import annotations

import argparse
import collections
import copy
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import published_v2  # noqa: E402,F401  (pins the published v2 graded scorer; see published_v2.py)
import collect_protocol_v2 as C  # noqa: E402
import _run_vlm_test as vlm  # noqa: E402


def kendall_tau_b(x, y):
    """Kendall tau-b (ties corrected); O(n^2), n = 14 models."""
    c = d = tx = ty = 0
    for i in range(len(x)):
        for j in range(i + 1, len(x)):
            a, b = x[i] - x[j], y[i] - y[j]
            if a == 0 and b == 0:
                continue
            if a == 0:
                tx += 1
            elif b == 0:
                ty += 1
            elif (a > 0) == (b > 0):
                c += 1
            else:
                d += 1
    den = ((c + d + tx) * (c + d + ty)) ** 0.5
    return (c - d) / den if den else float("nan")


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1  # average rank for ties
        i = j + 1
    return r


def spearman_rho(x, y):
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else float("nan")

S, G = C.S, C.G
PARAMS = [  # (dict, key, unit, note)
    ("STRICT", "engagement_speed_kmh", "km/h", "movement gate speed"),
    ("STRICT", "engagement_progress_m", "m", "movement gate progress"),
    ("V2", "front_clearance_m", "m", "front-bumper stop-line margin"),
    ("STRICT", "stopline_engagement_m", "m", "16.59 m proximity band"),
    ("STRICT", "stop_speed_kmh", "km/h", "stopped speed"),
    ("GRADED", "stop_speed_zero_kmh", "km/h", "speed-credit zero point"),
    ("GRADED", "latency_full_credit_s", "s", "latency plateau"),
    ("GRADED", "latency_zero_credit_s", "s", "latency zero point"),
    ("GRADED", "stop_engagement_creep_cap", "-", "creep cap on engagement"),
    ("GRADED", "stop_engagement_speed_full_kmh", "km/h", "engagement approach speed (full)"),
    ("STRICT", "detour_lateral_m", "m", "detour lateral clearance"),
    ("STRICT", "yield_stop_speed_kmh", "km/h", "yield slow speed"),
    ("V2", "proceed_start_deadline_s", "s", "PROCEED start deadline"),
]
SCALES = (0.75, 0.90, 1.10, 1.25)


def capture():
    """(scenario, model, seed, expected, weight, rows, kw) for every leaderboard episode, via the collector."""
    cap, out = {}, []
    orig = S.score_episode_from_telemetry

    def grab(result, rows, **kw):
        cap["args"] = (copy.deepcopy(result), list(rows), dict(kw))
        return orig(result, rows, **kw)
    S.score_episode_from_telemetry = grab
    runs = Path("tmp/_protocol_v2")
    for md in sorted(p for p in runs.iterdir() if p.is_dir() and "_cap3" not in p.name and "_abl-" not in p.name):
        for sd in sorted(p for p in md.iterdir() if p.is_dir() and p.name.startswith("s")):
            for sc in vlm.SCENARIO_ORDER:
                ep = C.find_episode(sd, sc)
                if ep is None:
                    continue
                cap.clear()
                r = C.score_episode(ep, sc, vlm.SCENARIOS, is_vlm=md.name in C.VLM_MODELS)
                if r.get("accepted") and "args" in cap:
                    res, rows, kw = cap["args"]
                    out.append({"model": md.name, "seed": int(sd.name[1:]), "scenario": sc, "res": res, "rows": rows,
                                "kw": kw, "forced_invalid": r["strict"] == "INVALID"})
    S.score_episode_from_telemetry = orig
    return out


def score_all(eps):
    out = []
    for e in eps:
        s = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        g = G.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        cr = 0.0 if e["forced_invalid"] else float(g.get("credit") or 0.0)
        out.append((s.get("verdict") if not e["forced_invalid"] else "INVALID", cr))
    return out


def board(eps, scores):
    W = G.SCENARIO_AUTHORITY_WEIGHTS
    per = collections.defaultdict(lambda: collections.defaultdict(float))
    for e, (_, c) in zip(eps, scores):
        per[e["model"]][e["seed"]] += W[e["scenario"]] * c
    ws = sum(W.values())
    return {m: sum(v / ws * 100 for v in d.values()) / len(d) for m, d in per.items()}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    eps = capture()
    base = score_all(eps)
    b0 = board(eps, base)
    models = sorted(b0, key=lambda m: -b0[m])
    dicts = {"STRICT": S.STRICT_THRESHOLDS, "GRADED": G.GRADED_THRESHOLDS, "V2": S.PROTOCOL_V2}
    rows = []
    for dname, key, unit, note in PARAMS:
        if key not in dicts[dname]:
            rows.append({"param": key, "status": f"not found in {dname}"})
            continue
        orig = dicts[dname][key]
        for k in SCALES:
            new = orig * k
            for d in dicts.values():  # GRADED copies STRICT keys at import: patch every dict holding the key
                if key in d:
                    d[key] = new
            sc = score_all(eps)
            for d in dicts.values():
                if key in d:
                    d[key] = orig
            b = board(eps, sc)
            flips = [(e["model"], e["seed"], e["scenario"], x[0], y[0]) for e, x, y in zip(eps, base, sc) if x[0] != y[0]]
            act = collections.Counter(vlm.SCENARIOS[f[2]]["expect"] for f in flips)
            tau = kendall_tau_b([b0[m] for m in models], [b[m] for m in models])
            rho = spearman_rho([b0[m] for m in models], [b[m] for m in models])
            rank = sorted(b, key=lambda m: -b[m])
            rows.append({"param": key, "unit": unit, "note": note, "base": orig, "scale": k, "value": round(new, 4),
                         "strict_flips": len(flips), "flips_by_action": dict(act),
                         "pass_total": sum(x[0] == "PASS" for x in sc),
                         "full": sum(c >= 1 - 1e-6 for _, c in sc), "zero": sum(c <= 1e-6 for _, c in sc),
                         "partial": sum(1e-6 < c < 1 - 1e-6 for _, c in sc),
                         "kendall_tau_b": round(tau, 4), "spearman_rho": round(rho, 4),
                         "top3": ";".join(rank[:3]), "max_abs_graded_change": round(max(abs(b[m] - b0[m]) for m in b), 2),
                         "model_graded": json.dumps({m: round(b[m], 2) for m in models})})
    out = Path(a.out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    keys = list(rows[0].keys())
    with (out / "data" / "threshold_sensitivity.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows([r for r in rows if "unit" in r])
    base_counts = (sum(x[0] == "PASS" for x in base), sum(c >= 1 - 1e-6 for _, c in base), sum(c <= 1e-6 for _, c in base))
    lines = ["# Threshold sensitivity (generated by scripts/sensitivity.py)\n",
             f"Recorded telemetry of the 650 leaderboard episodes, rescored with one threshold scaled at a time. "
             f"Baseline: strict PASS {base_counts[0]}, full {base_counts[1]}, zero {base_counts[2]}. Ranking statistics "
             f"compare the 14 model graded means (Kendall τ-b and Spearman ρ, ties averaged). Not perturbed (hard-coded "
             f"in function bodies): conflict factor 0.35, evidence factor 0.75, safety steps 0.25/0.10/0, action-curve "
             f"weights. A single deterministic run per stack: these numbers describe scorer sensitivity, not run-to-run "
             f"stability.\n",
             "| threshold | unit | base | ×0.75 | ×0.90 | ×1.10 | ×1.25 |", "|---|---|---:|---|---|---|---|"]
    by = collections.defaultdict(dict)
    for r in rows:
        if "unit" in r:
            by[(r["param"], r["unit"], r["base"])][r["scale"]] = r
    for (p, u, b), d in by.items():
        cell = lambda r: (f"flips {r['strict_flips']} · PASS {r['pass_total']} · full/zero {r['full']}/{r['zero']} · "
                          f"τ {r['kendall_tau_b']} · Δmax {r['max_abs_graded_change']}")
        lines.append(f"| {p} | {u} | {b:g} | " + " | ".join(cell(d[k]) for k in SCALES) + " |")
    (out / "threshold_sensitivity_raw.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
