"""Checks and comparisons for MARSHAL Score (candidate) (offline, read-only).

- PASS/FAIL relation, distribution, zero composition, comparison with previous Graded and stored Q_cont
- dominance check on real episodes (same requirement type: every element >= and contacts <=, one strictly better,
  yet a lower score)
- 25 / 50 / 75 +-5 bands across scenarios
- separate application to the gs_approach_c1 56 runs (never pooled with v2)
usage (from the repository root): python -I reports/marshal_score/scripts/verify_marshal_score.py
"""
import csv, json, sys, itertools, statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path.cwd()
W = ROOT / "reports" / "marshal_score"
sys.path.insert(0, str(W))
import marshal_score as MS  # noqa: E402

rows = list(csv.DictReader(open(W / "data" / "marshal_score_700.csv", encoding="utf-8")))
f = lambda x, k: None if x[k] == "" else float(x[k])
res = {"episodes": len(rows), "scored": sum(x["marshal_score"] not in ("", "None") for x in rows),
       "strict": Counter(x["strict_verdict"] for x in rows)}
P = [x for x in rows if x["strict_verdict"] == "PASS"]; F = [x for x in rows if x["strict_verdict"] == "FAIL"]
res["pass_scores"] = sorted({float(x["marshal_score"]) for x in P})
res["pass_raw_min"] = min(float(x["raw_before_caps"]) for x in P)
res["fail_max"] = max(float(x["marshal_score"]) for x in F)
res["fail_ge_90"] = [(x["model"], x["seed"], x["scenario"], x["marshal_score"], x["strict_reason"]) for x in F if float(x["marshal_score"]) >= 90]
res["fail_distinct_values"] = len({x["marshal_score"] for x in F})
res["previous_graded_fail_distinct_values"] = len({x["previous_graded_credit_x100"] for x in F})
res["fail_zero"] = sum(float(x["marshal_score"]) == 0 for x in F)
res["previous_graded_fail_zero"] = sum(float(x["previous_graded_credit_x100"]) == 0 for x in F)
res["binding_rule_fail"] = Counter(x["binding_rule"] for x in F)
res["by_requirement"] = {k: {"n": len(v), "pass": sum(x["strict_verdict"] == "PASS" for x in v),
                             "fail_min_med_max": [min(s), st.median(s), max(s)] if (s := [float(x["marshal_score"]) for x in v if x["strict_verdict"] == "FAIL"]) else None}
                         for k, v in sorted(defaultdict(list, {r: [x for x in rows if x["requirement_type"] == r] for r in {x["requirement_type"] for x in rows}}).items())}
