"""Q-gate validation on existing logs (STOP/HOLD); other actions keep v2.2-final.

    python <audit>/credit_quality/scripts/q_gate_validation.py          # from the workspace root

Scope: Q-gate (credit_quality.qgate) scores the 14 STOP/HOLD scenarios with a stop-line target. dual_authority_handoff
(STOP, but the required stop is a band past a hazard, not a stop line) keeps v2.2-final, as do PROCEED/DETOUR/YIELD.
Episode set = the final leaderboard set: protocol-v2 runs of every model except InterFuser + the InterFuser final
forward-speed re-measure s0-2. The InterFuser protocol-v2 run is scored and listed as excluded.
Writes credit_quality/data/qg_*.csv and qg_stdout.txt only.
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
import credit_quality as Q  # noqa: E402

D, F, X, V, G, S, CF, FL = Q.D, Q.F, Q.X, Q.V, Q.G, Q.S, Q.D.CF, Q.FL
W = F.W
EPS = 1e-6
SH = ("STOP", "HOLD")
OUT_OF_SCOPE = {"dual_authority_handoff": "STOP with a stop band past a hazard (approach requirement), no stop-line target"}
LOG = []
N_W = 1000
NAMES = FL.NAMES


def say(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s)


def wr(name, rows):
    F.OUT = Q.OUT
    F.wr(name, rows)


def in_scope(sc, act):
    return act in SH and sc not in OUT_OF_SCOPE


def prep(e):
    e["ids"] = V.event_ids(e["ep"]) if e.get("ep") else ()
    s = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
    e["strict"] = "INVALID" if e["forced_invalid"] else s.get("verdict")
    e["reason"] = s.get("reason")
    e["id"] = f"{e['model']}/s{e['seed']}/{e['scenario']}"
    if in_scope(e["scenario"], e["kw"]["expected_action"]):
        e["x"] = Q.comps(e)
        e["final"] = e["x"]["final"]
    else:
        e["x"] = None
        e["final"] = F.scored(e["res"], e["rows"], e["kw"], e["ids"], "final", e["forced_invalid"])[0]
    return e


def qg(e, **kw):
    return Q.qgate(e["x"], **kw) if e["x"] else e["final"]


def synth_ep(sc, act, tel, model="synthetic", seed=0):
    return prep({"model": model, "seed": seed, "scenario": sc, "res": {"scenario": sc}, "rows": tel, "ep": None,
                 "kw": dict(scenario=sc, expected_action=act, max_reaction_time=3.0, protocol="v2"), "forced_invalid": False})


# ------------------------------------------------------------------------------------------------ statistics
def spearman(a, b):
    return D.spearman(a, b)


def tau(a, b):
    return V.SN.kendall_tau_b(a, b)


def dist(xs):
    z = sum(x < EPS for x in xs)
    f = sum(x >= 1 - EPS for x in xs)
    dec = [0] * 10
    for x in xs:
        dec[min(9, int(x * 10))] += 1
    return {"n": len(xs), "zero": z, "partial": len(xs) - z - f, "full": f, "deciles [0,.1)..[.9,1]": " ".join(map(str, dec))}


def eta2(groups):
    allx = [x for g in groups.values() for x in g]
    m = sum(allx) / len(allx)
    sst = sum((x - m) ** 2 for x in allx)
    ssb = sum(len(g) * (sum(g) / len(g) - m) ** 2 for g in groups.values())
    return None if sst == 0 else round(ssb / sst, 3)


def safety(e):
    """(latency, line, enforced, contacts) oriented so that larger = safer."""
    f = e["x"]["f"]
    lat = f["held_latency_s"] if f["held_latency_s"] is not None else math.inf
    return (-lat, min(f["min_front_m"] if f["min_front_m"] is not None else 0.0, 1.0), -(f["max_enforced_kmh"] or 0.0), -f["contacts"])


def dominance(es, score):
    """Pairs in the same scenario, both engaged (travel >= 1 m): A Pareto-safer than B but score(A) < score(B)."""
    bad, n = [], 0
    by = collections.defaultdict(list)
    for e in es:
        if e["x"] and e["x"]["f"]["travel_m"] >= 1.0 and not e["x"]["invalid"]:
            by[e["scenario"]].append(e)
    for sc, xs in by.items():
        for a, b in itertools.permutations(xs, 2):
            sa, sb = safety(a), safety(b)
            if all(p >= q for p, q in zip(sa, sb)) and any(p > q for p, q in zip(sa, sb)):
                n += 1
                if score(a) < score(b) - EPS:
                    bad.append((a["id"], b["id"], round(score(a), 4), round(score(b), 4)))
    return n, bad


FEATS = {"min front-to-line m (+)": lambda e: e["x"]["f"]["min_front_m"] or 0.0,
         "crossing depth m (-)": lambda e: -max(0.0, -(e["x"]["f"]["min_front_m"] or 0.0)),
         "peak speed after onset km/h (-)": lambda e: -e["x"]["f"]["peak_after_onset_kmh"],
         "enforced max speed km/h (-)": lambda e: -(e["x"]["f"]["max_enforced_kmh"] or 0.0),
         "held-stop latency s (-; none = 25)": lambda e: -(e["x"]["f"]["held_latency_s"] if e["x"]["f"]["held_latency_s"] is not None else 25.0),
         "stopped share of enforced window (+)": lambda e: e["x"]["f"]["stopped_share_enforced"] or 0.0,
         "longest stop s (+)": lambda e: e["x"]["hold_s"],
         "distinct contacts (-)": lambda e: -e["x"]["f"]["contacts"],
         "travel m (+)": lambda e: e["x"]["f"]["travel_m"]}


# ------------------------------------------------------------------------------------------------ synthetic behaviours
def behaviours(sc):
    """Per-template STOP behaviours; None where the station geometry cannot host the behaviour."""
    F0 = float(CF.template(sc)[0]["stopline_front_m"])
    c = min(2.0, F0 - 1.2)
    out = collections.OrderedDict()
    out["normal stop"] = CF.ramp_to_stop(15, 0.2, c, F0, a=3.0) if c >= 1.0 else None
    v_l = min(8.0, (F0 - c) / 6.0 * 3.6)              # cruise so that the stop is reached ~6 s after launch

    def late(t, v=v_l):
        if t < 0.2:
            return 0.0
        tb = 0.2 + max(0.0, (F0 - c - (v / 3.6) ** 2 / 6.0)) / (v / 3.6)
        return v if t < tb else max(0.0, v - 10.8 * (t - tb))
    out["late stop"] = late if c >= 1.0 and v_l >= 2.0 else None
    out["overrun then stop (2 m past)"] = CF.ramp_to_stop(15, 0.2, -2.0, F0, a=3.0)
    out["crawl through (5 km/h)"] = lambda t: min(5.0, 9 * t)
    out["drive through (25 km/h)"] = lambda t: min(25.0, 9 * t)
    out["no start"] = lambda t: 0.0
    return out


INTENT = ("normal stop", "late stop", "overrun then stop (2 m past)", "drive through (25 km/h)")
LAB_INTENT = ("PASS", "LATE_STOP", "OVERRUN_THEN_STOP", "DRIVE_THROUGH")


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    eps = V.SN.capture()
    assert len(eps) == 650
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
    excluded = [prep(e) for e in eps if e["model"] == "interfuser"]
    lb = [e for e in eps if e["model"] != "interfuser"]
    re_eps, _ = FL.capture_dir(HERE.parents[3] / "tmp" / "_remeasure_interfuser" / "final_forward" / "interfuser", "interfuser", FL.REQUIRED_SEEDS)
    re_eps = FL.check_speed(FL.ranked_episodes(re_eps), "forward")
    assert len(re_eps) == 75
    lb = [prep(e) for e in lb + re_eps]
    assert len(lb) == 700
    fx = [prep(e) for e in V.capture_validation() if e["phase"] == "23_fixtures"]
    for e in fx:
        e["id"] = f"fixture:{e['model']}/s{e['seed']}"
    sh = [e for e in lb if e["x"]]
    shf = [e for e in sh if e["strict"] == "FAIL"]
    say("episodes", len(lb), "Q-gate scope (STOP/HOLD)", len(sh), "FAIL", len(shf), "fixtures", len(fx))

    # ---- per-episode table ---------------------------------------------------------------------------
    wr("qg_episodes.csv", [{"episode": e["id"], "scenario": e["scenario"], "strict": e["strict"], "label": e["x"]["label"],
                             "credit_final": round(e["final"], 4), "Q_gate": round(qg(e), 4), "Z": e["x"]["Z"],
                             **{k: round(v, 4) for k, v in e["x"]["c"].items() if k in "LEKRHCM"},
                             "min_front_m": e["x"]["f"]["min_front_m"], "peak_kmh": e["x"]["f"]["peak_after_onset_kmh"],
                             "enforced_max_kmh": e["x"]["f"]["max_enforced_kmh"], "held_latency_s": e["x"]["f"]["held_latency_s"],
                             "stopped_share": e["x"]["f"]["stopped_share_enforced"], "hold_s": e["x"]["hold_s"],
                             "contacts": e["x"]["f"]["contacts"], "travel_m": e["x"]["f"]["travel_m"]} for e in sh + fx])

    # ---- 1. distribution + boards --------------------------------------------------------------------
    drows = []
    for nm, es in (("STOP/HOLD in scope, all", sh), ("STOP/HOLD in scope, FAIL", shf),
                   ("suite (Q-gate STOP/HOLD + v2.2-final other)", lb)):
        drows.append({"set": nm, "score": "v2.2-final", **dist([e["final"] for e in es])})
        drows.append({"set": nm, "score": "Q-gate", **dist([qg(e) for e in es])})
    wr("qg_distribution.csv", drows)

    def board(score, es=lb, key="graded"):
        cells = [{"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"],
                  "exp": e["kw"]["expected_action"], "credit": score(e)} for e in es]
        return {m: b[key] for m, b in V.board(cells).items()}

    def sh_board(score):   # STOP/HOLD in-scope only, same weights
        out = collections.defaultdict(list)
        runs = collections.defaultdict(list)
        for e in sh:
            runs[(e["model"], e["seed"])].append(e)
        for (m, _), es in runs.items():
            out[m].append(100 * sum(W[e["scenario"]] * score(e) for e in es) / sum(W[e["scenario"]] for e in es))
        return {m: sum(v) / len(v) for m, v in out.items()}
    B = {"final": board(lambda e: e["final"]), "qg": board(qg), "final_sh": sh_board(lambda e: e["final"]), "qg_sh": sh_board(qg)}
    models = sorted(B["final"], key=lambda m: -B["final"][m])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    rk = {k: {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -b[m]))} for k, b in B.items()}
    T = lambda a, b, ms=models: round(tau([B[a][m] for m in ms], [B[b][m] for m in ms]), 3)  # noqa: E731
    wr("qg_leaderboard.csv", [{"model": NAMES[m], "suite_final": round(B["final"][m], 2), "rank_final": rk["final"][m],
                               "suite_qgate": round(B["qg"][m], 2), "rank_qgate": rk["qg"][m],
                               "stophold_final": round(B["final_sh"][m], 2), "rank_sh_final": rk["final_sh"][m],
                               "stophold_qgate": round(B["qg_sh"][m], 2), "rank_sh_qgate": rk["qg_sh"][m]} for m in models])
    taus = {"suite tau(final, Q-gate) all / non-priv": f"{T('final', 'qg')} / {T('final', 'qg', npm)}",
            "STOP/HOLD tau(final, Q-gate) all / non-priv": f"{T('final_sh', 'qg_sh')} / {T('final_sh', 'qg_sh', npm)}"}
    say(taus)
    ex = {"InterFuser protocol-v2 run (excluded: integration defect)": {
        "suite_final": round(board(lambda e: e["final"], excluded)["interfuser"], 2), "suite_qgate": round(board(qg, excluded)["interfuser"], 2)}}
    say("excluded", ex)

    # ---- 2. Spearman --------------------------------------------------------------------------------
    sp = []
    eng = [e for e in shf if e["x"]["f"]["travel_m"] >= 1.0]
    for nm, es in (("STOP/HOLD all", sh), ("STOP/HOLD FAIL", shf), ("engaged STOP/HOLD FAIL, travel >= 1 m", eng)):
        for fn, ff in FEATS.items():
            sp.append({"set": f"{nm} (n={len(es)})", "feature (sign: + higher is safer/better)": fn,
                       "rho_final": spearman([e["final"] for e in es], [ff(e) for e in es]),
                       "rho_qgate": spearman([qg(e) for e in es], [ff(e) for e in es])})
    wr("qg_spearman.csv", sp)

    # ---- 3. discrimination --------------------------------------------------------------------------
    def discr(score):
        fails = shf
        cs = [score(e) for e in fails]
        tied = diff = 0
        for a, b in itertools.combinations(fails, 2):
            if a["x"]["label"] != b["x"]["label"]:
                diff += 1
                tied += abs(score(a) - score(b)) < EPS
        groups = collections.defaultdict(list)
        for e in fails:
            groups[e["x"]["label"]].append(score(e))
        ps = [score(e) for e in sh if e["strict"] == "PASS"]
        within = [x for g in groups.values() if len(g) > 1 for x in [max(g) - min(g)]]
        fxg = collections.defaultdict(list)
        for e in fx:
            fxg[e["model"]].append(score(e))
        rep_sd = max((max(v) - min(v)) for v in fxg.values())
        means = sorted(sum(v) / len(v) for v in fxg.values())
        by = collections.defaultdict(list)
        for e in sh:
            by[e["scenario"]].append(e)
        fp = sum(1 for es in by.values() for a in es for b in es if a["strict"] == "FAIL" and b["strict"] == "PASS" and score(a) >= score(b) - EPS)
        fmax = max(cs)
        return {"same-scenario (FAIL, PASS) pairs with FAIL >= PASS": fp, "FAIL max": round(fmax, 3),
                "FAIL episodes above the lowest PASS": sum(c > min(ps) + EPS for c in cs),
                "distinct levels, STOP/HOLD all (4 dp)": len({round(score(e), 4) for e in sh}),
                "distinct levels, STOP/HOLD FAIL (4 dp)": len({round(c, 4) for c in cs}),
                "distinct levels, STOP/HOLD FAIL (2 dp)": len({round(c, 2) for c in cs}),
                "FAIL zero share": round(sum(c < EPS for c in cs) / len(cs), 3),
                "PASS n / min / max / SD": f"{len(ps)} / {min(ps):.3f} / {max(ps):.3f} / {(sum((p - sum(ps) / len(ps)) ** 2 for p in ps) / (len(ps) - 1)) ** .5:.3f}",
                "different-label FAIL pairs tied": f"{tied}/{diff} ({tied / diff:.3f})",
                "eta^2 label -> score (FAIL)": eta2(groups),
                "median within-label range (FAIL labels with n>1)": round(sorted(within)[len(within) // 2], 3),
                "fixture replicate range (max over 6 behaviours, 3 seeds)": round(rep_sd, 4),
                "fixture behaviour means": " / ".join(f"{m:.3f}" for m in means)}
    disc = [{"score": "v2.2-final", **discr(lambda e: e["final"])}, {"score": "Q-gate", **discr(qg)}]
    wr("qg_discrimination.csv", disc)
    lab = []
    for L_ in sorted({e["x"]["label"] for e in sh}):
        es = [e for e in sh if e["x"]["label"] == L_]
        rec = {"label": L_, "n": len(es)}
        for nm, fn in (("final", lambda e: e["final"]), ("qgate", qg)):
            cs = [fn(e) for e in es]
            rec.update({f"{nm}_zero": sum(c < EPS for c in cs), f"{nm}_mean": round(sum(cs) / len(cs), 3),
                        f"{nm}_min": round(min(cs), 3), f"{nm}_max": round(max(cs), 3)})
        # within-label: does Q-gate track features?  rho with the label's most relevant feature
        rec["within-label rho(Q-gate, held latency -)"] = spearman([qg(e) for e in es], [FEATS["held-stop latency s (-; none = 25)"](e) for e in es])
        rec["within-label rho(Q-gate, min front +)"] = spearman([qg(e) for e in es], [FEATS["min front-to-line m (+)"](e) for e in es])
        rec["within-label rho(Q-gate, enforced speed -)"] = spearman([qg(e) for e in es], [FEATS["enforced max speed km/h (-)"](e) for e in es])
        lab.append(rec)
    wr("qg_labels.csv", lab)

    # ---- 4. behaviour ordering, value pairs, dominance, dangerous gains ----------------------------------
    scen = sorted({e["scenario"] for e in sh}, key=V.vlm.SCENARIO_ORDER.index)
    order_rows, syn_rows, pair_rows = [], [], []
    for sc in scen:
        es = [e for e in sh if e["scenario"] == sc]
        grp = {l: [e for e in es if e["x"]["label"] == l] for l in LAB_INTENT}
        for nm, fn in (("v2.2-final", lambda e: e["final"]), ("Q-gate", qg)):
            viol = ties = n = 0
            for (la, ga), (lb_, gb) in itertools.combinations(grp.items(), 2):
                for a in ga:
                    for b in gb:
                        n += 1
                        viol += fn(a) < fn(b) - EPS
                        ties += abs(fn(a) - fn(b)) < EPS
            order_rows.append({"scenario": sc, "score": nm, "source": "real", **{f"n_{l}": len(g) for l, g in grp.items()},
                               "ordered pairs": n, "violations (better label scores lower)": viol, "ties": ties})
            for (pa, pb) in (("NO_START", "DRIVE_THROUGH"), ("NO_START", "CREEP_NO_STOP"), ("CRAWL_THROUGH", "DRIVE_THROUGH")):
                A = [fn(e) for e in es if e["x"]["label"] == pa]
                Bv = [fn(e) for e in es if e["x"]["label"] == pb]
                if A and Bv:
                    pair_rows.append({"scenario": sc, "score": nm, "pair": f"{pa} vs {pb}", "n": f"{len(A)} / {len(Bv)}",
                                      "means": f"{sum(A) / len(A):.3f} / {sum(Bv) / len(Bv):.3f}",
                                      "ranges": f"{min(A):.3f}-{max(A):.3f} / {min(Bv):.3f}-{max(Bv):.3f}"})
        sy = {}
        for name, fn_ in behaviours(sc).items():
            if fn_ is None:
                syn_rows.append({"scenario": sc, "behaviour": name, "label": "not hostable (geometry)"})
                continue
            e = synth_ep(sc, V.vlm.SCENARIOS[sc]["expect"], CF.synth(sc, fn_))
            sy[name] = e
            syn_rows.append({"scenario": sc, "behaviour": name, "strict": e["strict"], "label": e["x"]["label"],
                             "final": round(e["final"], 4), "Q_gate": round(qg(e), 4), "components": " ".join(f"{k}{v:.2f}" for k, v in e["x"]["c"].items())})
        chain = [n_ for n_ in INTENT if n_ in sy]
        for nm, fn in (("v2.2-final", lambda e: e["final"]), ("Q-gate", qg)):
            v_ = sum(fn(sy[a]) <= fn(sy[b]) + EPS for a, b in itertools.combinations(chain, 2))
            order_rows.append({"scenario": sc, "score": nm, "source": "synthetic [S]", "ordered pairs": len(chain) * (len(chain) - 1) // 2,
                               "violations (better label scores lower)": v_, "chain": " > ".join(f"{fn(sy[n_]):.3f}" for n_ in chain),
                               "labels": " / ".join(sy[n_]["x"]["label"] for n_ in chain)})
    fxe = {m: [e for e in fx if e["model"] == m] for m in sorted({e["model"] for e in fx})}
    for nm, fn in (("v2.2-final", lambda e: e["final"]), ("Q-gate", qg)):
        ch = ["scripted_normal_stop", "scripted_late_stop", "scripted_overshoot", "scripted_constant_go"]
        v_ = sum(min(fn(e) for e in fxe[a]) <= max(fn(e) for e in fxe[b]) + EPS for a, b in itertools.combinations(ch, 2))
        order_rows.append({"scenario": "green_stop", "score": nm, "source": "CARLA fixtures (3 seeds, min of better vs max of worse)",
                           "ordered pairs": 6, "violations (better label scores lower)": v_,
                           "chain": " > ".join(f"{min(fn(e) for e in fxe[m]):.3f}-{max(fn(e) for e in fxe[m]):.3f}" for m in ch)})
    wr("qg_ordering.csv", order_rows)
    wr("qg_synthetic_behaviours.csv", syn_rows)
    wr("qg_value_pairs.csv", pair_rows)
    dom = []
    noK = lambda e: Q.qgate(e["x"], [1, 1, 0, 1, 1, 1]) if e["x"] else e["final"]  # noqa: E731
    for nm, fn in (("v2.2-final", lambda e: e["final"]), ("Q-gate", qg), ("Q-gate without K (diagnostic)", noK)):
        for setn, es in (("leaderboard STOP/HOLD", sh), ("green_stop fixtures", fx), ("leaderboard green_stop + fixtures", [e for e in sh if e["scenario"] == "green_stop"] + fx)):
            n, bad = dominance(es, fn)
            dom.append({"score": nm, "set": setn, "dominance pairs": n, "violations": len(bad), "examples": "; ".join(f"{a} ({sa}) < {b} ({sb})" for a, b, sa, sb in bad[:4])})
    wr("qg_dominance.csv", dom)
    gains = []
    for L_ in ("DRIVE_THROUGH", "CRAWL_THROUGH", "COLLISION", "EARLY_ENTRY", "CREEP_NO_STOP", "STOP_NOT_HELD", "OVERRUN_THEN_STOP", "LATE_STOP", "NO_START", "STOP_POSITION"):
        es = [e for e in sh if e["x"]["label"] == L_]   # other actions keep v2.2-final: gain 0 by construction
        g = [qg(e) - e["final"] for e in es]
        gains.append({"label (STOP/HOLD in scope)": L_, "n": len(es), "episodes gaining": sum(x > EPS for x in g),
                      "episodes losing": sum(x < -EPS for x in g), "max gain": round(max(g), 4) if g else "",
                      "sum gain": round(sum(g), 3) if g else "", "Q-gate > 0": sum(qg(e) > EPS for e in es)})
    wr("qg_dangerous_gains.csv", gains)

    # ---- 5. K diagnostics ---------------------------------------------------------------------------
    kd = []
    for e in [e for e in sh if e["strict"] == "PASS"] + [e for e in fx if e["strict"] == "PASS"]:
        x = e["x"]
        kd.append({"episode": e["id"], "K": x["c"]["K"], **x["kdiag"], "onset_s": x["f"]["onset_s"], "min_front_m": x["f"]["min_front_m"],
                   "peak_after_onset_kmh": x["f"]["peak_after_onset_kmh"]})
    wr("qg_k_diagnostics.csv", kd)
    real = [r for r in kd if not r["episode"].startswith("fixture")]
    ksum = {"STOP/HOLD PASS (in scope)": len(real), "K < 0.5": sum(r["K"] < 0.5 for r in real), "K = 0": sum(r["K"] <= EPS for r in real),
            "K=0 with a_obs_max > A_MAX": sum(r["K"] <= EPS and r["a_obs_max"] > Q.A_MAX for r in real),
            "K=0 with tick before onset": sum(r["K"] <= EPS and r["a_req_tick (t, km/h, front m)"] and r["a_req_tick (t, km/h, front m)"][0] < r["onset_s"] for r in real),
            "K_with_pre_onset < K": sum(r["K_with_pre_onset"] < r["K"] - EPS for r in real),
            "K_to_actual_stop_point: K < 0.5 / K = 0": f"{sum(r['K_to_actual_stop_point'] < 0.5 for r in real)} / {sum(r['K_to_actual_stop_point'] <= EPS for r in real)}",
            "median a_obs_max (m/s^2)": sorted(r["a_obs_max"] for r in real)[len(real) // 2]}
    say("K", ksum)
    wr("qg_k_summary.csv", [ksum])

    # ---- 6. weight + cap sensitivity ----------------------------------------------------------------
    rng = random.Random(20261030)
    ref = B["qg"]
    ref_sh = B["qg_sh"]
    settings = [("weights", [rng.gammavariate(1.0, 1.0) for _ in range(6)], 0.35, 0.25) for _ in range(N_W)]
    settings += [("caps", None, c1, c2) for c1 in (0.1, 0.2, 0.35, 0.5) for c2 in (0.1, 0.25, 0.4)]
    sens = collections.defaultdict(lambda: collections.defaultdict(list))
    flips = collections.defaultdict(lambda: collections.Counter())
    taus_ = collections.defaultdict(list)
    gs_rng = collections.defaultdict(list)
    gs_eps = [e for e in sh if e["scenario"] == "green_stop"] + fx
    for kind, w, c1, c2 in settings:
        fn = lambda e, w=w, c1=c1, c2=c2: Q.qgate(e["x"], w, c1, c2) if e["x"] else e["final"]  # noqa: E731
        b, bsh = board(fn), sh_board(fn)
        for m in models:
            sens[kind][m].append((b[m], bsh[m]))
        taus_[kind].append((tau([ref[m] for m in models], [b[m] for m in models]), tau([ref[m] for m in npm], [b[m] for m in npm]),
                            tau([ref_sh[m] for m in models], [bsh[m] for m in models])))
        for x, y in itertools.combinations(models, 2):
            if (ref[x] - ref[y]) * (b[x] - b[y]) < 0:
                flips[kind][(x, y)] += 1
        for e in gs_eps:
            gs_rng[(kind, e["id"])].append(fn(e))
    srows = []
    for kind in ("weights", "caps"):
        n = len(taus_[kind])
        for m in models:
            xs = [p[0] for p in sens[kind][m]]
            ys = [p[1] for p in sens[kind][m]]
            ranks = [1 + sum(sens[kind][o][i][0] > xs[i] for o in models) for i in range(n)]
            srows.append({"sweep": f"{kind} (n={n})", "model": NAMES[m], "suite ref": round(ref[m], 2), "suite min": round(min(xs), 2),
                          "suite max": round(max(xs), 2), "STOP/HOLD ref": round(ref_sh[m], 2), "STOP/HOLD min": round(min(ys), 2),
                          "STOP/HOLD max": round(max(ys), 2), "rank ref": rk["qg"][m], "rank min": min(ranks), "rank max": max(ranks),
                          "share at ref rank": round(sum(r == rk["qg"][m] for r in ranks) / n, 3)})
    wr("qg_sensitivity_models.csv", srows)
    trows = []
    for kind in ("weights", "caps"):
        for i, nm in enumerate(("suite all", "suite non-priv", "STOP/HOLD all")):
            ts = sorted(t[i] for t in taus_[kind])
            trows.append({"sweep": kind, "tau vs reference": nm, "min": round(ts[0], 3), "p05": round(ts[int(.05 * len(ts))], 3),
                          "median": round(ts[len(ts) // 2], 3), "max": round(ts[-1], 3), "share tau = 1": round(sum(t > 1 - EPS for t in ts) / len(ts), 3)})
    wr("qg_sensitivity_tau.csv", trows)
    wr("qg_sensitivity_flips.csv", [{"sweep": k, "pair": f"{NAMES[x]} - {NAMES[y]}", "ref": f"{ref[x]:.2f} / {ref[y]:.2f}",
                                     "flip share": round(n / len(taus_[k]), 3)} for k in ("weights", "caps")
                                    for (x, y), n in flips[k].most_common()])
    wr("qg_sensitivity_green_stop.csv", [{"episode": e["id"], "label": e["x"]["label"], "Q_gate ref": round(qg(e), 3),
                                          "weights min-max": f"{min(gs_rng[('weights', e['id'])]):.3f}-{max(gs_rng[('weights', e['id'])]):.3f}",
                                          "caps min-max": f"{min(gs_rng[('caps', e['id'])]):.3f}-{max(gs_rng[('caps', e['id'])]):.3f}"} for e in gs_eps])
    # fixture order under every sweep setting
    ford = collections.Counter()
    for kind, w, c1, c2 in settings:
        fn = lambda e, w=w, c1=c1, c2=c2: Q.qgate(e["x"], w, c1, c2)  # noqa: E731
        ch = ["scripted_normal_stop", "scripted_late_stop", "scripted_overshoot", "scripted_constant_go"]
        ford[kind] += any(min(fn(e) for e in fxe[a]) <= max(fn(e) for e in fxe[b]) + EPS for a, b in zip(ch, ch[1:]))
    say("fixture order broken in settings:", dict(ford))

    # ---- 7. F2 policies -----------------------------------------------------------------------------
    pols, held = X.policies()
    tot = sum(W.values())
    prow = []
    for pn, fn_ in pols.items():
        acc = {"final": 0.0, "qg": 0.0}
        for sc in V.vlm.SCENARIO_ORDER:
            e = synth_ep(sc, V.vlm.SCENARIOS[sc]["expect"], CF.synth(sc, fn_))
            acc["final"] += W[sc] * e["final"]
            acc["qg"] += W[sc] * qg(e)
        prow.append({"policy": pn, "graded_final": round(100 * acc["final"] / tot, 2), "graded_qgate": round(100 * acc["qg"] / tot, 2)})
    wr("qg_policies.csv", prow)
    best_f, best_q = max(B["final"][m] for m in npm), max(B["qg"][m] for m in npm)
    f2 = {"best policy final": max(p["graded_final"] for p in prow), "above best non-priv final": sum(p["graded_final"] > best_f for p in prow),
          "best non-priv final": round(best_f, 2), "best policy Q-gate": max(p["graded_qgate"] for p in prow),
          "best policy name Q-gate": max(prow, key=lambda p: p["graded_qgate"])["policy"],
          "above best non-priv Q-gate": sum(p["graded_qgate"] > best_q for p in prow), "best non-priv Q-gate": round(best_q, 2)}
    say("F2", f2)
    wr("qg_f2_summary.csv", [f2])

    for d in drows:
        say(d)
    for d in disc:
        say(d)
    for d in dom:
        say(d)
    for g in gains:
        say(g)
    for t in trows:
        say(t)
    for m in models:
        say(NAMES[m], round(B["final"][m], 2), rk["final"][m], round(B["qg"][m], 2), rk["qg"][m], round(B["final_sh"][m], 2), round(B["qg_sh"][m], 2))
    (Q.OUT / "qg_stdout.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
