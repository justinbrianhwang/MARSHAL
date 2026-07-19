"""Staged-scene integrity: blocking actors must stay where they were staged.

Regression for the Axis-B HardRainNoon emergency_scene_blocking failure: the
staged firetruck spawned physics-ON without a freeze, a depenetration impulse
launched it airborne within ~0.5 s of scenario start, and its chaotic landing
spot decided the episode outcome (clear path = lucky PASS, detour lane =
collision FAIL). Two structural guarantees fix this:

  1. every staged stationary vehicle is frozen (physics off) at spawn, and
  2. run_scenario flags any pre-contact blocking-actor drift as a setup error
     so the episode scores INVALID instead of a lucky PASS.
"""
import math
from types import SimpleNamespace

from marshal_bench.actors import scene_actors
from marshal_bench.scenarios import marshal_emergency_scene_blocking_demo as esb
from marshal_bench.scenarios._common import (
    STAGING_DRIFT_LIMIT_M,
    _capture_actor_xy,
    _staging_drift_violations,
)


# ---------------------------------------------------------------------------
# pure drift helpers
# ---------------------------------------------------------------------------
def test_no_drift_yields_no_violations():
    staged = {1: (10.0, 5.0), 2: (20.0, -3.0)}
    assert _staging_drift_violations(staged, dict(staged)) == []


def test_drift_beyond_limit_is_flagged_with_distance():
    staged = {7: (0.0, 0.0)}
    live = {7: (18.0, 24.0)}  # the launched-firetruck case: 30 m away
    violations = _staging_drift_violations(staged, live)
    assert len(violations) == 1
    key, drift = violations[0]
    assert key == 7
    assert drift == 30.0


def test_drift_at_exact_limit_is_not_flagged():
    staged = {1: (0.0, 0.0)}
    live = {1: (STAGING_DRIFT_LIMIT_M, 0.0)}
    assert _staging_drift_violations(staged, live) == []


def test_missing_or_nonfinite_live_positions_are_skipped():
    staged = {1: (0.0, 0.0), 2: (5.0, 5.0)}
    live = {2: (float("nan"), 5.0)}  # 1 absent (destroyed), 2 non-finite
    assert _staging_drift_violations(staged, live) == []


def test_capture_actor_xy_reads_live_transforms_and_skips_broken_actors():
    def actor(actor_id, x, y):
        return SimpleNamespace(
            id=actor_id,
            get_transform=lambda: SimpleNamespace(
                location=SimpleNamespace(x=x, y=y, z=0.0)
            ),
        )

    def broken(actor_id):
        def _raise():
            raise RuntimeError("actor destroyed")

        return SimpleNamespace(id=actor_id, get_transform=_raise)

    xy = _capture_actor_xy([actor(3, 1.5, -2.5), broken(4), None])
    assert xy == {3: (1.5, -2.5)}


# ---------------------------------------------------------------------------
# CARLA fakes for the freeze wiring
# ---------------------------------------------------------------------------
class _FakeCarla(SimpleNamespace):
    def __init__(self):
        super().__init__(
            Location=lambda x=0.0, y=0.0, z=0.0: SimpleNamespace(x=x, y=y, z=z),
            Rotation=lambda pitch=0.0, yaw=0.0, roll=0.0: SimpleNamespace(
                pitch=pitch, yaw=yaw, roll=roll
            ),
            Transform=lambda loc, rot: SimpleNamespace(location=loc, rotation=rot),
            Vector3D=lambda x=0.0, y=0.0, z=0.0: SimpleNamespace(x=x, y=y, z=z),
            VehicleControl=lambda **kw: dict(kw),
        )


class _FakeActor:
    def __init__(self, type_id="vehicle.carlamotors.firetruck"):
        self.type_id = type_id
        self.physics_calls = []
        self.controls = []

    def set_simulate_physics(self, enabled):
        self.physics_calls.append(bool(enabled))

    def set_autopilot(self, *_a):
        pass

    def set_target_velocity(self, *_a):
        pass

    def set_target_angular_velocity(self, *_a):
        pass

    def apply_control(self, control):
        self.controls.append(control)


class _FakeBlueprint(SimpleNamespace):
    def __init__(self, bp_id):
        super().__init__(id=bp_id)

    def has_attribute(self, _name):
        return False


