"""v2.2 verification checks (recorded telemetry + synthetic; no rollouts; v2 / v2.1 / v2.2 outputs untouched).

    python <audit>/credit_v22/checks/scripts/checks.py          # from the workspace root

Items:
  1  F6h response time vs the strict reaction criterion (650 episodes) + the strict-aligned variant "final hold"
  2  F4 cross-speed ordering: term trace, equal-stop-time pairs, creep-cap-off variant
  3  the two single-step inversions: term trace + minimal in-term fixes ("front-capped approach distance",
     "spawn-anchored approach distance") and their 650-episode effect
Outputs: credit_v22/checks/data/*.csv and stdout.txt.
"""
from __future__ import annotations

import collections
import contextlib
import csv
import inspect
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data"
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
import credit_v22 as X  # noqa: E402

V, G, S, CF, W = X.V, X.G, X.S, X.CF, X.W
EPS = 1e-6
LOG = []
_A_PRIME = V.ALT._engagement_motion_anchored
_HELD = X.held_stop


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


# ------------------------------------------------------------------------------------------------ variants
def build_engagement(*, front_cap=False, spawn_anchor=False, creep_cap_far_only=False):
    """A' (as in v2.1/v2.2) plus optional in-term changes to the published engagement function."""
    src = textwrap.dedent(inspect.getsource(V.ALT._orig_engagement))
    reps = [('    first_stop_time: Optional[float] = None\n'
             '    for row in rows:\n'
             '        if float(row["sim_time"]) >= onset and float(row["ego_speed_kmh"]) <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n',
             '    first_stop_time: Optional[float] = None\n'
             '    _search_start = next((float(r["sim_time"]) for r in rows if float(r["sim_time"]) >= onset\n'
             '                          and float(r["ego_speed_kmh"]) > GRADED_THRESHOLDS["stop_speed_kmh"]), onset)\n'
             '    for row in rows:\n'
             '        if float(row["sim_time"]) >= _search_start and float(row["ego_speed_kmh"]) <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n')]
    if spawn_anchor:   # approach distance measured from the first scored row instead of the onset row
        reps.append(('    onset_stopline = _finite_float(onset_row.get("distance_to_stopline_m"))\n',
                     '    onset_row = rows[0]\n    onset_stopline = _finite_float(onset_row.get("distance_to_stopline_m"))\n'))
    if front_cap:      # approach distance counted in front-bumper coordinates and only up to the stop line
        reps.append(('    speed_credit = _linear_credit(\n        max_approach_speed,',
                     '    _of = _finite_float(onset_row.get("stopline_front_m"))\n'
                     '    if _of is not None and front_clearance is not None:\n'
                     '        approach_distance = _of - max(front_clearance, 0.0)\n'
                     '    speed_credit = _linear_credit(\n        max_approach_speed,'))
    if creep_cap_far_only:  # the cap's documented purpose: low-speed creep that stays far upstream of the line
        reps.append(('    if speed_credit <= 0.0 and near_stopline_progress_credit > GRADED_THRESHOLDS["stop_engagement_creep_cap"]:',
                     '    if speed_credit <= 0.0 and near_stopline_progress_credit > GRADED_THRESHOLDS["stop_engagement_creep_cap"] and (\n'
                     '            min_stopline is None or min_stopline > GRADED_THRESHOLDS["stopline_engagement_m"]):'))
    for a, b in reps:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    src = src.replace("def _stop_hold_engagement_factor(", "def _eng_variant(", 1)
    ns = dict(vars(G))
    exec(compile(src, "<engagement-variant>", "exec"), ns)
    return ns["_eng_variant"]


def held_final(rows, onset):
    """Strict-aligned F6h: the response is the start of the stop that is held until the last scored row (the directive
    end): the final contiguous span at <= 1 km/h, which must itself contain a 2.0 s window with drift <= 0.5 m.
    Same anchors as F6h (onset; the first launch for an ego at rest at onset)."""
    lat, trig = _HELD(rows, onset)
    if lat is None or trig == "at_rest_never_moved":
        return lat, trig
    v = lambda r: G._finite_float(r.get("ego_speed_kmh")) or 0.0  # noqa: E731
    after = [r for r in rows if float(r["sim_time"]) >= onset - 1e-9]
    at_rest = v(after[0]) <= X.STOP_KMH
    start = next(float(r["sim_time"]) for r in after if v(r) > X.STOP_KMH) if at_rest else onset
    i = len(after)
    while i > 0 and v(after[i - 1]) <= X.STOP_KMH:
        i -= 1
    span = [r for r in after[i:] if float(r["sim_time"]) >= start - 1e-9]
    if not held_window(span):
        return None, "no_final_hold"
    return float(span[0]["sim_time"]) - start, "final_hold" if not at_rest else "final_hold_after_launch"


