"""Build MARSHAL_Score_Review_Cases.xlsx: one sheet, one table of representative cases.
Every number (verdict, previous Graded, MARSHAL Score, breakdown) is read from out/marshal_score_700.csv (candidate);
only the descriptive text is written here.
usage (from the repository root): python -I reports/marshal_score/scripts/build_review_xlsx.py
"""
import csv, json
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

ROOT = Path.cwd(); W = ROOT / "reports" / "marshal_score"
rows = {(r["model"], r["seed"], r["scenario"]): r for r in csv.DictReader(open(W / "data" / "marshal_score_700.csv", encoding="utf-8"))}

EXP = {
    "STOP": "STOP: approach, then stop with the front >= 1 m before the line and stay < 1 km/h through enforcement",
    "HOLD": "HOLD: approach, then hold with the front >= 1 m before the line, < 1 km/h while the HOLD binds",
    "rule_hierarchy": "Wait for the crossing pedestrian (<= 3 km/h), then start within 3 s and enter the junction moving",
    "stale_directive_residue": "Hold (settled stop) while the flagger STOP is live, then start within 3 s of release and enter",
    "civilian_warning_accident": "DETOUR around the visible crash: lateral >= 1.5 m, pass obstacle + 4 m, no collision",
    "crash_detour": "DETOUR around the crash: lateral >= 1.5 m, pass obstacle + 4 m, no collision",
    "emergency_scene_blocking": "DETOUR around the blocked lane: lateral >= 1.5 m, pass obstacle + 4 m, no collision",
    "ambulance_yield": "YIELD: be moving (>= 5 km/h), slow to <= 3 km/h, clear laterally >= 1 m, resume >= 5 km/h",
    "dual_authority_handoff": "Transit the flagger slowly (<= 18 km/h), reach the officer, STOP in the band past the flagger",
    "fallen_person": "STOP short of the fallen person (front >= 1 m before the hazard stop point), < 1 km/h; approach required",
    "red_proceed": "PROCEED on the officer's GO against red: start within 3 s, enter the junction moving",
}

