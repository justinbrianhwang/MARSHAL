"""v2.2-candidate graded credit on recorded telemetry (experimental; strict verdicts unchanged).

    python <audit>/credit_v22/scripts/credit_v22.py          # from the workspace root

v2.2 repairs scoring-implementation defects only (computed value != intended criterion):
  F1  STOP/HOLD engagement approach window anchored at the first post-onset motion (A', as in v2.1)
  F6h STOP/HOLD response = reaching a HELD stop: the first hold_dwell_min_s (2.0 s) window at <= stop_speed_kmh
      (1 km/h) on every row with forward drift <= hold_dwell_max_drift_m (0.5 m), starting after onset (after
      the first post-onset motion for an ego at rest at onset). Latency = window start - onset; for an ego at rest at
      onset it is window start - its first post-onset motion (the launch is a violation that the action terms already
      score; latency times how quickly it is corrected). An ego at rest at onset that never moves again keeps latency 0. No held stop -> no response -> latency factor 0 (as published for
      "no reaction detected"). Deceleration and low-speed driving are not a response.
  F3  safety factor on distinct contacts (as in v2.1)
  F7  YIELD slowdown over every post-onset row (as in v2.1)
The F2 approach-evidence gate (a3) is NOT part of v2.2; "v2.2+a3" is reported only as an optional F2 mitigation.

Outputs: credit_v22/data/*.csv and stdout.txt. Reads the v2.1 module and the audit loaders; writes nothing else.
"""
from __future__ import annotations

import collections
import csv
import itertools
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data"
AUD = HERE.parents[1]
sys.path[:0] = [str(AUD / "credit_v21" / "scripts"), str(AUD / "credit_v21" / "adoption" / "scripts")]
import credit_v21 as V  # noqa: E402  (patched published functions, loaders, F1/F3/F7)
import adoption as AD  # noqa: E402  (facts(): what the ego did in the scored window)

G, S, CF, W = V.G, V.S, V.CF, V.W
STOP_KMH = G.GRADED_THRESHOLDS["stop_speed_kmh"]
DWELL_S = G.STRICT_THRESHOLDS["hold_dwell_min_s"]
DRIFT_M = G.STRICT_THRESHOLDS["hold_dwell_max_drift_m"]
VERSIONS = ("v2", "v2.1", "v2.2", "v2.2+a3")
EPS = 1e-6
LOG = []
_published_latency = G._latency_factor


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


# ------------------------------------------------------------------------------------------------ F6h
def held_stop(rows, onset):
    """-> (latency_s or None, trigger). See the module docstring."""
    after = [r for r in rows if float(r["sim_time"]) >= onset - 1e-9]
    if not after:
        return None, "none"
    v = lambda r: G._finite_float(r.get("ego_speed_kmh")) or 0.0  # noqa: E731
    at_rest = v(after[0]) <= STOP_KMH
    t_move = next((float(r["sim_time"]) for r in after if v(r) > STOP_KMH), None)
    if at_rest and t_move is None:
        return 0.0, "at_rest_never_moved"
    start = t_move if at_rest else onset
    span = []
    for r in [r for r in after if float(r["sim_time"]) >= start - 1e-9] + [None]:
        if r is not None and v(r) <= STOP_KMH:
            span.append(r)
            continue
        ts = [float(s["sim_time"]) for s in span]
        fwd = [G._finite_float(s.get("ego_forward_m")) for s in span]
        for i in range(len(span)):  # first DWELL_S window inside the span whose forward drift is <= DRIFT_M
            j = next((j for j in range(i, len(span)) if ts[j] - ts[i] >= DWELL_S - 1e-9), None)
            if j is None:
                break
            win = [x for x in fwd[i:j + 1] if x is not None]
            if win and max(win) - min(win) <= DRIFT_M:
                return ts[i] - start, "held_stop" if not at_rest else "held_stop_after_launch"
        span = []
    return None, "no_held_stop"


def latency_v22(rows, action, scenario, onset, *, action_credit, reaction_budget=3.0):
    if action not in ("STOP", "HOLD"):
        return _published_latency(rows, action, scenario, onset, action_credit=action_credit, reaction_budget=reaction_budget)
    lat, trig = held_stop(rows, onset)
    if lat is None:
        if action_credit >= 0.999:  # published fallback: already-compliant hold
            return 1.0, {"latency_s": 0.0, "trigger": "already_compliant_hold"}
        return 0.0, {"latency_s": None, "trigger": trig}
    full = max(G.GRADED_THRESHOLDS["latency_full_credit_s"], float(reaction_budget))
    zero = full + G.GRADED_THRESHOLDS["latency_zero_credit_s"] - G.GRADED_THRESHOLDS["latency_full_credit_s"]
    return G._linear_credit(lat, full, zero), {"latency_s": round(lat, 4), "trigger": trig}


