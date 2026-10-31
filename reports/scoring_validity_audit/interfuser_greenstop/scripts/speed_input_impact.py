"""How far the old InterFuser speed input (3-D |v|) was from the horizontal speed, per episode, in the existing
fixed-adapter runs. What is compared: verbose-trace speed (3-D |v|, the old input) vs telemetry ego_speed_kmh
(horizontal |v|). This is NOT the leaderboard forward projection: the telemetry logs no pitch and no velocity components,
so the forward speed itself cannot be reconstructed. The forward speed also differs from horizontal |v| by its sign when
reversing and by the pitch terms (cos(pitch) on vx/vy, sin(pitch)*vz). As a partial check for the sign case, reverse
motion after 1 s is counted from the logged positions and yaw (displacement along the heading < -0.2 m/s).

    python reports/scoring_validity_audit/interfuser_greenstop/scripts/speed_input_impact.py [run_dir ...]

Default run dirs: tmp/_remeasure_interfuser/fixed/interfuser/s{0,1,2}. Writes ../data/speed_input_impact.csv.
"""
import csv
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
dirs = [Path(p) for p in sys.argv[1:]] or [Path(f"tmp/_remeasure_interfuser/fixed/interfuser/s{s}") for s in (0, 1, 2)]
out = []
for d in dirs:
    for ep in sorted(d.glob("interfuser_*")):
        vp = ep / "interfuser_debug" / "interfuser_verbose.jsonl"
        tp = ep / "strict_telemetry.json"
        if not (vp.exists() and tp.exists()):
            continue
        tel = json.loads(tp.read_text(encoding="utf-8"))
        tel = tel.get("telemetry") if isinstance(tel, dict) else tel
        hz = {round(float(r["sim_time"]), 2): float(r["ego_speed_kmh"] or 0) / 3.6 for r in tel}
        rows = [json.loads(x) for x in vp.read_text(encoding="utf-8").splitlines() if x.strip()]
        diffs = [(r["t"], r["speed"] - hz[round(r["t"], 2)]) for r in rows if round(r["t"], 2) in hz]
        rev = 0
        for a, b in zip(tel, tel[1:]):
            dt = float(b["sim_time"]) - float(a["sim_time"])
            if float(b["sim_time"]) > 1.0 and dt > 0:
                yaw = math.radians(float(a["ego_yaw_deg"]))
                v_along = ((float(b["ego_x"]) - float(a["ego_x"])) * math.cos(yaw)
                           + (float(b["ego_y"]) - float(a["ego_y"])) * math.sin(yaw)) / dt
                rev += v_along < -0.2
        fall = [t for t, dv in diffs if dv > 0.5 and t <= 1.0]
        late = [(t, dv) for t, dv in diffs if t > 1.0 and abs(dv) > 0.2]
        # first-second controller decisions that a forward-speed input would see differently: brake requested
        # while the 3-D speed exceeded desired*1.1 only because of the fall
        brake_in_fall = sum(1 for r in rows if r["t"] <= 1.0 and r["requested"]["brake"] > 0.1
                            and r["speed"] - hz.get(round(r["t"], 2), r["speed"]) > 0.5)
        out.append({"run": d.name, "scenario": ep.name[len("interfuser_"):], "fall_ticks_dv_gt_0_5": len(fall),
                    "fall_end_s": max(fall) if fall else None, "brake_ticks_in_fall": brake_in_fall,
                    "ticks_after_1s_dv_gt_0_2": len(late),
                    "max_dv_after_1s": round(max((abs(dv) for _, dv in late), default=0.0), 3),
                    "reverse_ticks_after_1s": rev})
data = HERE.parent / "data"
data.mkdir(parents=True, exist_ok=True)
with (data / "speed_input_impact.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0]))
    w.writeheader()
    w.writerows(out)
n = len(out)
print("3-D |v| vs horizontal |v| (not the forward projection)")
print(f"{n} episodes; with fall ticks: {sum(1 for r in out if r['fall_ticks_dv_gt_0_5'])}; brake requested during the fall: "
      f"{sum(1 for r in out if r['brake_ticks_in_fall'])}; episodes with |dv|>0.2 after 1 s: "
      f"{sum(1 for r in out if r['ticks_after_1s_dv_gt_0_2'])} (max ticks {max(r['ticks_after_1s_dv_gt_0_2'] for r in out)}); episodes with reverse motion after 1 s: "
      f"{sum(1 for r in out if r['reverse_ticks_after_1s'])}")
