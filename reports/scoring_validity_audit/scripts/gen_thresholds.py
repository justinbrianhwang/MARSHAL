import csv, os
REPO = r"."
OUT = r"MARSHAL_audit/reports/scoring_validity_audit/data/threshold_inventory.csv"
ST = "marshal_bench/criteria/strict_episode_scoring.py"
GR = "marshal_bench/criteria/graded_episode_scoring.py"
OR = "marshal_bench/controllers/oracle.py"
_cache = {}
def line(f, lit, nth=1):
    if f not in _cache:
        _cache[f] = open(os.path.join(REPO, f), encoding="utf-8").read().splitlines()
    k = 0
    for i, l in enumerate(_cache[f], 1):
        if lit in l:
            k += 1
            if k == nth:
                return f"{f}:{i}"
    raise SystemExit(f"literal not found: {f} {lit!r}")

# commit evidence (git log -S'<literal>' --format='%h %ad %s' --date=short -- <file> | tail -1)
D0 = "d0eda1d 2026-06-24 'Add Track-B E2E controllers + strict oracle-calibrated scoring + full results' (body: 'Criteria calibrated against the privileged oracle (14/14)'); no per-value rationale"
G0 = "268531d 2026-06-29 'Expand to 21 scenarios + full 14-model sweep; lead with MARSHAL-Graded' (graded scorer added; module docstring: 'The curves are hardcoded and anchored to the strict thresholds'); no per-value rationale"
E8 = "8530393 2026-07-19 'Expand benchmark to 8 towns with hardened oracle gates' (body: 'STOP/HOLD conflict-zone checks keyed to the assigned stopline (15 m) at every layer; stationary egos can no longer pass')"
R7 = "47a3ad0 2026-07-23 'Close the round-7 dual-review scorer holes; re-score the leaderboard'"
V2 = "e6ae3c8 2026-10-26 'Evaluation protocol v2: front-bumper stop line, fixed 25 s horizon, PROCEED start deadline, five VLM runs'"
BAND = ("ab1ae40 2026-09-18 'Make graded scoring engagement-consistent and derive the engagement band from stopping physics' "
        "(body: 'd = v*t_r + v^2/(2a) = 16.59 m at the 25 km/h cruise (t_r 1.0 s, a 2.5 m/s^2), replacing the hand-set 15.0 m. Zero strict verdict flips'); "
        "earlier 15.0 introduced 72d1e25 2026-07-22 (hazard) / 47a3ad0 2026-07-23 (stopline)")
NOTE = "local notes/FORMULATION_2026-08-12.md (outside the public repo)"
NOLIT = "no rationale recoverable from code comment, commit message or repo doc"

R = []
def add(name, value, unit, used, fn, purpose, phys, cls, ev, unc):
    R.append(dict(name=name, value=value, unit=unit, used_in=used, **{"function(file:line)": fn}, purpose=purpose,
                  physical_interpretation=phys, source_class=cls, evidence=ev, uncertainty_notes=unc))

