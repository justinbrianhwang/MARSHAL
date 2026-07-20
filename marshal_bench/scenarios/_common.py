"""Shared scaffolding for the three MARSHAL demo scenarios.

The :mod:`marshal_green_stop_demo`, :mod:`marshal_red_proceed_demo` and
:mod:`marshal_signal_officer_control_demo` modules each implement a thin
``run(client, config, logger)`` entrypoint. They differ in *what* the traffic
light and officer do, but they share an identical lifecycle:

    1.  Load town / apply weather.
    2.  Spawn ego near a signalised intersection.
    3.  Resolve the relevant traffic light and pin its state.
    4.  Spawn a :class:`TrafficOfficer` ~12 m in front of the ego, facing it.
    5.  Attach a collision sensor to ego, wire it into the criteria.
    6.  Enable autopilot and step the world via :class:`SyncModeContext`,
        ticking the officer and the criteria every frame.
    7.  Always destroy spawned actors in a ``finally`` block.

All of that bookkeeping lives here so the three demo modules remain short and
diff-readable.

Important caveat exposed by the benchmark
-----------------------------------------
CARLA's built-in TrafficManager autopilot reacts to traffic lights but is
*blind* to walkers / gestures. In the GREEN+STOP and RED+PROCEED scenarios the
ego is therefore **expected** to violate the officer's command — that gap is
exactly the research insight MARSHAL is designed to surface, and the criteria
modules record the violation rather than treating it as a test failure.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from marshal_bench.actors.gesture_engine import GestureID
from marshal_bench.actors.traffic_officer import TrafficOfficer
from marshal_bench.criteria.authority_compliance import AuthorityComplianceCriterion
from marshal_bench.criteria.reaction_latency import ReactionLatencyCriterion
from marshal_bench.criteria.strict_episode_scoring import (
    score_episode_from_telemetry,
    write_strict_artifacts,
)
from marshal_bench.utils.carla_api_compat import SyncModeContext, import_carla
from marshal_bench.utils.conditions import (
    condition_from_config,
    describe,
    resolve,
)
try:  # logos/landmarks are dropped in the distributed repo — optional import
    from marshal_bench.utils.landmarks import ensure_town03_landmarks
except Exception:  # noqa: BLE001
    def ensure_town03_landmarks(world):  # type: ignore[misc]
        return None
from marshal_bench.utils.logging_utils import EpisodeLogger
from marshal_bench.utils.traffic_light_utils import (
    find_relevant_traffic_light,
    get_traffic_light_state,
    release_traffic_light,
    set_traffic_light_state,
)

log = logging.getLogger("marshal_bench.scenarios._common")

# Default fall-back values shared by the three demos.
DEFAULT_FPS = 20.0
DEFAULT_TIMEOUT_SEC = 25.0
OFFICER_DISTANCE_FROM_EGO = 30.0  # metres ahead of the ego (default; config-overridable)
OFFICER_LATERAL_OFFSET = 2.2  # metres to the ego's right — lane edge, ego clears it
YIELD_ROUTE_OFFSET_M = 1.6
WALKER_CAPSULE_RADIUS_M = 0.30
GESTURE_ARM_REACH_ALLOWANCE_M = 0.75
YIELD_CLEARANCE_MARGIN_M = 0.50
TL_SEARCH_RADIUS = 80.0  # metres
SPAWN_TL_SEARCH_DISTANCE = 40.0  # metres of forward projection used to score spawns


def _stop_completion_reached(
    sim_time: float,
    onset_time: float,
    speed_kmh: float,
    in_junction: bool,
    entered_junction_after_start: bool,
    stopline_distance_m: Optional[float] = None,
    reaction_deadline_s: float = 3.0,
) -> bool:
    """Only complete STOP after a genuine post-start conflict-zone entry.

    The junction must be the ASSIGNED conflict zone: an unrelated junction
    polygon near the spawn (curated Town03 green_stop sits 1.2 m before
    one) must not complete the episode, so entry only counts within
    15 m of the assigned stopline. Completion must also leave the strict
    scorer enough telemetry to verify a sustained stop, so the time floor
    covers the reaction deadline plus a margin.
    """
    if stopline_distance_m is not None:
        try:
            if math.isfinite(float(stopline_distance_m)) and float(
                stopline_distance_m
            ) > 15.0:
                return False
        except (TypeError, ValueError):
            pass
    return (
        sim_time > onset_time + max(2.0, reaction_deadline_s + 1.0)
        and speed_kmh < 0.5
        and in_junction
        and entered_junction_after_start
    )


def _detour_completion_reached(
    anchor_forward_m: Optional[float],
    ego_forward_m: Any,
    ego_lateral_m: Any,
    sim_time: float,
    onset_time: float,
) -> bool:
    """True once the DETOUR maneuver's validated envelope is fully driven.

    Envelope end = hazard anchor + pass margin (4) + merge taper (12) + 1 m
    buffer, with the merge physically complete (|lateral| < 1.0). Episodes
    must not run past it: on small grid towns the route can loop back
    through the staged scene (Town02 esb re-met its firetruck at +47 m).
    """
    if anchor_forward_m is None or sim_time <= onset_time:
        return False
    try:
        forward = float(ego_forward_m)
        lateral = float(ego_lateral_m)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(forward) and math.isfinite(lateral)):
        return False
    return forward >= float(anchor_forward_m) + 17.0 and abs(lateral) < 1.0


def _hold_ego_during_spawn_settle(ego: Any) -> None:
    """Prevent spawn roll with service brake, avoiding CARLA handbrake latch."""
    try:
        carla = import_carla()
        ego.apply_control(
            carla.VehicleControl(
                throttle=0.0,
                steer=0.0,
                brake=1.0,
                hand_brake=False,
            )
        )
    except Exception as exc:
        log.debug("Could not hold ego during spawn settle: %s", exc)


def _release_ego_after_spawn_settle(ego: Any) -> None:
    """Explicitly clear CARLA's latched handbrake before controller setup."""
    try:
        carla = import_carla()
        ego.apply_control(
            carla.VehicleControl(
                throttle=0.0,
                steer=0.0,
                brake=1.0,
                hand_brake=False,
            )
        )
    except Exception as exc:
        log.warning("Could not release ego spawn-settle handbrake: %s", exc)


# ---------------------------------------------------------------------------
# Result / context plumbing
# ---------------------------------------------------------------------------
@dataclass
class ScenarioContext:
    """Container for everything we allocate during a demo run.

    The :func:`teardown` helper iterates over every field that holds CARLA
    actors / sensors and destroys them in a defensive try/except loop.
    """

    world: Any = None
    ego: Any = None
    officer: Optional[TrafficOfficer] = None
    traffic_light: Any = None
    collision_sensor: Any = None
    camera: Any = None          # 3rd-person scene/director camera
    ego_camera: Any = None      # ego dashcam — the VLM benchmark input view
    latest_ego_frame: Any = None  # latest dashcam RGB ndarray, HWC uint8
    frames_ego_dir: Optional[str] = None
    traffic_manager: Any = None
    original_settings: Any = None
    original_weather: Any = None
    weather_applied: bool = False
    extra_actors: list = field(default_factory=list)  # per-scenario scene actors
    blocking_actors: list = field(default_factory=list)  # on-route obstruction
    spawned_actor_ids: list = field(default_factory=list)
    blocking_route_samples: list = field(default_factory=list)
    blocking_ego_route_index: int = 0
    blocking_forward_monotonic_m: Optional[float] = None
    blocking_clear_latched: bool = False
    blocking_ego_odometer_m: float = 0.0
    blocking_ego_last_location: Any = None
    blocking_actor_route_s: dict = field(default_factory=dict)
    blocking_actor_last_distance: dict = field(default_factory=dict)


def _actor_id(actor: Any) -> Optional[int]:
    try:
        actor_id = getattr(actor, "id", None)
        return int(actor_id) if actor_id is not None else None
    except (TypeError, ValueError):
        return None


def _scene_actor_group(ctx: ScenarioContext, actor_id: Optional[int]) -> Optional[str]:
    """Classify an actor ID within this episode's managed scene."""
    if actor_id is None:
        return None
    if any(_actor_id(actor) == actor_id for actor in ctx.blocking_actors):
        return "blocking"
    officer_actors = []
    if ctx.officer is not None:
        try:
            officer_actors.append(ctx.officer.get_actor())
        except Exception:
            pass
        officer_actors.extend(list(getattr(ctx.officer, "_aux_actors", ()) or ()))
    if any(_actor_id(actor) == actor_id for actor in officer_actors):
        return "officer_or_civilian"
    if any(_actor_id(actor) == actor_id for actor in ctx.extra_actors):
        return "extra"
    return None


def _collision_identity_record(
    ctx: ScenarioContext, event: Any, sim_time: float
) -> dict[str, Any]:
    other = getattr(event, "other_actor", None)
    other_id = _actor_id(other)
    group = _scene_actor_group(ctx, other_id)
    return {
        "sim_time": round(float(sim_time), 4),
        "other_type_id": str(getattr(other, "type_id", "") or ""),
        "other_actor_id": other_id,
        "other_is_scene_actor": group is not None,
        "other_scene_actor_group": group,
    }


def _append_collision_identity(
    records: list[dict[str, Any]],
    ctx: ScenarioContext,
    event: Any,
    sim_time: float,
) -> None:
    if len(records) < 20:
        records.append(_collision_identity_record(ctx, event, sim_time))


# ---------------------------------------------------------------------------
# World / weather helpers
# ---------------------------------------------------------------------------
def ensure_town(client: Any, requested_town: Optional[str]) -> Any:
    """Load ``requested_town`` if it differs from the currently loaded map.

    Returns the resulting :class:`carla.World`. If ``requested_town`` is None
    we just return the current world without reloading.
    """
    world = client.get_world()
    if not requested_town:
        return world
    try:
        current_map = world.get_map().name
    except Exception:
        current_map = ""
    # CARLA map names look like ``Carla/Maps/Town03``; only compare the suffix.
    short = current_map.rsplit("/", 1)[-1] if current_map else ""
    if short.lower() == requested_town.lower():
        return world
    log.info("Loading town %s (was %r)", requested_town, short or "unknown")
    try:
        return client.load_world(requested_town)
    except Exception as e:
        log.warning("client.load_world(%s) failed: %s — using current world", requested_town, e)
        return world


def apply_weather(world: Any, weather_name: Optional[str]) -> None:
    """Apply a named ``carla.WeatherParameters`` preset, defaulting to ClearNoon."""
    if not weather_name:
        return
    carla = import_carla()
    preset = getattr(carla.WeatherParameters, weather_name, None)
    if preset is None:
        log.warning("Unknown weather preset %r — leaving weather unchanged", weather_name)
        return
    try:
        world.set_weather(preset)
    except Exception as e:
        log.warning("world.set_weather(%s) failed: %s", weather_name, e)


def should_apply_condition(cfg: dict) -> bool:
    """Whether the new first-class ``cfg['weather']`` branch is requested."""
    if "weather" not in cfg:
        return False
    condition = condition_from_config(cfg.get("weather"))
    return condition.weather_preset is not None or condition.weather_params is not None


