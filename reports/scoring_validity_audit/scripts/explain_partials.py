"""Add a per-episode explanation to partial_credit_episodes.csv (audit).

    python <audit>/scripts/explain_partials.py --data <audit>/data
"""
from __future__ import annotations

import argparse
import sys
import collections
import csv
import json
from pathlib import Path


def explain(r):
    parts = []
    comp = json.loads(r["components"] or "{}")
    if "movement_without_entry" in comp:
        parts.append("no junction entry: PROCEED no-entry branch (at most 0.30)")
    for k, v in sorted(comp.items()):
        if v < 0.999:
            parts.append(f"{k}={v:.3f}")
    if float(r["latency_factor"]) < 0.999:
        parts.append(f"latency={float(r['latency_factor']):.3f} (τ={r['latency_s']} s, {r['latency_trigger']})")
    if float(r["safety_factor"]) < 0.999:
        parts.append(f"safety={float(r['safety_factor']):.2f} (collision events={r['collision_count']})")
    return "; ".join(parts)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    a = ap.parse_args()
    p = Path(a.data) / "partial_credit_episodes.csv"
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    keep = ["episode_id", "model", "scenario", "seed", "expected_action", "strict", "strict_reason", "credit",
            "action_credit", "latency_factor", "safety_factor", "movement_gate", "engagement_factor",
            "min_front_to_stopline_m", "max_scored_speed_kmh", "entered_junction", "latency_s", "latency_trigger",
            "collision_count", "motion_class", "components"]
    for r in rows:
        r["explanation"] = explain(r)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keep + ["explanation"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    # what limits the partial credits: the smallest component per episode
    lim = collections.Counter()
    for r in rows:
        comp = json.loads(r["components"] or "{}")
        cands = dict(comp, latency=float(r["latency_factor"]), safety=float(r["safety_factor"]))
        lim[min(cands, key=cands.get)] += 1
    print(len(rows), "partial episodes; smallest term:", lim.most_common())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
