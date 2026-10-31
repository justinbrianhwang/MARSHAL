"""v2.1-candidate graded credit on recorded telemetry (experimental; strict verdicts unchanged).

    python <audit>/credit_v21/scripts/credit_v21.py          # from the workspace root

The workspace root holds marshal_bench/, scripts/ and tmp/_protocol_v2 (the published scorers and raw runs). The
green_stop validation episodes are read from tmp/_validation_greenstop_v2 under the audit repository root (resolved
relative to this file). Nothing official is modified: every fix is a patched copy of a published function, applied
only inside `scored()`.

Fixes (each switchable, for attribution):
  F1  engagement approach window anchored at the first motion after onset (the audit's A', reused from alternatives.py)
  F6  STOP/HOLD latency: an ego at rest at onset is timed from onset to its first response AFTER its first post-onset
      motion (same anchoring as F1); an ego that never moves after onset keeps latency 0
  F3  safety factor on distinct contacts (same identity, events <= 1 s apart merged) instead of sensor callbacks;
      identity = (actor id where logged, type id): two events match when their types match and their actor ids,
      where both are known, match
  F7  YIELD slowdown measured on every post-onset row (the strict window) instead of the ambulance-active window
  F2  approach-evidence gate on STOP/HOLD credit: E = 1[v_peak >= V_STAR or progress >= D_STAR] on the rows with
      t <= min(directive end, 25 s) (`approach()`); dual_authority_handoff keeps its own approach requirement and is exempt

Outputs (credit_v21/data/): episodes_v21.csv, leaderboard_v21.csv, validation_v21.csv, f2_candidates_passes.csv,
f2_candidates_summary.csv, f2_null_policies.csv, f2_threshold_window.csv, f2_model_level.csv,
f2_null_ceiling_per_station.csv, collision_logs.csv, f3_merge_gap.csv, fix_attribution.csv.
"""
from __future__ import annotations

import collections
import contextlib
import copy
import csv
import inspect
import json
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data"
AUD = HERE.parents[1]                       # scoring_validity_audit/
VAL = HERE.parents[3] / "tmp" / "_validation_greenstop_v2"
RUNS = Path("tmp/_protocol_v2")
sys.path[:0] = [".", "scripts", str(AUD / "scripts")]
import sensitivity as SN  # noqa: E402  (capture(), kendall_tau_b: the audit's loaders)
import alternatives as ALT  # noqa: E402  (A' engagement patch)
import counterfactual as CF  # noqa: E402  (synthetic telemetry)

C, S, G, vlm = SN.C, SN.S, SN.G, SN.vlm
W = G.SCENARIO_AUTHORITY_WEIGHTS
MOVE = ("PROCEED", "DETOUR", "YIELD")
PRIV, REF = {"oracle"}, {"baseline"}
V_STAR, D_STAR = 9.0, 2.0                   # F2 gate (km/h, m)
CONTACT_GAP_S = 1.0                         # F3 merge gap
ALL_FIXES = ("F1", "F6", "F3", "F7", "F2")
EPS = 1e-6


# ------------------------------------------------------------------------------------------------ patched copies
def _patch_source(fn, old, new, name):
    src = textwrap.dedent(inspect.getsource(fn))
    assert src.count(old) == 1, f"published {fn.__name__} changed; patch no longer applies"
    src = src.replace(old, new).replace(f"def {fn.__name__}(", f"def {name}(", 1)
    ns = dict(vars(G))
    exec(compile(src, f"<{name}>", "exec"), ns)
    return ns[name]


_LAT_OLD = (
    '        if at_onset is not None and at_onset <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n'
    '            latency_s = 0.0\n'
    '            trigger = "already_below_stop_speed"\n'
    '        else:\n'
    '            low_t = _first_low_speed_time(rows, onset, GRADED_THRESHOLDS["stop_speed_kmh"])\n'
    '            decel_t = _first_decel_time(rows, onset)\n')
_LAT_NEW = (
    '        _rest = at_onset is not None and at_onset <= GRADED_THRESHOLDS["stop_speed_kmh"]\n'
    '        _t_move = next((float(r["sim_time"]) for r in rows if float(r["sim_time"]) >= onset\n'
    '                        and float(r["ego_speed_kmh"]) > GRADED_THRESHOLDS["stop_speed_kmh"]), None)\n'
    '        if _rest and _t_move is None:\n'
    '            latency_s = 0.0\n'
    '            trigger = "already_below_stop_speed"\n'
    '        else:\n'
    '            _from = _t_move if _rest else onset\n'
    '            low_t = _first_low_speed_time(rows, _from, GRADED_THRESHOLDS["stop_speed_kmh"])\n'
    '            decel_t = _first_decel_time(rows, _from)\n')