# previous Graded zero -> new
prev0 = [x for x in F if float(x["previous_graded_credit_x100"]) == 0]
res["previous_zero_now"] = {"n": len(prev0), "now_distinct": len({x["marshal_score"] for x in prev0}),
                            "now_hist": sorted(Counter(int(float(x["marshal_score"]) // 10) * 10 for x in prev0).items())}
# Q_cont (stored, STOP/HOLD subset)
q = [x for x in rows if x["previous_qcont_x100"] != ""]
res["qcont_subset"] = len(q)
def by_label(pred):
    s = [float(x["marshal_score"]) for x in q if pred(x)]
    return [len(s), min(s), st.median(s), max(s)] if s else None
res["qcont_vs_new_no_approach"] = by_label(lambda x: json.loads(x["observables"]).get("engagement", 1) == 0)
res["qcont_no_approach_previous_qcont"] = [min(v := [float(x["previous_qcont_x100"]) for x in q if json.loads(x["observables"]).get("engagement", 1) == 0]), max(v)]

# dominance check (real data, same requirement type)
viol = []
by = defaultdict(list)
for x in F:
    by[x["requirement_type"]].append(x)
for req, xs in by.items():
    for a, b in itertools.permutations(xs, 2):
        ea = [f(a, k) for k in ("R_core", "B_boundary", "T_timing", "P_persistence")]
        eb = [f(b, k) for k in ("R_core", "B_boundary", "T_timing", "P_persistence")]
        if any((u is None) != (v is None) for u, v in zip(ea, eb)):
            continue
        ge = all(u is None or u >= v - 1e-12 for u, v in zip(ea, eb)) and int(a["collision_partners"]) <= int(b["collision_partners"])
        gt = any(u is not None and u > v + 1e-9 for u, v in zip(ea, eb)) or int(a["collision_partners"]) < int(b["collision_partners"])
        if ge and gt and float(a["marshal_score"]) < float(b["marshal_score"]) - 1e-9:
            viol.append((a["model"], a["seed"], a["scenario"], b["model"], b["seed"], b["scenario"]))
res["dominance_pairs_checked_by_requirement"] = {k: len(v) * (len(v) - 1) for k, v in by.items()}
res["dominance_violations"] = len(viol)

# bands
band_rows = []
for c in (25, 50, 75):
    for x in rows:
        s = float(x["marshal_score"])
        if c - 5 <= s <= c + 5:
            band_rows.append({"band": c, **{k: x[k] for k in ("scenario", "requirement_type", "expected_action", "model", "seed",
                              "strict_verdict", "previous_graded_credit_x100", "previous_qcont_x100", "marshal_score",
                              "binding_rule", "R_core", "B_boundary", "T_timing", "P_persistence", "collision_partners", "observables", "strict_reason")}})
with open(W / "data" / "bands_25_50_75.csv", "w", newline="", encoding="utf-8") as fh:
    wr = csv.DictWriter(fh, fieldnames=list(band_rows[0])); wr.writeheader(); wr.writerows(band_rows)
res["bands"] = {c: {"n": sum(b["band"] == c for b in band_rows), "scenarios": len({b["scenario"] for b in band_rows if b["band"] == c}),
                    "requirement_types": Counter(b["requirement_type"] for b in band_rows if b["band"] == c),
                    "binding": Counter(b["binding_rule"] for b in band_rows if b["band"] == c)} for c in (25, 50, 75)}

# gs_approach_c1 (separate protocol; STOP condition only is scored; absent = NOT_SCORED -> no score)
gs = []
for r in csv.DictReader(open(ROOT / "reports/green_stop_approach/data/runs.csv", encoding="utf-8")):
    ep = ROOT / r["episode"] if not Path(r["episode"]).is_absolute() else Path(r["episode"])
    verdict = r["official_verdict"]
    rec = {"condition": r["condition"], "model": r["model"], "seed": r["seed"], "reference_fixture": r["is_reference_fixture"],
           "strict_verdict": verdict, "episode": r["episode"]}
    if verdict in ("PASS", "FAIL"):
        tel = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
        tr = [t for t in tel["telemetry"] if t["sim_time"] <= float(r["scored_horizon_s"]) + 1e-9]
        on, end = float(r["scored_onset_s"]), float(r["scored_active_end_s"])
        enf = float(r["enforcement_start_s"]) if r["enforcement_start_s"] else on + 3.0  # protocol: onset + 3 s
        el, obs = MS.stop_hold(tr, on, end, enf - on, boundary="stopline")
        ids = {c.get("other_actor_id") for c in tel["metadata"].get("collisions") or [] if c.get("sim_time") is not None and c["sim_time"] <= end and c.get("other_actor_id") is not None}
        sc = MS.combine(el, len(ids), verdict)
        rec.update({"marshal_score": sc["score"], "binding_rule": sc["binding"], **{k: round(v, 4) for k, v in el.items()},
                    "collision_partners": len(ids), "min_front_m": obs["min_front_m"], "v_enforcement_max_kmh": obs["v_enforcement_max_kmh"],
                    "held_stop_start_s": obs["held_stop_start_s"], "hold_share": round(obs["hold_share"], 3)})
    else:
        rec.update({"marshal_score": None, "binding_rule": "not scored (no Strict verdict; not replaced by 0)"})
    gs.append(rec)
keys = list(dict.fromkeys(k for r in gs for k in r))
with open(W / "data" / "marshal_score_approach_c1.csv", "w", newline="", encoding="utf-8") as fh:
    wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(gs)
sc = [g for g in gs if g["marshal_score"] is not None]
res["approach_c1"] = {"runs": len(gs), "scored": len(sc), "not_scored": len(gs) - len(sc),
                      "scores": sorted((g["model"], g["seed"], g["strict_verdict"], g["marshal_score"]) for g in sc)}
d = [float(x["marshal_score"]) - float(x["previous_graded_credit_x100"]) for x in F]
res["fail_vs_previous_graded"] = {"higher": sum(v > 0.05 for v in d), "equal_within_0.05": sum(abs(v) <= 0.05 for v in d), "lower": sum(v < -0.05 for v in d),
                                  "median_change": st.median(d), "mean_change": sum(d) / len(d)}
res["mean_score_all_700"] = {"marshal_score": sum(float(x["marshal_score"]) for x in rows) / len(rows),
                             "previous_graded": sum(float(x["previous_graded_credit_x100"]) for x in rows) / len(rows)}
(W / "data" / "verification_summary.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
print(json.dumps(res, indent=1, default=str))