def _fake_waypoint(x=0.0, y=0.0, yaw=0.0):
    tf = SimpleNamespace(
        location=SimpleNamespace(x=x, y=y, z=0.0),
        rotation=SimpleNamespace(pitch=0.0, yaw=yaw, roll=0.0),
        get_forward_vector=lambda: SimpleNamespace(x=1.0, y=0.0, z=0.0),
        get_right_vector=lambda: SimpleNamespace(x=0.0, y=1.0, z=0.0),
    )
    wp = SimpleNamespace(transform=tf)
    wp.next = lambda d: [_fake_waypoint(x + d, y, yaw)]
    return wp


# ---------------------------------------------------------------------------
# ESB: the parked emergency vehicle must be frozen at spawn
# ---------------------------------------------------------------------------
def test_emergency_vehicle_is_frozen_at_spawn(monkeypatch):
    spawned = []

    def try_spawn_actor(_bp, _tf):
        actor = _FakeActor()
        spawned.append(actor)
        return actor

    bp = _FakeBlueprint("vehicle.carlamotors.firetruck")
    world = SimpleNamespace(
        get_blueprint_library=lambda: SimpleNamespace(
            find=lambda _bp_id: bp, filter=lambda _pattern: [bp]
        ),
        try_spawn_actor=try_spawn_actor,
    )
    monkeypatch.setattr(esb, "import_carla", _FakeCarla)
    monkeypatch.setattr(esb, "route_waypoint", lambda *_a: _fake_waypoint(18.0))

    out = esb._spawn_emergency_vehicle(
        world, SimpleNamespace(location=SimpleNamespace(x=0, y=0, z=0)), {}
    )

    assert len(out) == 1
    assert spawned[0].physics_calls == [False], (
        "the parked emergency vehicle must have physics disabled at spawn — "
        "physics-on parking let a depenetration impulse launch it airborne"
    )


# ---------------------------------------------------------------------------
# Construction zone: the works vehicle must be frozen at spawn
# ---------------------------------------------------------------------------
def test_construction_works_vehicle_is_frozen_at_spawn(monkeypatch):
    spawned = []

    def try_spawn_actor(bp, _tf):
        if not str(bp.id).startswith("vehicle."):
            return None
        actor = _FakeActor(bp.id)
        spawned.append(actor)
        return actor

    truck_bp = _FakeBlueprint("vehicle.carlamotors.firetruck")

    def bp_filter(pattern):
        if pattern.startswith("vehicle"):
            return [truck_bp]
        return []

    base_wp = _fake_waypoint(0.0)
    world = SimpleNamespace(
        get_blueprint_library=lambda: SimpleNamespace(filter=bp_filter),
        get_map=lambda: SimpleNamespace(
            get_waypoint=lambda *_a, **_k: base_wp
        ),
        try_spawn_actor=try_spawn_actor,
    )
    monkeypatch.setattr(scene_actors, "import_carla", _FakeCarla)

    out = scene_actors.spawn_construction_zone(
        world, SimpleNamespace(location=SimpleNamespace(x=0, y=0, z=0))
    )

    assert len(spawned) == 1
    assert spawned[0].physics_calls == [False], (
        "the works vehicle behind the barricades must be frozen like the "
        "crash-pileup vehicles — physics-on parking can launch it"
    )
    assert list(out) == spawned


# ---------------------------------------------------------------------------
# Adjacent-lane car: frozen at spawn, not only in the scenario's late hook
# ---------------------------------------------------------------------------
def test_adjacent_vehicle_is_frozen_at_spawn(monkeypatch):
    spawned = []

    def try_spawn_actor(_bp, _tf):
        actor = _FakeActor("vehicle.tesla.model3")
        spawned.append(actor)
        return actor

    world = SimpleNamespace(
        try_spawn_actor=try_spawn_actor,
        get_blueprint_library=lambda: None,
    )
    monkeypatch.setattr(scene_actors, "import_carla", _FakeCarla)
    monkeypatch.setattr(
        scene_actors, "route_waypoint", lambda *_a: _fake_waypoint(26.0)
    )
    monkeypatch.setattr(
        scene_actors, "_four_wheelers",
        lambda _lib: [_FakeBlueprint("vehicle.tesla.model3")],
    )

    out = scene_actors.spawn_adjacent_vehicle(
        world, SimpleNamespace(location=SimpleNamespace(x=0, y=0, z=0))
    )

    assert len(out) == 1
    assert False in spawned[0].physics_calls, (
        "the adjacent-lane car must be frozen AT SPAWN — the async window "
        "before the scenario's late freeze is the same depenetration-launch "
        "window that ejected the esb firetruck"
    )
