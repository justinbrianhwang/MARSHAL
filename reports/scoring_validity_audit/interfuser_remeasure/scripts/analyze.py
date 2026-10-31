"""InterFuser re-measurement: score every run with the published v2 scorer and the v2.1 candidate credit, decompose
the gaps, and write the tables used by ../interfuser_remeasure.md.

    python reports/scoring_validity_audit/interfuser_remeasure/scripts/analyze.py --v2-runs <tmp/_protocol_v2>

Run from the audit checkout root (it imports the published scorers from here). --v2-runs is the protocol-v2 run tree
holding the leaderboard episode interfuser/s0 (read only). Outputs go to ../data/.

Runs (setup, label, seed, directory):
  v2cfg  lb   s0   <v2-runs>/interfuser/s0                                   (the v2 leaderboard cell)
  v2cfg  rm   s1-2 tmp/_remeasure_interfuser/v2cfg/interfuser_v2cfg/s{1,2}    (v2 adapter configuration via config)
  fixed  a    s0   tmp/_validation_interfuser/authority/interfuser/s0         (first fixed-adapter run)
  fixed  rm   s0-2 tmp/_remeasure_interfuser/fixed/interfuser/s{0,1,2}
  ab     -    s0-2 tmp/_remeasure_interfuser/ab/interfuser_noofficer_green/s{0,1,2} (A->B, no officer, light Green)
"""
from __future__ import annotations

import argparse
import collections
import copy
import csv
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data"
AUD = HERE.parents[1]
sys.path[:0] = [str(AUD / "credit_v21" / "scripts"), str(AUD / "scripts"), ".", "scripts", "scripts/validation"]
import credit_v21 as V  # noqa: E402  (scored(), event_ids(), ALL_FIXES; imports the published collector as V.C)

C, S, G, vlm = V.C, V.S, V.G, V.vlm
W = G.SCENARIO_AUTHORITY_WEIGHTS
SCEN = list(vlm.SCENARIO_ORDER)
TERMS = ("A", "F_lat", "F_safe", "G_move", "e", "E_app")


def capture(ep: Path, sc: str):
    """(res, rows, kw, strict result) exactly as the published collector scores the episode."""
    cap, orig = {}, S.score_episode_from_telemetry

    def grab(res, rows, **kw):
        cap["a"] = (copy.deepcopy(res), list(rows), dict(kw))
        return orig(res, rows, **kw)
    S.score_episode_from_telemetry = grab
    try:
        r = C.score_episode(ep, sc, vlm.SCENARIOS, is_vlm=False)
    finally:
        S.score_episode_from_telemetry = orig
    if not r.pop("accepted"):
        raise SystemExit(f"collector refused {ep}: {r}")
    return (*cap["a"], r)


def kinematics(rows):
    hz = [r for r in rows if float(r["sim_time"]) <= 25.0 + 1e-9]
    v = [float(r.get("ego_speed_kmh") or 0) for r in hz]
    fwd = [float(r["ego_forward_m"]) for r in hz if r.get("ego_forward_m") not in (None, "")]
    t5 = next((float(r["sim_time"]) for r, x in zip(hz, v) if x >= 5.0), None)
    return {"v_max_kmh": round(max(v, default=0.0), 2), "travel_m": round(max(fwd) - min(fwd), 2) if fwd else 0.0,
            "t_5kmh_s": t5, "moved": max(v, default=0.0) >= 5.0}


def brake_cause(ep: Path):
    """Dominant native-controller brake reason while at rest after 1 s (verbose trace; absent for the v2 run)."""
    p = ep / "interfuser_debug" / "interfuser_verbose.jsonl"
    if not p.exists():
        return ""
    rest = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    rest = [r for r in rest if r["t"] > 1.0 and r["speed"] < 0.5]
    if not rest:
        return "-"
    c = collections.Counter("+".join(r["brake_reasons"]) or "desired_speed_0" if r["requested"]["throttle"] < 0.05
                            else "throttle" for r in rest)
    top, n = c.most_common(1)[0]
    return f"{top} ({n / len(rest):.0%} of {len(rest)} rest ticks)"


