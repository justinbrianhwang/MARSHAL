"""Q_cont (existing stored values, 392 real STOP/HOLD episodes; scripted fixtures excluded). Nothing recomputed."""
import csv, json, itertools
from collections import Counter
from pathlib import Path
ROOT = Path.cwd(); OUT = ROOT / "reports/scoring_consistency/data"
Q = [r for r in csv.DictReader(open(ROOT / "reports/scoring_validity_audit/credit_quality/data/qc_episodes.csv", encoding="utf-8")) if not r["model"].startswith("scripted_")]
assert len(Q) == 392, len(Q)
res = {"n": len(Q), "strict": Counter(r["strict"] for r in Q), "pass_min": min(float(r["Q_cont"]) for r in Q if r["strict"] == "PASS"),
       "fail_max": max(float(r["Q_cont"]) for r in Q if r["strict"] == "FAIL"), "P_eq1": sum(float(r["P"]) == 1 for r in Q), "C_eq1": sum(float(r["C"]) == 1 for r in Q),
       "R_eq1": sum(float(r["R"]) == 1 for r in Q)}
for name, (lo, hi) in {"20": (0.15, 0.25), "50": (0.45, 0.55), "80": (0.75, 0.85)}.items():
    rr = [r for r in Q if lo <= float(r["Q_cont"]) <= hi]
    res["band_" + name] = {"n": len(rr), "strict": Counter(r["strict"] for r in rr), "scenarios": len({r["scenario"] for r in rr}),
        "labels": Counter(r["label"] for r in rr).most_common(), "SV_LINE": sum(r["SV-LINE"] == "1" for r in rr), "SV_NOHOLD": sum(r["SV-NOHOLD"] == "1" for r in rr),
        "SV_CONTACT": sum(r["SV-CONTACT"] == "1" for r in rr), "SV_SPEED": sum(r["SV-SPEED"] == "1" for r in rr),
        "min_front_range": [min(float(r["min_front_m"]) for r in rr if r["min_front_m"]), max(float(r["min_front_m"]) for r in rr if r["min_front_m"])] if rr else None}
fq = [r for r in Q if r["strict"] == "FAIL"]
res["fail_ge_0p8"] = {"n": sum(float(r["Q_cont"]) >= 0.8 for r in fq), "of": len(fq), "labels": Counter(r["label"] for r in fq if float(r["Q_cont"]) >= 0.8).most_common(),
                      "SV_LINE": sum(float(r["Q_cont"]) >= 0.8 and r["SV-LINE"] == "1" for r in fq)}
res["fail_ge_0p9"] = sum(float(r["Q_cont"]) >= 0.9 for r in fq)
res["fail_lt_0p333"] = sum(float(r["Q_cont"]) < 0.333 for r in fq)
ns = [float(r["Q_cont"]) for r in Q if r["label"] == "NO_START"]
dt = [float(r["Q_cont"]) for r in Q if r["label"] == "DRIVE_THROUGH"]
res["no_start"] = {"n": len(ns), "min": min(ns), "max": max(ns)}; res["drive_through"] = {"n": len(dt), "min": min(dt), "max": max(dt)}
# cross-scenario same-Q (|dQ|<=0.01) pairs where one crossed the line (SV-LINE) and the other stopped >= 1 m short with no SV flag
ex = []
for a, b in itertools.combinations(Q, 2):
    if a["scenario"] != b["scenario"] and abs(float(a["Q_cont"]) - float(b["Q_cont"])) <= 0.01:
        for x, y in ((a, b), (b, a)):
            if x["SV-LINE"] == "1" and y["strict"] == "PASS":
                ex.append((x, y))
res["pairs_line_cross_vs_pass_dQ_le_0p01"] = len(ex)
res["pair_examples"] = [[{k: r[k] for k in ("model", "seed", "scenario", "strict", "label", "Q_cont", "L", "V", "R", "H", "C", "P", "min_front_m", "enforced_max_kmh")} for r in p] for p in ex[:4]]
(OUT / "qcont_bands.json").write_text(json.dumps(res, indent=1), encoding="utf-8"); print(json.dumps(res, indent=1))