def scored(res, rows, kw, ids=(), version="v2.2", forced_invalid=False):
    """-> (credit, components, graded verdict) under v2 / v2.1 / v2.2 / v2.2+a3."""
    if version == "v2":
        return V.scored(res, rows, kw, ids, fixes=(), forced_invalid=forced_invalid)
    if version == "v2.1":
        return V.scored(res, rows, kw, ids, fixes=V.ALL_FIXES, forced_invalid=forced_invalid)
    with V.patched(("F1", "F3", "F7"), list(ids)):
        G._latency_factor = latency_v22
        g = G.score_episode_from_telemetry(res, rows, **kw)
    ev = g.get("evidence") or {}
    credit = 0.0 if forced_invalid else float(g.get("credit") or 0.0)
    gate = 1
    if version == "v2.2+a3" and V.f2_applies(kw) and not g.get("invalid"):
        gate = int(V.gate_a3(V._approach_of(rows, g)))
        credit *= gate
    comp = {"A": g.get("raw_action_credit"), "F_lat": g.get("latency_factor"), "F_safe": g.get("safety_factor"),
            "G_move": int(bool((ev.get("movement_gate") or {}).get("engaged"))),
            "e": (g.get("component_credits") or {}).get("engagement_gate"), "E_app": gate,
            "n_collision": (ev.get("safety") or {}).get("collision_count"),
            "latency_s": (ev.get("latency") or {}).get("latency_s"), "latency_trigger": (ev.get("latency") or {}).get("trigger")}
    return round(min(max(credit, 0.0), 1.0), 6), comp, g


def r4(x):
    return None if x is None else round(float(x), 4)


def terms(c, k):
    return f"{c:.3f} (A {r4(k['A'])}, e {r4(k['e'])}, F_lat {r4(k['F_lat'])}, F_safe {r4(k['F_safe'])})"


# ------------------------------------------------------------------------------------------------ synthetic sets
def policies():
    """The 48 instruction-independent policies of credit_v21 (same definitions)."""
    pols = {"park": lambda t: 0.0,
            "audit lurch (8 km/h, ~1.6 m)": lambda t: 9.0 * t if t < 0.889 else max(0.0, 8.0 - 14.4 * (t - 0.889)),
            "constant 20 km/h": lambda t: min(20.0, 9 * t)}
    for v in (6, 8, 10, 12, 15):
        for dist in (1.2, 1.6, 2.0, 2.5, 3.0, 4.0):
            pols[f"lurch target {v} km/h, {dist} m"] = CF.ramp_to_stop(v, 0.2, 0.0, dist, a=2.5)
    for v in (5, 10, 30):
        pols[f"constant {v} km/h"] = (lambda v: lambda t: min(float(v), 9 * t))(v)
    for dist in (5.0, 10.0, 20.0):
        pols[f"stop after {dist:g} m at 20 km/h"] = CF.ramp_to_stop(20, 0.2, 0.0, dist, a=2.5)
    held = set()
    for t_go in (2.0, 4.0, 6.0, 8.0, 10.0, 13.5, 14.5, 16.5, 20.0):
        lurch = CF.ramp_to_stop(10, 0.2, 0.0, 2.5, a=2.5)
        name = f"lurch 2.5 m, go 20 km/h at {t_go:g} s"
        pols[name] = (lambda lurch, t_go: lambda t: lurch(t) if t < t_go else min(20.0, 9 * (t - t_go)))(lurch, t_go)
        held.add(name)
    return pols, held


def crawl(settle_s):
    """TCP-like: at rest until 1.05 s, launch to 7.9 km/h in 0.5 s, settle to 5.5 km/h over settle_s, never stop."""
    def fn(t):
        if t < 1.05:
            return 0.0
        u = t - 1.05
        return 15.8 * u if u < 0.5 else max(5.5, 7.9 - 2.4 / settle_s * (u - 0.5))
    return fn


