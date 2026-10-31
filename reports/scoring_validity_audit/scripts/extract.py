import json, sys, os, copy, yaml
REPO = r"."
RUNS = r"tmp/_protocol_v2/oracle/s0"
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import published_v2  # noqa: E402,F401  (pins the published v2 graded scorer; see published_v2.py)
from marshal_bench.criteria import strict_episode_scoring as S, graded_episode_scoring as G
sys.path.insert(0, os.path.join(REPO, "scripts"))
src = open(os.path.join(REPO, "scripts/_run_vlm_test.py"), encoding="utf-8").read()
ns = {}
import re
blk = src[src.index("SCENARIOS: Dict"):src.index("SMOKE_SCENARIOS")]
exec("from typing import Dict\n" + blk, ns)
out = {}
for sc in ns["SCENARIO_ORDER"]:
    d = os.path.join(RUNS, f"reference_oracle_{sc}")
    res = json.load(open(os.path.join(d, "result.json")))["result"]
    tj = json.load(open(os.path.join(d, "strict_telemetry.json")))
    rows = tj["telemetry"]
    gt = res.get("ground_truth") or {}
    r0 = rows[0]
    phases = []
    for r in rows:
        k = (r.get("officer_gesture_id"), r.get("officer_onset_time"), r.get("officer_duration_sec"))
        if not phases or phases[-1][0] != k:
            phases.append((k, r["sim_time"]))
    act_first = next((r["sim_time"] for r in rows if r.get("officer_active")), None)
    act_last = max((r["sim_time"] for r in rows if r.get("officer_active")), default=None)
    action = res["expected_action"]
    norm, _ = S._normalise_rows(S._horizon_raw(rows))
    win = S.v2_windows(norm, action)
    onset = win[2][0]
    tsafe = S.safe_to_proceed_time(win[0], res["scenario"], onset) if action == "PROCEED" else None
    # parked-at-spawn counterfactual
    parked = []
    for r in rows:
        p = dict(r)
        for key in ("ego_speed_kmh", "ego_x", "ego_y", "ego_forward_m", "ego_lateral_m", "in_junction",
                    "distance_to_stopline_m", "stopline_forward_m", "stopline_front_m", "distance_to_officer_m",
                    "distance_to_hazard_m"):
            if key in r0:
                p[key] = r0[key]
        p["ego_speed_kmh"] = 0.0
        p["collision_count"] = 0
        parked.append(p)
    mrt = gt.get("max_reaction_time_sec")
    sp = S.score_episode_from_telemetry(res, parked, scenario=res["scenario"], expected_action=action,
                                        max_reaction_time=mrt, protocol="v2")
    gp = G.score_episode_from_telemetry(res, parked, scenario=res["scenario"], expected_action=action,
                                        max_reaction_time=mrt, protocol="v2")
    ss = res.get("strict_scoring") or {}
    cfg = yaml.safe_load(open(os.path.join(REPO, ns["SCENARIOS"][sc]["config"]), encoding="utf-8"))
    out[sc] = dict(
        module=ns["SCENARIOS"][sc]["module"], config=ns["SCENARIOS"][sc]["config"],
        runner_expect=ns["SCENARIOS"][sc]["expect"], result_expect=action,
        yaml_expected=cfg.get("expected_behavior"), yaml_scene=cfg.get("scene"),
        yaml_second=cfg.get("second_authority"), yaml_timeout=cfg.get("timeout_sec"),
        yaml_weather=cfg.get("weather") or (cfg.get("environment") or {}).get("weather"),
        gt={k: gt.get(k) for k in ("L_light_state", "A_authority", "G_gesture", "G_gesture_onset_sec",
                                   "G_gesture_duration_sec", "A_second_authority", "T_target_relation",
                                   "S_safety_context", "V_visibility", "W_weather", "max_reaction_time_sec",
                                   "target_speed_kmh", "Y_expected_action")},
        tl=res.get("traffic_light_state"),
        r0={k: r0.get(k) for k in ("sim_time", "ego_speed_kmh", "stopline_front_m", "stopline_forward_m",
                                   "distance_to_stopline_m", "distance_to_officer_m", "distance_to_hazard_m",
                                   "hazard_forward_m", "hazard_lateral_m", "in_junction")},
        dt=rows[1]["sim_time"] - rows[0]["sim_time"], last_t=rows[-1]["sim_time"], n=len(rows),
        phases=phases, officer_active_first=act_first, officer_active_last=act_last,
        v2_window=win[2], tsafe=tsafe,
        oracle_strict=ss.get("verdict"), oracle_reason=ss.get("reason"),
        parked_strict=(sp["verdict"], sp["reason"]), parked_graded=gp.get("credit"),
        speed_at_onset=next((r["ego_speed_kmh"] for r in rows if r["sim_time"] >= onset - 1e-9), None),
        fwd_at_onset=next((r["ego_forward_m"] for r in rows if r["sim_time"] >= onset - 1e-9), None),
        weight=G.SCENARIO_AUTHORITY_WEIGHTS.get(sc),
    )
json.dump(out, open(sys.argv[1], "w"), indent=1, default=str)
for sc, v in out.items():
    print(sc, v["runner_expect"], v["result_expect"], v["r0"], "| ph", v["phases"], "| act", v["officer_active_first"], v["officer_active_last"],
          "| win", v["v2_window"], "| tsafe", v["tsafe"], "| oracle", v["oracle_strict"], "| parked", v["parked_strict"], v["parked_graded"],
          "| onset v,fwd", v["speed_at_onset"], v["fwd_at_onset"], "| last", v["last_t"], "dt", v["dt"])
    print()