# ---------------------------------------------------------------------------
# Ego spawning
# ---------------------------------------------------------------------------
def _auto_pick_ego_spawn_near_signal(
    world: Any,
    seed: Optional[int] = None,
    forward_distance: float = SPAWN_TL_SEARCH_DISTANCE,
) -> Any:
    """Scan map spawn points and return one that faces a traffic light.

    Strategy:
      * Enumerate ``world.get_map().get_spawn_points()``.
      * For each, project ``forward_distance`` metres along the spawn forward
        vector. If a ``traffic.traffic_light`` actor lies within the projection
        radius and roughly in front of the spawn, the spawn is a candidate.
      * Pick the candidate whose projected target is closest to a traffic
        light (and fall back to a random spawn if nothing qualifies).
    """
    carla = import_carla()
    spawn_points = list(world.get_map().get_spawn_points())
    if not spawn_points:
        raise RuntimeError("Map has no spawn points; cannot place ego.")

    rng = random.Random(seed)

    try:
        lights = list(world.get_actors().filter("traffic.traffic_light*"))
    except Exception:
        lights = []

    if not lights:
        log.warning("No traffic lights in world; picking a random spawn point.")
        return rng.choice(spawn_points)

    carla_map = world.get_map()
    scored: list[tuple[float, Any]] = []
    for sp in spawn_points:
        loc = sp.location
        # Skip spawns where the TrafficManager autopilot cannot drive away:
        # points inside a junction, or on a lane that dead-ends within 50 m.
        try:
            wp = carla_map.get_waypoint(loc, project_to_road=True)
            if wp is None or wp.is_junction:
                continue
            if not wp.next(50.0):
                continue
        except Exception:
            continue
        fwd = sp.get_forward_vector()
        projected = carla.Location(
            x=loc.x + fwd.x * forward_distance,
            y=loc.y + fwd.y * forward_distance,
            z=loc.z,
        )
        # Closest light to the projection target.
        best_dist = float("inf")
        for tl in lights:
            try:
                tl_loc = tl.get_transform().location
            except Exception:
                continue
            d = math.hypot(tl_loc.x - projected.x, tl_loc.y - projected.y)
            # Require the light to be roughly in front of the spawn.
            dot = (tl_loc.x - loc.x) * fwd.x + (tl_loc.y - loc.y) * fwd.y
            if dot <= 0:
                continue
            if d < best_dist:
                best_dist = d
        if best_dist < float("inf"):
            scored.append((best_dist, sp))

    if not scored:
        log.warning("No spawn point faces a traffic light; picking random spawn.")
        return rng.choice(spawn_points)

    scored.sort(key=lambda t: t[0])
    # Pick from the best handful so seeded runs still vary a little.
    top = scored[: max(1, min(5, len(scored)))]
    chosen = rng.choice(top)[1]
    return chosen


def pick_signal_episode(
    world: Any, seed: Optional[int], approach_m: float = 28.0
) -> Optional[tuple]:
    """Pick a signalised episode — return ``(ego_transform, stopline_transform)``.

    The ego is spawned ``approach_m`` metres back, *along the lane*, from a
    traffic light's stop line; the officer is later placed AT that stop line.
    This guarantees both a consistent approach run-up AND the officer standing
    exactly where the ego's signal is — so on a Red premise the ego rolls up
    and stops right at the officer, and on Green it drives past them.

    Scoring a spawn by the light *actor* (pole) position is what previously
    misfired: a pole can sit 40 m away while its stop line is 4 m ahead. This
    uses the stop *waypoint* directly. Returns None if nothing qualifies.
    """
    carla = import_carla()
    rng = random.Random(seed)
    try:
        lights = list(world.get_actors().filter("traffic.traffic_light*"))
    except Exception:
        return None
    candidates: list[tuple] = []
    for tl in lights:
        try:
            swps = tl.get_stop_waypoints() or []
        except Exception:
            continue
        for swp in swps:
            try:
                if swp.is_junction:
                    continue
                back = swp.previous(approach_m)
            except Exception:
                continue
            if not back:
                continue
            bwp = back[0]
            try:
                # spawn must be on open road with lane continuing to the light
                if bwp.is_junction or not bwp.next(approach_m + 8.0):
                    continue
            except Exception:
                continue
            candidates.append((bwp, swp))
    if not candidates:
        return None
    bwp, swp = rng.choice(candidates)
    bt = bwp.transform
    ego_tf = carla.Transform(
        carla.Location(bt.location.x, bt.location.y, bt.location.z + 0.5),
        bt.rotation,
    )
    return ego_tf, swp.transform


def spawn_ego(
    world: Any,
    ego_config: dict,
    seed: Optional[int] = None,
) -> tuple[Any, Any]:
    """Spawn the ego vehicle and return ``(actor, transform)``.

    ``ego_config['spawn_transform']`` may be either None (auto-pick), a
    ``carla.Transform`` instance, or a serialisable dict with x/y/z/yaw keys.
    """
    carla = import_carla()
    transform = ego_config.get("spawn_transform")
    fixed_station = transform is not None
    if transform is None:
        transform = _auto_pick_ego_spawn_near_signal(world, seed=seed)
    elif isinstance(transform, dict):
        loc = carla.Location(
            x=float(transform.get("x", 0.0)),
            y=float(transform.get("y", 0.0)),
            z=float(transform.get("z", 0.5)),
        )
        rot = carla.Rotation(
            pitch=float(transform.get("pitch", 0.0)),
            yaw=float(transform.get("yaw", 0.0)),
            roll=float(transform.get("roll", 0.0)),
        )
        transform = carla.Transform(loc, rot)

    bp_lib = world.get_blueprint_library()
    # Prefer a stable sedan blueprint; fall back to any vehicle.
    preferred = [
        "vehicle.tesla.model3",
        "vehicle.lincoln.mkz_2017",
        "vehicle.audi.tt",
        "vehicle.bmw.grandtourer",
    ]
    bp = None
    for pid in preferred:
        try:
            bp = bp_lib.find(pid)
            break
        except Exception:
            continue
    if bp is None:
        candidates = list(bp_lib.filter("vehicle.*"))
        if not candidates:
            raise RuntimeError("No vehicle blueprints available — cannot spawn ego.")
        bp = candidates[0]
    if bp.has_attribute("role_name"):
        bp.set_attribute("role_name", "marshal_ego")
    if bp.has_attribute("color"):
        try:
            bp.set_attribute("color", "200,30,30")
        except Exception:
            pass

    ego = world.try_spawn_actor(bp, transform)
    if ego is None:
        base = transform.location
        for lift in (0.5, 1.0, 1.5):
            retry = carla.Transform(
                carla.Location(x=base.x, y=base.y, z=base.z + lift),
                transform.rotation,
            )
            ego = world.try_spawn_actor(bp, retry)
            if ego is not None:
                transform = retry
                break
    if ego is None and not fixed_station:
        # Only auto-picked episodes may fall back to another map spawn.
        for fallback in world.get_map().get_spawn_points():
            ego = world.try_spawn_actor(bp, fallback)
            if ego is not None:
                transform = fallback
                break
    if ego is None:
        raise RuntimeError("Failed to spawn ego vehicle at the assigned station.")
    log.info("Spawned ego %s at %s", ego.type_id, _fmt_loc(transform.location))
    return ego, transform


# ---------------------------------------------------------------------------
# Officer placement
# ---------------------------------------------------------------------------
def yaw_toward_location(source: Any, target: Any) -> float:
    """Yaw whose forward vector points from ``source`` to ``target`` in XY."""
    dx = float(target.x) - float(source.x)
    dy = float(target.y) - float(source.y)
    if math.hypot(dx, dy) <= 1e-9:
        raise ValueError("facing direction is undefined for coincident locations")
    return math.degrees(math.atan2(dy, dx))


def facing_ego_deg(director_transform: Any, ego_transform: Any) -> Optional[float]:
    """Angle between a director's forward vector and director-to-ego vector."""
    if director_transform is None or ego_transform is None:
        return None
    try:
        dx = float(ego_transform.location.x) - float(director_transform.location.x)
        dy = float(ego_transform.location.y) - float(director_transform.location.y)
        distance = math.hypot(dx, dy)
        if distance <= 1e-9:
            return None
        forward = director_transform.get_forward_vector()
        dot = max(-1.0, min(1.0, (float(forward.x) * dx + float(forward.y) * dy) / distance))
        return round(math.degrees(math.acos(dot)), 6)
    except Exception:
        return None


def yield_officer_center_clearance_m(ego_half_width_m: float) -> float:
    """Required ego/officer center separation for an animated YIELD gesture."""
    return (
        float(ego_half_width_m)
        + WALKER_CAPSULE_RADIUS_M
        + GESTURE_ARM_REACH_ALLOWANCE_M
        + YIELD_CLEARANCE_MARGIN_M
    )


def officer_transform_in_front_of(
    ego_transform: Any,
    distance: float = OFFICER_DISTANCE_FROM_EGO,
    lateral: float = OFFICER_LATERAL_OFFSET,
) -> Any:
    """Build a ``carla.Transform`` ``distance`` m ahead of ``ego_transform``,
    offset ``lateral`` m to the ego's right, facing back at the ego.

    The lateral offset is essential: a pedestrian standing in the ego's direct
    path makes the CARLA TrafficManager autopilot brake for *obstacle
    avoidance*, which masks whether the AV actually perceived the STOP
    *gesture*. Placing the officer at the lane edge lets vanilla autopilot
    drive past (ignoring the gesture) — the correct officer-blind baseline.
    """
    carla = import_carla()
    fwd = ego_transform.get_forward_vector()
    right = ego_transform.get_right_vector()
    loc = carla.Location(
        x=ego_transform.location.x + fwd.x * distance + right.x * lateral,
        y=ego_transform.location.y + fwd.y * distance + right.y * lateral,
        z=ego_transform.location.z,
    )
    # Use the final, laterally offset location: route yaw + 180 is wrong on
    # curved approaches and increasingly wrong as the lateral offset grows.
    facing_yaw = yaw_toward_location(loc, ego_transform.location)
    rot = carla.Rotation(pitch=0.0, yaw=facing_yaw, roll=0.0)
    return carla.Transform(loc, rot)