def behaviours(F0):
    """Synthetic STOP behaviours on the green_stop template (front bumper F0 m before the line at spawn)."""
    late = lambda t: 0.0 if t < 0.2 else (8.0 if t < 7.0 else max(0.0, 8.0 - 20 * (t - 7.0)))  # noqa: E731
    stop_go = CF.ramp_to_stop(15, 0.2, 2.0, F0)
    return collections.OrderedDict([
        ("normal stop (15 km/h, stop 2 m before the line)", CF.ramp_to_stop(15, 0.2, 2.0, F0)),
        ("launch after onset (1.5 s), stop 2 m before the line", CF.ramp_to_stop(15, 1.5, 2.0, F0)),
        ("careful short stop (6 m before the line, ~1.4 m travel)", CF.ramp_to_stop(15, 0.2, 6.0, F0)),
        ("F2 lurch (audit, ~1.6 m)", lambda t: 9.0 * t if t < 0.889 else max(0.0, 8.0 - 14.4 * (t - 0.889))),
        ("late stop (8 km/h, brake at 7 s)", late),
        ("stop 0.5 m before the line (inside the 1 m margin)", CF.ramp_to_stop(15, 0.2, 0.5, F0)),
        ("overrun: stop 2 m past the line (25 km/h)", CF.ramp_to_stop(25, 0.2, -2.0, F0, a=4.0)),
        ("overrun: stop 10 m past the line (25 km/h)", CF.ramp_to_stop(25, 0.2, -10.0, F0, a=4.0)),
        ("stop-and-hold, go after the directive (14 s)", lambda t: stop_go(t) if t < 14 else min(20, 5 * (t - 14))),
        ("crawl from onset, settle 1.67 m/s2", crawl(0.4)),
        ("crawl from onset, settle 0.67 m/s2", crawl(1.0)),
        ("constant 5 km/h", lambda t: min(5.0, 9 * t)),
        ("constant 25 km/h (drive-through)", lambda t: min(25.0, 9 * t)),
        ("no start (park)", lambda t: 0.0),
    ])


def mono_families(F0):
    gs, rp = "green_stop", "red_proceed"
    stop_then_go = CF.ramp_to_stop(15, 0.2, 2.0, F0)
    fam = collections.OrderedDict()
    fam["stop clearance 6/4/2/1/0.5 m"] = [(f"{x} m", gs, "STOP", CF.ramp_to_stop(15, 0.2, x, F0), {}) for x in (6.0, 4.0, 2.0, 1.0, 0.5)]
    fam["overshoot at 5 km/h"] = [(f"{o} m", gs, "STOP", CF.ramp_to_stop(5, 0.2, -o, F0, a=1.0), {}) for o in (0.0, 0.1, 0.5, 2.0, 10.0, 50.0)]
    fam["overshoot at 25 km/h"] = [(f"{o} m", gs, "STOP", CF.ramp_to_stop(25, 0.2, -o, F0, a=4.0), {}) for o in (0.0, 0.1, 0.5, 2.0, 10.0, 50.0)]
    fam["approach start 0/0.5/1/1.5/3 s"] = [(f"{t0} s", gs, "STOP", CF.ramp_to_stop(15, t0, 0.5, F0), {}) for t0 in (0.0, 0.5, 1.0, 1.5, 3.0)]
    fam["collision events 0.5 s apart 0/1/3/6"] = [(f"{n}", gs, "STOP", stop_then_go, {"collisions_at": tuple(5 + i * 0.5 for i in range(n))}) for n in (0, 1, 3, 6)]
    fam["collision events 1.5 s apart 0/1/3/6"] = [(f"{n}", gs, "STOP", stop_then_go, {"collisions_at": tuple(5 + i * 1.5 for i in range(n))}) for n in (0, 1, 3, 6)]
    fam["later braking from 8 km/h"] = [(f"{t0 + 1:.1f} s", gs, "STOP", (lambda t0: (lambda t: 0.0 if t < 0.2 else (8.0 if t < t0 + 1.0 else max(0.0, 8.0 - 20 * (t - t0 - 1.0)))))(t0), {}) for t0 in (0.2, 2.0, 4.0, 6.0)]
    fam["PROCEED start delay"] = [(f"{d} s", rp, "PROCEED", (lambda d: (lambda t: 0.0 if t < 1.0 + d else min(20.0, 9 * (t - 1.0 - d))))(d), {}) for d in (0.0, 1.0, 2.9, 3.1, 5.0, 8.0)]
    fam["stop then go at 14 s vs hold (equal)"] = [("go at 14 s", gs, "STOP", lambda t: stop_then_go(t) if t < 14 else min(20, 5 * (t - 14)), {}),
                                                   ("hold", gs, "STOP", stop_then_go, {})]
    return fam