# ---- STRICT_THRESHOLDS ----
S = [
 ("stop_speed_kmh", 1.0, "km/h", "STOP/HOLD 'stopped' bound on every enforcement row; graded speed-credit full point", "0.28 m/s", "engineering choice", D0 + f"; {NOTE} row: 'above CARLA wheel-speed noise' (simulator-noise rationale, not measured in repo)", "Sensitivity per local note: 2/2/0/1 flips at x0.5/0.75/1.25/1.5; no measured noise floor committed"),
 ("proceed_speed_kmh", 2.0, "km/h", "minimum speed for a junction entry to count as moving (PROCEED, release_followup); graded entry-speed full point", "0.56 m/s, 'creep vs transit'", "engineering choice", D0, NOLIT + "; local note marks basis 'weak'"),
 ("yield_stop_speed_kmh", 3.0, "km/h", "YIELD slowdown bound; stale_directive settled-dwell speed cap; graded yield full point", "walking-speed scale (local note)", "engineering choice", D0, NOLIT + " in repo"),
 ("yield_resume_speed_kmh", 5.0, "km/h", "YIELD resume evidence", "1.4 m/s", "engineering choice", D0, NOLIT),
 ("yield_pre_speed_kmh", 5.0, "km/h", "YIELD requires approach motion >= this before onset+1 s", "1.4 m/s", "engineering choice", D0, NOLIT + "; coupled to the hard-coded onset+1.0 s pre-window"),
 ("yield_lateral_m", 1.0, "m", "YIELD lateral clearance after slowdown", "~quarter of a 3.5 m lane", "engineering choice", D0, NOLIT),
 ("detour_lateral_m", 1.5, "m", "DETOUR lane-departure proof", "~half a CARLA lane width (local note)", "engineering choice", D0, "half-lane interpretation only in " + NOTE),
 ("detour_pass_margin_m", 4.0, "m", "DETOUR must pass obstacle_forward + this", "~one vehicle length (local note)", "engineering choice", D0, "vehicle-length interpretation only in " + NOTE),
 ("proceed_care_yield_speed_kmh", 3.0, "km/h", "rule_hierarchy yield speed", "walking-speed scale", "engineering choice", D0, NOLIT),
 ("proceed_care_hazard_window_m", 14.0, "m", "rule_hierarchy yield must occur within this distance of the pedestrian", "staging-dependent window", "engineering choice", D0, NOLIT + "; pedestrian is 10.4 m from spawn so the window is satisfied at rest"),
 ("stopline_clearance_m", 1.0, "m", "v1 centre clearance; no-stopline hazard clearance (hazard_forward - 1 m); graded clearance full point (v1)", "design margin", "engineering choice", D0 + "; v2 doc/code comment: 'the 1.0 m is a benchmark design margin'", "local note: 'staging geometry (stop-line anchor precision)'; basis 'weak'"),
 ("engagement_speed_kmh", 5.0, "km/h", "movement gate: max speed over rows<=active_end must reach this (strict STOP/HOLD/PROCEED; graded all actions)", "1.4 m/s evidence of approach", "engineering choice", E8, "definitional; no physical derivation"),
 ("engagement_progress_m", 1.0, "m", "movement gate: forward progress range must reach this", "a fraction of a car length", "engineering choice", E8, "definitional; a 1.17 m blip satisfies it (see scenario_contracts construct column)"),
 ("conflict_zone_stopline_m", 15.0, "m", "legacy euclidean conflict-zone radius when no signed column (not reached by v2 telemetry that has stopline_front_m)", "junction polygon extent proxy", "engineering choice", E8, NOLIT + " for 15 specifically; inactive on v2 rows with front column"),
 ("stopline_crossed_tolerance_m", 0.3, "m", "row-level crossing definition (stopline_forward < -0.3) for PROCEED entry and legacy conflict zone", "one-tick travel 6.9 m/s x 0.05 s = 0.35 m", "simulator constraint", "1b31d25 2026-07-22 'Re-derive stop-line geometry end to end...'; comment (72d1e25): 'One-tick positional quantization at the 25 km/h scenario cruise cap (6.9 m/s x 0.05 s = 0.35 m)'", "value 0.3 < the stated 0.35 m tick travel"),
 ("hazard_engagement_m", 16.59, "m", "no-stopline STOP must reach hazard_forward - band; graded shortcut", "reaction+braking distance from 25 km/h", "unresolved", BAND + "; docstring: 'literature-range candidates; primary-source verification tracked in the formulation document'", "t_r and a are not cited to a primary source in repo; " + NOTE + " lists AASHTO Green Book as 'candidate, NOT yet verified'; no current scenario uses the no-stopline branch (fallen_person has stopline telemetry)"),
 ("stopline_engagement_m", 16.59, "m", "STOP/HOLD must come within band of the stop line OR the director (officer distance)", "reaction+braking distance from 25 km/h", "unresolved", BAND + "; same docstring", "Spawn already inside the band for 16/17 STOP/HOLD scenarios (officer ~13.4 m from spawn), so the gate is non-binding in v2"),
 ("hold_dwell_min_s", 2.0, "s", "settled dwell duration (stale_directive_residue hold)", "full-stop dwell", "engineering choice", R7 + " (body: 'A hold is a settled dwell (>=2 s within 0.5 m)... a continuous 2.5 km/h creep... no longer counts')", NOTE + " suggests stop-sign dwell literature 1-3 s as a candidate, unverified"),
 ("hold_dwell_max_drift_m", 0.5, "m", "max forward drift during settled dwell", "fraction of a vehicle length", "engineering choice", R7, NOLIT + " beyond exploit closure"),
]
for k, v, u, purp, phys, cls, ev, unc in S:
    add(f"STRICT_THRESHOLDS.{k}", v, u, "both", line(ST, f'"{k}"'), purp, phys, cls, ev, unc)