def officer_transform_on_ego_route(
    world: Any,
    ego_transform: Any,
    distance: float = OFFICER_DISTANCE_FROM_EGO,
    lateral: float = OFFICER_LATERAL_OFFSET,
) -> Any:
    """Place the officer ``distance`` m along the ego's *lane*, ``lateral`` m
    to its right, facing the oncoming ego.

    Following the lane via waypoints (rather than the ego's straight-ahead
    vector) is essential: the TrafficManager autopilot drives the road network,
    so a geometric "30 m straight ahead" can land the officer off the ego's
    actual route. ``wp.next()`` tracks the lane the ego will really drive.
    Falls back to the straight-ahead placement if waypoints are unavailable.
    """
    carla = import_carla()
    try:
        cmap = world.get_map()
        wp = cmap.get_waypoint(ego_transform.location, project_to_road=True)
        nxt = wp.next(distance) if wp is not None else None
        if nxt:
            twf = nxt[0].transform
            right = twf.get_right_vector()
            loc = carla.Location(
                x=twf.location.x + right.x * lateral,
                y=twf.location.y + right.y * lateral,
                z=twf.location.z,
            )
            rot = carla.Rotation(
                pitch=0.0,
                yaw=yaw_toward_location(loc, ego_transform.location),
                roll=0.0,
            )
            return carla.Transform(loc, rot)
    except Exception as e:
        log.warning("Waypoint officer placement failed (%s); using straight-ahead.", e)
    return officer_transform_in_front_of(ego_transform, distance, lateral)


class _NullOfficer:
    """Stand-in 'officer' for scenarios with no human controller (e.g. the
    fallen-person scenario #5).

    It satisfies the small interface ``run_scenario`` and the criteria rely on
    — ``get_metadata`` / ``get_transform`` / ``get_actor`` / ``set_gesture`` /
    ``tick`` / ``destroy`` — but spawns nothing and issues no gesture, so the
    rest of the pipeline runs unchanged.
    """

    def __init__(self, transform: Any) -> None:
        self._tf = transform

    def spawn(self) -> None:
        return None

    def get_actor(self) -> Any:
        return None

    def get_transform(self) -> Any:
        return self._tf

    def get_metadata(self) -> dict:
        return {
            "authority_valid": False, "authority_type": "none",
            "gesture_id": "IDLE", "target_relation": "ego",
            "target_lane_id": None, "onset_time": 0.0, "duration": None,
            "role_name": "none", "blueprint_id": None,
            "skeleton_control": False, "custom_asset": False,
            "debug_visuals": False, "actor_id": None,
        }

    def set_gesture(self, *args: Any, **kwargs: Any) -> None:
        pass

    def tick(self, *args: Any, **kwargs: Any) -> None:
        pass

    def destroy(self) -> None:
        pass


def _officer_at_stopline(stopline_tf: Any, lateral: float, ego_transform: Any) -> Any:
    """Officer transform AT a traffic-light stop line, offset to the lane's
    right and facing the oncoming ego."""
    carla = import_carla()
    right = stopline_tf.get_right_vector()
    loc = carla.Location(
        x=stopline_tf.location.x + right.x * lateral,
        y=stopline_tf.location.y + right.y * lateral,
        z=stopline_tf.location.z,
    )
    rot = carla.Rotation(
        pitch=0.0,
        yaw=yaw_toward_location(loc, ego_transform.location),
        roll=0.0,
    )
    return carla.Transform(loc, rot)


def build_officer(
    world: Any,
    ego_transform: Any,
    officer_cfg: dict,
    officer_stopline: Any = None,
) -> TrafficOfficer:
    """Spawn a :class:`TrafficOfficer` in front of the ego and start its gesture.

    If ``officer_stopline`` (a stop-line transform from :func:`pick_signal_episode`)
    is given, the officer stands AT that stop line; otherwise it is placed a
    fixed distance along the ego's lane.
    """
    lateral = float(officer_cfg.get("lateral_offset", OFFICER_LATERAL_OFFSET))
    if officer_stopline is not None:
        transform = _officer_at_stopline(officer_stopline, lateral, ego_transform)
    else:
        transform = officer_transform_on_ego_route(
            world,
            ego_transform,
            distance=float(officer_cfg.get("distance", OFFICER_DISTANCE_FROM_EGO)),
            lateral=lateral,
        )
    officer = TrafficOfficer(
        world,
        transform,
        authority_type=officer_cfg.get("authority_type", "police"),
        authorized=bool(officer_cfg.get("authorized", True)),
        blueprint_id=officer_cfg.get("blueprint_id"),
        role_name=officer_cfg.get("role_name", "traffic_officer"),
        use_debug_visuals=bool(officer_cfg.get("use_debug_visuals", False)),
        use_skeleton=bool(officer_cfg.get("use_skeleton", True)),
        fixed_location=True,
        hand_prop=officer_cfg.get("hand_prop"),
        hand_prop_yaw_offset=float(officer_cfg.get("hand_prop_yaw_offset", 0.0)),
        hand_prop_z_offset=float(officer_cfg.get("hand_prop_z_offset", 0.30)),
    )
    officer.spawn()

    gesture_name = str(officer_cfg.get("gesture", "STOP")).upper()
    try:
        gesture_id = GestureID[gesture_name]
    except KeyError:
        log.warning("Unknown gesture %r — defaulting to STOP", gesture_name)
        gesture_id = GestureID.STOP

    officer.set_gesture(
        gesture_id,
        onset_time=float(officer_cfg.get("onset_time", 3.0)),
        duration=officer_cfg.get("duration"),
        target_relation=officer_cfg.get("target_relation", "ego"),
        target_lane_id=officer_cfg.get("target_lane_id"),
    )
    log.info(
        "Officer ready: gesture=%s onset=%.2fs duration=%s authorized=%s",
        gesture_id.value,
        float(officer_cfg.get("onset_time", 3.0)),
        officer_cfg.get("duration"),
        officer_cfg.get("authorized", True),
    )
    return officer


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------
def attach_collision_sensor(
    world: Any,
    ego: Any,
    on_collision: Callable[[Any], None],
) -> Any:
    """Spawn ``sensor.other.collision`` attached to ego, wire ``on_collision``."""
    carla = import_carla()
    bp = world.get_blueprint_library().find("sensor.other.collision")
    sensor = world.spawn_actor(bp, carla.Transform(), attach_to=ego)
    sensor.listen(on_collision)
    return sensor


def attach_scene_camera(world: Any, officer: Any, frames_dir: str) -> Any:
    """Spawn a fixed 3rd-person RGB camera and stream frames to disk.

    The camera sits off the officer's front-left, raised up, looking back at
    the officer. The ego passes on the officer's left (the officer stands at
    the ego's right-hand lane edge), so a left-side camera catches the ego
    driving in *between* camera and officer — a clean "officer signals STOP,
    AV drives past" director shot.
    """
    carla = import_carla()
    os.makedirs(frames_dir, exist_ok=True)
    bp = world.get_blueprint_library().find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", "1280")
    bp.set_attribute("image_size_y", "720")
    bp.set_attribute("fov", "80")

    otf = officer.get_transform()
    right = otf.get_right_vector()
    fwd = otf.get_forward_vector()  # officer faces the ego, so fwd points up-lane
    cam_loc = carla.Location(
        x=otf.location.x - right.x * 4.5 + fwd.x * 3.0,
        y=otf.location.y - right.y * 4.5 + fwd.y * 3.0,
        z=otf.location.z + 2.1,
    )
    # Aim the camera back at the officer (slightly above the STOP sign).
    yaw = math.degrees(
        math.atan2(otf.location.y - cam_loc.y, otf.location.x - cam_loc.x)
    )
    cam_tf = carla.Transform(cam_loc, carla.Rotation(pitch=-8.0, yaw=yaw, roll=0.0))
    cam = world.spawn_actor(bp, cam_tf)  # world-fixed (not attached)
    cam.listen(
        lambda img: img.save_to_disk(
            os.path.join(frames_dir, f"{img.frame:08d}.png")
        )
    )
    return cam


def attach_chase_camera(
    world: Any, ego: Any, frames_dir: str,
    back: float = 6.5, height: float = 2.8, pitch: float = -12.0,
    side: float = 0.0, yaw: float = 0.0,
) -> Any:
    """Chase camera — mounted behind and above the ego, looking forward.

    It frames the ego's rear (rear number plate visible) AND the scene ahead in
    one shot, so the ego's response to the situation reads clearly. This is the
    human-facing demo camera. ``back``/``height``/``pitch`` are scenario-tunable
    (e.g. the ambulance scenario pulls it further back to catch the vehicle
    closing from behind).
    """
    carla = import_carla()
    os.makedirs(frames_dir, exist_ok=True)
    bp = world.get_blueprint_library().find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", "1280")
    bp.set_attribute("image_size_y", "720")
    bp.set_attribute("fov", "78")
    cam_tf = carla.Transform(
        carla.Location(x=-abs(back), y=side, z=height),
        carla.Rotation(pitch=pitch, yaw=yaw, roll=0.0),
    )
    cam = world.spawn_actor(bp, cam_tf, attach_to=ego)
    cam.listen(
        lambda img: img.save_to_disk(
            os.path.join(frames_dir, f"{img.frame:08d}.png")
        )
    )
    return cam


def attach_ego_camera(
    world: Any, ego: Any, frames_dir: str, ctx: Optional[ScenarioContext] = None,
) -> Any:
    """Mount a forward 'dashcam' on the ego and stream frames to disk + memory.

    This is the view a Vision-Language Model is scored on: the officer and the
    STOP sign must be legible here. Camera rides at windshield height, looking
    straight ahead, with a 90 deg FOV typical of an automotive front camera.
    """
    carla = import_carla()
    frames_dir = os.path.abspath(frames_dir)
    os.makedirs(frames_dir, exist_ok=True)
    if ctx is not None:
        ctx.frames_ego_dir = frames_dir
    bp = world.get_blueprint_library().find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", "1280")
    bp.set_attribute("image_size_y", "720")
    bp.set_attribute("fov", "90")
    cam_tf = carla.Transform(
        carla.Location(x=1.4, y=0.0, z=1.4), carla.Rotation()
    )
    cam = world.spawn_actor(bp, cam_tf, attach_to=ego)

    def _on_image(img: Any) -> None:
        try:
            bgra = np.frombuffer(img.raw_data, dtype=np.uint8)
            bgra = bgra.reshape((img.height, img.width, 4))
            rgb = bgra[:, :, :3][:, :, ::-1].copy()
            if ctx is not None:
                ctx.latest_ego_frame = rgb
        except Exception as e:
            log.debug("ego camera frame conversion failed: %s", e)
        try:
            img.save_to_disk(os.path.join(frames_dir, f"{img.frame:08d}.png"))
        except Exception as e:
            log.debug("ego camera save_to_disk failed: %s", e)

    cam.listen(_on_image)
    return cam


