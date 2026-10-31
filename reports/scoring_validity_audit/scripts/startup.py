"""Startup / motion diagnostics per model x scenario x run from episode_decomposition.csv (audit).

    python <audit>/scripts/startup.py --data <audit>/data       # writes <audit>/data/startup_diagnostics.csv + tables
"""
from __future__ import annotations

import argparse
import sys
import collections
import csv
from pathlib import Path

VLM = ("glm-4.5v", "qwen2.5-vl", "qwen3-vl")


def cause(r):
    mc = r["motion_class"]
    if not mc.startswith("no_meaningful_motion"):
        return ""
    if r["model"] in VLM:
        return "model output: no GO token in any query" if r["vlm_n_go"] == "0" else f"GO issued {r['vlm_n_go']}x"
    if mc.endswith("no_throttle_requested"):
        return "model output: zero throttle requested"
    if mc.endswith("brief_throttle_pulses_then_brake"):
        return "model output: throttle pulses <= 0.2 s each, then full brake"
    return mc.split(":", 1)[1]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    a = ap.parse_args()
    d = Path(a.data)
    with (d / "episode_decomposition.csv").open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["cohort"] == "leaderboard"]
    keep = ["episode_id", "model", "seed", "scenario", "expected_action", "strict", "credit", "movement_gate",
            "motion_class", "all_max_v", "all_progress", "all_net_fwd", "win_max_v", "win_progress", "all_t_v1",
            "all_t_v5", "max_raw_throttle", "max_applied_throttle", "max_contiguous_drive_s", "frac_raw_brake_ge05",
            "ticks_raw_ne_applied", "frac_applied_eq_prev_raw", "hand_brake_ticks", "gears", "collision_count",
            "vlm_queries", "vlm_query_errors", "vlm_n_go", "vlm_n_stop_hold", "vlm_n_slow", "vlm_actions"]
    for r in rows:
        r["cause"] = cause(r)
    with (d / "startup_diagnostics.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keep + ["cause"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    grp = lambda r: "STOP/HOLD" if r["expected_action"] in ("STOP", "HOLD") else "move-required"
    c = collections.Counter((r["model"], grp(r), r["cause"]) for r in rows if r["cause"])
    tot = collections.Counter((r["model"], grp(r)) for r in rows)
    models = list(dict.fromkeys(r["model"] for r in rows))
    lines = ["| model | episodes STOP/HOLD | no meaningful motion (STOP/HOLD) | episodes move-required | no meaningful motion (move-required) | cause |",
             "|---|---:|---:|---:|---:|---|"]
    for m in models:
        causes = sorted({k[2] for k in c if k[0] == m})
        n_s = sum(v for k, v in c.items() if k[0] == m and k[1] == "STOP/HOLD")
        n_m = sum(v for k, v in c.items() if k[0] == m and k[1] == "move-required")
        lines.append(f"| {m} | {tot[(m, 'STOP/HOLD')]} | {n_s} | {tot[(m, 'move-required')]} | {n_m} | "
                     f"{'; '.join(causes) or '—'} |")
    # scenarios where a model never moves in EVERY run vs never moves in a matched no-authority control: none exist
    by_sc = collections.Counter((r["scenario"], r["expected_action"]) for r in rows if r["cause"])
    lines += ["", "| scenario | expected | episodes with no meaningful motion (of 50) |", "|---|---|---:|"]
    lines += [f"| {sc} | {e} | {n} |" for (sc, e), n in by_sc.most_common()]
    (d / "startup_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