def held_window(span):
    """True when the span (all rows <= 1 km/h) contains a 2.0 s window with forward drift <= 0.5 m (as in F6h)."""
    ts = [float(r["sim_time"]) for r in span]
    fwd = [G._finite_float(r.get("ego_forward_m")) for r in span]
    for i in range(len(span)):
        j = next((j for j in range(i, len(span)) if ts[j] - ts[i] >= X.DWELL_S - 1e-9), None)
        if j is None:
            return False
        win = [x for x in fwd[i:j + 1] if x is not None]
        if win and max(win) - min(win) <= X.DRIFT_M:
            return True
    return False


@contextlib.contextmanager
def variant(eng=None, held=None):
    a0, h0 = V.ALT._engagement_motion_anchored, X.held_stop
    if eng is not None:
        V.ALT._engagement_motion_anchored = eng
    if held is not None:
        X.held_stop = held
    try:
        yield
    finally:
        V.ALT._engagement_motion_anchored, X.held_stop = a0, h0


VARIANTS = {
    "v2.2": {},
    "final_hold (item 1)": {"held": held_final},
    "creep_cap_far_only (item 2)": {"eng": build_engagement(creep_cap_far_only=True)},
    "front_cap (item 3a)": {"eng": build_engagement(front_cap=True)},
    "spawn_anchor (item 3b)": {"eng": build_engagement(spawn_anchor=True)},
    "front_cap+spawn_anchor": {"eng": build_engagement(front_cap=True, spawn_anchor=True)},
    "adopted: front_cap + creep_cap_far_only": {"eng": build_engagement(front_cap=True, creep_cap_far_only=True)},
}
ADOPTED = "adopted: front_cap + creep_cap_far_only"


def vscore(res, rows, kw, ids, name, forced_invalid=False):
    with variant(**VARIANTS[name]):
        return X.scored(res, rows, kw, ids, "v2.2", forced_invalid)


def syn(sc, act, tel, name):
    return vscore({"scenario": sc}, tel, dict(scenario=sc, expected_action=act, max_reaction_time=3.0, protocol="v2"), (), name)


def stop_time(fn, t_end=80.0):
    t, moved = 0.0, False
    while t < t_end:
        v = fn(t)
        moved = moved or v > 0
        if moved and v <= 0:
            return t
        t += 0.01
    return None