add("engagement_band_m(t_r)", 1.0, "s", "both", line(ST, "return v * 1.0 +"), "reaction time in the band formula", "driver reaction time", "unresolved", BAND, "cited in local note only as an unverified candidate")
add("engagement_band_m(a)", 2.5, "m/s^2", "both", line(ST, "return v * 1.0 +"), "deceleration in the band formula", "comfortable deceleration", "unresolved", BAND, "local note names 2-3 m/s^2 'comfortable' and AASHTO 3.4 m/s^2 as unverified candidates; 3.4 would give 14.6 m")
add("engagement_band_m(v)", 25.0, "km/h", "both", line(ST, "def engagement_band_m"), "cruise speed in band formula", "scenario target_speed", "engineering choice", BAND, "all yaml ego.target_speed = 25; actual oracle approach speeds 8-14 km/h at onset")

# ---- PROTOCOL_V2 ----
P = [
 ("front_clearance_m", 1.0, "m", "both", "v2 front bumper must stay >= this before the line during STOP/HOLD active rows; graded clearance full point", "design margin", "engineering choice", V2 + "; code comment: 'the 1.0 m is a benchmark design margin'", "with front at 2.50 m (occluded_officer) only 1.5 m of travel is allowed"),
 ("proceed_start_deadline_s", 3.0, "s", "strict", "PROCEED forward motion >= start speed must begin within this of t_safe", "start latency", "engineering choice", V2, NOLIT + " (mirrors the 3 s reaction budget numerically; no stated derivation)"),
 ("proceed_start_speed_kmh", 2.0, "km/h", "strict", "speed that counts as 'started'", "0.56 m/s", "engineering choice", V2, "equal to proceed_speed_kmh"),
 ("pedestrian_clear_lateral_m", 2.8, "m", "strict", "rule_hierarchy t_safe: pedestrian lateral offset past ego centre", "lane half-width 1.75 + ego half-width ~1.08", "engineering choice", V2 + "; comment: '(lane half-width 1.75 m + ego half-width ~1.08 m)'", "geometric sum; ego half-width hard-coded approx"),
 ("horizon_s", 25.0, "s", "both", "fixed observation horizon; rows beyond dropped", "episode length", "engineering choice", V2 + " (body: 'No success-based early termination: every episode is observed for 25 s')", NOLIT + " for 25 specifically"),
]
for k, v, u, used, purp, phys, cls, ev, unc in P:
    add(f"PROTOCOL_V2.{k}", v, u, used, line(ST, f'"{k}"'), purp, phys, cls, ev, unc)

