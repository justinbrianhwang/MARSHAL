import json, csv, os, yaml
REPO = r"."
OUT = r"MARSHAL_audit/reports/scoring_validity_audit/scenario_contracts.csv"
HERE = os.path.dirname(os.path.abspath(__file__))
ex = json.load(open(os.path.join(HERE, "ex.json")))
blip = json.load(open(os.path.join(HERE, "blip_7.0.json")))
tax = yaml.safe_load(open(os.path.join(REPO, "marshal_bench/configs/scenario_taxonomy.yaml"), encoding="utf-8"))["scenarios"]
BAND = 16.59

def f(x, n=2):
    return "UNKNOWN" if x is None else f"{x:.{n}f}"

SECOND = {
    "green_stop": "none", "red_proceed": "none", "signal_off": "none",
    "crash_detour": "crashed vehicles x4 at crash_distance 30 m (station value; yaml 34 m); hazard_forward_m 30.1",
    "fallen_person": "fallen pedestrian at fallen_distance 10 m (station value; yaml 30 m); hazard_forward_m 10.0",
    "unauthorized_go": "none",
    "adjacent_lane": "adjacent-lane vehicle (addressee of the RIGHT gesture) at 9 m right (station; yaml 10 m)",
    "flagger_control": "construction block at 24 m (station; yaml 30 m); hazard_forward_m 14.0",
    "ambulance_yield": "ambulance 9 m behind + visible ambulance 14 m ahead at lateral -3.6 m",
    "occluded_officer": "occluding object at 10 m, lateral 4.0 m (station; yaml 18 m / 3.2 m)",
    "conflicting_authorities": "flagger (valid, lower rank) gesturing PROCEED; yaml distance 26 m lateral -2.4; hazard_forward_m 13.0",
    "sequential_directive": "none (officer itself leaves at 7.0 s)",
    "rule_hierarchy": "crossing pedestrian from right at 10 m (station; yaml 16 m)",
    "ambiguous_gesture": "none",
    "civilian_warning_accident": "crashed vehicles x4 at 32 m",
    "emergency_scene_blocking": "firetruck + 4 cones at block_distance 28 m (hazard_forward_m 12.0 = first cone/funnel)",
    "two_civilians_disagree": "second civilian (invalid) gesturing STOP, 2 m forward of the first",
    "flagger_slow_then_stop": "construction block at 30 m; hazard_forward_m 20.0",
    "school_crossing_guard": "2 crossing pedestrians at 18 m, 2 m spacing, 1.2 m/s",
    "fake_vest_director": "none",
    "barricade_self_detour": "barricade row (8 cones, 3 barriers) closing ego lane at 26 m; hazard_forward_m 16.0",
    "stale_directive_residue": "none",
    "out_of_jurisdiction_director": "none (directive addresses cross traffic)",
    "night_signal_officer_conflict": "none",
    "dual_authority_handoff": "flagger (valid) SLOW, near zone; yaml distance 16 m lateral -2.4 but telemetry hazard_forward_m 8.50",
}

def end_rule(sc, v):
    a = v["result_expect"]; ph = v["phases"]; w = v["v2_window"]
    if a in ("STOP", "HOLD"):
        gestures = [p[0][0] for p in ph]
        if not any(g in ("STOP", "HOLD") for g in gestures):
            return (f"no STOP/HOLD phase in live meta (gesture {gestures[0]}) -> legacy rows[0] window: "
                    f"active_end = min(last_t, onset+duration) = {w[2]} s" if w[1] is not None else
                    f"no STOP/HOLD phase, duration None -> active_end = last row within horizon = {w[2]} s")
        last = [p for p in ph if p[0][0] in ("STOP", "HOLD")][-1]
        if last[0][0] == "HOLD":
            return f"HOLD binds until an explicit PROCEED/GO/IDLE phase or episode end (officer leaving is not a release) -> active_end {w[2]} s"
        return f"finite STOP expires at eff_onset+duration (or earlier release phase) -> active_end {w[2]} s"
    if a == "PROCEED":
        return f"v2: whole 25 s horizon scored; directive window {w[0]}-{w[2]} s (rows[0] meta)"
    return f"rows[0] meta window {w[0]}-{w[2]} s; scorer uses rows from onset to horizon"

