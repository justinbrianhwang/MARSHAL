from types import SimpleNamespace

import pytest

from marshal_bench.controllers.oracle import OracleController


class _Transform:
    def __init__(self, forward):
        self._forward = forward

    def get_forward_vector(self):
        return self._forward


class _Ego:
    def __init__(self, location, forward):
        self.location = location
        self.forward = forward

    def get_location(self):
        return self.location

    def get_transform(self):
        return _Transform(self.forward)


def _controller(*, x=5.0, y=0.0, offset=3.6):
    controller = OracleController()
    controller.ego = _Ego(
        SimpleNamespace(x=x, y=y),
        SimpleNamespace(x=1.0, y=0.0),
    )
    controller._route_origin = SimpleNamespace(x=0.0, y=0.0)
    controller._route_forward = SimpleNamespace(x=1.0, y=0.0)
    controller._route_right = SimpleNamespace(x=0.0, y=1.0)
    controller._route_offset = offset
    controller._onset_time = 1.0
    return controller


@pytest.mark.parametrize(("offset", "expected_sign"), [(3.6, 1), (-3.6, -1)])
def test_lateral_watchdog_steers_when_offset_planner_stays_straight(offset, expected_sign):
    controller = _controller(offset=offset)
    control = SimpleNamespace(steer=0.0)

    result = controller._ensure_lateral_response(control, sim_time=2.0)

    assert controller._lateral_watchdog_engaged is True
    assert result.steer * expected_sign > 0.0
    assert abs(result.steer) <= 0.55


def test_lateral_watchdog_preserves_working_local_planner_path():
    controller = _controller()
    control = SimpleNamespace(steer=0.12)

    result = controller._ensure_lateral_response(control, sim_time=2.0)

    assert controller._lateral_watchdog_engaged is False
    assert controller._lateral_watchdog_stood_down is True
    assert result.steer == pytest.approx(0.12)


def test_lateral_watchdog_does_not_touch_healthy_plan_with_straight_start():
    controller = _controller(x=0.0)

    for forward in (1.0, 3.0, 4.99):
        controller.ego.location.x = forward
        control = SimpleNamespace(steer=0.0)
        assert controller._ensure_lateral_response(control, sim_time=2.0).steer == 0.0
        assert controller._lateral_watchdog_engaged is False

    controller.ego.location.x = 5.0
    steering = SimpleNamespace(steer=-0.08)
    assert controller._ensure_lateral_response(steering, sim_time=2.0).steer == -0.08
    assert controller._lateral_watchdog_stood_down is True

    controller.ego.location.x = 8.0
    later_straight = SimpleNamespace(steer=0.0)
    assert controller._ensure_lateral_response(later_straight, sim_time=3.0).steer == 0.0
    assert controller._lateral_watchdog_engaged is False


def test_lateral_watchdog_stands_down_if_planner_recovers():
    controller = _controller()
    controller._ensure_lateral_response(SimpleNamespace(steer=0.0), sim_time=2.0)
    assert controller._lateral_watchdog_engaged is True

    recovered = SimpleNamespace(steer=0.10)
    assert controller._ensure_lateral_response(recovered, sim_time=2.1).steer == 0.10
    assert controller._lateral_watchdog_engaged is False
    assert controller._lateral_watchdog_stood_down is True