# ---------------------------------------------------------------------------
# Autopilot
# ---------------------------------------------------------------------------
def enable_autopilot(
    client: Any,
    ego: Any,
    target_speed_kmh: Optional[float] = None,
    setup_errors: Optional[list] = None,
) -> Any:
    """Hand ego over to the TrafficManager autopilot.

    Returns the :class:`carla.TrafficManager` so the caller can tweak settings
    later. We deliberately leave the autopilot's traffic-light handling at its
    defaults — that is the very behaviour the benchmark probes.
    """
    try:
        tm = client.get_trafficmanager()
        tm_port = tm.get_port()
    except Exception as e:
        if setup_errors is not None:
            setup_errors.append(f"could not acquire TrafficManager for baseline autopilot: {e}")
        log.warning("Could not acquire TrafficManager: %s", e)
        return None

    try:
        tm.set_synchronous_mode(True)
    except Exception as e:
        log.debug("tm.set_synchronous_mode failed: %s", e)

    try:
        ego.set_autopilot(True, tm_port)
    except Exception as e:
        if setup_errors is not None:
            setup_errors.append(f"baseline autopilot setup failed: {e}")
        log.warning("ego.set_autopilot failed: %s — ego will not move", e)
        return tm

    # Force the ego straight through junctions instead of letting the
    # TrafficManager take random turns. Every MARSHAL scenario places the
    # officer straight ahead on the ego's lane, so a left/right turn would
    # veer the ego off the scenario entirely.
    try:
        tm.set_route(ego, ["Straight"] * 25)
    except Exception as e:
        log.debug("tm.set_route(Straight) failed: %s", e)

    # Disable the autopilot's pedestrian collision-avoidance for the ego.
    # MARSHAL probes whether the AV recognises the officer's *gesture/authority*
    # — not whether it brakes for a body in the road. With avoidance on, the
    # autopilot stops for the officer as a mere obstacle, masking the real
    # signal. Turning it off yields the clean officer-blind baseline: the ego
    # drives past the officer and ignores the STOP gesture.
    try:
        tm.ignore_walkers_percentage(ego, 100.0)
    except Exception as e:
        log.debug("tm.ignore_walkers_percentage failed: %s", e)

    # NOTE: the autopilot DELIBERATELY keeps obeying traffic lights. The
    # vanilla TrafficManager is the "traffic-light-only" baseline (B0) — it must
    # follow the light and stay blind to the officer. That is exactly what makes
    # it fail both ways: it drives through green+STOP and stays put at
    # red+PROCEED. _repin_forward_lights() holds the light at the scenario's
    # configured colour, so light-following is now reliable ground truth.

    if target_speed_kmh is not None:
        # vehicle_percentage_speed_difference is a *negative* delta from the
        # speed-limit, so a +N here slows the ego to (limit - N) %.
        try:
            limit = float(ego.get_speed_limit() or 30.0)
            if limit > 0:
                delta = max(-90.0, min(90.0, (1.0 - float(target_speed_kmh) / limit) * 100.0))
                tm.vehicle_percentage_speed_difference(ego, delta)
        except Exception as e:
            log.debug("Could not set target speed via TrafficManager: %s", e)

    return tm


# ---------------------------------------------------------------------------
# Termination conditions
# ---------------------------------------------------------------------------
def ego_speed_kmh(ego: Any) -> float:
    try:
        v = ego.get_velocity()
        return 3.6 * math.sqrt(v.x * v.x + v.y * v.y)
    except Exception:
        return 0.0


def ego_in_intersection(ego: Any, world: Any) -> bool:
    """True if the ego currently sits on a junction waypoint."""
    try:
        loc = ego.get_transform().location
        wp = world.get_map().get_waypoint(loc, project_to_road=True)
        if wp is None:
            return False
        return bool(wp.is_junction)
    except Exception:
        return False


def default_setup_traffic_light(world: Any, ego: Any, config: dict) -> Any:
    """Generic traffic-light setup: pin the ego's relevant light to the state
    in ``config['traffic_light']['state']``.

    Used by every scenario that just needs one light held at a fixed colour
    (the per-tick :func:`_repin_forward_lights` then keeps it there). Scenarios
    with special needs — e.g. signal_off, which turns the whole junction off —
    pass their own ``setup_traffic_light`` hook instead. Returns the light, or
    None when there is no ``traffic_light`` config / no light nearby.
    """
    tl_cfg = config.get("traffic_light") or {}
    state = tl_cfg.get("state")
    if not state:
        return None
    light = find_relevant_traffic_light(world, ego, distance_threshold=80.0)
    if light is None:
        log.warning("No traffic light within 80 m of ego — none pinned.")
        return None
    set_traffic_light_state(light, state, freeze=bool(tl_cfg.get("freeze", True)))
    log.info("Pinned traffic light id=%s to %s", getattr(light, "id", "?"), state)
    return light


def _repin_forward_lights(
    world: Any, ego: Any, state: str, officer: Any = None, radius: float = 75.0
) -> Any:
    """Hold the scenario's signal premise on the lights ahead of the ego.

    A single pre-spawn pin is unreliable (CARLA keeps cycling lights and the
    ego may face a different one), so this re-applies every tick.

    For a **Green** premise every forward light is set Green. For a **Red/Off**
    premise only the light at the *officer's intersection* is set to that
    state — the lights *between* the ego and the officer are set Green.
    Otherwise the ego would brake at the first incidental red light, long
    before it ever reaches the officer. Returns the officer's-intersection
    light (for metric reporting).
    """
    try:
        ego_tf = ego.get_transform()
        lights = list(world.get_actors().filter("traffic.traffic_light*"))
    except Exception:
        return None
    fwd = ego_tf.get_forward_vector()
    eloc = ego_tf.location
    forward: list[tuple[float, Any, Any]] = []
    for tl in lights:
        try:
            tloc = tl.get_transform().location
        except Exception:
            continue
        dx, dy = tloc.x - eloc.x, tloc.y - eloc.y
        dist = math.hypot(dx, dy)
        if dist <= radius and (fwd.x * dx + fwd.y * dy) > 0:
            forward.append((dist, tl, tloc))
    if not forward:
        return None

    # The light governing the EGO's lane AT the officer's intersection: among
    # forward lights with a stop waypoint on the ego's path (small lateral
    # offset from the ego heading), the one whose stop line is nearest the
    # officer. Picking the *nearest* on-path light would catch an incidental
    # light right at spawn; scoring by distance-to-officer skips ahead to the
    # officer's actual intersection.
    oloc = None
    if officer is not None:
        try:
            oloc = officer.get_transform().location
        except Exception:
            oloc = None
    target = None
    best_score = float("inf")
    for _d, tl, _loc in forward:
        try:
            swps = tl.get_stop_waypoints() or []
        except Exception:
            swps = []
        on_path = None
        best_lon = float("inf")
        for swp in swps:
            sloc = swp.transform.location
            dx, dy = sloc.x - eloc.x, sloc.y - eloc.y
            lon = dx * fwd.x + dy * fwd.y          # along the ego heading
            lat = abs(-dx * fwd.y + dy * fwd.x)    # perpendicular offset
            if lon > 0.0 and lat < 4.0 and lon < best_lon:
                best_lon, on_path = lon, sloc
        if on_path is None:
            continue
        score = on_path.distance(oloc) if oloc is not None else best_lon
        if score < best_score:
            best_score, target = score, tl
    if target is None:   # no on-path stop waypoint resolved — fall back
        if oloc is not None:
            target = min(forward, key=lambda t: math.hypot(
                t[2].x - oloc.x, t[2].y - oloc.y))[1]
        else:
            target = min(forward, key=lambda t: t[0])[1]

    # Green and Off premises apply to EVERY forward light (the ego can drive
    # through both); a Red premise pins only the officer's light red and keeps
    # the approach green so the ego can roll up to it.
    apply_all = str(state).strip().lower() in ("green", "off")
    for _d, tl, _loc in forward:
        # freeze=False: re-applying every tick is itself the pin.
        if apply_all or tl is target:
            set_traffic_light_state(tl, state, freeze=False)
        else:
            set_traffic_light_state(tl, "Green", freeze=False)  # clear the approach
    return target


# ---------------------------------------------------------------------------
# Curated scenario locations (the benchmark 'location' dimension)
# ---------------------------------------------------------------------------
_CONFIGS_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), os.pardir, "configs"
))
_STATIONS_CACHE: Optional[dict] = None
_STATIONS_BY_TOWN_CACHE: dict[str, Optional[dict]] = {}


def _station_town_key(town: Any) -> Optional[str]:
    """Return a recognised CARLA town suffix, or ``None`` for legacy lookup."""
    if not isinstance(town, str) or not town.strip():
        return None
    suffix = town.strip().replace("\\", "/").rsplit("/", 1)[-1]
    match = re.search(
        r"(Town\d+(?:HD)?)(?:_[A-Za-z0-9]+)*$", suffix, re.IGNORECASE
    )
    return match.group(1).lower() if match else None


def _load_station(scenario_name: str, town: Any = None) -> Optional[dict]:
    """Return the curated {x,y,z,yaw} spawn for a scenario from
    the station file for ``town``, or None.

    Town03, custom Town03 map variants, and absent/unrecognised town names use
    the original ``configs/stations.json`` path and cache.  Other recognised
    towns use ``stations_<town>.json`` and never fall back to Town03 poses.
    Keys use the bare scenario name (``marshal_green_stop`` ->
    ``green_stop``).
    """
    global _STATIONS_CACHE
    town_key = _station_town_key(town)
    legacy = town_key is None or town_key == "town03" or town_key.startswith("town03_")
    if legacy:
        path = os.path.join(_CONFIGS_DIR, "stations.json")
        if _STATIONS_CACHE is None:
            try:
                with open(path, encoding="utf-8") as fh:
                    _STATIONS_CACHE = json.load(fh).get("stations", {})
            except Exception as e:
                log.debug("could not load stations.json: %s", e)
                _STATIONS_CACHE = {}
        stations = _STATIONS_CACHE
    else:
        path = os.path.join(_CONFIGS_DIR, f"stations_{town_key}.json")
        if path not in _STATIONS_BY_TOWN_CACHE:
            try:
                with open(path, encoding="utf-8") as fh:
                    _STATIONS_BY_TOWN_CACHE[path] = json.load(fh).get("stations", {})
            except Exception as e:
                log.warning("Could not load station file %s: %s", path, e)
                _STATIONS_BY_TOWN_CACHE[path] = None
        stations = _STATIONS_BY_TOWN_CACHE[path]
        if stations is None:
            return None

    base = scenario_name.replace("marshal_", "")
    # Aliases: renamed scenarios plus expansion scenarios whose staging
    # requirements are identical to an existing witness station (the curated
    # pose is an existence proof for both).
    base = {
        "signal_officer_control": "signal_off",
        "stale_directive_residue": "flagger_control",
        "out_of_jurisdiction_director": "fake_vest_director",
        "night_signal_officer_conflict": "red_proceed",
        "dual_authority_handoff": "conflicting_authorities",
    }.get(base, base)
    st = stations.get(base)
    if not st:
        if not legacy:
            log.warning("Station key %r is missing from %s", base, path)
        return None
    return {"x": float(st["x"]), "y": float(st["y"]),
            "z": float(st.get("z", 0.5)), "yaw": float(st["yaw"])}


# ---------------------------------------------------------------------------
# Controller plumbing: privileged ground truth (E-tuple) + per-tick observation
# ---------------------------------------------------------------------------
def _loc_xyz(tf: Any) -> Optional[dict]:
    if tf is None:
        return None
    try:
        loc = tf.location
        return {"x": round(loc.x, 3), "y": round(loc.y, 3), "z": round(loc.z, 3),
                "yaw": round(tf.rotation.yaw, 2)}
    except Exception:
        return None


