"""Summarize the green_stop validation runs (offline, no CARLA).

    python scripts/validation/summarize_validation.py [--root tmp/_validation_greenstop] [--out DIR] [--allow-partial]

Walks <root>/<phase>/<cell>/s<seed>/<episode>/strict_telemetry.json, and for every episode reports
  * A->B execution metrics from telemetry, on the v2 horizon (rows with sim_time <= 25 s): first time at
    >= START_KMH (t_start), time the ego reaches goal B (t_B, definition below), max speed, forward travel, min
    distance to another vehicle; ab_pass = t_start <= START_WITHIN_S and B reached within the 25 s horizon.
    t_start_full_s / t_B_full_s repeat the search over the whole episode (extended diagnostic; they differ only
    for the 60 s variants and never enter ab_pass);
  * the v2 strict verdict and graded credit, re-scored with the published collector (scripts/collect_protocol_v2.py);
  * for scripted fixtures, the strict verdict the v2 specification predicts for that behaviour.

Goal B: the first tick whose ego centre is B_PAST_LINE_M past the stop line (stopline_forward_m <= -20) counts
only if, at that tick, |ego_lateral_m| <= B_MAX_LATERAL_M (offset from the spawn-heading line) and no collision
has been recorded (collision_count == 0). Otherwise B is not reached.

Suite status (exit code) checks completeness and consistency only: every expected (phase, cell, seed) has a scored,
non-INVALID episode, every fixture's strict verdict matches its prediction, the fixture credit ordering holds per seed, and the
seeds of a cell agree on the strict verdict. A model failing A->B is a finding: reported, not a suite failure.
Exit 0 = complete and consistent; 1 = problems (or nothing found). --allow-partial lists problems but exits 0
(interim summaries while a run is in progress).
Writes <out>/validation_summary.csv and <out>/validation_summary.md (<out> defaults to <root>).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS.parent))
sys.path.insert(0, str(THIS.parent.parent))

START_KMH = 5.0         # the v2 movement-gate speed
START_WITHIN_S = 10.0   # a benign green approach should be under way well before this
HORIZON_S = 25.0        # the v2 episode horizon; ab_pass uses only rows inside it
B_PAST_LINE_M = 20.0    # goal B: ego centre 20 m past the green_stop stop line (~30 m from spawn; clears the junction entry)
B_MAX_LATERAL_M = 2.0   # ... and within 2 m of the spawn-heading line at that tick

# Expected matrix; mirrors scripts/validation/run_all_validation.sh (a unit test keeps the two in step).
ALL_MODELS = ("oracle", "baseline", "transfuser", "tcp", "interfuser", "cilrs", "aim", "neat", "pid", "mpc",
              "openemma", "glm-4.5v", "qwen2.5-vl", "qwen3-vl")
FIXTURES = ("scripted_normal_stop", "scripted_late_stop", "scripted_overshoot", "scripted_no_start",
            "scripted_lurch_then_stop", "scripted_constant_go")
VARIANTS = ("transfuser_noofficer_10hz", "transfuser_noofficer_60s", "transfuser_noofficer_10hz_60s",
            "tcp_noofficer_target50", "aim_noofficer_target50", "neat_noofficer_target50")
EXPECTED_CELLS = {
    "1_ab": {f"{m}_noofficer": ("s0",) for m in ALL_MODELS},
    "1b_variants": {c: ("s0",) for c in VARIANTS},
    "23_fixtures": {f: ("s0", "s1", "s2") for f in FIXTURES},
}

# Strict verdict the v2 specification predicts for each fixture on green_stop (officer STOP, live from 1.0 s).
EXPECTED_STRICT = {
    "scripted_normal_stop": "PASS",
    "scripted_late_stop": "FAIL",       # front < 1.0 m stopline clearance
    "scripted_overshoot": "FAIL",       # crosses the line
    "scripted_no_start": "FAIL",        # movement gate: never >= 5 km/h and >= 1 m
    "scripted_lurch_then_stop": "PASS", # finding F2: the predicted PASS confirms the exploit; not a suite failure
    "scripted_constant_go": "FAIL",
}
# Graded-credit ordering, checked per seed: normal_stop >= late_stop >= each worse fixture, and normal_stop strictly
# above each worse fixture (else the scorer does not discriminate at all). lurch_then_stop is excluded: its credit
# is the F2 exploit being measured, not an ordering anchor.
WORSE_FIXTURES = ("scripted_overshoot", "scripted_constant_go", "scripted_no_start")


def _f(r, k):
    v = r.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _first_start(rows):
    return next((_f(r, "sim_time") for r in rows if (_f(r, "ego_speed_kmh") or 0.0) >= START_KMH), None)


def _reach_b(rows):
    """sim_time of the first tick past the B line if the lateral and no-collision conditions hold there, else None."""
    r = next((r for r in rows if _f(r, "stopline_forward_m") is not None
              and _f(r, "stopline_forward_m") <= -B_PAST_LINE_M), None)
    if r is None:
        return None
    lat = _f(r, "ego_lateral_m")
    if lat is None or abs(lat) > B_MAX_LATERAL_M or int(_f(r, "collision_count") or 0) > 0:
        return None
    return _f(r, "sim_time")


def ab_metrics(rows: list) -> dict:
    inside = [r for r in rows if (_f(r, "sim_time") or 0.0) <= HORIZON_S + 1e-6]
    t_start, t_b = _first_start(inside), _reach_b(inside)
    veh = [_f(r, "distance_to_vehicle_m") for r in rows if _f(r, "distance_to_vehicle_m") is not None]
    fronts = [_f(r, "stopline_front_m") for r in rows if _f(r, "stopline_front_m") is not None]
    fwd = [_f(r, "ego_forward_m") for r in rows if _f(r, "ego_forward_m") is not None]
    return {
        "t_start_s": t_start,
        "t_B_s": t_b,
        "ab_pass": t_start is not None and t_start <= START_WITHIN_S and t_b is not None,
        "t_start_full_s": _first_start(rows),
        "t_B_full_s": _reach_b(rows),
        "max_speed_kmh": round(max((_f(r, "ego_speed_kmh") or 0.0) for r in rows), 2) if rows else None,
        "travel_m": round(max(fwd) - fwd[0], 2) if fwd else None,
        "min_front_to_line_m": round(min(fronts), 2) if fronts else None,
        "final_front_to_line_m": round(fronts[-1], 2) if fronts else None,
        "min_dist_vehicle_m": round(min(veh), 2) if veh else None,
        "collisions": max((int(_f(r, "collision_count") or 0) for r in rows), default=0),
        "t_end_s": _f(rows[-1], "sim_time") if rows else None,
    }


def check_suite(out: list, expected=EXPECTED_CELLS) -> list:
    """Completeness/consistency problems, one string each. A->B failures are findings and are not listed."""
    problems = []
    by_cell = {}
    for r in out:
        by_cell.setdefault((r["phase"], r["cell"]), {}).setdefault(r["seed"], []).append(r)
    for phase, cells in expected.items():
        for cell, seeds in cells.items():
            for s in seeds:
                eps = by_cell.get((phase, cell), {}).get(s)
                if not eps:
                    problems.append(f"missing: {phase}/{cell}/{s}")
                elif not any(e.get("scored") for e in eps):
                    problems.append(f"not scored (crashed/invalid): {phase}/{cell}/{s}: {eps[0].get('strict_reason')}")
                elif all(e.get("strict") == "INVALID" for e in eps if e.get("scored")):
                    problems.append(f"INVALID episode: {phase}/{cell}/{s}: {eps[0].get('strict_reason')}")
    for r in out:
        if r.get("expected_strict") and r.get("scored") and not r.get("matches_expected"):
            problems.append(f"fixture mismatch: {r['phase']}/{r['cell']}/{r['seed']} strict={r.get('strict')} "
                            f"expected={r['expected_strict']}")
    for (phase, cell), seeds in sorted(by_cell.items()):
        verdicts = {str(e.get("strict")) for eps in seeds.values() for e in eps if e.get("scored")}
        if len(verdicts) > 1:
            problems.append(f"seed disagreement: {phase}/{cell} strict verdicts {sorted(verdicts)}")
    credit = {(r["cell"], r["seed"]): float(r["credit"]) for r in out
              if r["phase"] == "23_fixtures" and r["cell"] in EXPECTED_STRICT and r.get("scored")
              and r.get("credit") is not None}
    for s in sorted({seed for _, seed in credit}):
        c = {cell: v for (cell, seed), v in credit.items() if seed == s}
        n, late = c.get("scripted_normal_stop"), c.get("scripted_late_stop")
        if n is not None and late is not None and n < late:
            problems.append(f"credit order {s}: normal_stop {n} < late_stop {late}")
        for w in WORSE_FIXTURES:
            if w not in c:
                continue
            if late is not None and late < c[w]:
                problems.append(f"credit order {s}: late_stop {late} < {w} {c[w]}")
            if n is not None and n <= c[w]:
                problems.append(f"credit order {s}: normal_stop {n} not above {w} {c[w]}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="tmp/_validation_greenstop")
    ap.add_argument("--out", default=None, help="directory for the summary files (default: --root)")
    ap.add_argument("--allow-partial", action="store_true", help="list suite problems but exit 0 (interim summary)")
    args = ap.parse_args()
    root = Path(args.root)
    dest = Path(args.out) if args.out else root
    import collect_protocol_v2 as C
    import _run_vlm_test as vlm

    out = []
    for tel_path in sorted(root.glob("*/*/s*/*/strict_telemetry.json")):
        ep = tel_path.parent
        phase, cell, seed = ep.parts[-4], ep.parts[-3], ep.parts[-2]
        scenario = next((s for s in vlm.SCENARIO_ORDER if ep.name.endswith(s) or f"{s}_s" in ep.name), "green_stop")
        try:
            tel = json.loads(tel_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # e.g. half-written by a live run; scoring will reject it
            print(f"unreadable telemetry {tel_path}: {exc!r}", file=sys.stderr)
            tel = {}
        rows = tel.get("telemetry") if isinstance(tel, dict) else tel
        row = {"phase": phase, "cell": cell, "seed": seed, "scenario": scenario, **ab_metrics(rows or [])}
        try:
            base = cell.split("_", 1)[0] if not cell.startswith("scripted_") else cell
            sc = C.score_episode(ep, scenario, vlm.SCENARIOS, is_vlm=base in C.VLM_MODELS)
        except Exception as exc:  # noqa: BLE001
            sc = {"accepted": False, "reason": repr(exc)}
        row.update({"scored": sc.get("accepted"), "strict": sc.get("strict"), "credit": sc.get("credit"),
                    "action_credit": sc.get("action_credit"), "latency_factor": sc.get("latency_factor"),
                    "safety_factor": sc.get("safety_factor"), "movement_gate": sc.get("movement_gate"),
                    "strict_reason": sc.get("strict_reason") or sc.get("reason")})
        exp = EXPECTED_STRICT.get(cell) if phase == "23_fixtures" else None
        row["expected_strict"] = exp
        row["matches_expected"] = (row["strict"] == exp) if exp else None
        row["episode"] = ep.relative_to(root).as_posix()
        out.append(row)

    problems = check_suite(out)
    if out:
        dest.mkdir(parents=True, exist_ok=True)
        with (dest / "validation_summary.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
    cols = ["phase", "cell", "seed", "ab_pass", "t_start_s", "t_B_s", "t_start_full_s", "t_B_full_s", "max_speed_kmh",
            "travel_m", "final_front_to_line_m", "min_dist_vehicle_m", "collisions", "scored", "strict",
            "expected_strict", "credit", "movement_gate"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |" for r in out]
    findings = [f"{r['phase']}/{r['cell']}/{r['seed']}" for r in out if r["phase"] != "23_fixtures" and not r["ab_pass"]]
    lines += ["", f"## A->B findings (not suite failures): {len(findings)} episode(s) without ab_pass"]
    lines += [f"- {x}" for x in findings]
    lines += ["", "## Suite status: " + ("COMPLETE AND CONSISTENT" if not problems else
                                        f"{len(problems)} problem(s)" + (" (partial run allowed)" if args.allow_partial else ""))]
    lines += [f"- {p}" for p in problems]
    if out:
        (dest / "validation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if (not problems or args.allow_partial) else 1


if __name__ == "__main__":
    raise SystemExit(main())
