"""Leaderboard impact of the scenario-geometry and model-configuration issues (audit; recorded data only, no rollouts).

    # optional, once: per-episode rescoring cache (needs the raw runs; run from the workspace root)
    python <audit>/leaderboard_impact/scripts/leaderboard_impact.py --rescore --runs tmp/_protocol_v2
    # analysis (reads only audit CSVs)
    python <audit>/leaderboard_impact/scripts/leaderboard_impact.py

Inputs (relative to the audit folder): data/episode_decomposition.csv (per-episode strict + credit),
data/published_leaderboard_v2_e6ae3c8.csv, crossing_analysis/crossing_causes.csv, and the cache written by --rescore
(leaderboard_impact/data/episode_rescore.csv: gate-free STOP/HOLD compliance for J, adjacent_lane rescored as STOP,
STOP-paddle requested/spawned). Graded on a scenario subset renormalises the weights: 100 * sum_{s in subset} w_s c_s /
sum_{s in subset} w_s per run, then the mean over runs (identical to the published formula when the subset is all 25).
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent                      # leaderboard_impact/
AUD = OUT.parent                       # scoring_validity_audit/
REPO = AUD.parents[1]                  # repository root holding scripts/ and marshal_bench/ (published scorers)
sys.path[:0] = [str(REPO / "scripts"), str(REPO), str(AUD / "scripts")]
import published_v2  # noqa: E402,F401  (pins the published v2 graded scorer; see published_v2.py)
import collect_protocol_v2 as C  # noqa: E402,F401  published collector; imported first so the audit modules reuse it
import _run_vlm_test as vlm  # noqa: E402
from sensitivity import kendall_tau_b  # noqa: E402  (audit's local tau-b, ties corrected)

PRIV = {"oracle"}
REFERENCE = {"baseline"}               # officer-blind reference; not counted as an evaluated model in headline checks
OFFICER_BLIND = {"baseline", "pid", "mpc"}
VLM_C = {"glm-4.5v", "qwen2.5-vl", "qwen3-vl"}
MOVE = {"PROCEED", "DETOUR", "YIELD"}
CFG_MODELS = {"interfuser", "aim", "transfuser"}

STOPHOLD_IN_BAND = ["green_stop", "signal_off", "fallen_person", "unauthorized_go", "adjacent_lane", "flagger_control",
                    "occluded_officer", "conflicting_authorities", "sequential_directive", "ambiguous_gesture",
                    "two_civilians_disagree", "flagger_slow_then_stop", "school_crossing_guard", "fake_vest_director"]
GEOMETRY = {  # id: (scenarios, short title)
    "G1": (["red_proceed", "night_signal_officer_conflict"], "spawn 4.58 m past the stop line (PROCEED not identifiable vs red-running)"),
    "G2": (["occluded_officer", "sequential_directive"], "tight onset margin (line 2.50 / 4.33 m from spawn)"),
    "G3": (["fallen_person"], "placeholder officer anchor 1.61 m from spawn enables stop-line gates"),
    "G4": (["two_civilians_disagree", "fake_vest_director"], "proximity anchor is the unauthorized civilian"),
    "G5": (["adjacent_lane"], "expected action STOP (yaml) vs HOLD (runner)"),
    "G6": (["dual_authority_handoff"], "hold band lies past the stop line (compliant run must cross)"),
    "G7": (STOPHOLD_IN_BAND, "16.59 m band never binds; spawn already inside band (F2 lurch exposure)"),
}
CONTRACT = ["G1", "G2", "G3", "G4", "G5", "G6"]


def rd(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def wr(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------------------------------- rescore cache
def rescore(runs: Path):
    """Per leaderboard episode: gate-free STOP/HOLD compliance (audit candidate B), adjacent_lane scored as STOP,
    and the STOP-paddle request/spawn record. Uses the published collector + scorers of this repository."""
    import alternatives as ALT        # noqa: E402  (score(): strict / credit / gate-free compliance)
    S = C.S
    out, cap = [], {}
    orig = S.score_episode_from_telemetry

    def grab(result, rows, **kw):
        cap["a"] = (dict(result), list(rows), dict(kw))
        return orig(result, rows, **kw)
    S.score_episode_from_telemetry = grab
    try:
        for md in sorted(p for p in runs.iterdir() if p.is_dir() and "_cap3" not in p.name and "_abl-" not in p.name):
            for sd in sorted(p for p in md.iterdir() if p.is_dir() and p.name.startswith("s")):
                for sc in vlm.SCENARIO_ORDER:
                    ep = C.find_episode(sd, sc)
                    if ep is None:
                        continue
                    cap.clear()
                    r = C.score_episode(ep, sc, vlm.SCENARIOS, is_vlm=md.name in C.VLM_MODELS)
                    if not (r.get("accepted") and "a" in cap):
                        continue
                    res, rows, kw = cap["a"]
                    S.score_episode_from_telemetry = orig
                    a = ALT.score(res, rows, kw, "A")
                    as_stop = ALT.score(res, rows, {**kw, "expected_action": "STOP"}, "A") if sc == "adjacent_lane" else None
                    S.score_episode_from_telemetry = grab
                    ev = (ep / "events.json").read_text(encoding="utf-8") if (ep / "events.json").exists() else ""
                    req = re.search(r'"hand_prop":\s*"[^"]+"', ev) is not None  # officer spawn event (all runners)
                    spawned = re.search(r'"hand_prop_actor_id":\s*\d', ev) is not None
                    out.append({"episode_id": f"{md.name}/s{int(sd.name[1:])}/{sc}", "model": md.name,
                                "seed": int(sd.name[1:]), "scenario": sc, "strict": a["strict"],
                                "credit": round(a["credit"], 6), "comply_gate_free": int(bool(a["comply"])),
                                "strict_as_STOP": as_stop["strict"] if as_stop else "",
                                "credit_as_STOP": round(as_stop["credit"], 6) if as_stop else "",
                                "paddle_requested": int(req), "paddle_spawned": int(spawned)})
    finally:
        S.score_episode_from_telemetry = orig
    wr(OUT / "data" / "episode_rescore.csv", out)
    print(f"rescored {len(out)} episodes")


# ---------------------------------------------------------------------------------------------------- analysis
def board(eps, scen, models):
    """model -> dict(graded, strict k, n, J, runs) on a scenario subset (weights renormalised per run)."""
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for e in eps:
        if e["scenario"] in scen and e["model"] in models:
            per[e["model"]][e["seed"]].append(e)
    out = {}
    for m, seeds in per.items():
        g, js = [], []
        k = n = 0
        for cells in seeds.values():
            g.append(100 * sum(c["w"] * c["credit"] for c in cells) / sum(c["w"] for c in cells))
            k += sum(c["strict"] == "PASS" for c in cells)
            n += len(cells)
            side = lambda pred: ([c for c in cells if pred(c["exp"])])  # noqa: E731
            st_, mv = side(lambda x: x in ("STOP", "HOLD")), side(lambda x: x in MOVE)
            if st_ and mv:
                rate = lambda cs: sum(c["w"] * c["comply"] for c in cs) / sum(c["w"] for c in cs)  # noqa: E731
                js.append(rate(st_) + rate(mv) - 1)
        out[m] = {"graded": sum(g) / len(g), "k": k, "n": n, "runs": len(seeds),
                  "J": sum(js) / len(js) if js else None}
    return out


def ranks(b):
    order = sorted(b, key=lambda m: -b[m]["graded"])
    return {m: i + 1 for i, m in enumerate(order)}


def headline(b):
    np_ = {m: v for m, v in b.items() if m not in PRIV | REFERENCE}
    top = sorted(np_, key=lambda m: -np_[m]["graded"])
    js = {m: v["J"] for m, v in np_.items() if v["J"] is not None}
    vl = [m for m in top if m in VLM_C]
    return {"top_nonpriv": top[0], "second_nonpriv": top[1], "top_margin": np_[top[0]]["graded"] - np_[top[1]]["graded"],
            "oracle_gap": (b["oracle"]["graded"] - np_[top[0]]["graded"]) if "oracle" in b else None,
            "best_vlm_c": vl[0] if vl else None,
            "max_J_nonpriv": max(js.values()) if js else None,
            "max_J_model": max(js, key=js.get) if js else None,
            "oracle_J": b.get("oracle", {}).get("J")}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--runs", default="tmp/_protocol_v2")
    a = ap.parse_args()
    if a.rescore:
        rescore(Path(a.runs))

    dec = [r for r in rd(AUD / "data" / "episode_decomposition.csv") if r["cohort"] == "leaderboard"]
    resc = {r["episode_id"]: r for r in rd(OUT / "data" / "episode_rescore.csv")}
    pub = {r["model"]: r for r in rd(AUD / "data" / "published_leaderboard_v2_e6ae3c8.csv")}
    eps = []
    for r in dec:
        x = resc[r["episode_id"]]
        assert x["strict"] == r["strict"] and abs(float(x["credit"]) - float(r["credit"])) < 1e-5, r["episode_id"]
        eps.append({"id": r["episode_id"], "model": r["model"], "seed": int(r["seed"]), "scenario": r["scenario"],
                    "exp": r["expected_action"], "w": float(r["weight"]), "credit": float(r["credit"]),
                    "strict": r["strict"], "comply": int(x["comply_gate_free"]), "gate": r["movement_gate"] == "1"})
    assert len(eps) == 650
    ALL = sorted({e["scenario"] for e in eps})
    MODELS = set(pub)
    base = board(eps, set(ALL), MODELS)
    for m, v in base.items():  # reproduction gate: published graded (2 dp) and strict total
        assert round(v["graded"], 2) == float(pub[m]["graded_mean"]), (m, v["graded"])
        assert f"{v['k']}/{v['n']}" == pub[m]["strict_pass_total"], m
    assert sum(float(w) for w in {e["scenario"]: e["w"] for e in eps}.values()) == 42.5

    conds = [("P", "published (25 scenarios)", set(ALL), MODELS)]
    for gid in GEOMETRY:
        conds.append((f"X-{gid}", f"exclude {gid}: {', '.join(GEOMETRY[gid][0]) if gid != 'G7' else '14 in-band STOP/HOLD'}",
                      set(ALL) - set(GEOMETRY[gid][0]), MODELS))
    clean = set(ALL) - {s for g in CONTRACT for s in GEOMETRY[g][0]}
    clean7 = clean - set(GEOMETRY["G7"][0])
    conds += [("CLEAN", f"exclude G1-G6 ({len(clean)} scenarios left)", clean, MODELS),
              ("CLEAN+G7", f"exclude G1-G7 ({len(clean7)} move-side scenarios left)", clean7, MODELS),
              ("CFG", "25 scenarios; InterFuser not evaluable (AIM kept, caveat)", set(ALL), MODELS - {"interfuser"}),
              ("CFG-AIM", "25 scenarios; InterFuser and AIM removed", set(ALL), MODELS - {"interfuser", "aim"}),
              ("BOTH", "CLEAN + InterFuser not evaluable", clean, MODELS - {"interfuser"}),
              ("BOTH-AIM", "CLEAN + InterFuser and AIM removed", clean, MODELS - {"interfuser", "aim"})]
    paddle = {x["scenario"] for x in resc.values() if x["paddle_requested"] == "1"}
    conds.append(("X-C5", f"exclude the {len(paddle)} scenarios whose STOP paddle was requested but not spawned",
                  set(ALL) - paddle, MODELS))

    impact, summ = [], []
    for cid, desc, scen, models in conds:
        b = board(eps, scen, models)
        rk = ranks(b)
        npr = ranks({m: v for m, v in b.items() if m not in PRIV | REFERENCE})
        r0 = ranks({m: base[m] for m in b})  # published order restricted to the models present
        np0 = ranks({m: base[m] for m in npr})
        ms = sorted(b, key=lambda m: -base[m]["graded"])
        tau = kendall_tau_b([base[m]["graded"] for m in ms], [b[m]["graded"] for m in ms])
        np_ms = [m for m in ms if m not in PRIV | REFERENCE]
        tau_np = kendall_tau_b([base[m]["graded"] for m in np_ms], [b[m]["graded"] for m in np_ms])
        h = headline(b)
        jx = [v["J"] for m, v in b.items() if m not in PRIV | REFERENCE | {"interfuser"} and v["J"] is not None]
        # InterFuser J with its no-motion cells dropped (gate-free compliance otherwise credits its non-start as a stop)
        bi = board([e for e in eps if e["model"] != "interfuser" or e["gate"]], scen, {"interfuser"}) if "interfuser" in b else {}
        blind = max((b[m]["graded"] for m in ("pid", "mpc") if m in b), default=None)
        evals = [m for m in b if m not in PRIV | REFERENCE | OFFICER_BLIND]
        moves = {m: np0[m] - npr[m] for m in npr}  # positive = moved up among non-privileged
        summ.append({"condition": cid, "description": desc, "n_scenarios": len(scen), "weight_sum": round(
            sum({e["scenario"]: e["w"] for e in eps if e["scenario"] in scen}.values()), 2), "n_models": len(b),
            "kendall_tau_b_all": round(tau, 3), "kendall_tau_b_nonpriv": round(tau_np, 3),
            "max_abs_delta_graded": round(max(abs(b[m]["graded"] - base[m]["graded"]) for m in b), 2),
            "max_nonpriv_rank_move": max(abs(v) for v in moves.values()),
            "n_nonpriv_rank_changes": sum(v != 0 for v in moves.values()),
            "top_nonpriv": h["top_nonpriv"], "second_nonpriv": h["second_nonpriv"], "top_margin": round(h["top_margin"], 2),
            "oracle_gap": round(h["oracle_gap"], 2), "best_vlm_c": h["best_vlm_c"],
            "max_J_nonpriv": "" if h["max_J_nonpriv"] is None else round(h["max_J_nonpriv"], 3),
            "max_J_model": h["max_J_model"] or "", "oracle_J": "" if h["oracle_J"] is None else round(h["oracle_J"], 3),
            "max_J_excl_interfuser": round(max(jx), 3) if jx else "",
            "J_interfuser_moving_cells": "" if not bi or bi["interfuser"]["J"] is None else round(bi["interfuser"]["J"], 3),
            "n_evaluated_below_pid_or_mpc": sum(b[m]["graded"] < blind for m in evals)})
        for m in ms:
            v = b[m]
            impact.append({"condition": cid, "model": m, "track": pub[m]["track"], "graded": round(v["graded"], 2),
                           "delta_graded": round(v["graded"] - base[m]["graded"], 2), "rank": rk[m],
                           "rank_change": r0[m] - rk[m], "rank_nonpriv": npr.get(m, ""),
                           "rank_nonpriv_change": moves.get(m, ""), "strict_k": v["k"], "strict_n": v["n"],
                           "J_gate_free": "" if v["J"] is None else round(v["J"], 3),
                           "n_scenarios": len(scen)})
    wr(OUT / "data" / "impact_scenarios.csv", impact)
    wr(OUT / "data" / "impact_conditions.csv", summ)

    # ------------------------------------------------------------------ issue registry
    cc = rd(AUD / "crossing_analysis" / "crossing_causes.csv")
    tight = [r["episode_id"] for r in cc if r["relevance"] == "binding" and r["scenario_tight_margin"] == "True"]
    spawned = [r["episode_id"] for r in cc if r["cause_class"] == "spawned_past"]
    adj = [x for x in resc.values() if x["scenario"] == "adjacent_lane"]
    adj_flip = [x["episode_id"] for x in adj if x["strict_as_STOP"] != x["strict"] or abs(float(x["credit_as_STOP"]) - float(x["credit"])) > 1e-9]
    pad_req = [x for x in resc.values() if x["paddle_requested"] == "1"]
    pad_sp = sum(x["paddle_spawned"] == "1" for x in pad_req)
    pad_scen = sorted({x["scenario"] for x in pad_req})
    S = {s["condition"]: s for s in summ}
    blind_pass = {sc: sorted(e["model"] for e in eps if e["scenario"] == sc and e["model"] in OFFICER_BLIND and e["strict"] == "PASS")
                  for sc in GEOMETRY["G1"][0]}

    def eff(cid):
        s = S[cid]
        return (f"tau_all {s['kendall_tau_b_all']}; tau_nonpriv {s['kendall_tau_b_nonpriv']}; max |dGraded| {s['max_abs_delta_graded']}; "
                f"non-priv rank changes {s['n_nonpriv_rank_changes']} (max {s['max_nonpriv_rank_move']}); top {s['top_nonpriv']}")

    reg = []
    allm = "all 14 models"
    reg.append(dict(issue_id="G1", kind="geometry", issue=GEOMETRY["G1"][1], scenarios=";".join(GEOMETRY["G1"][0]), models=allm,
                    n_episodes=len(spawned), episodes="all runs of both scenarios",
                    evidence="measured: row-0 stopline_front_m = -4.578 in all 52 episodes; officer-blind controllers pass: "
                    + "; ".join(f"{k}={','.join(v) or 'none'}" for k, v in blind_pass.items()),
                    evidence_class="measured", leaderboard_effect=eff("X-G1")))
    reg.append(dict(issue_id="G2", kind="geometry", issue=GEOMETRY["G2"][1], scenarios=";".join(GEOMETRY["G2"][0]), models=allm,
                    n_episodes=len(tight), episodes=";".join(tight),
                    evidence="measured: 31 binding crossings classed tight_onset_margin (stoppable at onset, not after a 1 s coast); "
                    "scenario exclusion removes all 52 runs", evidence_class="measured", leaderboard_effect=eff("X-G2")))
    reg.append(dict(issue_id="G3", kind="geometry", issue=GEOMETRY["G3"][1], scenarios="fallen_person", models=allm, n_episodes=26,
                    episodes="all runs", evidence="code + recorded metadata: null-officer point 1.61 m from spawn used as proximity anchor",
                    evidence_class="measured (anchor) / inferred (effect on verdicts)", leaderboard_effect=eff("X-G3")))
    reg.append(dict(issue_id="G4", kind="geometry", issue=GEOMETRY["G4"][1], scenarios=";".join(GEOMETRY["G4"][0]), models=allm,
                    n_episodes=52, episodes="all runs", evidence="code: director position (unauthorized civilian) is the band anchor",
                    evidence_class="code-inspected / inferred (effect)", leaderboard_effect=eff("X-G4")))
    reg.append(dict(issue_id="G5", kind="geometry", issue=GEOMETRY["G5"][1], scenarios="adjacent_lane", models=allm, n_episodes=len(adj),
                    episodes=";".join(adj_flip) or "none change",
                    evidence=f"measured: rescoring all {len(adj)} runs with expected_action=STOP changes {len(adj_flip)} strict verdicts/credits",
                    evidence_class="measured", leaderboard_effect=eff("X-G5")))
    reg.append(dict(issue_id="G6", kind="geometry", issue=GEOMETRY["G6"][1], scenarios="dual_authority_handoff", models=allm, n_episodes=26,
                    episodes="all runs", evidence="measured: 15 runs cross by contract; only the oracle passes",
                    evidence_class="measured", leaderboard_effect=eff("X-G6")))
    reg.append(dict(issue_id="G7", kind="geometry", issue=GEOMETRY["G7"][1], scenarios=";".join(STOPHOLD_IN_BAND), models=allm,
                    n_episodes=26 * len(STOPHOLD_IN_BAND), episodes="all runs",
                    evidence="measured: 0 verdict changes at +-25% band; scripted lurch-then-stop passes green_stop in CARLA (3/3 seeds); "
                    "synthetic lurch 13/25 strict, 59.8 graded", evidence_class="measured (exposure) / inferred (exploitation by evaluated models)",
                    leaderboard_effect=eff("X-G7")))
    reg.append(dict(issue_id="C1", kind="config", issue="InterFuser v2 integration deviated from the original agent (LiDAR half-sweep parity feeding an empty front LiDAR BEV, CARLA-default LiDAR/camera attributes, 4 m route target, no odd-step hold); v2 no-start on an empty road",
                    scenarios="all 25", models="interfuser", n_episodes=25, episodes="interfuser/s0/*",
                    evidence="measured: v2 no-officer A->B v_max 0.3 km/h, travel 0.0 m; parity defect measured on input data; restored adapter, seed 0: graded 43.53 -> 38.06, strict 8/25 -> 7/25 (single seed)", evidence_class="measured (defect, single-seed effect) / unresolved (direction of the multi-seed effect)",
                    leaderboard_effect=eff("CFG")))
    reg.append(dict(issue_id="C2", kind="config", issue="AIM 4 m route target: sub-5 km/h creep", scenarios="all 25", models="aim", n_episodes=25,
                    episodes="aim/s0/*", evidence="measured on green_stop no-officer: 4 m target v_max 4.6 km/h; 50 m target 16.6 km/h",
                    evidence_class="measured (one station) / inferred (leaderboard effect)", leaderboard_effect=eff("CFG-AIM") + " (removal; re-run needed for true effect)"))
    reg.append(dict(issue_id="C3", kind="config", issue="TransFuser 5 Hz instead of 10 Hz; deadlock escape needs >=55 s (unreachable in 25 s)",
                    scenarios="all 25", models="transfuser", n_episodes=25, episodes="transfuser/s0/*",
                    evidence="measured: empty-road start 6.15 s at 5 Hz vs 1.4 s at 10 Hz; with officer present never moves in 25 s (scene response)",
                    evidence_class="measured (start delay) / inferred (leaderboard effect small)", leaderboard_effect="not removable by subsetting; needs re-run"))
    reg.append(dict(issue_id="C4", kind="config", issue="TCP and NEAT get ~4 m targets instead of up to 50 m", scenarios="all 25", models="tcp;neat",
                    n_episodes=50, episodes="tcp/s0/*;neat/s0/*",
                    evidence="measured: TCP 50 m target 7.8 vs 7.7 km/h (slow by itself); NEAT 26.5 vs 25.8 km/h, same start/arrival",
                    evidence_class="measured (no effect on green_stop)", leaderboard_effect="none measured"))
    reg.append(dict(issue_id="C5", kind="stimulus", issue="STOP paddle prop requested but never spawned (gesture shown by pose only)",
                    scenarios=";".join(pad_scen), models=allm, n_episodes=len(pad_req), episodes=f"{len(pad_req)} leaderboard episodes request it; {pad_sp} spawned",
                    evidence="measured: officer_metadata.hand_prop set, hand_prop_actor_id null", evidence_class="measured (defect) / not measurable (effect; needs re-run)",
                    leaderboard_effect=eff("X-C5") + " (removal only; effect of a visible paddle needs re-run)"))
    wr(OUT / "data" / "issue_registry.csv", reg)
    print(json.dumps(summ, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
