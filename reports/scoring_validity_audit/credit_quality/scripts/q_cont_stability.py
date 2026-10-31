"""Q-cont interpretation and stability: parameter settings of Q-cont evaluated as options (none adopted).

    python <audit>/credit_quality/scripts/q_cont_stability.py          # from the workspace root

Every option is a parameter setting of the reference Q-cont (scripts/q_cont.py): weights, saturation, or an
applicability rule for a term (a term that is not applicable is left out of the weighted mean):
  RH_app  R and H are credited only when the ego approached the stop: travel >= 1 m (strict engagement progress) or
          moving at directive onset; otherwise R = H = 0 (a stop it never left is not a response).
  C_cond  C is applicable only when a contact occurred (contact-free is the expected state, as F_safe = 1 is neutral
          in the published credit), so "no contact" is not paid as merit.
  P_cond  P is not applicable once the front crossed the line (published C2: travel past the line is not approach).
Strict and the four Safety-Violation flags are unchanged and reported separately: a high Q-cont is not safe driving
and not a strict PASS. Writes credit_quality/data/qcs_*.csv and qcs_stdout.txt only.
"""
from __future__ import annotations

import collections
import itertools
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import q_cont as QC  # noqa: E402

QV, D, V, X, CF, W, NAMES = QC.QV, QC.D, QC.V, QC.X, QC.CF, QC.W, QC.NAMES
KEYS, SV, EPS = QC.KEYS, QC.SV, 1e-6
LOG = []
CHAIN = ["scripted_normal_stop", "scripted_late_stop", "scripted_overshoot", "scripted_constant_go"]
NOTE = "A high Q-cont is not safe driving and not a strict PASS: read it with the strict verdict and the SV flags."


def say(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s)


def wr(name, rows):
    QV.wr(name, rows)


def mov0(e):
    on = e["x"]["f"]["onset_s"]
    r0 = next((r for r in e["rows"] if float(r["sim_time"]) >= on - 1e-9), None)
    return r0 is not None and D._v(r0) > D.STOP_KMH


def prep(e):
    QC.prep(e)
    if e["r"]:
        e["r"]["moving_at_onset"] = mov0(e)
    return e


def comps(e, opt):
    r = e["r"]
    c = QC.comps(r, {**QC.REF, **opt.get("cfg", {})})
    app = set(KEYS)
    if opt.get("RH_app") and not (r["travel"] >= 1.0 or r["moving_at_onset"]):
        c["R"] = c["H"] = 0.0
    if opt.get("C_cond") and r["contacts"] == 0:
        app.discard("C")
    if opt.get("P_cond") and r["crossed"]:
        app.discard("P")
    return c, app


