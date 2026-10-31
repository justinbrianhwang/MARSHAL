"""Q-cont: continuous STOP/HOLD performance-quality credit, reported next to Strict and Safety-Violation flags.

    python <audit>/credit_quality/scripts/q_cont.py          # from the workspace root

Q-cont = 1[valid] * weighted mean(L, V, R, H, C, P); no other gate, cap or zeroing rule. Reference weights equal (not decided).
  L  line:      1 at front-to-line >= +1 m (strict margin); past it, depth x = 1 - d_min:
                L = 1 - ln(1 + x) / ln(1 + X), X = speed limit (logged speed_limit_kmh) x directive window length
                (the deepest crossing reachable at the speed limit inside the window). Log shape: no anchor.
  V  speed:     enforced-window max speed v; 1 at <= 1 km/h (strict stop speed), 0 at >= logged speed limit, linear.
  R  reaction:  F6h held-stop latency (0 for an ego that never moves): 1 up to the 3 s budget, linear to 0 at
                max(8 s published zero, directive window) (D1). No held stop: the same curve at the first momentary
                stop (<= 1 km/h), times min(1, its duration / 2 s held-stop dwell); never stopped: 0.
  H  hold:      stopped share (<= 1 km/h) of the enforced window (directive window).
  C  contacts:  published F_safe steps 1 / 0.25 / 0.10 / 0 by distinct contacts (no physical anchor).
  P  progress:  travel / 1 m, capped at 1 (strict engagement progress).
Safety-Violation flags (never folded into the credit): SV-LINE crossed the line in the directive window; SV-NOHOLD no
F6h held stop (an ego that never moves is not flagged); SV-CONTACT >= 1 distinct contact; SV-SPEED enforced max > 12 km/h.
Scope: the 14 STOP/HOLD scenarios of Q-gate; dual_authority_handoff and other actions keep v2.2-final.
Writes credit_quality/data/qc_*.csv and qc_stdout.txt only.
"""
from __future__ import annotations

import collections
import itertools
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import q_gate_validation as QV  # noqa: E402

Q, D, F, X, V, G, S, CF, FL = QV.Q, QV.D, QV.F, QV.X, QV.V, QV.G, QV.S, QV.CF, QV.FL
W, NAMES, EPS = F.W, QV.NAMES, 1e-6
KEYS = "LVRHCP"
LOG = []
SV = ("SV-LINE", "SV-NOHOLD", "SV-CONTACT", "SV-SPEED")
REF = {"L_form": "log", "L_sat": "window", "V_sat": "limit", "V_form": "linear", "R_zero": "D1", "R_stop": "held+fallback",
       "H": "share", "C": "steps", "P": "travel", "agg": "mean"}


def say(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s)


def wr(name, rows):
    QV.wr(name, rows)


def clip(x):
    return min(1.0, max(0.0, x))


def raw(e):
    """Feature vector used by Q-cont (from the same scored rows as Q-gate; e must be QV.prep()-ed and in scope)."""
    f = e["x"]["f"]
    on, end = f["onset_s"], f["scored_end_s"] or 25.0
    rows = [r for r in e["rows"] if on - 1e-9 <= float(r["sim_time"]) <= end + 1e-9]
    v = [D._v(r) for r in rows]
    t = [float(r["sim_time"]) for r in rows]
    at_rest = bool(v) and v[0] <= D.STOP_KMH
    i_move = next((i for i, x in enumerate(v) if x > D.STOP_KMH), None)
    span = 0.0
    if at_rest and i_move is None:
        lat_first, start = 0.0, on
    else:
        start = t[i_move] if at_rest else on
        i0 = i_move if at_rest else 0
        i_s = next((i for i in range(i0, len(v)) if v[i] <= D.STOP_KMH and t[i] >= start), None)
        lat_first = None if i_s is None else t[i_s] - start
        if i_s is not None:
            j = next((j for j in range(i_s, len(v)) if v[j] > D.STOP_KMH), len(v) - 1)
            span = t[j] - t[i_s]
    lims = [G._finite_float(r.get("speed_limit_kmh")) for r in rows]
    lim = max([x for x in lims if x] or [30.0])
    d0 = G._finite_float(rows[0].get("stopline_front_m")) if rows else None
    i_cross = next((i for i, r in enumerate(rows) if (G._finite_float(r.get("stopline_front_m")) or 1.0) < 0), None)
    return {"d_min": f["min_front_m"] if f["min_front_m"] is not None else 0.0, "d0": d0, "window_s": end - start,
            "v_enf": f["max_enforced_kmh"] or 0.0, "lat_first": lat_first, "lat_held": f["held_latency_s"], "first_span_s": round(span, 2),
            "held_trigger": f["held_trigger"], "share": f["stopped_share_enforced"] or 0.0, "longest_s": e["x"]["hold_s"],
            "enf_len_s": max(0.0, end - on - 3.0), "contacts": f["contacts"], "F_safe": e["x"]["c"]["C"], "travel": f["travel_m"],
            "v_lim": lim, "v_cross": None if i_cross is None else round(v[i_cross], 2),
            "peak": f["peak_after_onset_kmh"], "crossed": f["t_cross_s"] is not None, "held": bool(f["held_stop"])}


