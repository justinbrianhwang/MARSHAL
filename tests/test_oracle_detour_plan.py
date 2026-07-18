import math
from types import SimpleNamespace

import pytest

from marshal_bench.controllers.oracle import OracleController


class _Location:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def distance(self, other):
        return math.sqrt(
            (self.x - other.x) ** 2
            + (self.y - other.y) ** 2
            + (self.z - other.z) ** 2
        )


class _Transform:
    def __init__(self, location, rotation=None):
        self.location = location
        self.rotation = rotation or SimpleNamespace(yaw=0.0)

    def get_right_vector(self):
        return SimpleNamespace(x=0.0, y=1.0, z=0.0)

    def get_forward_vector(self):
        return SimpleNamespace(x=1.0, y=0.0, z=0.0)


class _Waypoint:
    lane_type = "Driving"
    lane_width = 3.5
    is_junction = False

    def __init__(self, name, x, y, yaw=0.0):
        self.name = name
        self.transform = _Transform(
            _Location(x, y), SimpleNamespace(yaw=yaw)
        )
        self._left = None
        self._right = None
        self._next = []

    def get_left_lane(self):
        return self._left

    def get_right_lane(self):
        return self._right

    def next(self, _distance):
        return self._next


class _Agent:
    def __init__(self):
        self.plan = None
        self.plans = []
        self.offset_calls = []

    def set_global_plan(self, plan, **kwargs):
        self.plan = (plan, kwargs)
        self.plans.append(self.plan)

    def set_offset(self, offset):
        self.offset_calls.append(offset)

    def ignore_vehicles(self, _ignore):
        pass

    def set_target_speed(self, _speed):
        pass


def _controller(*, adjacent=True):
    originals = [_Waypoint(f"original-{i}", i * 2.0, 0.0) for i in range(9)]
    for current, following in zip(originals, originals[1:]):
        current._next = [following]
    if adjacent:
        lane = _Waypoint("adjacent", 0.0, -3.4)
        originals[0]._left = lane
    ego = SimpleNamespace(
        get_location=lambda: _Location(0.0, 0.0),
        bounding_box=SimpleNamespace(extent=SimpleNamespace(y=0.95)),
    )
    controller = OracleController()
    controller.carla = SimpleNamespace(Location=_Location, Transform=_Transform)
    controller.ego = ego
    controller._map = SimpleNamespace(get_waypoint=lambda *_args, **_kwargs: originals[0])
    controller._agent = _Agent()
    controller._road_option = "LANEFOLLOW"
    controller._original_route = [(wp, "LANEFOLLOW") for wp in originals]
    return controller, originals


def _control():
    return SimpleNamespace(
        throttle=0.3,
        brake=0.0,
        steer=0.0,
        hand_brake=False,
        reverse=False,
        manual_gear_shift=False,
        gear=0,
    )


def test_detour_plan_offsets_original_route_not_adjacent_lane_chain():
    controller, originals = _controller()

    controller._prepare_detour_plan()

    plan, options = controller._agent.plan
    targets = [waypoint for waypoint, _option in plan]
    assert [target._waypoint for target in targets] == originals
    assert [target.transform.location.x for target in targets] == pytest.approx(
        [wp.transform.location.x for wp in originals]
    )
    assert [target.transform.location.y for target in targets] == pytest.approx(
        [-4.0] * len(originals)
    )
    assert options == {"stop_waypoint_creation": True, "clean_queue": True}
    assert controller._route_offset == pytest.approx(-4.0)
    assert controller._agent.offset_calls == []


def test_detour_fallback_is_lane_width_plus_safety_margin():
    controller, _originals = _controller(adjacent=False)

    controller._prepare_detour_plan()

    assert controller._route_offset == pytest.approx(-4.05)
    plan, _options = controller._agent.plan
    assert plan[0][0].transform.location.y == pytest.approx(-4.05)


def test_hazard_clear_starts_bounded_merge_on_original_route():
    controller, originals = _controller()
    controller._prepare_detour_plan()

    assert controller._hazard_cleared({"hazard_forward_m": -5.0}) is False
    assert controller._hazard_cleared({"hazard_forward_m": -5.01}) is True
    controller._start_detour_merge(blend_distance_m=12.0)

    plan, _options = controller._agent.plan
    targets = [waypoint for waypoint, _option in plan]
    assert [target._waypoint for target in targets] == originals
    assert targets[0].transform.location.y == pytest.approx(-4.0)
    assert targets[3].transform.location.y == pytest.approx(-2.0)
    assert targets[6].transform.location.y == pytest.approx(0.0)
    assert targets[-1].transform.location.y == pytest.approx(0.0)
    assert controller._detour_merge_started is True
    assert controller._route_offset == 0.0


def test_blocking_set_clear_is_not_pinned_by_roadside_context():
    controller, _originals = _controller()

    assert controller._hazard_cleared({
        "blocking_hazard_forward_m": -5.01,
        "hazard_forward_m": 10.0,
    }) is True


def test_merge_plan_does_not_follow_adjacent_chain_through_junction():
    controller, originals = _controller()
    cross_street = _Waypoint("cross-street", 2.0, -3.4, yaw=90.0)
    originals[0]._left._next = [cross_street]

    controller._prepare_detour_plan()
    controller._start_detour_merge()

    for installed, _options in controller._agent.plans:
        assert all(target._waypoint is not cross_street for target, _option in installed)
