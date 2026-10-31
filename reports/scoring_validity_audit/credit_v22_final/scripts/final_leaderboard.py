"""Final candidate leaderboard under the frozen Credit v2.2 (recorded telemetry; strict verdicts as published).

    python <audit>/credit_v22_final/scripts/final_leaderboard.py [--remeasure DIR] [--superseded DIR]   # from the workspace root

- Every model keeps its protocol-v2 leaderboard runs (tmp/_protocol_v2), except InterFuser.
- InterFuser: only the final re-measure with the forward speed input (default DIR:
  <audit checkout>/tmp/_remeasure_interfuser/final_forward, layout interfuser/s<seed>/...). Only seeds 0, 1, 2 are read,
  and the row is ranked only when all three are complete; otherwise it is listed as pending and not ranked. An episode
  whose metadata records another speed input is refused.
- Listed separately, not compared: the v2 InterFuser run (integration defect) and the fixed-adapter re-measure with the
  3-D speed input (superseded; default --superseded DIR: <audit checkout>/tmp/_remeasure_interfuser/fixed).
- Sensitivity: F3 contact-identity bounds, v2.2 without the two geometry scenarios, F2 reference rows (48
  instruction-independent policies), F4 synthetic pairs.
Outputs (credit_v22_final/data/): final_leaderboard.csv, final_leaderboard_excluded.csv,
final_leaderboard_sensitivity.csv, final_leaderboard_coverage.csv; and credit_v22_final/final_leaderboard.md.
"""
from __future__ import annotations

import argparse
import collections
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import credit_final as F  # noqa: E402

X, V, G, S, CF = F.X, F.V, F.G, F.S, F.CF
GEOMETRY = ("occluded_officer", "sequential_directive")
MODEL = "interfuser"
REQUIRED_SEEDS = (0, 1, 2)  # the InterFuser row needs exactly these seeds, all complete
SOURCE = "re-measured, final adapter (forward speed input), seeds 0-2"


def check_speed(eps, expected, required=True):
    """Refuse episodes whose metadata records a different InterFuser speed input. required: an unrecorded value is
    also refused (ranked row); otherwise it is taken from the directory (superseded 3-D runs predate the field)."""
    import json
    for e in eps:
        cfg = json.loads((e["ep"] / "metadata.json").read_text(encoding="utf-8")).get("config") or {}
        rec = (cfg.get("interfuser") or {}).get("speed_input")
        if rec != expected and (required or rec is not None):
            raise SystemExit(f"{e['ep']} records speed_input={rec!r}, expected {expected!r}")
    return eps
NAMES = {"oracle": "oracle", "interfuser": "InterFuser", "neat": "NEAT", "cilrs": "CILRS", "qwen2.5-vl": "Qwen2.5-VL",
         "tcp": "TCP", "transfuser": "TransFuser", "mpc": "MPC", "pid": "PID", "qwen3-vl": "Qwen3-VL", "openemma": "OpenEMMA",
         "aim": "AIM", "baseline": "baseline", "glm-4.5v": "GLM-4.5V"}


def capture_dir(model_dir: Path, model: str, seeds=None):
    """Collector-scored episodes of every s<seed> under model_dir -> (episodes of complete seeds, coverage rows).
    seeds: if given, only these seed directories are read (any other s<n> is ignored)."""
    cap, out, cov = {}, [], []
    orig = S.score_episode_from_telemetry

    def grab(res, rows, **kw):
        cap["a"] = (copy.deepcopy(res), list(rows), dict(kw))
        return orig(res, rows, **kw)
    S.score_episode_from_telemetry = grab
    try:
        for sd in sorted(p for p in model_dir.iterdir() if p.is_dir() and p.name.startswith("s") and p.name[1:].isdigit()
                         and p.name == f"s{int(p.name[1:])}"
                         and (seeds is None or int(p.name[1:]) in seeds)):
            got = []
            for sc in V.vlm.SCENARIO_ORDER:
                ep = V.C.find_episode(sd, sc)
                if ep is None:
                    continue
                cap.clear()
                r = V.C.score_episode(ep, sc, V.vlm.SCENARIOS, is_vlm=False)
                if r.get("accepted") and "a" in cap:
                    res, rows, kw = cap["a"]
                    got.append({"model": model, "seed": int(sd.name[1:]), "scenario": sc, "res": res, "rows": rows, "kw": kw,
                                "forced_invalid": r["strict"] == "INVALID", "ep": ep})
            complete = len(got) == len(V.vlm.SCENARIO_ORDER)
            cov.append({"seed": sd.name, "scenarios_accepted": len(got), "complete": complete})
            if complete:
                out += got
    finally:
        S.score_episode_from_telemetry = orig
    return out, cov