# Speed input each run directory was recorded with. The adapter option was added after these runs, so their episode
# metadata does not record it; runs made with a pinned override record it in config["interfuser"]["speed_input"].
#   3d:      <v2 tree>/interfuser, tmp/_remeasure_interfuser/{fixed,v2cfg,ab}, tmp/_validation_interfuser/*
#   forward: tmp/_remeasure_interfuser/speedfix, tmp/_greenstop_interfuser/{ab,officer}
def check_speed_input(ep: Path, expected: str) -> str:
    """Refuse an episode whose recorded speed input differs from the setting its directory is documented with."""
    cfg = json.loads((ep / "metadata.json").read_text(encoding="utf-8")).get("config") or {}
    rec = (cfg.get("interfuser") or {}).get("speed_input")
    if rec is not None and rec != expected:
        raise SystemExit(f"mixed configuration: {ep} records speed_input={rec!r}, expected {expected!r}")
    return rec or f"{expected} (by directory)"


def score_run(setup, label, seed, run_dir: Path):
    out = []
    for sc in SCEN:
        ep = C.find_episode(run_dir, sc)
        if ep is None:
            raise SystemExit(f"missing {sc} in {run_dir}")
        speed_input = check_speed_input(ep, "3d")  # every run in this analysis used the 3-D speed input
        res, rows, kw, r = capture(ep, sc)
        fi = r["strict"] == "INVALID"
        ids = V.event_ids(ep)
        c2, k2, _ = V.scored(res, rows, kw, ids, fixes=(), forced_invalid=fi)
        c21, k21, _ = V.scored(res, rows, kw, ids, fixes=V.ALL_FIXES, forced_invalid=fi)
        assert abs(c2 - float(r["credit"] or 0)) < 1e-6, (ep, c2, r["credit"])  # v2 credit = published collector
        out.append({"setup": setup, "run": label, "seed": seed, "scenario": sc, "expected": kw["expected_action"],
                    "weight": W[sc], "strict": r["strict"], "strict_reason": r["strict_reason"],
                    "credit_v2": c2, "credit_v21": c21, **kinematics(rows),
                    **{f"{t}_v2": k2[t] for t in TERMS}, **{f"{t}_v21": k21[t] for t in TERMS},
                    "brake_cause_at_rest": brake_cause(ep), "speed_input": speed_input,
                    "episode": str(ep).replace("\\", "/")})
    return out


def suite(cells, key):
    return 100 * sum(c["weight"] * c[key] for c in cells) / sum(c["weight"] for c in cells)


