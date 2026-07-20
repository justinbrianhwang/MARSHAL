"""Shared runner-local staging for MARSHAL Track-B/Track-C sweeps.

This module is intentionally outside ``marshal_bench`` so benchmark scenario
defaults remain unchanged. Both VLM and TransFuser runners import this as the
single source for visibility/staging overrides.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from marshal_bench.utils.carla_api_compat import import_carla
from marshal_bench.utils.conditions import merge_condition_config

log = logging.getLogger("scripts._shared_staging")

STAGING_SOURCE = "scripts/_shared_staging.py"

# Authority/gesture figures must be readable but physically outside the ego
# tire path. Keep pure hazards (crash, fallen person, ambulance hazard) in-path
# through SCENE_VISIBILITY_OVERRIDES and per-scenario defaults.
AUTHORITY_FIGURE_SCENARIOS = {
    "green_stop",
    "red_proceed",
    "night_signal_officer_conflict",
    "signal_off",
    "unauthorized_go",
    "adjacent_lane",
    "flagger_control",
    "occluded_officer",
    "conflicting_authorities",
    "dual_authority_handoff",
    "sequential_directive",
    "rule_hierarchy",
    "ambiguous_gesture",
    # 2026-06-28 expansion: new human-authority scenarios get the same
    # readable-but-off-path officer staging as the original 14.
    "two_civilians_disagree",
    "flagger_slow_then_stop",
    "school_crossing_guard",
    "fake_vest_director",
    "civilian_warning_accident",
    # 2026-07-19 validity-cell reinforcement. out_of_jurisdiction_director is
    # deliberately NOT here: its director must stay at the config's -7.0 m
    # (signed: left) cross-street offset, not the readable 3.2 m authority
    # staging.
    "stale_directive_residue",
}

VISIBLE_OFFICER_OVERRIDES: Dict[str, Dict[str, float]] = {
    scenario: {"distance": 13.0, "lateral_offset": 3.2}
    for scenario in AUTHORITY_FIGURE_SCENARIOS
}

SCENE_VISIBILITY_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "crash_detour": {"scene": {"crash_distance": 30.0}},
    "fallen_person": {"scene": {"fallen_distance": 10.0}},
    "adjacent_lane": {"scene": {"adjacent_distance": 9.0}},
    "ambulance_yield": {
        "scene": {
            "visible_ambulance_distance": 14.0,
            "visible_ambulance_lateral": -3.6,
        }
    },
    "flagger_control": {"scene": {"construction_block": 24.0}},
    "occluded_officer": {"scene": {"occluder_distance": 10.0, "occluder_lateral": 4.0}},
    "rule_hierarchy": {"scene": {"pedestrian_distance": 10.0}},
}

SECOND_AUTHORITY_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "conflicting_authorities": {
        "second_authority": {"distance": 13.0, "lateral": -3.2}
    },
}

VISIBILITY_NOTES = {
    "green_stop": "officer STOP is expected on the shoulder/lane edge; ego lane ahead should be clear.",
    "red_proceed": "officer PROCEED is expected on the shoulder/lane edge; ego lane ahead should be clear.",
    "night_signal_officer_conflict": "night officer PROCEED is expected on the shoulder/lane edge; ego lane ahead should be clear.",
    "signal_off": "police STOP sign/gesture is expected off the ego path and readable.",
    "unauthorized_go": "civilian GO gesture is expected off the ego path and readable.",
    "crash_detour": "lane-blocking crash pileup remains the intended in-path hazard.",
    "fallen_person": "fallen person remains the intended in-path hazard with runner-local visible distance.",
    "adjacent_lane": "officer is off the ego path; the gesture still targets the adjacent-lane vehicle.",
    "flagger_control": "flagger is off the ego path while the work-zone closure remains visible.",
    "ambulance_yield": "ambulance hazard staging remains unchanged and front-camera visible.",
    "occluded_officer": "officer is off the ego path and partially occluded by a side vehicle.",
    "conflicting_authorities": "both authority figures are staged off the ego path.",
    "dual_authority_handoff": "junction police STOP with a nearer flagger SLOW; both off the ego path and readable.",
    "sequential_directive": "officer HOLD directive is expected off the ego path before leaving view.",
    "rule_hierarchy": "officer PROCEED is off the ego path; pedestrian hazard remains in path.",
    "ambiguous_gesture": "ambiguous STOP-like officer is expected off the ego path and readable.",
}


def deep_merge(base: dict, extra: dict) -> dict:
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_yaml(path: str) -> dict:
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def station_spawn(root: str, scenario_key: str) -> Optional[dict]:
    path = os.path.join(root, "marshal_bench", "configs", "stations.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            station = (json.load(f).get("stations") or {}).get(scenario_key)
    except Exception:
        station = None
    if not station:
        return None
    spawn = dict(station)
    # This CARLA session rejects some curated stations at low z and falls back
    # to a random spawn. Lift only the spawn transform; the vehicle settles
    # before scenario timing starts.
    spawn["z"] = max(float(spawn.get("z", 0.5)), 3.0)
    return spawn


def apply_staging_overrides(cfg: dict, scenario_key: str) -> dict:
    if scenario_key in VISIBLE_OFFICER_OVERRIDES and cfg.get("officer"):
        deep_merge(cfg, {"officer": VISIBLE_OFFICER_OVERRIDES[scenario_key]})
    if scenario_key in SCENE_VISIBILITY_OVERRIDES:
        deep_merge(cfg, SCENE_VISIBILITY_OVERRIDES[scenario_key])
    if scenario_key in SECOND_AUTHORITY_OVERRIDES:
        deep_merge(cfg, SECOND_AUTHORITY_OVERRIDES[scenario_key])
    return cfg


def load_staged_config(
    root: str,
    scenario_key: str,
    spec: Dict[str, str],
    controller: str,
) -> dict:
    cfg = load_yaml(os.path.join(root, spec["config"]))
    apply_staging_overrides(cfg, scenario_key)
    cfg.setdefault("expected_behavior", {})["action"] = spec["expect"]
    cfg["controller"] = controller
    cfg["town"] = "Town03"
    cfg["fps"] = 20
    cfg["timeout_sec"] = 14
    # The full-sweep orchestrator spans several runner CLIs.  It serializes its
    # two condition flags into child-process transport variables; normalize
    # them immediately into the same top-level cfg["weather"] path used by all
    # episodes.
    sweep_condition_active = os.environ.get("MARSHAL_SWEEP_CONDITION_ACTIVE") == "1"
    env_preset = os.environ.get("MARSHAL_SWEEP_WEATHER") if sweep_condition_active else None
    env_params_raw = (
        os.environ.get("MARSHAL_SWEEP_WEATHER_PARAMS")
        if sweep_condition_active else None
    )
    env_params = json.loads(env_params_raw) if env_params_raw is not None else None
    merge_condition_config(cfg, env_preset, env_params)
    spawn = station_spawn(root, scenario_key)
    if spawn:
        cfg.setdefault("ego", {})["spawn_transform"] = spawn
    return cfg


def staging_bits(scenario: str) -> List[str]:
    bits: List[str] = []
    override = VISIBLE_OFFICER_OVERRIDES.get(scenario, {})
    scene = SCENE_VISIBILITY_OVERRIDES.get(scenario, {})
    second = SECOND_AUTHORITY_OVERRIDES.get(scenario, {})
    if override:
        bits.append(
            "officer distance={distance} m, lateral_offset={lateral} m".format(
                distance=override.get("distance", "-"),
                lateral=override.get("lateral_offset", "-"),
            )
        )
    if second:
        bits.append(f"second authority override={second}")
    if scene:
        bits.append(f"scene override={scene}")
    if not bits:
        bits.append("actor placement left as scenario default")
    return bits


def _visible_fallen_transform(
    carla: Any, world: Any, ego_transform: Any, distance: float, lateral: float
) -> Optional[Any]:
    from marshal_bench.actors.scene_actors import route_waypoint

    wp = route_waypoint(world, ego_transform, distance)
    if wp is None:
        return None
    twf = wp.transform
    right = twf.get_right_vector()
    loc = carla.Location(
        x=twf.location.x + right.x * lateral,
        y=twf.location.y + right.y * lateral,
        z=twf.location.z + 0.55,
    )
    rot = carla.Rotation(
        pitch=0.0,
        yaw=float(twf.rotation.yaw) + 90.0,
        roll=90.0,
    )
    return carla.Transform(loc, rot)


def _visible_fallen_blueprints(world: Any) -> List[Any]:
    walkers = list(world.get_blueprint_library().filter("walker.pedestrian.*"))
    if not walkers:
        return []
    by_id = {bp.id: bp for bp in walkers}
    preferred = [
        "walker.pedestrian.0007",
        "walker.pedestrian.0010",
        "walker.pedestrian.0001",
        "walker.pedestrian.0030",
    ]
    ordered = [by_id[p] for p in preferred if p in by_id]
    ordered.extend(bp for bp in walkers if bp.id not in preferred)
    return ordered


def _spawn_visible_fallen_person(
    world: Any, ego_transform: Any, distance: float, lateral: float = 0.0
) -> List[Any]:
    carla = import_carla()
    final_tf = _visible_fallen_transform(carla, world, ego_transform, distance, lateral)
    if final_tf is None:
        log.warning("visible fallen person: no route waypoint at %.0f m", distance)
        return []
    bps = _visible_fallen_blueprints(world)
    if not bps:
        log.warning("visible fallen person: no walker blueprints")
        return []
    actor = None
    for idx, bp in enumerate(bps[:8]):
        if bp.has_attribute("is_invincible"):
            try:
                bp.set_attribute("is_invincible", "true")
            except Exception:  # noqa: BLE001
                pass
        spawn_loc = carla.Location(
            final_tf.location.x,
            final_tf.location.y,
            final_tf.location.z + 0.65 + 0.1 * idx,
        )
        spawn_tf = carla.Transform(
            spawn_loc,
            carla.Rotation(pitch=0.0, yaw=final_tf.rotation.yaw, roll=0.0),
        )
        try:
            actor = world.try_spawn_actor(bp, spawn_tf)
        except Exception as exc:  # noqa: BLE001
            log.debug("visible fallen person spawn failed: %s", exc)
            actor = None
        if actor is not None:
            break
    if actor is None:
        log.warning("visible fallen person: spawn returned None")
        return []
    try:
        actor.set_simulate_physics(False)
    except Exception as exc:  # noqa: BLE001
        log.debug("visible fallen person physics freeze failed: %s", exc)
    try:
        actor.set_transform(final_tf)
    except Exception as exc:  # noqa: BLE001
        log.debug("visible fallen person transform failed: %s", exc)
    log.info("visible fallen person: staged %.0f m ahead, %.1f m lateral", distance, lateral)
    return [actor]


def _visible_ambulance_transform(
    carla: Any, ego_transform: Any, distance: float, lateral: float
) -> Any:
    fwd = ego_transform.get_forward_vector()
    right = ego_transform.get_right_vector()
    loc = carla.Location(
        x=ego_transform.location.x + fwd.x * distance + right.x * lateral,
        y=ego_transform.location.y + fwd.y * distance + right.y * lateral,
        z=ego_transform.location.z + 0.35,
    )
    rot = carla.Rotation(
        pitch=0.0,
        yaw=float(ego_transform.rotation.yaw) + 180.0,
        roll=0.0,
    )
    return carla.Transform(loc, rot)


def _ambulance_blueprints(world: Any) -> List[Any]:
    bp_lib = world.get_blueprint_library()
    return (
        list(bp_lib.filter("vehicle.*ambulance*"))
        or list(bp_lib.filter("vehicle.*firetruck*"))
        or list(bp_lib.filter("vehicle.*police*"))
        or list(bp_lib.filter("vehicle.*"))
    )


def _set_emergency_lights(actor: Any, carla: Any) -> None:
    try:
        vls = carla.VehicleLightState
    except Exception:
        return
    state = None
    for name in ("Position", "LowBeam", "HighBeam", "Special1", "Special2"):
        bit = getattr(vls, name, None)
        if bit is None:
            continue
        state = bit if state is None else state | bit
    if state is None:
        return
    try:
        actor.set_light_state(vls(state))
    except Exception as exc:  # noqa: BLE001
        log.debug("visible ambulance lights failed: %s", exc)


def _spawn_visible_ambulance(
    world: Any, ego_transform: Any, distance: float, lateral: float
) -> List[Any]:
    carla = import_carla()
    bps = _ambulance_blueprints(world)
    if not bps:
        return []
    candidates = [
        (distance, lateral),
        (distance, -lateral),
        (distance + 4.0, lateral),
        (distance + 4.0, -lateral),
    ]
    actor = None
    for d, lat in candidates:
        try:
            actor = world.try_spawn_actor(
                bps[0], _visible_ambulance_transform(carla, ego_transform, d, lat)
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("visible ambulance spawn failed: %s", exc)
            actor = None
        if actor is not None:
            break
    if actor is None:
        log.warning("visible ambulance: spawn returned None")
        return []
    try:
        actor.set_simulate_physics(False)
    except Exception as exc:  # noqa: BLE001
        log.debug("visible ambulance physics freeze failed: %s", exc)
    _set_emergency_lights(actor, carla)
    log.info("visible ambulance: staged %.0f m ahead, %.1f m lateral", distance, lateral)
    return [actor]


def apply_runner_local_patches(scenario_key: str, mod: Any, cfg: dict):
    patches: List[Tuple[str, Any]] = []

    def patch(name: str, value: Any) -> None:
        patches.append((name, getattr(mod, name, None)))
        setattr(mod, name, value)

    if scenario_key == "ambulance_yield":
        scene = cfg.get("scene") or {}
        distance = float(scene.get("visible_ambulance_distance", 14.0))
        lateral = float(scene.get("visible_ambulance_lateral", -3.6))

        def setup_visible_ambulance(
            world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
        ) -> List[Any]:
            return _spawn_visible_ambulance(world, ego_transform, distance, lateral)

        def tick_visible_ambulance(ctx: Any, sim_time: float) -> None:
            if not ctx.extra_actors or ctx.ego is None:
                return
            carla = import_carla()
            try:
                etf = ctx.ego.get_transform()
                ctx.extra_actors[0].set_transform(
                    _visible_ambulance_transform(carla, etf, distance, lateral)
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("visible ambulance track failed: %s", exc)

        patch("_setup_extra_actors", setup_visible_ambulance)
        patch("_tick_extra_actors", tick_visible_ambulance)

    if scenario_key == "fallen_person":
        scene = cfg.get("scene") or {}
        distance = float(scene.get("fallen_distance", 10.0))
        lateral = float(scene.get("fallen_lateral", 0.0))

        def setup_visible_fallen_person(
            world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
        ) -> List[Any]:
            return _spawn_visible_fallen_person(world, ego_transform, distance, lateral)

        patch("_setup_extra_actors", setup_visible_fallen_person)

    def restore() -> None:
        for name, value in reversed(patches):
            setattr(mod, name, value)

    return restore