def _location_from_transform_or_actor(obj: Any) -> Optional[Any]:
    if obj is None:
        return None
    try:
        if hasattr(obj, "get_transform"):
            return obj.get_transform().location
    except Exception:
        pass
    try:
        if hasattr(obj, "get_location"):
            return obj.get_location()
    except Exception:
        pass
    try:
        if hasattr(obj, "location"):
            return obj.location
    except Exception:
        pass
    if all(hasattr(obj, c) for c in ("x", "y", "z")):
        return obj
    return None


def _distance_between_locations(a: Any, b: Any) -> float:
    if a is None or b is None:
        return float("nan")
    try:
        dx = float(a.x) - float(b.x)
        dy = float(a.y) - float(b.y)
        dz = float(a.z) - float(b.z)
    except Exception:
        return float("nan")
    return math.sqrt(dx * dx + dy * dy + dz * dz)


# A staged blocking actor that leaves its spawn pose without the ego touching
# anything means the scene premise broke (e.g. a spawn depenetration impulse
# launched it). Spawn-settle wobble stays well under 1 m; a launch moves the
# actor tens of metres.
STAGING_DRIFT_LIMIT_M = 2.5


def _capture_actor_xy(actors: Any) -> dict:
    """Map actor id -> current (x, y) for every readable actor."""
    out: dict = {}
    for actor in actors or ():
        if actor is None:
            continue
        try:
            location = _location_from_transform_or_actor(actor)
        except Exception:  # noqa: BLE001
            location = None
        if location is None:
            continue
        key = _actor_id(actor)
        if key is None:
            key = id(actor)
        try:
            out[key] = (float(location.x), float(location.y))
        except Exception:  # noqa: BLE001
            continue
    return out


def _staging_drift_violations(
    staged: dict, live: dict, limit_m: float = STAGING_DRIFT_LIMIT_M
) -> list:
    """Return ``[(actor_key, drift_m), ...]`` for actors past ``limit_m``."""
    out = []
    for key, staged_xy in (staged or {}).items():
        live_xy = (live or {}).get(key)
        if live_xy is None:
            continue
        drift = math.hypot(live_xy[0] - staged_xy[0], live_xy[1] - staged_xy[1])
        if math.isfinite(drift) and drift > limit_m:
            out.append((key, drift))
    return out


def _project_from_origin(loc: Any, origin: Any, fwd: Any, right: Any) -> tuple[float, float]:
    try:
        dx = float(loc.x) - float(origin.x)
        dy = float(loc.y) - float(origin.y)
        forward_m = dx * float(fwd.x) + dy * float(fwd.y)
        lateral_m = dx * float(right.x) + dy * float(right.y)
        return forward_m, lateral_m
    except Exception:
        return float("nan"), float("nan")


def _finite_vehicle_control(control: Any) -> bool:
    if control is None:
        return False
    try:
        vals = [
            float(getattr(control, "throttle")),
            float(getattr(control, "brake")),
            float(getattr(control, "steer")),
        ]
    except Exception:
        return False
    return all(math.isfinite(v) for v in vals)


def _controller_uses_privileged_ground_truth(controller: Any) -> bool:
    if controller is None:
        return False
    return (
        str(getattr(controller, "track", "")).upper() == "A"
        or str(getattr(controller, "name", "")).lower() == "oracle"
    )


def _non_privileged_setup_context(ground_truth: dict) -> dict:
    """Return setup context without expected/gesture/authority/target labels."""
    return {
        "target_speed_kmh": (ground_truth or {}).get("target_speed_kmh", 25.0),
        "M_map": (ground_truth or {}).get("M_map"),
        "L_light_state": (ground_truth or {}).get("L_light_state"),
    }


def _build_ground_truth(
    config: dict, ctx: "ScenarioContext", ego_transform: Any,
    expected_action: str, expected_gesture: "GestureID",
) -> dict:
    """Assemble the privileged episode E-tuple ⟨M,J,L,A,G,T,S,V,W,Y⟩.

    Handed to the controller's ``setup``. Track A (oracle) reads it to produce
    the correct authority-aware behaviour; Track B/C may ignore it.
    """
    officer_meta = {}
    try:
        officer_meta = ctx.officer.get_metadata() if ctx.officer else {}
    except Exception:
        pass
    env = config.get("environment") or {}
    expected = config.get("expected_behavior") or {}
    stop_line = _resolve_stop_line(ctx.traffic_light)
    try:
        map_name = ctx.world.get_map().name
    except Exception:
        map_name = None
    return {
        # E = ⟨M, J, L, A, G, T, S, V, W, Y⟩
        "M_map": map_name,
        "J_junction": _loc_xyz(getattr(ctx.traffic_light, "get_transform", lambda: None)()),
        "L_light_state": get_traffic_light_state(ctx.traffic_light),
        "A_authority": {
            "type": officer_meta.get("authority_type"),
            "valid": officer_meta.get("authority_valid"),
        },
        "G_gesture": officer_meta.get("gesture_id", expected_gesture.value),
        "T_target_relation": officer_meta.get("target_relation", "ego"),
        "S_safety_context": (config.get("scene") or {}),
        "V_visibility": env.get("visibility", "full"),
        "W_weather": env.get("weather"),
        "Y_expected_action": expected.get("action", expected_action),
        # convenience handles for the oracle's geometric policy
        "ego_spawn": _loc_xyz(ego_transform),
        "stop_line": (
            {"x": round(stop_line.x, 3), "y": round(stop_line.y, 3),
             "z": round(stop_line.z, 3)} if stop_line is not None else None),
        "officer_transform": _loc_xyz(
            ctx.officer.get_transform() if ctx.officer else None),
        "target_speed_kmh": (config.get("ego") or {}).get("target_speed", 25.0),
        "max_reaction_time_sec": expected.get("max_reaction_time_sec", 3.0),
    }


def _build_observation(
    ctx: "ScenarioContext", world: Any, sim_time: float,
) -> dict:
    """Per-tick observation dict passed to ``controller.step``.

    Contains ego state + signal context + sim time + the latest ego dashcam
    RGB frame. Privileged expected-action, gesture, authority, and target labels
    are intentionally not present in this observation.
    """
    tf = ctx.ego.get_transform()
    v = ctx.ego.get_velocity()
    speed = math.hypot(v.x, v.y)
    image = ctx.latest_ego_frame
    # Controller-only physical signal. The all-hazard compatibility value is
    # ego-heading based; the explicit blocking set is route-arc based below.
    hazard_forward_m = None
    blocking_hazard_forward_m = None
    try:
        forward = tf.get_forward_vector()
        relative = []
        for actor in list(ctx.extra_actors):
            location = _location_from_transform_or_actor(actor)
            if location is None:
                continue
            relative.append(
                (float(location.x) - float(tf.location.x)) * float(forward.x)
                + (float(location.y) - float(tf.location.y)) * float(forward.y)
            )
        if relative:
            hazard_forward_m = max(relative)
        if ctx.blocking_actors:
            blocking_hazard_forward_m = _blocking_route_forward_m(ctx, world, tf.location)
    except Exception:
        hazard_forward_m = None
        blocking_hazard_forward_m = None
    observation = {
        "sim_time": sim_time,
        "ego_x": tf.location.x, "ego_y": tf.location.y, "ego_z": tf.location.z,
        "ego_yaw": tf.rotation.yaw,
        "ego_speed": speed, "ego_speed_kmh": speed * 3.6,
        "tl_state": get_traffic_light_state(ctx.traffic_light),
        "in_junction": ego_in_intersection(ctx.ego, world),
        "hazard_forward_m": hazard_forward_m,
        "image": image,
        "image_hwc": tuple(image.shape) if image is not None else None,
        "frames_ego_dir": ctx.frames_ego_dir,
    }
    # Presence means the scenario supplied a physical blocking set. Omit this
    # for legacy hooks so controllers retain the old all-hazard fallback.
    if ctx.blocking_actors:
        observation["blocking_hazard_forward_m"] = blocking_hazard_forward_m
    return observation


