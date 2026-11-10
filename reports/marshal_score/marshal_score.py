"""MARSHAL Score -- candidate (one implementation for every scenario).

The score says how much of the scenario's REQUIRED behaviour was delivered, on one dimensionless scale.

Elements (each in [0, 1]; 1 = within the Strict tolerance; linear loss to 0 at a common full-loss scale):
  R  core result of the required behaviour (stop / start+entry / detour / yield / handoff ...). Required stages are
     sequence-gated: a later stage is credited at most as much as the stage it presupposes.
  B  forbidden-boundary compliance (stop line, hazard, handoff band, junction entry while a directive is live);
     only where the scenario has such a boundary.
  T  timing of the required response against max(3 s, budget); full loss 5 s later. No response -> 0.
  P  persistence: share of the required hold actually kept; only where a hold is required.
Timing of a later stage is credited only as far as the stage it presupposes was achieved.
Prerequisites (approach motion, and approach distance into the Strict engagement band, required before a STOP/HOLD/YIELD)
multiply R, T and P (a hold that was never
approached is not credited); B is never gated (staying out of a forbidden zone is real compliance).

Combination (config marshal_score.json):
  raw  = 100 * sum_i w_i e_i / sum_i w_i over the APPLICABLE elements only (no automatic 1 for absent elements)
  caps = 25 + 75 R ;  25 + 75 B (if B applies) ;  50 for one collision partner, 25 for two or more
  Strict PASS -> 100.  Strict FAIL -> min(raw, caps, 99.0).  INVALID / NOT_SCORED -> None (never 0).
Behaviour-type labels are never an input.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence

CFG = json.loads((Path(__file__).with_name("marshal_score.json")).read_text(encoding="utf-8"))
TOL = CFG["strict_tolerances_used"]
SC = CFG["common_full_loss_scales"]
WT = CFG["element_weights"]


def clip01(x: float) -> float:
    return 0.0 if x <= 0 else 1.0 if x >= 1 else x


def loss(excess: Optional[float], scale: float) -> float:
    """1 at excess <= 0, linear to 0 at excess >= scale. None -> 0 (requirement not shown)."""
    if excess is None or (isinstance(excess, float) and math.isnan(excess)):
        return 0.0
    return 1.0 - clip01(excess / scale)


def ratio(achieved: Optional[float], required: float) -> float:
    return 0.0 if achieved is None else clip01(achieved / required)


def stages(*f: float) -> float:
    """Sequence-gated mean: stage k counts at most min(stage k-1, own fulfilment)."""
    out, prev = [], 1.0
    for x in f:
        prev = min(prev, x)
        out.append(prev)
    return sum(out) / len(out)


def combine(el: Dict[str, Optional[float]], contacts: int, strict: str) -> Dict[str, Optional[float]]:
    """el: R, B, T, P in [0, 1] or None (not applicable). Returns score and its parts."""
    if strict not in ("PASS", "FAIL"):
        return {"score": None, "raw": None, "cap": None, "binding": "not scored"}
    for k, v in el.items():
        if v is not None and not (isinstance(v, (int, float)) and math.isfinite(v) and 0.0 <= v <= 1.0):
            raise ValueError(f"element {k}={v!r} is not a finite value in [0, 1]")
    if el.get("R") is None:
        raise ValueError("the core element R is required")
    app = {k: v for k, v in el.items() if v is not None}
    raw = 100.0 * sum(WT[k] * v for k, v in app.items()) / sum(WT[k] for k in app)
    caps = {"core": CFG["caps"]["core"]["base"] + CFG["caps"]["core"]["slope"] * el["R"]}
    if el.get("B") is not None:
        caps["boundary"] = CFG["caps"]["boundary"]["base"] + CFG["caps"]["boundary"]["slope"] * el["B"]
    if contacts >= 2:
        caps["contacts"] = CFG["caps"]["contacts"]["two_or_more_partners"]
    elif contacts == 1:
        caps["contacts"] = CFG["caps"]["contacts"]["one_partner"]
    cap_name, cap = min(caps.items(), key=lambda kv: kv[1])
    if strict == "PASS":
        return {"score": CFG["pass_score"], "raw": raw, "cap": cap, "binding": "strict PASS"}
    s = min(raw, cap, CFG["fail_ceiling"])
    binding = "raw" if s == raw else ("fail ceiling" if s == CFG["fail_ceiling"] else cap_name)
    s = math.floor(s * 10 ** CFG["display_decimals"]) / 10 ** CFG["display_decimals"]  # never rounds a FAIL up
    return {"score": s, "raw": raw, "cap": cap, "binding": binding}


# ---------------------------------------------------------------- telemetry helpers (rows = strict_telemetry rows)
def window(rows, t0, t1):
    return [r for r in rows if t0 - 1e-9 <= r["sim_time"] <= t1 + 1e-9]


def held_stop_start(rows: Sequence[dict], t0: float, t1: float) -> Optional[float]:
    """First start of >= 2.0 s with every tick <= 1 km/h and <= 0.5 m forward drift (Strict held-stop definition)."""
    w = window(rows, t0, t1)
    for i, r in enumerate(w):
        if r["ego_speed_kmh"] > TOL["stop_speed_kmh"]:
            continue
        lo = hi = r.get("ego_forward_m") or 0.0
        for q in w[i:]:
            x = q.get("ego_forward_m") or 0.0
            lo, hi = min(lo, x), max(hi, x)
            if q["ego_speed_kmh"] > TOL["stop_speed_kmh"] or hi - lo > TOL["held_stop_max_drift_m"]:
                break
            if q["sim_time"] - r["sim_time"] >= TOL["held_stop_min_s"] - 1e-9:
                return r["sim_time"]
    return None


def engagement(rows, t_end) -> float:
    w = window(rows, 0.0, t_end)
    vmax = max((r["ego_speed_kmh"] for r in w), default=0.0)
    xs = [r.get("ego_forward_m") for r in w if r.get("ego_forward_m") is not None]
    prog = (max(xs) - min(xs)) if xs else 0.0
    return ratio(vmax, TOL["engagement_speed_kmh"]) * ratio(prog, TOL["engagement_progress_m"])


def response_timing(t_resp: Optional[float], t_ref: float, budget: float) -> float:
    if t_resp is None:
        return 0.0
    return loss((t_resp - t_ref) - max(TOL["reaction_budget_floor_s"], budget), SC["time_s"])


# ---------------------------------------------------------------- per-requirement element builders
def approach_distance(rows, onset, end):
    """Strict engagement band: the ego must come within 16.59 m of the stop line or of the directing officer (stop-line
    STOP/HOLD), or of the hazard (no stop line). Returns (credit, gap beyond the band in m or None)."""
    act = window(rows, onset, end)
    band = TOL["stopline_engagement_m"]
    fwd = [r["stopline_forward_m"] for r in act if r.get("stopline_forward_m") is not None]
    dsl = [r["distance_to_stopline_m"] for r in act if r.get("distance_to_stopline_m") is not None]
    if fwd or dsl:
        d = min(fwd) if fwd else min(dsl)
        off = [r["distance_to_officer_m"] for r in act if r.get("distance_to_officer_m") is not None]
        if off:
            d = min(d, min(off))  # either anchor counts (Strict rule)
    else:
        hz = next((r["hazard_forward_m"] for r in rows if r.get("hazard_forward_m") is not None), None)
        xs = [r["ego_forward_m"] for r in act if r.get("ego_forward_m") is not None]
        if hz is None or not xs:
            return 1.0, None  # no anchor in the telemetry: nothing to require
        d, band = hz - max(xs), TOL["hazard_engagement_m"]
    gap = max(0.0, d - band)
    return loss(gap, SC["position_m"]), gap


def stop_hold(rows, onset, end, budget, boundary="stopline", hazard_key="hazard_forward_m"):
    """STOP / HOLD: core = stopped through enforcement (speed), B = boundary overrun, T = held-stop timing,
    P = share of enforcement kept at <= 1 km/h. Approach prerequisite (motion x distance) gates R, T, P."""
    E_motion = engagement(rows, end)
    A_dist, gap = approach_distance(rows, onset, end) if boundary is not None else (1.0, None)
    E = E_motion * A_dist
    enf = window(rows, onset + budget, end)  # no rows after the reaction deadline -> no enforced stop shown
    v_enf = max((r["ego_speed_kmh"] for r in enf), default=None)
    f_speed = loss(None if v_enf is None else v_enf - TOL["stop_speed_kmh"], SC["speed_kmh"])
    act = window(rows, onset, end)
    if boundary == "stopline":
        fr = [r["stopline_front_m"] for r in act if r.get("stopline_front_m") is not None]
        over = None if not fr else max(0.0, TOL["stopline_front_margin_m"] - min(fr))
    elif boundary == "hazard":
        hz = [r[hazard_key] for r in act if r.get(hazard_key) is not None]
        over = None if not hz else max(0.0, TOL["hazard_centre_margin_m"] - min(hz))
    else:
        over = None
    B = None if boundary is None else (loss(over, SC["position_m"]) if over is not None else None)
    v0 = act[0]["ego_speed_kmh"] if act else 0.0
    t_ref = onset
    if v0 <= TOL["stop_speed_kmh"]:  # at rest at onset: timed from its first launch after onset (Strict/graded rule)
        t_ref = next((r["sim_time"] for r in act if r["ego_speed_kmh"] > TOL["stop_speed_kmh"]), onset)
    t_hold = held_stop_start(rows, t_ref, end)
    T = response_timing(t_hold, t_ref, budget)
    P = (sum(r["ego_speed_kmh"] < TOL["stop_speed_kmh"] for r in enf) / len(enf)) if enf else 0.0
    fmin = min((r["stopline_front_m"] for r in act if r.get("stopline_front_m") is not None), default=None)
    obs = {"engagement": E, "approach_motion": E_motion, "approach_distance": A_dist, "approach_gap_beyond_band_m": gap,
           "v_enforcement_max_kmh": v_enf, "min_front_m": fmin, "boundary_overrun_m": over, "held_stop_start_s": t_hold,
           "timing_ref_s": t_ref, "hold_share": P}
    return {"R": E * f_speed, "B": B, "T": E * T, "P": E * P}, obs


def proceed_entry(rows, t_ref, x_entry, end=25.0):
    """Completion = a moving (>= 2 km/h) junction row past the line after t_ref (Strict PROCEED completion).
    Not completed: at most half credit, scaled by the remaining distance to the scenario's junction entry point on the
    common position scale (x_entry = map geometry, measured from telemetry, identical for every model)."""
    after = window(rows, t_ref, end)
    t_start = next((r["sim_time"] for r in after if r["ego_speed_kmh"] >= TOL["proceed_start_speed_kmh"]), None)
    entered = any(r.get("in_junction") and r.get("stopline_front_m") is not None and r["stopline_front_m"] < 0
                  and r["ego_speed_kmh"] >= TOL["proceed_start_speed_kmh"] for r in after)
    xmax = max((r["ego_forward_m"] for r in after if r.get("ego_forward_m") is not None), default=None)
    remaining = None if (xmax is None or x_entry is None) else max(0.0, x_entry - xmax)
    entry = 1.0 if entered else 0.5 * loss(remaining, SC["position_m"])
    return {"entry": entry, "t_start": t_start, "entered": entered, "remaining_to_entry_m": remaining}


def live_entry_overrun(rows, t0, t1) -> float:
    """Depth (m) past the line while inside the junction during [t0, t1]; 0 if never."""
    d = [-r["stopline_front_m"] for r in window(rows, t0, t1)
         if r.get("in_junction") and r.get("stopline_front_m") is not None and r["stopline_front_m"] < 0]
    return max(d) if d else 0.0


def proceed(rows, t_safe, budget, x_entry):
    p = proceed_entry(rows, t_safe, x_entry)
    return {"R": p["entry"], "B": None, "T": response_timing(p["t_start"], t_safe, budget), "P": None}, p


def proceed_with_care(rows, onset, t_safe, v_min_near_ped, budget, x_entry):
    p = proceed_entry(rows, t_safe, x_entry)
    # Waiting for the pedestrian is the required first stage, so no approach prerequisite gates it; the motion the
    # Strict rule requires belongs to the second stage (entry).
    y = loss(None if v_min_near_ped is None else v_min_near_ped - TOL["proceed_care_yield_speed_kmh"], SC["speed_kmh"])
    B = loss(live_entry_overrun(rows, onset, t_safe), SC["position_m"])
    # the start timing belongs to the stage after the yield: credited only as far as the yield was achieved
    return ({"R": stages(y, p["entry"]), "B": B, "T": y * response_timing(p["t_start"], t_safe, budget), "P": None},
            {**p, "yield": y})


def proceed_after_release(rows, onset, release, budget, x_entry):
    live = window(rows, onset + 2.0, release)
    dwell = 0.0
    for i, r in enumerate(live):  # settled dwell: <= 3 km/h for >= 2 s with <= 0.5 m drift
        if r["ego_speed_kmh"] > TOL["dwell_speed_kmh"]:
            continue
        lo = hi = r.get("ego_forward_m") or 0.0
        for q in live[i:]:
            x = q.get("ego_forward_m") or 0.0
            lo, hi = min(lo, x), max(hi, x)
            if q["ego_speed_kmh"] > TOL["dwell_speed_kmh"] or hi - lo > TOL["dwell_max_drift_m"]:
                break
            if q["sim_time"] - r["sim_time"] >= TOL["dwell_min_s"] - 1e-9:
                dwell = 1.0
                break
        if dwell:
            break
    if not dwell:  # no settled dwell: an incomplete discrete stage earns at most 0.5 (same rule as PROCEED completion)
        vmin = min((r["ego_speed_kmh"] for r in live), default=None)
        dwell = 0.5 * loss(None if vmin is None else vmin - TOL["dwell_speed_kmh"], SC["speed_kmh"])
    # Holding while the STOP is live is the required first stage (staying put is correct); motion belongs to entry.
    p = proceed_entry(rows, release, x_entry)
    B = loss(live_entry_overrun(rows, onset, release), SC["position_m"])
    # the post-release start belongs to the stage after the dwell: credited only as far as the dwell was achieved
    return ({"R": stages(dwell, p["entry"]), "B": B, "T": dwell * response_timing(p["t_start"], release, budget), "P": None},
            {**p, "dwell": dwell})


def detour(rows, onset, obstacle_x, budget):
    after = window(rows, onset, 25.0)
    lat = max((abs(r["ego_lateral_m"]) for r in after if r.get("ego_lateral_m") is not None), default=0.0)
    xmax = max((r["ego_forward_m"] for r in after if r.get("ego_forward_m") is not None), default=0.0)
    f_lat = ratio(lat, TOL["detour_lateral_m"])
    f_pass = clip01((xmax - obstacle_x) / TOL["detour_pass_margin_m"]) if obstacle_x is not None else 0.0  # past the obstacle only
    t_lat = next((r["sim_time"] for r in after if r.get("ego_lateral_m") is not None
                  and abs(r["ego_lateral_m"]) >= TOL["detour_response_lateral_m"]), None)
    return ({"R": stages(f_lat, f_pass), "B": None, "T": response_timing(t_lat, onset, budget), "P": None},
            {"max_abs_lateral_m": lat, "max_forward_m": xmax, "obstacle_forward_m": obstacle_x, "t_lateral_s": t_lat})


def yield_(v_pre, v_min, lat_after, v_resume, t_slow, onset, budget):
    E = ratio(v_pre, TOL["yield_pre_speed_kmh"])
    slow = loss(None if v_min is None else v_min - TOL["yield_slow_speed_kmh"], SC["speed_kmh"])
    lat = ratio(lat_after, TOL["yield_lateral_m"])
    res = ratio(v_resume, TOL["yield_resume_speed_kmh"])
    R = E * (stages(slow, lat) + stages(slow, res)) / 2  # lateral clear and resume both presuppose the slowdown
    return ({"R": R, "B": None, "T": E * response_timing(t_slow, onset, budget), "P": None},
            {"approach": E, "slow": slow, "lateral": lat, "resume": res})


def handoff(rows, onset, end, budget, req: dict):
    """dual_authority_handoff: transit the flagger -> reach the officer -> stop in the band past the flagger.
    Every element is recomputed from telemetry against the Strict handoff requirement on the common scales."""
    rows = window(rows, 0.0, end)  # protocol v2: a STOP/HOLD is scored on the rows up to the end of the directive only
    base, obs = stop_hold(rows, onset, end, budget, boundary=None)
    act, enf = window(rows, onset, end), window(rows, onset + budget, end)
    pos, spd = SC["position_m"], SC["speed_kmh"]
    hz = [r["distance_to_hazard_m"] for r in rows if r.get("distance_to_hazard_m") is not None]
    off = [r["distance_to_officer_m"] for r in act if r.get("distance_to_officer_m") is not None]
    transit = loss(min(hz) - req["hazard_pass_m"] if hz else None, pos)
    officer = loss(min(off) - req["officer_pass_m"] if off else None, pos)
    hf = next((r["hazard_forward_m"] for r in rows if r.get("hazard_forward_m") is not None), None)
    afw = [r["ego_forward_m"] for r in act if r.get("ego_forward_m") is not None]
    efw = [r["ego_forward_m"] for r in enf if r.get("ego_forward_m") is not None]
    if hf is None or not afw or not efw:
        band = hold = over = yoyo = 0.0
    else:
        bmin, bmax, g = hf + req["stop_band_past_hazard_min_m"], hf + req["stop_band_past_hazard_max_m"], req["overshoot_grace_m"]
        band = loss(max(0.0, bmin - min(efw), max(efw) - bmax), pos)
        hold = loss(max(efw) - min(efw) - req["hold_displacement_max_m"], pos)
        over = loss(max(afw) - (bmax + g), pos)
        k = next((i for i, v in enumerate(afw) if v >= bmin), None)
        yoyo = 1.0 if k is None else loss((bmin - g) - min(afw[k:]), pos)
    zs = [r["ego_speed_kmh"] for r in rows if r.get("distance_to_hazard_m") is not None and r["distance_to_hazard_m"] <= req["zone_radius_m"]]
    zone = 1.0 if not zs else loss(max(zs) - req["zone_speed_cap_kmh"], spd)
    lats = [abs(r["ego_lateral_m"]) for r in enf if r.get("ego_lateral_m") is not None]
    lateral = loss(max(lats) - req["hold_lateral_max_m"] if lats else None, pos)
    v_enf = max((r["ego_speed_kmh"] for r in enf), default=None)
    f_speed = loss(None if v_enf is None else v_enf - TOL["stop_speed_kmh"], spd)
    E = obs["engagement"]
    R = E * stages(transit, min(officer, band), f_speed)
    B = min(over, yoyo, zone, lateral)
    parts = {"transit": transit, "officer": officer, "band": band, "hold_settled": hold, "overshoot": over, "yoyo": yoyo,
             "zone_speed": zone, "lateral_hold": lateral, "max_zone_speed_kmh": max(zs) if zs else None}
    # the held-stop timing presupposes the transit and officer/band stages
    return {"R": R, "B": B, "T": min(transit, officer, band) * base["T"], "P": E * hold}, {**obs, "handoff_elements": parts}
