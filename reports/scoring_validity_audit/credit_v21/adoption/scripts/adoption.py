"""v2.1-candidate adoption check on recorded telemetry (no rollouts; strict verdicts unchanged).

    python <audit>/credit_v21/adoption/scripts/adoption.py          # from the workspace root

Reuses credit_v21.py (patched copies of the published scorer). Writes only to credit_v21/adoption/data/:
  subset_boards.csv          graded per model for all 32 on/off combinations of F1, F6, F3, F7, F2
  fix_shapley.csv            per-model fixed-order, single-on, single-off and Shapley attribution of v2 -> v2.1
  tcp_episodes.csv           TCP per episode: credit terms v2 / v2.1, per-fix Shapley (graded points), telemetry facts
  f1_changed_episodes.csv    every episode F1 changes: credit v2 / F1 only / v2.1 and what the ego did in the window
  tcp_crawl_synthetic.csv    synthetic STOP crawl launched at onset (TCP-like) under v2, F1 only and v2.1
  f3_bounds.csv              per-model graded: v2.1 without F3, v2.1, v2.1 with +1 hidden contact, all hidden distinct
  consistency.csv            credit = 1 <=> strict PASS, FAIL >= PASS within a scenario, ordering checks (real data)
  monotonicity.csv           synthetic case families under v2 and v2.1 (expected orderings, violations)
  tight_margin.csv           the 31 tight_onset_margin crossings: v2 / v2.1 credit
  tight_margin_boards.csv    v2.1 graded with the 31 episodes included / excluded / both scenarios excluded
  stdout.txt                 console summary
"""
from __future__ import annotations

import collections
import csv
import itertools
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data"
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
import credit_v21 as V  # noqa: E402

FIX = V.ALL_FIXES                                   # fixed attribution order: F1, F6, F3, F7, F2
AUD = V.AUD
EPS = 1e-6
LOG = []


def say(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s)


def wr(name, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with (OUT / name).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def tau(models, a, b):
    return V.SN.kendall_tau_b([a[m] for m in models], [b[m] for m in models])


def graded(cells):
    return {m: s["graded"] for m, s in V.board(cells).items()}


def ranks(g, models):
    return {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -g[m]))}


# ------------------------------------------------------------------------------------------------ telemetry facts
def f(r, k):
    return V.G._finite_float(r.get(k))


def facts(e, g):
    """What the ego did, from the scored telemetry (t <= 25 s; STOP/HOLD also <= directive end)."""
    aw = (g.get("evidence") or {}).get("active_window") or {}
    onset, end = aw.get("onset_s"), aw.get("active_end_s")
    hz = [r for r in e["rows"] if float(r["sim_time"]) <= 25.0 + 1e-9]
    stop_like = e["kw"]["expected_action"] in ("STOP", "HOLD")
    rows = [r for r in hz if float(r["sim_time"]) <= end + 1e-9] if stop_like else hz
    t = [float(r["sim_time"]) for r in rows]
    v = [f(r, "ego_speed_kmh") or 0.0 for r in rows]
    after = [i for i in range(len(rows)) if t[i] >= onset]
    i_on = after[0] if after else 0
    i_move = next((i for i in after if v[i] > 1.0), None)
    fronts = [(t[i], f(rows[i], "stopline_front_m")) for i in range(len(rows)) if f(rows[i], "stopline_front_m") is not None]
    cross = next((tt for tt, x in fronts if x < 0), None)
    stopped_s = sum(0.05 for i in after if v[i] <= 1.0)
    win_s = max(0.05, (t[-1] - onset + 0.05)) if after else 0.0
    cc = [int(float(r.get("collision_count") or 0)) for r in rows]
    t_coll = next((t[i] for i in range(len(rows)) if cc[i] > 0), None)
    v_coll = next((v[i] for i in range(len(rows)) if cc[i] > 0), None)
    md = [x for x in e["ids"][:max(cc) if cc else 0]]
    types = sorted({ty for _, ty in md})
    lat = [abs(f(r, "ego_lateral_m") or 0.0) for r in rows]
    thr = [f(r, "raw_throttle") or 0.0 for r in rows]
    brk = [f(r, "raw_brake") or 0.0 for r in rows]
    # after the first collision: does the ego stay pinned (speed <= 1 km/h for the rest of the scored rows)?
    pinned = None
    if t_coll is not None:
        rest = [v[i] for i in range(len(rows)) if t[i] >= t_coll + 1.0]
        pinned = bool(rest) and max(rest) <= 1.0
    return {
        "onset_s": onset, "scored_end_s": round(t[-1], 2),
        "speed_at_onset_kmh": round(v[i_on], 2), "t_first_motion_after_onset_s": None if i_move is None else round(t[i_move], 2),
        "peak_speed_scored_kmh": round(max(v), 2), "progress_scored_m": round(V.approach(e["rows"], end if stop_like else 25.0)["progress"], 2),
        "front_at_onset_m": next((round(x, 2) for tt, x in fronts if tt >= onset), None),
        "min_front_to_line_m": round(min(x for _, x in fronts), 2) if fronts else None, "t_cross_s": cross,
        "stopped_share_after_onset": round(min(1.0, stopped_s / win_s), 3) if after else None,
        "speed_at_scored_end_kmh": round(v[-1], 2), "max_lateral_m": round(max(lat), 2),
        "max_raw_throttle": round(max(thr), 2), "max_raw_brake_after_onset": round(max([brk[i] for i in after] or [0.0]), 2),
        "collision_callbacks": max(cc) if cc else 0, "t_first_collision_s": t_coll,
        "speed_at_first_collision_kmh": None if v_coll is None else round(v_coll, 2),
        "collision_types": ";".join(t_ for t_ in types if t_), "pinned_after_collision": pinned,
    }


