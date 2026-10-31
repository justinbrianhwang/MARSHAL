"""Counterfactual / boundary / metamorphic tests of the v2 strict and graded scorers on SYNTHETIC telemetry (audit).

    python <audit>/scripts/counterfactual.py --out <audit>/counterfactual_tests.md   # from the workspace root

A recorded oracle episode supplies every non-kinematic column (officer state, light, timing); only the ego
kinematics (speed, route position and the stop-line / officer distances derived from it, junction flag,
collision count) are replaced by a scripted speed profile. These are scorer tests, NOT physically validated
trajectories: nothing here shows a controller can produce them in CARLA.

Every case states its expected ordering BEFORE the score is computed; the report prints observed values and
whether the expectation held. A failed expectation is a finding to judge, not automatically a bug.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import published_v2  # noqa: E402,F401  (pins the published v2 graded scorer; see published_v2.py)
from marshal_bench.criteria import graded_episode_scoring as G  # noqa: E402
from marshal_bench.criteria import strict_episode_scoring as S  # noqa: E402

DT = 0.05
TEMPLATE = "tmp/_protocol_v2/oracle/s0/reference_oracle_{sc}/strict_telemetry.json"


def template(sc):
    return json.loads(Path(TEMPLATE.format(sc=sc)).read_text(encoding="utf-8"))["telemetry"]


def synth(sc, speed_fn, collisions_at=(), junction_front=None):
    """speed_fn(t) -> km/h (>= 0). Route position integrates speed; distances follow the template's spawn geometry."""
    base = template(sc)
    r0 = base[0]
    num = lambda k: (None if r0.get(k) is None else float(r0[k]))
    front0, centre0, off0 = num("stopline_front_m"), num("stopline_forward_m"), num("distance_to_officer_m")
    haz0, ped0 = num("distance_to_hazard_m"), num("distance_to_pedestrian_m")
    # junction polygon: first junction row of the template, in front-bumper coordinates (falls back to the line)
    jf = junction_front
    if jf is None:
        j = next((r for r in base if r.get("in_junction") is True), None)
        jf = float(j["stopline_front_m"]) if (j and j.get("stopline_front_m") is not None) else 0.0
    x, out = 0.0, []
    for r in base:
        t = float(r["sim_time"])
        v = max(0.0, float(speed_fn(t)))
        x += v / 3.6 * DT
        n = copy.deepcopy(r)
        n.update({"ego_speed_kmh": v, "ego_forward_m": x, "ego_lateral_m": 0.0,
                  "ego_x": float(r0["ego_x"]) + x, "ego_y": float(r0["ego_y"]),
                  "collision_count": sum(1 for c in collisions_at if t >= c)})
        if front0 is not None:
            n.update({"stopline_front_m": front0 - x, "stopline_forward_m": centre0 - x,
                      "distance_to_stopline_m": abs(centre0 - x), "in_junction": (front0 - x) <= jf})
        else:
            n["in_junction"] = False
        if off0 is not None:
            n["distance_to_officer_m"] = abs(off0 - x)
        if haz0 is not None and r.get("distance_to_hazard_m") is not None:
            n["distance_to_hazard_m"] = max(0.0, haz0 - x)
        if ped0 is not None and r.get("distance_to_pedestrian_m") is not None:
            n["distance_to_pedestrian_m"] = max(0.0, ped0 - x)
        out.append(n)
    return out


def score(sc, rows, action):
    kw = dict(scenario=sc, expected_action=action, max_reaction_time=3.0, protocol="v2")
    s = S.score_episode_from_telemetry({"scenario": sc}, rows, **kw)
    g = G.score_episode_from_telemetry({"scenario": sc}, rows, **kw)
    return s, g


def ramp_to_stop(v, t0, stop_front, front0, a=2.5):
    """Accelerate at a from t0 to v, cruise, brake at a so the front stops at stop_front (+ before the line)."""
    vm = v / 3.6
    dist = front0 - stop_front  # travel needed
    acc_d = vm * vm / (2 * a)
    if 2 * acc_d > dist:  # triangular profile
        vm = (a * dist) ** 0.5
        acc_d = dist / 2
    t_acc = vm / a
    t_cruise = (dist - 2 * acc_d) / vm if vm > 0 else 0
    t1, t2, t3 = t0 + t_acc, t0 + t_acc + t_cruise, t0 + 2 * t_acc + t_cruise

    def fn(t):
        if t < t0:
            return 0.0
        if t < t1:
            return 3.6 * a * (t - t0)
        if t < t2:
            return 3.6 * vm
        if t < t3:
            return 3.6 * (vm - a * (t - t2))
        return 0.0
    return fn