latency_f6 = _patch_source(G._latency_factor, _LAT_OLD, _LAT_NEW, "_latency_factor_f6")
_orig_yield = G._score_yield


def yield_f7(rows, onset, active_end):
    """F7: the slowdown minimum over every post-onset row (strict accepts the slowdown at any time after onset)."""
    return _orig_yield(rows, onset, float(rows[-1]["sim_time"]))


def same_identity(a, b):
    """a, b: (actor id or None, type id), or None when no event log exists (synthetic telemetry)."""
    if a is None or b is None:
        return a is None and b is None
    return a[1] == b[1] and (a[0] is None or b[0] is None or a[0] == b[0])


def distinct_contacts(rows, ids, gap=None):
    """F3: distinct contacts from the per-tick cumulative callback count. ids[k] = (actor id or None, type id) of
    counted event k. A new contact starts on an identity change or a gap > `gap` (default CONTACT_GAP_S)."""
    gap = CONTACT_GAP_S if gap is None else gap
    n, prev, last_t, last_id = 0, 0, None, None
    for r in rows:
        c, t = int(float(r.get("collision_count") or 0)), float(r["sim_time"])
        for k in range(prev, c):
            ident = ids[k] if k < len(ids) else None
            if last_t is None or not same_identity(ident, last_id) or t - last_t > gap:
                n += 1
            last_t, last_id = t, ident
        prev = max(prev, c)
    return n


def band(n):
    return 1.0 if n == 0 else 0.25 if n == 1 else 0.10 if n <= 5 else 0.0


def safety_f3(ids, gap=None):
    def _safety(rows):
        n = distinct_contacts(rows, ids, gap)
        f = band(n)
        return f, {"collision_count": n, "severity_signal": "distinct_contacts"}
    return _safety


@contextlib.contextmanager
def patched(fixes, ids, gap=None):
    saved = (G._stop_hold_engagement_factor, G._latency_factor, G._safety_factor, G._score_yield)
    if "F1" in fixes:
        G._stop_hold_engagement_factor = ALT._engagement_motion_anchored
    if "F6" in fixes:
        G._latency_factor = latency_f6
    if "F3" in fixes:
        G._safety_factor = safety_f3(ids, gap)
    if "F7" in fixes:
        G._score_yield = yield_f7
    try:
        yield
    finally:
        G._stop_hold_engagement_factor, G._latency_factor, G._safety_factor, G._score_yield = saved


def f2_applies(kw):
    return kw["expected_action"] in ("STOP", "HOLD") and G._approach_requirement_for(kw["scenario"]) is None


def approach(rows, active_end):
    """F2 gate inputs: peak ego speed and forward progress (max - min ego_forward_m) over t <= min(active_end, 25 s).
    The single source for the gate, the candidate tables and the threshold window; motion after the directive ends
    never counts."""
    end = min(float(active_end), 25.0) + 1e-9
    rr = [r for r in rows if float(r["sim_time"]) <= end]
    v = [x for x in (G._finite_float(r.get("ego_speed_kmh")) for r in rr) if x is not None]
    fwd = [x for x in (G._finite_float(r.get("ego_forward_m")) for r in rr) if x is not None]
    return {"v_peak": max(v, default=0.0), "progress": (max(fwd) - min(fwd)) if fwd else 0.0}


def _approach_of(rows, g):
    return approach(rows, ((g.get("evidence") or {}).get("active_window") or {}).get("active_end_s"))


def gate_a3(f, v=V_STAR, d=D_STAR):
    return f["v_peak"] >= v or f["progress"] >= d


def scored(res, rows, kw, ids=(), fixes=ALL_FIXES, forced_invalid=False, gap=None):
    """Graded verdict with the selected fixes; returns (credit, components, graded verdict)."""
    with patched(fixes, list(ids), gap):
        g = G.score_episode_from_telemetry(res, rows, **kw)
    ev = g.get("evidence") or {}
    mg = ev.get("movement_gate") or {}
    credit = 0.0 if forced_invalid else float(g.get("credit") or 0.0)
    gate = 1
    if "F2" in fixes and f2_applies(kw) and not g.get("invalid"):
        gate = int(gate_a3(_approach_of(rows, g)))
        credit *= gate
    comp = {"A": g.get("raw_action_credit"), "F_lat": g.get("latency_factor"), "F_safe": g.get("safety_factor"),
            "G_move": int(bool(mg.get("engaged"))), "e": (g.get("component_credits") or {}).get("engagement_gate"),
            "E_app": gate, "n_collision": (ev.get("safety") or {}).get("collision_count"),
            "latency_s": (ev.get("latency") or {}).get("latency_s"), "latency_trigger": (ev.get("latency") or {}).get("trigger")}
    return round(min(max(credit, 0.0), 1.0), 6), comp, g


