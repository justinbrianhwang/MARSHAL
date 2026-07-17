import json
from pathlib import Path

import pytest

import start
from scripts import _shared_staging as staging
from marshal_bench.scenarios._common import should_apply_condition
from marshal_bench.utils.conditions import (
    EpisodeCondition,
    WEATHER_PARAMETER_FIELDS,
    condition_from_config,
    describe,
    resolve,
)


class FakeWeatherParameters:
    def __init__(self, **kwargs):
        for field in WEATHER_PARAMETER_FIELDS:
            setattr(self, field, 0.0)
        for key, value in kwargs.items():
            setattr(self, key, float(value))


FakeWeatherParameters.ClearNoon = FakeWeatherParameters(
    cloudiness=5.0,
    precipitation=0.0,
    sun_altitude_angle=75.0,
)
FakeWeatherParameters.HardRainNoon = FakeWeatherParameters(
    cloudiness=100.0,
    precipitation=100.0,
    wetness=100.0,
)


class FakeCarla:
    WeatherParameters = FakeWeatherParameters


def test_resolve_none_passthrough():
    assert resolve(None, FakeCarla) is None
    assert resolve(EpisodeCondition(), FakeCarla) is None


def test_resolve_preset_lookup_clones_preset():
    weather = resolve(EpisodeCondition(weather_preset="ClearNoon"), FakeCarla)

    assert weather is not FakeWeatherParameters.ClearNoon
    assert weather.cloudiness == 5.0
    assert weather.sun_altitude_angle == 75.0


def test_resolve_typo_lists_valid_presets():
    with pytest.raises(ValueError) as excinfo:
        resolve(EpisodeCondition(weather_preset="ClearMoon"), FakeCarla)

    message = str(excinfo.value)
    assert "ClearMoon" in message
    assert "ClearNoon" in message
    assert "HardRainNoon" in message


def test_resolve_params_override_preset_fields():
    weather = resolve(
        EpisodeCondition(
            weather_preset="ClearNoon",
            weather_params={"cloudiness": 42.5, "fog_density": 8.0},
        ),
        FakeCarla,
    )

    assert weather.cloudiness == 42.5
    assert weather.fog_density == 8.0
    assert weather.sun_altitude_angle == 75.0


def test_resolve_rejects_unknown_param_key():
    with pytest.raises(ValueError, match="not_a_weather_field"):
        resolve(
            EpisodeCondition(weather_params={"not_a_weather_field": 1.0}),
            FakeCarla,
        )


def test_describe_round_trips_all_fields_and_is_json_serializable():
    original = FakeWeatherParameters(
        **{field: index + 0.25 for index, field in enumerate(WEATHER_PARAMETER_FIELDS)}
    )

    payload = describe(original)

    assert set(payload) == set(WEATHER_PARAMETER_FIELDS)
    assert payload["cloudiness"] == 0.25
    assert payload["dust_storm"] == len(WEATHER_PARAMETER_FIELDS) - 1 + 0.25
    assert json.loads(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    ("raw", "preset", "params"),
    [
        ("WetNoon", "WetNoon", None),
        (
            {"preset": "HardRainNoon", "params": {"wind_intensity": 33}},
            "HardRainNoon",
            {"wind_intensity": 33.0},
        ),
        (
            {"fog_density": 20, "sun_altitude_angle": -5},
            None,
            {"fog_density": 20.0, "sun_altitude_angle": -5.0},
        ),
    ],
)
def test_condition_cfg_parsing(raw, preset, params):
    condition = condition_from_config(raw)

    assert condition.weather_preset == preset
    assert condition.weather_params == params


def test_start_default_cfg_has_no_weather_key_and_branch_is_not_taken():
    args = start._build_parser().parse_args(["--controller", "oracle"])

    cfg = start._build_episode_condition_cfg(args)

    assert "weather" not in cfg
    assert should_apply_condition(cfg) is False


def test_start_cli_parses_weather_params_kv_list():
    args = start._build_parser().parse_args(
        [
            "--controller",
            "oracle",
            "--weather",
            "ClearNoon",
            "--weather-params",
            "cloudiness=12.5,sun_altitude_angle=-8",
        ]
    )

    assert args.weather == "ClearNoon"
    assert args.weather_params == {
        "cloudiness": 12.5,
        "sun_altitude_angle": -8.0,
    }
    cfg = start._build_episode_condition_cfg(args)
    assert cfg["weather"] == {
        "preset": "ClearNoon",
        "params": {
            "cloudiness": 12.5,
            "sun_altitude_angle": -8.0,
        },
    }


def test_staged_config_default_has_no_first_class_weather(monkeypatch):
    for key in (
        "MARSHAL_SWEEP_CONDITION_ACTIVE",
        "MARSHAL_SWEEP_WEATHER",
        "MARSHAL_SWEEP_WEATHER_PARAMS",
    ):
        monkeypatch.delenv(key, raising=False)
    root = str(Path(__file__).resolve().parents[1])
    spec = {
        "config": "marshal_bench/configs/demo_green_stop.yaml",
        "expect": "STOP",
    }

    cfg = staging.load_staged_config(root, "green_stop", spec, "oracle")

    assert "weather" not in cfg
    assert cfg["environment"]["weather"] == "ClearNoon"


def test_full_sweep_transport_is_normalized_into_episode_cfg(monkeypatch):
    monkeypatch.setenv("MARSHAL_SWEEP_CONDITION_ACTIVE", "1")
    monkeypatch.setenv("MARSHAL_SWEEP_WEATHER", "WetNoon")
    monkeypatch.setenv(
        "MARSHAL_SWEEP_WEATHER_PARAMS",
        json.dumps({"cloudiness": 44.0}),
    )
    root = str(Path(__file__).resolve().parents[1])
    spec = {
        "config": "marshal_bench/configs/demo_green_stop.yaml",
        "expect": "STOP",
    }

    cfg = staging.load_staged_config(root, "green_stop", spec, "oracle")

    assert cfg["weather"] == {
        "preset": "WetNoon",
        "params": {"cloudiness": 44.0},
    }