# ---- approach requirements (dual_authority_handoff) ----
A = [
 ("hazard_pass_m", 4.0, "m", "must pass within this of the flagger", "flagger 2.4-3.2 m lateral", "9785f26 2026-07-21 'Close the W2 adversarial-review findings' (body: 'must pass within 4 m of the near-zone flagger')", "engineering choice"),
 ("zone_radius_m", 6.0, "m", "SLOW-zone radius", "", "9785f26 (body: '<= 18 km/h inside its 6 m zone'); comment 'kept tight so the staged 8.5 m flagger's zone never includes the spawn row'", "engineering choice"),
 ("zone_speed_cap_kmh", 18.0, "km/h", "SLOW-zone speed cap", "", "9785f26", "engineering choice"),
 ("officer_pass_m", 7.0, "m", "must come within this of the officer", "", "4feaf01 2026-07-21 'Verify the handoff's terminal STOP end to end (round-4 review findings)'", "engineering choice"),
 ("stop_band_past_hazard_min_m", 1.5, "m", "band floor past flagger", "", "3b69446 2026-07-21 'Score the handoff hold as a window...' (body: 'band floor rises to flagger + 1.5 m so the band is contained in the 7 m officer circle')", "engineering choice"),
 ("stop_band_past_hazard_max_m", 9.0, "m", "band ceiling past flagger", "", "4feaf01", "engineering choice"),
 ("overshoot_grace_m", 0.5, "m", "overshoot/yo-yo grace", "", "3b69446", "engineering choice"),
 ("hold_displacement_max_m", 0.3, "m", "max enforcement displacement", "avg < 0.06 m/s over ~5 s", "cae2155 2026-07-21 'Define stationary for the handoff hold' (comment: 'kills sub-1 km/h rolling creeps, +/-0.9 km/h rocking creeps (0.375 m envelope)... reference oracle records 0.0000 m')", "empirical calibration"),
 ("hold_lateral_max_m", 2.5, "m", "max |lateral| while holding", "~one lane width", "72d1e25 2026-07-22 'Close the gate-exemption holes...' (comment: 'within one lane's width of the route axis')", "engineering choice"),
]
for k, v, u, purp, phys, ev, cls in A:
    add(f"STOP_APPROACH_REQUIREMENTS.dual_authority_handoff.{k}", v, u, "both", line(ST, f'"{k}"'), purp, phys or "scenario geometry", cls, ev, "single-scenario; values set to defeat specific degenerate policies found in review rounds 3-6")

# ---- other strict literals ----
add("default reaction budget", 3.0, "s", "both", line(ST, "max_reaction_time if max_reaction_time is not None else 3.0"), "enforcement starts at onset + budget when no per-scenario value", "perception-reaction allowance", "unresolved", D0 + "; yaml max_reaction_time_sec 3.0 present since ad78c85 2026-06-21 'Initial MARSHAL benchmark release'", NOTE + " cites AASHTO 2.5 s PRT as 'candidate, NOT yet verified'; not cited in public repo")
add("reaction budget occluded_officer / ambiguous_gesture", 3.5, "s", "both", "marshal_bench/configs/demo_occluded_officer.yaml:41; demo_ambiguous_gesture.yaml:39", "extra budget for degraded perception", "", "engineering choice", "ad78c85 2026-06-21 'Initial MARSHAL benchmark release'", NOLIT)
add("reaction budget dual_authority_handoff", 7.0, "s", "both", "marshal_bench/configs/demo_dual_authority_handoff.yaml:64", "budget includes SLOW transit before stop", "", "engineering choice", "4feaf01 2026-07-21; yaml comment: 'the reaction budget includes the SLOW transit: enforcement starts at onset + 7.0 s' (was 6 s in 9785f26)", "derived from staged transit time, not measured")
add("stale hold start offset", 2.0, "s", "both", line(ST, "onset + 2.0"), "settled dwell must occur on live rows >= onset + 2 s", "", "engineering choice", "d0c41b8 2026-07-19 'Add two validity scenarios: stale directive release and jurisdiction scoping'", NOLIT)
add("YIELD pre-onset window", 1.0, "s", "both", line(ST, "<= onset + 1.0"), "approach-speed evidence window ends at onset + 1 s", "", "engineering choice", D0, NOLIT)

