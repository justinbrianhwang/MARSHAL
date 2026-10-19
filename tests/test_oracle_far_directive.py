from types import SimpleNamespace

import pytest

from marshal_bench.controllers.oracle import OracleController
from marshal_bench.scenarios import _common


class _VehicleControl:
    def __init__(self, throttle=0.0, brake=0.0, steer=0.0):
        self.throttle = throttle
        self.brake = brake
        self.steer = steer


def _oracle(officer_transform):
    controller = OracleController()
    controller.carla = SimpleNamespace(VehicleControl=_VehicleControl)
    controller.gt = {
        "A_authority": {"type": "police"},
        "officer_transform": officer_transform,
    }
    controller._action = "STOP"
    controller._onset_time = 1.0
    controller._approach_stop_gap_m = None
    controller._far_directive_mode = False
    return controller


def _post_onset_control(controller):
    return controller._stop_control(
        _VehicleControl(throttle=0.4),
        sim_time=1.0,
        speed=2.0,
        obs={
            "ego_x": 0.0,
            "ego_y": 0.0,
            "ego_z": 0.0,
            "distance_to_stopline_m": 30.0,
        },
    )


def test_far_stopline_with_near_officer_brakes_without_latching():
    controller = _oracle({"x": 12.0, "y": 0.0, "z": 0.0})

    control = _post_onset_control(controller)

    assert controller._far_directive_mode is False
    assert control.throttle == 0.0
    assert control.brake == 1.0


def test_far_stopline_with_far_officer_latches_approach():
    controller = _oracle({"x": 30.0, "y": 0.0, "z": 0.0})

    control = _post_onset_control(controller)

    assert controller._far_directive_mode is True
    assert control.throttle == pytest.approx(0.65)
    assert control.brake == 0.0


def test_unknown_officer_distance_brakes_without_latching():
    controller = _oracle(None)

    control = _post_onset_control(controller)

    assert controller._far_directive_mode is False
    assert control.throttle == 0.0
    assert control.brake == 1.0


def test_shared_observation_does_not_expose_officer_distance(monkeypatch):
    transform = SimpleNamespace(
        location=SimpleNamespace(x=1.0, y=2.0, z=3.0),
        rotation=SimpleNamespace(yaw=4.0),
        get_forward_vector=lambda: SimpleNamespace(x=1.0, y=0.0),
    )
    ego = SimpleNamespace(
        get_transform=lambda: transform,
        get_velocity=lambda: SimpleNamespace(x=3.0, y=4.0),
    )
    ctx = _common.ScenarioContext(ego=ego)
    monkeypatch.setattr(_common, "ego_in_intersection", lambda *_args: False)

    observation = _common._build_observation(ctx, object(), sim_time=1.25)

    assert "distance_to_officer_m" not in observation
