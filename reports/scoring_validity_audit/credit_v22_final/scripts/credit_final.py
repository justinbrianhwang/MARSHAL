"""Credit v2.2 final (frozen candidate) and its confirmation on recorded + synthetic telemetry.

    python <audit>/credit_v22_final/scripts/credit_final.py          # from the workspace root

Formula = Credit v2.2 (F1 A' window, F6h held-stop response, F3 distinct contacts, F7 YIELD window; no a3 gate) with
two in-term corrections to the published STOP/HOLD engagement function, and nothing else:
  C1  the creep cap (engagement <= 0.25 when approach-speed credit is 0) applies only when the ego stayed more than
      stopline_engagement_m (16.59 m) from the stop line;
  C2  the engagement approach distance is measured with the front bumper (stopline_front_m) and capped at the line:
      d_app = front-to-line at onset - max(min front-to-line, 0), instead of the vehicle-centre distance.
Outputs: credit_v22_final/data/*.csv and stdout.txt. Reads the v2.2 module; writes nothing else.
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
sys.path.insert(0, str(HERE.parents[1] / "credit_v22" / "scripts"))
import credit_v22 as X  # noqa: E402  (v2.2: F6h, scored(), synthetic sets)

V, G, S, CF, W = X.V, X.G, X.S, X.CF, X.W
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


# ------------------------------------------------------------------------------------------------ frozen formula
def _build_engagement():
    """Published engagement function + A' (F1) + C1 + C2, as exact source replacements."""
    src = textwrap.dedent(inspect.getsource(V.ALT._orig_engagement))
    reps = [
        # F1 (A'): the stop that ends the approach window is searched from the first post-onset motion
        ('    first_stop_time: Optional[float] = None\n'
         '    for row in rows:\n'
         '        if float(row["sim_time"]) >= onset and float(row["ego_speed_kmh"]) <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n',
         '    first_stop_time: Optional[float] = None\n'
         '    _search_start = next((float(r["sim_time"]) for r in rows if float(r["sim_time"]) >= onset\n'
         '                          and float(r["ego_speed_kmh"]) > GRADED_THRESHOLDS["stop_speed_kmh"]), onset)\n'
         '    for row in rows:\n'
         '        if float(row["sim_time"]) >= _search_start and float(row["ego_speed_kmh"]) <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n'),
        # C2: front-bumper approach distance, capped at the line
        ('    speed_credit = _linear_credit(\n        max_approach_speed,',
         '    _onset_front = _finite_float(onset_row.get("stopline_front_m"))\n'
         '    if _onset_front is not None and front_clearance is not None:\n'
         '        approach_distance = _onset_front - max(front_clearance, 0.0)\n'
         '    speed_credit = _linear_credit(\n        max_approach_speed,'),
        # C1: creep cap only far upstream of the line
        ('    if speed_credit <= 0.0 and near_stopline_progress_credit > GRADED_THRESHOLDS["stop_engagement_creep_cap"]:',
         '    if speed_credit <= 0.0 and near_stopline_progress_credit > GRADED_THRESHOLDS["stop_engagement_creep_cap"] and (\n'
         '            min_stopline is None or min_stopline > GRADED_THRESHOLDS["stopline_engagement_m"]):'),
    ]
    for a, b in reps:
        assert src.count(a) == 1, "published engagement function changed; frozen patch no longer applies"
        src = src.replace(a, b)
    src = src.replace("def _stop_hold_engagement_factor(", "def _engagement_final(", 1)
    ns = dict(vars(G))
    exec(compile(src, "<engagement-final>", "exec"), ns)
    return ns["_engagement_final"]


ENGAGEMENT_FINAL = _build_engagement()


@contextlib.contextmanager
def _final_engagement():
    saved = V.ALT._engagement_motion_anchored   # read by the F1 patch at call time
    V.ALT._engagement_motion_anchored = ENGAGEMENT_FINAL
    try:
        yield
    finally:
        V.ALT._engagement_motion_anchored = saved


def scored(res, rows, kw, ids=(), version="final", forced_invalid=False):
    """version: "final" (frozen formula) or any credit_v22 version ("v2", "v2.1", "v2.2", "v2.2+a3")."""
    if version != "final":
        return X.scored(res, rows, kw, ids, version, forced_invalid)
    with _final_engagement():
        return X.scored(res, rows, kw, ids, "v2.2", forced_invalid)


def scored_without_f3(res, rows, kw, ids, forced_invalid=False):
    with _final_engagement(), V.patched(("F1", "F7"), list(ids)):
        G._latency_factor = X.latency_v22
        g = G.score_episode_from_telemetry(res, rows, **kw)
    return 0.0 if forced_invalid else round(min(max(float(g.get("credit") or 0.0), 0.0), 1.0), 6)


