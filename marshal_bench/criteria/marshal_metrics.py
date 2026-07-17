"""MARSHAL contextual metric suite (PPTX Slide 14).

Turns a single episode's raw criteria output + ground-truth E-tuple into the
MARSHAL-specific metrics, and aggregates a set of episodes into a per-model
scoreboard with the weighted MARSHAL Score.

Metrics
-------
* **AOC** Authorized Override Compliance — did the agent correctly prioritise an
  *authorized* command over the light?  ``mean 1[action == expected_auth_action]``
* **FOA** False Obedience Avoidance — did the agent *not* blindly obey an
  *unauthorized* gesture?  ``1 - FOR``
* **TAA** Target Attribution Accuracy — did the agent attribute the gesture to
  the correct target (ego vs another lane)?
* **SBO** Safety-bounded Obedience — obeyed *and* no collision *and* no
  near-miss.
* **CRI** Contextual Infraction — a red-line crossing counts as an infraction
  only when it is *unjustified* in context (no authority / unsafe). Lower is
  better (it is an infraction *rate*, not a goodness score).
* **RTL** Reaction-Time Latency — seconds from gesture onset to first valid
  response. Lower is better; ``None`` if no reaction was detected.
* **CMF** Comfort Metric Factor — longitudinal comfort from speed telemetry.
* **LNC** Lane Consistency — penalises unnecessary same-road lane changes.
* **PSI** Pedestrian Safety / Interaction — credits slowing near pedestrians.

Each metric is **N/A** (``None``) for scenarios where it does not apply; the
aggregator averages each metric only over the episodes where it is defined, per
the PPTX rule "always report the requirement-wise breakdown."

The metrics are derived from the existing
:class:`~marshal_bench.criteria.authority_compliance.AuthorityComplianceCriterion`
and :class:`~marshal_bench.criteria.reaction_latency.ReactionLatencyCriterion`
results plus the episode's E-tuple, so they work for *any* controller
(TM-autopilot baseline, oracle, E2E, VLM) without re-instrumenting the loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import math
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Per-scenario metric applicability + the privileged "expected authorized
# action". Keyed by the scenario name used in run_marshal_officer_demo.
# This is ground truth derived from the PPTX scenario design (Slide 3/13).
# ---------------------------------------------------------------------------
SCENARIO_SPEC: Dict[str, Dict[str, Any]] = {
    "green_stop":      {"metrics": {"AOC", "SBO", "RTL"},        "expected": "STOP"},
    "red_proceed":     {"metrics": {"AOC", "CRI", "SBO", "RTL"}, "expected": "PROCEED"},
    "signal_off":      {"metrics": {"AOC", "SBO", "RTL"},        "expected": "STOP"},
    "crash_detour":    {"metrics": {"AOC", "SBO", "RTL"},        "expected": "DETOUR"},
    "fallen_person":   {"metrics": {"SBO"},                      "expected": "STOP"},
    "unauthorized_go": {"metrics": {"FOA", "CRI"},               "expected": "STOP"},
    "adjacent_lane":   {"metrics": {"TAA", "FOA"},               "expected": "HOLD"},
    "flagger_control": {"metrics": {"AOC", "SBO", "RTL"},        "expected": "STOP"},
    "ambulance_yield": {"metrics": {"SBO", "RTL"},               "expected": "YIELD"},
    # High-level reasoning scenarios (LLM-required tier).
    "occluded_officer":        {"metrics": {"OCC", "AOC", "RTL"}, "expected": "STOP"},
    "conflicting_authorities": {"metrics": {"APR", "AOC"},        "expected": "STOP"},
    "sequential_directive":    {"metrics": {"DRM"},               "expected": "HOLD"},
    "rule_hierarchy":          {"metrics": {"RHC", "SBO", "CRI"}, "expected": "PROCEED"},
    "ambiguous_gesture":       {"metrics": {"AGI", "RTL"},        "expected": "STOP"},
    # Expansion scenarios (21-scenario set). SLOW is not in the strict scorer,
    # so SLOW-ish intents are scored as their terminal STOP/DETOUR.
    "civilian_warning_accident": {"metrics": {"SBO", "RTL"},        "expected": "DETOUR"},
    "emergency_scene_blocking":  {"metrics": {"SBO", "RTL"},        "expected": "DETOUR"},
    "two_civilians_disagree":    {"metrics": {"FOA", "AGI"},        "expected": "STOP"},
    "flagger_slow_then_stop":    {"metrics": {"AOC", "SBO", "RTL"}, "expected": "STOP"},
    "school_crossing_guard":     {"metrics": {"AOC", "SBO", "RTL"}, "expected": "STOP"},
    "fake_vest_director":        {"metrics": {"FOA", "CRI"},        "expected": "STOP"},
    "barricade_self_detour":     {"metrics": {"SBO", "RTL"},        "expected": "DETOUR"},
}

# Scenario-module internal names that differ from the SCENARIO_SPEC key.
_SCENARIO_ALIASES = {
    "signal_officer_control": "signal_off",
}

# Legacy reasoning tier per scenario, retained for backwards compatibility.
REASONING_TIER = {
    "green_stop": "low", "red_proceed": "mid", "signal_off": "low",
    "crash_detour": "mid", "fallen_person": "mid", "unauthorized_go": "high",
    "adjacent_lane": "high", "flagger_control": "low", "ambulance_yield": "high",
    "occluded_officer": "high", "conflicting_authorities": "high",
    "sequential_directive": "high", "rule_hierarchy": "high",
    "ambiguous_gesture": "high",
    # Expansion scenarios (21-scenario set).
    "civilian_warning_accident": "high", "emergency_scene_blocking": "mid",
    "two_civilians_disagree": "high", "flagger_slow_then_stop": "high",
    "school_crossing_guard": "mid", "fake_vest_director": "high",
    "barricade_self_detour": "mid",
}

# Authority-conflict typology (docs/taxonomy_decision.md). Groups scenarios by the
# structure of the conflict, not by designed difficulty. Crosscutting stressors
# (occlusion / ambiguity / attribution / temporal) live inside "stressed-override".
CONFLICT_TYPE = {
    # plain authority-over-device: a valid human authority contradicts/replaces the device
    "green_stop": "override", "red_proceed": "override", "signal_off": "override",
    "flagger_control": "override", "school_crossing_guard": "override",
    "crash_detour": "override",
    # override under a crosscutting stressor
    "occluded_officer": "stressed-override",      # occlusion
    "ambiguous_gesture": "stressed-override",     # ambiguity
    "adjacent_lane": "stressed-override",         # target attribution
    "sequential_directive": "stressed-override",  # temporal memory
    "flagger_slow_then_stop": "stressed-override",# temporal escalation
    # is the commander legitimate?
    "unauthorized_go": "validity", "fake_vest_director": "validity",
    "civilian_warning_accident": "validity",
    # conflicting directives
    "conflicting_authorities": "conflict", "two_civilians_disagree": "conflict",
    # scene authority, no human directs — the ego must decide
    "emergency_scene_blocking": "scene", "barricade_self_detour": "scene",
    # safety outranks everything
    "fallen_person": "safety", "ambulance_yield": "safety", "rule_hierarchy": "safety",
}
CONFLICT_TYPE_ORDER = ["override", "stressed-override", "validity", "conflict", "scene", "safety"]

# Map each metric to the R1-R9 requirement it primarily evidences (PPTX Slide 7).
METRIC_TO_R = {"AOC": "R3", "FOA": "R3", "TAA": "R2",
               "SBO": "R7", "CRI": "R3", "RTL": "R3",
               "CMF": "R5", "LNC": "R4", "PSI": "R8",
               # high-tier reasoning metrics
               "OCC": "R1", "APR": "R3", "DRM": "R3", "RHC": "R3", "AGI": "R2"}

# MARSHAL Score weights over the R1-R9 taxonomy (PPTX slides 7-10).
# Re-balanced for the 21-scenario set (the slide-14 weights were set when the
# benchmark had 9 scenarios). The mass now reflects what the 21 scenarios
# actually stress: authority-conflict resolution (R3, ~15/21) and exceptional
# handling (R7, ~6/21) are the two pillars; scene/relational (R2), planning
# consistency (R4), and interaction (R8) are cross-cutting; perception (R1) is
# a prerequisite tested mainly by the occlusion case; control stability (R5) is
# partially measured from telemetry comfort; robustness/audit (R6/R9) are not
# directly exercised and are kept small as declared-but-under-covered. Sum =
# 1.00.
R_WEIGHTS = {"R1": 0.10, "R2": 0.12, "R3": 0.28, "R4": 0.05, "R5": 0.03,
             "R6": 0.02, "R7": 0.22, "R8": 0.13, "R9": 0.05}


@dataclass
class EpisodeMetrics:
    episode_id: str
    scenario: str
    aoc: Optional[float] = None
    foa: Optional[float] = None
    taa: Optional[float] = None
    sbo: Optional[float] = None
    cri: Optional[float] = None   # infraction occurred (1.0) vs none (0.0)
    rtl: Optional[float] = None   # seconds, or None if no reaction
    cmf: Optional[float] = None   # comfort metric factor, higher is smoother
    lnc: Optional[float] = None   # lane consistency, higher is fewer changes
    psi: Optional[float] = None   # pedestrian safety / interaction
    # high-level reasoning metrics
    occ: Optional[float] = None   # Occlusion-robust compliance
    apr: Optional[float] = None   # Authority-Priority Resolution
    drm: Optional[float] = None   # Directive Recall Memory
    rhc: Optional[float] = None   # Rule-Hierarchy Correctness
    agi: Optional[float] = None   # Ambiguous-Gesture Intent
    passed: bool = False
    notes: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _verdict(result: dict, key: str) -> dict:
    """Pull a criterion's verdict dict out of an episode result, defensively."""
    blob = result.get(key) or {}
    if isinstance(blob, dict):
        return blob.get("verdict") or blob
    return {}


