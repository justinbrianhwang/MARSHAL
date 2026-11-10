"""Re-run the benchmark Strict + MARSHAL-Graded scorers on the 700 protocol-v2 leaderboard episodes, check that they
reproduce reports/protocol_v2/data/cells_v2.csv, and store every episode's full scorer evidence for the MARSHAL Score.

Read-only over raw logs. usage (from the repository root):
    python -I reports/marshal_score/scripts/extract_observables.py
"""
import csv, json, sys
from pathlib import Path

ROOT = Path.cwd()
W = ROOT / "reports" / "marshal_score"
sys.path.insert(0, str(ROOT / "scripts")); sys.path.insert(0, str(ROOT))
from marshal_bench.criteria import graded_episode_scoring as G  # noqa: E402
from marshal_bench.criteria import strict_episode_scoring as S  # noqa: E402
import collect_protocol_v2 as C  # noqa: E402



def ep_dir(row):
    p = Path(row["episode"])
    if row["model"] == "interfuser":
        return ROOT / "tmp" / "_remeasure_interfuser" / "final_forward" / "interfuser" / p.parts[-2] / p.parts[-1]
    return ROOT / p


def score(ep: Path, row):
    tel = C._json(ep / "strict_telemetry.json") or {}
    rows = tel.get("telemetry")
    res = C._json(ep / "result.json") or {}
    res = res.get("result", res) if isinstance(res, dict) else {}
    if not res:
        rr = C._json(ep.parent / "_results.json") or []
        res = next((r for r in rr if isinstance(r, dict) and Path(str(r.get("episode_dir", ""))).name == ep.name), {})
    meta = C._json(ep / "metadata.json") or {}
    stored = C._json(ep / "strict_scoring.json") or {}
    budget = ((res.get("ground_truth") or {}).get("max_reaction_time_sec")
              or ((meta.get("config") or {}).get("expected_behavior") or {}).get("max_reaction_time_sec") or 3.0)
    kw = dict(scenario=row["scenario"], expected_action=row["expected_action"], max_reaction_time=budget,
              setup_errors=res.get("setup_errors") or (), controller_errors=res.get("controller_errors") or (),
              protocol="v2", decision_times=C._decision_times(ep))
    inner = dict(res or {"scenario": row["scenario"]})
    s = S.score_episode_from_telemetry(inner, rows or [], **kw)
    ids = C._collision_identities(ep, tel)
    g = G.score_episode_from_telemetry(inner, rows or [], **kw, collision_identities=ids)
    if stored.get("verdict") == "INVALID" and s.get("verdict") != "INVALID":
        s = {**s, "verdict": "INVALID"}; g = {**g, "credit": 0.0}
    partners = sorted({str(a) if a is not None else f"type:{t}" for a, t in (ids or [])})
    return {"budget_s": budget, "strict": s, "graded": g, "collision_partners": partners,
            "n_collision_callbacks": len(ids or [])}


def main():
    cells = [c for c in csv.DictReader(open(ROOT / "reports/protocol_v2/data/cells_v2.csv", encoding="utf-8"))
             if not c["model"].endswith("_cap3")]
    assert len(cells) == 700
    out = open(W / "data" / "observables.jsonl", "w", encoding="utf-8")
    mism = 0
    for c in cells:
        ep = ep_dir(c)
        r = score(ep, c)
        ok = r["strict"].get("verdict") == c["strict"] and abs(float(r["graded"].get("credit") or 0) - float(c["credit"])) < 1e-6
        mism += not ok
        out.write(json.dumps({"model": c["model"], "seed": c["seed"], "scenario": c["scenario"],
                              "expected_action": c["expected_action"], "weight": c["weight"],
                              "cells_strict": c["strict"], "cells_credit": c["credit"], "reproduced": ok,
                              "episode_dir": str(ep.relative_to(ROOT)).replace("\\", "/"), **r}, default=str) + "\n")
    out.close()
    print("episodes", len(cells), "reproduction mismatches", mism)


if __name__ == "__main__":
    (W / "data").mkdir(exist_ok=True)
    main()
