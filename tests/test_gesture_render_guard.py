from __future__ import annotations

from types import SimpleNamespace

import pytest

from marshal_bench.actors.gesture_engine import GestureEngine, GestureID, GestureState
from marshal_bench.actors.traffic_officer import TrafficOfficer
from marshal_bench.scenarios._common import actor_in_camera_frustum


class _Transform:
    def __init__(
        self,
        x=0.0,
        y=0.0,
        z=0.0,
        pitch=0.0,
        yaw=0.0,
        roll=0.0,
    ):
        self.location = SimpleNamespace(x=x, y=y, z=z)
        self.rotation = SimpleNamespace(pitch=pitch, yaw=yaw, roll=roll)


class _BoneActor:
    _BONES = (
        "crl_shoulder__L",
        "crl_arm__L",
        "crl_foreArm__L",
        "crl_hand__L",
        "crl_shoulder__R",
        "crl_arm__R",
        "crl_foreArm__R",
        "crl_hand__R",
    )

    def __init__(self, actor_id: int):
        self.id = actor_id
        self.rotations = {name: (0.0, 0.0, 0.0) for name in self._BONES}

    def get_transform(self):
        return _Transform()

    def get_bones(self):
        bones = []
        for name, rotation in self.rotations.items():
            tf = _Transform(pitch=rotation[0], yaw=rotation[1], roll=rotation[2])
            bones.append(SimpleNamespace(name=name, world=tf, component=tf, relative=tf))
        return SimpleNamespace(bone_transforms=bones)


@pytest.fixture(autouse=True)
def _fake_carla(monkeypatch):
    fake_carla = SimpleNamespace(
        Location=lambda x=0.0, y=0.0, z=0.0: SimpleNamespace(x=x, y=y, z=z),
        Rotation=lambda pitch=0.0, yaw=0.0, roll=0.0: SimpleNamespace(
            pitch=pitch, yaw=yaw, roll=roll
        ),
        Transform=lambda location, rotation: SimpleNamespace(location=location, rotation=rotation),
    )
    monkeypatch.setattr("marshal_bench.actors.gesture_engine.import_carla", lambda: fake_carla)


def _guard(actor: _BoneActor, gesture_id: GestureID = GestureID.STOP) -> TrafficOfficer:
    officer = TrafficOfficer.__new__(TrafficOfficer)
    officer.authorized = True
    officer.authority_type = "police"
    officer.role_name = "traffic_officer"
    officer._actor_blueprint_id = "walker.test"
    officer._caps = SimpleNamespace(custom_asset_walker=False)
    officer.use_debug_visuals = False
    officer._using_fallback = False
    officer.hand_prop = None
    officer._hand_prop_blueprint_id = None
    officer._hand_prop_actor = None
    officer._hand_prop_debug_paddle = False
    officer._actor = actor
    officer._skeleton_ok = True
    officer._current_gesture = GestureState(gesture_id, onset_time=1.0, duration=5.0)
    officer._gesture_engine = GestureEngine(SimpleNamespace())
    officer._gesture_engine._ensure_baseline(actor)
    officer._idle_bone_rotations = {
        name: (0.0, 0.0, 0.0) for name in actor.rotations
    }
    officer._last_gesture_verify_at = None
    officer._gesture_render_checks_performed = 0
    officer._gesture_render_checks_passed = 0
    officer._gesture_render_checks_skipped_off_screen = 0
    officer._setup_errors = []
    return officer


def _verify_frustum(officer: TrafficOfficer, frustum) -> str | None:
    return officer.verify_gesture_rendered(
        1.2,
        in_camera_frustum=(
            True if frustum.status == "visible"
            else False if frustum.status == "off_screen"
            else None
        ),
        unavailable_reason=frustum.reason if frustum.status == "error" else None,
    )


def test_render_guard_flags_a_visible_arm_that_never_moves():
    officer = _guard(_BoneActor(1))

    error = officer.verify_gesture_rendered(1.2, in_camera_frustum=True)

    assert error is not None
    assert "gesture not rendered" in error
    assert officer.get_setup_errors() == [error]


def test_render_guard_accepts_a_visible_arm_that_moves():
    actor = _BoneActor(2)
    officer = _guard(actor)
    actor.rotations["crl_hand__R"] = (0.0, 0.0, -88.0)

    assert officer.verify_gesture_rendered(1.2, in_camera_frustum=True) is None
    assert officer.get_setup_errors() == []
    metadata = officer.get_metadata()
    assert metadata["gesture_render_checks_performed"] == 1
    assert metadata["gesture_render_checks_passed"] == 1
    assert metadata["gesture_render_checks_skipped_off_screen"] == 0


def test_render_guard_does_not_judge_an_offscreen_actor():
    officer = _guard(_BoneActor(3))

    assert officer.verify_gesture_rendered(1.2, in_camera_frustum=False) is None
    assert officer.get_setup_errors() == []
    assert officer.get_metadata()["gesture_render_checks_skipped_off_screen"] == 1


def test_render_guard_fails_closed_on_an_unreadable_pose():
    actor = _BoneActor(4)
    officer = _guard(actor)
    probe_name, _ = officer._gesture_engine.gesture_render_probe(actor, GestureID.STOP)
    actor.rotations.pop(probe_name)

    error = officer.verify_gesture_rendered(1.2, in_camera_frustum=True)

    assert error is not None
    assert error.startswith("gesture render check unavailable: bone read failed:")
    assert officer.get_setup_errors() == [error]


def test_render_guard_fails_closed_when_probe_is_missing(monkeypatch):
    officer = _guard(_BoneActor(5))
    monkeypatch.setattr(officer._gesture_engine, "gesture_render_probe", lambda *_: None)

    error = officer.verify_gesture_rendered(1.2, in_camera_frustum=True)

    assert error == "gesture render check unavailable: missing probe for STOP"
    assert officer.get_setup_errors() == [error]


