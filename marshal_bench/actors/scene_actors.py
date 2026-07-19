"""Reusable scene-actor spawners for MARSHAL scenarios 4-9.

Beyond the ego + officer, the later scenarios need extra props/actors:
  * a multi-car crash pileup blocking the lane          (crash_detour)
  * a fallen person on the crosswalk                    (fallen_person)
  * a vehicle in the lane next to the ego               (adjacent_lane)
  * a construction zone (cones + barricade)             (flagger_control)
  * an approaching ambulance                            (ambulance_yield)

Each helper spawns its actors and returns a flat ``list`` so the scenario's
teardown can destroy them. All CARLA access is defensive: a failure logs a
warning and returns whatever managed to spawn.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from marshal_bench.utils.carla_api_compat import import_carla

log = logging.getLogger("marshal_bench.actors.scene_actors")


class ManagedSceneActors(list):
    """Scene actors with an optional lane-blocking subset."""

    def __init__(self, actors=(), *, blocking_actors=()) -> None:
        super().__init__(actors)
        self.blocking_actors = list(blocking_actors)


def route_blocking_actors(actors: list) -> ManagedSceneActors:
    """Mark already-spawned, route-confined actors as the DETOUR blockage."""
    return ManagedSceneActors(actors, blocking_actors=actors)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def route_waypoint(world: Any, ego_transform: Any, distance: float) -> Any:
    """Return the waypoint ``distance`` m along the ego's lane (or None)."""
    try:
        cmap = world.get_map()
        wp = cmap.get_waypoint(ego_transform.location, project_to_road=True)
        if wp is None:
            return None
        nxt = wp.next(max(1.0, float(distance)))
        return nxt[0] if nxt else None
    except Exception as e:  # noqa: BLE001
        log.debug("route_waypoint failed: %s", e)
        return None


def _freeze(actor: Any) -> Any:
    """Disable physics so a placed actor holds its exact pose."""
    try:
        actor.set_simulate_physics(False)
    except Exception:  # noqa: BLE001
        pass
    return actor


def _four_wheelers(bp_lib: Any) -> list:
    out = []
    for b in bp_lib.filter("vehicle.*"):
        try:
            if int(b.get_attribute("number_of_wheels")) == 4:
                out.append(b)
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------------------
# #4  crash pileup
# ---------------------------------------------------------------------------
def spawn_crash_pileup(
    world: Any, ego_transform: Any, distance: float = 32.0, n: int = 4,
    seed: int = 0,
) -> list:
    """Spawn ``n`` vehicles forming a pileup that blocks the ego's lane ahead."""
    carla = import_carla()
    out: list = []
    wp = route_waypoint(world, ego_transform, distance)
    if wp is None:
        log.warning("crash pileup: no route waypoint at %.0f m", distance)
        return out
    cars = _four_wheelers(world.get_blueprint_library())
    if not cars:
        return out
    rng = random.Random(seed)
    twf = wp.transform
    fwd = twf.get_forward_vector()
    right = twf.get_right_vector()
    # (forward, lateral, yaw-offset) — staggered ~6 m apart, kept WITHIN the
    # ego's own lane (|lateral| <= ~0.6 m, mild yaw) so the pileup fully blocks
    # THIS lane but does NOT spill into the adjacent lane — leaving a real LEFT
    # detour open where the road has one. (Earlier wide jack-knife spilled into
    # the neighbour lane and forced the oracle to stop short.)
    layout = [(0.0, 0.0, 12.0), (6.0, 0.4, -16.0), (12.0, -0.5, 18.0),
              (18.0, 0.3, -12.0), (24.0, -0.3, 14.0)]
    for df, dl, dyaw in layout[:max(1, min(n, len(layout)))]:
        rot = carla.Rotation(yaw=twf.rotation.yaw + dyaw)
        spawn_candidates = (
            (df, dl, 0.30),
            (df, dl, 0.60),
            (df, dl, 1.00),
            (df - 1.0, dl, 0.60),
            (df + 1.0, dl, 0.60),
            (df, dl - 0.35, 0.60),
            (df, dl + 0.35, 0.60),
            (df - 1.0, dl - 0.35, 1.00),
            (df + 1.0, dl + 0.35, 1.00),
        )
        car_order = list(cars)
        rng.shuffle(car_order)
        actor = None
        for cdf, cdl, dz in spawn_candidates:
            loc = carla.Location(
                x=twf.location.x + fwd.x * cdf + right.x * cdl,
                y=twf.location.y + fwd.y * cdf + right.y * cdl,
                z=twf.location.z + dz,
            )
            spawn_tf = carla.Transform(loc, rot)
            for bp in car_order[: min(12, len(car_order))]:
                try:
                    actor = world.try_spawn_actor(bp, spawn_tf)
                except Exception:
                    actor = None
                if actor is not None:
                    _freeze(actor)
                    break
            if actor is not None:
                break
        if actor is not None:
            out.append(actor)
    log.info("crash pileup: spawned %d/%d vehicles at %.0f m", len(out), n, distance)
    return route_blocking_actors(out)