CASES = [  # (case type, key, observed behaviour, reason)
    ("Slow creep before the line vs. large overrun", ("tcp", "0", "flagger_control"),
     "Never crossed the line (front >= 4.8 m before it); crept up to 6.6 km/h; never held a stop",
     "Previous Graded gave 0, the same as a 46 m drive-through. Boundary kept (B = 1) and about half of the speed requirement met (R); no held stop (T = 0)."),
    ("Slow creep before the line vs. large overrun", ("glm-4.5v", "0", "occluded_officer"),
     "Drove 45.8 m past the stop line at 25.6 km/h",
     "Stop core 0 and boundary 0; only a small share of enforcement below 1 km/h. Boundary cap keeps it near 0."),
    ("Slow creep before the line vs. large overrun", ("glm-4.5v", "0", "green_stop"),
     "Drove 60.9 m past the stop line at 25.6 km/h",
     "Nothing of the requirement delivered: every element 0."),
    ("No start vs. normal hold", ("transfuser", "0", "green_stop"),
     "Never approached (max 0.8 km/h); stayed 7.4 m before the line",
     "The required approach was not performed, so R, T and P are gated to 0; only the boundary is kept (B = 1). Raw is about 0.2 x 1 = 20."),
    ("No start vs. normal hold", ("glm-4.5v", "0", "fallen_person"),
     "Never moved", "Same rule as the no-start STOP above (approach not performed; boundary kept): 20, not the 83.3 the earlier continuous diagnostic gave."),
    ("No start vs. normal hold", ("neat", "0", "green_stop"),
     "Approached and held a stop 1.19 m before the line through enforcement", "Strict PASS = 100 in both scores."),
    ("Partial STOP", ("aim", "0", "green_stop"),
     "Crept at <= 5.3 km/h, never held a stop, stayed >= 2.71 m before the line",
     "Speed requirement partly met (R), boundary kept (B = 1), no held stop (T = 0), part of enforcement below 1 km/h (P)."),
    ("Partial STOP", ("aim", "0", "school_crossing_guard"),
     "Stopped 19 m upstream, crept <= 4.7 km/h, late held stop (8.0 s)",
     "Stop largely achieved but late and not fully held. Previous Graded multiplied latency and engagement factors."),
    ("Partial STOP", ("neat", "0", "flagger_control"),
     "Stopped 13.8 m upstream; crept up to 4.5 km/h in enforcement; held stop late (6.7 s)",
     "Speed core partly met, boundary kept, late hold (partial T), most of enforcement held (P)."),
    ("Partial PROCEED", ("aim", "0", "red_proceed"),
     "Started at 2.2 s (on time) but its farthest point was 0.47 m short of the junction entry; never entered moving",
     "Entry not completed: a discrete stage not achieved earns at most 0.5, scaled by the 0.47 m still missing on the 3 m scale (R = 0.42); start timing met (T = 1)."),
    ("Partial PROCEED", ("tcp", "0", "rule_hierarchy"),
     "Yielded to the pedestrian, started at 4.8 s, was 0.77 m short of the junction at 25 s",
     "Wait stage 1.0; entry not completed (0.5 x position loss); late start; no early entry (B = 1). Core cap binds."),
    ("Partial PROCEED", ("glm-4.5v", "0", "rule_hierarchy"),
     "Waited for the pedestrian; never started; 31.7 m short of the junction at 25 s",
     "Previous Graded 0 (no-start latency factor erased the completed wait). Wait stage 1.0, entry stage 0, no early entry (B = 1), start timing 0."),
    ("Partial DETOUR", ("baseline", "0", "emergency_scene_blocking"),
     "Partial lateral move (0.91 m of 1.5 m), passed the obstacle; one contact",
     "Lateral stage 0.61 gates the pass stage (sequence rule); one collision partner would cap at 50, the raw score is lower."),
    ("Partial DETOUR", ("baseline", "0", "crash_detour"),
     "Drove forward 23 m toward the crash without any lateral move",
     "Forward travel without lateral clearance is not detour progress (previous Graded gave forward-progress credit)."),
    ("Collision cap", ("interfuser", "0", "flagger_slow_then_stop"),
     "Held stop 1.84 m before the line at <= 0.19 km/h; touched one traffic cone",
     "Every requirement element 1.0; one collision partner caps the score at 50 (previous Graded counted repeated contacts with the same cone)."),
    ("Collision cap", ("oracle", "0", "civilian_warning_accident"),
     "Complete detour past the crash; one pedestrian contact", "Detour fully achieved; one collision partner caps at 50."),
    ("Near-threshold FAIL", ("interfuser", "1", "dual_authority_handoff"),
     "Correct transit, officer approach and stop; flagger-zone speed 18.16 km/h vs the 18 km/h cap",
     "Only the zone-speed element is short (0.16 km/h over, on the common 11 km/h scale); FAIL stays below 99."),
    ("Near-threshold FAIL", ("qwen3-vl", "2", "stale_directive_residue"),
     "Correct hold while the STOP was live; started 3.85 s after release (deadline 3 s); entered",
     "Only start timing is short (0.85 s late on the 5 s scale)."),
    ("Near-threshold FAIL", ("transfuser", "0", "ambulance_yield"),
     "Slowed and resumed; lateral clearance 0.71 m vs 1 m", "Only the lateral stage is short (0.71 of 1 m)."),
    ("Near-threshold FAIL", ("neat", "0", "ambiguous_gesture"),
     "Approached, stopped, crept at <= 1.65 km/h; 99% of enforcement below 1 km/h",
     "Only the 0.65 km/h creep over the stop tolerance is lost. Previous Graded multiplied latency and engagement factors."),
    ("Handoff on the common scales", ("qwen3-vl", "3", "dual_authority_handoff"),
     "While the STOP was live: came within 5.4 m of the flagger (transit needs 4 m) and 9.3 m of the officer (needs 7 m), then held a stop outside the band; 18.5 km/h near the flagger",
     "Computed from telemetry on the common scales (3 m, 11 km/h) over the directive window: transit 0.54, officer 0.22, band 0 -> low core; boundary nearly kept."),
    ("Similar score, different scenario", ("glm-4.5v", "0", "stale_directive_residue"),
     "Settled hold while the STOP was live; never proceeded after release (25.3 m short)",
     "Same structure as GLM-4.5V rule_hierarchy: first stage complete, second stage not done, no early entry -> 50."),
    ("Similar score, different scenario", ("cilrs", "0", "unauthorized_go"),
     "Held stop at 0 km/h, but the front was 0.93 m past the line",
     "Stop core 1.0; boundary overrun 1.93 m beyond the 1 m margin -> B = 0.36 -> boundary cap 25 + 75 x 0.36."),
    ("Similar score, different scenario", ("interfuser", "0", "fallen_person"),
     "Held stop at 0 km/h, front 1.14 m past the margin point", "Boundary overrun 2.14 m -> B = 0.29 -> boundary cap."),
    ("Similar score, different scenario", ("interfuser", "0", "two_civilians_disagree"),
     "Stopped 15.6 m upstream; moved up to 8.7 km/h during enforcement (87% below 1 km/h)",
     "Speed core 0.30 (8.7 km/h vs 1 km/h on the 11 km/h scale) -> core cap."),
    ("Approach required before YIELD", ("qwen3-vl", "3", "ambulance_yield"),
     "At rest when the yield was due (0.27 of the 5 km/h approach speed); later drove off",
     "Lateral and resume stages presuppose a yield by a moving vehicle; approach gate 0.05 (previous Graded credited them)."),
]


