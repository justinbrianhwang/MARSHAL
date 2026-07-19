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


def test_lateral_watchdog_engages_despite_offset_spawn_projection():
    """Engagement must measure drift from the ego's own initial projection.

    The Town02 civilian_warning_accident spawn projects 0.30 m off the
    route frame's centreline; gating on absolute |lateral| >= 0.25 made
    watchdog engagement a millimetre-drift coin flip (straight-line crash
    into the pileup when it lost).
    """
    controller = _controller(offset=-4.55)
    controller.ego.location.y = -0.30  # spawn's own route projection

    control = SimpleNamespace(steer=0.0)
    result = controller._ensure_lateral_response(control, sim_time=2.0)

    assert controller._lateral_watchdog_engaged is True
    assert result.steer < 0.0


def test_fallback_merge_keeps_holding_centreline_after_taper_completes():
    """Post-merge the fallback must keep steering at offset 0.

    Releasing lateral control at taper end left the ego steering straight
    off curved roads (Town01/02/03 post-merge pole and guardrail
    collisions ~3 s after a clean-looking 12 m taper).
    """
    controller = _controller(offset=-4.0)
    controller._merge_fallback_active = True
    controller._lateral_watchdog_engaged = True
    controller._merge_start_offset = -4.0
    controller._merge_blend_distance_m = 12.0
    controller._merge_progress_distance_m = 0.0
    controller._merge_last_location = SimpleNamespace(
        x=0.0, y=-4.0, distance=lambda other: 0.0
    )

    # Drive 12 m of merge progress, then 10 s of continued driving.
    class _Loc(SimpleNamespace):
        def distance(self, other):
            return abs(self.x - other.x)

    x = 0.0
    for _step in range(40):  # 40 x 0.6 m = 24 m >> 12 m blend
        prev_x = x
        x += 0.6
        controller.ego.location = _Loc(x=x, y=0.0)
        controller._merge_last_location = _Loc(x=prev_x, y=0.0)
        controller._update_fallback_merge_target()

    assert controller._merge_fallback_active is True
    assert controller._route_offset == pytest.approx(0.0)

    # A later drift off the route centreline must still produce corrective
    # steering (before the fix the fallback had deactivated and steer
    # stayed 0.0 forever).
    controller.ego.location = _Loc(x=x, y=1.2)
    drifting = SimpleNamespace(steer=0.0)
    result = controller._ensure_lateral_response(drifting, sim_time=15.0)
    assert result.steer < 0.0