def synth_score(sc, act, tel, version):
    kw = dict(scenario=sc, expected_action=act, max_reaction_time=3.0, protocol="v2")
    return scored({"scenario": sc}, tel, kw, version=version)


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    eps = V.SN.capture()
    assert len(eps) == 650
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
        e["id"] = f"{e['model']}/s{e['seed']}/{e['scenario']}"
        e["ids"] = V.event_ids(e["ep"])
        e["s"] = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        e["strict"] = "INVALID" if e["forced_invalid"] else e["s"].get("verdict")
        e["v"] = {ver: scored(e["res"], e["rows"], e["kw"], e["ids"], ver, e["forced_invalid"]) for ver in VERSIONS}
    val = V.capture_validation()
    for e in val:
        e["ids"] = V.event_ids(e["ep"])
        e["strict"] = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"]).get("verdict")
        e["v"] = {ver: scored(e["res"], e["rows"], e["kw"], e["ids"], ver, e["forced_invalid"]) for ver in VERSIONS}

    # ---- per-episode table + boards ------------------------------------------------------------------
    rows = []
    for e in eps:
        r = {"episode_id": e["id"], "model": e["model"], "seed": e["seed"], "scenario": e["scenario"],
             "expected_action": e["kw"]["expected_action"], "weight": W[e["scenario"]], "strict": e["strict"]}
        for ver in VERSIONS:
            c, k, _ = e["v"][ver]
            r.update({f"credit_{ver}": c, f"A_{ver}": r4(k["A"]), f"e_{ver}": r4(k["e"]), f"F_lat_{ver}": r4(k["F_lat"]),
                      f"latency_s_{ver}": k["latency_s"], f"trigger_{ver}": k["latency_trigger"], f"F_safe_{ver}": r4(k["F_safe"])})
        rows.append(r)
    wr("episodes_v22.csv", rows)
    base = lambda e: {"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"], "exp": e["kw"]["expected_action"]}  # noqa: E731
    boards = {ver: V.board([{**base(e), "credit": e["v"][ver][0]} for e in eps]) for ver in VERSIONS}
    gr = {ver: {m: b[m]["graded"] for m in b} for ver, b in boards.items()}
    models = sorted(gr["v2"], key=lambda m: -gr["v2"][m])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    rk = {ver: {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -gr[ver][m]))} for ver in VERSIONS}
    rk_np = {ver: {m: i + 1 for i, m in enumerate(sorted(npm, key=lambda m: -gr[ver][m]))} for ver in VERSIONS}
    tau = {ver: (V.SN.kendall_tau_b([gr["v2"][m] for m in models], [gr[ver][m] for m in models]),
                 V.SN.kendall_tau_b([gr["v2"][m] for m in npm], [gr[ver][m] for m in npm])) for ver in VERSIONS}
    wr("leaderboard_v22.csv", [{"model": m, "strict_pass": f"{boards['v2'][m]['k']}/{boards['v2'][m]['n']}",
                                **{f"graded_{ver}": round(gr[ver][m], 2) for ver in VERSIONS},
                                **{f"rank_{ver}": rk[ver][m] for ver in VERSIONS},
                                **{f"rank_nonpriv_{ver}": rk_np[ver].get(m, "") for ver in VERSIONS},
                                **{f"sd_{ver}": round(boards[ver][m]["sd"], 2) for ver in VERSIONS}} for m in models])

    # ---- 1. TCP five crawl cells; 2. every F1-affected episode ------------------------------------------
    crawl_cells = ("signal_off", "flagger_control", "conflicting_authorities", "two_civilians_disagree", "fake_vest_director")
    f1 = [e for e in eps if abs(V.scored(e["res"], e["rows"], e["kw"], e["ids"], fixes=("F1",), forced_invalid=e["forced_invalid"])[0]
                                - e["v"]["v2"][0]) > EPS]
    f1rows = []
    for e in f1:
        fct = AD.facts(e, e["v"]["v2.2"][2])
        f1rows.append({"episode": e["id"], "tcp_crawl_cell": e["model"] == "tcp" and e["scenario"] in crawl_cells, "strict": e["strict"],
                       **{ver: terms(e["v"][ver][0], e["v"][ver][1]) for ver in ("v2", "v2.1", "v2.2")},
                       "credit_v2": e["v"]["v2"][0], "credit_v21": e["v"]["v2.1"][0], "credit_v22": e["v"]["v2.2"][0],
                       "trigger_v21": e["v"]["v2.1"][1]["latency_trigger"], "latency_v21": e["v"]["v2.1"][1]["latency_s"],
                       "trigger_v22": e["v"]["v2.2"][1]["latency_trigger"], "latency_v22": e["v"]["v2.2"][1]["latency_s"],
                       "stopped_share_after_onset": fct["stopped_share_after_onset"], "speed_at_scored_end_kmh": fct["speed_at_scored_end_kmh"],
                       "min_front_to_line_m": fct["min_front_to_line_m"], "t_cross_s": fct["t_cross_s"]})
    wr("f1_affected_v22.csv", f1rows)

    # ---- 3. fixtures + synthetic behaviours -----------------------------------------------------------
    fx = collections.defaultdict(lambda: collections.defaultdict(list))
    for e in val:
        if e["phase"] == "23_fixtures":
            for ver in VERSIONS:
                fx[e["model"]][ver].append(e["v"][ver][0])
    fixrows = [{"fixture (CARLA, 3 seeds)": m, "strict": "/".join(sorted({x["strict"] for x in val if x["model"] == m and x["phase"] == "23_fixtures"})),
                **{ver: " / ".join(f"{c:.3f}" for c in fx[m][ver]) for ver in VERSIONS},
                **{f"mean_{ver}": round(sum(fx[m][ver]) / len(fx[m][ver]), 4) for ver in VERSIONS}} for m in sorted(fx)]
    wr("fixtures_v22.csv", fixrows)
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    behrows = []
    for name, fn in behaviours(F0).items():
        tel = CF.synth("green_stop", fn)
        st = S.score_episode_from_telemetry({"scenario": "green_stop"}, tel, scenario="green_stop", expected_action="STOP",
                                            max_reaction_time=3.0, protocol="v2").get("verdict")
        rec = {"behaviour (synthetic, green_stop)": name, "strict": st}
        for ver in VERSIONS:
            c, k, _ = synth_score("green_stop", "STOP", tel, ver)
            rec[ver] = round(c, 4)
            rec[f"terms_{ver}"] = terms(c, k)
        behrows.append(rec)
    wr("behaviours_v22.csv", behrows)

    # ---- 4. strict consistency (real) + synthetic monotonicity ----------------------------------------------
    cons = []
    by_sc = collections.defaultdict(list)
    for e in eps:
        by_sc[e["scenario"]].append(e)
    for ver in VERSIONS:
        c = {e["id"]: e["v"][ver][0] for e in eps}
        cons.append({"version": ver,
                     "credit1_not_pass": sum(c[e["id"]] >= 1 - EPS and e["strict"] != "PASS" for e in eps),
                     "pass_below_1": sum(e["strict"] == "PASS" and c[e["id"]] < 1 - EPS for e in eps),
                     "pass_below_1_episodes": ";".join(e["id"] for e in eps if e["strict"] == "PASS" and c[e["id"]] < 1 - EPS),
                     "fail_ge_pass_pairs_same_scenario": sum(1 for es in by_sc.values() for a in es for b in es
                                                             if a["strict"] == "FAIL" and b["strict"] == "PASS" and c[a["id"]] >= c[b["id"]] - EPS),
                     "zero": sum(c[i] < EPS for i in c), "partial": sum(EPS <= c[i] < 1 - EPS for i in c), "full": sum(c[i] >= 1 - EPS for i in c)})
    mono = []
    for name, cases in mono_families(F0).items():
        sc_ = {ver: [] for ver in VERSIONS}
        for lab, sc, act, fn, kw_ in cases:
            tel = CF.synth(sc, fn, **kw_)
            for ver in VERSIONS:
                sc_[ver].append(synth_score(sc, act, tel, ver)[0])
        for ver in VERSIONS:
            seq = sc_[ver]
            viol = int(abs(seq[0] - seq[1]) > EPS) if "(equal)" in name else sum(seq[i + 1] > seq[i] + EPS for i in range(len(seq) - 1))
            mono.append({"family": name, "version": ver, "cases": " | ".join(x[0] for x in cases),
                         "credits": " | ".join(f"{x:.3f}" for x in seq), "violations": viol})
        if name == "overshoot at 25 km/h":
            fast = sc_
        if name == "overshoot at 5 km/h":
            slow = sc_
    for ver in VERSIONS:
        mono.append({"family": "25 km/h <= 5 km/h at equal overshoot", "version": ver, "cases": "0..50 m",
                     "credits": " | ".join(f"{a:.3f}<={b:.3f}" for a, b in zip(fast[ver], slow[ver])),
                     "violations": sum(a > b + EPS for a, b in zip(fast[ver], slow[ver]))})
    wr("monotonicity_v22.csv", mono)
    for c in cons:
        c["synthetic_violations"] = sum(m["violations"] for m in mono if m["version"] == c["version"])
    wr("consistency_v22.csv", cons)

    # ---- 5. instruction-independent policies ------------------------------------------------------------
    pols, held = policies()
    tot = sum(W.values())
    prow = []
    for pn, fn in pols.items():
        rec = {"policy": pn, "family": "held-out" if pn in held else "reference"}
        cells = {ver: 0.0 for ver in VERSIONS}
        npass = 0
        for sc in V.vlm.SCENARIO_ORDER:
            act = V.vlm.SCENARIOS[sc]["expect"]
            tel = CF.synth(sc, fn)
            npass += S.score_episode_from_telemetry({"scenario": sc}, tel, scenario=sc, expected_action=act,
                                                    max_reaction_time=3.0, protocol="v2").get("verdict") == "PASS"
            for ver in VERSIONS:
                cells[ver] += W[sc] * synth_score(sc, act, tel, ver)[0]
        rec["strict_pass"] = npass
        for ver in VERSIONS:
            g = 100 * cells[ver] / tot
            rec[f"graded_{ver}"] = round(g, 2)
            rec[f"rank_vs_14_{ver}"] = 1 + sum(gr[ver][m] > g for m in models)
            rec[f"beats_nonpriv_{ver}"] = sum(gr[ver][m] < g for m in npm)
        prow.append(rec)
    wr("policies_v22.csv", prow)
    psum = []
    for ver in VERSIONS:
        gs = sorted(p[f"graded_{ver}"] for p in prow)
        best = max(gr[ver][m] for m in npm)
        psum.append({"version": ver, "min": gs[0], "median": round((gs[23] + gs[24]) / 2, 2), "max": gs[-1],
                     ">=60": sum(g >= 60 for g in gs), "40-60": sum(40 <= g < 60 for g in gs), "20-40": sum(20 <= g < 40 for g in gs),
                     "<20": sum(g < 20 for g in gs), "best_nonpriv_system": round(best, 2), "above_best_nonpriv": sum(g > best for g in gs),
                     "above_oracle": sum(g > gr[ver]["oracle"] for g in gs)})
    wr("policy_summary_v22.csv", psum)

    # ---- 6. a3 counterexample: careful short stops at every STOP/HOLD station ----------------------------------
    a3rows = []
    for sc in V.vlm.SCENARIO_ORDER:
        act = V.vlm.SCENARIOS[sc]["expect"]
        if act not in ("STOP", "HOLD") or V.G._approach_requirement_for(sc) is not None:
            continue
        f0 = float(CF.template(sc)[0]["stopline_front_m"] or 0.0)
        for vmax, travel in itertools.product((6.0, 8.0, 15.0), (1.2, 1.5, 1.9, 2.5)):
            if f0 - travel < 1.0:
                continue
            tel = CF.synth(sc, CF.ramp_to_stop(vmax, 0.2, f0 - travel, f0))
            st = S.score_episode_from_telemetry({"scenario": sc}, tel, scenario=sc, expected_action=act, max_reaction_time=3.0, protocol="v2")
            c22, _, g = synth_score(sc, act, tel, "v2.2")
            ap = V.approach(tel, ((g.get("evidence") or {}).get("active_window") or {}).get("active_end_s"))
            a3rows.append({"scenario": sc, "spawn_front_to_line_m": round(f0, 2), "target_kmh": vmax, "travel_m": travel,
                           "peak_kmh": round(ap["v_peak"], 2), "progress_m": round(ap["progress"], 2), "strict": st.get("verdict"),
                           "credit_v22": c22, "credit_v22_a3": synth_score(sc, act, tel, "v2.2+a3")[0]})
    wr("a3_counterexample_synthetic.csv", a3rows)
    real_a3 = []
    for e in eps:
        c22, c3 = e["v"]["v2.2"][0], e["v"]["v2.2+a3"][0]
        if abs(c22 - c3) > EPS or (e["kw"]["expected_action"] in ("STOP", "HOLD") and V.f2_applies(e["kw"]) and e["strict"] == "PASS"):
            ap = V._approach_of(e["rows"], e["v"]["v2.2"][2])
            fct = AD.facts(e, e["v"]["v2.2"][2])
            real_a3.append({"episode": e["id"], "strict": e["strict"], "credit_v22": c22, "credit_v22_a3": c3,
                            "peak_kmh": round(ap["v_peak"], 2), "progress_m": round(ap["progress"], 2),
                            "min_front_to_line_m": fct["min_front_to_line_m"], "a3_margin_speed_kmh": round(ap["v_peak"] - V.V_STAR, 2),
                            "a3_margin_progress_m": round(ap["progress"] - V.D_STAR, 2)})
    wr("a3_counterexample_real.csv", real_a3)

    # ---- 7. F3 identity bounds on v2.2 ---------------------------------------------------------------------

    hi_cells = []

    for e in eps:
        ids_hi = [(a if a is not None else ("hidden", k), t) for k, (a, t) in enumerate(e["ids"])]
        hi_cells.append({**base(e), "credit": scored(e["res"], e["rows"], e["kw"], ids_hi, "v2.2", e["forced_invalid"])[0]})
    g_lo_contacts = gr["v2.2"]
    g_hi_contacts = {m: s["graded"] for m, s in V.board(hi_cells).items()}
    nof3 = []
    for e in eps:
        with V.patched(("F1", "F7"), list(e["ids"])):
            G._latency_factor = latency_v22
            g = G.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        nof3.append({**base(e), "credit": 0.0 if e["forced_invalid"] else round(min(max(float(g.get("credit") or 0), 0), 1), 6)})
    g_nof3 = {m: s["graded"] for m, s in V.board(nof3).items()}
    pairs = [f"{a}-{b}" for a, b in itertools.combinations(models, 2)
             if not (g_hi_contacts[a] > g_lo_contacts[b] or g_hi_contacts[b] > g_lo_contacts[a])]
    wr("f3_bounds_v22.csv", [{"model": m, "graded_v22_without_F3": round(g_nof3[m], 2), "graded_v22": round(g_lo_contacts[m], 2),
                              "graded_v22_all_hidden_distinct": round(g_hi_contacts[m], 2), "rank_v22": rk["v2.2"][m]} for m in models])
    say("F3 bounds v2.2: pairs not separated:", pairs)
    say(f"F3 v2.2 without F3 vs v2.2 tau all {V.SN.kendall_tau_b([g_nof3[m] for m in models], [g_lo_contacts[m] for m in models]):.3f} "
        f"non-priv {V.SN.kendall_tau_b([g_nof3[m] for m in npm], [g_lo_contacts[m] for m in npm]):.3f}; "
        f"all-hidden vs v2.2 tau {V.SN.kendall_tau_b([g_hi_contacts[m] for m in models], [g_lo_contacts[m] for m in models]):.3f}")

    # ---- final comparison ---------------------------------------------------------------------------------
    best_real = {ver: max(gr[ver][m] for m in npm) for ver in VERSIONS}
    comp = []
    for c in cons:
        ver = c["version"]
        comp.append({**{k: c[k] for k in ("version", "zero", "partial", "full", "credit1_not_pass", "pass_below_1",
                                          "fail_ge_pass_pairs_same_scenario", "synthetic_violations")},
                     "best_nonpriv_system": round(best_real[ver], 2),
                     "best_policy": max(p[f"graded_{ver}"] for p in prow),
                     "policies_above_best_system": sum(p[f"graded_{ver}"] > best_real[ver] for p in prow),
                     "tau_vs_v2_all": round(tau[ver][0], 3), "tau_vs_v2_nonpriv": round(tau[ver][1], 3),
                     "rank_changes_vs_v2": ";".join(f"{m} {rk['v2'][m]}->{rk[ver][m]}" for m in models if rk[ver][m] != rk["v2"][m])})
    wr("comparison_v22.csv", comp)
    for r in comp:
        say(r)
    for m in models:
        say(m, {ver: (round(gr[ver][m], 2), rk[ver][m]) for ver in VERSIONS})
    (OUT / "stdout.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