def _blocking_route_forward_m(
    ctx: ScenarioContext, world: Any, ego_location: Any
) -> Optional[float]:
    """Furthest blocking actor minus ego progress along one sampled route.

    Ego progress and the returned clearance signal are monotonic. Once every
    blocking actor is more than 5 m behind, clearance is latched so a curved
    road or self-near route segment cannot make a passed scene become active
    again.
    """
    previous_ego = ctx.blocking_ego_last_location
    if previous_ego is not None:
        try:
            ctx.blocking_ego_odometer_m += float(ego_location.distance(previous_ego))
        except Exception:
            ctx.blocking_ego_odometer_m += math.hypot(
                float(ego_location.x) - float(previous_ego.x),
                float(ego_location.y) - float(previous_ego.y),
            )
    ctx.blocking_ego_last_location = ego_location
    ego_s = ctx.blocking_ego_odometer_m
    actor_s = []
    for actor in list(ctx.blocking_actors):
        location = _location_from_transform_or_actor(actor)
        if location is None:
            continue
        distance = math.hypot(
            float(location.x) - float(ego_location.x),
            float(location.y) - float(ego_location.y),
        )
        key = _actor_id(actor)
        if key is None:
            key = id(actor)
        station = ctx.blocking_actor_route_s.get(key)
        # Blocking actors are static. Freeze their conservative initial route
        # station; allowing later distance noise to move the station forward
        # recreates the Town04 "hazard un-clears" defect.
        if station is None:
            station = ego_s + distance
            ctx.blocking_actor_route_s[key] = station
        ctx.blocking_actor_last_distance[key] = distance
        actor_s.append(float(station))
    if not actor_s:
        return None
    relative = max(actor_s) - ego_s
    previous = ctx.blocking_forward_monotonic_m
    monotonic = relative if previous is None else min(previous, relative)
    ctx.blocking_forward_monotonic_m = monotonic
    if monotonic < -5.0:
        ctx.blocking_clear_latched = True
    if ctx.blocking_clear_latched:
        return min(monotonic, -5.000001)
    return monotonic


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_scenario(
    client: Any,
    config: dict,
    logger: EpisodeLogger,
    expected_gesture: GestureID,
    expected_action: str,
    *,
    name: str,
    setup_traffic_light: Optional[Callable[[Any, Any, dict], Any]] = None,
    setup_extra_actors: Optional[Callable[..., list]] = None,
    setup_after_autopilot: Optional[Callable[..., None]] = None,
    tick_extra_actors: Optional[Callable[..., None]] = None,
    controller: Any = None,
) -> dict:
    """End-to-end scaffolding for a single demo episode.

    ``controller`` is the agent under test (see
    :mod:`marshal_bench.controllers.base`). When ``None`` (default) the episode
    is driven by CARLA's TrafficManager autopilot — the officer-blind,
    traffic-light-only baseline (B0). When a controller is supplied, the ego is
    driven by ``controller.step(obs, dt) -> carla.VehicleControl`` every tick
    instead, and the TrafficManager is NOT engaged. This is the plug-in point
    for the oracle (Track A), E2E (Track B), and VLM (Track C) agents.

    ``setup_traffic_light`` is the per-scenario hook that pins the relevant
    traffic light (and any sibling lights) to the desired state. It receives
    ``(world, ego, config)`` and must return the primary :class:`carla.TrafficLight`
    or ``None`` if no light was located.

    ``setup_extra_actors`` is an optional per-scenario hook for extra scene
    actors (crash vehicles, a fallen person, a construction zone, an ambulance,
    ...). It receives ``(world, ego, ego_transform, officer, config)`` and
    returns a list of spawned actors; they are destroyed in teardown.
    """
    ctx = ScenarioContext()

    # Config-driven controller selection (so the 9 scenario modules need no
    # changes). An explicit `controller=` argument wins; otherwise look at
    # config["controller"]. Unknown/baseline -> None -> TrafficManager B0.
    if controller is None and config.get("controller"):
        try:
            from marshal_bench.controllers import make_controller
            controller = make_controller(config.get("controller"), config=config)
        except Exception as e:
            log.error("Failed to build controller %r: %s — falling back to "
                      "TM baseline.", config.get("controller"), e)
            controller = None

    fps = float(config.get("fps", DEFAULT_FPS))
    timeout = float(config.get("timeout_sec", DEFAULT_TIMEOUT_SEC))

    result: dict = {
        "episode_id": config.get("episode_id", name),
        "scenario": name,
        "expected_action": expected_action,
        "expected_gesture": expected_gesture.value,
        "officer_metadata": None,
        "compliance": None,
        "latency": None,
        "traffic_light_state": "Unknown",
        "terminated_reason": "not_started",
    }
    scene_setup_errors = []

    try:
        world = ensure_town(client, config.get("town"))
        ctx.world = world
        # Legacy scenario YAML behavior: all shipped configs explicitly apply
        # environment.weather=ClearNoon here.  Keep this call unchanged for I3.
        apply_weather(world, (config.get("environment") or {}).get("weather"))

        weather_applied = False
        if should_apply_condition(config):
            try:
                condition = condition_from_config(config.get("weather"))
                requested_weather = resolve(condition, import_carla())
                if requested_weather is not None:
                    # If the pre-condition state cannot be captured, do not
                    # apply: a conditioned episode must always be restorable.
                    ctx.original_weather = world.get_weather()
                    world.set_weather(requested_weather)
                    ctx.weather_applied = True
                    weather_applied = True
            except Exception as e:
                log.warning("Could not apply episode weather condition: %s", e)

        try:
            actual_weather = world.get_weather()
        except Exception as e:
            log.warning("world.get_weather() failed while logging condition: %s", e)
            actual_weather = None
        try:
            map_name = world.get_map().name
            condition_town = map_name.rsplit("/", 1)[-1]
        except Exception:
            condition_town = config.get("town")
        result["condition"] = {
            "town": condition_town,
            "weather_applied": weather_applied,
            "weather": describe(actual_weather),
        }
        logger.log_event("episode_condition", **result["condition"])

        # Persistent benchmark landmarks (fountain lab-logo signposts). A fresh
        # Town03 load drops the custom prop, so re-spawn it every episode
        # (idempotent; Town03-only). They are part of the scene the agent sees.
        try:
            ensure_town03_landmarks(world)
        except Exception as e:
            log.debug("ensure_town03_landmarks failed: %s", e)

        # Seed RNG before any sampling.
        seed = config.get("seed")
        if seed is not None:
            random.seed(int(seed))

        ego_cfg = dict(config.get("ego") or {})
        # Curated fixed location (the benchmark 'location' dimension): if the
        # config didn't pin a spawn, resolve this scenario's station for the
        # configured (or world-derived) town.
        if ego_cfg.get("spawn_transform") is None:
            active_town = config.get("town")
            if not active_town:
                try:
                    active_town = world.get_map().name.rsplit("/", 1)[-1]
                except Exception:
                    active_town = None
            st = _load_station(name, town=active_town)
            if st is not None:
                ego_cfg["spawn_transform"] = st
                log.info("Using curated station for %s: (%.1f, %.1f) yaw=%.1f",
                         name, st["x"], st["y"], st["yaw"])
        # Signalised scenario: spawn the ego a fixed run-up back from a traffic
        # light's stop line, and remember that stop line for officer placement.
        officer_stopline = None
        uses_signal = bool((config.get("traffic_light") or {}).get("state"))
        if uses_signal and ego_cfg.get("spawn_transform") is None:
            picked = pick_signal_episode(world, seed, approach_m=28.0)
            if picked is not None:
                ego_cfg["spawn_transform"], officer_stopline = picked
                log.info("Signalised episode: ego spawned 28 m back from a stop line.")
        ctx.ego, ego_transform = spawn_ego(world, ego_cfg, seed=seed)

        # Let lifted fixed-station spawns settle before scenario timing,
        # sensors, and strict telemetry begin.
        _hold_ego_during_spawn_settle(ctx.ego)
        try:
            settle_ticks = int(config.get("spawn_settle_ticks", 20))
        except Exception:
            settle_ticks = 20
        for _ in range(max(1, settle_ticks)):
            try:
                world.tick() if world.get_settings().synchronous_mode else world.wait_for_tick()
            except Exception:
                break
        _release_ego_after_spawn_settle(ctx.ego)

        # Refresh ego_transform after the tick.
        try:
            ego_transform = ctx.ego.get_transform()
        except Exception:
            pass

        tl_setup = setup_traffic_light or default_setup_traffic_light
        ctx.traffic_light = tl_setup(world, ctx.ego, config)
        result["traffic_light_state"] = get_traffic_light_state(ctx.traffic_light)
        logger.log_event(
            "traffic_light_setup",
            state=result["traffic_light_state"],
            light_id=getattr(ctx.traffic_light, "id", None),
        )

        # Officer — or a null stand-in for scenarios with no human controller
        # (the fallen-person scenario has no officer; the ego must stop on its
        # own). A scenario opts out by having no `officer` block in its config.
        officer_raw = config.get("officer")
        officer_cfg = dict(officer_raw) if officer_raw else {}
        yield_clearance_geometry = None
        if officer_raw and str(expected_action).upper() == "YIELD":
            try:
                ego_half_width = float(ctx.ego.bounding_box.extent.y)
            except Exception:
                ego_half_width = 1.082
            center_clearance = yield_officer_center_clearance_m(ego_half_width)
            required_lateral = YIELD_ROUTE_OFFSET_M + center_clearance
            officer_cfg["lateral_offset"] = max(
                float(officer_cfg.get("lateral_offset", OFFICER_LATERAL_OFFSET)),
                required_lateral,
            )
            yield_clearance_geometry = {
                "ego_half_width_m": ego_half_width,
                "walker_capsule_radius_m": WALKER_CAPSULE_RADIUS_M,
                "gesture_arm_reach_allowance_m": GESTURE_ARM_REACH_ALLOWANCE_M,
                "margin_m": YIELD_CLEARANCE_MARGIN_M,
                "minimum_center_distance_m": center_clearance,
                "yield_route_offset_m": YIELD_ROUTE_OFFSET_M,
                "officer_lateral_offset_m": officer_cfg["lateral_offset"],
            }
        if officer_raw:
            ctx.officer = build_officer(
                world, ego_transform, officer_cfg,
                officer_stopline=officer_stopline,
            )
        else:
            ctx.officer = _NullOfficer(officer_stopline or ego_transform)
            log.info("No officer configured — running with a null officer.")
        result["officer_metadata"] = ctx.officer.get_metadata()
        if yield_clearance_geometry is not None:
            result["officer_metadata"]["yield_clearance_geometry"] = yield_clearance_geometry
        director_facing = {
            "staging": facing_ego_deg(ctx.officer.get_transform(), ego_transform)
            if officer_raw else None,
            "gesture_onset": None,
        }
        result["officer_metadata"]["facing_ego_deg"] = dict(director_facing)
        logger.log_event("officer_spawned", **result["officer_metadata"])
        if officer_raw and result["officer_metadata"].get("actor_id") is None:
            message = f"required officer actor missing for {name}"
            scene_setup_errors.append(message)
            log.warning(message)

        # Per-scenario extra scene actors (crash vehicles, fallen person,
        # construction zone, ambulance, ...). If a scenario requested extra
        # actors and none spawned, the episode is not the intended scenario.
        result["scene_setup"] = {
            "extra_actor_required": setup_extra_actors is not None,
            "extra_actor_count": 0,
            "valid": setup_extra_actors is None,
            "errors": [],
        }
        blocking_staged_xy: dict = {}
        if setup_extra_actors is not None:
            try:
                extra = setup_extra_actors(
                    world, ctx.ego, ego_transform, ctx.officer, config
                )
                blocking = getattr(extra, "blocking_actors", None)
                ctx.extra_actors = [a for a in (extra or []) if a is not None]
                if blocking is not None:
                    managed_ids = {id(a) for a in ctx.extra_actors}
                    ctx.blocking_actors = [
                        a for a in blocking if a is not None and id(a) in managed_ids
                    ]
                    blocking_staged_xy = _capture_actor_xy(ctx.blocking_actors)
                log.info("Spawned %d extra scene actor(s)", len(ctx.extra_actors))
                if not ctx.extra_actors:
                    message = f"required extra scene actors missing for {name}"
                    scene_setup_errors.append(message)
                    log.warning(message)
                logger.log_event(
                    "extra_actors_spawned", count=len(ctx.extra_actors)
                )
            except Exception as e:
                message = f"setup_extra_actors failed: {e}"
                scene_setup_errors.append(message)
                log.warning("setup_extra_actors failed: %s", e)
        result["scene_setup"] = {
            "extra_actor_required": setup_extra_actors is not None,
            "extra_actor_count": len(ctx.extra_actors),
            "valid": not scene_setup_errors,
            "errors": list(scene_setup_errors),
        }
        logger.log_event("scene_setup", **result["scene_setup"])

        # Criteria.
        expected = config.get("expected_behavior") or {}
        expected_action_effective = str(expected.get("action", expected_action)).upper()
        max_reaction_time = float(expected.get("max_reaction_time_sec", 3.0))
        stop_line_location = _resolve_stop_line(ctx.traffic_light)
        compliance = AuthorityComplianceCriterion(
            ego_vehicle=ctx.ego,
            officer=ctx.officer,
            expected_action=expected_action_effective,
            max_reaction_time=max_reaction_time,
            stop_line_location=stop_line_location,
            metadata={"episode_id": result["episode_id"], "scenario": name},
        )
        latency = ReactionLatencyCriterion(
            ego_vehicle=ctx.ego,
            officer=ctx.officer,
            expected_action=expected_action_effective,
        )

        # Collision hookup.
        collision_count = 0
        collision_events: list[dict[str, Any]] = []
        staging_drift_flagged = False
        sim_time = 0.0

        def _on_collision(event: Any) -> None:
            nonlocal collision_count
            count_event = True
            other = getattr(event, "other_actor", None)
            try:
                ego_id = getattr(ctx.ego, "id", None)
                other_id = getattr(other, "id", None)
                count_event = not (ego_id is not None and other_id is not None and ego_id == other_id)
            except Exception:
                count_event = True
            try:
                other_type = str(getattr(other, "type_id", "") or "")
                other_role = str((getattr(other, "attributes", {}) or {}).get("role_name", "") or "")
                if other_type == "static.road" or other_role == "marshal_ego":
                    count_event = False
            except Exception:
                pass
            if count_event:
                collision_count += 1
                _append_collision_identity(collision_events, ctx, event, sim_time)
                try:
                    compliance.register_collision(event)
                except Exception as e:
                    log.debug("compliance.register_collision failed: %s", e)
                try:
                    logger.log_event(
                        "collision",
                        other_actor=getattr(other, "type_id", "?"),
                    )
                except Exception:
                    pass

        ctx.collision_sensor = attach_collision_sensor(world, ctx.ego, _on_collision)

        # Cameras: chase camera (demo view — ego rear + scene ahead) ->
        # frames/, ego dashcam (the VLM benchmark input view) -> frames_ego/.
        cam_cfg = config.get("camera") or {}
        try:
            ctx.camera = attach_chase_camera(
                world, ctx.ego, logger.path("frames"),
                back=float(cam_cfg.get("chase_back", 6.5)),
                height=float(cam_cfg.get("chase_height", 2.8)),
                pitch=float(cam_cfg.get("chase_pitch", -12.0)),
                side=float(cam_cfg.get("chase_side", 0.0)),
                yaw=float(cam_cfg.get("chase_yaw", 0.0)),
            )
        except Exception as e:
            log.warning("Could not attach chase camera: %s", e)
        try:
            ctx.frames_ego_dir = os.path.abspath(logger.path("frames_ego"))
            ctx.ego_camera = attach_ego_camera(
                world, ctx.ego, ctx.frames_ego_dir, ctx
            )
        except Exception as e:
            log.warning("Could not attach ego camera: %s", e)

        logger.log_event(
            "scenario_started",
            scenario=name,
            timeout=timeout,
            fps=fps,
            autopilot_blind_to_officer=True,
        )

        # Main sync loop.
        delta = 1.0 / fps
        terminated = "timeout"
        telemetry_rows: list[dict] = []
        facing_onset_recorded = False
        controller_errors: list[str] = []
        # Desired signal state to hold for the whole run (Green for green_stop).
        tl_state_desired = (config.get("traffic_light") or {}).get("state")
        # Privileged ground truth handed to the controller (Track A oracle uses
        # it directly; Track B/C may ignore it). This IS the episode E-tuple.
        ground_truth = _build_ground_truth(
            config, ctx, ego_transform, expected_action, expected_gesture,
        )
        result["ground_truth"] = ground_truth

        carla = import_carla()
        setup_ground_truth = (
            ground_truth
            if _controller_uses_privileged_ground_truth(controller)
            else _non_privileged_setup_context(ground_truth)
        )
        try:
            route_origin = ctx.ego.get_transform().location
            route_fwd = ctx.ego.get_transform().get_forward_vector()
            route_right = ctx.ego.get_transform().get_right_vector()
        except Exception:
            route_origin = ego_transform.location
            route_fwd = ego_transform.get_forward_vector()
            route_right = ego_transform.get_right_vector()
        # Cache the map once (get_map() per tick is expensive) for R4 lane-change
        # tracking; None-safe so a failure never breaks the episode loop.
        try:
            _carla_map = world.get_map()
        except Exception:
            _carla_map = None
        with SyncModeContext(world, fps=fps) as sync:
            if controller is None:
                # Enable autopilot only AFTER the world is in synchronous mode.
                # Handing a vehicle to the TrafficManager while the world is
                # still async leaves it unregistered, so it never gets throttle.
                ctx.traffic_manager = enable_autopilot(
                    client,
                    ctx.ego,
                    target_speed_kmh=ego_cfg.get("target_speed"),
                    setup_errors=scene_setup_errors,
                )
            else:
                # The world is synchronous; the TrafficManager MUST be made
                # synchronous too or world.tick() deadlocks (classic CARLA
                # gotcha). The baseline path gets this via enable_autopilot;
                # the controller path must set it explicitly.
                try:
                    tm = client.get_trafficmanager()
                    tm.set_synchronous_mode(True)
                    ctx.traffic_manager = tm
                except Exception as e:
                    log.debug("TrafficManager sync setup failed: %s", e)
                try:
                    if hasattr(controller, "set_episode_dir"):
                        controller.set_episode_dir(logger.episode_dir)
                    controller.setup(world, ctx.ego, setup_ground_truth, carla)
                    log.info("Controller '%s' set up (track=%s)",
                             getattr(controller, "name", "?"),
                             getattr(controller, "track", "?"))
                except Exception as e:
                    controller_errors.append(f"setup: {repr(e)}")
                    log.error("controller.setup failed: %s", e)
            sync.tick(timeout=2.0)  # let the TM register the ego / settle

            initial_in_junction = ego_in_intersection(ctx.ego, world)
            entered_junction_after_start = False
            detour_anchor_forward_m = None

            # Per-scenario hook to drive extra vehicles (ambulance, the
            # adjacent-lane car, ...) on the now-synced TrafficManager.
            if setup_after_autopilot is not None and ctx.traffic_manager is not None:
                try:
                    setup_after_autopilot(ctx, ctx.traffic_manager, config)
                except Exception as e:
                    scene_setup_errors.append(f"setup_after_autopilot failed: {e}")
                    log.warning("setup_after_autopilot failed: %s", e)
            steps = int(math.ceil(timeout * fps)) + 1
            for _ in range(steps):
                sync.tick(timeout=2.0)
                sim_time += delta

                # Hold the signal premise: re-pin every forward light each
                # tick so the light the ego faces (and the dashcam shows) is
                # the configured state, not whatever CARLA cycled to.
                if tl_state_desired:
                    _near_tl = _repin_forward_lights(
                        world, ctx.ego, tl_state_desired, officer=ctx.officer
                    )
                    if _near_tl is not None:
                        ctx.traffic_light = _near_tl

                # Officer + criteria ticks.
                try:
                    ctx.officer.tick(sim_time)
                except Exception as e:
                    log.debug("officer.tick failed: %s", e)
                # Per-scenario per-tick extra-actor update (e.g. keep the
                # ambulance locked behind the ego).
                if tick_extra_actors is not None:
                    try:
                        tick_extra_actors(ctx, sim_time)
                    except Exception as e:
                        log.debug("tick_extra_actors failed: %s", e)

                # Controller (agent under test) drives the ego when present.
                control_finite_this_tick = True
                if controller is not None:
                    control_finite_this_tick = False
                    try:
                        obs = _build_observation(ctx, world, sim_time)
                        control = controller.step(obs, delta)
                        if _finite_vehicle_control(control):
                            ctx.ego.apply_control(control)
                            control_finite_this_tick = True
                        else:
                            msg = f"step@{sim_time:.2f}: non-finite or missing control"
                            if len(controller_errors) < 20:
                                controller_errors.append(msg)
                            logger.log_event("controller_invalid_control", sim_time=sim_time)
                    except Exception as e:
                        if len(controller_errors) < 20:
                            controller_errors.append(f"step@{sim_time:.2f}: {repr(e)}")
                        logger.log_event("controller_step_error", sim_time=sim_time, error=repr(e))
                        log.debug("controller.step failed: %s", e)

                try:
                    compliance.tick(sim_time)
                except Exception as e:
                    log.debug("compliance.tick failed: %s", e)
                try:
                    latency.tick(sim_time)
                except Exception as e:
                    log.debug("latency.tick failed: %s", e)

                # Per-step metric row.
                try:
                    _ctrl = ctx.ego.get_control()
                    _thr, _brk = float(_ctrl.throttle), float(_ctrl.brake)
                    _autopilot = bool(getattr(ctx.ego, "is_autopilot_enabled", lambda: True)())
                except Exception:
                    _thr, _brk, _autopilot = -1.0, -1.0, False
                ego_loc = _location_from_transform_or_actor(ctx.ego)
                officer_loc = None
                try:
                    officer_loc = _location_from_transform_or_actor(ctx.officer.get_transform() if ctx.officer else None)
                except Exception:
                    officer_loc = None
                stop_ref = stop_line_location or officer_loc or ego_loc
                distance_to_officer = _distance_between_locations(ego_loc, officer_loc)
                distance_to_stopline = _distance_between_locations(ego_loc, stop_ref)
                ego_forward_m, ego_lateral_m = _project_from_origin(
                    ego_loc, route_origin, route_fwd, route_right
                )
                hazard_distance = None
                hazard_forward = None
                for actor in list(ctx.extra_actors):
                    hloc = _location_from_transform_or_actor(actor)
                    if hloc is None or ego_loc is None:
                        continue
                    dist = _distance_between_locations(ego_loc, hloc)
                    h_forward, _h_lat = _project_from_origin(
                        hloc, route_origin, route_fwd, route_right
                    )
                    if math.isfinite(dist) and (hazard_distance is None or dist < hazard_distance):
                        hazard_distance = dist
                        hazard_forward = h_forward

                # Staging integrity: a blocking actor that moves before any
                # ego contact voids the scene premise (spawn depenetration
                # launch, runaway prop, ...). Surface it as a setup error so
                # the episode scores INVALID instead of a lucky PASS against
                # a vanished obstacle.
                if (blocking_staged_xy and not staging_drift_flagged
                        and collision_count == 0):
                    drifted = _staging_drift_violations(
                        blocking_staged_xy, _capture_actor_xy(ctx.blocking_actors)
                    )
                    if drifted:
                        staging_drift_flagged = True
                        drift_key, drift_m = drifted[0]
                        message = (
                            f"staging integrity: blocking actor {drift_key} "
                            f"moved {drift_m:.1f} m with no ego contact"
                        )
                        scene_setup_errors.append(message)
                        logger.log_event(
                            "staging_integrity_violation",
                            sim_time=sim_time,
                            actor_id=drift_key,
                            drift_m=round(drift_m, 2),
                        )
                        log.warning(message)
                officer_meta_now = {}
                try:
                    officer_meta_now = ctx.officer.get_metadata() if ctx.officer else {}
                except Exception:
                    officer_meta_now = {}
                onset_now = float(officer_meta_now.get("onset_time", 0.0) or 0.0)
                duration_raw = officer_meta_now.get("duration")
                try:
                    duration_now = float(duration_raw) if duration_raw is not None else None
                except Exception:
                    duration_now = None
                officer_active = sim_time >= onset_now and (
                    duration_now is None or sim_time <= onset_now + duration_now
                )
                officer_facing_now = None
                if officer_raw:
                    try:
                        officer_facing_now = facing_ego_deg(
                            ctx.officer.get_transform(), ctx.ego.get_transform()
                        )
                    except Exception:
                        officer_facing_now = None
                    if not facing_onset_recorded and sim_time >= onset_now:
                        director_facing["gesture_onset"] = officer_facing_now
                        facing_onset_recorded = True
                speed_now = ego_speed_kmh(ctx.ego)
                in_junction_now = ego_in_intersection(ctx.ego, world)
                if not initial_in_junction and in_junction_now:
                    entered_junction_after_start = True
                # R4 (planning): ego lane/road id for lane-change counting.
                ego_lane_id = None
                ego_road_id = None
                if _carla_map is not None and ego_loc is not None:
                    try:
                        _wp = _carla_map.get_waypoint(ego_loc, project_to_road=True)
                        if _wp is not None:
                            ego_lane_id = int(_wp.lane_id)
                            ego_road_id = int(_wp.road_id)
                    except Exception:
                        ego_lane_id = None
                        ego_road_id = None
                # R8 (interaction): distance to the nearest pedestrian that is NOT
                # the directing officer (the officer is authority, not a VRU). Used
                # for pedestrian TTC / yield. None when no such walker is present.
                distance_to_pedestrian = None
                try:
                    for _w in world.get_actors().filter("walker.*"):
                        _wl = _w.get_location()
                        if officer_loc is not None and _distance_between_locations(_wl, officer_loc) < 0.6:
                            continue
                        _d = _distance_between_locations(ego_loc, _wl)
                        if math.isfinite(_d) and (distance_to_pedestrian is None or _d < distance_to_pedestrian):
                            distance_to_pedestrian = _d
                except Exception:
                    distance_to_pedestrian = None
                telemetry_row = {
                    "sim_time": round(sim_time, 4),
                    "ego_speed_kmh": speed_now,
                    "ego_x": getattr(ego_loc, "x", float("nan")),
                    "ego_y": getattr(ego_loc, "y", float("nan")),
                    "in_junction": in_junction_now,
                    "distance_to_officer_m": distance_to_officer,
                    "distance_to_stopline_m": distance_to_stopline,
                    "distance_to_hazard_m": hazard_distance,
                    "ego_forward_m": ego_forward_m,
                    "ego_lateral_m": ego_lateral_m,
                    "hazard_forward_m": hazard_forward,
                    "ego_lane_id": ego_lane_id,
                    "ego_road_id": ego_road_id,
                    "distance_to_pedestrian_m": distance_to_pedestrian,
                    "collision_count": collision_count,
                    "officer_gesture_id": str(officer_meta_now.get("gesture_id", "UNKNOWN")),
                    "officer_onset_time": onset_now,
                    "officer_duration_sec": duration_now,
                    "officer_active": officer_active,
                    "officer_facing_ego_deg": officer_facing_now,
                    "control_finite": control_finite_this_tick,
                }
                telemetry_rows.append(telemetry_row)
                logger.log_metric_row(
                    t=sim_time,
                    speed_kmh=speed_now,
                    throttle=_thr,
                    brake=_brk,
                    in_junction=in_junction_now,
                    tl_state=get_traffic_light_state(ctx.traffic_light),
                    ego_x=telemetry_row["ego_x"],
                    ego_y=telemetry_row["ego_y"],
                    distance_to_officer_m=distance_to_officer,
                    distance_to_stopline_m=distance_to_stopline,
                    distance_to_hazard_m=hazard_distance,
                    ego_forward_m=ego_forward_m,
                    ego_lateral_m=ego_lateral_m,
                    hazard_forward_m=hazard_forward,
                    collision_count=collision_count,
                    officer_gesture_id=telemetry_row["officer_gesture_id"],
                    officer_onset_time=onset_now,
                    officer_duration_sec=duration_now,
                    officer_active=officer_active,
                    officer_facing_ego_deg=officer_facing_now,
                    control_finite=control_finite_this_tick,
                )

                # Termination: ego stopped within intersection / conflict zone
                # *after* gesture onset, for STOP scenarios.
                onset = float(officer_cfg.get("onset_time", 0.0))
                if expected_action_effective == "STOP":
                    if _stop_completion_reached(
                        sim_time,
                        onset,
                        speed_now,
                        in_junction_now,
                        entered_junction_after_start,
                        stopline_distance_m=distance_to_stopline,
                        reaction_deadline_s=max_reaction_time,
                    ):
                        terminated = "ego_stopped_in_conflict_zone"
                        break
                elif expected_action_effective == "PROCEED":
                    if in_junction_now and sim_time > onset + 1.0:
                        terminated = "ego_entered_intersection"
                        # Keep running briefly to record post-entry state.
                        if sim_time > onset + 4.0:
                            break
                elif expected_action_effective == "DETOUR":
                    if detour_anchor_forward_m is None:
                        try:
                            _hf = float(hazard_forward)
                            if math.isfinite(_hf) and _hf > 0.0:
                                detour_anchor_forward_m = _hf
                        except (TypeError, ValueError):
                            pass
                    if _detour_completion_reached(
                        detour_anchor_forward_m,
                        ego_forward_m,
                        ego_lateral_m,
                        sim_time,
                        onset,
                    ):
                        terminated = "detour_completed"
                        break

        result["terminated_reason"] = terminated
        logger.log_event("scenario_finished", reason=terminated, sim_time=sim_time)
        result["setup_errors"] = list(scene_setup_errors)
        if scene_setup_errors:
            result.setdefault("scene_setup", {})
            result["scene_setup"]["valid"] = False
            result["scene_setup"]["errors"] = list(scene_setup_errors)
            logger.log_event(
                "scene_setup_final",
                valid=False,
                errors=list(scene_setup_errors),
            )

        # Finalise criteria.
        try:
            result["compliance"] = compliance.to_json()
        except Exception as e:
            log.warning("compliance.to_json failed: %s", e)
            result["compliance"] = {"error": str(e)}
        try:
            result["latency"] = latency.to_json()
        except Exception as e:
            log.warning("latency.to_json failed: %s", e)
            result["latency"] = {"error": str(e)}

        try:
            strict_score = score_episode_from_telemetry(
                result,
                telemetry_rows,
                scenario=name,
                expected_action=expected_action_effective,
                max_reaction_time=max_reaction_time,
                controller_errors=controller_errors,
                setup_errors=scene_setup_errors,
            )
            result["officer_metadata"] = ctx.officer.get_metadata()
            result["officer_metadata"]["facing_ego_deg"] = dict(director_facing)
            artifact_paths = write_strict_artifacts(
                logger.episode_dir,
                telemetry_rows,
                strict_score,
                metadata={
                    "condition": result.get("condition"),
                    "collisions": collision_events,
                    "officer": {
                        "facing_ego_deg": dict(director_facing),
                    } if officer_raw else None,
                },
            )
            strict_score["artifacts"] = artifact_paths
            result["strict_scoring"] = strict_score
            logger.log_event("strict_scoring", **strict_score)
        except Exception as e:
            log.warning("strict telemetry scoring failed: %s", e)
            result["strict_scoring"] = {
                "passed": False,
                "invalid": True,
                "verdict": "INVALID",
                "reason": f"strict scorer raised {repr(e)}",
            }

        # MARSHAL contextual metric suite (AOC/FOA/TAA/SBO/CRI/RTL).
        try:
            from marshal_bench.criteria.marshal_metrics import (
                compute_episode_metrics)
            target_pred = None
            if controller is not None and hasattr(controller, "report_target"):
                try:
                    target_pred = controller.report_target()
                except Exception:
                    target_pred = None
            em = compute_episode_metrics(result, scenario=name,
                                         target_pred=target_pred)
            result["marshal_metrics"] = em.as_dict()
            logger.log_event("marshal_metrics", **em.as_dict())
        except Exception as e:
            log.warning("compute_episode_metrics failed: %s", e)

        return result

    finally:
        if controller is not None:
            try:
                controller.teardown()
            except Exception as e:
                log.debug("controller.teardown failed: %s", e)
        teardown(ctx)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def teardown(ctx: ScenarioContext) -> None:
    """Best-effort destruction of every actor allocated by :func:`run_scenario`."""
    # Collision sensor first so it stops firing.
    if ctx.collision_sensor is not None:
        try:
            ctx.collision_sensor.stop()
        except Exception:
            pass
        try:
            ctx.collision_sensor.destroy()
        except Exception as e:
            log.debug("collision_sensor.destroy failed: %s", e)

    # Cameras (stop streaming before destroying; ego cam before its parent ego).
    for _cam, _label in ((ctx.ego_camera, "ego_camera"), (ctx.camera, "camera")):
        if _cam is None:
            continue
        try:
            _cam.stop()
        except Exception:
            pass
        try:
            _cam.destroy()
        except Exception as e:
            log.debug("%s.destroy failed: %s", _label, e)

    # Officer encapsulates its own walker + props.
    if ctx.officer is not None:
        try:
            ctx.officer.destroy()
        except Exception as e:
            log.debug("officer.destroy failed: %s", e)

    # Per-scenario extra scene actors.
    for _actor in list(ctx.extra_actors):
        try:
            _actor.destroy()
        except Exception as e:
            log.debug("extra actor destroy failed: %s", e)
    ctx.extra_actors.clear()

    # Release the traffic light freeze so future runs aren't stuck.
    if ctx.traffic_light is not None:
        try:
            release_traffic_light(ctx.traffic_light)
        except Exception as e:
            log.debug("release_traffic_light failed: %s", e)

    # Disable autopilot and destroy ego.
    if ctx.ego is not None:
        try:
            ctx.ego.set_autopilot(False)
        except Exception:
            pass
        try:
            ctx.ego.destroy()
        except Exception as e:
            log.debug("ego.destroy failed: %s", e)

    # Drop any stragglers we tracked manually.
    for actor_id in list(ctx.spawned_actor_ids):
        if ctx.world is None:
            break
        try:
            actor = ctx.world.get_actor(actor_id)
            if actor is not None:
                actor.destroy()
        except Exception:
            pass

    if ctx.traffic_manager is not None:
        try:
            ctx.traffic_manager.set_synchronous_mode(False)
        except Exception:
            pass

    # Restore the exact pre-condition object after all episode actors are gone,
    # preventing weather leakage between sweep cells.
    if ctx.weather_applied and ctx.world is not None and ctx.original_weather is not None:
        try:
            ctx.world.set_weather(ctx.original_weather)
        except Exception as e:
            log.debug("weather restoration failed: %s", e)


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------
def _resolve_stop_line(light: Any) -> Optional[Any]:
    """Best-effort: return the carla.Location of ``light``'s primary stop line."""
    if light is None:
        return None
    try:
        wps = light.get_stop_waypoints()
        if wps:
            return wps[0].transform.location
    except Exception:
        pass
    try:
        return light.get_transform().location
    except Exception:
        return None


def _fmt_loc(loc: Any) -> str:
    try:
        return f"({loc.x:.1f}, {loc.y:.1f}, {loc.z:.1f})"
    except Exception:
        return repr(loc)


__all__ = [
    "DEFAULT_FPS",
    "DEFAULT_TIMEOUT_SEC",
    "ScenarioContext",
    "apply_weather",
    "attach_collision_sensor",
    "build_officer",
    "enable_autopilot",
    "ensure_town",
    "ego_in_intersection",
    "ego_speed_kmh",
    "officer_transform_in_front_of",
    "run_scenario",
    "should_apply_condition",
    "spawn_ego",
    "teardown",
]