def comps(r, cfg=REF):
    x = max(0.0, 1.0 - r["d_min"])
    Xs = {"window": r["v_lim"] / 3.6 * max(r["window_s"], 1.0), "published -2 m": 3.0,
          "spawn-to-line": max(r["d0"] or 1.0, 1.0), "2x window": 2 * r["v_lim"] / 3.6 * max(r["window_s"], 1.0)}[cfg["L_sat"]]
    L = 1 - clip(math.log1p(x) / math.log1p(Xs)) if cfg["L_form"] == "log" else 1 - clip(x / Xs)
    vs = {"limit": r["v_lim"], "12": 12.0, "20": 20.0, "40": 40.0}[cfg["V_sat"]]
    u = clip((r["v_enf"] - 1.0) / (vs - 1.0))
    Vc = 1 - (u if cfg["V_form"] == "linear" else u * u if cfg["V_form"] == "energy" else u)
    zero = max(8.0, r["window_s"]) if cfg["R_zero"] == "D1" else 8.0
    rc = lambda lat: 0.0 if lat is None else clip((zero - max(lat, 3.0)) / (zero - 3.0))  # noqa: E731
    held = 0.0 if r["held_trigger"] == "at_rest_never_moved" else r["lat_held"]
    if cfg["R_stop"] == "first":
        R = rc(r["lat_first"])
    elif held is not None or cfg["R_stop"] == "held only":
        R = rc(held)
    else:   # no held stop: first momentary stop, scaled by the share of the 2 s held-stop dwell it lasted
        R = rc(r["lat_first"]) * clip(r["first_span_s"] / G.STRICT_THRESHOLDS["hold_dwell_min_s"])
    H = r["share"] if cfg["H"] == "share" else clip(r["longest_s"] / max(r["enf_len_s"], 2.0))
    C = r["F_safe"] if cfg["C"] == "steps" else 1 / (1 + r["contacts"])
    if cfg["P"] == "travel":
        P = clip(r["travel"] / 1.0)
    else:
        d0 = r["d0"] or 1.0
        P = clip((d0 - max(r["d_min"], 1.0)) / (d0 - 1.0)) if d0 > 1.0 else 1.0
        P = 1.0 if r["d_min"] < 1.0 else P
    return {"L": L, "V": Vc, "R": R, "H": H, "C": C, "P": P}


def qcont(e, w=None, cfg=REF):
    if not e.get("r"):
        return e["final"]
    if e["x"]["invalid"]:
        return 0.0
    c = comps(e["r"], cfg)
    w = w or [1.0] * 6
    if cfg["agg"] == "geo":
        return math.exp(sum(wi * math.log(max(c[k], 0.01)) for wi, k in zip(w, KEYS)) / sum(w))
    return sum(wi * c[k] for wi, k in zip(w, KEYS)) / sum(w)


def flags(e):
    r = e["r"]
    return {"SV-LINE": r["crossed"], "SV-NOHOLD": not r["held"], "SV-CONTACT": r["contacts"] > 0, "SV-SPEED": r["v_enf"] > 12.0}


def prep(e):
    QV.prep(e)
    e["r"] = raw(e) if e["x"] else None
    return e


def load():
    eps = V.SN.capture()
    assert len(eps) == 650
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
    lb = [e for e in eps if e["model"] != "interfuser"]
    re_eps, _ = FL.capture_dir(HERE.parents[3] / "tmp" / "_remeasure_interfuser" / "final_forward" / "interfuser", "interfuser", FL.REQUIRED_SEEDS)
    lb = [prep(e) for e in lb + FL.check_speed(FL.ranked_episodes(re_eps), "forward")]
    assert len(lb) == 700
    fx = [prep(e) for e in V.capture_validation() if e["phase"] == "23_fixtures"]
    for e in fx:
        e["id"] = f"fixture:{e['model']}/s{e['seed']}"
    return lb, fx