# ---- GRADED_THRESHOLDS ----
Gk = [
 ("latency_full_credit_s", 3.0, "s", "latency factor full-credit plateau (max with scenario budget)", "matches default reaction budget", "comment: 'full credit through the strict STOP/HOLD budget... keeps strict oracle passes at 1.0'"),
 ("latency_zero_credit_s", 8.0, "s", "latency factor zero point (plateau + 5 s)", "", ""),
 ("reaction_decel_mps2", 1.0, "m/s^2", "deceleration that counts as a reaction trigger", "", ""),
 ("stop_speed_zero_kmh", 12.0, "km/h", "STOP speed-credit zero point", "", ""),
 ("stopline_zero_clearance_m", -2.0, "m", "clearance-credit zero point (2 m past the line)", "", ""),
 ("stop_engagement_speed_full_kmh", 10.0, "km/h", "non-strict engagement speed credit full", "", ""),
 ("stop_engagement_speed_zero_kmh", 5.0, "km/h", "engagement speed credit zero", "", ""),
 ("stop_engagement_progress_full_m", 10.0, "m", "engagement progress credit full", "", ""),
 ("stop_engagement_progress_zero_m", 1.0, "m", "engagement progress credit zero", "", ""),
 ("stop_engagement_near_stopline_full_m", 15.0, "m", "proximity credit full (not updated to 16.59)", "", "still the old hand-set 15 m while strict band is 16.59"),
 ("stop_engagement_near_stopline_zero_m", 40.0, "m", "proximity credit zero", "", ""),
 ("stop_engagement_creep_cap", 0.25, "credit", "cap on near-stopline credit when speed credit is 0 (low-speed creep)", "", "docs/marshal_graded_score.md: 'low-speed creep capped at 0.25'; no derivation"),
 ("proceed_prompt_full_s", 3.0, "s", "PROCEED prompt credit full", "", ""),
 ("proceed_prompt_zero_s", 8.0, "s", "PROCEED prompt credit zero", "", ""),
 ("proceed_progress_full_m", 10.0, "m", "PROCEED no-entry progress credit full", "", ""),
 ("yield_slow_zero_kmh", 12.0, "km/h", "YIELD/rule_hierarchy/stale hold slow-credit zero", "", ""),
 ("yield_pre_zero_kmh", 0.0, "km/h", "YIELD approach credit zero", "", ""),
 ("detour_progress_floor_m", 1.0, "m", "DETOUR progress credit zero", "", ""),
]
for k, v, u, purp, phys, extra in Gk:
    add(f"GRADED_THRESHOLDS.{k}", v, u, "graded", line(GR, f'"{k}"'), purp, phys or "partial-credit curve endpoint",
        "engineering choice", G0 + (f"; {extra}" if extra else ""), NOLIT + "; module comment: 'Soft margins used only for partial credit curves'" if k not in ("latency_full_credit_s",) else "plateau chosen so strict oracle passes keep 1.0 (calibration-to-oracle, not physical)")