# ---------------------------------------------------------------------------
# #5  fallen person
# ---------------------------------------------------------------------------
def spawn_fallen_person(
    world: Any, ego_transform: Any, distance: float = 30.0, lateral: float = 0.0,
) -> list:
    """Spawn a walker lying on the ground in the ego's lane ahead."""
    carla = import_carla()
    wp = route_waypoint(world, ego_transform, distance)
    if wp is None:
        log.warning("fallen person: no route waypoint at %.0f m", distance)
        return []
    walkers = list(world.get_blueprint_library().filter("walker.pedestrian.*"))
    if not walkers:
        return []
    twf = wp.transform
    right = twf.get_right_vector()

    def _bp(i):
        bp = walkers[i % len(walkers)]
        if bp.has_attribute("is_invincible"):
            try:
                bp.set_attribute("is_invincible", "true")
            except Exception:  # noqa: BLE001
                pass
        return bp

    # Walkers spawn upright; a spawn point overlapping the road surface often
    # fails (returns None). Try several heights / blueprints / tiny lateral
    # nudges, spawning UPRIGHT first, then lay it down once it exists.
    actor = None
    for k, (dz, dl) in enumerate(
            [(1.0, 0.0), (1.2, 0.0), (0.9, 0.4), (1.0, -0.4), (1.5, 0.0)]):
        loc = carla.Location(
            x=twf.location.x + right.x * (lateral + dl),
            y=twf.location.y + right.y * (lateral + dl),
            z=twf.location.z + dz,
        )
        actor = world.try_spawn_actor(_bp(k), carla.Transform(loc, twf.rotation))
        if actor is not None:
            break
    if actor is None:
        log.warning("fallen person: walker spawn failed after retries at %.0f m",
                    distance)
        return []
    _freeze(actor)
    # Lay the walker on its side (roll ~90 deg) — it now reads as "person down".
    try:
        cur = actor.get_transform().location
        actor.set_transform(carla.Transform(
            carla.Location(cur.x, cur.y, twf.location.z + 0.25),
            carla.Rotation(pitch=0.0, yaw=twf.rotation.yaw, roll=90.0),
        ))
    except Exception as e:  # noqa: BLE001
        log.debug("fallen person: set_transform(roll) failed: %s", e)
    log.info("fallen person: spawned at %.0f m ahead", distance)
    return [actor]


