"""Scorer-only comparison of v2 (Candidate A) with experimental alternatives on IDENTICAL recorded telemetry (audit).

    python <audit>/scripts/alternatives.py --out <audit>     # from the workspace root

Nothing here replaces an official number. Candidates:

A   v2 as published.
A'  v2 with one specification correction: the STOP/HOLD engagement "approach window" ends at the first stop
    that FOLLOWS motion (> 1 km/h after onset) instead of the first sub-1 km/h tick at/after onset; the onset row
    that anchors the approach distance is unchanged (one-line change, applied to the published function's source). Under the v2
    rest start (0 m/s, directive onset 1 s) the published rule ends the window at the onset tick for any ego still
    at rest, so an approach made after the directive appears is scored as a creep (approach-speed credit 0).
B   state-aware eligibility + discrimination. STOP/HOLD "compliance" = the v2 strict cascade with the movement /
    proximity eligibility gates removed (a vehicle that stays safely behind the line complies). Move-required
    cells (PROCEED / DETOUR / YIELD) keep the v2 strict verdict. Suite metric: J = STOP-side rate + move-side
    rate - 1 (weighted by the published scenario weights within each side). The two sides are different,
    unmatched scenarios, so J is a difference of side-specific rates: an instruction-independent policy is NOT
    guaranteed J <= 0 (scenario confounding); a discrimination claim needs matched-condition pairs.
C   hierarchical: rank by (collision-free share desc, J desc, A graded desc) — reported, not adopted.
D   multidimensional report: STOP-side compliance, move-side completion, safety, timeliness, engagement, J.

Desiderata (fixed before computing): D1 instruction-independent policies must not outscore the light-only
baseline; D2 timing-invariant engagement; D3 at equal overshoot a faster crossing must not score higher;
D4 strict-compliant behaviour keeps full credit; D5 ranking changes are reported, not optimised.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sensitivity as SN  # noqa: E402  (capture() of the collector's exact scorer inputs)
import counterfactual as CF  # noqa: E402

S, G, vlm = SN.S, SN.G, SN.vlm
W = G.SCENARIO_AUTHORITY_WEIGHTS
MOVE = ("PROCEED", "DETOUR", "YIELD")
ELIG = {"engagement_speed_kmh": 0.0, "engagement_progress_m": 0.0, "stopline_engagement_m": 1e9,
        "hazard_engagement_m": 1e9}


class patched:
    """Temporarily override threshold-dict values in every dict that holds the key."""
    def __init__(self, **kv):
        self.kv, self.old = kv, []

    def __enter__(self):
        for d in (S.STRICT_THRESHOLDS, G.GRADED_THRESHOLDS, S.PROTOCOL_V2):
            for k, v in self.kv.items():
                if k in d:
                    self.old.append((d, k, d[k]))
                    d[k] = v

    def __exit__(self, *a):
        for d, k, v in reversed(self.old):
            d[k] = v


_orig_engagement = G._stop_hold_engagement_factor


def _build_motion_anchored():
    """A': the published function with ONE change -- the search for the stop that ends the approach window starts
    at the first tick >= onset with v > stop speed (first motion after the directive appears) instead of at the
    onset tick. The onset row that anchors d_app and every other use of `onset` are unchanged."""
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(_orig_engagement))
    old_loop = (
        '    first_stop_time: Optional[float] = None\n'
        '    for row in rows:\n'
        '        if float(row["sim_time"]) >= onset and float(row["ego_speed_kmh"]) <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n')
    new_loop = (
        '    first_stop_time: Optional[float] = None\n'
        '    _search_start = next((float(r["sim_time"]) for r in rows if float(r["sim_time"]) >= onset\n'
        '                          and float(r["ego_speed_kmh"]) > GRADED_THRESHOLDS["stop_speed_kmh"]), onset)\n'
        '    for row in rows:\n'
        '        if float(row["sim_time"]) >= _search_start and float(row["ego_speed_kmh"]) <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n')
    assert src.count(old_loop) == 1, "published engagement function changed; A' patch no longer applies"
    src = src.replace(old_loop, new_loop).replace("def _stop_hold_engagement_factor(", "def _stop_hold_engagement_factor_a_prime(", 1)
    ns = dict(vars(G))
    exec(compile(src, "<A-prime>", "exec"), ns)
    return ns["_stop_hold_engagement_factor_a_prime"]


_engagement_motion_anchored = _build_motion_anchored()


def score(res, rows, kw, cand):
    if cand == "A'":
        G._stop_hold_engagement_factor = _engagement_motion_anchored
    try:
        s = S.score_episode_from_telemetry(res, rows, **kw)
        g = G.score_episode_from_telemetry(res, rows, **kw)
    finally:
        G._stop_hold_engagement_factor = _orig_engagement
    out = {"strict": s.get("verdict"), "credit": float(g.get("credit") or 0.0),
           "collisions": (g.get("collision_count") or 0)}
    if kw["expected_action"] in ("STOP", "HOLD"):
        with patched(**ELIG):
            out["comply"] = S.score_episode_from_telemetry(res, rows, **kw).get("verdict") == "PASS"
    else:
        out["comply"] = out["strict"] == "PASS"
    return out


def suite(cells):
    """cells: list of (scenario, expected, out) for ONE run -> dict of suite metrics."""
    def side(pred):
        ws = [(W[sc], o) for sc, e, o in cells if pred(e)]
        return sum(w * o["comply"] for w, o in ws) / sum(w for w, _ in ws) if ws else float("nan")
    stop_r, move_r = side(lambda e: e in ("STOP", "HOLD")), side(lambda e: e in MOVE)
    return {"graded": 100 * sum(W[sc] * o["credit"] for sc, _, o in cells) / sum(W.values()),
            "strict": sum(o["strict"] == "PASS" for _, _, o in cells),
            "stop_side": stop_r, "move_side": move_r, "J": stop_r + move_r - 1,
            "collision_free": sum(o["collisions"] == 0 for _, _, o in cells) / len(cells)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    eps = SN.capture()
    res = collections.defaultdict(lambda: collections.defaultdict(list))
    changed = collections.Counter()
    for e in eps:
        exp = e["kw"]["expected_action"]
        oa = score(e["res"], e["rows"], e["kw"], "A")
        oa2 = score(e["res"], e["rows"], e["kw"], "A'")
        if e["forced_invalid"]:
            oa["credit"] = oa2["credit"] = 0.0
            oa["comply"] = oa2["comply"] = False
        if abs(oa["credit"] - oa2["credit"]) > 1e-9:
            changed[(e["model"], e["scenario"], e["seed"], round(oa["credit"], 4), round(oa2["credit"], 4))] += 1
        res["A"][(e["model"], e["seed"])].append((e["scenario"], exp, oa))
        res["A'"][(e["model"], e["seed"])].append((e["scenario"], exp, oa2))
    table = {}
    for cand in ("A", "A'"):
        per = collections.defaultdict(list)
        for (m, sd), cells in res[cand].items():
            per[m].append(suite(cells))
        table[cand] = {m: {k: sum(x[k] for x in v) / len(v) for k in v[0]} for m, v in per.items()}
    # synthetic instruction-independent policies (D1)
    pol = {"park at spawn": lambda t: 0.0,
           "lurch then stopped": lambda t: 9.0 * t if t < 0.889 else max(0.0, 8.0 - 14.4 * (t - 0.889)),
           "constant 20 km/h": lambda t: min(20.0, 9 * t)}
    syn = {}
    for pn, fn in pol.items():
        for cand in ("A", "A'"):
            cells = []
            for sc in vlm.SCENARIO_ORDER:
                exp = vlm.SCENARIOS[sc]["expect"]
                kw = dict(scenario=sc, expected_action=exp, max_reaction_time=3.0, protocol="v2")
                cells.append((sc, exp, score({"scenario": sc}, CF.synth(sc, fn), kw, cand)))
            syn[(pn, cand)] = suite(cells)
    # D2/D3 on the counterfactual approach-timing and overshoot cases
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    kwg = dict(scenario="green_stop", expected_action="STOP", max_reaction_time=3.0, protocol="v2")
    timing = {cand: [round(score({"scenario": "green_stop"}, CF.synth("green_stop", CF.ramp_to_stop(15, t0, 0.5, F0)), kwg, cand)["credit"], 4)
                     for t0 in (0.0, 0.5, 1.0, 1.5, 3.0)] for cand in ("A", "A'")}
    over = {cand: [(o, round(score({"scenario": "green_stop"}, CF.synth("green_stop", CF.ramp_to_stop(5, 0.2, -o, F0, a=1.0)), kwg, cand)["credit"], 4),
                    round(score({"scenario": "green_stop"}, CF.synth("green_stop", CF.ramp_to_stop(25, 0.2, -o, F0, a=4.0)), kwg, cand)["credit"], 4))
                   for o in (0.0, 0.5, 2.0, 10.0)] for cand in ("A", "A'")}

    models = sorted(table["A"], key=lambda m: -table["A"][m]["graded"])
    L = ["# Scoring alternatives on identical recorded telemetry (generated by scripts/alternatives.py)\n",
         "Scorer-only comparison; no controller was rerun. Official v2 results are unchanged. Per-model values are the "
         "mean over runs (five for VLM-based controllers, one otherwise).\n",
         "## Per model\n",
         "| model | A graded | A strict | A' graded | B STOP-side compliance | B move-side completion | B J (discrimination) | collision-free share |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for m in models:
        x, y = table["A"][m], table["A'"][m]
        L.append(f"| {m} | {x['graded']:.2f} | {x['strict']:.1f} | {y['graded']:.2f} | {x['stop_side']:.3f} | "
                 f"{x['move_side']:.3f} | {x['J']:+.3f} | {x['collision_free']:.3f} |")
    ga, ga2, jb = ([table[c][m][k] for m in models] for c, k in (("A", "graded"), ("A'", "graded"), ("A", "J")))
    L += [f"\nKendall τ-b vs A: A' {SN.kendall_tau_b(ga, ga2):.3f}; B (J) {SN.kendall_tau_b(ga, jb):.3f}. "
          "Candidate C (hierarchical) ranks by collision-free share, then J, then A graded: "
          + " > ".join(sorted(models, key=lambda m: (-round(table['A'][m]['collision_free'], 3), -round(table['A'][m]['J'], 3), -table['A'][m]['graded']))) + ".\n",
          f"\nEpisodes whose credit changes under A': {len(changed)} (all STOP/HOLD; listed in data/alternatives_changed.json).\n",
          "## D1 — instruction-independent policies (synthetic telemetry, all 25 scenarios)\n",
          "| policy | A graded | A strict | A' graded | B STOP-side | B move-side | B J |", "|---|---:|---:|---:|---:|---:|---:|"]
    for pn in pol:
        x, y = syn[(pn, "A")], syn[(pn, "A'")]
        L.append(f"| {pn} | {x['graded']:.2f} | {x['strict']} | {y['graded']:.2f} | {x['stop_side']:.3f} | {x['move_side']:.3f} | {x['J']:+.3f} |")
    L += ["\n## D2 — identical approach (15 km/h, stop 0.5 m before the line) started at 0.0 / 0.5 / 1.0 / 1.5 / 3.0 s\n",
          f"A: {timing['A']}  ·  A': {timing[chr(65) + chr(39)]}\n",
          "\n## D3 — overshoot (m past the line): (overshoot, slow 5 km/h creep credit, fast 25 km/h credit)\n",
          f"A: {over['A']}  ·  A': {over[chr(65) + chr(39)]}\n"]
    out = Path(a.out)
    (out / "scoring_alternatives_raw.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (out / "data" / "alternatives_changed.json").write_text(json.dumps([list(k) for k in changed], indent=0), encoding="utf-8")
    (out / "data" / "alternatives_table.json").write_text(json.dumps({"models": table, "synthetic": {f"{k[0]}|{k[1]}": v for k, v in syn.items()},
                                                                      "timing": timing, "overshoot": over}, indent=1), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
