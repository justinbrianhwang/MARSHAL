"""Credit discrimination: v2.2-final vs v2.2-D, failure-type labels, component sensitivity, F2 signals.

    python <audit>/credit_discrimination/scripts/discrimination.py          # from the workspace root

v2.2-D = v2.2-final with three changes (D1-D3) and a categorical failure-type label next to the score:
  D3  STOP/HOLD only: the credit-side movement gate (G_move and the engagement early zero) requires forward progress
      >= 1 m only; the 5 km/h speed arm is dropped there (PROCEED/DETOUR/YIELD keep it: there it blocks partial credit
      for an ego that never performed the required movement). A slow ego that travelled metres is no longer scored as if it never moved;
      the strict-compliant shortcut keeps the strict definition, so a strict FAIL cannot reach 1.0.
  D1  STOP/HOLD latency factor for an ego that reaches a HELD stop: full credit up to the reaction plateau
      (max(3 s, budget)), then linear to 0 at the LATER of the published zero point (plateau + 5 s) and the end of the
      live directive (measured from the same anchor as F6h). A held stop reached while the directive is still live is
      no longer scored the same as never stopping. Egos without a held stop are unchanged (latency factor 0).
  D2  stale_directive_residue (PROCEED after a live STOP): the live-STOP hold factor is 1 for a settled dwell (the
      published dwell test) and 0 otherwise. The published curve gave a rolling creep 0.3 and a faster roll up to
      1 - (v_min - 3)/9 (a 3.5 km/h roll 0.94 > a 2.9 km/h creep 0.3): not monotone, and it paid low-speed driving
      through a live STOP, which v2.2 already scores 0 on STOP stations.
  label  PASS / INVALID / NO_START / COLLISION / DRIVE_THROUGH / CRAWL_THROUGH / CREEP_NO_STOP / OVERRUN_THEN_STOP /
         LATE_STOP / STOP_NOT_HELD / STOP_POSITION / WRONG_ACTION / LATE_START / EARLY_ENTRY / INCOMPLETE_DETOUR /
         INCOMPLETE_YIELD / NO_RESUME, from existing logged features (rules in failure_label()).
Writes credit_discrimination/data/*.csv and stdout.txt only.
"""
from __future__ import annotations

import collections
import contextlib
import csv
import itertools
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data"
sys.path.insert(0, str(HERE.parents[1] / "credit_v22_final" / "scripts"))
import credit_final as F  # noqa: E402  (frozen v2.2-final)

X, V, G, S, CF, W = F.X, F.V, F.G, F.S, F.CF, F.W
EPS = 1e-6
STOP_KMH = G.STRICT_THRESHOLDS["stop_speed_kmh"]           # 1 km/h
FAST_KMH = G.GRADED_THRESHOLDS["stop_speed_zero_kmh"]       # 12 km/h: speed-margin credit reaches 0
LOG = []
_LAT22 = X.latency_v22
LABELS_STOP = ("NO_START", "COLLISION", "DRIVE_THROUGH", "CRAWL_THROUGH", "CREEP_NO_STOP", "OVERRUN_THEN_STOP",
               "LATE_STOP", "STOP_NOT_HELD", "STOP_POSITION")


def say(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s)


def wr(name, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with (OUT / name).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------------------------------------ v2.2-D
def _v(r):
    return G._finite_float(r.get("ego_speed_kmh")) or 0.0


def latency_d(rows, action, scenario, onset, *, action_credit, reaction_budget=3.0):
    f, ev = _LAT22(rows, action, scenario, onset, action_credit=action_credit, reaction_budget=reaction_budget)
    if action not in ("STOP", "HOLD") or ev.get("trigger") not in ("held_stop", "held_stop_after_launch"):
        return f, ev
    after = [r for r in rows if float(r["sim_time"]) >= onset - 1e-9]
    anchor = onset if ev["trigger"] == "held_stop" else next(float(r["sim_time"]) for r in after if _v(r) > STOP_KMH)
    full = max(G.GRADED_THRESHOLDS["latency_full_credit_s"], float(reaction_budget))
    zero = max(full + G.GRADED_THRESHOLDS["latency_zero_credit_s"] - G.GRADED_THRESHOLDS["latency_full_credit_s"],
               float(rows[-1]["sim_time"]) - anchor)
    return G._linear_credit(ev["latency_s"], full, zero), {**ev, "zero_credit_s": round(zero, 3)}


def _build_after_release():
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(G._score_proceed_after_release))
    a = src[src.index("    if min_hold is None:\n"):src.index("    components = dict(components, directive_hold=")]
    assert src.count(a) == 1
    src = src.replace(a, "    hold_factor = 1.0 if (min_hold is not None and settled) else 0.0\n")
    src = src.replace("def _score_proceed_after_release(", "def _score_proceed_after_release_d(", 1)
    ns = dict(vars(G))
    exec(compile(src, "<after-release-D>", "exec"), ns)
    return ns["_score_proceed_after_release_d"]


_AFTER_RELEASE = G._score_proceed_after_release
AFTER_RELEASE_D = _build_after_release()
_MOVEMENT = G._movement_evidence
_ENG_FINAL = F.ENGAGEMENT_FINAL


