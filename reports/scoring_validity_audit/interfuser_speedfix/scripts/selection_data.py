"""Per-scenario evidence for the speed-input re-measure selection (offline, existing fixed-adapter runs s0-s2).

    python reports/scoring_validity_audit/interfuser_speedfix/scripts/selection_data.py      # from the audit checkout root

Writes ../data/selection_evidence.csv: directive onset (staged config), stop-line distance at spawn (first telemetry
row), the spawn-fall speed-input inflation (interfuser_greenstop/data/speed_input_impact.csv), and the s0-s2 spread of
the fixed runs (strict, Credit v2.2-final, start time, travel; credit_v22_final/data/interfuser_setups_cells.csv).
"""
import csv
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUD = HERE.parents[1]
sys.path[:0] = [".", "scripts"]
import _run_vlm_test as vlm  # noqa: E402
import _shared_staging as staging  # noqa: E402

FIXED = Path("tmp/_remeasure_interfuser/fixed/interfuser")
imp = list(csv.DictReader((AUD / "interfuser_greenstop/data/speed_input_impact.csv").open(encoding="utf-8")))
cells = [c for c in csv.DictReader((AUD / "credit_v22_final/data/interfuser_setups_cells.csv").open(encoding="utf-8"))
         if c["setup"] == "fixed" and c["run"] == "re-run"]
out = []
for sc in vlm.SCENARIO_ORDER:
    cfg = staging.load_staged_config(".", sc, vlm.SCENARIOS[sc], "interfuser")
    off = cfg.get("officer") or {}
    second = cfg.get("second_authority") or {}
    starts, travel, stop0 = [], [], None
    for s in (0, 1, 2):
        ep = next(iter((FIXED / f"s{s}").glob(f"interfuser_{sc}")))
        tel = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
        rows = tel.get("telemetry") if isinstance(tel, dict) else tel
        rows = [r for r in rows if float(r["sim_time"]) <= 25.0 + 1e-9]
        if stop0 is None:
            stop0 = rows[0].get("stopline_forward_m")
        starts.append(next((float(r["sim_time"]) for r in rows if float(r["ego_speed_kmh"] or 0) >= 5.0), None))
        fwd = [float(r["ego_forward_m"]) for r in rows if r.get("ego_forward_m") not in (None, "")]
        travel.append(round(max(fwd) - min(fwd), 1) if fwd else 0.0)
    cs = sorted((c for c in cells if c["scenario"] == sc), key=lambda c: int(c["seed"]))
    cr = [float(c["credit_v22_final"]) for c in cs]
    im = [r for r in imp if r["scenario"] == sc and r["run"] in ("s0", "s1", "s2")]
    out.append({"scenario": sc, "expected": vlm.SCENARIOS[sc]["expect"],
                "onset_s": off.get("onset_time"), "duration_s": off.get("duration"),
                "second_authority_onset_s": second.get("onset_time") if second else None,
                "stopline_fwd_at_spawn_m": None if stop0 in (None, "") else round(float(stop0), 1),
                "fall_ticks_dv_gt_0_5": ";".join(r["fall_ticks_dv_gt_0_5"] for r in im),
                "brake_ticks_in_fall": ";".join(r["brake_ticks_in_fall"] for r in im),
                "strict_s0_s2": "".join(c["strict"][0] for c in cs), "credit_v22f_s0_s2": ";".join(f"{x:.3f}" for x in cr),
                "credit_range": round(max(cr) - min(cr), 3),
                "start_s_s0_s2": ";".join("-" if x is None else f"{x:.2f}" for x in starts),
                "travel_m_s0_s2": ";".join(str(x) for x in travel)})
with (HERE.parent / "data" / "selection_evidence.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0]))
    w.writeheader()
    w.writerows(out)
for r in out:
    print(" | ".join(str(v) for v in r.values()))
