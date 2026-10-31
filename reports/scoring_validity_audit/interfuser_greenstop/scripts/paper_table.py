"""green_stop control comparison for the paper: officer present (STOP) vs absent, current InterFuser adapter.

    python reports/scoring_validity_audit/interfuser_greenstop/scripts/paper_table.py    # from the audit checkout root

Officer present: tmp/_greenstop_interfuser/officer (seeds 0-2), scored with the published strict scorer and Credit
v2.2-final (credit_v22_final/scripts/credit_final.py, unchanged). Officer absent: tmp/_greenstop_interfuser/ab
(no officer, light frozen Green, seeds 0-4); no directive, so no credit. Writes ../data/officer_vs_absent.csv.
"""
import csv
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUD = HERE.parents[1]
sys.path[:0] = [str(AUD / "credit_v22_final" / "scripts"), "scripts/validation"]
import final_leaderboard as L  # noqa: E402
import summarize_validation as SV  # noqa: E402

_spec = importlib.util.spec_from_file_location("remeasure_analyze", AUD / "interfuser_remeasure" / "scripts" / "analyze.py")
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)
F, V = L.F, L.V


def first_brake(ep):
    p = ep / "interfuser_debug" / "interfuser_verbose.jsonl"
    for r in (json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()):
        if r["t"] > 1.0 and r["speed"] < 0.5 and r["requested"]["brake"] > 0.1:
            blk = [k for k in ("d_0", "d_0.5", "d_1", "d_1.5", "d_2") if r[k] < 3.0]
            return r["t"], "+".join(r["brake_reasons"]) or "desired_speed_0", ";".join(blk), r["red_light_p"]
    return None, "", "", None


rows = []
for cond, root, seeds in (("officer present (STOP)", Path("tmp/_greenstop_interfuser/officer/interfuser"), (0, 1, 2)),
                          ("officer absent, light Green", Path("tmp/_greenstop_interfuser/ab/interfuser_noofficer_green"),
                           (0, 1, 2, 3, 4))):
    for s in seeds:
        ep = V.C.find_episode(root / f"s{s}", "green_stop")
        A.check_speed_input(ep, "forward")  # both conditions ran with the forward speed input
        res, tel, kw, r = A.capture(ep, "green_stop")
        m = SV.ab_metrics(tel)
        t, why, blk, red = first_brake(ep)
        credit = round(F.scored(res, tel, kw, V.event_ids(ep), "final", r["strict"] == "INVALID")[0], 4) if "present" in cond else ""
        rows.append({"condition": cond, "seed": s, "travel_m": m["travel_m"], "v_max_kmh": m["max_speed_kmh"],
                     "start_s": m["t_start_s"], "final_front_to_line_m": m["final_front_to_line_m"],
                     "reaches_B": m["ab_pass"], "first_brake_at_rest_s": t, "brake_branch": why,
                     "zero_safe_distance_terms": blk, "red_light_p": red, "brake_at_rest_share": A.brake_cause(ep),
                     "strict": r["strict"] if "present" in cond else "", "credit_v22_final": credit})
with (HERE.parent / "data" / "officer_vs_absent.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
for r in rows:
    print(r)
