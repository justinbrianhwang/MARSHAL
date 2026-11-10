"""GREEN STOP protocol selection (configs/green_stop_protocols.yaml).

A green_stop run must name its protocol explicitly; there is no default. The
legacy protocol leaves the config untouched (released v2 behaviour). The
approach candidate rewrites only the spawn, the officer's onset mechanism
(position trigger instead of a fixed time) and the episode timing, and
refuses to run when any other setting would silently override them.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional

_CONFIGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs")
PROTOCOLS_PATH = os.path.join(_CONFIGS, "green_stop_protocols.yaml")
STATIONS_PATH = os.path.join(_CONFIGS, "stations.json")
ENV_KEY = "MARSHAL_GREEN_STOP_PROTOCOL"
# Diagnostic knobs that would move the spawn, the officer or the clock behind
# the candidate's back. Any of them set -> refuse (no silent precedence).
CONFLICTING_ENV = ("MARSHAL_SPAWN_BACK_M", "MARSHAL_SPAWN_BACK_FIXED_TIMING",
                   "MARSHAL_TIMEOUT_S", "MARSHAL_OFFICER_DISTANCE_M")


class GreenStopProtocolError(RuntimeError):
    pass


def load_protocols(path: str = PROTOCOLS_PATH) -> Dict[str, Any]:
    import yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def select_protocol(config: dict, env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    from_cfg = config.get("green_stop_protocol")
    from_env = env.get(ENV_KEY)
    if from_cfg and from_env and from_cfg != from_env:
        raise GreenStopProtocolError(
            f"GREEN STOP protocol conflict: config says {from_cfg!r}, {ENV_KEY} says {from_env!r}")
    name = from_cfg or from_env
    known = sorted(load_protocols())
    if not name:
        raise GreenStopProtocolError(
            f"GREEN STOP needs an explicit protocol: set {ENV_KEY} (or config green_stop_protocol) "
            f"to one of {known}. There is no default.")
    if name not in known:
        raise GreenStopProtocolError(f"unknown GREEN STOP protocol {name!r}; choose from {known}")
    return name


def _station() -> dict:
    with open(STATIONS_PATH, encoding="utf-8") as fh:
        st = json.load(fh)["stations"]["green_stop"]
    return {k: float(st[k]) for k in ("x", "y", "z", "yaw")}


def apply_protocol(config: dict, env: Optional[dict] = None) -> Dict[str, Any]:
    """Select and apply the GREEN STOP protocol in place; return the applied record
    (values plus where each came from). Raises GreenStopProtocolError on any conflict."""
    env = os.environ if env is None else env
    name = select_protocol(config, env)
    proto = load_protocols()[name]
    source = "config green_stop_protocol" if config.get("green_stop_protocol") else ENV_KEY
    config["green_stop_protocol"] = name
    applied: Dict[str, Any] = {"protocol": name, "selected_by": source, "mode": proto["mode"],
                               "result_label": proto["result_label"], "definitions": PROTOCOLS_PATH}
    if proto["mode"] == "legacy":
        return applied

    bad_env = [k for k in CONFLICTING_ENV if env.get(k)]
    if bad_env:
        raise GreenStopProtocolError(f"{name}: environment overrides conflict with the protocol: {bad_env}")
    cond = (env.get("MARSHAL_OFFICER_CONDITION") or "").strip().lower()
    if cond not in ("", "absent"):  # the candidate is a STOP by an authorized officer, or its officer-absent pair
        raise GreenStopProtocolError(f"{name}: MARSHAL_OFFICER_CONDITION={cond!r} changes the instruction or the authority; "
                                     f"only the officer-absent control is defined for this protocol")
    sp, trig, timing, off = proto["spawn"], proto["signal_trigger"], proto["timing"], proto["officer"]

    station = _station()
    ego = config.setdefault("ego", {})
    given = ego.get("spawn_transform")
    if given:  # staging passes the station (z lifted); anything else is a conflicting spawn
        dxy = math.hypot(float(given["x"]) - station["x"], float(given["y"]) - station["y"])
        dyaw = abs((float(given.get("yaw", 0.0)) - station["yaw"] + 180.0) % 360.0 - 180.0)
        if dxy > 0.01 or dyaw > 0.01:
            raise GreenStopProtocolError(
                f"{name}: config spawn {given} is not the green_stop station {station}")
    shift = float(sp["centre_to_stopline_m"]) - float(sp["station_centre_to_stopline_m"])
    yaw = math.radians(station["yaw"])
    ego["spawn_transform"] = {"x": station["x"] - shift * math.cos(yaw),
                              "y": station["y"] - shift * math.sin(yaw),
                              "z": max(station["z"], 3.0), "yaw": station["yaw"]}

    eb = config.setdefault("expected_behavior", {})
    budget = float(eb.get("max_reaction_time_sec", 3.0))
    if abs(budget - float(timing["reaction_budget_s"])) > 1e-9:
        raise GreenStopProtocolError(f"{name}: reaction budget {budget} != protocol {timing['reaction_budget_s']}")

    officer = config.get("officer")
    if officer:
        if officer.get("duration") is not None and abs(float(officer["duration"]) - float(trig["duration_s"])) > 1e-9:
            raise GreenStopProtocolError(f"{name}: officer duration {officer['duration']} != {trig['duration_s']}")
        gesture = str(officer.get("gesture") or "").upper()
        if gesture and gesture != str(trig["gesture"]).upper():
            raise GreenStopProtocolError(f"{name}: officer gesture {officer.get('gesture')!r} != protocol {trig['gesture']!r}")
        removed_onset = officer.pop("onset_time", None)
        officer["distance"] = float(off["station_along_lane_m"]) + shift
        officer["lateral_offset"] = float(off["lateral_offset_m"])
        officer["duration"] = float(trig["duration_s"])
        officer["onset_trigger"] = {"quantity": trig["quantity"], "threshold_m": float(trig["threshold_m"])}
        applied["officer"] = {"distance_from_spawn_m": officer["distance"], "lateral_offset_m": officer["lateral_offset"],
                              "gesture": officer.get("gesture"), "duration_s": officer["duration"],
                              "removed_fixed_onset_time_s": removed_onset}
    else:
        applied["officer"] = None  # absent condition: the reference crossing is still recorded

    config["timeout_sec"] = float(timing["approach_time_limit_s"]) + float(timing["post_signal_observation_s"])
    target = float(ego.get("target_speed", 25.0))
    config["approach_protocol"] = {
        "name": name, "result_label": proto["result_label"],
        "spawn_centre_to_stopline_m": float(sp["centre_to_stopline_m"]),
        "spawn_tolerance_m": float(sp["tolerance_m"]),
        "station_shift_m": shift,
        "trigger_quantity": trig["quantity"], "trigger_threshold_m": float(trig["threshold_m"]),
        "signal_gesture": trig["gesture"], "signal_duration_s": float(trig["duration_s"]),
        "approach_time_limit_s": float(timing["approach_time_limit_s"]),
        "post_signal_observation_s": float(timing["post_signal_observation_s"]),
        "reaction_budget_s": budget,
        "target_speed_kmh": target,
        "normal_approach": dict(proto["normal_approach"]),
        "verify": dict(proto["verify"]),
    }
    applied.update({"spawn_transform": dict(ego["spawn_transform"]), "approach_protocol": config["approach_protocol"],
                    "timeout_sec": config["timeout_sec"],
                    "sources": {"spawn": "stations.json green_stop shifted by protocol spawn.centre_to_stopline_m",
                                "officer": "protocol officer block (world position of the released episodes)",
                                "trigger": "protocol signal_trigger", "timing": "protocol timing",
                                "target_speed": "demo_green_stop.yaml ego.target_speed"}})
    return applied


def classify_normal_approach(rows: List[dict], t_trigger: float, target_kmh: float, crit: dict) -> Dict[str, Any]:
    """Pre-registered candidate criterion on the window before the trigger tick.

    normal = every speed sample in [t_trigger - window, t_trigger] is >= fraction*target
    and the window's speed range is <= max_speed_range_kmh. Reported, never acted on.
    """
    win_s = float(crit["window_s"])
    w = [r for r in rows if t_trigger - win_s - 1e-9 <= float(r["sim_time"]) <= t_trigger + 1e-9]
    speeds = [float(r["ego_speed_kmh"]) for r in w if r.get("ego_speed_kmh") is not None]
    floor = float(crit["min_speed_fraction_of_target"]) * float(target_kmh)
    covered = bool(w) and float(w[0]["sim_time"]) <= t_trigger - win_s + 0.051
    if not speeds or not covered:
        return {"normal": False, "reason": "window not covered by telemetry", "window_rows": len(w)}
    rng = max(speeds) - min(speeds)
    fast = min(speeds) >= floor
    steady = rng <= float(crit["max_speed_range_kmh"])
    reason = "normal approach" if fast and steady else ", ".join(
        x for x, bad in (("below target fraction", not fast), ("speed not steady", not steady)) if bad)
    return {"normal": fast and steady, "reason": reason, "speed_min_kmh": round(min(speeds), 3),
            "speed_max_kmh": round(max(speeds), 3), "speed_range_kmh": round(rng, 3),
            "speed_floor_kmh": round(floor, 3), "steady": steady, "at_or_above_floor": fast,
            "window_s": win_s, "window_rows": len(w)}


class PositionTrigger:
    """Latched one-shot trigger on the ego front-to-stopline gap.

    Fires on the first update whose gap is <= threshold (so a tick that jumps
    over the reference still fires) and never again. It has no clock and no
    speed input: time alone can never fire it, and nothing can move it per model.
    """

    def __init__(self, threshold_m: float) -> None:
        self.threshold_m = float(threshold_m)
        self.fired_at: Optional[float] = None
        self.gap_at_fire: Optional[float] = None

    def update(self, gap_m: Optional[float], sim_time: float) -> bool:
        if self.fired_at is not None or gap_m is None or not math.isfinite(gap_m):
            return False
        if gap_m <= self.threshold_m:
            self.fired_at, self.gap_at_fire = float(sim_time), float(gap_m)
            return True
        return False