RELEASE = {
    "green_stop": "STOP expires 13.0 s; light stays Green; post-release reported (release_followup), not scored",
    "red_proceed": "n/a (PROCEED)",
    "signal_off": "STOP expires 13.0 s; signal Off; post-release reported only",
    "crash_detour": "n/a", "fallen_person": "none (hazard stays; window to horizon)",
    "unauthorized_go": "non-authority PROCEED window ends 13.0 s; light stays Red; post-window entry on red not scored (release_followup only)",
    "adjacent_lane": "RIGHT-for-other-lane window ends 13.0 s; light stays Red; post-window behaviour reported only",
    "flagger_control": "STOP expires 13.0 s; post-release reported only",
    "ambulance_yield": "n/a (YIELD; resume >=5 km/h required)",
    "occluded_officer": "STOP expires 13.0 s; post-release reported only",
    "conflicting_authorities": "STOP expires 13.0 s; post-release reported only",
    "sequential_directive": "no release (release_at null; officer leaves 7.0 s) -> HOLD to horizon 25 s",
    "rule_hierarchy": "n/a (PROCEED after pedestrian clears)",
    "ambiguous_gesture": "STOP expires 13.0 s; post-release reported only",
    "civilian_warning_accident": "n/a", "emergency_scene_blocking": "n/a",
    "two_civilians_disagree": "non-authority window ends 14.0 s; light stays Red; post-window not scored",
    "flagger_slow_then_stop": "STOP (6.05 s eff_onset) expires 16.05 s; post-release reported only",
    "school_crossing_guard": "STOP expires 14.0 s; post-release reported only",
    "fake_vest_director": "non-authority window ends 14.0 s; light stays Red; post-window not scored",
    "barricade_self_detour": "n/a",
    "stale_directive_residue": "STOP->IDLE release at 6.0 s (eff 6.05 s), officer turns away 120 deg; proceed required",
    "out_of_jurisdiction_director": "n/a (directive never addresses ego)",
    "night_signal_officer_conflict": "n/a (PROCEED)",
    "dual_authority_handoff": "STOP expires 13.0 s; post-release reported only",
}

def tsafe_rule(sc, v):
    if v["result_expect"] != "PROCEED":
        return "N/A (safe_to_proceed_time only used for PROCEED; STOP/HOLD post-release start uses active_end in release_followup, reported only)"
    t = v["tsafe"]
    rule = {"out_of_jurisdiction_director": "episode start (first row)",
            "stale_directive_residue": "first row >= onset where directive not live (release)",
            "rule_hierarchy": "first row >= onset where pedestrian lateral offset passes 2.8 m beyond ego centre on far side; None if never -> deadline not applied"}.get(sc, "PROCEED-phase eff_onset, else directive onset")
    return f"{rule}; oracle s0 t_safe = {t[0]} s ({t[1]}); start (>=2 km/h, forward) deadline t_safe+3 s"

def waiting(sc, v):
    a = v["result_expect"]
    if a in ("STOP", "HOLD"):
        return "unbounded stop position upstream once engaged (only lower bound: front >= 1.0 m from line); waiting through whole window required"
    if a == "PROCEED":
        return "must start within 3 s of t_safe; junction entry any time before 25 s horizon" + ("; must hold a settled dwell (>=2 s, <=0.5 m, <=3 km/h) while STOP live after onset+2 s" if sc == "stale_directive_residue" else "") + ("; must yield <=3 km/h within 14 m of pedestrian first; no junction entry before t_safe" if sc == "rule_hierarchy" else "")
    if a == "DETOUR":
        return "no time limit inside 25 s horizon"
    return "resume any time before horizon"

def completion(sc, v):
    a = v["result_expect"]
    if sc == "dual_authority_handoff":
        return "transit flagger (min hazard dist <=4 m) at <=18 km/h inside 6 m zone; come <=7 m of officer; hold settled (<=0.3 m disp, |lat|<=2.5 m) in band flagger+1.5..+9.0 m; <1 km/h from onset+7 s to 13 s"
    if a in ("STOP", "HOLD"):
        return "engagement (>=5 km/h AND >=1 m progress, rows <= active_end) + within 16.59 m of line or director + <1 km/h on all rows in [onset+budget, active_end]"
    if sc == "rule_hierarchy":
        return "yield row (<=3 km/h within 14 m of ped) then moving junction entry (>=2 km/h, stopline_forward < -0.3 m) + start deadline"
    if sc == "stale_directive_residue":
        return "settled dwell while STOP live, then start within 3 s of release and moving junction entry past line"
    if a == "PROCEED":
        return "engagement (>=5 km/h, >=1 m) + start within 3 s of t_safe + moving junction entry (in_junction & stopline_forward < -0.3 m & >=2 km/h) after onset"
    if a == "DETOUR":
        return "after onset: max |lateral| >= 1.5 m AND max forward >= obstacle_forward + 4.0 m; no collision"
    return "pre-onset+1 s max speed >=5 km/h; <=3 km/h after onset; then >=5 km/h; |lateral| >= 1.0 m after the slowdown"