def ranked_episodes(eps):
    """InterFuser episodes that may be ranked: only seeds 0, 1, 2, and only if all three are complete (capture_dir
    returns complete seeds only); otherwise none (row pending)."""
    eps = [e for e in eps if e["seed"] in REQUIRED_SEEDS]
    cells = {(e["seed"], e["scenario"]) for e in eps}
    want = len(REQUIRED_SEEDS) * len(V.vlm.SCENARIO_ORDER)
    return eps if {e["seed"] for e in eps} == set(REQUIRED_SEEDS) and len(cells) == len(eps) == want else []


def per_run(cells):
    runs = collections.defaultdict(list)
    for c in cells:
        runs[(c["model"], c["seed"])].append(c)
    out = collections.defaultdict(list)
    for (m, _), cs in runs.items():
        out[m].append(100 * sum(F.W[c["scenario"]] * c["credit"] for c in cs) / sum(F.W[c["scenario"]] for c in cs))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remeasure", type=Path, default=HERE.parents[3] / "tmp" / "_remeasure_interfuser" / "final_forward")
    ap.add_argument("--superseded", type=Path, default=HERE.parents[3] / "tmp" / "_remeasure_interfuser" / "fixed")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    eps = V.SN.capture()
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
    excluded = [e for e in eps if e["model"] == MODEL]
    eps = [e for e in eps if e["model"] != MODEL]
    re_eps, cov = capture_dir(a.remeasure / MODEL, MODEL, REQUIRED_SEEDS) if (a.remeasure / MODEL).is_dir() else ([], [])
    re_eps = check_speed(ranked_episodes(re_eps), "forward")
    sup_eps = []
    if (a.superseded / MODEL).is_dir():
        sup_eps = check_speed(ranked_episodes(capture_dir(a.superseded / MODEL, MODEL, REQUIRED_SEEDS)[0]), "3d", required=False)
    eps += re_eps
    for e in eps + excluded + sup_eps:
        e["ids"] = V.event_ids(e["ep"])
        e["strict"] = "INVALID" if e["forced_invalid"] else S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"]).get("verdict")
    F.wr("final_leaderboard_coverage.csv", cov or [{"seed": "", "scenarios_accepted": 0, "complete": False}])

    def cell(e, credit):
        return {"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"], "exp": e["kw"]["expected_action"], "credit": credit}

    def col(es, fn):
        return [cell(e, fn(e)) for e in es]

    final = lambda e: F.scored(e["res"], e["rows"], e["kw"], e["ids"], "final", e["forced_invalid"])[0]  # noqa: E731
    cols = {"final": col(eps, final),
            "v2": col(eps, lambda e: F.scored(e["res"], e["rows"], e["kw"], e["ids"], "v2", e["forced_invalid"])[0]),
            "F3 upper bound (hidden contacts distinct)": col(eps, lambda e: F.scored(e["res"], e["rows"], e["kw"], F.hidden_distinct(e["ids"]), "final", e["forced_invalid"])[0]),
            "without F3": col(eps, lambda e: F.scored_without_f3(e["res"], e["rows"], e["kw"], e["ids"], e["forced_invalid"])),
            "without the 2 geometry scenarios": [c for c in col(eps, final) if c["scenario"] not in GEOMETRY]}
    boards = {k: V.board(v) for k, v in cols.items()}
    models = sorted(boards["final"], key=lambda m: -boards["final"][m]["graded"])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    rank = {k: {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -b[m]["graded"]))} for k, b in boards.items()}
    runs = per_run(cols["final"])
    lb = []
    for m in models:
        b = boards["final"][m]
        lb.append({"rank": rank["final"][m], "model": NAMES.get(m, m), "runs": b["runs"],
                   "source": SOURCE if m == MODEL else "protocol-v2 run",
                   "strict_pass": f"{b['k']}/{b['n']}", "graded": round(b["graded"], 2), "sd": round(b["sd"], 2),
                   "min": round(min(runs[m]), 2), "max": round(max(runs[m]), 2), "graded_v2_same_runs": round(boards["v2"][m]["graded"], 2)})
    F.wr("final_leaderboard.csv", lb)
    exb = V.board(col(excluded, final))[MODEL]
    exv2 = V.board(col(excluded, lambda e: F.scored(e["res"], e["rows"], e["kw"], e["ids"], "v2", e["forced_invalid"])[0]))[MODEL]
    exrows = [{"model": "InterFuser (protocol-v2 run)", "status": "excluded: integration defect",
               "strict_pass": f"{exb['k']}/{exb['n']}", "graded_final": round(exb["graded"], 2), "sd_final": "",
               "graded_v2": round(exv2["graded"], 2)}]
    sup = None
    if sup_eps:
        sup = V.board(col(sup_eps, final))[MODEL]
        supv2 = V.board(col(sup_eps, lambda e: F.scored(e["res"], e["rows"], e["kw"], e["ids"], "v2", e["forced_invalid"])[0]))[MODEL]
        exrows.append({"model": "InterFuser (fixed adapter, 3-D speed input, seeds 0-2)", "status": "superseded: speed input",
                       "strict_pass": f"{sup['k']}/{sup['n']}", "graded_final": round(sup["graded"], 2),
                       "sd_final": round(sup["sd"], 2), "graded_v2": round(supv2["graded"], 2)})
    F.wr("final_leaderboard_excluded.csv", exrows)

    tau = lambda k, ms: round(V.SN.kendall_tau_b([boards["final"][m]["graded"] for m in ms], [boards[k][m]["graded"] for m in ms]), 3)  # noqa: E731
    lo, hi = boards["F3 upper bound (hidden contacts distinct)"], boards["final"]
    pairs = [(x, y) for i, x in enumerate(models) for y in models[i + 1:]
             if not (lo[x]["graded"] > hi[y]["graded"] or lo[y]["graded"] > hi[x]["graded"])]
    sens = [{"row": k, "tau_vs_final_all": tau(k, models), "tau_vs_final_nonpriv": tau(k, npm),
             **{NAMES.get(m, m): f"{boards[k][m]['graded']:.2f} (#{rank[k][m]})" for m in models}}
            for k in cols if k not in ("final",)]
    pols = F.policy_rows(("final",))
    best_sys = max(boards["final"][m]["graded"] for m in npm) if npm else 0.0
    best_pol = max(pols, key=lambda p: p["graded_final"])
    n_above = sum(p["graded_final"] > best_sys for p in pols)
    sens.append({"row": "F2 reference: best instruction-independent policy", "tau_vs_final_all": "", "tau_vs_final_nonpriv": "",
                 "oracle": f"{best_pol['policy']}: {best_pol['graded_final']:.2f}, strict {best_pol['strict_pass']}/25; "
                           f"{n_above}/48 policies above the best non-privileged system ({best_sys:.2f})"})
    F.wr("final_leaderboard_sensitivity.csv", sens)
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    f4 = [(o, F.syn("green_stop", "STOP", CF.synth("green_stop", CF.ramp_to_stop(25, 0.2, -o, F0, a=4.0)))[0],
           F.syn("green_stop", "STOP", CF.synth("green_stop", CF.ramp_to_stop(5, 0.2, -o, F0, a=1.0)))[0]) for o in (0.0, 0.1, 0.5, 2.0, 10.0)]

    # ---- leaderboard md (results only) ----------------------------------------------------------------
    n_seeds = len({e["seed"] for e in re_eps})
    L = ["# Final candidate leaderboard (Credit v2.2 final, frozen)", "",
         "- **Credit:** the frozen v2.2 formula ([credit_v22_final.md](credit_v22_final.md)). Strict verdicts are the "
         "published ones. MARSHAL-Graded = 100 · Σ w C / Σ w per run, then the mean over runs.",
         "- **Runs:** every model keeps its protocol-v2 runs, except InterFuser. InterFuser uses only the final "
         f"re-measure (forward speed input): {n_seeds} complete seed(s) of the required 0, 1, 2 (coverage in "
         "`data/final_leaderboard_coverage.csv`); it is ranked only when exactly these three seeds are complete.", ""]
    if not n_seeds:
        L += ["**InterFuser:** re-measure pending; the required seeds 0, 1, 2 are not all complete. It is not ranked.", ""]
    L += ["| rank | model | runs | strict | graded (mean ± SD) | min–max over runs |", "|---:|---|---:|---|---:|---|"]
    for r in lb:
        L.append(f"| {r['rank']} | {r['model']} | {r['runs']} | {r['strict_pass']} | {r['graded']:.2f} ± {r['sd']:.2f} | {r['min']:.2f}–{r['max']:.2f} |")
    L += ["", f"**Excluded: integration defect.** The InterFuser protocol-v2 run (1 run, strict {exb['k']}/{exb['n']}) is "
          f"preserved but not compared. It scores {exb['graded']:.2f} under this credit and {exv2['graded']:.2f} under v2 "
          "(`data/final_leaderboard_excluded.csv`).", ""]
    if sup:
        L += [f"**Superseded: speed input.** The InterFuser fixed-adapter re-measure with the 3-D speed input (3 runs, strict "
              f"{sup['k']}/{sup['n']}) scores {sup['graded']:.2f} ± {sup['sd']:.2f} under this credit "
              "(`data/final_leaderboard_excluded.csv`).", ""]
    L += ["## Sensitivity", "", "| row | Kendall τ-b vs final (all / non-privileged) | rank changes |", "|---|---|---|"]
    for k in cols:
        if k == "final":
            continue
        ch = ", ".join(f"{NAMES.get(m, m)} {rank['final'][m]}→{rank[k][m]}" for m in models if rank[k][m] != rank["final"][m]) or "none"
        L.append(f"| {k} | {tau(k, models)} / {tau(k, npm)} | {ch} |")
    L += ["", "**F3 contact-count uncertainty.** After the 20th counted collision event only the type id is logged.",
          "", "- The final score is the lower contact bound: same-type events within 1 s are one contact.",
          "- The upper bound counts every event without an actor id as a new actor.",
          "- Pairs whose score intervals overlap, so their order is not separated by the logs: "
          + (", ".join(f"{NAMES.get(x, x)}–{NAMES.get(y, y)}" for x, y in pairs) or "none") + ".", "",
          "**Geometry scenarios.** `occluded_officer` and `sequential_directive` place the line 2.5 m and 4.33 m from "
          "spawn, so a stop is feasible only with a near-immediate reaction. The row above drops both scenarios for "
          "every model.", "",
          "## Limits of the metric", "",
          "**F2: not resolved.** Credit scores observable trajectory compliance:", "",
          "- stop, hold and proceed timing;", "- line position;", "- enforced speed;", "- engagement and safety.", "",
          "It cannot observe whether the authority or the instruction was perceived. An instruction-independent "
          "trajectory can be identical to an instruction-following one.", "",
          f"- **Reference row (48 instruction-independent policies, synthetic):** the best is \"{best_pol['policy']}\" at "
          f"{best_pol['graded_final']:.2f} graded with strict {best_pol['strict_pass']}/25.",
          f"- {n_above} of the 48 policies score above the best non-privileged system in this table ({best_sys:.2f}).",
          "- The remedy is matched counterfactual runs (officer present vs absent, or STOP vs PROCEED), which need new "
          "rollouts.", "",
          "**F4: speed ordering at equal overshoot.**", "",
          "- The credit rewards a stop reached within the reaction budget.",
          "- A 25 km/h approach that stops on time therefore outscores a 5 km/h creep that overruns by the same distance "
          "but stops 4–10 s later (synthetic, `green_stop`): "
          + ", ".join(f"{o:g} m: {f:.3f} vs {s:.3f}" for o, f, s in f4) + ".",
          "- The metric does not rank impact speed.", ""]
    (HERE.parent / "final_leaderboard.md").write_text("\n".join(L), encoding="utf-8")
    print("InterFuser complete seeds:", n_seeds, "coverage:", cov, "| source:", SOURCE if n_seeds else "pending")
    for r in lb:
        print(r)
    print("excluded:", round(exb["graded"], 2), "pairs:", pairs, "policies above:", n_above, "best:", best_pol["graded_final"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
