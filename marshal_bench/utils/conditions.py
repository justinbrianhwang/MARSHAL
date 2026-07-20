"""Episode weather/time-of-day conditions without a hard CARLA dependency."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

log = logging.getLogger(__name__)


REPORTED_GRID = [
    "ClearNoon",
    "WetNoon",
    "HardRainNoon",
    "FogMorning",
    "ClearSunset",
    "ClearNight",
]

# Public attributes on CARLA 0.9.x WeatherParameters.  Keeping this list here
# makes validation and telemetry stable across fake modules and CARLA versions.
WEATHER_PARAMETER_FIELDS = (
    "cloudiness",
    "precipitation",
    "precipitation_deposits",
    "wind_intensity",
    "sun_azimuth_angle",
    "sun_altitude_angle",
    "fog_density",
    "fog_distance",
    "wetness",
    "fog_falloff",
    "scattering_intensity",
    "mie_scattering_scale",
    "rayleigh_scattering_scale",
    "dust_storm",
)


@dataclass(frozen=True)
class EpisodeCondition:
    weather_preset: Optional[str] = None
    weather_params: Optional[Dict[str, float]] = None


def parse_weather_params(value: Optional[str]) -> Optional[Dict[str, float]]:
    """Parse ``k=v,k=v`` CLI syntax into float-valued weather parameters."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return {}
    parsed: Dict[str, float] = {}
    for item in text.split(","):
        item = item.strip()
        if not item or "=" not in item:
            raise ValueError(
                f"Invalid weather parameter {item!r}; expected k=v,k=v"
            )
        key, raw = (part.strip() for part in item.split("=", 1))
        if not key or not raw:
            raise ValueError(
                f"Invalid weather parameter {item!r}; expected k=v,k=v"
            )
        try:
            parsed[key] = float(raw)
        except ValueError as exc:
            raise ValueError(
                f"Weather parameter {key!r} must be a float, got {raw!r}"
            ) from exc
    return parsed


def condition_from_config(value: Any) -> EpisodeCondition:
    """Normalize a ``cfg.get('weather')`` value into an EpisodeCondition."""
    if value is None:
        return EpisodeCondition()
    if isinstance(value, str):
        name = value.strip()
        if not name:
            raise ValueError("weather preset name must not be empty")
        return EpisodeCondition(weather_preset=name)
    if not isinstance(value, Mapping):
        raise ValueError("weather must be a preset string or a mapping")
    if not value:
        raise ValueError(
            "weather mapping must not be empty (a bare 'weather: {}' would "
            "silently apply default-constructed weather)")

    raw = dict(value)
    if "preset" in raw or "params" in raw:
        unknown = sorted(set(raw) - {"preset", "params"})
        if unknown:
            raise ValueError(
                "weather mapping with preset/params has unknown keys: "
                + ", ".join(unknown)
            )
        preset = raw.get("preset")
        if preset is not None and (not isinstance(preset, str) or not preset.strip()):
            raise ValueError("weather preset must be a non-empty string or null")
        params = raw.get("params")
        if params is not None and not isinstance(params, Mapping):
            raise ValueError("weather params must be a mapping")
        return EpisodeCondition(
            weather_preset=preset.strip() if isinstance(preset, str) else None,
            weather_params=_float_params(params),
        )
    return EpisodeCondition(weather_params=_float_params(raw))