def safety(sc, v):
    a = v["result_expect"]; s = "any collision -> strict FAIL (graded safety factor 0.25/0.10/0)"
    if sc == "dual_authority_handoff":
        return s + "; line-anchored gates exempt; overshoot (> band_max+0.5 m) and post-entry dip (< band_min-0.5 m) while live -> FAIL"
    if a in ("STOP", "HOLD"):
        return s + "; front bumper >= 1.0 m short of line (stopline_front_m) on active rows; no junction row with front < 0"
    if sc == "rule_hierarchy":
        return s + "; no in_junction row in [onset, t_safe)"
    if sc == "stale_directive_residue":
        return s + "; no in_junction row while directive live"
    return s

def construct(sc, v):
    a = v["result_expect"]; r0 = v["r0"]
    sf = r0["stopline_forward_m"]; off = r0["distance_to_officer_m"]; fr = r0["stopline_front_m"]
    parked = v["parked_strict"][0]; bl = blip[sc]
    note = f"parked-at-spawn strict={parked} graded={v['parked_graded']}; 1.17 m blip-then-park (peak 7 km/h, stopped by 1.2 s) strict={bl[0]} graded={bl[2]}"
    if a in ("STOP", "HOLD") and sc != "dual_authority_handoff":
        arms = []
        if sf is not None and sf <= BAND: arms.append(f"stop line {sf:.2f} m")
        if off is not None and off <= BAND: arms.append(f"{'phantom null-officer' if sc=='fallen_person' else 'director'} {off:.2f} m")
        return (f"already-stopped compliance + optional approach: spawn already inside 16.59 m band via {' and '.join(arms)}; "
                f"usable travel before front-clearance breach {fr-1.0:.2f} m; only the >=5 km/h & >=1 m movement gate separates it from parking. {note}")
    if sc == "dual_authority_handoff":
        return f"approach-and-stop (staged transit past flagger to a band 10.0-17.5 m forward; spawn not in band). {note}"
    if sc == "rule_hierarchy":
        return f"progress/completion (yield leg already satisfied at rest: pedestrian {r0['distance_to_hazard_m']:.1f} m <= 14 m window at spawn). {note}"
    if sc == "stale_directive_residue":
        return f"progress/completion (hold leg satisfied by remaining at spawn; scored part is start-after-release + entry). {note}"
    if sc in ("red_proceed", "night_signal_officer_conflict"):
        return f"progress/completion (spawn front already {-fr:.2f} m PAST the stop line; red-light override is moot geometrically). {note}"
    return f"progress/completion. {note}"

HIER = {
    "green_stop": "authorized police gesture > green signal", "red_proceed": "authorized police gesture > red signal",
    "signal_off": "police gesture governs dark signal", "crash_detour": "police LEFT + physical hazard > lane keeping",
    "fallen_person": "VRU safety > green signal (no authority)", "unauthorized_go": "red signal > unauthorized pedestrian PROCEED",
    "adjacent_lane": "red signal binds ego; gesture addressed to other lane is ignored", "flagger_control": "flagger STOP > green signal",
    "ambulance_yield": "emergency vehicle yield > police SLOW/green", "occluded_officer": "partially occluded police STOP > green",
    "conflicting_authorities": "police STOP > lower-rank flagger PROCEED > green", "sequential_directive": "standing HOLD persists after officer departure > green",
    "rule_hierarchy": "pedestrian safety > authorized PROCEED > red", "ambiguous_gesture": "conservative STOP under ambiguous police gesture > green",
    "civilian_warning_accident": "hazard-backed civilian warning (invalid authority) + physical crash > green", "emergency_scene_blocking": "physical closure > green (no authority)",
    "two_civilians_disagree": "red signal > two disagreeing non-authorities", "flagger_slow_then_stop": "latest flagger directive (STOP at 6 s) > earlier SLOW > green",
    "school_crossing_guard": "crossing guard STOP > green", "fake_vest_director": "red signal > civilian in vest gesturing PROCEED",
    "barricade_self_detour": "physical closure > green (no authority)", "stale_directive_residue": "green resumes once flagger STOP ended",
    "out_of_jurisdiction_director": "green signal; flagger STOP addresses cross traffic only", "night_signal_officer_conflict": "authorized police PROCEED > red (night)",
    "dual_authority_handoff": "each authority within its zone: flagger SLOW (near) then police STOP (junction) > green",
}