# ---------------------------------------------------------------------------
# #7  adjacent-lane vehicle
# ---------------------------------------------------------------------------
def spawn_adjacent_vehicle(
    world: Any, ego_transform: Any, distance: float = 26.0, side: str = "right",
) -> list:
    """Spawn a stopped vehicle in the lane beside the ego (the officer's target)."""
    carla = import_carla()
    wp = route_waypoint(world, ego_transform, distance)
    if wp is None:
        return []
    try:
        lane_wp = wp.get_right_lane() if side == "right" else wp.get_left_lane()
    except Exception:  # noqa: BLE001
        lane_wp = None
    twf = (lane_wp or wp).transform
    if lane_wp is None:
        # No real neighbouring lane — offset laterally as a fallback.
        right = twf.get_right_vector()
        s = 1.0 if side == "right" else -1.0
        loc = carla.Location(
            x=twf.location.x + right.x * 3.5 * s,
            y=twf.location.y + right.y * 3.5 * s,
            z=twf.location.z + 0.3,
        )
        twf = carla.Transform(loc, twf.rotation)
    cars = _four_wheelers(world.get_blueprint_library())
    if not cars:
        return []
    spawn_tf = carla.Transform(
        carla.Location(twf.location.x, twf.location.y, twf.location.z + 0.3),
        twf.rotation,
    )
    actor = world.try_spawn_actor(cars[0], spawn_tf)
    if actor is None:
        log.warning("adjacent vehicle: spawn returned None")
        return []
    # Physics OFF at spawn like every other staged stationary vehicle: the
    # window before the scenario's own late freeze is the same
    # depenetration-launch window that ejected the esb firetruck.
    _freeze(actor)
    log.info("adjacent vehicle: spawned in %s lane at %.0f m", side, distance)
    return [actor]


# ---------------------------------------------------------------------------
# #8  construction zone (cones + barricade)
# ---------------------------------------------------------------------------
def spawn_construction_zone(
    world: Any, ego_transform: Any, block_distance: float = 30.0, n_cones: int = 6,
) -> list:
    """Fully close the ego's lane at ``block_distance``: a row of barricades
    across the lane, a works vehicle right behind them (so the TrafficManager
    actually brakes — it ignores static props), and cones funnelling up to it.
    """
    carla = import_carla()
    out: list = []
    bp_lib = world.get_blueprint_library()
    cmap = world.get_map()
    base_wp = cmap.get_waypoint(ego_transform.location, project_to_road=True)
    if base_wp is None:
        log.warning("construction zone: no ego waypoint")
        return out

    blk = base_wp.next(block_distance)
    if not blk:
        log.warning("construction zone: no road at %.0f m", block_distance)
        return out
    btf = blk[0].transform
    bright = btf.get_right_vector()

    # Barricades spanning the lane width (turned across the direction of travel).
    bar_bps = list(bp_lib.filter("static.prop.barricade")) or \
        list(bp_lib.filter("static.prop.streetbarrier"))
    if bar_bps:
        for lat in (-1.3, 0.0, 1.3):
            loc = carla.Location(btf.location.x + bright.x * lat,
                                 btf.location.y + bright.y * lat,
                                 btf.location.z + 0.1)
            a = world.try_spawn_actor(
                bar_bps[0],
                carla.Transform(loc, carla.Rotation(yaw=btf.rotation.yaw + 90.0)))
            if a is not None:
                _freeze(a)
                out.append(a)

    # A works vehicle just behind the barricades — the real obstacle the
    # TrafficManager brakes for (it does not brake for cones/barricades).
    veh = [b for b in bp_lib.filter("vehicle.*")
           if "truck" in b.id or "carlacola" in b.id or "firetruck" in b.id]
    if not veh:
        veh = _four_wheelers(bp_lib)
    tk = base_wp.next(block_distance + 5.0)
    if veh and tk:
        ttf = tk[0].transform
        a = world.try_spawn_actor(veh[0], carla.Transform(
            carla.Location(ttf.location.x, ttf.location.y, ttf.location.z + 0.3),
            ttf.rotation))
        if a is not None:
            _freeze(a)
            out.append(a)

    # Cones funnelling up to the closure from both lane edges.
    cone_bps = list(bp_lib.filter("static.prop.*cone*"))
    if cone_bps:
        for i in range(n_cones):
            frac = i / max(1, n_cones - 1)
            d = block_distance - 10.0 + frac * 10.0
            nxt = base_wp.next(max(1.0, d))
            if not nxt:
                continue
            twf = nxt[0].transform
            r = twf.get_right_vector()
            lat = (1.7 - frac * 1.0) * (1.0 if i % 2 else -1.0)
            loc = carla.Location(twf.location.x + r.x * lat,
                                 twf.location.y + r.y * lat,
                                 twf.location.z + 0.1)
            a = world.try_spawn_actor(cone_bps[0], carla.Transform(loc, twf.rotation))
            if a is not None:
                _freeze(a)
                out.append(a)
    log.info("construction zone: spawned %d items, lane closed at %.0f m",
             len(out), block_distance)
    return route_blocking_actors(out)