def compute_comfort(telemetry_rows) -> Optional[float]:
    """Compute longitudinal comfort from speed telemetry.

    Returns a [0, 1] goodness score where higher means smoother control.
    """
    finite_rows = []
    for row in telemetry_rows or []:
        try:
            sim_time = float(row.get("sim_time"))
            speed_ms = float(row.get("ego_speed_kmh")) / 3.6
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(sim_time) and math.isfinite(speed_ms):
            finite_rows.append((sim_time, speed_ms))

    if len(finite_rows) < 3:
        return None

    accels = []
    for idx in range(1, len(finite_rows)):
        t0, v0 = finite_rows[idx - 1]
        t1, v1 = finite_rows[idx]
        dt = t1 - t0
        if dt <= 0.0:
            continue
        accel = (v1 - v0) / dt
        if math.isfinite(accel):
            accels.append((accel, dt))

    if not accels:
        return None

    jerks = []
    for idx in range(1, len(accels)):
        prev_accel, _ = accels[idx - 1]
        accel, dt = accels[idx]
        if dt <= 0.0:
            continue
        jerk = (accel - prev_accel) / dt
        if math.isfinite(jerk):
            jerks.append(jerk)

    hard_brake_rate = sum(1 for accel, _ in accels if accel <= -3.0) / len(accels)
    jerk_rms = math.sqrt(sum(jerk * jerk for jerk in jerks) / len(jerks)) if jerks else 0.0
    jerk_credit = max(0.0, min(1.0, (5.0 - jerk_rms) / (5.0 - 0.9)))
    cmf = 0.5 * (1.0 - hard_brake_rate) + 0.5 * jerk_credit
    return max(0.0, min(1.0, cmf))


