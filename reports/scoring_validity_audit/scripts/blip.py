import json, sys, os
REPO = r"."
RUNS = r"tmp/_protocol_v2/oracle/s0"
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import published_v2  # noqa: E402,F401  (pins the published v2 graded scorer; see published_v2.py)
from marshal_bench.criteria import strict_episode_scoring as S, graded_episode_scoring as G
ex = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ex.json")))
peak = float(sys.argv[1]) if len(sys.argv) > 1 else 7.0
res_out = {}
for sc, v in ex.items():
    d = os.path.join(RUNS, f"reference_oracle_{sc}")
    res = json.load(open(os.path.join(d, "result.json")))["result"]
    rows = json.load(open(os.path.join(d, "strict_telemetry.json")))["telemetry"]
    r0 = rows[0]; fwd = 0.0; out = []
    for r in rows:
        t = r["sim_time"]
        if t <= 0.6: spd = peak * t / 0.6
        elif t <= 1.2: spd = peak * (1.2 - t) / 0.6
        else: spd = 0.0
        fwd += spd / 3.6 * 0.05
        p = dict(r)
        for k in ("ego_x","ego_y","ego_lateral_m","in_junction","distance_to_officer_m","distance_to_hazard_m"):
            if k in r0: p[k] = r0[k]
        for k in ("stopline_forward_m","stopline_front_m","distance_to_stopline_m"):
            if r0.get(k) is not None: p[k] = r0[k] - fwd
        p["ego_speed_kmh"] = spd; p["ego_forward_m"] = fwd; p["collision_count"] = 0
        out.append(p)
    mrt = (res.get("ground_truth") or {}).get("max_reaction_time_sec")
    s = S.score_episode_from_telemetry(res, out, scenario=res["scenario"], expected_action=res["expected_action"], max_reaction_time=mrt, protocol="v2")
    g = G.score_episode_from_telemetry(res, out, scenario=res["scenario"], expected_action=res["expected_action"], max_reaction_time=mrt, protocol="v2")
    res_out[sc] = (s["verdict"], s["reason"], g.get("credit"), round(fwd,2))
    print(sc, res["expected_action"], s["verdict"], "|", s["reason"][:90], "| graded", g.get("credit"), "| fwd", round(fwd,2))
json.dump(res_out, open(f"blip_{peak}.json","w"), indent=1)