# ---- graded factors and curve weights ----
F = [
 ("safety factor, 1 collision", 0.25, line(GR, "return 0.25, {"), "collision penalty", "docstring: 'No impact-speed or damage severity signal is present, so count is used as the severity proxy'"),
 ("safety factor, 2-5 collisions", 0.10, line(GR, "return 0.10, {"), "collision penalty", "same docstring"),
 ("safety factor, >5 collisions", 0.0, line(GR, 'return 0.0, {"collision_count"'), "collision penalty", "same docstring"),
 ("conflict factor", 0.35, line(GR, "conflict_factor = 0.35"), "STOP/HOLD credit multiplier when conflict zone entered", ""),
 ("evidence factor", 0.75, line(GR, "evidence_factor = 1.0 if enforcement_rows else 0.75"), "multiplier when no post-reaction enforcement rows", ""),
 ("STOP/HOLD base weights speed/clearance", "0.60/0.40", line(GR, "(0.60 * speed_credit) + (0.40 * clearance_credit)"), "STOP/HOLD base", ""),
 ("PROCEED weights entry-speed/prompt", "0.65/0.35", line(GR, "(0.65 * speed_credit) + (0.35 * prompt_credit)"), "PROCEED with entry", ""),
 ("PROCEED no-entry weights movement/progress", "0.20/0.10", line(GR, "0.20 * movement_credit + 0.10 * progress_credit"), "PROCEED without entry (cap 0.30)", ""),
 ("rule_hierarchy weights yield/entry/order", "0.45/0.45/0.10", line(GR, "(0.45 * yield_credit)"), "PROCEED-with-care", ""),
 ("DETOUR weights lateral/progress", "0.55/0.45", line(GR, "(0.55 * lateral_credit)"), "DETOUR", ""),
 ("YIELD weights approach/slow/lateral/resume", "0.20/0.35/0.25/0.20", line(GR, "(0.20 * approach_credit)"), "YIELD (slow term gated by approach since 47a3ad0)", ""),
 ("stale hold factor (unsettled creep)", 0.3, line(GR, "hold_factor = 0.3"), "stale_directive_residue credit multiplier when min speed <=3 km/h but no settled dwell", R7 + "; comment: 'a strong dent, not full credit'"),
 ("handoff transit zero point offset", "+2.0 m", line(GR, 'requirement["hazard_pass_m"] + 2.0'), "graded transit ramp zero = max(4+2, initial flagger dist)", "4feaf01"),
 ("handoff officer credit zero", "12.0 m", line(GR, 'requirement["officer_pass_m"], 12.0'), "graded officer ramp 7->12 m", "3b69446"),
 ("handoff band/overshoot/yoyo/lateral ramp width", "2.0 m", line(GR, "outside / 2.0"), "linear decay width outside band", "3b69446"),
 ("handoff hold ramp width", "1.5 m", line(GR, '- requirement["hold_displacement_max_m"]) / 1.5'), "displacement decay", "3b69446/cae2155"),
 ("handoff zone-speed ramp width", "10 km/h", line(GR, '- requirement["zone_speed_cap_kmh"]) / 10.0'), "zone speed decay", R7),
 ("DETOUR latency lateral trigger", "0.5 m", line(GR, '>= 0.5:'), "latency trigger", ""),
 ("DETOUR latency forward trigger", "2.0 m", line(GR, "initial_forward + 2.0"), "latency trigger", ""),
 ("default authority weight", 1.0, line(GR, "weight_table.get(scenario, 1.0)"), "fallback weight for unlisted scenario", "0909d77 notes 7 scenarios once silently defaulted to 1.0"),
]
for name, v, fn, purp, extra in F:
    first = "47a3ad0 2026-07-23 (slow*approach gating) / " if name.startswith("YIELD weights") else ""
    ev = G0 if not extra or extra[:7] in ("4feaf01", "3b69446") and False else G0
    if extra and extra.split()[0] in ("4feaf01", "3b69446", "3b69446/cae2155", "47a3ad0"):
        ev = f"{extra} (handoff graded mirror, review rounds 4-6)"
        extra2 = ""
    elif extra == R7:
        ev = R7; extra2 = ""
    else:
        extra2 = extra
    add(name, v, "credit" if "weight" in name or "factor" in name else "", "graded", fn, purp, "dimensionless credit" if "weight" in name or "factor" in name else "ramp geometry",
        "engineering choice", first + ev + (f"; {extra2}" if extra2 else ""), NOLIT + " for the specific number")