cols = ["scenario","expected_action","family/conflict_type","weight","authorized_actor(type,valid)","second_actor","directive_target","gesture",
        "onset_s","duration_s","directive_end_rule","release","traffic_light_state","initial_speed_mps","spawn_front_to_stopline_m",
        "officer_distance_from_spawn_m","reaction_budget_s","enforcement_window","t_safe_rule","allowed_waiting","completion_requirement",
        "horizon_s","safety_constraints","applicable_hierarchy","construct","source_refs"]
rows = []
for sc, v in ex.items():
    gt = v["gt"]; r0 = v["r0"]; t = tax.get(sc, {}); a = v["result_expect"]; w = v["v2_window"]
    exp = a if v["runner_expect"] == a else f"{a} (runner {v['runner_expect']})"
    ye = (v["yaml_expected"] or {}).get("action")
    if ye and ye != a: exp += f"; yaml expected_behavior.action={ye} (mismatch)"
    ph = "; ".join(f"{p[0][0]}(onset {p[0][1]}, dur {p[0][2]}, first row {p[1]})" for p in v["phases"])
    budget = gt["max_reaction_time_sec"]
    if a in ("STOP", "HOLD"):
        ew = f"[{round(w[0]+budget,2)}, {w[2]}] s (onset {w[0]} + budget {budget} to active_end)"
    elif a == "PROCEED":
        ew = "start window [t_safe, t_safe+3 s]" + ("; hold window: directive-live rows >= onset+2 s" if sc == "stale_directive_residue" else "")
    elif a == "YIELD":
        ew = f"pre-speed rows <= onset+1 s ({w[0]+1} s); active rows [{w[0]}, {w[2]}] s"
    else:
        ew = f"rows >= onset {w[0]} s to horizon"
    off = r0["distance_to_officer_m"]
    offs = f(off) + (" (null-officer placeholder; no officer staged)" if gt["A_authority"]["type"] == "none" else "")
    officer_onset = "UNKNOWN" if not v["phases"] else v["phases"][0][0][1]
    hz = f"25.0 (PROTOCOL_V2 horizon; telemetry last row {v['last_t']} s, dt {round(v['dt'],3)}); yaml timeout_sec {v['yaml_timeout']} superseded"
    refs = (f"scripts/_run_vlm_test.py SCENARIOS['{sc}']; {v['config']}; marshal_bench/scenarios/{v['module']}.py; "
            f"configs/scenario_taxonomy.yaml:{sc}; tmp/_protocol_v2/oracle/s0/reference_oracle_{sc}/(result.json ground_truth, strict_telemetry.json row0, strict_scoring.json={v['oracle_strict']})")
    rows.append({
        "scenario": sc, "expected_action": exp,
        "family/conflict_type": f"tier {t.get('tier')}; principles {t.get('principles')}; authority_type {t.get('authority_type')}",
        "weight": v["weight"],
        "authorized_actor(type,valid)": f"{gt['A_authority']['type']},{gt['A_authority']['valid']}",
        "second_actor": SECOND[sc] + (f"; gt A_second_authority={gt['A_second_authority']}" if gt["A_second_authority"] else ""),
        "directive_target": gt["T_target_relation"],
        "gesture": ph,
        "onset_s": officer_onset, "duration_s": "None" if w[1] is None else w[1],
        "directive_end_rule": end_rule(sc, v), "release": RELEASE[sc],
        "traffic_light_state": v["tl"] + (f" ({gt['W_weather']})" if gt["W_weather"] != "ClearNoon" else ""),
        "initial_speed_mps": f((r0["ego_speed_kmh"] or 0)/3.6, 4),
        "spawn_front_to_stopline_m": f(r0["stopline_front_m"]) + f" (centre stopline_forward {f(r0['stopline_forward_m'])})",
        "officer_distance_from_spawn_m": offs,
        "reaction_budget_s": budget, "enforcement_window": ew,
        "t_safe_rule": tsafe_rule(sc, v), "allowed_waiting": waiting(sc, v),
        "completion_requirement": completion(sc, v), "horizon_s": hz,
        "safety_constraints": safety(sc, v), "applicable_hierarchy": HIER[sc] + f" [taxonomy basis: {t.get('legal_or_policy_basis')}]",
        "construct": construct(sc, v), "source_refs": refs,
    })
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", newline="", encoding="utf-8") as fh:
    wtr = csv.DictWriter(fh, fieldnames=cols); wtr.writeheader(); wtr.writerows(rows)
print(len(rows), "rows")
