"""Second pass: equal-score comparisons, rank agreement, component constancy, weight re-aggregation, Q_cont bands.

Reads out/episode_behaviour.csv (from consistency.py), cells_v2.csv and the existing Q_cont episode table.
Ordinal outcome classes reuse the scorer's thresholds, so agreement with them is NOT an independent validation.
usage (from the repository root): python -I reports/scoring_consistency/scripts/consistency2.py
"""
import csv, json, itertools, statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "reports" / "scoring_consistency" / "data"
E = list(csv.DictReader(open(OUT / "episode_behaviour.csv", encoding="utf-8")))
Q = list(csv.DictReader(open(ROOT / "reports/scoring_validity_audit/credit_quality/data/qc_episodes.csv", encoding="utf-8")))
W = {r["scenario"]: float(r["weight"]) for r in E}
res = {}

# 1. what a zero means
z = [r for r in E if float(r["credit"]) == 0]
res["credit0_n"] = len(z)
res["credit0_by_outcome"] = Counter(r["telemetry_outcome"] for r in z).most_common()
res["credit0_by_first_zero_term"] = Counter("movement" if r["movement_gate"] == "0" else "action" if float(r["action_credit"]) == 0
                                            else "latency" if float(r["latency_factor"]) == 0 else "safety" for r in z).most_common()
res["credit0_scenarios"] = len({r["scenario"] for r in z})

# 2. same behaviour class, different credit by scenario (stop-line STOP/HOLD)
ORD = {"STOP_HELD_BEFORE_MARGIN": 5, "STOP_INSIDE_1M_MARGIN": 4, "STOP_BEFORE_MARGIN_NOT_HELD_THROUGH_ENFORCEMENT": 3,
       "STOP_PAST_LINE_LE_3M": 2, "CROSSED_LINE_LE_3M_NO_HOLD": 1, "DROVE_THROUGH_LINE_GT_3M": 0}
EXCL = {"dual_authority_handoff", "fallen_person"}  # band / hazard anchored: stop-line classes do not apply
sl = [r for r in E if r["telemetry_outcome"] in ORD and r["scenario"] not in EXCL]
cls = defaultdict(lambda: defaultdict(list))
for r in sl:
    cls[r["telemetry_outcome"]][r["scenario"]].append(float(r["credit"]))
res["stopline_class_credit_by_scenario"] = {k: {s: [round(min(v), 3), round(st.median(v), 3), round(max(v), 3), len(v)]
                                                for s, v in sorted(d.items())} for k, d in cls.items()}

# 3. cross-scenario near-equal pairs among partial-credit stop-line cells, outcome gap >= 2 ordinal levels
part = [r for r in sl if 0 < float(r["credit"]) < 1]
pairs = []
for a, b in itertools.combinations(part, 2):
    if a["scenario"] != b["scenario"] and abs(float(a["credit"]) - float(b["credit"])) <= 0.05:
        gap = abs(ORD[a["telemetry_outcome"]] - ORD[b["telemetry_outcome"]])
        pairs.append((gap, a, b))
res["partial_stopline_cells"] = len(part)
res["cross_scenario_pairs_within_0p05"] = len(pairs)
res["pairs_gap_ge2"] = sum(g >= 2 for g, *_ in pairs)
ex = sorted([p for p in pairs if p[0] >= 2], key=lambda p: (-p[0], abs(float(p[1]["credit"]) - float(p[2]["credit"]))))[:12]
res["pair_examples"] = [{"gap": g, "a": {k: a[k] for k in ("model", "seed", "scenario", "strict", "credit", "telemetry_outcome", "min_front_in_directive_window_m", "max_speed_enforcement_kmh", "action_credit", "latency_factor", "safety_factor")},
                         "b": {k: b[k] for k in ("model", "seed", "scenario", "strict", "credit", "telemetry_outcome", "min_front_in_directive_window_m", "max_speed_enforcement_kmh", "action_credit", "latency_factor", "safety_factor")}} for g, a, b in ex]

# 4. rank agreement credit vs ordinal outcome (stop-line STOP/HOLD), pooled and within scenario
def kendall(xs, ys):
    c = d = 0
    for (x1, y1), (x2, y2) in itertools.combinations(zip(xs, ys), 2):
        s = (x1 - x2) * (y1 - y2)
        c += s > 0; d += s < 0
    return (c - d) / (c + d) if c + d else None, c, d