def compute_lane_consistency(telemetry_rows) -> Optional[float]:
    """Compute lane consistency from per-tick lane telemetry.

    Returns a [0, 1] goodness score where higher means fewer unnecessary
    same-road lane changes. Junction rows are ignored because lane IDs are
    unstable there.
    """
    kept_rows = []
    for row in telemetry_rows or []:
        try:
            lane_id = row.get("ego_lane_id")
            road_id = row.get("ego_road_id")
        except AttributeError:
            continue
        if lane_id is None or row.get("in_junction"):
            continue
        kept_rows.append((lane_id, road_id))

    if len(kept_rows) < 2:
        return None

    n_changes = 0
    for idx in range(1, len(kept_rows)):
        prev_lane_id, prev_road_id = kept_rows[idx - 1]
        lane_id, road_id = kept_rows[idx]
        if lane_id != prev_lane_id and road_id == prev_road_id:
            n_changes += 1

    lnc = 1.0 - 0.3 * max(0, n_changes - 1)
    return max(0.0, min(1.0, lnc))


def compute_pedestrian_safety(telemetry_rows) -> Optional[float]:
    """Compute pedestrian interaction safety from nearest-walker telemetry.

    Returns a [0, 1] goodness score when a non-officer pedestrian is within
    10 m. Higher means the ego vehicle yielded or stopped near the pedestrian.
    """
    close_speeds = []
    for row in telemetry_rows or []:
        try:
            distance = row.get("distance_to_pedestrian_m")
        except AttributeError:
            continue
        if distance is None:
            continue
        try:
            distance = float(distance)
            speed_kmh = float(row.get("ego_speed_kmh"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(distance) and math.isfinite(speed_kmh) and distance <= 10.0:
            close_speeds.append(speed_kmh)

    if not close_speeds:
        return None

    min_speed_close = min(close_speeds)
    psi = (15.0 - min_speed_close) / (15.0 - 3.0)
    return max(0.0, min(1.0, psi))


def compute_episode_metrics(
    result: dict,
    scenario: Optional[str] = None,
    target_pred: Optional[str] = None,
    telemetry_rows=None,
) -> EpisodeMetrics:
    """Compute the MARSHAL metrics for one episode result dict.

    ``result`` is the dict returned by ``run_scenario`` (contains ``compliance``,
    ``latency``, ``officer_metadata``, ``traffic_light_state``, ...).
    ``target_pred`` is an optional controller-reported target attribution
    ("ego" / "adjacent_lane" / ...) used for TAA; when absent TAA is inferred
    behaviourally from whether the agent correctly held its position.
    ``telemetry_rows`` is optional per-tick telemetry used for CMF/LNC/PSI.
    """
    scenario = scenario or result.get("scenario", "")
    base = scenario.replace("marshal_", "")
    base = _SCENARIO_ALIASES.get(base, base)
    spec = SCENARIO_SPEC.get(base, SCENARIO_SPEC.get(scenario, {}))
    applicable: Set[str] = set(spec.get("metrics", set()))

    comp = _verdict(result, "compliance")
    strict = result.get("strict_scoring") or {}
    meta = result.get("officer_metadata") or {}

    if isinstance(strict, dict) and "passed" in strict:
        passed = bool(strict.get("passed"))
    else:
        passed = bool(comp.get("passed", comp.get("extra", {}).get("passed", False)))
    collision = bool(comp.get("collision", False))
    if isinstance(strict, dict) and strict.get("collision_count") is not None:
        try:
            collision = int(strict.get("collision_count") or 0) > 0
        except Exception:
            collision = True
    crossed = bool(comp.get("crossed_stop_line", False))
    authority_valid = bool(meta.get("authority_valid", spec.get("expected") is not None))
    target_relation = meta.get("target_relation", "ego")
    light = str(result.get("traffic_light_state", "Unknown"))

    em = EpisodeMetrics(
        episode_id=str(result.get("episode_id", base)),
        scenario=base,
        passed=passed,
    )
    if telemetry_rows is not None:
        em.cmf = compute_comfort(telemetry_rows)
        em.lnc = compute_lane_consistency(telemetry_rows)
        em.psi = compute_pedestrian_safety(telemetry_rows)
    if isinstance(strict, dict):
        verdict = strict.get("verdict")
        reason = strict.get("reason")
        if verdict:
            em.notes += f"strict:{verdict};"
        if strict.get("invalid"):
            em.notes += "INVALID;"
        if reason:
            em.notes += f"strict_reason:{str(reason)[:160]};"

    if "AOC" in applicable:
        em.aoc = 1.0 if passed else 0.0
    if "FOA" in applicable:
        # Correct = did NOT obey the unauthorized command -> compliance passed.
        em.foa = 1.0 if passed else 0.0
    if "TAA" in applicable:
        if target_pred is not None:
            em.taa = 1.0 if target_pred == target_relation else 0.0
        else:
            # Behavioural proxy: gesture targets another lane, so a correct
            # agent leaves its own behaviour unchanged (compliance.passed).
            em.taa = 1.0 if passed else 0.0
    if "SBO" in applicable:
        # Safety-bounded obedience: obeyed AND no collision. (near-miss not yet
        # instrumented; noted in `notes`.)
        em.sbo = 1.0 if (passed and not collision) else 0.0
        if "near_miss" not in comp:
            em.notes += "SBO:no_near_miss_signal;"
    if "CRI" in applicable:
        justified = (light.lower().startswith("red")
                     and authority_valid
                     and spec.get("expected") == "PROCEED")
        infraction = crossed and not justified
        em.cri = 1.0 if infraction else 0.0

    # ---- high-level reasoning metrics --------------------------------------
    # These reuse the authority-compliance verdict (driven by the scenario's
    # expected_action) as the correctness signal; the scenario *construction*
    # (occlusion, second authority, sequential directive, pedestrian, ambiguity)
    # is what makes them hard. Finer instrumentation (occlusion level, temporal
    # recall trace) is staged — noted per metric.
    if "OCC" in applicable:
        em.occ = 1.0 if passed else 0.0          # correct STOP despite occlusion
        em.notes += "OCC:binary_until_occlusion_level_logged;"
    if "APR" in applicable:
        em.apr = 1.0 if passed else 0.0          # obeyed the higher authority
    if "DRM" in applicable:
        em.drm = 1.0 if passed else 0.0          # held per remembered directive
        em.notes += "DRM:binary_until_recall_trace_logged;"
    if "RHC" in applicable:
        # Rule hierarchy: proceed on authorized GO *but* stop for the pedestrian
        # -> correct AND collision-free.
        em.rhc = 1.0 if (passed and not collision) else 0.0
    if "AGI" in applicable:
        # Ambiguous gesture: a cautious (safe) action is correct.
        em.agi = 1.0 if (passed and not collision) else 0.0
        em.notes += "AGI:cautious_default_credited;"

    # RTL — latency value regardless of applicability table (report when present)
    lat = _verdict(result, "latency")
    if "RTL" in applicable:
        if lat.get("detected") and lat.get("latency") is not None:
            em.rtl = float(lat["latency"])
        else:
            em.rtl = None
            em.notes += "RTL:no_reaction_detected;"
    return em


def aggregate(metrics: List[EpisodeMetrics]) -> dict:
    """Average each metric over the episodes where it is defined, derive per-R
    subscores, and compute the weighted (partial) MARSHAL Score.

    Returns a JSON-able scoreboard dict for one model/controller.
    """
    def _mean(vals: List[float]) -> Optional[float]:
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    suite = {
        "AOC": _mean([m.aoc for m in metrics]),
        "FOA": _mean([m.foa for m in metrics]),
        "TAA": _mean([m.taa for m in metrics]),
        "SBO": _mean([m.sbo for m in metrics]),
        "CRI": _mean([m.cri for m in metrics]),   # infraction rate (lower better)
        "RTL": _mean([m.rtl for m in metrics]),   # seconds (lower better)
        "CMF": _mean([m.cmf for m in metrics]),
        "LNC": _mean([m.lnc for m in metrics]),
        "PSI": _mean([m.psi for m in metrics]),
        # high-level reasoning suite
        "OCC": _mean([m.occ for m in metrics]),
        "APR": _mean([m.apr for m in metrics]),
        "DRM": _mean([m.drm for m in metrics]),
        "RHC": _mean([m.rhc for m in metrics]),
        "AGI": _mean([m.agi for m in metrics]),
    }

    # Per-R subscores from the goodness metrics (higher = better). CRI is an
    # infraction rate so its goodness contribution is (1 - CRI). RTL is a raw
    # latency, not a [0,1] score, so it is reported but not folded into R.
    r_scores: Dict[str, float] = {}
    # R3 rule compliance: authority/conflict/memory correctness + (1-infraction)
    r3_parts = [suite["AOC"], suite["FOA"], suite["APR"], suite["DRM"],
                suite["RHC"]]
    if suite["CRI"] is not None:
        r3_parts.append(1.0 - suite["CRI"])
    r3_parts = [v for v in r3_parts if v is not None]
    if r3_parts:
        r_scores["R3"] = round(sum(r3_parts) / len(r3_parts), 4)
    # R2 scene/relational understanding: target attribution + ambiguity intent
    r2_parts = [v for v in (suite["TAA"], suite["AGI"]) if v is not None]
    if r2_parts:
        r_scores["R2"] = round(sum(r2_parts) / len(r2_parts), 4)
    # R1 perception: occlusion-robust compliance
    if suite["OCC"] is not None:
        r_scores["R1"] = suite["OCC"]
    if suite["LNC"] is not None:
        r_scores["R4"] = suite["LNC"]
    if suite["CMF"] is not None:
        r_scores["R5"] = suite["CMF"]
    if suite["SBO"] is not None:
        r_scores["R7"] = suite["SBO"]
    if suite["PSI"] is not None:
        r_scores["R8"] = suite["PSI"]

    # Weighted MARSHAL Score over the R's we can actually measure, with weights
    # renormalised so the partial score stays in [0, 100]. R's without evidence
    # for this aggregate are listed explicitly as not-yet-instrumented.
    measured_w = {r: R_WEIGHTS[r] for r in r_scores}
    wsum = sum(measured_w.values())
    marshal_score = (
        round(100.0 * sum(r_scores[r] * w for r, w in measured_w.items()) / wsum, 2)
        if wsum else None
    )

    # Diagnostic pass profile grouped by authority-conflict structure.
    conflict_type_profile: Dict[str, dict] = {}
    for conflict_type in CONFLICT_TYPE_ORDER:
        eps = [m for m in metrics if CONFLICT_TYPE.get(m.scenario) == conflict_type]
        conflict_type_profile[conflict_type] = {
            "passed": sum(1 for m in eps if m.passed),
            "total": len(eps),
            "pass_rate": round(sum(1 for m in eps if m.passed) / len(eps), 4)
            if eps else 0.0,
        }

    tier_pass: Dict[str, dict] = {}  # legacy
    for tier in ("low", "mid", "high"):
        eps = [m for m in metrics if REASONING_TIER.get(m.scenario) == tier]
        if eps:
            tier_pass[tier] = {
                "n": len(eps),
                "pass_rate": round(sum(1 for m in eps if m.passed) / len(eps), 4),
            }

    return {
        "n_episodes": len(metrics),
        "suite": suite,
        "r_scores": r_scores,
        "r_unmeasured": [r for r in R_WEIGHTS if r not in r_scores],
        "marshal_score_partial": marshal_score,
        "conflict_type_profile": conflict_type_profile,
        "tier_pass_rate": tier_pass,  # legacy
        "per_episode": [m.as_dict() for m in metrics],
    }
