"""Stop-line crossing cause analysis for the Protocol v2 leaderboard (audit, read-only).

Run from the workspace root (the directory holding scripts/, marshal_bench/ and tmp/_protocol_v2):

    python <audit>/crossing_analysis/scripts/crossing_causes.py --runs tmp/_protocol_v2 \
        --audit <audit> --out <audit>/crossing_analysis

Inputs: the audit's baseline_manifest.csv (episode directory chosen by the published collector),
episode_decomposition.csv (leaderboard cohort, onset / active_end), scenario_contracts.csv (reaction
budget), and per-episode strict_telemetry.json (+ vlm_debug/vlm_trace.csv for Track-C). Telemetry is only
read. Output: crossing_causes.csv (one row per episode whose signed front-bumper stop-line distance
`stopline_front_m` goes < 0 within the 25 s horizon) and crossing_trajectories.png.

Definitions (all on 0.05 s telemetry rows, sim_time <= 25 s):
  crossing tick      first row with stopline_front_m < 0
  brake request      raw_brake >= B_REQ (controller output, before the actuator); Track-C: only while the
                     VLM decision in force is STOP/HOLD/SLOW (under GO the brake is base speed regulation)
  approach segment   rows after the last row with speed < V_STILL before the crossing (else from t=0)
  required decel     a_req = v^2 / (2 * d_eff), d_eff = stopline_front_m - v * LAG (one-tick actuation lag)
  feasible stop      a_req <= A_MAX (A_MAX measured from the runs: median decel over 0.5 s spans with
                     applied_brake >= 0.9 from >= 15 km/h, no collision; reported in the output)
Relevance (whether the crossing violates the episode's contract):
  binding                    STOP/HOLD crossing at t <= active_end; stale_directive_residue crossing while the
                             STOP is live; rule_hierarchy crossing before t_safe (pinned scorer's function)
  post_window                STOP/HOLD crossing after the directive window (unscored)
  contract_requires_crossing dual_authority_handoff (its hold band lies past the stop line)
  expected_crossing          PROCEED / DETOUR / YIELD crossings otherwise
Mechanism classes (first match wins):
  spawned_past             stopline_front_m < 0 on the first row                         -> scenario geometry
  rolled_without_throttle  no raw_throttle >= 0.05 before the crossing                   -> implementation suspect
  collision_displaced      a collision is recorded by the crossing tick and speed < 1 km/h there (pushed/halted
                           on the line, not driven across)                                -> collision related
  crossed_before_onset     crossing before the directive onset                            -> geometry/timing
  infeasible_at_onset_immediate  at the onset tick, with 0 s reaction (one-tick actuation lag only), a_req > A_MAX:
                           no controller could have stopped once the directive appeared   -> scenario geometry
  tight_onset_margin       stoppable at the onset tick with immediate braking, but not after a T_REACT coast at
                           the onset speed: avoidable only with near-immediate reaction   -> model decision (late
                           reaction); the row is also flagged scenario_tight_margin (scenario-design concern)
  no_post_onset_decision   Track-C only: no VLM query tick in [onset, crossing) — the only decisions in force were
                           made on pre-onset frames (query cadence vs. line distance)     -> geometry/timing
  creep_across             speed < V_CREEP at the crossing and over the preceding 2 s      -> model decision
  stop_decision_not_actuated  Track-C: STOP/HOLD decision active in the approach while the stop was feasible,
                           yet no brake requested                                         -> implementation suspect
  never_braked             no brake request in the approach segment                      -> model decision
  braked_late              at the first approach brake request a_req > A_MAX              -> model decision
  released_brake           feasible at brake onset, brake request later dropped < B_REQ  -> model decision
  control_not_followed     feasible at brake onset, request held with mean >= 0.8         -> implementation suspect
  braked_insufficiently    feasible at brake onset, request held but mean < 0.8          -> model decision
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
from marshal_bench.criteria import strict_episode_scoring as S  # noqa: E402  (pinned scorer, read-only use)

DT, LAG = 0.05, 0.05
B_REQ, V_STILL, V_CREEP, FULL_BRAKE = 0.3, 0.5, 5.0, 0.8
T_REACT = 1.0  # s, assumed coast used only to separate tight_onset_margin from later classes
HORIZON = S.PROTOCOL_V2["horizon_s"]
TOL = S.PROTOCOL_V2.get("stopline_crossed_tolerance_m", 0.3)
VLM = ("glm-4.5v", "qwen2.5-vl", "qwen3-vl")
GROUP = {"spawned_past": "scenario_geometry", "collision_displaced": "collision_related",
         "crossed_before_onset": "geometry_timing", "infeasible_at_onset_immediate": "scenario_geometry",
         "tight_onset_margin": "model_decision",
         "no_post_onset_decision": "geometry_timing", "rolled_without_throttle": "implementation_suspect",
         "stop_decision_not_actuated": "implementation_suspect", "control_not_followed": "implementation_suspect",
         "creep_across": "model_decision", "never_braked": "model_decision", "braked_late": "model_decision",
         "released_brake": "model_decision", "braked_insufficiently": "model_decision"}


def fl(r, k, d=0.0):
    try:
        v = float(r.get(k))
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def load_rows(ep: Path):
    t = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
    rows = t.get("telemetry") if isinstance(t, dict) else t
    return [r for r in rows if float(r["sim_time"]) <= HORIZON + 1e-9]


def load_actions(ep: Path):
    p = ep / "vlm_debug" / "vlm_trace.csv"
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as fh:
        tr = list(csv.DictReader(fh))
    return ({round(float(r["sim_time"]), 2): (r.get("active_action") or "") for r in tr},
            [float(r["sim_time"]) for r in tr if r.get("queried_this_tick") == "True"])


def a_req(v_kmh, d):
    v = v_kmh / 3.6
    if v < 1e-3:
        return 0.0
    return v * v / (2 * max(d - v * LAG, 0.01))


def runs(seq):
    """Run-length 'A@t>B@t' summary of a (t, label) sequence."""
    out, last = [], None
    for t, a in seq:
        if a != last:
            out.append(f"{a or '-'}@{t:.2f}")
            last = a
    return ">".join(out)


def measure_a_max(eps):
    vals = []
    for rows in eps:
        for i in range(len(rows) - 10):
            w = rows[i:i + 11]
            if (all(fl(x, "applied_brake") >= 0.9 for x in w[:-1]) and fl(w[0], "ego_speed_kmh") >= 15
                    and fl(w[-1], "ego_speed_kmh") > 2 and w[0].get("collision_count") == w[-1].get("collision_count")):
                vals.append((fl(w[0], "ego_speed_kmh") - fl(w[-1], "ego_speed_kmh")) / 3.6 / 0.5)
    return statistics.median(vals), len(vals)


def areq_after_reaction(v_kmh, d, t_react):
    rem = d - v_kmh / 3.6 * t_react
    return float("inf") if rem <= 0 else a_req(v_kmh, rem)


def classify(rows, i, acts, a_max, is_vlm, onset=0.0, queries=(), t_react=T_REACT):
    """Mechanism class + evidence for a crossing at row index i."""
    t = lambda k: float(rows[k]["sim_time"])
    v = lambda k: fl(rows[k], "ego_speed_kmh")
    d = lambda k: fl(rows[k], "stopline_front_m")
    rb = lambda k: fl(rows[k], "raw_brake")
    ev = {}
    launch = max((k for k in range(i) if v(k) < V_STILL), default=-1) + 1
    seg = range(launch, i + 1)
    ev["approach_start_s"] = t(launch)
    ev["peak_speed_approach_kmh"] = max(v(k) for k in seg)
    # Track-C: a brake under a GO decision is the base lane-follower's speed regulation, not a stop response
    brakes = [k for k in seg if rb(k) >= B_REQ
              and (not acts or acts.get(round(t(k), 2), "") in ("STOP", "HOLD", "SLOW"))]
    stop_dec = [k for k in seg if acts and acts.get(round(t(k), 2), "") in ("STOP", "HOLD")]
    ev["first_stop_decision_s"] = t(stop_dec[0]) if stop_dec else None
    ev["areq_at_first_stop_decision"] = a_req(v(stop_dec[0]), d(stop_dec[0])) if stop_dec else None
    mism = sum(abs(fl(rows[k], "applied_brake") - rb(k - 1)) > 1e-3 and abs(fl(rows[k], "applied_brake") - rb(k)) > 1e-3
               for k in seg if k > 0)
    ev["ticks_applied_ne_requested_brake"] = mism
    if brakes:
        b = brakes[0]
        ev.update(first_brake_s=t(b), speed_at_first_brake_kmh=v(b), dist_at_first_brake_m=d(b),
                  areq_at_first_brake=a_req(v(b), d(b)),
                  mean_raw_brake_after_onset=statistics.mean(rb(k) for k in range(b, i + 1)))
        rel = next((k for k in range(b, i + 1) if rb(k) < B_REQ), None)
        ev["brake_release_s"] = t(rel) if rel is not None else None
        ev["areq_at_release"] = a_req(v(rel), d(rel)) if rel is not None else None
    if d(0) < 0:
        return "spawned_past", ev
    if max(fl(rows[k], "raw_throttle") for k in range(i + 1)) < 0.05:
        return "rolled_without_throttle", ev
    if int(fl(rows[i], "collision_count")) > int(fl(rows[0], "collision_count")) and v(i) < 1.0:
        return "collision_displaced", ev
    if t(i) < onset - 1e-9:
        return "crossed_before_onset", ev
    ko = next((k for k in range(i + 1) if t(k) >= onset - 1e-9), 0)
    if ko > 0 and d(ko) > 0 and a_req(v(ko), d(ko)) > a_max:
        return "infeasible_at_onset_immediate", ev
    if ko > 0 and d(ko) > 0 and areq_after_reaction(v(ko), d(ko), t_react) > a_max:
        return "tight_onset_margin", ev
    if is_vlm and not any(onset - 1e-9 <= q < t(i) for q in queries):
        return "no_post_onset_decision", ev
    if v(i) < V_CREEP and max(v(k) for k in range(i + 1) if t(k) >= t(i) - 2.0) < V_CREEP:
        return "creep_across", ev
    if not brakes:
        if is_vlm and stop_dec and ev["areq_at_first_stop_decision"] <= a_max:
            return "stop_decision_not_actuated", ev
        return "never_braked", ev
    if ev["areq_at_first_brake"] > a_max:
        return "braked_late", ev
    if ev["brake_release_s"] is not None:
        return "released_brake", ev
    return ("control_not_followed" if ev["mean_raw_brake_after_onset"] >= FULL_BRAKE else "braked_insufficiently"), ev


def relevance(sc, exp, rows, i, onset, active_end):
    tc = float(rows[i]["sim_time"])
    if sc == "dual_authority_handoff":
        return "contract_requires_crossing"
    if exp in ("STOP", "HOLD"):
        return "binding" if tc <= active_end + 1e-9 else "post_window"
    if sc == "stale_directive_residue":
        return "binding" if S._directive_live_row(rows[i]) else "expected_crossing"
    if sc == "rule_hierarchy":
        ts, _ = S.safe_to_proceed_time(rows, sc, onset)
        return "binding" if ts is None or tc < ts - 1e-9 else "expected_crossing"
    return "expected_crossing"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="tmp/_protocol_v2")
    ap.add_argument("--audit", required=True, help="scoring_validity_audit directory")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    audit, out, runs_dir = Path(a.audit), Path(a.out), Path(a.runs)
    out.mkdir(parents=True, exist_ok=True)
    man = {r["episode_id"]: r for r in csv.DictReader((audit / "data/baseline_manifest.csv").open(encoding="utf-8"))
           if r["selected_by_collector"] == "True"}
    dec = [r for r in csv.DictReader((audit / "data/episode_decomposition.csv").open(encoding="utf-8"))
           if r["cohort"] == "leaderboard"]
    budget = {r["scenario"]: float(r["reaction_budget_s"])
              for r in csv.DictReader((audit / "scenario_contracts.csv").open(encoding="utf-8"))}
    eps = {r["episode_id"]: runs_dir / man[r["episode_id"]]["episode_dir"].replace("\\", "/") for r in dec}
    data = {k: load_rows(p) for k, p in eps.items()}
    a_max, n_spans = measure_a_max(data.values())
    print(f"A_MAX (empirical median full-brake decel) = {a_max:.2f} m/s^2 over {n_spans} spans")
    out_rows, traces = [], {}
    for r in dec:
        rows = data[r["episode_id"]]
        i = next((k for k, x in enumerate(rows) if x.get("stopline_front_m") is not None
                  and fl(x, "stopline_front_m", 1.0) < 0), None)
        if i is None:
            continue
        is_vlm = r["model"] in VLM
        acts, queries = load_actions(eps[r["episode_id"]]) if is_vlm else (None, [])
        onset = float(r["onset_s"] or 0.0)
        active_end = float(r["active_end_s"] or HORIZON)
        cls, ev = classify(rows, i, acts, a_max, is_vlm, onset, queries)
        cls_alt = {am: classify(rows, i, acts, am, is_vlm, onset, queries)[0] for am in (6.0, 8.0)}
        cls_alt["t0"] = classify(rows, i, acts, a_max, is_vlm, onset, queries, t_react=0.0)[0]
        tc = float(rows[i]["sim_time"])
        post_q = [q for q in queries if onset - 1e-9 <= q < tc]
        ko = next((k for k, x in enumerate(rows) if float(x["sim_time"]) >= onset - 1e-9), 0)
        on_v, on_d = fl(rows[ko], "ego_speed_kmh"), fl(rows[ko], "stopline_front_m")
        bud = budget.get(r["scenario"], 3.0)
        phase = "before_onset" if tc < onset else ("within_reaction_budget" if tc < onset + bud else "after_budget")
        j = range(i + 1)
        rec = {"episode_id": r["episode_id"], "model": r["model"], "seed": r["seed"], "scenario": r["scenario"],
               "expected_action": r["expected_action"], "strict": r["strict"], "credit": r["credit"],
               "relevance": relevance(r["scenario"], r["expected_action"], rows, i, onset, active_end),
               "cause_class": cls, "group": GROUP[cls], "scenario_tight_margin": cls == "tight_onset_margin",
               "cause_class_amax6": cls_alt[6.0], "cause_class_amax8": cls_alt[8.0],
               "cause_class_treact0": cls_alt["t0"],
               "spawn_front_to_line_m": fl(rows[0], "stopline_front_m"),
               "t_cross_s": tc, "speed_at_cross_kmh": fl(rows[i], "ego_speed_kmh"),
               "raw_throttle_at_cross": fl(rows[i], "raw_throttle"), "raw_brake_at_cross": fl(rows[i], "raw_brake"),
               "onset_s": onset, "reaction_budget_s": bud, "active_end_s": active_end, "phase": phase,
               "speed_at_onset_kmh": on_v, "dist_at_onset_m": on_d,
               "areq_at_onset": a_req(on_v, on_d) if on_d > 0 else None,
               "areq_at_onset_after_reaction": min(areq_after_reaction(on_v, on_d, T_REACT), 999.0) if on_d > 0 else None,
               "min_front_to_line_m": min(fl(x, "stopline_front_m") for x in rows),
               "shallow_crossing_le_tol": min(fl(x, "stopline_front_m") for x in rows) >= -TOL,
               "collision_by_cross": int(fl(rows[i], "collision_count")) > int(fl(rows[0], "collision_count")),
               "max_raw_throttle_pre_cross": max(fl(rows[k], "raw_throttle") for k in j),
               **ev,
               "decision_trace_pre_cross": runs([(float(rows[k]["sim_time"]), acts.get(round(float(rows[k]["sim_time"]), 2), ""))
                                                 for k in j]) if acts else "",
               "decision_at_cross": acts.get(round(tc, 2), "") if acts else "",
               "vlm_queries_after_onset_before_cross": len(post_q) if is_vlm else "",
               "decisions_after_onset_before_cross": "".join((acts.get(round(q, 2)) or "?")[0] for q in post_q) if is_vlm else "",
               "episode_dir": man[r["episode_id"]]["episode_dir"].replace("\\", "/")}
        for k, val in list(rec.items()):
            if isinstance(val, float):
                rec[k] = round(val, 3)
        out_rows.append(rec)
        traces[r["episode_id"]] = rows
    keys = list(dict.fromkeys(k for r in out_rows for k in r))
    with (out / "crossing_causes.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(out_rows)
    # actuation check over every leaderboard Track-C tick: a STOP/HOLD decision in force must reach the actuator
    n_sh = n_sh_bad = 0
    for e, p in eps.items():
        if e.split("/")[0] in VLM:
            with (p / "vlm_debug" / "vlm_trace.csv").open(encoding="utf-8") as fh:
                for x in csv.DictReader(fh):
                    if x["active_action"] in ("STOP", "HOLD"):
                        n_sh += 1
                        n_sh_bad += fl(x, "applied_brake") < 0.5 or fl(x, "applied_throttle") > 0.05
    with (out / "a_max.json").open("w", encoding="utf-8") as fh:
        json.dump({"a_max_mps2": round(a_max, 3), "n_spans": n_spans, "vlm_stop_hold_ticks": n_sh,
                   "vlm_stop_hold_ticks_not_actuated": n_sh_bad}, fh)
    print(Counter(r["relevance"] for r in out_rows))
    tab = defaultdict(Counter)
    for r in out_rows:
        if r["relevance"] == "binding":
            tab[r["model"]][r["cause_class"]] += 1
    for m in sorted(tab):
        print(m, dict(tab[m]))
    plot(out, out_rows, traces)
    return 0


REPS = ["pid/s0/green_stop", "qwen3-vl/s0/green_stop", "qwen3-vl/s0/occluded_officer", "cilrs/s0/unauthorized_go"]


def plot(out, out_rows, traces):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    reps = [r for e in REPS for r in out_rows if r["episode_id"] == e]
    if not reps:
        return
    fig, ax = plt.subplots(3, len(reps), figsize=(4 * len(reps), 7.5), sharex="col", squeeze=False)
    for c, r in enumerate(reps):
        rows = traces[r["episode_id"]]
        t = [float(x["sim_time"]) for x in rows]
        ax[0][c].plot(t, [fl(x, "stopline_front_m") for x in rows], color="#2a6fbb")
        ax[0][c].axhline(0, color="#c0392b", lw=0.8)
        ax[0][c].set_ylim(-10, max(fl(rows[0], "stopline_front_m") + 2, 5))
        ax[0][c].set_title(f"{r['episode_id']}\n{r['cause_class']}", fontsize=9)
        ax[1][c].plot(t, [fl(x, "ego_speed_kmh") for x in rows], color="#444")
        ax[2][c].plot(t, [fl(x, "raw_throttle") for x in rows], color="#27ae60", label="requested throttle")
        ax[2][c].plot(t, [fl(x, "raw_brake") for x in rows], color="#c0392b", label="requested brake")
        for a_ in ax[:, c]:
            a_.axvline(r["t_cross_s"], color="#999", ls="--", lw=0.8)
            a_.axvline(r["onset_s"], color="#e67e22", ls=":", lw=0.8)
            a_.set_xlim(0, min(25, r["t_cross_s"] + 6))
    ax[0][0].set_ylabel("front bumper to stop line (m)")
    ax[1][0].set_ylabel("speed (km/h)")
    ax[2][0].set_ylabel("requested control")
    ax[2][0].legend(fontsize=7)
    for a_ in ax[2]:
        a_.set_xlabel("sim time (s)")
    fig.suptitle("dotted: directive onset   dashed: stop-line crossing", fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "crossing_trajectories.png", dpi=110)


if __name__ == "__main__":
    raise SystemExit(main())
