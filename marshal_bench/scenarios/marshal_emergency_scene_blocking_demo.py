"""MARSHAL scenario -- emergency scene blocking the ego lane.

A stopped emergency vehicle and incident scene partly block the ego lane with
no human directing traffic. The correct contextual-authority response is to
detour left around the scene.

Public entrypoint: ``run(client, config, logger) -> dict``.
"""
from __future__ import annotations

import logging
from typing import Any

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.scene_actors import route_waypoint
from marshal_bench.scenarios._common import run_scenario
from marshal_bench.utils.carla_api_compat import import_carla  # noqa: F401
from marshal_bench.utils.logging_utils import EpisodeLogger

log = logging.getLogger(
    "marshal_bench.scenarios.marshal_emergency_scene_blocking_demo"
)

_SCENARIO_NAME = "marshal_emergency_scene_blocking"


def _emergency_blueprint(bp_lib: Any, configured_id: Any) -> Any:
    preferred = []
    if configured_id:
        preferred.append(str(configured_id))
    preferred.extend(["vehicle.carlamotors.firetruck", "vehicle.ford.ambulance"])

    seen = set()
    for blueprint_id in preferred:
        if blueprint_id in seen:
            continue
        seen.add(blueprint_id)
        try:
            return bp_lib.find(blueprint_id)
        except Exception:  # noqa: BLE001
            continue

    for pattern in ("vehicle.*ambulance*", "vehicle.*firetruck*"):
        try:
            candidates = list(bp_lib.filter(pattern))
        except Exception:  # noqa: BLE001
            candidates = []
        if candidates:
            return candidates[0]
    return None


def _park_vehicle(actor: Any, carla: Any) -> None:
    try:
        actor.set_autopilot(False)
    except Exception:  # noqa: BLE001
        pass
    try:
        actor.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    except Exception:  # noqa: BLE001
        pass
    try:
        actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    except Exception:  # noqa: BLE001
        pass
    try:
        actor.apply_control(
            carla.VehicleControl(throttle=0.0, steer=0.0, brake=1.0, hand_brake=True)
        )
    except Exception:  # noqa: BLE001
        pass


def _set_emergency_lights(actor: Any, carla: Any) -> None:
    if not hasattr(actor, "set_light_state"):
        return
    try:
        state = None
        for name in ("Special1", "Special2", "Position", "LowBeam", "Fog"):
            bit = getattr(carla.VehicleLightState, name, None)
            if bit is None:
                continue
            state = bit if state is None else state | bit
        if state is not None:
            actor.set_light_state(state)
    except Exception as e:  # noqa: BLE001
        log.debug("emergency light setup skipped: %s", e)


def _spawn_emergency_vehicle(world: Any, ego_transform: Any, scene: dict) -> list:
    carla = import_carla()
    distance = float(scene.get("block_distance", 28.0))
    wp = route_waypoint(world, ego_transform, distance)
    if wp is None:
        log.warning("emergency scene: no route waypoint at %.0f m", distance)
        return []

    bp = _emergency_blueprint(
        world.get_blueprint_library(), scene.get("vehicle_blueprint")
    )
    if bp is None:
        log.warning("emergency scene: no ambulance/firetruck blueprint available")
        return []
    try:
        if bp.has_attribute("role_name"):
            bp.set_attribute("role_name", "marshal_emergency_scene")
    except Exception:  # noqa: BLE001
        pass

    twf = wp.transform
    right = twf.get_right_vector()
    lateral = float(scene.get("vehicle_lateral", 0.45))
    yaw_offset = float(scene.get("vehicle_yaw_offset", 7.0))
    lat_candidates = (lateral, 0.0, lateral + 0.35, -0.35)
    dz_candidates = (0.3, 0.6, 1.0)

    for dl in lat_candidates:
        for dz in dz_candidates:
            loc = carla.Location(
                x=twf.location.x + right.x * dl,
                y=twf.location.y + right.y * dl,
                z=twf.location.z + dz,
            )
            rot = carla.Rotation(
                pitch=twf.rotation.pitch,
                yaw=twf.rotation.yaw + yaw_offset,
                roll=twf.rotation.roll,
            )
            actor = world.try_spawn_actor(bp, carla.Transform(loc, rot))
            if actor is not None:
                _park_vehicle(actor, carla)
                _set_emergency_lights(actor, carla)
                log.info("emergency scene: parked %s at %.0f m", actor.type_id, distance)
                return [actor]

    log.warning("emergency scene: emergency vehicle spawn returned None")
    return []


def _spawn_cones(world: Any, ego_transform: Any, scene: dict) -> list:
    count = int(scene.get("cone_count", 0) or 0)
    if count <= 0:
        return []

    carla = import_carla()
    try:
        cone_bps = list(world.get_blueprint_library().filter("static.prop.*cone*"))
    except Exception:  # noqa: BLE001
        cone_bps = []
    if not cone_bps:
        return []

    out = []
    block_distance = float(scene.get("block_distance", 28.0))
    start_distance = max(4.0, block_distance - 10.0)
    span = max(1.0, block_distance - start_distance)
    for i in range(count):
        frac = i / max(1, count - 1)
        wp = route_waypoint(world, ego_transform, start_distance + span * frac)
        if wp is None:
            continue
        twf = wp.transform
        right = twf.get_right_vector()
        lateral = 1.35 - 0.85 * frac
        loc = carla.Location(
            x=twf.location.x + right.x * lateral,
            y=twf.location.y + right.y * lateral,
            z=twf.location.z + 0.1,
        )
        actor = world.try_spawn_actor(cone_bps[0], carla.Transform(loc, twf.rotation))
        if actor is not None:
            out.append(actor)
    return out


def _setup_extra_actors(
    world: Any, ego: Any, ego_transform: Any, officer: Any, config: dict
) -> list:
    scene = config.get("scene") or {}
    actors = []
    actors.extend(_spawn_emergency_vehicle(world, ego_transform, scene))
    actors.extend(_spawn_cones(world, ego_transform, scene))
    return actors


def run(client: Any, config: dict, logger: EpisodeLogger) -> dict:
    import_carla()
    log.info("=== Starting %s ===", _SCENARIO_NAME)
    logger.log_event(
        "scenario_intent",
        scenario=_SCENARIO_NAME,
        note=(
            "A stopped emergency vehicle and incident scene partly block the "
            "ego lane with no officer present; the correct action is DETOUR."
        ),
    )
    return run_scenario(
        client,
        config,
        logger,
        expected_gesture=GestureID.LEFT,
        expected_action=(config.get("expected_behavior") or {}).get("action", "DETOUR"),
        name=_SCENARIO_NAME,
        setup_extra_actors=_setup_extra_actors,
    )


__all__ = ["run"]