def movement_d(rows):
    """D3: the credit-side movement gate keeps its progress arm (>= engagement_progress_m) and drops the speed arm."""
    ev = _MOVEMENT(rows)
    return {**ev, "engaged": float(ev["forward_progress_m"] or 0.0) >= G.STRICT_THRESHOLDS["engagement_progress_m"]}


def _build_engagement_d():
    """The frozen final engagement function with D3: the early 'no approach evidence' zero uses progress only; the
    strict-compliant shortcut keeps the strict definition (speed AND progress), so a slow ego never reaches 1.0."""
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(V.ALT._orig_engagement))
    reps = [
        ('    first_stop_time: Optional[float] = None\n'
         '    for row in rows:\n'
         '        if float(row["sim_time"]) >= onset and float(row["ego_speed_kmh"]) <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n',
         '    first_stop_time: Optional[float] = None\n'
         '    _search_start = next((float(r["sim_time"]) for r in rows if float(r["sim_time"]) >= onset\n'
         '                          and float(r["ego_speed_kmh"]) > GRADED_THRESHOLDS["stop_speed_kmh"]), onset)\n'
         '    for row in rows:\n'
         '        if float(row["sim_time"]) >= _search_start and float(row["ego_speed_kmh"]) <= GRADED_THRESHOLDS["stop_speed_kmh"]:\n'),
        ('    speed_credit = _linear_credit(\n        max_approach_speed,',
         '    _onset_front = _finite_float(onset_row.get("stopline_front_m"))\n'
         '    if _onset_front is not None and front_clearance is not None:\n'
         '        approach_distance = _onset_front - max(front_clearance, 0.0)\n'
         '    speed_credit = _linear_credit(\n        max_approach_speed,'),
        ('    if speed_credit <= 0.0 and near_stopline_progress_credit > GRADED_THRESHOLDS["stop_engagement_creep_cap"]:',
         '    if speed_credit <= 0.0 and near_stopline_progress_credit > GRADED_THRESHOLDS["stop_engagement_creep_cap"] and (\n'
         '            min_stopline is None or min_stopline > GRADED_THRESHOLDS["stopline_engagement_m"]):'),
        ('    if not _movement_engaged():\n',
         '    def _movement_progressed() -> bool:\n'
         '        mrows = rows if movement_rows is None else movement_rows\n'
         '        fv = [v for v in (_finite_float(r.get("ego_forward_m")) for r in mrows) if v is not None]\n'
         '        return bool(fv) and (max(fv) - min(fv)) >= STRICT_THRESHOLDS["engagement_progress_m"]\n'
         '\n'
         '    if not _movement_progressed():\n'),
    ]
    for a, b in reps:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    src = src.replace("def _stop_hold_engagement_factor(", "def _engagement_d(", 1)
    ns = dict(vars(G))
    exec(compile(src, "<engagement-D>", "exec"), ns)
    return ns["_engagement_d"]


ENGAGEMENT_D = _build_engagement_d()


@contextlib.contextmanager
def d_mode(stop_hold=True):
    X.latency_v22 = latency_d
    G._score_proceed_after_release = AFTER_RELEASE_D
    if stop_hold:   # D3 applies to STOP/HOLD only
        G._movement_evidence = movement_d
        F.ENGAGEMENT_FINAL = ENGAGEMENT_D
    try:
        yield
    finally:
        X.latency_v22 = _LAT22
        G._score_proceed_after_release = _AFTER_RELEASE
        G._movement_evidence = _MOVEMENT
        F.ENGAGEMENT_FINAL = _ENG_FINAL


def scored(res, rows, kw, ids=(), version="D", forced_invalid=False):
    if version == "D":
        with d_mode(kw.get("expected_action") in ("STOP", "HOLD")):
            return F.scored(res, rows, kw, ids, "final", forced_invalid)
    return F.scored(res, rows, kw, ids, version, forced_invalid)


def syn(sc, act, tel, version="D"):
    return scored({"scenario": sc}, tel, dict(scenario=sc, expected_action=act, max_reaction_time=3.0, protocol="v2"), (), version)


