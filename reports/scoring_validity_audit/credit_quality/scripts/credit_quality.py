"""green_stop quality-credit candidates (Q-sep, Q-add, Q-gate) at reference equal weights; one table.

    python <audit>/credit_quality/scripts/credit_quality.py          # from the workspace root

Reads existing logs only (protocol-v2 green_stop runs, final InterFuser forward-speed re-measure s0-2, CARLA
green_stop fixtures). Writes credit_quality/data/green_stop_table.csv only. Strict verdicts are the published ones.

STOP components (each in [0, 1]):
  L  line:      min front-to-line d; 1 at d >= +1 m (strict margin), 0 at d <= -2 m (published clearance zero; no physical anchor)
  E  enforced:  max speed after onset+budget in the directive window; 1 at <= 1 km/h (strict stop speed), 0 at >= 12 km/h (published zero)
  K  braking:   1 - max_t a_req / A_MAX, a_req = v^2 / (2 (front - 1 m)); A_MAX = 4.48 m/s^2 (measured median full brake); moving inside 1 m -> 0
  R  reaction:  held-stop latency (F6h); 1 up to the 3 s budget, 0 at 8 s (published); no held stop -> 0; at rest never moved -> 1
  H  hold:      stopped share (<= 1 km/h) of the enforced window (directive-window anchor)
  C  contacts:  v2.2-final F_safe step (1 / 0.25 / 0.10 / 0; published bands, no physical anchor)
  M  movement:  travel >= 1 m (strict engagement progress)
  S  strict:    1 if strict PASS
Z (all candidates): crossed the line with no held stop in the directive window -> 0 (driving through a live STOP is not paid).
  Q-sep  = mean(L, E, K, R, H, C, M)            reported beside strict, never merged
  Q-add  = mean(L, E, K, R, H, C, M, S)         one merged number
  Q-gate = M * ceiling * mean(L, E, K, R, H, C), ceiling = min(0.35 if crossed [published conflict factor],
           0.25 if no held stop [published creep cap], F_safe if contacts)
Weights are equal reference weights, not decided.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUD = HERE.parents[1]
sys.path[:0] = [str(AUD / "credit_discrimination" / "scripts"), str(AUD / "credit_v22_final" / "scripts")]
import discrimination as D  # noqa: E402
import final_leaderboard as FL  # noqa: E402

F, X, V, G, S = D.F, D.X, D.V, D.G, D.S
SC = "green_stop"
A_MAX = 4.48
OUT = HERE.parent / "data"


def clip(x):
    return min(1.0, max(0.0, x))


def _a_req(rows, target):
    """Max required deceleration v^2 / (2 (front - target)) over moving rows -> (a, tick); moving at/inside target -> inf."""
    best = (0.0, None)
    for r in rows:
        v, fr = D._v(r), G._finite_float(r.get("stopline_front_m"))
        if v > D.STOP_KMH and fr is not None:
            a = float("inf") if fr <= target + 1e-9 else (v / 3.6) ** 2 / (2 * (fr - target))
            if a > best[0]:
                best = (a, (round(float(r["sim_time"]), 2), round(v, 2), round(fr, 2)))
    return best


def extra(rows_all, f):
    """Hold duration (longest <= 1 km/h span in the scored window after onset), K and K diagnostics."""
    on, end = f["onset_s"], f["scored_end_s"] or 25.0
    rows = [r for r in rows_all if on - 1e-9 <= float(r["sim_time"]) <= end + 1e-9]
    best = run = 0.0
    for p, r in zip(rows, rows[1:]):
        run = run + float(r["sim_time"]) - float(p["sim_time"]) if D._v(r) <= D.STOP_KMH and D._v(p) <= D.STOP_KMH else 0.0
        best = max(best, run)
    a, tick = _a_req(rows, 1.0)
    a_pre, _ = _a_req([r for r in rows_all if float(r["sim_time"]) <= end + 1e-9], 1.0)
    stop_pt = max(1.0, f["min_front_m"]) if f["min_front_m"] is not None and f["held_stop"] else 1.0
    a_stop, _ = _a_req(rows, stop_pt - 0.05) if stop_pt > 1.0 else (a, None)
    obs = 0.0   # observed deceleration over 0.3 s after onset
    for i in range(len(rows)):
        j = next((j for j in range(i, len(rows)) if float(rows[j]["sim_time"]) - float(rows[i]["sim_time"]) >= 0.3 - 1e-9), None)
        if j is None:
            break
        obs = max(obs, (D._v(rows[i]) - D._v(rows[j])) / 3.6 / (float(rows[j]["sim_time"]) - float(rows[i]["sim_time"])))
    k = lambda x: round(1 - clip(x / A_MAX), 4)  # noqa: E731
    return round(best, 2), k(a), {"a_req": round(a, 3), "a_req_tick (t, km/h, front m)": tick, "K_with_pre_onset": k(a_pre),
                                  "K_to_actual_stop_point": k(a_stop), "a_obs_max": round(obs, 2)}


def comps(e):
    """Q-gate inputs for a STOP/HOLD episode (e: res, rows, kw, ids, forced_invalid, strict, reason)."""
    st, sc = e["strict"], e["kw"]["scenario"]
    cF, kF, gF = F.scored(e["res"], e["rows"], e["kw"], e["ids"], "final", e["forced_invalid"])
    f = D.features(e["rows"], e["kw"], gF, kF)
    hold_s, K, kd = extra(e["rows"], f)
    d = f["min_front_m"] if f["min_front_m"] is not None else 0.0
    lat, trig = f["held_latency_s"], f["held_trigger"]
    c = {"L": clip((d + 2) / 3), "E": clip((12 - (f["max_enforced_kmh"] or 0.0)) / 11), "K": K,
         "R": 1.0 if trig == "at_rest_never_moved" else (0.0 if lat is None else clip((8 - max(lat, 3.0)) / 5)),
         "H": f["stopped_share_enforced"] or 0.0, "C": float(kF["F_safe"] or 0.0),
         "M": float(f["travel_m"] >= G.STRICT_THRESHOLDS["engagement_progress_m"]), "S": float(st == "PASS")}
    crossed = f["t_cross_s"] is not None
    return {"c": c, "f": f, "hold_s": hold_s, "kdiag": kd, "crossed": crossed, "held": bool(f["held_stop"]),
            "Z": crossed and f["t_hold_s"] is None and trig != "at_rest_never_moved", "invalid": bool(e["forced_invalid"]),
            "final": cF, "label": D.failure_label(e["kw"]["expected_action"], st, e["reason"], f, sc)}


KEYS = "LEKRHC"


def qgate(x, w=None, conflict=0.35, nohold=0.25):
    """Q-gate from comps(); w: weights over L, E, K, R, H, C (default equal)."""
    c = x["c"]
    if x["invalid"] or x["Z"]:
        return 0.0
    w = w or [1.0] * 6
    ceil = min([1.0] + ([conflict] if x["crossed"] else []) + ([nohold] if not x["held"] else [])
               + ([c["C"]] if x["f"]["contacts"] else []))
    return c["M"] * ceil * sum(wi * c[k] for wi, k in zip(w, KEYS)) / sum(w)


def score(e):
    x = comps(e)
    c, f = x["c"], x["f"]
    mean = lambda ks: sum(c[k] for k in ks) / len(ks)  # noqa: E731
    ok = not x["invalid"] and not x["Z"]
    q = {"Q_sep": mean("LEKRHCM") if ok else 0.0, "Q_add": mean("LEKRHCMS") if ok else 0.0, "Q_gate": qgate(x)}
    ceil = min([1.0] + ([0.35] if x["crossed"] else []) + ([0.25] if not x["held"] else []) + ([c["C"]] if f["contacts"] else []))
    return {"model": e["model"], "seed": e["seed"], "source": e["source"], "strict": e["strict"], "label": x["label"],
            "peak_kmh": f["peak_after_onset_kmh"], "travel_m": f["travel_m"], "min_front_m": f["min_front_m"],
            "held_latency_s": f["held_latency_s"], "hold_s": x["hold_s"], "enforced_max_kmh": f["max_enforced_kmh"], "contacts": f["contacts"],
            "v2": round(F.scored(e["res"], e["rows"], e["kw"], e["ids"], "v2", e["forced_invalid"])[0], 3),
            "v22_final": round(x["final"], 3), "v22_D": round(D.scored(e["res"], e["rows"], e["kw"], e["ids"], "D", e["forced_invalid"])[0], 3),
            **{k: round(v, 3) for k, v in q.items()}, "Z_drive_through": x["Z"], "Q_gate_ceiling": ceil,
            "components": " ".join(f"{k}{c[k]:.2f}" for k in "LEKRHCMS")}


def load(model_dir, model, seeds=None):
    saved = V.vlm.SCENARIO_ORDER
    V.vlm.SCENARIO_ORDER = [SC]
    try:
        return FL.capture_dir(model_dir, model, seeds)[0]
    finally:
        V.vlm.SCENARIO_ORDER = saved


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    eps = []
    for md in sorted(p for p in V.RUNS.iterdir() if p.is_dir() and not p.name.endswith("_cap3")):
        for e in load(md, md.name):
            e["source"] = "protocol-v2 (excluded: integration defect)" if md.name == "interfuser" else "protocol-v2"
            eps.append(e)
    for e in FL.check_speed(load(AUD.parents[1] / "tmp" / "_remeasure_interfuser" / "final_forward" / "interfuser", "interfuser", (0, 1, 2)), "forward"):
        e["source"] = "final forward-speed re-measure"
        eps.append(e)
    for e in V.capture_validation():
        if e["phase"] == "23_fixtures":
            e["source"] = "CARLA fixture"
            eps.append(e)
    rows = []
    for e in eps:
        e["ids"] = V.event_ids(e["ep"])
        s = S.score_episode_from_telemetry(e["res"], e["rows"], **e["kw"])
        e["strict"] = "INVALID" if e["forced_invalid"] else s.get("verdict")
        e["reason"] = s.get("reason")
        rows.append(score(e))
    F.OUT = OUT
    F.wr("green_stop_table.csv", rows)
    for r in rows:
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