def hidden_distinct(ids):
    """F3 upper contact bound: every counted event without an actor id is a different actor."""
    return [(a if a is not None else ("hidden", k), t) for k, (a, t) in enumerate(ids)]


def syn(sc, act, tel, version="final"):
    return scored({"scenario": sc}, tel, dict(scenario=sc, expected_action=act, max_reaction_time=3.0, protocol="v2"), (), version)


def policy_rows(versions=("final",)):
    """The 48 instruction-independent policies: graded and strict count per version."""
    pols, held = X.policies()
    tot = sum(W.values())
    out = []
    for pn, fn in pols.items():
        acc = {v: 0.0 for v in versions}
        npass = 0
        for sc in V.vlm.SCENARIO_ORDER:
            act = V.vlm.SCENARIOS[sc]["expect"]
            tel = CF.synth(sc, fn)
            npass += S.score_episode_from_telemetry({"scenario": sc}, tel, scenario=sc, expected_action=act,
                                                    max_reaction_time=3.0, protocol="v2").get("verdict") == "PASS"
            for v in versions:
                acc[v] += W[sc] * syn(sc, act, tel, v)[0]
        out.append({"policy": pn, "family": "held-out" if pn in held else "reference", "strict_pass": npass,
                    **{f"graded_{v}": round(100 * acc[v] / tot, 2) for v in versions}})
    return out


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    VERS = ("v2", "v2.2", "final")
    eps = V.SN.capture()
    assert len(eps) == 650
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
        e["id"] = f"{e['model']}/s{e['seed']}/{e['scenario']}"
        e["ids"] = V.event_ids(e["ep"])
        e["strict"] = "INVALID" if e["forced_invalid"] else S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"]).get("verdict")
        e["v"] = {v: scored(e["res"], e["rows"], e["kw"], e["ids"], v, e["forced_invalid"]) for v in VERS}

    # ---- per-episode, consistency, distribution ---------------------------------------------------------
    rows = []
    for e in eps:
        c, k, g = e["v"]["final"]
        cc = g.get("component_credits") or {}
        rows.append({"episode_id": e["id"], "model": e["model"], "seed": e["seed"], "scenario": e["scenario"],
                     "expected_action": e["kw"]["expected_action"], "weight": W[e["scenario"]], "strict": e["strict"],
                     "credit_v2": e["v"]["v2"][0], "credit_v22_prefix": e["v"]["v2.2"][0], "credit_final": c,
                     "A": X.r4(k["A"]), "e": X.r4(k["e"]), "F_lat": X.r4(k["F_lat"]), "latency_s": k["latency_s"],
                     "latency_trigger": k["latency_trigger"], "F_safe": X.r4(k["F_safe"]), "distinct_contacts": k["n_collision"],
                     "speed_margin": X.r4(cc.get("speed_margin")), "stopline_clearance": X.r4(cc.get("stopline_clearance")),
                     "approach_progress": X.r4(cc.get("approach_progress")), "approach_speed": X.r4(cc.get("approach_speed"))})
    wr("episodes_final.csv", rows)
    by_sc = collections.defaultdict(list)
    for e in eps:
        by_sc[e["scenario"]].append(e)
    cons = []
    for v in VERS:
        c = {e["id"]: e["v"][v][0] for e in eps}
        cons.append({"version": {"v2": "v2", "v2.2": "v2.2 (pre-fix)", "final": "v2.2 final"}[v],
                     "credit1_not_pass": sum(c[e["id"]] >= 1 - EPS and e["strict"] != "PASS" for e in eps),
                     "pass_below_1": sum(e["strict"] == "PASS" and c[e["id"]] < 1 - EPS for e in eps),
                     "fail_ge_pass_pairs_same_scenario": sum(1 for es in by_sc.values() for a in es for b in es
                                                             if a["strict"] == "FAIL" and b["strict"] == "PASS" and c[a["id"]] >= c[b["id"]] - EPS),
                     "zero": sum(x < EPS for x in c.values()), "partial": sum(EPS <= x < 1 - EPS for x in c.values()),
                     "full": sum(x >= 1 - EPS for x in c.values()),
                     "changed_vs_v22_prefix": sum(abs(c[e["id"]] - e["v"]["v2.2"][0]) > EPS for e in eps)})

    # ---- boards, ranks, tau -------------------------------------------------------------------------------
    base = lambda e: {"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"], "exp": e["kw"]["expected_action"]}  # noqa: E731
    gr = {v: {m: s["graded"] for m, s in V.board([{**base(e), "credit": e["v"][v][0]} for e in eps]).items()} for v in VERS}
    models = sorted(gr["final"], key=lambda m: -gr["final"][m])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    rk = {v: {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -gr[v][m]))} for v in VERS}
    tau = lambda a, b, ms: round(V.SN.kendall_tau_b([gr[a][m] for m in ms], [gr[b][m] for m in ms]), 3)  # noqa: E731
    wr("leaderboard_650_final.csv", [{"model": m, "graded_v2": round(gr["v2"][m], 2), "graded_v22_prefix": round(gr["v2.2"][m], 2),
                                      "graded_final": round(gr["final"][m], 2), "rank_v2": rk["v2"][m], "rank_v22_prefix": rk["v2.2"][m],
                                      "rank_final": rk["final"][m]} for m in models])
    for c in cons:
        v = {"v2": "v2", "v2.2 (pre-fix)": "v2.2", "v2.2 final": "final"}[c["version"]]
        c.update({"tau_vs_v2": f"{tau('v2', v, models)} / {tau('v2', v, npm)}", "tau_vs_v22_prefix": f"{tau('v2.2', v, models)} / {tau('v2.2', v, npm)}",
                  "rank_changes_vs_v22_prefix": ";".join(f"{m} {rk['v2.2'][m]}->{rk[v][m]}" for m in models if rk[v][m] != rk["v2.2"][m])})
    wr("confirmation_650.csv", cons)

    # ---- synthetic ordering + counterexamples ------------------------------------------------------------
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    mono = []
    for name, cases in X.mono_families(F0).items():
        seq = {v: [] for v in ("v2.2", "final")}
        for lab, sc, act, fn, kw_ in cases:
            tel = CF.synth(sc, fn, **kw_)
            for v in seq:
                seq[v].append(syn(sc, act, tel, v)[0])
        for v, s_ in seq.items():
            if "(equal)" in name:
                bad = [f"{cases[0][0]} vs {cases[1][0]}"] if abs(s_[0] - s_[1]) > EPS else []
            else:
                bad = [f"{cases[i][0]} -> {cases[i + 1][0]}" for i in range(len(s_) - 1) if s_[i + 1] > s_[i] + EPS]
            mono.append({"family": name, "version": v, "cases": " | ".join(c[0] for c in cases),
                         "credits": " | ".join(f"{x:.3f}" for x in s_), "violations": len(bad), "violating_steps": "; ".join(bad)})
        if name == "overshoot at 25 km/h":
            fast = seq
        if name == "overshoot at 5 km/h":
            slow = seq
    for v in ("v2.2", "final"):
        bad = [f"{o} m" for o, a, b in zip((0.0, 0.1, 0.5, 2.0, 10.0, 50.0), fast[v], slow[v]) if a > b + EPS]
        mono.append({"family": "F4: 25 km/h <= 5 km/h at equal overshoot", "version": v, "cases": "0..50 m",
                     "credits": " | ".join(f"{a:.3f}<={b:.3f}" for a, b in zip(fast[v], slow[v])), "violations": len(bad),
                     "violating_steps": "; ".join(bad)})
    wr("ordering_final.csv", mono)
    beh = []
    for name, fn in X.behaviours(F0).items():
        tel = CF.synth("green_stop", fn)
        st = S.score_episode_from_telemetry({"scenario": "green_stop"}, tel, scenario="green_stop", expected_action="STOP",
                                            max_reaction_time=3.0, protocol="v2").get("verdict")
        beh.append({"behaviour (synthetic, green_stop)": name, "strict": st, "credit_v22_prefix": round(syn("green_stop", "STOP", tel, "v2.2")[0], 4),
                    "credit_final": round(syn("green_stop", "STOP", tel)[0], 4)})
    wr("behaviours_final.csv", beh)
    val = V.capture_validation()
    fx = collections.defaultdict(list)
    for e in val:
        if e["phase"] == "23_fixtures":
            fx[e["model"]].append((e["seed"], S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"]).get("verdict"),
                                   scored(e["res"], e["rows"], e["kw"], V.event_ids(e["ep"]), "final", e["forced_invalid"])[0]))
    wr("fixtures_final.csv", [{"fixture (CARLA)": m, "strict": "/".join(sorted({x[1] for x in xs})),
                               "credit_final": " / ".join(f"{x[2]:.3f}" for x in sorted(xs))} for m, xs in sorted(fx.items())])
    pols = policy_rows(("v2", "final"))
    wr("policies_final.csv", pols)

    for c in cons:
        say(c)
    for m in mono:
        if m["violations"]:
            say("ordering violation:", m["family"], m["version"], m["violating_steps"])
    best = max(gr["final"][m] for m in npm)
    say("policies: best final", max(p["graded_final"] for p in pols), "above best non-priv (", round(best, 2), "):",
        sum(p["graded_final"] > best for p in pols))
    for m in models:
        say(m, round(gr["v2"][m], 2), round(gr["v2.2"][m], 2), round(gr["final"][m], 2), rk["v2"][m], rk["v2.2"][m], rk["final"][m])
    (OUT / "stdout.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