def synth(sc, fn, **kw):
    return prep({"model": "synthetic", "seed": 0, "scenario": sc, "res": {"scenario": sc}, "rows": CF.synth(sc, fn, **kw), "ep": None,
                 "kw": dict(scenario=sc, expected_action=V.vlm.SCENARIOS[sc]["expect"], max_reaction_time=3.0, protocol="v2"),
                 "forced_invalid": False})


def showrow(e, name):
    r, c = e["r"], comps(e["r"])
    return {"episode": name, "strict": e["strict"], "label": e["x"]["label"], **{k: int(v) for k, v in flags(e).items()},
            "peak_kmh": r["peak"], "travel_m": r["travel"], "min_front_m": round(r["d_min"], 2), "v_cross_kmh": r["v_cross"],
            "enforced_max_kmh": r["v_enf"], "first_stop_s": None if r["lat_first"] is None else round(r["lat_first"], 2),
            "held_latency_s": None if r["lat_held"] is None else round(r["lat_held"], 2), "stopped_share": r["share"],
            "longest_stop_s": r["longest_s"], "contacts": r["contacts"], "credit_final": round(e["final"], 3),
            "Q_gate": round(QV.qg(e), 3), "Q_cont": round(qcont(e), 3), **{k: round(v, 3) for k, v in c.items()}}


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    lb, fx = load()
    sh = [e for e in lb if e["r"]]
    shf = [e for e in sh if e["strict"] == "FAIL"]
    eng = [e for e in shf if e["r"]["travel"] >= 1.0]
    SC = {"v2.2-final": lambda e: e["final"], "Q-gate": QV.qg, "Q-cont": qcont}
    lab = lambda e: e["x"]["label"]  # noqa: E731
    say("in scope", len(sh), "FAIL", len(shf), "engaged FAIL", len(eng), "invalid", sum(e["x"]["invalid"] for e in sh))

    # ---- episodes ----
    wr("qc_episodes.csv", [{**showrow(e, e["id"]), "scenario": e["scenario"], "model": e["model"], "seed": e["seed"]} for e in sh + fx])

    # ---- distribution, levels, eta2, overlap ----
    rows = []
    for nm, fn in SC.items():
        for setn, es in (("STOP/HOLD in scope", sh), ("STOP/HOLD FAIL", shf)):
            cs = [fn(e) for e in es]
            groups = collections.defaultdict(list)
            for e in es:
                groups[lab(e)].append(fn(e))
            within = sorted(max(g) - min(g) for g in groups.values() if len(g) > 1)
            rows.append({"score": nm, "set": setn, **QV.dist(cs), "distinct (4 dp)": len({round(c, 4) for c in cs}),
                         "distinct (2 dp)": len({round(c, 2) for c in cs}), "eta2 label->score": QV.eta2(groups),
                         "median within-label range": round(within[len(within) // 2], 3), "min / max": f"{min(cs):.3f} / {max(cs):.3f}"})
    wr("qc_distribution.csv", rows)
    ov = []
    for nm, fn in SC.items():
        ps = [fn(e) for e in sh if e["strict"] == "PASS"]
        fs = [fn(e) for e in shf]
        by = collections.defaultdict(list)
        for e in sh:
            by[e["scenario"]].append(e)
        ov.append({"score": nm, "PASS min": round(min(ps), 3), "PASS median": round(sorted(ps)[len(ps) // 2], 3),
                   "FAIL max": round(max(fs), 3), "FAIL >= lowest PASS": sum(x >= min(ps) - EPS for x in fs),
                   "same-scenario FAIL >= PASS pairs": sum(1 for es in by.values() for a in es for b in es
                                                           if a["strict"] == "FAIL" and b["strict"] == "PASS" and fn(a) >= fn(b) - EPS),
                   "same-scenario (FAIL, PASS) pairs": sum(1 for es in by.values() for a in es for b in es if a["strict"] == "FAIL" and b["strict"] == "PASS")})
    wr("qc_pass_fail_overlap.csv", ov)

    # ---- Spearman: sets and within labels ----
    sp = []
    for setn, es in (("STOP/HOLD all", sh), ("STOP/HOLD FAIL (incl. no-start, drive-through)", shf), ("engaged FAIL", eng)):
        for fnm, ff in QV.FEATS.items():
            sp.append({"set": f"{setn} (n={len(es)})", "feature": fnm, **{nm: QV.spearman([fn(e) for e in es], [ff(e) for e in es]) for nm, fn in SC.items()}})
    wr("qc_spearman.csv", sp)
    wl = []
    for L_ in sorted({lab(e) for e in sh}):
        es = [e for e in sh if lab(e) == L_]
        if len(es) < 3:
            continue
        for fnm, ff in QV.FEATS.items():
            if len({ff(e) for e in es}) < 2:
                continue
            wl.append({"label": L_, "n": len(es), "feature": fnm, **{nm: QV.spearman([fn(e) for e in es], [ff(e) for e in es]) for nm, fn in SC.items()},
                       "Q-cont range": f"{min(qcont(e) for e in es):.3f}-{max(qcont(e) for e in es):.3f}"})
    wr("qc_spearman_within_label.csv", wl)

    # ---- spread without a feature basis: near-duplicate pairs and fixture replicates ----
    def close(a, b):
        ra, rb = a["r"], b["r"]
        la, lb_ = ra["lat_held"], rb["lat_held"]
        return (abs(ra["d_min"] - rb["d_min"]) < 0.5 and abs(ra["v_enf"] - rb["v_enf"]) < 0.5 and abs(ra["share"] - rb["share"]) < 0.02
                and ra["contacts"] == rb["contacts"] and abs(ra["travel"] - rb["travel"]) < 0.5
                and ((la is None and lb_ is None) or (la is not None and lb_ is not None and abs(la - lb_) < 0.25)))
    nd = []
    pairs = [(a, b) for es in (sh + fx,) for a, b in itertools.combinations(es, 2) if a["scenario"] == b["scenario"] and close(a, b)]
    for nm, fn in SC.items():
        dd = sorted(abs(fn(a) - fn(b)) for a, b in pairs)
        rep = collections.defaultdict(list)
        for e in fx:
            rep[e["model"]].append(fn(e))
        nd.append({"score": nm, "near-duplicate pairs": len(pairs), "max |dscore|": round(dd[-1], 4), "p95 |dscore|": round(dd[int(.95 * len(dd))], 4),
                   "fixture replicate range (max over behaviours)": round(max(max(v) - min(v) for v in rep.values()), 4)})
    wr("qc_noise.csv", nd)

    # ---- ordering: real, synthetic, fixtures; dominance; dangerous labels with SV ----
    order = []
    for sc in sorted({e["scenario"] for e in sh}, key=V.vlm.SCENARIO_ORDER.index):
        es = [e for e in sh if e["scenario"] == sc]
        grp = {l: [e for e in es if lab(e) == l] for l in QV.LAB_INTENT}
        for nm, fn in SC.items():
            n = viol = 0
            for (la, ga), (lb_, gb) in itertools.combinations(grp.items(), 2):
                for a in ga:
                    for b in gb:
                        n += 1
                        viol += fn(a) <= fn(b) + EPS
            sy = {k: synth(sc, f_) for k, f_ in QV.behaviours(sc).items() if f_ is not None}
            ch = [k for k in QV.INTENT if k in sy]
            sv = sum(fn(sy[a]) <= fn(sy[b]) + EPS for a, b in itertools.combinations(ch, 2))
            order.append({"scenario": sc, "score": nm, "real cross-label pairs": n, "real violations or ties": viol,
                          "synthetic pairs": len(ch) * (len(ch) - 1) // 2, "synthetic violations or ties": sv,
                          "synthetic chain": " > ".join(f"{fn(sy[k]):.3f}" for k in ch), "synthetic labels": " / ".join(lab(sy[k]) for k in ch)})
    fxe = {m: [e for e in fx if e["model"] == m] for m in sorted({e["model"] for e in fx})}
    chain = ["scripted_normal_stop", "scripted_late_stop", "scripted_overshoot", "scripted_constant_go"]
    for nm, fn in SC.items():
        order.append({"scenario": "green_stop CARLA fixtures (min of better vs max of worse)", "score": nm,
                      "real cross-label pairs": 6, "real violations or ties": sum(min(fn(e) for e in fxe[a]) <= max(fn(e) for e in fxe[b]) + EPS
                                                                                    for a, b in itertools.combinations(chain, 2)),
                      "synthetic chain": " > ".join(f"{min(fn(e) for e in fxe[m]):.3f}-{max(fn(e) for e in fxe[m]):.3f}" for m in chain)})
    wr("qc_ordering.csv", order)
    ids = {e["id"]: e for e in sh + fx}

    def cause(a, b):
        ca, cb = comps(ids[a]["r"]), comps(ids[b]["r"])
        return max(KEYS, key=lambda k: cb[k] - ca[k])
    dom = []
    for nm, fn in SC.items():
        for setn, es in (("leaderboard STOP/HOLD", sh), ("green_stop fixtures", fx),
                         ("leaderboard green_stop + fixtures", [e for e in sh if e["scenario"] == "green_stop"] + fx)):
            n, bad = QV.dominance(es, fn)
            dom.append({"score": nm, "set": setn, "pairs": n, "violations": len(bad),
                        "cross-behaviour fixture violations": sum(1 for a, b, *_ in bad if a.startswith("fixture") and b.startswith("fixture")
                                                                  and a.split("/")[0] != b.split("/")[0]),
                        "examples": "; ".join(f"{a} ({sa}) < {b} ({sb})" for a, b, sa, sb in bad[:3]),
                        "component raising B most (count)": "" if nm != "Q-cont" else str(dict(collections.Counter(cause(a, b) for a, b, *_ in bad)))})
    wr("qc_dominance.csv", dom)
    dg = []
    for L_ in ("DRIVE_THROUGH", "CRAWL_THROUGH", "COLLISION", "NO_START", "CREEP_NO_STOP", "STOP_NOT_HELD", "OVERRUN_THEN_STOP", "LATE_STOP", "STOP_POSITION", "PASS"):
        es = [e for e in sh if lab(e) == L_]
        if not es:
            continue
        q = sorted(qcont(e) for e in es)
        dg.append({"label": L_, "n": len(es), "Q-cont min": round(q[0], 3), "Q-cont median": round(q[len(q) // 2], 3), "Q-cont max": round(q[-1], 3),
                   "v2.2-final mean": round(sum(e["final"] for e in es) / len(es), 3), "Q-gate mean": round(sum(QV.qg(e) for e in es) / len(es), 3),
                   "Q-cont mean": round(sum(q) / len(q), 3), **{f"{k} share": round(sum(flags(e)[k] for e in es) / len(es), 3) for k in SV},
                   "strict PASS share": round(sum(e["strict"] == "PASS" for e in es) / len(es), 3)})
    wr("qc_labels_sv.csv", dg)

    # ---- boards: STOP/HOLD and suite, strict + SV rates ----
    def sh_board(fn):
        runs = collections.defaultdict(list)
        for e in sh:
            runs[(e["model"], e["seed"])].append(e)
        out = collections.defaultdict(list)
        for (m, _), es in runs.items():
            out[m].append(100 * sum(W[e["scenario"]] * fn(e) for e in es) / sum(W[e["scenario"]] for e in es))
        return {m: sum(v) / len(v) for m, v in out.items()}

    def suite(fn):
        return {m: b["graded"] for m, b in V.board([{"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"],
                                                      "exp": e["kw"]["expected_action"], "credit": fn(e)} for e in lb]).items()}
    B = {nm: sh_board(fn) for nm, fn in SC.items()}
    BS = {nm: suite(fn) for nm, fn in SC.items()}
    models = sorted(B["Q-cont"], key=lambda m: -B["Q-cont"][m])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    rk = lambda b: {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -b[m]))}  # noqa: E731
    T = lambda a, b, ms=models: round(QV.tau([a[m] for m in ms], [b[m] for m in ms]), 3)  # noqa: E731
    brows = []
    for m in models:
        es = [e for e in sh if e["model"] == m]
        brows.append({"model": NAMES[m], "runs": len({e["seed"] for e in es}),
                      "strict PASS rate STOP/HOLD": round(sum(e["strict"] == "PASS" for e in es) / len(es), 3),
                      **{f"{k} rate": round(sum(flags(e)[k] for e in es) / len(es), 3) for k in SV},
                      **{f"{nm} STOP/HOLD": round(B[nm][m], 2) for nm in SC}, **{f"rank {nm}": rk(B[nm])[m] for nm in SC},
                      **{f"suite {nm}": round(BS[nm][m], 2) for nm in SC}})
    wr("qc_leaderboard.csv", brows)
    taus = {"STOP/HOLD: Q-cont vs v2.2-final (all / non-priv)": f"{T(B['Q-cont'], B['v2.2-final'])} / {T(B['Q-cont'], B['v2.2-final'], npm)}",
            "STOP/HOLD: Q-cont vs Q-gate": f"{T(B['Q-cont'], B['Q-gate'])} / {T(B['Q-cont'], B['Q-gate'], npm)}",
            "suite: Q-cont vs v2.2-final": f"{T(BS['Q-cont'], BS['v2.2-final'])} / {T(BS['Q-cont'], BS['v2.2-final'], npm)}",
            "suite: Q-cont vs Q-gate": f"{T(BS['Q-cont'], BS['Q-gate'])} / {T(BS['Q-cont'], BS['Q-gate'], npm)}"}
    say(taus)
    wr("qc_tau.csv", [{"comparison": k, "tau (all / non-priv)": v} for k, v in taus.items()])

    # ---- sensitivity: weights + each range / saturation / form ----
    rng = random.Random(20261030)
    ref = B["Q-cont"]
    gs_show = [e for e in sh if e["scenario"] == "green_stop"] + fx
    settings = [("weights (Dirichlet 1,000)", [rng.gammavariate(1.0, 1.0) for _ in range(6)], REF) for _ in range(1000)]
    alts = [("L_form", "linear"), ("L_sat", "published -2 m"), ("L_sat", "spawn-to-line"), ("L_sat", "2x window"),
            ("V_sat", "12"), ("V_sat", "20"), ("V_sat", "40"), ("V_form", "energy"), ("R_zero", "8 s"), ("R_stop", "first"), ("R_stop", "held only"),
            ("H", "longest"), ("C", "1/(1+n)"), ("P", "target zone"), ("agg", "geo")]
    for k, v in alts:
        settings.append((f"{k} = {v}", None, {**REF, k: v}))
    acc = collections.defaultdict(list)
    for name, w, cfg in settings:
        fn = lambda e, w=w, cfg=cfg: qcont(e, w, cfg)  # noqa: E731
        b = sh_board(fn)
        fails = [fn(e) for e in shf]
        ford = all(min(fn(e) for e in fxe[a]) > max(fn(e) for e in fxe[b_]) + EPS for a, b_ in zip(chain, chain[1:]))
        acc[name.split(" (")[0] if name.startswith("weights") else name].append(
            (b, T(ref, b), T(ref, b, npm), sum(x < EPS for x in fails), ford, {e["id"]: fn(e) for e in gs_show}))
    srows, flips = [], collections.Counter()
    for name, res in acc.items():
        ts = sorted(r[1] for r in res)
        tn = sorted(r[2] for r in res)
        srows.append({"setting": name, "n": len(res), "tau all min / p05 / median": f"{ts[0]} / {ts[int(.05 * len(ts))]} / {ts[len(ts) // 2]}",
                      "tau non-priv min / median": f"{tn[0]} / {tn[len(tn) // 2]}", "FAIL at 0 (max)": max(r[3] for r in res),
                      "fixture order holds (share)": round(sum(r[4] for r in res) / len(res), 3),
                      **{NAMES[m]: f"{min(r[0][m] for r in res):.1f}-{max(r[0][m] for r in res):.1f}" for m in models}})
        if name == "weights":
            for r in res:
                for x, y in itertools.combinations(models, 2):
                    if (ref[x] - ref[y]) * (r[0][x] - r[0][y]) < 0:
                        flips[(x, y)] += 1
    wr("qc_sensitivity.csv", srows)
    wr("qc_sensitivity_flips.csv", [{"pair": f"{NAMES[x]} - {NAMES[y]}", "ref": f"{ref[x]:.2f} / {ref[y]:.2f}", "flip share": round(n / 1000, 3)}
                                    for (x, y), n in flips.most_common()])
    gsr = []
    for e in gs_show:
        rec = {"episode": e["id"], "label": lab(e), "Q-cont ref": round(qcont(e), 3)}
        for name, res in acc.items():
            xs = [r[5][e["id"]] for r in res]
            rec[name] = f"{min(xs):.3f}-{max(xs):.3f}" if len(xs) > 1 else f"{xs[0]:.3f}"
        gsr.append(rec)
    wr("qc_sensitivity_green_stop.csv", gsr)

    # ---- green_stop showcase + synthetic sweeps ----
    show = []
    for e in sorted([e for e in sh if e["scenario"] == "green_stop"], key=lambda e: -qcont(e)):
        show.append(showrow(e, f"{NAMES[e['model']]} s{e['seed']}"))
    for m in chain + ["scripted_no_start", "scripted_lurch_then_stop"]:
        show.append(showrow(fxe[m][0], f"fixture {m.replace('scripted_', '')} s0"))
    wr("qc_green_stop_showcase.csv", show)
    gs = "green_stop"
    F0 = float(CF.template(gs)[0]["stopline_front_m"])
    ramp = CF.ramp_to_stop
    fam = collections.OrderedDict()
    fam["overrun depth, 25 km/h then stop (m past the line)"] = [(x, synth(gs, ramp(25, 0.2, -x, F0, a=4.0))) for x in (0.0, 2.0, 10.0, 30.0, 70.0)]
    fam["drive-through speed (constant km/h)"] = [(v, synth(gs, (lambda v: lambda t: min(float(v), 9 * t))(v))) for v in (5, 8, 12, 18, 25)]
    def brake_at(tb):
        return lambda t: 0.0 if t < 0.2 else (8.0 if t < tb else max(0.0, 8.0 - 20 * (t - tb)))
    fam["reaction: 8 km/h, brake at t s (signal_off)"] = [(tb, synth("signal_off", brake_at(tb))) for tb in (2.0, 4.0, 6.0, 8.0)]
    hs = ramp(15, 0.2, 4.0, F0, a=3.0)
    t_stop = next(i * 0.01 for i in range(30, 3000) if hs(i * 0.01) <= 0.0)
    fam["hold before 5 km/h relaunch (s)"] = [(h, synth(gs, (lambda h: lambda t: hs(t) if t < t_stop + h else min(5.0, 5 * (t - t_stop - h)))(h))) for h in (0.5, 2.5, 4.0, 8.0, 99.0)]
    fam["contacts (normal stop)"] = [(n, synth(gs, ramp(15, 0.2, 2.0, F0), collisions_at=tuple(5 + 1.5 * i for i in range(n)))) for n in (0, 1, 2, 6)]
    fam["travel (m, 8 km/h short stop)"] = [(d, synth(gs, ramp(8, 0.2, F0 - d, F0))) for d in (0.0, 0.5, 0.9, 1.2, 2.0)]
    sw = []
    for nm, pts in fam.items():
        prev = None
        for val, e in pts:
            rec = {"family [S]": nm, "value": val, **showrow(e, f"{nm}: {val}")}
            rec["dQ-cont per unit from previous"] = "" if prev is None else round((qcont(e) - qcont(prev[1])) / (val - prev[0]), 4) if val != prev[0] else ""
            sw.append(rec)
            prev = (val, e)
    wr("qc_sweeps.csv", sw)

    # ---- F2 ----
    pols, _ = X.policies()
    tot = sum(W.values())
    pr = []
    for pn, fn_ in pols.items():
        es = [synth(sc, fn_) for sc in V.vlm.SCENARIO_ORDER]
        pr.append({"policy": pn, "suite Q-cont": round(100 * sum(W[e["scenario"]] * qcont(e) for e in es) / tot, 2),
                   "STOP/HOLD Q-cont": round(100 * sum(W[e["scenario"]] * qcont(e) for e in es if e["r"]) / sum(W[e["scenario"]] for e in es if e["r"]), 2)})
    best_s, best_h = max(BS["Q-cont"][m] for m in npm), max(B["Q-cont"][m] for m in npm)
    f2 = {"best policy suite": max(p["suite Q-cont"] for p in pr), "policies above best non-priv suite": sum(p["suite Q-cont"] > best_s for p in pr),
          "best non-priv suite": round(best_s, 2), "best policy STOP/HOLD": max(p["STOP/HOLD Q-cont"] for p in pr),
          "policies above best non-priv STOP/HOLD": sum(p["STOP/HOLD Q-cont"] > best_h for p in pr), "best non-priv STOP/HOLD": round(best_h, 2),
          "park policy STOP/HOLD": next(p["STOP/HOLD Q-cont"] for p in pr if p["policy"] == "park")}
    wr("qc_policies.csv", pr)
    wr("qc_f2_summary.csv", [f2])
    say("F2", f2)
    for r in rows + ov + nd + dom + dg + srows[:3]:
        say(r)
    for r in brows:
        say(r)
    (Q.OUT / "qc_stdout.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