def test_missing_camera_is_a_setup_error():
    officer = _guard(_BoneActor(6))

    error = _verify_frustum(
        officer,
        actor_in_camera_frustum(None, officer._actor, horizontal_fov_deg=90.0),
    )

    assert error == "gesture render check unavailable: ego camera missing"
    assert officer.get_setup_errors() == [error]


def test_camera_transform_failure_is_a_setup_error():
    officer = _guard(_BoneActor(7))
    camera = SimpleNamespace(get_transform=lambda: (_ for _ in ()).throw(RuntimeError("bad camera")))

    error = _verify_frustum(
        officer,
        actor_in_camera_frustum(camera, officer._actor, horizontal_fov_deg=90.0),
    )

    assert error is not None
    assert error.startswith("gesture render check unavailable: frustum computation failed:")
    assert officer.get_setup_errors() == [error]


def test_frustum_computation_exception_is_a_setup_error(monkeypatch):
    officer = _guard(_BoneActor(8))
    officer._actor.get_transform = lambda: _Transform(x=2.0)
    camera_tf = _Transform()
    camera_tf.get_forward_vector = lambda: SimpleNamespace(x=1.0, y=0.0, z=0.0)
    camera_tf.get_right_vector = lambda: SimpleNamespace(x=0.0, y=1.0, z=0.0)
    camera_tf.get_up_vector = lambda: SimpleNamespace(x=0.0, y=0.0, z=1.0)
    camera = SimpleNamespace(get_transform=lambda: camera_tf)
    monkeypatch.setattr("marshal_bench.scenarios._common.math.tan", lambda *_: (_ for _ in ()).throw(RuntimeError("bad math")))

    error = _verify_frustum(
        officer,
        actor_in_camera_frustum(camera, officer._actor, horizontal_fov_deg=90.0),
    )

    assert error is not None
    assert "frustum computation failed: bad math" in error
    assert officer.get_setup_errors() == [error]


def test_actor_genuinely_outside_frustum_is_skipped():
    officer = _guard(_BoneActor(9))
    actor = officer._actor
    actor.get_transform = lambda: _Transform(x=-2.0)
    camera_tf = _Transform()
    camera_tf.get_forward_vector = lambda: SimpleNamespace(x=1.0, y=0.0, z=0.0)
    camera_tf.get_right_vector = lambda: SimpleNamespace(x=0.0, y=1.0, z=0.0)
    camera_tf.get_up_vector = lambda: SimpleNamespace(x=0.0, y=0.0, z=1.0)
    camera = SimpleNamespace(get_transform=lambda: camera_tf)

    frustum = actor_in_camera_frustum(camera, actor, horizontal_fov_deg=90.0)

    assert frustum.status == "off_screen"
    assert _verify_frustum(officer, frustum) is None
    assert officer.get_setup_errors() == []
    assert officer.get_metadata()["gesture_render_checks_skipped_off_screen"] == 1


@pytest.mark.parametrize(
    "gesture_id",
    [
        GestureID.STOP,
        GestureID.HOLD,
        GestureID.SLOW,
        GestureID.PROCEED,
        GestureID.LEFT,
        GestureID.RIGHT,
    ],
)
def test_render_guard_accepts_every_real_pose_phase(gesture_id: GestureID):
    """The threshold comes from the real keyframes, including small phases."""
    engine = GestureEngine(SimpleNamespace())
    for index, frame in enumerate(engine.build_pose_sequence(gesture_id)):
        actor = _BoneActor(100 + index)
        officer = _guard(actor, gesture_id)
        probe_name, _ = officer._gesture_engine.gesture_render_probe(actor, gesture_id)
        bone_map = officer._gesture_engine._bone_map(actor)
        canonical = next(key for key, actual in bone_map.items() if actual == probe_name)
        actor.rotations[probe_name] = frame.bones[canonical]

        assert officer.verify_gesture_rendered(1.2, in_camera_frustum=True) is None
        assert officer.get_setup_errors() == []


def test_engine_cache_includes_carla_actor_id():
    engine = GestureEngine(SimpleNamespace())
    actor = _BoneActor(10)

    engine._ensure_baseline(actor)
    actor.id = 11
    engine._ensure_baseline(actor)

    assert len(engine.pose_baseline_rotations_) == 2
    assert {key[1] for key in engine.pose_baseline_rotations_} == {10, 11}
    engine.clear_actor(actor)
    assert engine.pose_baseline_rotations_ == {}


def test_compact_26_bone_rig_is_not_gesture_capable():
    names = [
        "crl_root", "crl_hips__C", "crl_spine__C", "crl_spine01__C",
        "crl_shoulder__R", "crl_arm__R", "crl_foreArm__R", "crl_hand__R",
        "crl_shoulder__L", "crl_arm__L", "crl_foreArm__L", "crl_hand__L",
    ] + [f"other_{index}" for index in range(14)]
    tf = _Transform()
    actor = _BoneActor(52)
    actor.get_bones = lambda: SimpleNamespace(
        bone_transforms=[
            SimpleNamespace(name=name, world=tf, component=tf, relative=tf)
            for name in names
        ]
    )
    engine = GestureEngine(SimpleNamespace())

    error = engine.gesture_capability_error(actor, GestureID.STOP)

    assert error is not None
    assert "compact 26-bone skeleton" in error


def test_full_rig_with_signalling_chain_is_gesture_capable():
    actor = _BoneActor(47)
    actor.rotations["crl_index01__R"] = (0.0, 0.0, 0.0)
    engine = GestureEngine(SimpleNamespace())

    assert engine.gesture_capability_error(actor, GestureID.STOP) is None