def run():
    F0 = float(template("green_stop")[0]["stopline_front_m"])
    cases, results = [], []

    def case(group, name, sc, action, fn, expect, **kw):
        s, g = score(sc, synth(sc, fn, **kw), action)
        c = g.get("component_credits") or {}
        results.append({"group": group, "name": name, "strict": s.get("verdict"), "strict_reason": s.get("reason"),
                        "credit": g.get("credit"), "A": g.get("raw_action_credit"), "lat": g.get("latency_factor"),
                        "saf": g.get("safety_factor"), "e": c.get("engagement_gate"),
                        "gate": int(bool(((g.get("evidence") or {}).get("movement_gate") or {}).get("engaged"))),
                        "expect": expect})

    gs = "green_stop"
    # 1. already stopped, STOP instructed (never moves): eligibility gate
    case("eligibility", "park at spawn (0 km/h throughout)", gs, "STOP", lambda t: 0.0, "credit 0 by movement gate (spec)")
    # 3. genuine approach then timely safe stop at various clearances
    for f_stop in (6.0, 4.0, 2.0, 1.0, 0.5):
        case("stop clearance", f"approach 15 km/h from 0.2 s, stop front {f_stop:+.1f} m", gs, "STOP",
             ramp_to_stop(15, 0.2, f_stop, F0), "non-increasing as clearance shrinks below 1.0 m")
    # STOP overshoot at low vs high speed
    for over in (0.0, 0.1, 0.5, 2.0, 10.0, 50.0):
        case("overshoot slow", f"creep 5 km/h, stop {over} m past the line", gs, "STOP",
             ramp_to_stop(5, 0.2, -over, F0, a=1.0), "non-increasing with overshoot")
    for over in (0.0, 0.1, 0.5, 2.0, 10.0, 50.0):
        case("overshoot fast", f"25 km/h, stop {over} m past the line", gs, "STOP",
             ramp_to_stop(25, 0.2, -over, F0, a=4.0), "≤ the slow case at the same overshoot")
    case("drive-through", "constant 25 km/h through the junction", gs, "STOP",
         lambda t: min(25.0, 9 * t), "0")
    # 4/5. low-speed approach that never reaches 5 km/h; speed threshold boundary
    for vmax in (3.0, 4.9, 5.1, 8.0):
        case("speed boundary", f"approach peak {vmax} km/h, stop front 2.0 m", gs, "STOP",
             ramp_to_stop(vmax, 0.2, 2.0, F0, a=0.5), "strict FAIL below 5 km/h (movement gate); credit should not jump discontinuously far beyond what the behaviour difference warrants")
    # progress boundary: reach 6 km/h briefly but travel < 1 m vs > 1 m
    for dist in (0.8, 1.2):
        case("progress boundary", f"pulse to 6 km/h, travel ≈{dist} m", gs, "STOP",
             ramp_to_stop(6, 0.2, F0 - dist, F0, a=4.0), "gate changes at 1 m")
    # 9. approach timing relative to onset (1.0 s): same physical approach, earlier/later start
    for t0 in (0.0, 0.5, 1.0, 1.5, 3.0):
        case("approach timing", f"approach 15 km/h starting {t0} s, stop front 0.5 m (non-strict)", gs, "STOP",
             ramp_to_stop(15, t0, 0.5, F0), "equal (onset-relative timing of an otherwise identical approach should not change engagement)")
    # 6. unnecessary acceleration after the directive ends (release follow-up only)
    stop_then_go = ramp_to_stop(15, 0.2, 2.0, F0)
    case("post-directive", "stop at 2 m, then accelerate at 14 s", gs, "STOP",
         lambda t: stop_then_go(t) if t < 14 else min(20, 5 * (t - 14)), "same credit as stopping (after the directive end does not count)")
    case("post-directive", "stop at 2 m, hold to 25 s", gs, "STOP", stop_then_go, "reference")
    # collisions: more collision events should not raise credit
    for n in (0, 1, 3, 6):
        case("collisions", f"stop at 2 m with {n} collision events", gs, "STOP", stop_then_go,
             "non-increasing in collision count", collisions_at=tuple(5 + i * 0.5 for i in range(n)))
    # latency: braking later
    for t0 in (0.2, 2.0, 4.0, 6.0):
        case("latency", f"cruise 8 km/h from 0.2 s, brake to stop at {t0 + 1:.1f} s… (start {t0})", gs, "STOP",
             (lambda t0: (lambda t: 0.0 if t < 0.2 else (8.0 if t < t0 + 1.0 else max(0.0, 8.0 - 20 * (t - t0 - 1.0)))))(t0),
             "non-increasing as the stop comes later")
    # instruction-independent policies on the PROCEED template
    rp = "red_proceed"
    case("policy", "always-stop (never moves) on red_proceed", rp, "PROCEED", lambda t: 0.0, "0")
    case("policy", "always-go from t=0 (ignores the red light) on red_proceed", rp, "PROCEED",
         lambda t: min(20.0, 9 * t), "PASS (indistinguishable from obeying the GO — identifiability limit)")
    for d in (0.0, 1.0, 2.9, 3.1, 5.0, 8.0):
        case("proceed start", f"start {d} s after the GO onset (1.0 s)", rp, "PROCEED",
             (lambda d: (lambda t: 0.0 if t < 1.0 + d else min(20.0, 9 * (t - 1.0 - d))))(d),
             "non-increasing with start delay; strict PASS only ≤ 3 s")
    # 8. instruction-independent policies over all 25 scenarios (suite score with the published weights)
    sys.path.insert(0, "scripts")
    import _run_vlm_test as vlm
    W = G.SCENARIO_AUTHORITY_WEIGHTS
    pols = {"park at spawn": lambda t: 0.0,
            "lurch: 6 km/h for 0.72 s (≈1.2 m) at t=0, then stopped": lambda t: 6.0 if t < 0.72 else 0.0,
            "lurch, plausible dynamics: +2.5 m/s² to 8 km/h, then −4 m/s² to rest (≈1.6 m), stopped":
                lambda t: 9.0 * t if t < 0.889 else max(0.0, 8.0 - 14.4 * (t - 0.889)),
            "constant 20 km/h (ignores every directive)": lambda t: min(20.0, 9 * t)}
    suite = []
    for pname, fn in pols.items():
        tot, n_pass, cells = 0.0, 0, []
        for sc in vlm.SCENARIO_ORDER:
            act = vlm.SCENARIOS[sc]["expect"]
            s_, g_ = score(sc, synth(sc, fn), act)
            c = float(g_.get("credit") or 0.0)
            tot += W[sc] * c
            n_pass += s_.get("verdict") == "PASS"
            cells.append((sc, act, s_.get("verdict"), round(c, 4)))
        suite.append({"policy": pname, "graded": round(100 * tot / sum(W.values()), 2), "strict": n_pass,
                      "cells": cells})
    return results, suite


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    res, suite = run()
    lines = ["# Counterfactual and boundary tests (generated by scripts/counterfactual.py)\n",
             "Synthetic telemetry: a recorded oracle episode (`green_stop` / `red_proceed`, seed 0) supplies every "
             "non-kinematic column; ego speed, route position, stop-line/officer distances, junction flag and collision "
             "count are replaced by the scripted profile. Scorer: pinned v2 (`protocol=\"v2\"`). These test the scorer's "
             "logic only; they are not evidence that a controller can produce the trajectory.\n"]
    grp = None
    for r in res:
        if r["group"] != grp:
            grp = r["group"]
            lines += [f"\n## {grp}\n", f"Expected: {r['expect']}\n",
                      "| case | strict | credit | action | latency | safety | e | gate | strict reason |",
                      "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        fmt = lambda v: "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))
        lines.append(f"| {r['name']} | {r['strict']} | {fmt(r['credit'])} | {fmt(r['A'])} | {fmt(r['lat'])} | "
                     f"{fmt(r['saf'])} | {fmt(r['e'])} | {r['gate']} | {(r['strict_reason'] or '')[:90]} |")
    lines += ["\n## Instruction-independent policies, all 25 scenarios (synthetic, published weights)\n",
              "Expected: every instruction-independent policy scores at or below the light-only baseline (7.7).\n",
              "| policy | MARSHAL-Graded | strict PASS /25 | passed scenarios | scenarios with 0 < credit < 1 |",
              "|---|---:|---:|---|---|"]
    for p in suite:
        lines.append(f"| {p['policy']} | {p['graded']} | {p['strict']} | "
                     f"{', '.join(c[0] for c in p['cells'] if c[2] == 'PASS') or '—'} | "
                     f"{', '.join(f'{c[0]} ({c[3]})' for c in p['cells'] if 0 < c[3] < 1) or '—'} |")
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if a.json:
        Path(a.json).write_text(json.dumps({"cases": res, "suite": suite}, indent=1), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