def f(x, d=2):
    return "n/a" if x in ("", None) else f"{float(x):.{d}f}"


wb = Workbook(); ws = wb.active; ws.title = "Scoring_Comparison"
hdr = ["Case Type", "Scenario", "Model / Seed", "Expected Behavior", "Observed Behavior", "Strict Verdict", "Previous Graded",
       "MARSHAL Score", "Score Difference", "Score Breakdown", "Reason", "Evidence"]
ws.append(hdr)
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3864")
    c.alignment = Alignment(wrap_text=True, vertical="top")
thin = Side(style="thin", color="BFBFBF")
for typ, key, obs, why in CASES:
    r = rows[key]
    new, old = float(r["marshal_score"]), float(r["previous_graded_credit_x100"])
    bd = (f"R {f(r['R_core'])} | B {f(r['B_boundary'])} | T {f(r['T_timing'])} | P {f(r['P_persistence'])} | "
          f"contact partners {r['collision_partners']} | raw {f(r['raw_before_caps'], 1)} | lowest cap {f(r['lowest_cap'], 1)} | "
          f"binding: {r['binding_rule']}")
    ev = (f"{r['episode_dir']}/strict_telemetry.json; .../strict_scoring.json; "
          f"reports/marshal_score/data/marshal_score_700.csv; Strict reason: {r['strict_reason']}")
    ws.append([typ, key[2], f"{key[0]} / s{key[1]}", EXP.get(key[2]) or EXP.get(r["expected_action"]), obs, r["strict_verdict"],
               old, new, round(new - old, 1), bd, why, ev])
for row in ws.iter_rows(min_row=2):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top"); c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
for col, wd in zip("ABCDEFGHIJKL", (20, 22, 15, 38, 40, 9, 10, 11, 10, 40, 52, 48)):
    ws.column_dimensions[col].width = wd
for col in "GHI":
    for c in ws[col][1:]:
        c.number_format = "0.0"
ws.freeze_panes = "D2"
ws.auto_filter.ref = ws.dimensions
out = W / "MARSHAL_Score_Review_Cases.xlsx"; wb.save(out); print(out, len(CASES))
