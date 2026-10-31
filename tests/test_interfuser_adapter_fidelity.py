"""InterFuser adapter: defaults follow the original leaderboard agent; the protocol-v2 settings stay reproducible."""
from marshal_bench.controllers.interfuser_model import InterFuserController, downsample_route_ids


def test_downsample_matches_leaderboard_rule():
    # leaderboard downsample_route(route, 50) on a straight single-option 1 m route: keep the first point, a point
    # once the distance since the last kept one exceeds 50 m, and the last point
    pts = [(float(i), 0.0) for i in range(160)]
    assert downsample_route_ids(pts, 50) == [0, 52, 104, 156, 159]


def test_defaults_are_original_agent_and_legacy_is_reproducible():
    c = InterFuserController({})
    assert (c.route_sample_m, c.sensor_profile, c.step_on_sensor_data, c.hold_odd_step_control) == (
        50.0, "leaderboard", True, True)
    legacy = InterFuserController({"interfuser": {"route_sample_m": 0, "sensor_profile": "carla_default",
                                                  "step_on_sensor_data": False, "hold_odd_step_control": False}})
    assert (legacy.route_sample_m, legacy.sensor_profile, legacy.step_on_sensor_data,
            legacy.hold_odd_step_control) == (0.0, "carla_default", False, False)


def test_verbose_trace_is_off_by_default(monkeypatch):
    monkeypatch.delenv("MARSHAL_INTERFUSER_VERBOSE", raising=False)
    assert InterFuserController({})._verbose is False
    monkeypatch.setenv("MARSHAL_INTERFUSER_VERBOSE", "1")
    assert InterFuserController({})._verbose is True


def test_forward_speed_matches_leaderboard_speedometer():
    from types import SimpleNamespace as N

    from marshal_bench.controllers.interfuser_model import forward_speed
    # falling straight down while level: the speedometer reads 0 (the 3-D norm would read 5)
    assert abs(forward_speed(N(x=0.0, y=0.0, z=-5.0), N(pitch=0.0, yaw=-90.0))) < 1e-9
    # driving north (yaw -90 => -y) at 4 m/s reads +4; reversing reads negative
    assert abs(forward_speed(N(x=0.0, y=-4.0, z=0.0), N(pitch=0.0, yaw=-90.0)) - 4.0) < 1e-9
    assert forward_speed(N(x=0.0, y=4.0, z=0.0), N(pitch=0.0, yaw=-90.0)) < 0
    assert InterFuserController({}).speed_input == "forward"
    assert InterFuserController({"interfuser": {"speed_input": "3d"}}).speed_input == "3d"