# ---------------------------------------------------------------------------
# #9  ambulance
# ---------------------------------------------------------------------------
def spawn_ambulance(
    world: Any, ego_transform: Any, behind: float = 16.0,
) -> list:
    """Spawn an ambulance ``behind`` m behind the ego in the SAME lane.

    The after-autopilot hook then drives it fast (it runs red lights, like a
    real emergency vehicle) so it catches up and bears down on the ego — which,
    as the officer-blind baseline, never pulls aside to let it through.
    """
    carla = import_carla()
    bp_lib = world.get_blueprint_library()
    amb = list(bp_lib.filter("vehicle.*ambulance*"))
    if not amb:
        amb = list(bp_lib.filter("vehicle.*firetruck*")) or _four_wheelers(bp_lib)
    if not amb:
        return []
    cmap = world.get_map()
    wp = cmap.get_waypoint(ego_transform.location, project_to_road=True)
    if wp is None:
        return []
    prev = wp.previous(max(2.0, behind))
    bwp = prev[0] if prev else wp
    twf = bwp.transform
    spawn_tf = carla.Transform(
        carla.Location(twf.location.x, twf.location.y, twf.location.z + 0.3),
        twf.rotation,
    )
    actor = world.try_spawn_actor(amb[0], spawn_tf)
    if actor is None:
        log.warning("ambulance: spawn returned None")
        return []
    # Physics off — the scenario locks it a fixed gap behind the ego each tick.
    try:
        actor.set_simulate_physics(False)
    except Exception:  # noqa: BLE001
        pass
    log.info("ambulance: spawned %.0f m behind the ego, same lane", behind)
    return [actor]


# ---------------------------------------------------------------------------
# #10  occluder — a large vehicle that partially hides the officer from ego
# ---------------------------------------------------------------------------
def spawn_occluder(
    world: Any, ego_transform: Any, distance: float = 18.0, lateral: float = 3.2,
) -> list:
    """Park a large vehicle (truck/van) between the ego and the officer so the
    officer is *partially occluded* — the agent must still infer the STOP."""
    carla = import_carla()
    wp = route_waypoint(world, ego_transform, distance)
    if wp is None:
        log.warning("occluder: no route waypoint at %.0f m", distance)
        return []
    bp_lib = world.get_blueprint_library()
    big = (list(bp_lib.filter("vehicle.*truck*"))
           + list(bp_lib.filter("vehicle.*carlamotors*"))
           + list(bp_lib.filter("vehicle.*ambulance*"))
           + list(bp_lib.filter("vehicle.*van*")))
    if not big:
        big = _four_wheelers(bp_lib)
    if not big:
        return []
    twf = wp.transform
    right = twf.get_right_vector()
    candidates = []
    for d_offset in (0.0, -3.0, 3.0, -6.0, 6.0):
        test_wp = route_waypoint(world, ego_transform, max(4.0, distance + d_offset))
        if test_wp is None:
            continue
        test_tf = test_wp.transform
        test_right = test_tf.get_right_vector()
        for lat in (lateral, max(2.0, lateral - 0.8), lateral + 0.8, -lateral):
            for dz in (0.30, 0.60, 1.00):
                candidates.append((test_tf, test_right, lat, dz))

    actor = None
    used_lat = lateral
    used_dist = distance
    for cand_tf, cand_right, lat, dz in candidates:
        loc = carla.Location(
            x=cand_tf.location.x + cand_right.x * lat,
            y=cand_tf.location.y + cand_right.y * lat,
            z=cand_tf.location.z + dz,
        )
        spawn_tf = carla.Transform(loc, cand_tf.rotation)
        for bp in big[: min(len(big), 10)]:
            actor = world.try_spawn_actor(bp, spawn_tf)
            if actor is not None:
                used_lat = lat
                try:
                    base_wp = route_waypoint(world, ego_transform, 0.0)
                    if base_wp is not None:
                        dx = cand_tf.location.x - base_wp.transform.location.x
                        dy = cand_tf.location.y - base_wp.transform.location.y
                        fwd = base_wp.transform.get_forward_vector()
                        used_dist = dx * fwd.x + dy * fwd.y
                except Exception:
                    used_dist = distance
                break
        if actor is not None:
            break
    if actor is None:
        log.warning("occluder: spawn returned None after retries")
        return []
    _freeze(actor)
    log.info("occluder: parked %.0f m ahead, %.1f m to the side", used_dist, used_lat)
    return [actor]