def comp_str(c, k, g):
    cc = g.get("component_credits") or {}
    en = (g.get("evidence") or {}).get("engagement") or {}
    f = lambda x: None if x is None else round(float(x), 3)  # noqa: E731
    return {"credit": round(c, 4), "speed_margin": f(cc.get("speed_margin")), "clearance": f(cc.get("stopline_clearance")),
            "no_conflict_entry": cc.get("no_conflict_entry"), "e": f(cc.get("engagement_gate")), "approach_speed": f(cc.get("approach_speed")),
            "approach_progress": f(cc.get("approach_progress")), "creep_cap": en.get("creep_cap_applied"),
            "approach_distance_m": en.get("approach_distance_m"), "max_approach_kmh": en.get("max_pre_stop_speed_kmh"),
            "max_enforced_kmh": (g.get("evidence") or {}).get("max_scored_speed_kmh"), "F_lat": f(k["F_lat"]), "latency_s": k["latency_s"]}


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    eps = V.SN.capture()
    assert len(eps) == 650
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
        e["id"] = f"{e['model']}/s{e['seed']}/{e['scenario']}"
        e["ids"] = V.event_ids(e["ep"])
        e["strict"] = "INVALID" if e["forced_invalid"] else S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"]).get("verdict")
        e["v"] = {n: vscore(e["res"], e["rows"], e["kw"], e["ids"], n, e["forced_invalid"]) for n in VARIANTS}
    base = lambda e: {"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"], "exp": e["kw"]["expected_action"]}  # noqa: E731
    gr = {n: {m: s["graded"] for m, s in V.board([{**base(e), "credit": e["v"][n][0]} for e in eps]).items()} for n in VARIANTS}
    models = sorted(gr["v2.2"], key=lambda m: -gr["v2.2"][m])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    rk = {n: {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -gr[n][m]))} for n in VARIANTS}

    # ---- item 1 -------------------------------------------------------------------------------------
    it1 = []
    for e in eps:
        kw = e["kw"]
        if kw["expected_action"] not in ("STOP", "HOLD") or e["forced_invalid"]:
            continue
        c, k, g = e["v"]["v2.2"]
        aw = (g.get("evidence") or {}).get("active_window") or {}
        on, end = aw.get("onset_s"), aw.get("active_end_s")
        budget = float(kw.get("max_reaction_time") or 3.0)
        rows = [r for r in e["rows"] if float(r["sim_time"]) <= min(end, 25.0) + 1e-9]
        enf = [r for r in rows if float(r["sim_time"]) >= on + budget - 1e-9]
        strict_ok = bool(enf) and max(float(r["ego_speed_kmh"]) for r in enf) < G.STRICT_THRESHOLDS["stop_speed_kmh"]
        i = len(rows)
        while i > 0 and float(rows[i - 1]["ego_speed_kmh"]) < G.STRICT_THRESHOLDS["stop_speed_kmh"]:
            i -= 1
        t_final = float(rows[i]["sim_time"]) if i < len(rows) else None
        lat, plateau = k["latency_s"], max(3.0, budget)
        f6_ok = lat is not None and lat <= plateau + 1e-9
        cf, kf, _ = e["v"]["final_hold (item 1)"]
        it1.append({"episode": e["id"], "strict": e["strict"], "engaged": k["G_move"], "budget_s": budget, "onset_s": on,
                    "strict_reaction_ok": strict_ok, "strict_final_stop_s_after_onset": None if t_final is None else round(t_final - on, 2),
                    "f6h_trigger": k["latency_trigger"], "f6h_latency_s": lat, "f6h_within_plateau": f6_ok,
                    "category": "agree" if strict_ok == f6_ok else ("strict_ok_f6h_late" if strict_ok else "strict_late_f6h_ok"),
                    "credit_v22": c, "F_lat_v22": k["F_lat"], "credit_final_hold": cf, "F_lat_final_hold": kf["F_lat"],
                    "latency_final_hold": kf["latency_s"]})
    wr("item1_reaction_timing.csv", it1)
    cat = collections.Counter(r["category"] for r in it1)
    say("item1 categories:", dict(cat), "engaged mismatches:",
        sum(r["category"] != "agree" and r["engaged"] for r in it1))

    # ---- item 2 / 3 synthetic --------------------------------------------------------------------------
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    tr, eq = [], {n: [] for n in VARIANTS}
    for o in (0.0, 0.1, 0.5, 2.0, 10.0, 50.0):
        slow, fast = CF.ramp_to_stop(5, 0.2, -o, F0, a=1.0), CF.ramp_to_stop(25, 0.2, -o, F0, a=4.0)
        t_slow, t_fast0 = stop_time(slow), stop_time(CF.ramp_to_stop(25, 0.0, -o, F0, a=4.0))
        fast_eq = CF.ramp_to_stop(25, max(0.0, t_slow - t_fast0), -o, F0, a=4.0)
        for lab, fn in (("5 km/h (a=1.0)", slow), ("25 km/h (a=4.0)", fast), ("25 km/h, same stop time as 5 km/h", fast_eq)):
            tel = CF.synth("green_stop", fn)
            st = S.score_episode_from_telemetry({"scenario": "green_stop"}, tel, scenario="green_stop", expected_action="STOP",
                                                max_reaction_time=3.0, protocol="v2").get("verdict")
            row = {"overshoot_m": o, "profile": lab, "stop_time_s": stop_time(fn), "strict": st}
            for n in ("v2.2", "creep_cap_far_only (item 2)"):
                c, k, g = syn("green_stop", "STOP", tel, n)
                row.update({f"{n}: {kk}": vv for kk, vv in comp_str(c, k, g).items()} if n == "v2.2" else {f"credit {n}": round(c, 4)})
            tr.append(row)
        tel_s, tel_f = CF.synth("green_stop", slow), CF.synth("green_stop", fast_eq)
        for n in VARIANTS:
            eq[n].append((syn("green_stop", "STOP", tel_f, n)[0], syn("green_stop", "STOP", tel_s, n)[0]))
    wr("item2_f4_trace.csv", tr)
    it3 = []
    for lab, fn in ([(f"25 km/h, overshoot {o} m", CF.ramp_to_stop(25, 0.2, -o, F0, a=4.0)) for o in (0.1, 0.5)]
                    + [(f"approach start {t0} s", CF.ramp_to_stop(15, t0, 0.5, F0)) for t0 in (0.0, 0.5)]):
        tel = CF.synth("green_stop", fn)
        row = {"case": lab}
        for n in ("v2.2", "front_cap (item 3a)", "spawn_anchor (item 3b)", "front_cap+spawn_anchor"):
            c, k, g = syn("green_stop", "STOP", tel, n)
            row.update({f"{n}: {kk}": vv for kk, vv in comp_str(c, k, g).items()} if n == "v2.2" else {f"credit {n}": round(c, 4)})
        it3.append(row)
    wr("item3_step_trace.csv", it3)

    # synthetic ordering families under every variant
    mono = []
    for name, cases in X.mono_families(F0).items():
        seqs = {n: [] for n in VARIANTS}
        for lab, sc, act, fn, kw_ in cases:
            tel = CF.synth(sc, fn, **kw_)
            for n in VARIANTS:
                seqs[n].append(syn(sc, act, tel, n)[0])
        for n, seq in seqs.items():
            viol = int(abs(seq[0] - seq[1]) > EPS) if "(equal)" in name else sum(seq[i + 1] > seq[i] + EPS for i in range(len(seq) - 1))
            mono.append({"family": name, "variant": n, "credits": " | ".join(f"{x:.3f}" for x in seq), "violations": viol})
            if name == "overshoot at 25 km/h":
                seqs_fast = seqs
            if name == "overshoot at 5 km/h":
                seqs_slow = seqs
    for n in VARIANTS:
        mono.append({"family": "25 km/h <= 5 km/h at equal overshoot", "variant": n,
                     "credits": " | ".join(f"{a:.3f}<={b:.3f}" for a, b in zip(seqs_fast[n], seqs_slow[n])),
                     "violations": sum(a > b + EPS for a, b in zip(seqs_fast[n], seqs_slow[n]))})
    for n in VARIANTS:
        mono.append({"family": "25 km/h <= 5 km/h at equal overshoot and equal stop time (diagnostic)", "variant": n,
                     "credits": " | ".join(f"{a:.3f}<={b:.3f}" for a, b in eq[n]),
                     "violations": sum(a > b + EPS for a, b in eq[n])})
    wr("monotonicity_variants.csv", mono)

    # ---- 650-episode effect of every variant -----------------------------------------------------------
    eff = []
    for n in VARIANTS:
        ch = [e for e in eps if abs(e["v"][n][0] - e["v"]["v2.2"][0]) > EPS]
        eff.append({"variant": n, "episodes_changed": len(ch), "up": sum(e["v"][n][0] > e["v"]["v2.2"][0] for e in ch),
                    "down": sum(e["v"][n][0] < e["v"]["v2.2"][0] for e in ch),
                    "credit1_not_pass": sum(e["v"][n][0] >= 1 - EPS and e["strict"] != "PASS" for e in eps),
                    "pass_below_1": sum(e["strict"] == "PASS" and e["v"][n][0] < 1 - EPS for e in eps),
                    "synthetic_violations": sum(m["violations"] for m in mono if m["variant"] == n and "diagnostic" not in m["family"]),
                    "tau_vs_v22_all": round(V.SN.kendall_tau_b([gr["v2.2"][m] for m in models], [gr[n][m] for m in models]), 3),
                    "tau_vs_v22_nonpriv": round(V.SN.kendall_tau_b([gr["v2.2"][m] for m in npm], [gr[n][m] for m in npm]), 3),
                    "rank_changes": ";".join(f"{m} {rk['v2.2'][m]}->{rk[n][m]}" for m in models if rk[n][m] != rk["v2.2"][m]),
                    **{m: round(gr[n][m] - gr["v2.2"][m], 2) for m in models}})
    wr("variant_effects.csv", eff)
    wr("variant_boards.csv", [{"model": m, **{f"graded {n}": round(gr[n][m], 2) for n in VARIANTS}, **{f"rank {n}": rk[n][m] for n in VARIANTS}}
                              for m in models])
    for r in eff:
        say({k: r[k] for k in ("variant", "episodes_changed", "up", "down", "credit1_not_pass", "pass_below_1", "synthetic_violations",
                               "tau_vs_v22_all", "tau_vs_v22_nonpriv", "rank_changes")})
    (OUT / "stdout.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
