"""Collect and score protocol-v2 episodes into the v2 leaderboard.

    python scripts/collect_protocol_v2.py [--runs tmp/_protocol_v2] [--out outputs/protocol_v2]

Only episodes whose result.json / strict_scoring.json declare protocol_version "v2" are accepted; anything else is
refused, so v1 and v2 results can never be mixed. Every episode is re-scored with the current strict and graded
scorers under protocol v2. For each model the graded suite score is computed per seed (100 * sum_s w_s c_s / sum_s w_s
over the 25 scenarios of that seed) and reported as mean and sample SD across seeds; strict passes are reported per
cell as PASS/n and per seed as a count out of 25.

Writes <out>/cells_v2.csv (one row per episode), <out>/leaderboard_v2.csv, <out>/leaderboard_v2.json and,
when present, <out>/vlm_query_cap_green_stop_v2.csv (green_stop, uncapped vs 3-query cap, seeds 0-4).
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS))
sys.path.insert(0, str(THIS.parent))

from marshal_bench.criteria import graded_episode_scoring as G  # noqa: E402
from marshal_bench.criteria import strict_episode_scoring as S  # noqa: E402
from marshal_bench.criteria.marshal_metrics import CONFLICT_TYPE, CONFLICT_TYPE_ORDER  # noqa: E402

VLM_MODELS = ("glm-4.5v", "qwen2.5-vl", "qwen3-vl")
REQUIRED_SEEDS = {**{m: tuple(range(5)) for m in VLM_MODELS}, "interfuser": (0, 1, 2)}  # every other model: seed 0
# Leaderboard InterFuser adapter: original leaderboard sensors and route sampling, forward speed input. Episodes whose
# metadata records anything else are refused, so runs of different adapter settings are never mixed in one row.
INTERFUSER_FINAL = {"speed_input": "forward", "route_sample_m": 50, "sensor_profile": "leaderboard",
                    "step_on_sensor_data": True, "hold_odd_step_control": True}
TRACK = {"oracle": "A", "baseline": "-", "glm-4.5v": "C", "qwen2.5-vl": "C", "qwen3-vl": "C"}


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _decision_times(ep: Path):
    trace = ep / "vlm_debug" / "vlm_trace.csv"
    if not trace.exists():
        return None
    with trace.open(encoding="utf-8") as fh:
        return [float(r["sim_time"]) for r in csv.DictReader(fh) if r.get("queried_this_tick") == "True"]


def _cell(name: str):
    """Model-directory name -> (base model, ablation level or None, query period s, capped)."""
    base, _, rest = name.partition("_abl-")
    level, period = (rest.split("_qp") + [None])[:2] if rest else (None, None)
    base = base.replace("_cap3", "")
    return base, level, float(period) if period else 1.5, name.endswith("_cap3")


def _schedule_problem(dts, capped: bool, period: float = 1.5):
    """v2 VLM schedule: first query at 0.05 s, then every `period` s (one tick of jitter), to the horizon unless capped at 3."""
    if not dts:
        return "no query trace (vlm_debug/vlm_trace.csv)"
    if dts[0] > 0.1 + 1e-9:
        return f"first query at {dts[0]:.2f} s"
    gaps = [b - a for a, b in zip(dts, dts[1:])]
    if any(not period - 0.05 <= g <= period + 0.05 + 1e-6 for g in gaps):
        return f"query intervals {sorted({round(g, 2) for g in gaps})}"
    if capped and len(dts) != 3:
        return f"{len(dts)} queries with the 3-query cap"
    if not capped and dts[-1] < S.PROTOCOL_V2["horizon_s"] - period - 0.05 - 1e-6:
        return f"last query at {dts[-1]:.2f} s (uncapped schedule must run to the horizon)"
    return None


def interfuser_config_problem(meta) -> str | None:
    """Reason an InterFuser episode was not run with INTERFUSER_FINAL (None when it was)."""
    cfg = ((meta or {}).get("config") or {}).get("interfuser") or {}
    bad = {k: cfg.get(k) for k, v in INTERFUSER_FINAL.items() if cfg.get(k) != v}
    return f"InterFuser adapter settings differ from the leaderboard adapter: {bad}" if bad else None


def _collision_identities(ep: Path, tel):
    """(actor id or None, type id) of every counted collision event, in callback order: the type from events.json
    (every event), the actor id from strict_telemetry metadata.collisions (first 20 events only). Returns None when
    the two logs disagree; without events.json only the metadata events are identified."""
    md = ((tel.get("metadata") if isinstance(tel, dict) else None) or {}).get("collisions") or []
    events = _json(ep / "events.json")
    if not isinstance(events, list):
        return [(m.get("other_actor_id"), m.get("other_type_id")) for m in md]
    types = [e["payload"].get("other_actor") for e in events if isinstance(e, dict) and e.get("name") == "collision"]
    if any(i >= len(types) or types[i] != m.get("other_type_id") for i, m in enumerate(md)):
        return None
    return [(md[i].get("other_actor_id") if i < len(md) else None, t) for i, t in enumerate(types)]


def _flat(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            out.update(_flat(v, prefix + k + "."))
        else:
            out[prefix + k] = v
    return out


def score_episode(ep: Path, scenario: str, registry, is_vlm: bool = False, is_cap: bool = False,
                  period: float = 1.5) -> dict:
    tel = _json(ep / "strict_telemetry.json") or {}
    rows = tel.get("telemetry") if isinstance(tel, dict) else tel
    res = _json(ep / "result.json") or {}
    res = res.get("result", res) if isinstance(res, dict) else {}
    if not res:  # VLM runner: per-episode rows live in the run-level _results.json
        rows_ = _json(ep.parent / "_results.json") or []
        res = next((r for r in rows_ if isinstance(r, dict) and Path(str(r.get("episode_dir", ""))).name == ep.name), {})
    meta = _json(ep / "metadata.json") or {}
    stored = _json(ep / "strict_scoring.json") or {}
    # Fail closed: every artifact that records a protocol must say v2, and at least one must.
    versions = {k: v for k, v in (("result", res.get("protocol_version")), ("strict_scoring", stored.get("protocol_version")),
                                  ("metadata", (meta.get("config") or {}).get("protocol_version"))) if v is not None}
    if not versions or set(versions.values()) != {"v2"}:
        return {"accepted": False, "reason": f"protocol_version={versions!r}"}
    if ep.parent.parent.name.replace("_cap3", "") == "interfuser":  # layout <runs>/<model>/s<seed>/<episode>
        bad = interfuser_config_problem(meta)
        if bad:
            return {"accepted": False, "reason": bad}
    t_end = float(rows[-1]["sim_time"]) if rows else 0.0
    ts = [float(r["sim_time"]) for r in rows or []]
    if ts and (ts[0] > 0.1 + 1e-9 or max(b - a for a, b in zip(ts, ts[1:])) > 0.05 + 1e-6):
        return {"accepted": False, "reason": "telemetry has a gap (first tick or a tick interval > 0.05 s)"}
    if t_end < S.PROTOCOL_V2["horizon_s"] - 1e-6:  # v2 episodes never end early (collisions included)
        return {"accepted": False, "reason": f"episode ended at {t_end:.2f} s before the {S.PROTOCOL_V2['horizon_s']} s horizon "
                                             f"(terminated={res.get('terminated_reason')!r})"}
    budget = ((res.get("ground_truth") or {}).get("max_reaction_time_sec")
              or ((meta.get("config") or {}).get("expected_behavior") or {}).get("max_reaction_time_sec") or 3.0)
    dts = _decision_times(ep)
    if is_vlm:
        bad = _schedule_problem(dts, capped=is_cap, period=period)
        if bad:
            return {"accepted": False, "reason": f"VLM query schedule: {bad}"}
    kw = dict(scenario=scenario, expected_action=registry[scenario]["expect"], max_reaction_time=budget,
              setup_errors=res.get("setup_errors") or (), controller_errors=res.get("controller_errors") or (),
              protocol="v2", decision_times=dts)
    inner = dict(res or {"scenario": scenario})
    s = S.score_episode_from_telemetry(inner, rows or [], **kw)
    ids = _collision_identities(ep, tel)
    if ids is None:
        return {"accepted": False, "reason": "collision events differ between events.json and strict_telemetry metadata"}
    g = G.score_episode_from_telemetry(inner, rows or [], **kw, collision_identities=ids)
    if stored.get("verdict") == "INVALID" and s.get("verdict") != "INVALID":
        # errors recorded in-episode (e.g. controller-step exceptions) are not all re-loadable; keep INVALID
        s = {**s, "verdict": "INVALID", "reason": "in-episode scoring was INVALID: " + str(stored.get("reason"))}
        g = {**g, "credit": 0.0}
    ev = s.get("evidence") or {}
    ev = {**(ev.get("proceed_start") or {}), **ev}
    rf = s.get("release_followup") or {}
    gev = g.get("evidence") or {}
    mv = gev.get("movement_gate") or {}
    gstop = gev.get("engagement") or {}
    return {"accepted": True, "strict": s.get("verdict"), "strict_reason": s.get("reason"), "credit": g.get("credit"),
            # credit = action_credit x latency_factor x safety_factor x movement_gate (0 for an INVALID episode)
            "graded_invalid": bool(g.get("invalid")) or s.get("verdict") == "INVALID",
            "action_credit": g.get("raw_action_credit"), "latency_factor": g.get("latency_factor"),
            "safety_factor": g.get("safety_factor"), "movement_gate": int(bool(mv.get("engaged"))) if mv else None,
            "stored_strict": stored.get("verdict"), "rows": len(rows or []),
            "t_end_s": float(rows[-1]["sim_time"]) if rows else None, "terminated": res.get("terminated_reason"),
            "min_front_to_stopline_m": ev.get("min_front_to_stopline_m"),
            "failure_class": ev.get("failure_class"), "t_safe_s": ev.get("t_safe_s"), "start_s": ev.get("start_s"),
            "max_speed_to_directive_end_kmh": mv.get("max_speed_kmh"), "progress_to_directive_end_m": mv.get("forward_progress_m"),
            "stop_engagement": json.dumps(gstop, sort_keys=True) if gstop else None,
            "queries": len(kw["decision_times"]) if kw["decision_times"] is not None else None,
            "release_start_within_3s": rf.get("started_within_3s"), "release_entered": rf.get("entered_junction_after_release"),
            "release_collision": rf.get("collision_after_release")}


def find_episode(run_dir: Path, scenario: str):
    for p in sorted(run_dir.iterdir()) if run_dir.exists() else []:
        if p.is_dir() and (p / "strict_telemetry.json").exists():
            name = p.name.rsplit("_s", 1)[0] if p.name.rsplit("_s", 1)[-1].isdigit() else p.name
            if name.endswith(scenario):
                return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default="tmp/_protocol_v2")
    ap.add_argument("--out", default="outputs/protocol_v2")
    a = ap.parse_args()
    import _run_vlm_test as vlm
    scen = list(vlm.SCENARIO_ORDER)
    runs, out = Path(a.runs), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    weights = {sc: G.SCENARIO_AUTHORITY_WEIGHTS.get(G.canonical_scenario_name(sc), 1.0) for sc in scen}
    cells, refused = [], []
    for model_dir in sorted(p for p in runs.iterdir() if p.is_dir()) if runs.exists() else []:
        for seed_dir in sorted(p for p in model_dir.iterdir() if p.is_dir() and p.name.startswith("s")):
            for sc in scen:
                ep = find_episode(seed_dir, sc)
                if ep is None:
                    continue
                base, _lvl, period, capped = _cell(model_dir.name)
                r = score_episode(ep, sc, vlm.SCENARIOS, is_vlm=base in VLM_MODELS, is_cap=capped, period=period)
                if not r.pop("accepted"):
                    refused.append({"episode": str(ep), **r})
                    continue
                cells.append({"model": model_dir.name, "seed": int(seed_dir.name[1:]), "scenario": sc,
                              "family": CONFLICT_TYPE.get(sc), "expected_action": vlm.SCENARIOS[sc]["expect"],
                              "weight": weights[sc], "episode": str(ep).replace("\\", "/"), **r})
    with (out / "cells_v2.csv").open("w", encoding="utf-8", newline="") as fh:
        if cells:
            w = csv.DictWriter(fh, fieldnames=list(cells[0].keys()))
            w.writeheader()
            w.writerows(cells)
    board = []
    by_model = defaultdict(list)
    for c in cells:
        by_model[c["model"]].append(c)
    for model, cs in sorted(by_model.items()):
        seeds = sorted({c["seed"] for c in cs})
        required = REQUIRED_SEEDS.get(model.replace("_cap3", ""), (0,))
        per_seed_graded, per_seed_pass = [], []
        complete_seeds = []
        for sd in seeds:
            sc_cells = {c["scenario"]: c for c in cs if c["seed"] == sd}
            if sd not in required or len(sc_cells) < len(scen):
                continue
            complete_seeds.append(sd)
            per_seed_graded.append(100.0 * sum(weights[s] * float(sc_cells[s]["credit"] or 0) for s in scen) / sum(weights.values()))
            per_seed_pass.append(sum(sc_cells[s]["strict"] == "PASS" for s in scen))
        missing = [sd for sd in required if sd not in complete_seeds]
        if missing:  # a model row is published only with its full required seed set
            per_seed_graded, per_seed_pass, complete_seeds = [], [], []
        n = len(complete_seeds)
        cell_pass = {s: sum(1 for c in cs if c["scenario"] == s and c["strict"] == "PASS" and c["seed"] in complete_seeds) for s in scen}
        fam = {f: sum(cell_pass[s] for s in scen if CONFLICT_TYPE.get(s) == f) for f in CONFLICT_TYPE_ORDER}
        fam_n = {f: n * sum(1 for s in scen if CONFLICT_TYPE.get(s) == f) for f in CONFLICT_TYPE_ORDER}
        board.append({
            "model": model, "track": TRACK.get(model.replace("_cap3", ""), "B"), "seeds": n,
            "graded_mean": round(st.mean(per_seed_graded), 2) if per_seed_graded else None,
            "graded_sd": round(st.stdev(per_seed_graded), 2) if len(per_seed_graded) > 1 else None,
            "strict_pass_mean": round(st.mean(per_seed_pass), 2) if per_seed_pass else None,
            "strict_pass_sd": round(st.stdev(per_seed_pass), 2) if len(per_seed_pass) > 1 else None,
            "strict_pass_total": f"{sum(per_seed_pass)}/{25 * n}" if n else None,
            "override_cells": f"{fam['override'] + fam['stressed-override']}/{fam_n['override'] + fam_n['stressed-override']}" if n else None,
            "profile": ";".join(f"{fam[f]}/{fam_n[f]}" for f in CONFLICT_TYPE_ORDER) if n else None,
            "per_seed_graded": ";".join(f"{x:.2f}" for x in per_seed_graded),
            "incomplete_seeds": ";".join(str(sd) for sd in seeds if sd not in complete_seeds),
            "missing_required_seeds": ";".join(map(str, missing)),
            "complete": not missing,
            "strict_pass_by_scenario": ";".join(f"{s}={cell_pass[s]}/{n}" for s in scen) if n else None,
        })
    # cap-3 and ablation cells are not leaderboard rows (they feed the comparison / ablation table below)
    board = [r for r in board if not r["model"].endswith("_cap3") and "_abl-" not in r["model"]]
    board.sort(key=lambda r: (not r["complete"], -(r["graded_mean"] or -1)))
    with (out / "leaderboard_v2.csv").open("w", encoding="utf-8", newline="") as fh:
        if board:
            w = csv.DictWriter(fh, fieldnames=list(board[0].keys()))
            w.writeheader()
            w.writerows(board)
    (out / "leaderboard_v2.json").write_text(json.dumps({"protocol_version": "v2", "weights": weights,
                                                         "leaderboard": board, "refused": refused}, indent=1), encoding="utf-8")
    # green_stop query-cap comparison (same v2 conditions, seeds 0-4)
    comp = []
    for m in ("glm-4.5v", "qwen2.5-vl", "qwen3-vl"):
        pair = {key: {c["seed"]: c for c in cells if c["model"] == key and c["scenario"] == "green_stop"}
                for key in (m, m + "_cap3")}
        paired = sorted(set(pair[m]) & set(pair[m + "_cap3"]) & set(range(5)))
        if paired != list(range(5)):
            print(f"  query-cap comparison for {m}: paired seeds {paired}, need 0-4 - not reported")
            continue
        bad = [c["episode"] for c in pair[m + "_cap3"].values() if (c["queries"] or 0) > 3]
        bad += [c["episode"] for c in pair[m].values() if (c["queries"] or 0) <= 3]
        for sd in paired:  # identical configuration apart from the episode id and the query cap
            ca = _flat((_json(Path(pair[m][sd]["episode"]) / "metadata.json") or {}).get("config"))
            cb = _flat((_json(Path(pair[m + "_cap3"][sd]["episode"]) / "metadata.json") or {}).get("config"))
            diff = {k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k)} - {"episode_id", "vlm.max_queries"}
            if diff or cb.get("vlm.max_queries") != 3 or ca.get("vlm.max_queries") is not None:
                bad.append(f"{pair[m + '_cap3'][sd]['episode']} (config differs: {sorted(diff) or 'max_queries'})")
        if bad:
            raise SystemExit(f"query-cap comparison: wiring mismatch in {bad}")
        for label, key in (("every 1.5 s (no cap)", m), ("3-query cap", m + "_cap3")):
            g = [pair[key][sd] for sd in paired]
            if g:
                cr = [float(c["credit"] or 0) for c in g]
                comp.append({"model": m, "wiring": label, "runs": len(g), "seeds": ";".join(map(str, paired)),
                             "queries_mean": round(st.mean(c["queries"] for c in g), 2), "strict_pass": f"{sum(c['strict'] == 'PASS' for c in g)}/{len(g)}",
                             "credit_mean": round(st.mean(cr), 3), "credit_sd": round(st.stdev(cr), 3) if len(cr) > 1 else None})
    if comp:
        with (out / "vlm_query_cap_green_stop_v2.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(comp[0].keys()))
            w.writeheader()
            w.writerows(comp)
    # oracle-assist ablation cells (run_protocol_v2.py --ablation-study): strict count, freezes, graded per cell
    abl = []
    for name in sorted({c["model"] for c in cells if "_abl-" in c["model"]}):
        base, level, period, _ = _cell(name)
        cs = [c for c in cells if c["model"] == name]
        abl.append({"cell": name, "model": base, "level": level, "query_period_s": period if base in VLM_MODELS
                    else (period if "_qp" in name else 3.0), "episodes": len(cs),
                    "strict_pass": sum(c["strict"] == "PASS" for c in cs),
                    "freezes": sum("never engaged" in str(c["strict_reason"]) for c in cs),
                    "graded": round(100.0 * sum(weights[c["scenario"]] * float(c["credit"] or 0) for c in cs)
                                    / sum(weights[c["scenario"]] for c in cs), 2) if cs else None,
                    "pass_set": ";".join(sorted(c["scenario"] for c in cs if c["strict"] == "PASS"))})
    if abl:
        with (out / "ablation_v2.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(abl[0].keys()))
            w.writeheader()
            w.writerows(abl)
        for r in abl:
            print(f"  {r['cell']:32s} {r['strict_pass']:2d}/{r['episodes']} freezes={r['freezes']} graded={r['graded']}")
    print(f"v2: {len(cells)} episodes accepted, {len(refused)} refused; {len(board)} models")
    for r in board:
        print(f"  {r['model']:16s} seeds={r['seeds']} graded={r['graded_mean']} sd={r['graded_sd']} strict={r['strict_pass_mean']} ({r['strict_pass_total']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