# ---- authority weights ----
W = {
 "green_stop": 1.50, "red_proceed": 1.50, "signal_off": 1.50, "crash_detour": 1.25, "fallen_person": 1.25,
 "unauthorized_go": 2.00, "adjacent_lane": 1.50, "flagger_control": 1.75, "ambulance_yield": 1.75, "occluded_officer": 2.00,
 "conflicting_authorities": 2.00, "sequential_directive": 2.00, "rule_hierarchy": 1.75, "ambiguous_gesture": 1.50,
 "fake_vest_director": 2.00, "two_civilians_disagree": 2.00, "civilian_warning_accident": 1.75, "flagger_slow_then_stop": 1.75,
 "emergency_scene_blocking": 1.50, "school_crossing_guard": 1.50, "barricade_self_detour": 1.50,
 "stale_directive_residue": 1.75, "out_of_jurisdiction_director": 1.50, "night_signal_officer_conflict": 2.00, "dual_authority_handoff": 2.00,
}
WEV = {
 **{k: G0 + " (table originally 'set when the benchmark had 9 scenarios' per 0909d77)" for k in list(W)[:14]},
 **{k: "0909d77 2026-07-02 'scoring: re-balance weights for the 21-scenario set' (body: 'Added on the existing logic (2.00 verify/conflict, 1.75 temporal/emergency, 1.50 exception/detour)')"
    for k in ("fake_vest_director", "two_civilians_disagree", "civilian_warning_accident", "flagger_slow_then_stop", "emergency_scene_blocking", "school_crossing_guard", "barricade_self_detour")},
 "stale_directive_residue": "d0c41b8 2026-07-19 'Add two validity scenarios: stale directive release and jurisdiction scoping'",
 "out_of_jurisdiction_director": "d0c41b8 2026-07-19",
 "night_signal_officer_conflict": "bb9a0b0 2026-07-20 'Add night_signal_officer_conflict scenario (suite 23 -> 24)'",
 "dual_authority_handoff": "f4949de 2026-07-20 'Add dual_authority_handoff scenario (suite 24 -> 25)'",
}
for k, v in W.items():
    add(f"SCENARIO_AUTHORITY_WEIGHTS.{k}", v, "weight", "graded", line(GR, f'"{k}": {v:.2f}'), "aggregate weight (normalised by weight sum)",
        "relative importance tier", "engineering choice", WEV[k] + "; inline comment: '" + _cache[GR][int(line(GR, f'"{k}": {v:.2f}').split(':')[-1]) - 1].split('#')[-1].strip() + "'",
        "tier logic stated categorically; 1.25 tier (crash_detour, fallen_person) has no stated rule; weight-sensitivity analysis exists (ab1ae40: 'tau 0.967 +/- 0.025') but is robustness, not rationale")

