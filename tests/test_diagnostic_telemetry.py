import json
from types import SimpleNamespace

import pytest

from marshal_bench.controllers.oracle import OracleController
from marshal_bench.scenarios._common import (
    ScenarioContext,
    _blocking_route_forward_m,
    _append_collision_identity,
    _collision_identity_record,
    yield_officer_center_clearance_m,
)


def _actor(actor_id, type_id):
    return SimpleNamespace(id=actor_id, type_id=type_id)


class _Officer:
    def __init__(self, actor, auxiliaries=()):
        self._actor = actor
        self._aux_actors = list(auxiliaries)

    def get_actor(self):
        return self._actor


def test_collision_identity_names_managed_actor_group_and_caps_at_twenty():
    blocking = _actor(11, "vehicle.crash")
    officer = _actor(12, "walker.pedestrian.0001")
    prop = _actor(13, "static.prop.trafficcone01")
    extra = _actor(14, "vehicle.ambulance")
    ctx = ScenarioContext(
        officer=_Officer(officer, [prop]),
        extra_actors=[blocking, extra],
        blocking_actors=[blocking],
    )

    record = _collision_identity_record(
        ctx, SimpleNamespace(other_actor=blocking), 11.437
    )
    assert record == {
        "sim_time": 11.437,
        "other_type_id": "vehicle.crash",
        "other_actor_id": 11,
        "other_is_scene_actor": True,
        "other_scene_actor_group": "blocking",
    }
    assert _collision_identity_record(
        ctx, SimpleNamespace(other_actor=officer), 1.0
    )["other_scene_actor_group"] == "officer_or_civilian"
    assert _collision_identity_record(
        ctx, SimpleNamespace(other_actor=prop), 1.0
    )["other_scene_actor_group"] == "officer_or_civilian"
    assert _collision_identity_record(
        ctx, SimpleNamespace(other_actor=extra), 1.0
    )["other_scene_actor_group"] == "extra"

    records = []
    event = SimpleNamespace(other_actor=blocking)
    for index in range(25):
        _append_collision_identity(records, ctx, event, float(index))
    assert len(records) == 20
    assert records[-1]["sim_time"] == 19.0


def test_oracle_debug_jsonl_is_flush_safe_and_additive(tmp_path, monkeypatch):
    monkeypatch.setenv("MARSHAL_ORACLE_DEBUG", "1")
    controller = OracleController()
    controller.set_episode_dir(str(tmp_path))
    controller._configure_debug_output()
    assert "step" in controller.__dict__
    controller._action = "DETOUR"
    controller._onset_time = 1.0
    controller._detour_committed = True
    controller._route_offset = -4.0
    controller._route_origin = SimpleNamespace(x=0.0, y=0.0)
    controller._route_forward = SimpleNamespace(x=1.0, y=0.0)
    controller._route_right = SimpleNamespace(x=0.0, y=1.0)
    controller.ego = SimpleNamespace(
        get_location=lambda: SimpleNamespace(x=6.0, y=-3.75)
    )

    controller._write_debug_record(
        {"blocking_hazard_forward_m": 8.5}, sim_time=2.0
    )
    debug_path = tmp_path / "oracle_debug.jsonl"
    record = json.loads(debug_path.read_text(encoding="utf-8"))
    assert record == {
        "sim_time": 2.0,
        "phase": "detour",
        "route_offset_target": -4.0,
        "applied_offset_estimate": -3.75,
        "blocking_hazard_forward_m": 8.5,
        "merge_active": False,
        "merge_progress_m": 0.0,
    }
    controller._detour_merge_started = True
    controller._merge_start_forward_m = 4.0
    controller._route_offset = 0.0
    controller._write_debug_record(
        {"blocking_hazard_forward_m": -5.25}, sim_time=2.05
    )
    records = [
        json.loads(line)
        for line in debug_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[1]["phase"] == "merge"
    assert records[1]["merge_active"] is True
    assert records[1]["merge_progress_m"] == 2.0
    controller.teardown()


def test_oracle_debug_unset_creates_no_artifact(tmp_path, monkeypatch):
    monkeypatch.delenv("MARSHAL_ORACLE_DEBUG", raising=False)
    controller = OracleController()
    controller.set_episode_dir(str(tmp_path))
    controller._configure_debug_output()
    assert "step" not in controller.__dict__
    controller._write_debug_record({}, sim_time=1.0)

    assert not (tmp_path / "oracle_debug.jsonl").exists()


def test_route_arc_clearance_is_monotonic_and_latched():
    locations = [SimpleNamespace(x=float(i), y=0.0, z=0.0) for i in range(12)]
    actor = SimpleNamespace(get_location=lambda: locations[5])
    ctx = ScenarioContext(
        blocking_actors=[actor],
    )

    assert _blocking_route_forward_m(ctx, object(), locations[0]) == 5.0
    assert _blocking_route_forward_m(ctx, object(), locations[11]) < -5.0
    actor.get_location = lambda: locations[11]
    assert _blocking_route_forward_m(ctx, object(), locations[6]) < -5.0
    assert ctx.blocking_clear_latched is True


def test_yield_clearance_arithmetic_is_pinned_to_model3_geometry():
    assert yield_officer_center_clearance_m(1.081725) == pytest.approx(2.631725)