# ---------------------------------------------------------------------------
# #11  second authority — a flagger standing near the officer (conflicting cmd)
# ---------------------------------------------------------------------------
def spawn_flagger(
    world: Any, ego_transform: Any, distance: float = 26.0, lateral: float = -2.4,
) -> list:
    """Spawn a construction-flagger walker offset from the officer. Its
    *conflicting* command is ground-truth metadata; the police outranks it.

    Returns the walker actor (held still). The scenario records the conflict;
    fine-grained flagger gesture animation is optional."""
    carla = import_carla()
    wp = route_waypoint(world, ego_transform, distance)
    if wp is None:
        return []
    walkers = list(world.get_blueprint_library().filter("walker.pedestrian.*"))
    if not walkers:
        return []
    twf = wp.transform
    right = twf.get_right_vector()
    loc = carla.Location(
        x=twf.location.x + right.x * lateral,
        y=twf.location.y + right.y * lateral,
        z=twf.location.z + 0.60,
    )
    from marshal_bench.scenarios._common import yaw_toward_location

    rot = carla.Rotation(yaw=yaw_toward_location(loc, ego_transform.location))
    bp = walkers[-1]  # a different blueprint from the police 0030
    actor = world.try_spawn_actor(bp, carla.Transform(loc, rot))
    if actor is None:
        return []
    _freeze(actor)
    log.info("flagger (second authority): spawned %.0f m ahead", distance)
    return [actor]


# ---------------------------------------------------------------------------
# #13  crossing pedestrian — walks across the ego's lane (rule-hierarchy safety)
# ---------------------------------------------------------------------------
def spawn_crossing_pedestrian(
    world: Any, ego_transform: Any, distance: float = 16.0, from_side: str = "right",
) -> list:
    """Spawn a pedestrian stepping into the ego's lane ahead. Even on an
    authorized PROCEED, a correct agent must yield to the crossing person."""
    carla = import_carla()
    wp = route_waypoint(world, ego_transform, distance)
    if wp is None:
        return []
    walkers = list(world.get_blueprint_library().filter("walker.pedestrian.*"))
    if not walkers:
        return []
    twf = wp.transform
    right = twf.get_right_vector()
    sign = 1.0 if from_side == "right" else -1.0
    loc = carla.Location(
        x=twf.location.x + right.x * 3.0 * sign,
        y=twf.location.y + right.y * 3.0 * sign,
        z=twf.location.z + 1.0,
    )
    # face across the lane (toward the opposite side)
    rot = carla.Rotation(yaw=twf.rotation.yaw - 90.0 * sign)
    bp = walkers[0]
    if bp.has_attribute("is_invincible"):
        try:
            bp.set_attribute("is_invincible", "false")
        except Exception:  # noqa: BLE001
            pass
    actor = world.try_spawn_actor(bp, carla.Transform(loc, rot))
    if actor is None:
        return []
    # Give it a slow walk velocity across the lane (toward the far side).
    try:
        direction = carla.Vector3D(x=-right.x * sign, y=-right.y * sign, z=0.0)
        actor.apply_control(carla.WalkerControl(
            direction=direction, speed=1.2, jump=False))
    except Exception as e:  # noqa: BLE001
        log.debug("crossing pedestrian: walker control failed: %s", e)
    log.info("crossing pedestrian: spawned %.0f m ahead from %s", distance, from_side)
    return [actor]