# ---- oracle-only margins ----
O = [
 ("oracle stop target (normal)", 2.5, "m", line(OR, 'return (1.3 if getattr(self, "_pull_up", False) else 2.5)'), "front-to-line stop target in v2 (+ half-length); v1 centre target", "1.0 m clearance + 1.5 m for control latency/brake ramp", "engineering choice", "1b31d25 2026-07-22 (v1, 'target >= 2.5 m short of the line; the margin absorbs the measured ~0.5 m of one-tick control latency'); v2 form " + V2 + "; docstring: 'strict centre clearance 1.0 m + 1.5 m for control latency and brake ramp'", "the ~0.5 m roll is described as measured but the measurement is not committed"),
 ("oracle pull-up target", 1.3, "m", line(OR, 'return (1.3 if getattr(self, "_pull_up", False) else 2.5)'), "front-to-line target when spawned inside the stop margin", "", "engineering choice", V2 + " (body: 'the oracle pulls up to the line when it spawns inside its stop margin'); comment: 'pull up to 1.3 m at a walking pace instead of staying parked short of the scene'", NOLIT + " for 1.3 (0.3 m above the 1.0 m front clearance)"),
 ("oracle pull-up trigger", 4.0, "m", line(OR, "dstop - self._half_length() < 4.0"), "pull-up mode if at rest and front < 4.0 m from line", "", "engineering choice", V2, "trigger approximated as centre distance minus half-length"),
 ("oracle pull-up rest speed", 0.5, "m/s", line(OR, "self._pull_up = speed < 0.5"), "'at rest' test", "", "engineering choice", V2, NOLIT),
 ("oracle pull-up speed cap", 2.0, "m/s", line(OR, "cap the pull-up at ~7 km/h"), "throttle cut above 2 m/s during pull-up", "~7 km/h; exceeds engagement 5 km/h", "engineering choice", V2, "cap sits above the 5 km/h movement gate, i.e. tuned so pull-up also satisfies engagement"),
 ("oracle braking envelope (normal)", "a=3.5 m/s^2, t=0.25 s", "", line(OR, "(2.0 * 3.5) + speed * 0.25"), "brake when remaining distance <= v^2/(2a)+v*t", "", "engineering choice", "1b31d25 2026-07-22", NOLIT),
 ("oracle braking envelope (pull-up)", "a=6.0 m/s^2, t=0.05 s", "", line(OR, "(2.0 * 6.0) + speed * 0.05"), "pull-up envelope", "", "engineering choice", V2 + "; comment: 'walking-pace pull-up: short latency term, firm braking'", NOLIT),
 ("oracle no-officer roll", 2.0, "m", line(OR, "if advanced < 2.0 and not _stopline_envelope_hit()"), "no-officer STOP rolls 2 m before stopping", "satisfies >=1 m movement gate", "engineering choice", "1b31d25 2026-07-22", "tuned to clear the engagement gate"),
 ("oracle handoff brake lead", 2.0, "m", line(OR, "self._approach_stop_gap_m or 0.0) - 2.0"), "start braking 2 m before roll target", "~14 km/h braking roll ~1.8 m", "empirical calibration", "4feaf01 2026-07-21; comment: 'review round 4 measured a 20.76 m stop against a 19.0 m target'", ""),
 ("oracle handoff creep speeds", "2.5 / 3.9 m/s", "m/s", line(OR, "elif speed > 3.9"), "throttle bands for ~14 km/h creep through SLOW zone", "under 18 km/h cap", "engineering choice", "9785f26 2026-07-21 (comment: 'Creep cap ~14 km/h: well under the strict 18 km/h zone cap')", ""),
 ("oracle handoff stop offset", 5.5, "m", "marshal_bench/configs/demo_dual_authority_handoff.yaml (expected_behavior.approach_stop_after_second_authority_m)", "stop target = second_authority.distance + 5.5", "", "engineering choice", "4feaf01 2026-07-21", "uses yaml flagger distance 16 m though telemetry shows flagger at 8.5 m; oracle s0 stopped at 13.39 m (inside band 10.0-17.5)"),
 ("oracle detour safety margin", "0.55 [0.45,0.70]", "m", line(OR, 'detour_safety_margin_m", 0.55'), "lateral offset margin beyond lane width", "", "engineering choice", "635c4ff 2026-07-18 'Multi-town oracle calibration (P3): town-aware stations, per-town gates'", NOLIT),
 ("oracle detour clearance", "crash: d+6(n-1)+5; block: d+12", "m", line(OR, '6.0 * (count - 1) + 5.0'), "forward distance at which detour considered cleared", "", "engineering choice", "635c4ff 2026-07-18", NOLIT),
 ("oracle merge blend distance", 12.0, "m", line(OR, "self._merge_blend_distance_m = 12.0"), "merge-back taper length", "", "engineering choice", "8530393 2026-07-19", NOLIT),
 ("oracle yield stop duration", 3.5, "s", line(OR, 'yield_stop_sec") or 3.5'), "YIELD resume time after onset", "", "engineering choice", D0, NOLIT),
 ("oracle pedestrian wait", 5.8, "s", line(OR, 'pedestrian_clear_sec") or 5.8'), "rule_hierarchy proceed wait after onset", "", "engineering choice", D0, "oracle t_safe in s0 telemetry was 4.8 s"),
 ("oracle initial throttle", 0.70, "throttle", line(OR, "carla.VehicleControl(throttle=0.70, brake=0.0, steer=0.0)"), "rolling start before directive", "", "engineering choice", "comment: 'Establish a real rolling approach before the directive... compliant STOP/HOLD episodes indistinguishable from a frozen controller'", "the oracle is explicitly tuned to create movement evidence for the gate"),
]
for name, v, u, fn, purp, phys, cls, ev, unc in O:
    add(name, v, u, "oracle", fn, purp, phys or "controller tuning", cls, ev, unc)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
cols = ["name","value","unit","used_in","function(file:line)","purpose","physical_interpretation","source_class","evidence","uncertainty_notes"]
with open(OUT, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(R)
print(len(R), "rows")