# ------------------------------------------------------------------------------------------------ episode inputs
def event_ids(ep: Path):
    """Identity (actor id or None, type id) of every counted collision event, in callback order. The type comes from
    events.json (every event); the actor id from metadata.collisions (first 20 events only)."""
    types = [e["payload"].get("other_actor") for e in json.loads((ep / "events.json").read_text(encoding="utf-8"))
             if e.get("name") == "collision"]
    md = (json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8")).get("metadata") or {}).get("collisions") or []
    for i, m in enumerate(md):
        assert i < len(types) and types[i] == m["other_type_id"], (ep, i)  # same order and type in both logs
    return [(md[i]["other_actor_id"] if i < len(md) else None, t) for i, t in enumerate(types)]


def capture_validation():
    cap, out = {}, []
    orig = S.score_episode_from_telemetry

    def grab(res, rows, **kw):
        cap["a"] = (copy.deepcopy(res), list(rows), dict(kw))
        return orig(res, rows, **kw)
    S.score_episode_from_telemetry = grab
    try:
        for ph in ("1_ab", "1b_variants", "23_fixtures"):
            for cell in sorted(p for p in (VAL / ph).iterdir() if p.is_dir()):
                for sd in sorted(p for p in cell.iterdir() if p.is_dir()):
                    ep = C.find_episode(sd, "green_stop")
                    cap.clear()
                    r = C.score_episode(ep, "green_stop", vlm.SCENARIOS, is_vlm=False)
                    res, rows, kw = cap["a"]
                    out.append({"phase": ph, "model": cell.name, "seed": int(sd.name[1:]), "scenario": "green_stop",
                                "res": res, "rows": rows, "kw": kw, "forced_invalid": r["strict"] == "INVALID", "ep": ep})
    finally:
        S.score_episode_from_telemetry = orig
    return out


def features(e, g, s):
    """Kinematic features of a STOP/HOLD episode on the scored rows (for the F2 candidates)."""
    ev = g.get("evidence") or {}
    aw = ev.get("active_window") or {}
    onset, end = aw.get("onset_s"), aw.get("active_end_s")
    rows = [r for r in e["rows"] if float(r["sim_time"]) <= min(end, 25.0) + 1e-9]
    v = [float(r.get("ego_speed_kmh") or 0) for r in rows]
    ipk = max(range(len(v)), key=v.__getitem__) if v else 0
    t_brake = None
    for i in range(max(ipk, 1), len(rows)):
        dt = float(rows[i]["sim_time"]) - float(rows[i - 1]["sim_time"])
        if dt > 1e-6 and (v[i - 1] - v[i]) / 3.6 / dt >= 1.0:
            t_brake = float(rows[i]["sim_time"]) - onset
            break
    rf = s.get("release_followup")
    return {**approach(e["rows"], end),
            "f_min": ev.get("min_front_to_stopline_m"), "t_brake_after_onset": t_brake,
            "release_in_horizon": rf is not None, "resumed_within_3s": None if rf is None else bool(rf.get("started_within_3s"))}


CANDIDATES = {  # id -> (description, gate(features) -> bool); applied to STOP/HOLD cells without an approach requirement
    "a1": (f"progress >= {D_STAR} m", lambda f: f["progress"] >= D_STAR),
    "a2": (f"peak speed >= {V_STAR} km/h", lambda f: f["v_peak"] >= V_STAR),
    "a3": (f"peak speed >= {V_STAR} km/h OR progress >= {D_STAR} m (recommended)", gate_a3),
    "a3r": ("peak speed >= 9 km/h OR progress >= 2.5 m (robust-margin variant)", lambda f: gate_a3(f, 9.0, 2.5)),
    "a4": ("stop within 5 m of the line (f_min <= 5 m)", lambda f: f["f_min"] is None or f["f_min"] <= 5.0),
    "a5": ("braking starts >= 0.5 s after onset (reaction floor)",
           lambda f: f["t_brake_after_onset"] is not None and f["t_brake_after_onset"] >= 0.5),
    "a6": ("resumes within 3 s of the release (when released inside the horizon)",
           lambda f: (not f["release_in_horizon"]) or bool(f["resumed_within_3s"])),
}


# ------------------------------------------------------------------------------------------------ aggregation
def board(cells):
    """cells: list of dicts (model, seed, scenario, credit, strict, exp) -> model -> stats (mean over runs)."""
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for c in cells:
        per[c["model"]][c["seed"]].append(c)
    out = {}
    for m, seeds in per.items():
        gs, ss, sm, k, n = [], [], [], 0, 0
        for cs in seeds.values():
            gs.append(100 * sum(W[c["scenario"]] * c["credit"] for c in cs) / sum(W[c["scenario"]] for c in cs))
            st = [c for c in cs if c["exp"] in ("STOP", "HOLD")]
            mv = [c for c in cs if c["exp"] in MOVE]
            ss.append(sum(W[c["scenario"]] * c["credit"] for c in st) / sum(W[c["scenario"]] for c in st))
            sm.append(sum(W[c["scenario"]] * c["credit"] for c in mv) / sum(W[c["scenario"]] for c in mv))
            k += sum(c["strict"] == "PASS" for c in cs)
            n += len(cs)
        mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
        out[m] = {"graded": mean(gs), "sd": (sum((x - mean(gs)) ** 2 for x in gs) / (len(gs) - 1)) ** 0.5 if len(gs) > 1 else 0.0,
                  "G_stop": mean(ss), "G_move": mean(sm), "k": k, "n": n, "runs": len(seeds)}
    return out


def ranks(b, key="graded"):
    return {m: i + 1 for i, m in enumerate(sorted(b, key=lambda m: -b[m][key]))}


def wr(name, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0])
    for r in rows:
        keys += [k for k in r if k not in keys]
    with (OUT / name).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def r6(x):
    return None if x is None else round(float(x), 6)


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pub = {r["model"]: r for r in csv.DictReader((AUD / "data" / "published_leaderboard_v2_e6ae3c8.csv").open(encoding="utf-8"))}
    dec = {r["episode_id"]: r for r in csv.DictReader((AUD / "data" / "episode_decomposition.csv").open(encoding="utf-8"))
           if r["cohort"] == "leaderboard"}
    eps = SN.capture()
    assert len(eps) == 650
    for e in eps:
        e["ep"] = C.find_episode(RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
        e["id"] = f"{e['model']}/s{e['seed']}/{e['scenario']}"
    val = capture_validation()

    # ---- collision logs (F3 inputs) -------------------------------------------------------------
    coll = []
    for e in eps + val:
        e["ids"] = event_ids(e["ep"])
        n_md = sum(a is not None for a, _ in e["ids"])
        hz = [r for r in e["rows"] if float(r["sim_time"]) <= 25.0 + 1e-9]
        cmax = max(int(float(r.get("collision_count") or 0)) for r in hz)
        if cmax:
            type_only = [(None, t) for _, t in e["ids"]]
            # the actor-identified prefix: counter capped at the events that carry an actor id
            pre = [{**r, "collision_count": min(int(float(r.get("collision_count") or 0)), n_md)} for r in hz]
            n = distinct_contacts(hz, e["ids"])
            coll.append({"episode": e.get("id") or f"{e['phase']}/{e['model']}/s{e['seed']}", "callbacks_in_horizon": cmax,
                         "events_logged_all": len(e["ids"]), "events_with_actor_id": n_md,
                         "distinct_contacts_horizon": n,
                         "distinct_contacts_type_only": distinct_contacts(hz, type_only),
                         "prefix_contacts_actor": distinct_contacts(pre, e["ids"]),
                         "prefix_contacts_type_only": distinct_contacts(pre, type_only),
                         "events_without_actor_id": max(0, cmax - n_md),
                         "band_changes_if_one_hidden_contact": cmax > n_md and band(n) != band(n + 1),
                         "complete_event_log": len(e["ids"]) >= cmax})
    wr("collision_logs.csv", coll)

    # ---- per-episode old / new / per-fix ---------------------------------------------------------
    rows_out, cells_old, cells_new = [], [], []
    variants = {"noF2gate": [], "a3r": []}
    single = {f: [] for f in ALL_FIXES}
    for e in eps:
        kw = e["kw"]
        exp = kw["expected_action"]
        s = S.score_episode_from_telemetry(e["res"], e["rows"], **kw)
        strict = "INVALID" if e["forced_invalid"] else s.get("verdict")
        c0, k0, _ = scored(e["res"], e["rows"], kw, e["ids"], fixes=(), forced_invalid=e["forced_invalid"])
        c1, k1, g1 = scored(e["res"], e["rows"], kw, e["ids"], fixes=ALL_FIXES, forced_invalid=e["forced_invalid"])
        d = dec[e["id"]]
        assert strict == d["strict"] and abs(c0 - float(d["credit"])) < 1e-6, e["id"]  # old = published
        per = {f: scored(e["res"], e["rows"], kw, e["ids"], fixes=(f,), forced_invalid=e["forced_invalid"])[0] for f in ALL_FIXES}
        why = [f for f in ALL_FIXES if abs(per[f] - c0) > EPS]
        rec = {"episode_id": e["id"], "model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "expected_action": exp,
               "weight": W[e["scenario"]], "strict": strict, "credit_v2": c0, "credit_v21": c1, "delta": round(c1 - c0, 6),
               "changed_by": ";".join(why) if abs(c1 - c0) > EPS else "",
               **{f"A_v2": r6(k0["A"]), "F_lat_v2": r6(k0["F_lat"]), "F_safe_v2": r6(k0["F_safe"]), "G_move_v2": k0["G_move"],
                  "e_v2": r6(k0["e"]), "latency_s_v2": k0["latency_s"], "n_callbacks": k0["n_collision"]},
               **{f"A_v21": r6(k1["A"]), "F_lat_v21": r6(k1["F_lat"]), "F_safe_v21": r6(k1["F_safe"]), "G_move_v21": k1["G_move"],
                  "e_v21": r6(k1["e"]), "latency_s_v21": k1["latency_s"], "latency_trigger_v21": k1["latency_trigger"],
                  "n_distinct_contacts": k1["n_collision"], "E_app": k1["E_app"]},
               **{f"credit_only_{f}": per[f] for f in ALL_FIXES}}
        if exp in ("STOP", "HOLD"):
            ft = features(e, g1, s)
            rec.update({"v_peak_kmh": round(ft["v_peak"], 4), "progress_m": round(ft["progress"], 4)})
        rows_out.append(rec)
        base = {"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": strict, "exp": exp}
        cells_old.append({**base, "credit": c0})
        cells_new.append({**base, "credit": c1})
        for f in ALL_FIXES:
            single[f].append({**base, "credit": per[f]})
        e["n_callbacks"] = k0["n_collision"]
        e["s"], e["g1"], e["c_nof2"] = s, g1, scored(e["res"], e["rows"], kw, e["ids"], fixes=("F1", "F6", "F3", "F7"),
                                                     forced_invalid=e["forced_invalid"])[0]
        f_ = _approach_of(e["rows"], g1)
        c_z = e["c_nof2"] * (gate_a3(f_, 9.0, 2.5) if f2_applies(kw) else 1)
        rec.update({"credit_v21_noF2gate": e["c_nof2"], "credit_v21_a3r": round(c_z, 6)})
        variants["noF2gate"].append({**base, "credit": e["c_nof2"]})
        variants["a3r"].append({**base, "credit": c_z})
    wr("episodes_v21.csv", rows_out)

    # ---- F3 merge-gap sensitivity on the scored rows (collided scored episodes) ----------------------
    hit = [e for e in eps if e["n_callbacks"]]
    per_gap = {gp: {e["id"]: scored(e["res"], e["rows"], e["kw"], e["ids"], fixes=("F3",), gap=gp)[1]["n_collision"] for e in hit}
               for gp in (0.25, 0.5, 1.0, 2.0, 5.0)}
    gap_rows = [{"gap_s": gp, "collided_scored_episodes": len(hit),
                 "band_changes_vs_1s": sum(band(ns[i]) != band(per_gap[1.0][i]) for i in ns),
                 "contacts_hist": ";".join(f"{k}:{v}" for k, v in sorted(collections.Counter(min(n, 6) for n in ns.values()).items())),
                 "max_contacts": max(ns.values())} for gp, ns in per_gap.items()]
    wr("f3_merge_gap.csv", gap_rows)

    # ---- leaderboard old vs new -------------------------------------------------------------------
    b0, b1 = board(cells_old), board(cells_new)
    for m in b0:  # reproduction gate against the published leaderboard
        assert round(b0[m]["graded"], 2) == float(pub[m]["graded_mean"]), m
        assert f"{b0[m]['k']}/{b0[m]['n']}" == pub[m]["strict_pass_total"], m
        assert (b0[m]["k"], b0[m]["n"]) == (b1[m]["k"], b1[m]["n"])
    models = sorted(b0, key=lambda m: -b0[m]["graded"])
    npm = [m for m in models if m not in PRIV | REF]
    r0, r1 = ranks(b0), ranks(b1)
    n0, n1 = ranks({m: b0[m] for m in npm}), ranks({m: b1[m] for m in npm})
    lb = [{"model": m, "track": pub[m]["track"], "runs": b0[m]["runs"], "strict_pass": f"{b0[m]['k']}/{b0[m]['n']}",
           "graded_v2": round(b0[m]["graded"], 2), "graded_v21": round(b1[m]["graded"], 2),
           "delta": round(b1[m]["graded"] - b0[m]["graded"], 2), "sd_v2": round(b0[m]["sd"], 2), "sd_v21": round(b1[m]["sd"], 2),
           "rank_v2": r0[m], "rank_v21": r1[m], "rank_nonpriv_v2": n0.get(m, ""), "rank_nonpriv_v21": n1.get(m, ""),
           "G_stop_v21": round(b1[m]["G_stop"], 4), "G_move_v21": round(b1[m]["G_move"], 4)} for m in models]
    bv = {k: board(v) for k, v in variants.items()}
    for r in lb:
        for k, b in bv.items():
            r[f"graded_v21_{k}"] = round(b[r["model"]]["graded"], 2)
            r[f"rank_nonpriv_v21_{k}"] = ranks({m: b[m] for m in npm}).get(r["model"], "")
    wr("leaderboard_v21.csv", lb)
    tau = SN.kendall_tau_b([b0[m]["graded"] for m in models], [b1[m]["graded"] for m in models])
    tau_np = SN.kendall_tau_b([b0[m]["graded"] for m in npm], [b1[m]["graded"] for m in npm])
    for k, b in bv.items():
        print(f"variant {k}: tau all {SN.kendall_tau_b([b0[m]['graded'] for m in models], [b[m]['graded'] for m in models]):.3f}; "
              f"non-priv {SN.kendall_tau_b([b0[m]['graded'] for m in npm], [b[m]['graded'] for m in npm]):.3f}")
    attr = []
    for f in ALL_FIXES:
        bf = board(single[f])
        attr.append({"fix": f, "episodes_changed": sum(abs(a["credit"] - b["credit"]) > EPS for a, b in zip(cells_old, single[f])),
                     "up": sum(b["credit"] - a["credit"] > EPS for a, b in zip(cells_old, single[f])),
                     "down": sum(a["credit"] - b["credit"] > EPS for a, b in zip(cells_old, single[f])),
                     "kendall_tau_b_all": round(SN.kendall_tau_b([b0[m]["graded"] for m in models], [bf[m]["graded"] for m in models]), 3),
                     "kendall_tau_b_nonpriv": round(SN.kendall_tau_b([b0[m]["graded"] for m in npm], [bf[m]["graded"] for m in npm]), 3),
                     **{m: round(bf[m]["graded"], 2) for m in models}})
    attr.append({"fix": "ALL", "episodes_changed": sum(abs(a["credit"] - b["credit"]) > EPS for a, b in zip(cells_old, cells_new)),
                 "up": sum(b["credit"] - a["credit"] > EPS for a, b in zip(cells_old, cells_new)),
                 "down": sum(a["credit"] - b["credit"] > EPS for a, b in zip(cells_old, cells_new)),
                 "kendall_tau_b_all": round(tau, 3), "kendall_tau_b_nonpriv": round(tau_np, 3), **{m: round(b1[m]["graded"], 2) for m in models}})
    wr("fix_attribution.csv", attr)

    # ---- F2 candidates on every real strict-PASS STOP/HOLD episode + fixtures -----------------------
    pas, fix_rows = [], []
    for e in eps:
        if e["kw"]["expected_action"] in ("STOP", "HOLD") and e["s"].get("verdict") == "PASS":
            ft = features(e, e["g1"], e["s"])
            pas.append({"episode": e["id"], "scenario": e["scenario"], "handoff": not f2_applies(e["kw"]),
                        **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in ft.items()},
                        **{c: int(fn(ft)) for c, (_, fn) in CANDIDATES.items()}})
    for e in val:
        s = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        c0 = scored(e["res"], e["rows"], e["kw"], e["ids"], fixes=(), forced_invalid=e["forced_invalid"])[0]
        c1, k1, g1 = scored(e["res"], e["rows"], e["kw"], e["ids"], fixes=ALL_FIXES, forced_invalid=e["forced_invalid"])
        ft = features(e, g1, s)
        fix_rows.append({"phase": e["phase"], "cell": e["model"], "seed": e["seed"], "strict": s.get("verdict"),
                         "credit_v2": c0, "credit_v21": c1, "A_v21": r6(k1["A"]), "F_lat_v21": r6(k1["F_lat"]),
                         "F_safe_v21": r6(k1["F_safe"]), "G_move": k1["G_move"], "E_app": k1["E_app"],
                         **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in ft.items()},
                         **{c: int(fn(ft)) for c, (_, fn) in CANDIDATES.items()}})
    wr("f2_candidates_passes.csv", pas)
    wr("validation_v21.csv", fix_rows)

    # ---- instruction-independent policies (synthetic, all 25 scenarios) ------------------------------
    pols = {"park": lambda t: 0.0,
            "audit lurch (8 km/h, ~1.6 m)": lambda t: 9.0 * t if t < 0.889 else max(0.0, 8.0 - 14.4 * (t - 0.889)),
            "constant 20 km/h": lambda t: min(20.0, 9 * t)}
    for v in (6, 8, 10, 12, 15):
        for dist in (1.2, 1.6, 2.0, 2.5, 3.0, 4.0):
            pols[f"lurch target {v} km/h, {dist} m"] = CF.ramp_to_stop(v, 0.2, 0.0, dist, a=2.5)
    for v in (5, 10, 30):
        pols[f"constant {v} km/h"] = (lambda v: lambda t: min(float(v), 9 * t))(v)
    for dist in (5.0, 10.0, 20.0):  # drive a fixed distance at 20 km/h, then stay stopped
        pols[f"stop after {dist:g} m at 20 km/h"] = CF.ramp_to_stop(20, 0.2, 0.0, dist, a=2.5)
    held_out = set()
    for t_go in (2.0, 4.0, 6.0, 8.0, 10.0, 13.5, 14.5, 16.5, 20.0):  # held-out family: lurch, then go at t_go
        lurch = CF.ramp_to_stop(10, 0.2, 0.0, 2.5, a=2.5)
        name = f"lurch 2.5 m, go 20 km/h at {t_go:g} s"
        pols[name] = (lambda lurch, t_go: lambda t: lurch(t) if t < t_go else min(20.0, 9 * (t - t_go)))(lurch, t_go)
        held_out.add(name)
    null, null_cells = [], {}
    for pn, fn in pols.items():
        cells = []
        for sc in vlm.SCENARIO_ORDER:
            exp = vlm.SCENARIOS[sc]["expect"]
            kw = dict(scenario=sc, expected_action=exp, max_reaction_time=3.0, protocol="v2")
            res, rows = {"scenario": sc}, CF.synth(sc, fn)
            s = S.score_episode_from_telemetry(res, rows, **kw)
            c0 = scored(res, rows, kw, fixes=())[0]
            cb, _, g = scored(res, rows, kw, fixes=("F1", "F6", "F3", "F7"))
            ft = features({"rows": rows}, g, s) if exp in ("STOP", "HOLD") else None
            cand = {c: (cb * (fn_(ft) if (ft and f2_applies(kw)) else 1)) for c, (_, fn_) in CANDIDATES.items()}
            cells.append({"scenario": sc, "exp": exp, "strict": s.get("verdict"), "v2": c0, "v21_noF2": cb, **cand})
        tot = sum(W.values())
        null_cells[pn] = {c["scenario"]: c for c in cells}
        g_side = lambda key, pred: sum(W[c["scenario"]] * c[key] for c in cells if pred(c["exp"])) / sum(W[c["scenario"]] for c in cells if pred(c["exp"]))  # noqa: E731
        null.append({"policy": pn, "family": "held-out (stop-and-go)" if pn in held_out else "reference",
                     "strict_pass": sum(c["strict"] == "PASS" for c in cells),
                     "graded_v2": round(100 * sum(W[c["scenario"]] * c["v2"] for c in cells) / tot, 2),
                     "graded_v21_noF2": round(100 * sum(W[c["scenario"]] * c["v21_noF2"] for c in cells) / tot, 2),
                     **{f"graded_{c}": round(100 * sum(W[x["scenario"]] * x[c] for x in cells) / tot, 2) for c in CANDIDATES},
                     "G_stop_a3": round(g_side("a3", lambda x: x in ("STOP", "HOLD")), 4),
                     "G_move_a3": round(g_side("a3", lambda x: x in MOVE), 4),
                     "a3_full_credit_cells": ";".join(c["scenario"] for c in cells if c["a3"] >= 1 - EPS)})
    wr("f2_null_policies.csv", null)

    # ---- model-level / reporting-layer candidates (b, c, d) on the per-episode credit without the F2 gate ------
    ref = [p for p in pols if p not in held_out]
    N = {sc: max(null_cells[p][sc]["v21_noF2"] for p in ref) for sc in vlm.SCENARIO_ORDER}  # null ceiling per station
    ident = [sc for sc in vlm.SCENARIO_ORDER if N[sc] < 0.5]

    def model_level(cs):
        """cs: scenario -> (expected, credit) for ONE run -> dict of b/c/d scores."""
        side = lambda pred: (sum(W[s] * c for s, (x, c) in cs.items() if pred(x)) / sum(W[s] for s, (x, c) in cs.items() if pred(x)))  # noqa: E731
        gs, gm = side(lambda x: x in ("STOP", "HOLD")), side(lambda x: x in MOVE)
        keep = [s for s in cs if N[s] < 1 - EPS]
        return {"graded": 100 * sum(W[s] * c for s, (_, c) in cs.items()) / sum(W[s] for s in cs),
                "b_identifiable_graded": 100 * sum(W[s] * cs[s][1] for s in ident) / sum(W[s] for s in ident),
                "c_DQ_min": 100 * min(gs, gm), "c_J_credit": gs + gm - 1, "G_stop": gs, "G_move": gm,
                "d_null_corrected": 100 * sum(W[s] * max(0.0, cs[s][1] - N[s]) / (1 - N[s]) for s in keep) / sum(W[s] for s in keep)}
    runs = collections.defaultdict(dict)
    for e in eps:
        runs[(e["model"], e["seed"])][e["scenario"]] = (e["kw"]["expected_action"], e["c_nof2"])
    per_model = collections.defaultdict(list)
    for (m, _), cs in runs.items():
        per_model[m].append(model_level(cs))
    ml = [{"model": m, "kind": "model", **{k: round(sum(x[k] for x in v) / len(v), 4) for k in v[0]}}
          for m, v in sorted(per_model.items(), key=lambda kv: -sum(x["graded"] for x in kv[1]) / len(kv[1]))]
    for p in pols:
        ml.append({"model": p, "kind": "null:" + ("held-out" if p in held_out else "reference"),
                   **{k: round(v, 4) for k, v in model_level({sc: (c["exp"], c["v21_noF2"]) for sc, c in null_cells[p].items()}).items()}})
    wr("f2_model_level.csv", ml)
    wr("f2_null_ceiling_per_station.csv", [{"scenario": sc, "expected_action": vlm.SCENARIOS[sc]["expect"], "weight": W[sc],
                                            "null_ceiling": round(N[sc], 4), "argmax_policy": max(ref, key=lambda p: null_cells[p][sc]["v21_noF2"]),
                                            "identifiable": sc in ident} for sc in vlm.SCENARIO_ORDER])

    # ---- candidate summary + zero-false-penalty window for a3 ---------------------------------------
    real = [p for p in pas if not p["handoff"]]
    lur = [r for r in fix_rows if r["cell"] == "scripted_lurch_then_stop"]
    nor = [r for r in fix_rows if r["cell"] == "scripted_normal_stop"]
    summ = []
    for c, (desc, _) in CANDIDATES.items():
        summ.append({"candidate": c, "rule": desc, "real_passes": len(real), "false_penalties": sum(1 - p[c] for p in real),
                     "false_penalty_episodes": ";".join(p["episode"] for p in real if not p[c]),
                     "lurch_fixture_rejected": f"{sum(1 - r[c] for r in lur)}/{len(lur)}",
                     "normal_fixture_kept": f"{sum(r[c] for r in nor)}/{len(nor)}",
                     "audit_lurch_graded": next(n[f"graded_{c}"] for n in null if n["policy"].startswith("audit lurch")),
                     "best_null_policy_graded": max(n[f"graded_{c}"] for n in null),
                     "best_null_policy": max(null, key=lambda n: n[f"graded_{c}"])["policy"]})
    wr("f2_candidates_summary.csv", summ)
    win = []
    for v in (8.0, 8.5, 9.0, 9.5, 10.0):
        for dd in (1.8, 2.0, 2.2, 2.5, 3.0):
            g_ = lambda f: f["v_peak"] >= v or f["progress"] >= dd  # noqa: E731
            win.append({"v_star_kmh": v, "d_star_m": dd, "false_penalties": sum(not g_(p) for p in real),
                        "lurch_rejected": sum(not g_(r) for r in lur), "normal_kept": sum(g_(r) for r in nor)})
    wr("f2_threshold_window.csv", win)

    # ---- console summary ---------------------------------------------------------------------------
    print(f"Kendall tau-b v2 vs v2.1: all {tau:.3f}; non-privileged {tau_np:.3f}")
    for r in lb:
        print(r)
    for a in attr:
        print({k: a[k] for k in ("fix", "episodes_changed", "up", "down", "kendall_tau_b_all", "kendall_tau_b_nonpriv")})
    for s_ in summ:
        print(s_)
    for r in gap_rows:
        print(r)
    for r in fix_rows:
        if r["phase"] == "23_fixtures":
            print(r["cell"], r["seed"], r["strict"], r["credit_v2"], "->", r["credit_v21"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