def merge_condition_config(
    cfg: Dict[str, Any],
    weather_preset: Optional[str] = None,
    weather_params: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Add the first-class weather key only when a condition was requested.

    A config that already carries a top-level ``weather`` key is an INTRINSIC
    scenario condition (e.g. night_signal_officer_conflict pins ClearNight as
    part of what the scenario tests). The sweep-wide CLI/env condition must
    never silently replace it: the pin wins and the requested condition is
    ignored for that episode, with a log line making the precedence visible.
    """
    if weather_preset is None and weather_params is None:
        return cfg
    # Truthiness, not mere presence: a degenerate 'weather: {}' / '' must not
    # silently veto a requested sweep condition (it still fails loudly on its
    # own via condition_from_config when no CLI condition is given).
    if cfg.get("weather"):
        log.info(
            "config pins an intrinsic weather condition (%r); ignoring the "
            "requested sweep condition (preset=%r, params=%r) for this episode",
            cfg.get("weather"), weather_preset, dict(weather_params or {}),
        )
        return cfg
    weather: Dict[str, Any] = {}
    if weather_preset is not None:
        weather["preset"] = weather_preset
    if weather_params is not None:
        weather["params"] = {str(k): float(v) for k, v in weather_params.items()}
    cfg["weather"] = weather
    return cfg


def resolve(condition: Optional[EpisodeCondition], carla: Any) -> Any:
    """Resolve a condition to WeatherParameters, or None for benchmark default."""
    if condition is None or (
        condition.weather_preset is None and condition.weather_params is None
    ):
        return None

    weather_type = carla.WeatherParameters
    if condition.weather_preset is not None:
        try:
            preset = getattr(weather_type, condition.weather_preset)
        except AttributeError as exc:
            raise _unknown_preset(condition.weather_preset, weather_type) from exc
        if not isinstance(preset, weather_type):
            raise _unknown_preset(condition.weather_preset, weather_type)
        weather = weather_type()
        for field in WEATHER_PARAMETER_FIELDS:
            if hasattr(preset, field):
                setattr(weather, field, float(getattr(preset, field)))
    else:
        weather = weather_type()

    params = condition.weather_params or {}
    unknown = sorted(set(params) - set(WEATHER_PARAMETER_FIELDS))
    if unknown:
        raise ValueError(
            "Unknown CARLA weather parameter(s): "
            + ", ".join(unknown)
            + ". Valid parameters: "
            + ", ".join(WEATHER_PARAMETER_FIELDS)
        )
    for key, value in params.items():
        setattr(weather, key, float(value))
    return weather


def describe(condition_or_weather: Any) -> Dict[str, Any]:
    """Return a deterministic, JSON-safe description of a condition/weather."""
    if isinstance(condition_or_weather, EpisodeCondition):
        return {
            "weather_preset": condition_or_weather.weather_preset,
            "weather_params": (
                None
                if condition_or_weather.weather_params is None
                else {
                    str(key): float(value)
                    for key, value in sorted(condition_or_weather.weather_params.items())
                }
            ),
        }
    if condition_or_weather is None:
        return {field: None for field in WEATHER_PARAMETER_FIELDS}
    return {
        field: _json_number(getattr(condition_or_weather, field, None))
        for field in WEATHER_PARAMETER_FIELDS
    }


def _float_params(value: Optional[Mapping[Any, Any]]) -> Optional[Dict[str, float]]:
    if value is None:
        return None
    result: Dict[str, float] = {}
    for key, raw in value.items():
        try:
            result[str(key)] = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Weather parameter {key!r} must be numeric, got {raw!r}"
            ) from exc
    return result


def _valid_presets(weather_type: Any) -> list[str]:
    presets = []
    for name in dir(weather_type):
        if name.startswith("_"):
            continue
        try:
            value = getattr(weather_type, name)
        except Exception:
            continue
        if isinstance(value, weather_type):
            presets.append(name)
    return sorted(presets)


def _unknown_preset(name: str, weather_type: Any) -> ValueError:
    valid = _valid_presets(weather_type)
    return ValueError(
        f"Unknown CARLA weather preset {name!r}. "
        f"Valid presets: {', '.join(valid) if valid else '(none found)'}"
    )


def _json_number(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


__all__ = [
    "EpisodeCondition",
    "REPORTED_GRID",
    "WEATHER_PARAMETER_FIELDS",
    "condition_from_config",
    "describe",
    "merge_condition_config",
    "parse_weather_params",
    "resolve",
]