# ------------------------------------------------------------------------------------------------ features + label
def features(rows_all, kw, g, k):
    """Behaviour features on the scored rows (STOP/HOLD: up to the directive end; others: 25 s horizon)."""
    exp = kw["expected_action"]
    aw = (g.get("evidence") or {}).get("active_window") or {}
    onset, end = aw.get("onset_s") or 0.0, aw.get("active_end_s") or 25.0
    budget = float(kw.get("max_reaction_time") or 3.0)
    lim = min(end, 25.0) if exp in ("STOP", "HOLD") else 25.0
    rows = [r for r in rows_all if float(r["sim_time"]) <= lim + 1e-9]
    t = [float(r["sim_time"]) for r in rows]
    v = [_v(r) for r in rows]
    after = [i for i in range(len(rows)) if t[i] >= onset - 1e-9]
    fronts = [(t[i], G._finite_float(rows[i].get("stopline_front_m"))) for i in after]
    fronts = [(a, b) for a, b in fronts if b is not None]
    fwd = [x for x in (G._finite_float(r.get("ego_forward_m")) for r in rows) if x is not None]
    enf = [v[i] for i in range(len(rows)) if t[i] >= onset + budget - 1e-9]
    lat, trig = X.held_stop(rows, onset) if exp in ("STOP", "HOLD") else (None, "")
    t_hold = None
    if trig in ("held_stop", "held_stop_after_launch"):
        anchor = onset if trig == "held_stop" else next(t[i] for i in after if v[i] > STOP_KMH)
        t_hold = anchor + lat
    relaunch = t_hold is not None and any(v[i] > STOP_KMH for i in range(len(rows)) if t[i] > t_hold + X.DWELL_S)
    t_cross = next((a for a, b in fronts if b < 0), None)
    return {"onset_s": round(onset, 2), "scored_end_s": round(t[-1], 2) if t else None,
            "peak_kmh": round(max(v), 2) if v else 0.0,
            "peak_after_onset_kmh": round(max([v[i] for i in after] or [0.0]), 2),
            "travel_m": round(max(fwd) - min(fwd), 2) if fwd else 0.0,
            "min_front_m": round(min(b for _, b in fronts), 2) if fronts else None, "t_cross_s": t_cross,
            "held_stop": t_hold is not None or trig == "at_rest_never_moved", "held_latency_s": lat, "held_trigger": trig,
            "t_hold_s": None if t_hold is None else round(t_hold, 2), "relaunch_after_hold": relaunch,
            "max_enforced_kmh": round(max(enf), 2) if enf else None,
            "stopped_share_enforced": round(sum(x < STOP_KMH for x in enf) / len(enf), 3) if enf else None,
            "contacts": k.get("n_collision") or 0, "G_move": k["G_move"],
            "max_lateral_m": round(max(abs(G._finite_float(r.get("ego_lateral_m")) or 0.0) for r in rows), 2) if rows else 0.0}


def failure_label(exp, strict, reason, f, scenario):
    """Categorical failure type from existing features (no ordering, no weights)."""
    if strict == "PASS":
        return "PASS"
    if strict == "INVALID":
        return "INVALID"
    r = (reason or "").lower()
    if f["travel_m"] < G.STRICT_THRESHOLDS["engagement_progress_m"]:      # did not move 1 m
        return "NO_START"
    if "collision" in r or f["contacts"]:
        return "COLLISION"
    if exp in ("STOP", "HOLD"):
        if G._approach_requirement_for(scenario) is not None:          # dual_authority_handoff: band geometry
            if "blasted" in r:
                return "DRIVE_THROUGH"
            if "overshoot" in r or "beyond the officer" in r:
                return "OVERRUN_THEN_STOP"
            if "parked short" in r:
                return "STOP_POSITION"
            return "STOP_NOT_HELD"
        crossed = f["t_cross_s"] is not None
        if f["t_hold_s"] is not None:
            if crossed and f["t_cross_s"] <= f["t_hold_s"] + 1e-9:
                return "OVERRUN_THEN_STOP"
            if f["relaunch_after_hold"]:
                return "STOP_NOT_HELD"
            if f["held_trigger"] in ("held_stop", "held_stop_after_launch") and f["max_enforced_kmh"] is not None \
                    and f["max_enforced_kmh"] >= STOP_KMH:
                return "LATE_STOP"
            return "STOP_POSITION"
        if f["held_trigger"] == "at_rest_never_moved":
            return "STOP_POSITION"
        if crossed:
            return "DRIVE_THROUGH" if f["peak_after_onset_kmh"] >= FAST_KMH else "CRAWL_THROUGH"
        return "CREEP_NO_STOP"
    if exp == "PROCEED":
        if "did not start proceeding" in r:
            return "LATE_START"
        if "while the stop directive was still active" in r:
            return "EARLY_ENTRY"
        if "rolling creep" in r or "never held a settled stop" in r:
            return "CRAWL_THROUGH"
        return "WRONG_ACTION"                                               # never entered / yielded but never proceeded
    if exp == "DETOUR":
        return "INCOMPLETE_DETOUR"
    if exp == "YIELD":
        if "never established approach motion" in r:
            return "LATE_START"
        if "did not slow/stop" in r:
            return "WRONG_ACTION"
        if "did not resume" in r:
            return "NO_RESUME"
        return "INCOMPLETE_YIELD"
    return "WRONG_ACTION"


def zeroing_terms(c, k, g, exp):
    if c > EPS:
        return ""
    out = []
    if not k["G_move"]:
        out.append("G_move")
    if (k["F_safe"] or 0) <= EPS:
        out.append("F_safe")
    if (k["F_lat"] or 0) <= EPS:
        out.append("F_lat")
    if (k["A"] or 0) <= EPS:
        cc = g.get("component_credits") or {}
        sub = [n for n in ("speed_margin", "stopline_clearance", "engagement_gate", "approach_requirement") if cc.get(n) is not None and cc[n] <= EPS] \
            if exp in ("STOP", "HOLD") else [n for n, x in cc.items() if isinstance(x, (int, float)) and x <= EPS]
        out.append("A(" + ",".join(sub) + ")" if sub else "A")
    return "+".join(out) or "other"


