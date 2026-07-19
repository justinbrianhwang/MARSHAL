from types import SimpleNamespace

from marshal_bench.scenarios import _common
from marshal_bench.scenarios._common import _stop_completion_reached


def test_stop_completion_does_not_fire_for_station_already_in_junction():
    assert not _stop_completion_reached(2.05, 0.0, 0.0, True, False)


def test_stop_completion_fires_after_post_start_junction_entry():
    assert _stop_completion_reached(5.05, 1.0, 0.0, True, True)


def test_stop_completion_rejects_unrelated_junction_far_from_stopline():
    # Curated Town03 green_stop: spawn sits 1.2 m before an unrelated
    # junction polygon while the assigned stopline is 44 m ahead. Entering
    # that polygon must never complete the episode.
    assert not _stop_completion_reached(
        5.05, 1.0, 0.0, True, True, stopline_distance_m=42.7
    )


def test_stop_completion_allows_assigned_junction_near_stopline():
    assert _stop_completion_reached(
        5.05, 1.0, 0.0, True, True, stopline_distance_m=6.0
    )


def test_stop_completion_waits_for_strict_scorer_reaction_window():
    # The scorer verifies a sustained stop after onset + reaction deadline
    # (3 s); completing at onset+2 truncates that telemetry (Town03
    # benchmark green_stop failed exactly this way at last_ts=3.05).
    assert not _stop_completion_reached(
        3.05, 1.0, 0.0, True, True, stopline_distance_m=6.0
    )
    assert _stop_completion_reached(
        5.10, 1.0, 0.0, True, True, stopline_distance_m=6.0
    )


def test_spawn_settle_uses_service_brake_without_latching_handbrake(monkeypatch):
    controls = []
    monkeypatch.setattr(
        _common,
        "import_carla",
        lambda: SimpleNamespace(VehicleControl=lambda **kwargs: kwargs),
    )
    ego = SimpleNamespace(apply_control=controls.append)

    _common._hold_ego_during_spawn_settle(ego)

    assert controls == [{
        "throttle": 0.0,
        "steer": 0.0,
        "brake": 1.0,
        "hand_brake": False,
    }]


def test_spawn_settle_handbrake_is_explicitly_released(monkeypatch):
    controls = []
    monkeypatch.setattr(
        _common,
        "import_carla",
        lambda: SimpleNamespace(VehicleControl=lambda **kwargs: kwargs),
    )
    ego = SimpleNamespace(apply_control=controls.append)

    _common._release_ego_after_spawn_settle(ego)

    assert controls == [{
        "throttle": 0.0,
        "steer": 0.0,
        "brake": 1.0,
        "hand_brake": False,
    }]