def wr(name, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def msd(xs):
    return (round(st.mean(xs), 2), round(st.stdev(xs), 2) if len(xs) > 1 else None, round(min(xs), 2), round(max(xs), 2))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-runs", required=True)
    a = ap.parse_args()
    rm = Path("tmp/_remeasure_interfuser")
    runs = [("v2cfg", "lb", 0, Path(a.v2_runs) / "interfuser" / "s0")]
    runs += [("v2cfg", "rm", s, rm / "v2cfg" / "interfuser_v2cfg" / f"s{s}") for s in (1, 2)]
    runs += [("fixed", "a", 0, Path("tmp/_validation_interfuser/authority/interfuser/s0"))]
    runs += [("fixed", "rm", s, rm / "fixed" / "interfuser" / f"s{s}") for s in (0, 1, 2)]
    def complete(d):
        eps = [C.find_episode(d, sc) for sc in SCEN] if d.exists() else [None]
        return all(e is not None and (e / "strict_scoring.json").exists() for e in eps)
    runs = [r for r in runs if complete(r[3])]  # runs still in progress are left out
    cells = [c for r in runs for c in score_run(*r)]
    wr("cells.csv", cells)

    # ---- suite scores per run and per setup ---------------------------------------------------------
    per_run, by_setup = [], collections.defaultdict(lambda: collections.defaultdict(list))
    for setup, label, seed, _ in runs:
        cs = [c for c in cells if (c["setup"], c["run"], c["seed"]) == (setup, label, seed)]
        rec = {"setup": setup, "run": label, "seed": seed, "graded_v2": round(suite(cs, "credit_v2"), 2),
               "graded_v21": round(suite(cs, "credit_v21"), 2), "strict_pass": sum(c["strict"] == "PASS" for c in cs),
               "no_motion_cells": sum(not c["moved"] for c in cs)}
        per_run.append(rec)
        if not (setup == "fixed" and label == "a"):  # the seed-0 repeat stands in for seed 0 in the setup mean
            for k in ("graded_v2", "graded_v21", "strict_pass", "no_motion_cells"):
                by_setup[setup][k].append(rec[k])
    wr("suite_per_run.csv", per_run)
    setup_rows = [{"setup": s, "seeds": len(d["graded_v2"]), **{f"{k}_{n}": v for k in d for n, v in
                   zip(("mean", "sd", "min", "max"), msd(d[k]))}} for s, d in by_setup.items()]
    wr("suite_per_setup.csv", setup_rows)

    # ---- gap 44.54 (v2.1, v2 run) vs 38.06 (v2, fixed run a): per scenario and per term ----------------
    key = {(c["setup"], c["run"], c["seed"], c["scenario"]): c for c in cells}
    sw = sum(W[s] for s in SCEN)
    gap = []
    for sc in SCEN:
        o, n = key[("v2cfg", "lb", 0, sc)], key[("fixed", "a", 0, sc)]
        cls = ("no-motion -> motion" if not o["moved"] and n["moved"] else "motion -> no-motion" if o["moved"] and not n["moved"]
               else f"strict {o['strict']} -> {n['strict']}" if o["strict"] != n["strict"] else "same strict")
        gap.append({"scenario": sc, "weight": W[sc], "class": cls,
                    "old_v2": o["credit_v2"], "old_v21": o["credit_v21"], "new_v2": n["credit_v2"], "new_v21": n["credit_v21"],
                    # contributions in suite points: scorer change on the old run, run change under v2, under v2.1
                    "pts_scorer_old": round(100 * W[sc] * (o["credit_v21"] - o["credit_v2"]) / sw, 3),
                    "pts_run_v2": round(100 * W[sc] * (n["credit_v2"] - o["credit_v2"]) / sw, 3),
                    "pts_run_v21": round(100 * W[sc] * (n["credit_v21"] - o["credit_v21"]) / sw, 3),
                    **{f"{t}_old_v21": o[f"{t}_v21"] for t in TERMS}, **{f"{t}_new_v21": n[f"{t}_v21"] for t in TERMS},
                    "old_strict": o["strict"], "new_strict": n["strict"], "old_v_max": o["v_max_kmh"], "new_v_max": n["v_max_kmh"],
                    "new_reason": n["strict_reason"]})
    wr("gap_decomposition.csv", gap)

    # ---- per-scenario table across seeds --------------------------------------------------------------
    sc_rows = []
    for sc in SCEN:
        for setup in ("v2cfg", "fixed"):
            cs = [c for c in cells if c["scenario"] == sc and c["setup"] == setup]
            sc_rows.append({"scenario": sc, "setup": setup,
                            "runs": ";".join(f"{c['run']}/s{c['seed']}" for c in cs),
                            "strict": ";".join(c["strict"][0] for c in cs),
                            "credit_v2": ";".join(f"{c['credit_v2']:.2f}" for c in cs),
                            "credit_v21": ";".join(f"{c['credit_v21']:.2f}" for c in cs),
                            "travel_m": ";".join(f"{c['travel_m']:.0f}" for c in cs),
                            "v_max_kmh": ";".join(f"{c['v_max_kmh']:.0f}" for c in cs),
                            "causes": " | ".join(sorted({(c["strict_reason"] or "")[:70] for c in cs if c["strict"] != "PASS"})),
                            "brake_at_rest": " | ".join(sorted({c["brake_cause_at_rest"].split(" (")[0] for c in cs
                                                                 if c["brake_cause_at_rest"] not in ("", "-")}))})
    wr("per_scenario_across_seeds.csv", sc_rows)

    # ---- setup comparison per scenario (fix vs seed noise) --------------------------------------------
    # every v2cfg run vs every fixed run except the seed-0 first run "a" (it is the repeatability check);
    # "separated" = the two setups' credit ranges do not overlap
    cmp_rows = []
    for sc in SCEN:
        o = [c for c in cells if c["scenario"] == sc and c["setup"] == "v2cfg"]
        n = [c for c in cells if c["scenario"] == sc and c["setup"] == "fixed" and c["run"] != "a"]
        if not o or not n:
            continue
        rec = {"scenario": sc, "weight": W[sc], "expected": o[0]["expected"]}
        for sfx in ("v2", "v21"):
            ov, nv = [c[f"credit_{sfx}"] for c in o], [c[f"credit_{sfx}"] for c in n]
            rec.update({f"v2cfg_{sfx}": ";".join(f"{x:.2f}" for x in ov), f"fixed_{sfx}": ";".join(f"{x:.2f}" for x in nv),
                        f"d_mean_{sfx}": round(st.mean(nv) - st.mean(ov), 3),
                        f"pts_{sfx}": round(100 * W[sc] * (st.mean(nv) - st.mean(ov)) / sw, 3),
                        f"separated_{sfx}": max(nv) < min(ov) or min(nv) > max(ov)})
        rec.update({"v2cfg_pass": sum(c["strict"] == "PASS" for c in o), "fixed_pass": sum(c["strict"] == "PASS" for c in n),
                    "v2cfg_moved": sum(c["moved"] for c in o), "fixed_moved": sum(c["moved"] for c in n), "runs": f"{len(o)}/{len(n)}"})
        for t in TERMS:
            def m(cs, k):
                xs = [float(c[k]) for c in cs if c[k] not in (None, "")]
                return round(st.mean(xs), 3) if xs else None
            rec[f"{t}_v21_v2cfg"], rec[f"{t}_v21_fixed"] = m(o, f"{t}_v21"), m(n, f"{t}_v21")
        cmp_rows.append(rec)
    if cmp_rows:
        wr("setup_comparison.csv", cmp_rows)
        for sfx in ("v2", "v21"):
            sep = sum(r[f"pts_{sfx}"] for r in cmp_rows if r[f"separated_{sfx}"])
            print(f"{sfx}: mean difference {sum(r[f'pts_{sfx}'] for r in cmp_rows):.2f} pts, "
                  f"of which separated cells {sep:.2f} pts ({sum(r[f'separated_{sfx}'] for r in cmp_rows)} cells)")

    # ---- A->B --------------------------------------------------------------------------------------
    ab = []
    for s in (0, 1, 2):
        d = rm / "ab" / "interfuser_noofficer_green" / f"s{s}"
        for sc in ("green_stop", "red_proceed", "signal_off", "unauthorized_go", "ambiguous_gesture", "fake_vest_director"):
            ep = C.find_episode(d, sc) if d.exists() else None
            if ep is None:
                continue
            tel = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
            rows = tel.get("telemetry") if isinstance(tel, dict) else tel
            import summarize_validation as SV  # noqa: E402  (A->B definition of the validation suite)
            m = SV.ab_metrics(rows)
            ab.append({"scenario": sc, "seed": s, **m, "brake_cause_at_rest": brake_cause(ep)})
    if ab:
        wr("ab_no_officer_green.csv", ab)

    for r in per_run:
        print(r)
    for r in setup_rows:
        print(r)
    print("gap points: scorer(old) %.2f, run under v2 %.2f, run under v2.1 %.2f" % (
        sum(g["pts_scorer_old"] for g in gap), sum(g["pts_run_v2"] for g in gap), sum(g["pts_run_v21"] for g in gap)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