# ------------------------------------------------------------------------------------------------ synthetic families
def monotonicity():
    CF, gs, rp = V.CF, "green_stop", "red_proceed"
    F0 = float(CF.template(gs)[0]["stopline_front_m"])
    stop_then_go = CF.ramp_to_stop(15, 0.2, 2.0, F0)
    fam = collections.OrderedDict()
    fam["stop clearance 6/4/2/1/0.5 m (non-increasing)"] = [
        (f"{x} m", gs, "STOP", CF.ramp_to_stop(15, 0.2, x, F0), {}) for x in (6.0, 4.0, 2.0, 1.0, 0.5)]
    fam["overshoot at 5 km/h, 0..50 m (non-increasing)"] = [
        (f"{o} m", gs, "STOP", CF.ramp_to_stop(5, 0.2, -o, F0, a=1.0), {}) for o in (0.0, 0.1, 0.5, 2.0, 10.0, 50.0)]
    fam["overshoot at 25 km/h, 0..50 m (non-increasing)"] = [
        (f"{o} m", gs, "STOP", CF.ramp_to_stop(25, 0.2, -o, F0, a=4.0), {}) for o in (0.0, 0.1, 0.5, 2.0, 10.0, 50.0)]
    fam["approach start 0/0.5/1/1.5/3 s (non-increasing)"] = [
        (f"{t0} s", gs, "STOP", CF.ramp_to_stop(15, t0, 0.5, F0), {}) for t0 in (0.0, 0.5, 1.0, 1.5, 3.0)]
    fam["collision events 0.5 s apart: 0/1/3/6 (non-increasing)"] = [
        (f"{n}", gs, "STOP", stop_then_go, {"collisions_at": tuple(5 + i * 0.5 for i in range(n))}) for n in (0, 1, 3, 6)]
    fam["collision events 1.5 s apart: 0/1/3/6 (non-increasing)"] = [
        (f"{n}", gs, "STOP", stop_then_go, {"collisions_at": tuple(5 + i * 1.5 for i in range(n))}) for n in (0, 1, 3, 6)]
    fam["later braking from 8 km/h (non-increasing)"] = [
        (f"brake {t0 + 1:.1f} s", gs, "STOP",
         (lambda t0: (lambda t: 0.0 if t < 0.2 else (8.0 if t < t0 + 1.0 else max(0.0, 8.0 - 20 * (t - t0 - 1.0)))))(t0), {})
        for t0 in (0.2, 2.0, 4.0, 6.0)]
    fam["PROCEED start delay 0..8 s (non-increasing)"] = [
        (f"{d} s", rp, "PROCEED", (lambda d: (lambda t: 0.0 if t < 1.0 + d else min(20.0, 9 * (t - 1.0 - d))))(d), {})
        for d in (0.0, 1.0, 2.9, 3.1, 5.0, 8.0)]
    fam["stop at 2 m, then go at 14 s vs hold (equal)"] = [
        ("go at 14 s", gs, "STOP", lambda t: stop_then_go(t) if t < 14 else min(20, 5 * (t - 14)), {}),
        ("hold", gs, "STOP", stop_then_go, {})]
    rows, scores = [], {}
    for name, cases in fam.items():
        for lab, sc, act, fn, kw_ in cases:
            tel = CF.synth(sc, fn, **kw_)
            kw = dict(scenario=sc, expected_action=act, max_reaction_time=3.0, protocol="v2")
            s = V.S.score_episode_from_telemetry({"scenario": sc}, tel, **kw)
            c0, c1 = V.scored({"scenario": sc}, tel, kw, fixes=())[0], V.scored({"scenario": sc}, tel, kw)[0]
            scores.setdefault(name, []).append((lab, s.get("verdict"), c0, c1))
    for name, xs in scores.items():
        for ver, k in (("v2", 2), ("v2.1", 3)):
            seq = [x[k] for x in xs]
            if "(equal)" in name:
                viol = int(abs(seq[0] - seq[1]) > EPS)
            else:
                viol = sum(seq[i + 1] > seq[i] + EPS for i in range(len(seq) - 1))
            rows.append({"family": name, "version": ver, "cases": " | ".join(x[0] for x in xs),
                         "strict": " | ".join(x[1] for x in xs), "credits": " | ".join(f"{c:.3f}" for c in seq), "violations": viol})
    # cross-family check (finding F4): at the same overshoot, 25 km/h should not score above 5 km/h
    slow = scores["overshoot at 5 km/h, 0..50 m (non-increasing)"]
    fast = scores["overshoot at 25 km/h, 0..50 m (non-increasing)"]
    for ver, k in (("v2", 2), ("v2.1", 3)):
        rows.append({"family": "overshoot: 25 km/h <= 5 km/h at the same overshoot", "version": ver,
                     "cases": " | ".join(x[0] for x in slow), "strict": "",
                     "credits": " | ".join(f"{a[k]:.3f}<={b[k]:.3f}" for a, b in zip(fast, slow)),
                     "violations": sum(a[k] > b[k] + EPS for a, b in zip(fast, slow))})
    return rows


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    eps = V.SN.capture()
    assert len(eps) == 650
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
        e["id"] = f"{e['model']}/s{e['seed']}/{e['scenario']}"
        e["ids"] = V.event_ids(e["ep"])
        e["s"] = V.S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        e["strict"] = "INVALID" if e["forced_invalid"] else e["s"].get("verdict")
    base = lambda e: {"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"],  # noqa: E731
                      "exp": e["kw"]["expected_action"]}

    # ---- 32 fix subsets ----------------------------------------------------------------------------
    subsets = [tuple(x for x in FIX if x in combo) for r in range(len(FIX) + 1) for combo in itertools.combinations(FIX, r)]
    cred = {}                                        # (episode id, subset) -> credit
    comp = {}
    for e in eps:
        for sub in subsets:
            c, k, g = V.scored(e["res"], e["rows"], e["kw"], e["ids"], fixes=sub, forced_invalid=e["forced_invalid"])
            cred[(e["id"], sub)] = c
            if sub in ((), FIX):
                comp[(e["id"], sub)] = (k, g)
    boards = {sub: graded([{**base(e), "credit": cred[(e["id"], sub)]} for e in eps]) for sub in subsets}
    models = sorted(boards[()], key=lambda m: -boards[()][m])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    wr("subset_boards.csv", [{"fixes_on": "+".join(sub) or "none", **{m: round(boards[sub][m], 4) for m in models}} for sub in subsets])

    def shap(val, i):                                # val: subset -> number; Shapley value of FIX[i]
        n, x = len(FIX), FIX[i]
        tot = 0.0
        for sub in subsets:
            if x in sub:
                continue
            w = math.factorial(len(sub)) * math.factorial(n - len(sub) - 1) / math.factorial(n)
            tot += w * (val(tuple(y for y in FIX if y in sub or y == x)) - val(sub))
        return tot

    def ordered(val):
        out, cur = {}, ()
        for x in FIX:
            nxt = tuple(y for y in FIX if y in cur or y == x)
            out[x] = val(nxt) - val(cur)
            cur = nxt
        return out

    attr = []
    for m in models:
        val = lambda sub: boards[sub][m]  # noqa: E731
        od = ordered(val)
        row = {"model": m, "graded_v2": round(val(()), 2), "graded_v21": round(val(FIX), 2), "delta": round(val(FIX) - val(()), 2)}
        for i, x in enumerate(FIX):
            row.update({f"{x}_ordered": round(od[x], 3), f"{x}_single_on": round(val((x,)) - val(()), 3),
                        f"{x}_single_off": round(val(FIX) - val(tuple(y for y in FIX if y != x)), 3), f"{x}_shapley": round(shap(val, i), 3)})
        attr.append(row)
    wr("fix_shapley.csv", attr)

    # ---- 1. TCP item by item -----------------------------------------------------------------------
    tcp = [e for e in eps if e["model"] == "tcp"]
    wsum = sum(V.W[e["scenario"]] for e in tcp)
    trows = []
    for e in tcp:
        k0, _ = comp[(e["id"], ())]
        k1, g1 = comp[(e["id"], FIX)]
        pts = lambda sub: 100 * V.W[e["scenario"]] * cred[(e["id"], sub)] / wsum  # noqa: E731
        od = ordered(pts)
        trows.append({"episode": e["id"], "scenario": e["scenario"], "expected": e["kw"]["expected_action"], "strict": e["strict"],
                      "strict_reason": (e["s"].get("reason") or "")[:140], "weight": V.W[e["scenario"]],
                      "credit_v2": cred[(e["id"], ())], "credit_v21": cred[(e["id"], FIX)],
                      "graded_points_delta": round(pts(FIX) - pts(()), 3),
                      **{f"{x}_shapley_pts": round(shap(pts, i), 3) for i, x in enumerate(FIX)},
                      **{f"{x}_ordered_pts": round(od[x], 3) for x in FIX},
                      "A_v2": V.r6(k0["A"]), "A_v21": V.r6(k1["A"]), "e_v2": V.r6(k0["e"]), "e_v21": V.r6(k1["e"]),
                      "F_lat_v2": V.r6(k0["F_lat"]), "F_lat_v21": V.r6(k1["F_lat"]), "latency_s_v21": k1["latency_s"],
                      "latency_trigger_v21": k1["latency_trigger"],
                      "F_safe_v2": V.r6(k0["F_safe"]), "F_safe_v21": V.r6(k1["F_safe"]), "contacts_v21": k1["n_collision"],
                      "G_move": k1["G_move"], "E_app": k1["E_app"], **facts(e, g1)})
    wr("tcp_episodes.csv", trows)

    # every episode F1 changes: what the ego did during the STOP/HOLD window
    f1rows = []
    for e in eps:
        if abs(cred[(e["id"], ("F1",))] - cred[(e["id"], ())]) > EPS:
            k1, g1 = comp[(e["id"], FIX)]
            f1rows.append({"episode": e["id"], "strict": e["strict"], "credit_v2": cred[(e["id"], ())], "credit_F1_only": cred[(e["id"], ("F1",))],
                           "credit_v21": cred[(e["id"], FIX)], "latency_trigger_v21": k1["latency_trigger"], **facts(e, g1)})
    wr("f1_changed_episodes.csv", f1rows)

    # synthetic STOP crawl launched at onset (TCP-like): ~7.9 km/h launch, settle to 5.5 km/h, never stop
    def crawl(settle_s):
        def fn(t):
            if t < 1.05:
                return 0.0
            u = t - 1.05
            if u < 0.5:
                return 15.8 * u
            return max(5.5, 7.9 - 2.4 / settle_s * (u - 0.5))
        return fn
    syn = []
    for sc in ("signal_off", "flagger_control", "conflicting_authorities", "two_civilians_disagree", "fake_vest_director"):
        kw = dict(scenario=sc, expected_action="STOP", max_reaction_time=3.0, protocol="v2")
        for name, fn in (("settle 7.9 -> 5.5 km/h over 1.0 s (0.67 m/s2)", crawl(1.0)),
                         ("settle 7.9 -> 5.5 km/h over 0.4 s (1.67 m/s2)", crawl(0.4))):
            tel = V.CF.synth(sc, fn)
            st = V.S.score_episode_from_telemetry({"scenario": sc}, tel, **kw)
            c1, k1, _ = V.scored({"scenario": sc}, tel, kw)
            syn.append({"scenario": sc, "profile": name, "strict": st.get("verdict"),
                        "credit_v2": V.scored({"scenario": sc}, tel, kw, fixes=())[0],
                        "credit_F1_only": V.scored({"scenario": sc}, tel, kw, fixes=("F1",))[0], "credit_v21": c1,
                        "latency_trigger_v21": k1["latency_trigger"], "latency_s_v21": k1["latency_s"], "F_lat_v21": k1["F_lat"],
                        "min_front_to_line_m": round(min(r["stopline_front_m"] for r in tel if float(r["sim_time"]) <= 14.0), 2)})
    wr("tcp_crawl_synthetic.csv", syn)

    # ---- 2. F3: without F3, and the 20-event identity bounds -------------------------------------------
    no_f3 = tuple(x for x in FIX if x != "F3")
    g_full, g_nof3 = boards[FIX], boards[no_f3]
    orig_f3 = V.safety_f3

    def plus_one(ids, gap=None):                     # one hidden contact whenever a counted event lacks an actor id
        n_md = sum(a is not None for a, _ in ids)

        def _s(rows):
            n = V.distinct_contacts(rows, ids, gap)
            hidden = max((int(float(r.get("collision_count") or 0)) for r in rows), default=0) > n_md
            m = n + 1 if hidden else n
            return V.band(m), {"collision_count": m, "severity_signal": "distinct_contacts+1"}
        return _s
    bound = {}
    for name, ids_of, sf in (("all_hidden_distinct", lambda e: [(a if a is not None else ("hidden", k), t)
                                                                 for k, (a, t) in enumerate(e["ids"])], orig_f3),
                             ("plus_one_hidden", lambda e: e["ids"], plus_one)):
        V.safety_f3 = sf
        try:
            cells = [{**base(e), "credit": V.scored(e["res"], e["rows"], e["kw"], ids_of(e), forced_invalid=e["forced_invalid"])[0]}
                     for e in eps]
        finally:
            V.safety_f3 = orig_f3
        bound[name] = graded(cells)
        bound[name + "_changed"] = sum(abs(c["credit"] - cred[(e["id"], FIX)]) > EPS for c, e in zip(cells, eps))
    r_full, r_nof3 = ranks(g_full, models), ranks(g_nof3, models)
    r_hi, r_p1 = ranks(bound["all_hidden_distinct"], models), ranks(bound["plus_one_hidden"], models)
    f3rows = [{"model": m, "graded_v21_without_F3": round(g_nof3[m], 2), "graded_v21": round(g_full[m], 2),
               "delta_F3": round(g_full[m] - g_nof3[m], 2), "rank_without_F3": r_nof3[m], "rank_v21": r_full[m],
               "graded_plus_one_hidden": round(bound["plus_one_hidden"][m], 2), "rank_plus_one_hidden": r_p1[m],
               "graded_all_hidden_distinct": round(bound["all_hidden_distinct"][m], 2), "rank_all_hidden_distinct": r_hi[m]}
              for m in models]
    # can any pair swap inside the per-model interval [all hidden distinct, none distinct (= v2.1)]?
    lo, hi = bound["all_hidden_distinct"], g_full
    flips = [(a, b) for a, b in itertools.combinations(models, 2) if not (lo[a] > hi[b] or lo[b] > hi[a])]
    wr("f3_bounds.csv", f3rows)
    say(f"F3: tau v2.1 vs v2.1-without-F3 all {tau(models, g_nof3, g_full):.3f}, non-priv {tau(npm, g_nof3, g_full):.3f}")
    say(f"F3 bounds: +1 hidden changes {bound['plus_one_hidden_changed']} episodes, tau {tau(models, g_full, bound['plus_one_hidden']):.3f}; "
        f"all hidden distinct changes {bound['all_hidden_distinct_changed']} episodes, tau {tau(models, g_full, lo):.3f}")
    say("F3 bounds: pairs whose intervals overlap (rank can flip):", flips)

    # ---- 3a. consistency on real data ------------------------------------------------------------------
    cons = []
    for ver, sub in (("v2", ()), ("v2.1", FIX)):
        c = {e["id"]: cred[(e["id"], sub)] for e in eps}
        full_not_pass = [e["id"] for e in eps if c[e["id"]] >= 1 - EPS and e["strict"] != "PASS"]
        pass_not_full = [e["id"] for e in eps if e["strict"] == "PASS" and c[e["id"]] < 1 - EPS]
        by_sc = collections.defaultdict(list)
        for e in eps:
            by_sc[e["scenario"]].append(e)
        fail_ge_pass = sum(1 for es in by_sc.values() for a in es for b in es
                           if a["strict"] == "FAIL" and b["strict"] == "PASS" and c[a["id"]] >= c[b["id"]] - EPS)
        cons.append({"version": ver, "credit1_not_pass": len(full_not_pass), "pass_below_1": len(pass_not_full),
                     "pass_below_1_episodes": ";".join(pass_not_full), "fail_ge_pass_pairs_same_scenario": fail_ge_pass})
    wr("consistency.csv", cons)
    mono = monotonicity()
    wr("monotonicity.csv", mono)

    # ---- 3c. tight onset margin --------------------------------------------------------------------------
    cc = list(csv.DictReader((AUD / "crossing_analysis" / "crossing_causes.csv").open(encoding="utf-8")))
    tight = {r["episode_id"] for r in cc if r["relevance"] == "binding" and r["cause_class"] == "tight_onset_margin"}
    assert len(tight) == 31, len(tight)
    byid = {e["id"]: e for e in eps}
    wr("tight_margin.csv", [{"episode": i, "strict": byid[i]["strict"], "credit_v2": cred[(i, ())], "credit_v21": cred[(i, FIX)]}
                            for i in sorted(tight)])
    full_cells = [{**base(e), "credit": cred[(e["id"], FIX)]} for e in eps]
    g_ex = graded([c for c, e in zip(full_cells, eps) if e["id"] not in tight])
    g_sc = graded([c for c in full_cells if c["scenario"] not in ("occluded_officer", "sequential_directive")])
    r_ex, r_sc = ranks(g_ex, models), ranks(g_sc, models)
    wr("tight_margin_boards.csv", [{"model": m, "graded_v21": round(g_full[m], 2), "rank_v21": r_full[m],
                                    "graded_excl_31": round(g_ex[m], 2), "delta_excl_31": round(g_ex[m] - g_full[m], 2), "rank_excl_31": r_ex[m],
                                    "graded_excl_2_scenarios": round(g_sc[m], 2), "delta_excl_2_scenarios": round(g_sc[m] - g_full[m], 2),
                                    "rank_excl_2_scenarios": r_sc[m]} for m in models])
    say(f"tight: tau excl-31 all {tau(models, g_full, g_ex):.3f} non-priv {tau(npm, g_full, g_ex):.3f}; "
        f"excl-2-scenarios all {tau(models, g_full, g_sc):.3f} non-priv {tau(npm, g_full, g_sc):.3f}")
    for r in attr:
        say(r)
    for r in cons:
        say(r)
    for r in mono:
        say(r)
    (OUT / "stdout.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