# ------------------------------------------------------------------------------------------------ statistics
def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return None if den == 0 else round(num / den, 3)


# ------------------------------------------------------------------------------------------------ synthetic sweeps
def sweeps():
    gs, so = "green_stop", "signal_off"
    F0 = float(CF.template(gs)[0]["stopline_front_m"])
    S0 = float(CF.template(so)[0]["stopline_front_m"])
    ramp = CF.ramp_to_stop
    sw = collections.OrderedDict()
    sw["stop position (front to line, m; 15 km/h)"] = [(f"{x:+g}", gs, ramp(15, 0.2, x, F0, a=3.0)) for x in (6.0, 4.0, 2.0, 1.0, 0.5, 0.0, -0.5, -2.0, -5.0)]
    sw["approach peak speed (km/h; stop 2 m before)"] = [(f"{vv}", gs, ramp(vv, 0.2, 2.0, F0, a=2.0)) for vv in (4, 6, 8, 10, 15, 20, 25)]
    sw["reaction time (brake start, s; 8 km/h, signal_off)"] = [
        (f"{tb}", so, (lambda tb: (lambda t: 0.0 if t < 0.2 else (8.0 if t < tb else max(0.0, 8.0 - 20 * (t - tb)))))(tb))
        for tb in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 9.0)]
    hold_stop = ramp(15, 0.2, 4.0, F0, a=3.0)
    t_stop = next(i * 0.01 for i in range(30, 3000) if hold_stop(i * 0.01) <= 0.0)  # first standstill after the launch at 0.2 s
    sw["stop hold before relaunch at 5 km/h (s)"] = [
        (f"{h}", gs, (lambda h: (lambda t: hold_stop(t) if t < t_stop + h else min(5.0, 5 * (t - t_stop - h))))(h))
        for h in (0.5, 1.5, 2.5, 4.0, 8.0, 99.0)]
    sw["collision events 1.5 s apart (normal stop)"] = [(f"{n}", gs, ramp(15, 0.2, 2.0, F0), tuple(5 + 1.5 * i for i in range(n))) for n in (0, 1, 2, 6)]
    sw["travel (m, 8 km/h short stop)"] = [(f"{d}", gs, ramp(8, 0.2, F0 - d, F0)) for d in (0.5, 0.9, 1.2, 2.0, 4.0, 6.0)]
    out = []
    for name, pts in sw.items():
        for p in pts:
            lab, sc, fn = p[:3]
            tel = CF.synth(sc, fn, collisions_at=p[3] if len(p) > 3 else ())
            st = S.score_episode_from_telemetry({"scenario": sc}, tel, scenario=sc, expected_action="STOP", max_reaction_time=3.0, protocol="v2")
            row = {"sweep": name, "value": lab, "strict": st.get("verdict")}
            for ver in ("final", "D"):
                c, k, g = syn(sc, "STOP", tel, ver)
                cc = g.get("component_credits") or {}
                if ver == "final":
                    f = features(tel, dict(scenario=sc, expected_action="STOP", max_reaction_time=3.0), g, k)
                    row.update({"label": failure_label("STOP", st.get("verdict"), st.get("reason"), f, sc),
                                "speed_margin": X.r4(cc.get("speed_margin")), "clearance": X.r4(cc.get("stopline_clearance")),
                                "no_conflict": cc.get("no_conflict_entry"), "e": X.r4(cc.get("engagement_gate")),
                                "approach_speed": X.r4(cc.get("approach_speed")), "approach_progress": X.r4(cc.get("approach_progress")),
                                "F_lat": X.r4(k["F_lat"]), "latency_s": k["latency_s"], "F_safe": X.r4(k["F_safe"]), "G_move": k["G_move"]})
                row[f"credit_{ver}"] = round(c, 4)
                if ver == "D":
                    row["F_lat_D"] = X.r4(k["F_lat"])
            out.append(row)
    return out


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    VERS = ("v2", "v2.1", "final", "D")
    eps = V.SN.capture()
    assert len(eps) == 650
    for e in eps:
        e["ep"] = V.C.find_episode(V.RUNS / e["model"] / f"s{e['seed']}", e["scenario"])
        e["id"] = f"{e['model']}/s{e['seed']}/{e['scenario']}"
        e["ids"] = V.event_ids(e["ep"])
        s = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        e["strict"] = "INVALID" if e["forced_invalid"] else s.get("verdict")
        e["reason"] = s.get("reason")
        e["v"] = {v: scored(e["res"], e["rows"], e["kw"], e["ids"], v, e["forced_invalid"]) for v in VERS}
        c, k, g = e["v"]["final"]
        e["f"] = features(e["rows"], e["kw"], g, k)
        e["label"] = failure_label(e["kw"]["expected_action"], e["strict"], e["reason"], e["f"], e["scenario"])

    # ---- per-episode table ---------------------------------------------------------------------------
    rows = []
    for e in eps:
        exp = e["kw"]["expected_action"]
        rec = {"episode_id": e["id"], "model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "expected_action": exp,
               "strict": e["strict"], "strict_reason": (e["reason"] or "")[:100], "label": e["label"]}
        for v in VERS:
            rec[f"credit_{v}"] = e["v"][v][0]
        cF, kF, gF = e["v"]["final"]
        cD, kD, gD = e["v"]["D"]
        rec.update({"A": X.r4(kF["A"]), "e": X.r4(kF["e"]), "F_lat_final": X.r4(kF["F_lat"]), "F_lat_D": X.r4(kD["F_lat"]),
                    "F_safe": X.r4(kF["F_safe"]), "G_move": kF["G_move"],
                    "zeroing_final": zeroing_terms(cF, kF, gF, exp), "zeroing_D": zeroing_terms(cD, kD, gD, exp), **e["f"]})
        rows.append(rec)
    wr("episodes_discrimination.csv", rows)

    # ---- 1/6: discrimination metrics ---------------------------------------------------------------------
    fails = [e for e in eps if e["strict"] == "FAIL"]
    sh_eng = [e for e in fails if e["kw"]["expected_action"] in ("STOP", "HOLD") and e["f"]["G_move"]]
    feats = {"min front to line (m, higher better)": lambda e: e["f"]["min_front_m"] if e["f"]["min_front_m"] is not None else 0.0,
             "max speed after the reaction deadline (km/h, lower better)": lambda e: -(e["f"]["max_enforced_kmh"] or 0.0),
             "time to held stop (s, lower better; none = 25)": lambda e: -(e["f"]["held_latency_s"] if e["f"]["held_latency_s"] is not None else 25.0),
             "stopped share of the enforced window (higher better)": lambda e: e["f"]["stopped_share_enforced"] or 0.0,
             "distinct contacts (lower better)": lambda e: -e["f"]["contacts"]}
    disc = []
    for v in VERS:
        cs = [e["v"][v][0] for e in fails]
        pairs = diff = 0
        for a, b in itertools.combinations(fails, 2):
            if a["label"] != b["label"]:
                diff += 1
                pairs += abs(a["v"][v][0] - b["v"][v][0]) < EPS
        allc = [e["v"][v][0] for e in eps]
        rec = {"version": v, "fail_episodes": len(fails), "distinct_fail_levels (rounded to 4 decimals)": len({round(c, 4) for c in cs}),
               "fail_at_zero": sum(c < EPS for c in cs), "fail_at_zero_share": round(sum(c < EPS for c in cs) / len(cs), 3),
               "all_zero/partial/one": f"{sum(c < EPS for c in allc)}/{sum(EPS <= c < 1 - EPS for c in allc)}/{sum(c >= 1 - EPS for c in allc)}",
               "different-label FAIL pairs tied in credit": f"{pairs}/{diff} ({pairs / diff:.3f})",
               "distinct (credit rounded to 4 decimals, label) FAIL states": len({(round(e['v'][v][0], 4), e['label']) for e in fails}),
               **{f"spearman, engaged STOP/HOLD FAIL (n={len(sh_eng)}): {n}": spearman([e["v"][v][0] for e in sh_eng], [fn(e) for e in sh_eng])
                  for n, fn in feats.items()}}
        disc.append(rec)
    wr("discrimination_metrics.csv", disc)

    # ---- 3: zero decomposition and collapse ---------------------------------------------------------------
    zt = collections.Counter((e["kw"]["expected_action"] in ("STOP", "HOLD"), e["label"], r["zeroing_final"]) for e, r in zip(eps, rows)
                             if e["v"]["final"][0] < EPS)
    wr("zero_decomposition.csv", [{"stop_or_hold": a, "label": b, "zeroing_terms_final": c, "episodes": n} for (a, b, c), n in sorted(zt.items(), key=lambda x: -x[1])])
    lab_tab = []
    for lab in sorted({e["label"] for e in eps}):
        es = [e for e in eps if e["label"] == lab]
        rec = {"label": lab, "episodes": len(es)}
        for v in ("v2", "final", "D"):
            cs = [e["v"][v][0] for e in es]
            rec.update({f"{v}: zero": sum(c < EPS for c in cs), f"{v}: mean": round(sum(cs) / len(cs), 3), f"{v}: max": round(max(cs), 3)})
        lab_tab.append(rec)
    wr("label_credit_table.csv", lab_tab)
    per_model = collections.defaultdict(collections.Counter)
    for e in eps:
        per_model[e["model"]][e["label"]] += 1
    labs = sorted({e["label"] for e in eps})
    wr("labels_per_model.csv", [{"model": m, **{l: per_model[m][l] for l in labs}} for m in sorted(per_model)])
    smp = []
    for lab in labs:
        for e in [x for x in eps if x["label"] == lab][:3]:
            smp.append({"label": lab, "episode": e["id"], "strict_reason": (e["reason"] or "")[:80], "credit_final": e["v"]["final"][0],
                        **{k: e["f"][k] for k in ("peak_after_onset_kmh", "min_front_m", "t_cross_s", "t_hold_s", "held_latency_s",
                                                   "relaunch_after_hold", "max_enforced_kmh", "contacts", "travel_m")}})
    wr("label_samples.csv", smp)

    # ---- boards ------------------------------------------------------------------------------------------
    base = lambda e: {"model": e["model"], "seed": e["seed"], "scenario": e["scenario"], "strict": e["strict"], "exp": e["kw"]["expected_action"]}  # noqa: E731
    gr = {v: {m: s["graded"] for m, s in V.board([{**base(e), "credit": e["v"][v][0]} for e in eps]).items()} for v in VERS}
    models = sorted(gr["final"], key=lambda m: -gr["final"][m])
    npm = [m for m in models if m not in V.PRIV | V.REF]
    rk = {v: {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: -gr[v][m]))} for v in VERS}
    tau = lambda a, b, ms: round(V.SN.kendall_tau_b([gr[a][m] for m in ms], [gr[b][m] for m in ms]), 3)  # noqa: E731
    wr("leaderboard_650.csv", [{"model": m, **{f"graded_{v}": round(gr[v][m], 2) for v in VERS}, **{f"rank_{v}": rk[v][m] for v in VERS}} for m in models])

    # ---- strict consistency + changed episodes ------------------------------------------------------------
    by_sc = collections.defaultdict(list)
    for e in eps:
        by_sc[e["scenario"]].append(e)
    cons = []
    for v in ("final", "D"):
        c = {e["id"]: e["v"][v][0] for e in eps}
        cons.append({"version": v, "credit1_not_pass": sum(c[e["id"]] >= 1 - EPS and e["strict"] != "PASS" for e in eps),
                     "pass_below_1": sum(e["strict"] == "PASS" and c[e["id"]] < 1 - EPS for e in eps),
                     "fail_ge_pass_pairs": sum(1 for es in by_sc.values() for a in es for b in es
                                               if a["strict"] == "FAIL" and b["strict"] == "PASS" and c[a["id"]] >= c[b["id"]] - EPS),
                     "tau_vs_v2": f"{tau('v2', v, models)} / {tau('v2', v, npm)}", "tau_vs_final": f"{tau('final', v, models)} / {tau('final', v, npm)}",
                     "rank_changes_vs_final": ";".join(f"{m} {rk['final'][m]}->{rk[v][m]}" for m in models if rk[v][m] != rk["final"][m])})
    changed = [e for e in eps if abs(e["v"]["D"][0] - e["v"]["final"][0]) > EPS]
    wr("d_changed_episodes.csv", [{"episode": e["id"], "label": e["label"], "strict": e["strict"], "credit_final": e["v"]["final"][0],
                                   "credit_D": e["v"]["D"][0], "held_latency_s": e["f"]["held_latency_s"], "min_front_m": e["f"]["min_front_m"],
                                   "F_lat_final": e["v"]["final"][1]["F_lat"], "F_lat_D": e["v"]["D"][1]["F_lat"]} for e in changed])
    cons[-1]["episodes_changed_vs_final"] = len(changed)
    cons[-1]["labels_of_changed"] = dict(collections.Counter(e["label"] for e in changed))
    wr("consistency.csv", cons)

    # ---- synthetic: orderings, behaviours, fixtures, sweeps, policies --------------------------------------
    F0 = float(CF.template("green_stop")[0]["stopline_front_m"])
    mono = []
    for name, cases in X.mono_families(F0).items():
        seq = {v: [] for v in ("final", "D")}
        for lab, sc, act, fn, kw_ in cases:
            tel = CF.synth(sc, fn, **kw_)
            for v in seq:
                seq[v].append(syn(sc, act, tel, v)[0])
        for v, s_ in seq.items():
            bad = ([f"{cases[0][0]} vs {cases[1][0]}"] if abs(s_[0] - s_[1]) > EPS else []) if "(equal)" in name else \
                [f"{cases[i][0]} -> {cases[i + 1][0]}" for i in range(len(s_) - 1) if s_[i + 1] > s_[i] + EPS]
            mono.append({"family": name, "version": v, "credits": " | ".join(f"{x:.3f}" for x in s_), "violations": len(bad), "steps": "; ".join(bad)})
        if name == "overshoot at 25 km/h":
            fast = seq
        if name == "overshoot at 5 km/h":
            slow = seq
    for v in ("final", "D"):
        bad = [f"{o} m" for o, a, b in zip((0.0, 0.1, 0.5, 2.0, 10.0, 50.0), fast[v], slow[v]) if a > b + EPS]
        mono.append({"family": "F4: 25 km/h <= 5 km/h at equal overshoot", "version": v, "credits": " | ".join(f"{a:.3f}<={b:.3f}" for a, b in zip(fast[v], slow[v])),
                     "violations": len(bad), "steps": "; ".join(bad)})
    beh = []
    items = [(n, "green_stop", "STOP", fn) for n, fn in X.behaviours(F0).items()]
    items.append(("late but held stop 7 m before the line (6 km/h, brake at 9 s)", "signal_off", "STOP",
                  lambda t: 0.0 if t < 0.2 else (6.0 if t < 9.0 else max(0.0, 6.0 - 20 * (t - 9.0)))))
    sd = "stale_directive_residue"
    t_rel = next(float(r["sim_time"]) for r in CF.template(sd) if float(r["sim_time"]) > 1.0 and not (
        r.get("officer_active") is True and str(r.get("officer_gesture_id") or "").upper() in ("STOP", "HOLD")))
    stale = []
    for vmin in (0.0, 2.9, 3.5, 6.0, 9.0):
        fn = (lambda vm: (lambda t: min(8.0, 9 * t) if t < 2.0 else (vm if t < t_rel + 0.5 else min(20.0, vm + 9 * (t - t_rel - 0.5)))))(vmin)
        items.append((f"stale_directive: live STOP at {vmin} km/h minimum, proceed after release", sd, "PROCEED", fn))
        stale.append(len(items) - 1)
    for name, sc_b, act, fn in items:
        tel = CF.synth(sc_b, fn)
        st = S.score_episode_from_telemetry({"scenario": sc_b}, tel, scenario=sc_b, expected_action=act, max_reaction_time=3.0, protocol="v2")
        kw_b = dict(scenario=sc_b, expected_action=act, max_reaction_time=3.0)
        cF, kF, gF = syn(sc_b, act, tel, "final")
        f = features(tel, kw_b, gF, kF)
        beh.append({"behaviour (synthetic)": name, "station": sc_b, "strict": st.get("verdict"),
                    "label": failure_label(act, st.get("verdict"), st.get("reason"), f, sc_b),
                    "credit_final": round(cF, 4), "credit_D": round(syn(sc_b, act, tel, "D")[0], 4)})
    wr("behaviours.csv", beh)
    for v, col in (("final", "credit_final"), ("D", "credit_D")):
        seq = [beh[i][col] for i in stale]
        bad = [f"{items[stale[i]][0].split(' at ')[1].split(' km')[0]} -> {items[stale[i + 1]][0].split(' at ')[1].split(' km')[0]} km/h"
               for i in range(len(seq) - 1) if seq[i + 1] > seq[i] + EPS]
        mono.append({"family": "stale_directive: minimum speed during the live STOP 0/2.9/3.5/6/9 km/h", "version": v,
                     "credits": " | ".join(f"{x:.3f}" for x in seq), "violations": len(bad), "steps": "; ".join(bad)})
    wr("ordering.csv", mono)
    val = V.capture_validation()
    fx = []
    for e in val:
        s = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        ids = V.event_ids(e["ep"])
        cF, kF, gF = scored(e["res"], e["rows"], e["kw"], ids, "final", e["forced_invalid"])
        f = features(e["rows"], e["kw"], gF, kF)
        fx.append({"phase": e["phase"], "cell": e["model"], "seed": e["seed"], "strict": s.get("verdict"),
                   "label": failure_label("STOP", s.get("verdict"), s.get("reason"), f, "green_stop"),
                   "credit_final": cF, "credit_D": scored(e["res"], e["rows"], e["kw"], ids, "D", e["forced_invalid"])[0],
                   "held_stop_after_window_check": f["held_stop"], "min_front_m": f["min_front_m"], "peak_after_onset_kmh": f["peak_after_onset_kmh"]})
    wr("validation_labels.csv", fx)
    wr("component_sweeps.csv", sweeps())
    pols_, held_ = X.policies()
    tot = sum(W.values())
    pol = []
    for pn, fn in pols_.items():
        acc, npass = {"final": 0.0, "D": 0.0}, 0
        for sc in V.vlm.SCENARIO_ORDER:
            act = V.vlm.SCENARIOS[sc]["expect"]
            tel = CF.synth(sc, fn)
            npass += S.score_episode_from_telemetry({"scenario": sc}, tel, scenario=sc, expected_action=act,
                                                    max_reaction_time=3.0, protocol="v2").get("verdict") == "PASS"
            for v_ in acc:
                acc[v_] += W[sc] * syn(sc, act, tel, v_)[0]
        pol.append({"policy": pn, "family": "held-out" if pn in held_ else "reference", "strict_pass": npass,
                    "graded_final": round(100 * acc["final"] / tot, 2), "graded_D": round(100 * acc["D"] / tot, 2)})
    bestF, bestD = max(gr["final"][m] for m in npm), max(gr["D"][m] for m in npm)
    wr("policies.csv", pol)

    # ---- 2: component profile on the 650 ----------------------------------------------------------------
    prof = []
    for exp_set, nm in ((("STOP", "HOLD"), "STOP/HOLD"), (("PROCEED", "DETOUR", "YIELD"), "PROCEED/DETOUR/YIELD")):
        es = [e for e in eps if e["kw"]["expected_action"] in exp_set and not e["forced_invalid"]]
        terms = collections.defaultdict(list)
        for e in es:
            c, k, g = e["v"]["final"]
            cc = g.get("component_credits") or {}
            for n in ("A", "F_lat", "F_safe", "G_move"):
                terms[n].append(float(k[n] or 0))
            for n, x in cc.items():
                if isinstance(x, (int, float)):
                    terms["A." + n].append(float(x))
        for n, xs in terms.items():
            prof.append({"actions": nm, "term": n, "n": len(xs), "share_0": round(sum(x <= EPS for x in xs) / len(xs), 3),
                         "share_1": round(sum(x >= 1 - EPS for x in xs) / len(xs), 3), "distinct_values (rounded to 4 decimals)": len({round(x, 4) for x in xs})})
    wr("component_profile_650.csv", prof)

    # ---- 5: F2 signals --------------------------------------------------------------------------------------
    def held_window(rows, t0, t1):
        rr = [r for r in rows if t0 - 1e-9 <= float(r["sim_time"]) <= t1 + 1e-9]
        return bool(rr) and max(_v(r) for r in rr) < STOP_KMH

    def S_stop(rows, kw, g):
        aw = (g.get("evidence") or {}).get("active_window") or {}
        on = aw.get("onset_s") or 0.0
        return held_window(rows, on + 3.0, on + 8.0)
    proceed = [sc for sc in V.vlm.SCENARIO_ORDER if V.vlm.SCENARIOS[sc]["expect"] == "PROCEED"]
    stops = [sc for sc in V.vlm.SCENARIO_ORDER if V.vlm.SCENARIOS[sc]["expect"] in ("STOP", "HOLD") and G._approach_requirement_for(sc) is None]
    isi = []
    acc = collections.defaultdict(lambda: {"s": [], "p": []})
    for e in eps:
        if e["forced_invalid"]:
            continue
        g = e["v"]["final"][2]
        if e["scenario"] in stops:
            acc[e["model"]]["s"].append(S_stop(e["rows"], e["kw"], g))
        elif e["scenario"] in proceed:
            acc[e["model"]]["p"].append(S_stop(e["rows"], e["kw"], g))
    for m in models:
        ps, pp = sum(acc[m]["s"]) / len(acc[m]["s"]), sum(acc[m]["p"]) / len(acc[m]["p"])
        isi.append({"row": m, "kind": "model", "P(stopped | STOP/HOLD)": round(ps, 3), "P(stopped | PROCEED)": round(pp, 3), "ISI": round(ps - pp, 3),
                    "graded_D": round(gr["D"][m], 2)})
    pols, held = X.policies()
    for pn, fn in pols.items():
        s_, p_ = [], []
        for sc in stops + proceed:
            act = V.vlm.SCENARIOS[sc]["expect"]
            tel = CF.synth(sc, fn)
            g = syn(sc, act, tel, "final")[2]
            (s_ if sc in stops else p_).append(S_stop(tel, {}, g))
        isi.append({"row": pn, "kind": "policy:" + ("held-out" if pn in held else "reference"), "P(stopped | STOP/HOLD)": round(sum(s_) / len(s_), 3),
                    "P(stopped | PROCEED)": round(sum(p_) / len(p_), 3), "ISI": round(sum(s_) / len(s_) - sum(p_) / len(p_), 3),
                    "graded_D": next(p["graded_D"] for p in pol if p["policy"] == pn)})
    wr("f2_instruction_sensitivity.csv", isi)
    # (a) deceleration onset relative to the directive onset, real STOP/HOLD passes
    dec = []
    for e in eps:
        if e["strict"] == "PASS" and e["kw"]["expected_action"] in ("STOP", "HOLD"):
            g = e["v"]["final"][2]
            on = ((g.get("evidence") or {}).get("active_window") or {}).get("onset_s") or 0.0
            rr = [r for r in e["rows"] if float(r["sim_time"]) <= 25.0]
            t_dec = G._first_decel_time(rr, 0.0)
            dec.append({"episode": e["id"], "onset_s": on, "first_decel_s": t_dec,
                        "decel_before_onset": t_dec is not None and t_dec < on - 1e-9})
    wr("f2_decel_onset_real_passes.csv", dec)
    # (c) no-officer green_stop runs vs the leaderboard green_stop run, same model
    noof = []
    lb_gs = {e["model"]: e for e in eps if e["scenario"] == "green_stop" and e["seed"] == 0}
    for e in val:
        if e["phase"] == "1_ab" and e["model"].endswith("_noofficer"):
            m = e["model"][:-len("_noofficer")]
            g = scored(e["res"], e["rows"], e["kw"], V.event_ids(e["ep"]), "final", e["forced_invalid"])[2]
            with_off = lb_gs.get(m)
            noof.append({"model": m, "seed_noofficer": e["seed"], "stopped_without_officer": S_stop(e["rows"], e["kw"], g),
                         "stopped_with_officer_s0": None if with_off is None else S_stop(with_off["rows"], with_off["kw"], with_off["v"]["final"][2]),
                         "credit_final_without_officer": scored(e["res"], e["rows"], e["kw"], V.event_ids(e["ep"]), "final", e["forced_invalid"])[0],
                         "credit_final_with_officer_s0": None if with_off is None else with_off["v"]["final"][0]})
    wr("f2_noofficer_green_stop.csv", noof)

    # ---- console ---------------------------------------------------------------------------------------------
    for d in disc:
        say(d)
    for c in cons:
        say(c)
    for m in mono:
        if m["violations"]:
            say("ordering violation:", m["family"], m["version"], m["steps"])
    say("policies above best non-priv: final", sum(p["graded_final"] > bestF for p in pol), "D", sum(p["graded_D"] > bestD for p in pol),
        "best", max(p["graded_final"] for p in pol), max(p["graded_D"] for p in pol))
    say("labels:", dict(collections.Counter(e["label"] for e in eps)))
    say("decel before onset among real passes:", sum(d["decel_before_onset"] for d in dec), "/", len(dec))
    (OUT / "stdout.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
