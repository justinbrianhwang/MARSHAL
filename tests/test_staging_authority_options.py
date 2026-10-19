from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from marshal_bench.actors.officer_blueprint_selector import select_officer_blueprint
from marshal_bench.scenarios import _common
from scripts import _shared_staging as staging


NEW_ENV_VARS = (
    "MARSHAL_OFFICER_DISTANCE_M",
    "MARSHAL_OFFICER_CONDITION",
    "MARSHAL_AUTHORITY_APPEARANCE",
    "MARSHAL_SPAWN_BACK_M",
)


def _clear_new_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NEW_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_officer_distance_overrides_visibility_before_spawn_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_new_env(monkeypatch)
    monkeypatch.setenv("MARSHAL_OFFICER_DISTANCE_M", "21.5")
    monkeypatch.setenv("MARSHAL_SPAWN_BACK_M", "7")
    cfg = {"officer": {"distance": 30.0, "lateral_offset": 2.0}}

    staging.apply_staging_overrides(cfg, "green_stop")

    assert cfg["officer"]["distance"] == 21.5

    requested_distances: list[float] = []
    route_transform = SimpleNamespace(
        location=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        rotation=SimpleNamespace(yaw=0.0),
        get_right_vector=lambda: SimpleNamespace(x=0.0, y=1.0),
    )
    waypoint = SimpleNamespace(
        next=lambda distance: requested_distances.append(distance)
        or [SimpleNamespace(transform=route_transform)]
    )
    world = SimpleNamespace(
        get_map=lambda: SimpleNamespace(get_waypoint=lambda *args, **kwargs: waypoint)
    )
    fake_carla = SimpleNamespace(
        Location=lambda **values: SimpleNamespace(**values),
        Rotation=lambda **values: SimpleNamespace(**values),
        Transform=lambda location, rotation: SimpleNamespace(
            location=location, rotation=rotation
        ),
    )
    ego_transform = SimpleNamespace(location=SimpleNamespace(x=0.0, y=0.0, z=0.0))
    monkeypatch.setattr(_common, "import_carla", lambda: fake_carla)

    _common.officer_transform_on_ego_route(
        world, ego_transform, cfg["officer"]["distance"], 3.2
    )

    assert requested_distances == [28.5]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "not-a-number"])
def test_officer_distance_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _clear_new_env(monkeypatch)
    monkeypatch.setenv("MARSHAL_OFFICER_DISTANCE_M", value)

    with pytest.raises(ValueError, match="MARSHAL_OFFICER_DISTANCE_M"):
        staging.apply_staging_overrides({"officer": {}}, "green_stop")


def test_fake_vest_stop_changes_only_requested_officer_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_new_env(monkeypatch)
    monkeypatch.setenv("MARSHAL_OFFICER_CONDITION", "fake_vest_stop")
    cfg = {
        "officer": {
            "distance": 30.0,
            "lateral_offset": 2.0,
            "gesture": "PROCEED",
            "onset_time": 1.25,
            "duration": 9.0,
            "hand_prop": "static.prop.stopquad",
        }
    }

    staging.apply_staging_overrides(cfg, "green_stop")

    assert cfg["officer"] == {
        "distance": 13.0,
        "lateral_offset": 3.2,
        "gesture": "STOP",
        "onset_time": 1.25,
        "duration": 9.0,
        "hand_prop": "static.prop.stopquad",
        "blueprint_id": "walker.pedestrian.0052",
        "authority_type": "civilian",
        "authorized": False,
    }


def test_authority_appearance_v2_maps_eligible_null_blueprints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_new_env(monkeypatch)
    monkeypatch.setenv("MARSHAL_AUTHORITY_APPEARANCE", "v2")
    cases = [
        ({"authority_type": "flagger"}, "walker.pedestrian.0052"),
        (
            {"authority_type": "civilian", "appearance": "orange hi-vis jacket"},
            "walker.pedestrian.0052",
        ),
        ({"authority_type": "police", "blueprint_id": None}, None),
        (
            {"authority_type": "flagger", "blueprint_id": "walker.pedestrian.0001"},
            "walker.pedestrian.0001",
        ),
    ]

    for officer, expected in cases:
        cfg = {"officer": officer}
        staging.apply_staging_overrides(cfg, "not_staged")
        assert cfg["officer"].get("blueprint_id") == expected

    cfg = {"second_authority": {"authority_type": "flagger", "blueprint_id": None}}
    staging.apply_staging_overrides(cfg, "not_staged")
    assert cfg["second_authority"]["blueprint_id"] == "walker.pedestrian.0052"


def test_explicit_officer_blueprint_wins_over_police_default() -> None:
    blueprints = [
        SimpleNamespace(id="walker.pedestrian.0030"),
        SimpleNamespace(id="walker.pedestrian.0052"),
    ]
    world = SimpleNamespace(
        get_blueprint_library=lambda: SimpleNamespace(filter=lambda _pattern: blueprints)
    )

    selected, reason = select_officer_blueprint(
        world, preferred="walker.pedestrian.0052"
    )

    assert selected.id == "walker.pedestrian.0052"
    assert "preferred" in reason


def test_new_env_unset_leaves_config_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_new_env(monkeypatch)
    cfg = {
        "officer": {
            "authority_type": "flagger",
            "blueprint_id": None,
            "appearance": "orange vest",
        },
        "second_authority": {"authority_type": "flagger", "blueprint_id": None},
    }
    original = deepcopy(cfg)

    staging.apply_staging_overrides(cfg, "not_staged")

    assert cfg == original