xs = [float(r["credit"]) for r in sl]; ys = [ORD[r["telemetry_outcome"]] for r in sl]
res["rank_pooled"] = dict(zip(("tau_a_untied", "concordant", "discordant"), kendall(xs, ys)), n=len(sl))
within = {}
for s in sorted({r["scenario"] for r in sl}):
    rr = [r for r in sl if r["scenario"] == s]
    within[s] = dict(zip(("tau", "conc", "disc"), kendall([float(r["credit"]) for r in rr], [ORD[r["telemetry_outcome"]] for r in rr])), n=len(rr))
res["rank_within_scenario"] = within

# 5. component constancy per expected action
comp = {}
for a in sorted({r["expected_action"] for r in E}):
    rr = [r for r in E if r["expected_action"] == a]
    comp[a] = {"n": len(rr), "latency_eq1": sum(float(r["latency_factor"]) == 1 for r in rr), "latency_eq0": sum(float(r["latency_factor"]) == 0 for r in rr),
               "safety_eq1": sum(float(r["safety_factor"]) == 1 for r in rr), "movement_eq1": sum(r["movement_gate"] == "1" for r in rr),
               "action_eq0": sum(float(r["action_credit"]) == 0 for r in rr), "action_eq1": sum(float(r["action_credit"]) >= 0.999 for r in rr)}
res["component_constancy"] = comp

# 6. model ranking, official weights vs equal scenario weights (offline re-aggregation only, not a proposal)
runs = defaultdict(dict)
for r in E:
    runs[(r["model"], r["seed"])][r["scenario"]] = float(r["credit"])
def board(wf):
    m = defaultdict(list)
    for (mod, seed), d in runs.items():
        m[mod].append(100 * sum(wf(s) * c for s, c in d.items()) / sum(wf(s) for s in d))
    return {k: round(st.mean(v), 2) for k, v in m.items()}
off, eq = board(lambda s: W[s]), board(lambda s: 1.0)
mods = sorted(off)
tau = kendall([off[m] for m in mods], [eq[m] for m in mods])
res["weights_official_vs_equal"] = {"official": dict(sorted(off.items(), key=lambda x: -x[1])), "equal": dict(sorted(eq.items(), key=lambda x: -x[1])),
                                    "kendall_tau_models": tau, "sum_w": sum(W.values())}

# 7. Q_cont bands on the existing 392 STOP/HOLD subset (values as stored; nothing recomputed)
res["qcont_n"] = len(Q)
qb = {}
for name, (lo, hi) in {"20": (0.15, 0.25), "50": (0.45, 0.55), "80": (0.75, 0.85)}.items():
    rr = [r for r in Q if lo <= float(r["Q_cont"]) <= hi]
    qb[name] = {"n": len(rr), "strict": Counter(r["strict"] for r in rr), "labels": Counter(r["label"] for r in rr).most_common(),
                "scenarios": len({r["scenario"] for r in rr}),
                "SV_LINE": sum(r["SV-LINE"] == "1" for r in rr), "SV_NOHOLD": sum(r["SV-NOHOLD"] == "1" for r in rr),
                "SV_CONTACT": sum(r["SV-CONTACT"] == "1" for r in rr), "SV_SPEED": sum(r["SV-SPEED"] == "1" for r in rr),
                "min_front_range": [min(float(r["min_front_m"]) for r in rr if r["min_front_m"]), max(float(r["min_front_m"]) for r in rr if r["min_front_m"])] if rr else None}
res["qcont_bands"] = qb
fq = [r for r in Q if r["strict"] == "FAIL"]
res["qcont_fail_ge_0p8"] = {"n": sum(float(r["Q_cont"]) >= 0.8 for r in fq), "labels": Counter(r["label"] for r in fq if float(r["Q_cont"]) >= 0.8).most_common(),
                            "with_SV_LINE": sum(float(r["Q_cont"]) >= 0.8 and r["SV-LINE"] == "1" for r in fq)}
res["qcont_P_eq1"] = sum(float(r["P"]) == 1 for r in Q); res["qcont_C_eq1"] = sum(float(r["C"]) == 1 for r in Q)
res["qcont_pass"] = Counter(r["strict"] for r in Q)
res["qcont_pass_min"] = min(float(r["Q_cont"]) for r in Q if r["strict"] == "PASS")
res["qcont_fail_max"] = max(float(r["Q_cont"]) for r in Q if r["strict"] == "FAIL")
hi = sorted(fq, key=lambda r: -float(r["Q_cont"]))[:8]
res["qcont_top_fail"] = [{k: r[k] for k in ("model", "seed", "scenario", "label", "Q_cont", "L", "V", "R", "H", "C", "P", "min_front_m", "enforced_max_kmh", "SV-LINE", "SV-NOHOLD", "SV-SPEED")} for r in hi]

(OUT / "summary2.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
print(json.dumps(res, indent=1, default=str))