def score(e, opt=None, w=None):
    """Q-cont under an option; w: dict of weights (default: opt["w"], else 1)."""
    if not e.get("r"):
        return e["final"]
    if e["x"]["invalid"]:
        return 0.0
    opt = opt or {}
    w = w or opt.get("w") or {}
    c, app = comps(e, opt)
    ws = {k: w.get(k, 1.0) for k in app}
    return sum(ws[k] * c[k] for k in app) / sum(ws.values())


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    lb, fx0 = QC.load()
    for e in lb + fx0:
        if e.get("r"):
            e["r"]["moving_at_onset"] = mov0(e)
    fx = fx0
    sh = [e for e in lb if e["r"]]
    shf = [e for e in sh if e["strict"] == "FAIL"]
    lab = lambda e: e["x"]["label"]  # noqa: E731
    fxe = {m: [e for e in fx if e["model"] == m] for m in sorted({e["model"] for e in fx})}
    syn = {sc: [prep(QC.synth(sc, f_)) for k, f_ in QV.behaviours(sc).items() if k in QV.INTENT and f_ is not None]
           for sc in sorted({e["scenario"] for e in sh}, key=V.vlm.SCENARIO_ORDER.index)}
    real_pairs = []
    for sc in syn:
        es = [e for e in sh if e["scenario"] == sc]
        grp = [[e for e in es if lab(e) == l] for l in QV.LAB_INTENT]
        real_pairs += [(a, b) for i, j in itertools.combinations(range(4), 2) for a in grp[i] for b in grp[j]]

    def close(a, b):
        ra, rb = a["r"], b["r"]
        la, lb_ = ra["lat_held"], rb["lat_held"]
        return (abs(ra["d_min"] - rb["d_min"]) < 0.5 and abs(ra["v_enf"] - rb["v_enf"]) < 0.5 and abs(ra["share"] - rb["share"]) < 0.02
                and ra["contacts"] == rb["contacts"] and abs(ra["travel"] - rb["travel"]) < 0.5
                and ((la is None and lb_ is None) or (la is not None and lb_ is not None and abs(la - lb_) < 0.25)))
    near = [(a, b) for a, b in itertools.combinations(sh + fx, 2) if a["scenario"] == b["scenario"] and close(a, b)]
    pols, _ = X.policies()
    pol_eps = {pn: [prep(QC.synth(sc, fn_)) for sc in V.vlm.SCENARIO_ORDER] for pn, fn_ in pols.items()}
    say("in scope", len(sh), "real intent pairs", len(real_pairs), "near-duplicate pairs", len(near))

    def order_counts(fn):
        real = sum(fn(a) <= fn(b) + EPS for a, b in real_pairs)
        synt = sum(fn(ch[i]) <= fn(ch[j]) + EPS for ch in syn.values() for i, j in itertools.combinations(range(len(ch)), 2))
        fixt = sum(min(fn(e) for e in fxe[a]) <= max(fn(e) for e in fxe[b]) + EPS for a, b in itertools.combinations(CHAIN, 2))
        return real, synt, fixt

    def sh_board(fn, es=sh):
        runs = collections.defaultdict(list)
        for e in es:
            runs[(e["model"], e["seed"])].append(e)
        out = collections.defaultdict(list)
        for (m, _), xs in runs.items():
            out[m].append(100 * sum(W[e["scenario"]] * fn(e) for e in xs) / sum(W[e["scenario"]] for e in xs))
        return {m: sum(v) / len(v) for m, v in out.items()}

    REFB = sh_board(lambda e: score(e))
    models = sorted(REFB, key=lambda m: -REFB[m])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    T = lambda a, b, ms=models: round(QV.tau([a[m] for m in ms], [b[m] for m in ms]), 3)  # noqa: E731

    # ---- 1. no-start decomposition and w_P ----
    ns = fxe["scripted_no_start"][0]
    targets = {"late-stop fixture": fxe["scripted_late_stop"][0], "overshoot fixture": fxe["scripted_overshoot"][0],
               "AIM creep (green_stop)": next(e for e in sh if e["id"] == "aim/s0/green_stop"),
               "Qwen2.5-VL s1 relaunch (green_stop)": next(e for e in sh if e["id"] == "qwen2.5-vl/s1/green_stop")}
    dts = sorted((e for e in sh if lab(e) == "DRIVE_THROUGH"), key=lambda e: score(e))
    targets["median drive-through (all 114)"] = dts[len(dts) // 2]
    gdt = sorted((e for e in dts if e["scenario"] == "green_stop"), key=lambda e: score(e))
    targets["median drive-through (green_stop)"] = gdt[len(gdt) // 2]
    cn, _ = comps(ns, {})
    N5, Pn = sum(cn[k] for k in "LVRHC"), cn["P"]
    wp_rows = [{"case": "no-start fixture (s0)", **{k: round(v, 3) for k, v in cn.items()},
                "closed form": f"({N5:.3f} + {Pn:.3f} w_P) / (5 + w_P)", "w_P* where no-start falls below": ""}]
    for nm, t in targets.items():
        ct, _ = comps(t, {})
        S5 = sum(ct[k] for k in "LVRHC")
        wstar = (N5 - S5) / (ct["P"] - Pn) if ct["P"] > Pn else None
        wp_rows.append({"case": f"{nm}: {t['id']}", **{k: round(v, 3) for k, v in ct.items()}, "closed form": f"({S5:.3f} + {ct['P']:.3f} w_P) / (5 + w_P)",
                        "w_P* where no-start falls below": None if wstar is None else round(max(wstar, 0.0), 2)})
    for wp in (0.25, 0.5, 1, 2, 3, 4, 5, 8):
        wp_rows.append({"case": f"w_P = {wp}", "no-start": round((N5 + Pn * wp) / (5 + wp), 3),
                        **{nm: round(score(t, w={"P": wp}), 3) for nm, t in targets.items()}})
    wr("qcs_nostart_wP.csv", wp_rows)

    # ---- options ----
    OPTS = collections.OrderedDict([
        ("reference (equal weights)", {}),
        ("w_P = 3", {"w": {"P": 3.0}}),
        ("RH_app", {"RH_app": True}),
        ("w_C = w_P = 0.5", {"w": {"C": 0.5, "P": 0.5}}),
        ("w_C = w_P = 0.25", {"w": {"C": 0.25, "P": 0.25}}),
        ("C_cond + P_cond", {"C_cond": True, "P_cond": True}),
        ("L saturation 2x window", {"cfg": {"L_sat": "2x window"}}),
        ("L saturation published -2 m", {"cfg": {"L_sat": "published -2 m"}}),
        ("RH_app + C_cond + P_cond", {"RH_app": True, "C_cond": True, "P_cond": True}),
    ])

    # ---- 3. grid w_L x w_R x L saturation (reference applicability and RH_app + C_cond + P_cond) ----
    grid = []
    mult = (0.25, 0.5, 1, 2, 3, 4)
    for base_nm in ("reference (equal weights)", "RH_app + C_cond + P_cond"):
        for sat in ("window", "published -2 m", "spawn-to-line", "2x window"):
            for wl, wrr in itertools.product(mult, mult):
                opt = {**OPTS[base_nm], "cfg": {"L_sat": sat}, "w": {"L": wl, "R": wrr}}
                fn = lambda e, opt=opt: score(e, opt)  # noqa: E731
                real, synt, fixt = order_counts(fn)
                grid.append({"applicability": base_nm, "L_sat": sat, "w_L": wl, "w_R": wrr, "real violations or ties (704)": real,
                             "synthetic violations or ties": synt, "fixture violations (6)": fixt,
                             "tau vs reference": T(REFB, sh_board(fn))})
    wr("qcs_grid.csv", grid)
    for base_nm in ("reference (equal weights)", "RH_app + C_cond + P_cond"):
        ok = [g for g in grid if g["applicability"] == base_nm and g["synthetic violations or ties"] == 0 and g["fixture violations (6)"] == 0]
        say(base_nm, "cells with 0 synthetic + 0 fixture violations:", len(ok), "of", sum(g["applicability"] == base_nm for g in grid),
            "| min real among them:", min((g["real violations or ties (704)"] for g in ok), default=None))
        for sat in ("window", "published -2 m", "spawn-to-line", "2x window"):
            cells = [g for g in grid if g["applicability"] == base_nm and g["L_sat"] == sat]
            say("  ", sat, " ".join(f"({g['w_L']},{g['w_R']}):{g['real violations or ties (704)']}/{g['synthetic violations or ties']}/{g['fixture violations (6)']}" for g in cells))
    REGION = {"L_min_ratio": 3.0, "R_max_ratio": 1.0}   # w_L >= 3 x mean(V, H, C, P) and w_R <= mean(V, H, C, P); from qcs_grid.csv
    OPTS["RH_app + C_cond + P_cond, w_L = 3 (grid region)"] = {"RH_app": True, "C_cond": True, "P_cond": True, "w": {"L": 3.0}}

    # ---- per-option metrics ----
    SC = collections.OrderedDict([("v2.2-final", lambda e: e["final"]), ("Q-gate", QV.qg)])
    for nm, opt in OPTS.items():
        SC[f"Q-cont: {nm}"] = (lambda opt: lambda e: score(e, opt))(opt)
    rows, within, gs_rows, dang = [], [], [], []
    boards = {}
    for nm, fn in SC.items():
        cs = [fn(e) for e in shf]
        groups = collections.defaultdict(list)
        for e in shf:
            groups[lab(e)].append(fn(e))
        real, synt, fixt = order_counts(fn)
        nd = [abs(fn(a) - fn(b)) for a, b in near]
        rep = max(max(fn(e) for e in v) - min(fn(e) for e in v) for v in fxe.values())
        dom_n, dom_b = QV.dominance(sh, fn)
        _, dom_f = QV.dominance(fx, fn)
        boards[nm] = sh_board(fn)
        best = max(boards[nm][m] for m in npm)
        pol = {pn: 100 * sum(W[e["scenario"]] * fn(e) for e in es if e["r"]) / sum(W[e["scenario"]] for e in es if e["r"]) for pn, es in pol_eps.items()}
        ps = [fn(e) for e in sh if e["strict"] == "PASS"]
        rows.append({"score": nm, **{f"all {k}": v for k, v in QV.dist([fn(e) for e in sh]).items() if k != "n"},
                     "FAIL zero": sum(x < EPS for x in cs), "FAIL distinct (4 dp)": len({round(x, 4) for x in cs}),
                     "FAIL distinct (2 dp)": len({round(x, 2) for x in cs}), "eta2 label->score (FAIL)": QV.eta2(groups),
                     "PASS min": round(min(ps), 3), "FAIL max": round(max(cs), 3), "FAIL >= lowest PASS": sum(x >= min(ps) - EPS for x in cs),
                     **{f"rho FAIL: {fnm}": QV.spearman(cs, [ff(e) for e in shf]) for fnm, ff in QV.FEATS.items()},
                     "real intent violations or ties (704)": real, "synthetic intent violations or ties": synt, "fixture order violations (6)": fixt,
                     "dominance violations (1,793 leaderboard pairs)": len(dom_b), "dominance violations (99 fixture pairs)": len(dom_f),
                     "near-duplicate max |dscore| (1,006 pairs)": round(max(nd), 4), "fixture seed range": round(rep, 4),
                     "tau vs v2.2-final (all / non-priv)": f"{T(boards[nm], boards.get('v2.2-final', boards[nm]))} / {T(boards[nm], boards.get('v2.2-final', boards[nm]), npm)}",
                     "tau vs Q-cont reference": T(boards[nm], REFB), "best non-priv STOP/HOLD": round(best, 2),
                     "park policy STOP/HOLD": round(pol["park"], 2), "policies above best non-priv (48)": sum(v > best for v in pol.values())})
        for L_ in ("DRIVE_THROUGH", "OVERRUN_THEN_STOP", "LATE_STOP", "STOP_NOT_HELD", "CREEP_NO_STOP", "COLLISION", "NO_START", "CRAWL_THROUGH"):
            es = [e for e in sh if lab(e) == L_]
            q = sorted(fn(e) for e in es)
            rec = {"score": nm, "label": L_, "n": len(es), "min": round(q[0], 3), "median": round(q[len(q) // 2], 3), "max": round(q[-1], 3),
                   **{f"{k} share": round(sum(QC.flags(e)[k] for e in es) / len(es), 2) for k in SV}}
            for fnm in ("crossing depth m (-)", "enforced max speed km/h (-)", "stopped share of enforced window (+)", "held-stop latency s (-; none = 25)"):
                rec[f"rho {fnm}"] = QV.spearman([fn(e) for e in es], [QV.FEATS[fnm](e) for e in es])
            within.append(rec)
        for e in [e for e in sh if e["scenario"] == "green_stop"] + [fxe[m][0] for m in CHAIN + ["scripted_no_start", "scripted_lurch_then_stop"]]:
            gs_rows.append({"score": nm, "episode": e["id"], "strict": e["strict"], "label": lab(e),
                            "SV": ",".join(k[3:] for k in SV if QC.flags(e)[k]) or "-", "value": round(fn(e), 3)})
    wr("qcs_options.csv", [{"note": NOTE, **r} for r in rows])
    wr("qcs_within_label.csv", within)
    piv = collections.OrderedDict()
    for r in gs_rows:
        piv.setdefault(r["episode"], {k: r[k] for k in ("episode", "strict", "label", "SV")})[r["score"]] = r["value"]
    wr("qcs_green_stop.csv", [{**v, "note": NOTE} for v in piv.values()])
    lbr = []
    for m in models:
        lbr.append({"model": NAMES[m], **{nm: round(b[m], 2) for nm, b in boards.items()},
                    **{f"rank {nm}": {mm: i + 1 for i, mm in enumerate(sorted(models, key=lambda x: -b[x]))}[m] for nm, b in boards.items()}})
    wr("qcs_leaderboard.csv", lbr)

    # ---- 4. Dirichlet weights: full simplex and grid region ----
    rng = random.Random(20261030)

    def draw(region):
        while True:
            w = dict(zip(KEYS, (rng.gammavariate(1.0, 1.0) for _ in KEYS)))
            base = sum(w[k] for k in "VHCP") / 4
            if not region or (w["L"] >= REGION["L_min_ratio"] * base and w["R"] <= REGION["R_max_ratio"] * base):
                return w
    drows, trows, frows = [], [], []
    for setn, opt, region in (("reference applicability, full simplex", {}, False), ("reference applicability, grid region", {}, True),
                              ("RH_app + C_cond + P_cond, full simplex", OPTS["RH_app + C_cond + P_cond"], False),
                              ("RH_app + C_cond + P_cond, grid region", OPTS["RH_app + C_cond + P_cond"], True)):
        ref = sh_board(lambda e: score(e, opt))
        res, taus, flips, ordbad = [], [], collections.Counter(), 0
        for _ in range(1000):
            w = draw(region)
            fn = lambda e, w=w: score(e, opt, w)  # noqa: E731
            b = sh_board(fn)
            res.append(b)
            taus.append((T(ref, b), T(ref, b, npm)))
            for x, y in itertools.combinations(models, 2):
                if (ref[x] - ref[y]) * (b[x] - b[y]) < 0:
                    flips[(x, y)] += 1
            ordbad += order_counts(fn)[2] > 0
        for m in models:
            xs = sorted(b[m] for b in res)
            rk = sorted(1 + sum(b[o] > b[m] for o in models) for b in res)
            drows.append({"set": setn, "model": NAMES[m], "ref": round(ref[m], 2), "min": round(xs[0], 2), "q25": round(xs[250], 2),
                          "q75": round(xs[750], 2), "max": round(xs[-1], 2), "rank ref": 1 + sum(ref[o] > ref[m] for o in models),
                          "rank median": rk[500], "rank p05-p95": f"{rk[50]}-{rk[950]}"})
        for i, nm in enumerate(("all", "non-priv")):
            ts = sorted(t[i] for t in taus)
            trows.append({"set": setn, "tau vs this setting's equal weights": nm, "min": ts[0], "p05": ts[50], "median": ts[500], "p95": ts[950],
                          "fixture order broken (of 1000)": ordbad})
        frows += [{"set": setn, "pair": f"{NAMES[x]} - {NAMES[y]}", "flip share": round(n / 1000, 3)} for (x, y), n in flips.most_common(8)]
    wr("qcs_dirichlet_models.csv", drows)
    wr("qcs_dirichlet_tau.csv", trows)
    wr("qcs_dirichlet_flips.csv", frows)

    # ---- 5. SV reporting table and high-Q-cont-with-SV cases ----
    sv = []
    for m in models:
        for nm, opt in (("reference", {}), ("RH_app + C_cond + P_cond", OPTS["RH_app + C_cond + P_cond"])):
            es = [e for e in sh if e["model"] == m]
            q = [score(e, opt) for e in es]
            mu = sum(q) / len(q)
            sv.append({"model": NAMES[m], "Q-cont setting": nm, "episodes": len(es), "strict PASS rate": round(sum(e["strict"] == "PASS" for e in es) / len(es), 3),
                       "Q-cont mean": round(mu, 3), "Q-cont SD": round((sum((x - mu) ** 2 for x in q) / (len(q) - 1)) ** .5, 3),
                       **{f"{k} rate": round(sum(QC.flags(e)[k] for e in es) / len(es), 3) for k in SV}, "note": NOTE})
    wr("qcs_sv_report.csv", sv)
    hi = []
    for e in sh:
        fl = [k for k in SV if QC.flags(e)[k]]
        if fl and score(e) >= 0.8:
            hi.append({"episode": e["id"], "strict": e["strict"], "label": lab(e), "SV": ",".join(fl), "Q-cont reference": round(score(e), 3),
                       "Q-cont RH_app + C_cond + P_cond": round(score(e, OPTS["RH_app + C_cond + P_cond"]), 3),
                       "min_front_m": e["r"]["d_min"], "enforced_max_kmh": e["r"]["v_enf"], "contacts": e["r"]["contacts"]})
    wr("qcs_high_with_sv.csv", sorted(hi, key=lambda r: -r["Q-cont reference"]))
    say("Q-cont >= 0.8 with an SV flag:", len(hi), "of", sum(any(QC.flags(e).values()) for e in sh), "flagged episodes")
    for r in rows:
        say({k: r[k] for k in list(r)[:6]}, r["FAIL zero"], r["eta2 label->score (FAIL)"], r["real intent violations or ties (704)"],
            r["synthetic intent violations or ties"], r["fixture order violations (6)"], r["dominance violations (1,793 leaderboard pairs)"],
            r["park policy STOP/HOLD"], r["best non-priv STOP/HOLD"])
    (QC.Q.OUT / "qcs_stdout.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
