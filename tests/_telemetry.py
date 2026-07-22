"""Small pure-python telemetry builders for MARSHAL scorer tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _series(value: Any, n: int) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != n:
            raise ValueError(f"series length {len(value)} does not match row count {n}")
        return list(value)
    return [value for _ in range(n)]


def _row_count(times: Sequence[float] | None, *values: Any) -> int:
    if times is not None:
        return len(times)
    for value in values:
        if value is None:
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return len(value)
    return 7


def make_rows(
    *,
    times: Sequence[float] | None = None,
    speeds: Any = 0.0,
    ego_x: Any | None = None,
    ego_y: Any = 0.0,
    in_junction: Any = False,
    distance_to_officer_m: Any = 10.0,
    distance_to_stopline_m: Any = 20.0,
    ego_forward_m: Any | None = None,
    ego_lateral_m: Any = 0.0,
    collision_count: Any = 0,
    officer_gesture_id: Any = "STOP",
    officer_onset_time: float = 0.0,
    officer_duration_sec: float = 10.0,
    officer_active: Any = True,
    control_finite: Any = True,
    distance_to_hazard_m: Any = 50.0,
    hazard_forward_m: Any = 20.0,
    stopline_forward_m: Any | None = None,
) -> list[dict[str, Any]]:
    """Build rows with every strict/graded telemetry field populated."""

    n = _row_count(
        times,
        speeds,
        ego_x,
        ego_forward_m,
        distance_to_stopline_m,
        ego_lateral_m,
        in_junction,
        collision_count,
    )
    ts = list(times) if times is not None else [float(i) for i in range(n)]
    forward = ego_forward_m if ego_forward_m is not None else [float(i) * 3.0 for i in range(n)]
    x_values = ego_x if ego_x is not None else forward

    values = {
        "ego_speed_kmh": _series(speeds, n),
        "ego_x": _series(x_values, n),
        "ego_y": _series(ego_y, n),
        "in_junction": _series(in_junction, n),
        "distance_to_officer_m": _series(distance_to_officer_m, n),
        "distance_to_stopline_m": _series(distance_to_stopline_m, n),
        "ego_forward_m": _series(forward, n),
        "ego_lateral_m": _series(ego_lateral_m, n),
        "collision_count": _series(collision_count, n),
        "officer_gesture_id": _series(officer_gesture_id, n),
        "officer_active": _series(officer_active, n),
        "control_finite": _series(control_finite, n),
        "distance_to_hazard_m": _series(distance_to_hazard_m, n),
        "hazard_forward_m": _series(hazard_forward_m, n),
        "stopline_forward_m": _series(stopline_forward_m, n),
    }

    rows: list[dict[str, Any]] = []
    for idx, sim_time in enumerate(ts):
        rows.append(
            {
                "sim_time": sim_time,
                "ego_speed_kmh": values["ego_speed_kmh"][idx],
                "ego_x": values["ego_x"][idx],
                "ego_y": values["ego_y"][idx],
                "in_junction": values["in_junction"][idx],
                "distance_to_officer_m": values["distance_to_officer_m"][idx],
                "distance_to_stopline_m": values["distance_to_stopline_m"][idx],
                "ego_forward_m": values["ego_forward_m"][idx],
                "ego_lateral_m": values["ego_lateral_m"][idx],
                "collision_count": values["collision_count"][idx],
                "officer_gesture_id": values["officer_gesture_id"][idx],
                "officer_onset_time": officer_onset_time,
                "officer_duration_sec": officer_duration_sec,
                "officer_active": values["officer_active"][idx],
                "control_finite": values["control_finite"][idx],
                "distance_to_hazard_m": values["distance_to_hazard_m"][idx],
                "hazard_forward_m": values["hazard_forward_m"][idx],
                "stopline_forward_m": values["stopline_forward_m"][idx],
            }
        )
    return rows


def clean_stop_before_line() -> list[dict[str, Any]]:
    return make_rows(
        speeds=[12.0, 8.0, 3.0, 0.5, 0.2, 0.0, 0.0],
        distance_to_stopline_m=[35.0, 25.0, 15.0, 8.0, 4.0, 3.0, 3.0],
        ego_forward_m=[0.0, 8.0, 16.0, 23.0, 27.0, 28.0, 28.0],
        officer_gesture_id="STOP",
    )


def clean_proceed_through_junction() -> list[dict[str, Any]]:
    return make_rows(
        speeds=[4.0, 7.0, 10.0, 12.0, 12.0],
        distance_to_stopline_m=[12.0, 5.0, 0.5, -3.0, -8.0],
        ego_forward_m=[0.0, 6.0, 12.0, 18.0, 25.0],
        in_junction=[False, False, True, True, False],
        officer_gesture_id="PROCEED",
    )


def clean_detour_around_obstacle() -> list[dict[str, Any]]:
    return make_rows(
        speeds=[5.0, 6.0, 7.0, 7.0, 6.0, 5.0],
        distance_to_stopline_m=[30.0, 25.0, 20.0, 15.0, 10.0, 5.0],
        ego_forward_m=[0.0, 4.0, 8.0, 13.0, 17.0, 21.0],
        ego_lateral_m=[0.0, 0.4, 1.6, 2.1, 1.9, 0.8],
        hazard_forward_m=[12.0] * 6,
        distance_to_hazard_m=[18.0, 14.0, 10.0, 7.0, 4.0, 2.0],
        officer_gesture_id="DETOUR",
    )


def clean_yield_then_resume() -> list[dict[str, Any]]:
    return make_rows(
        speeds=[8.0, 6.0, 2.5, 1.5, 5.5, 8.0],
        distance_to_stopline_m=[40.0, 36.0, 33.0, 32.0, 28.0, 22.0],
        ego_forward_m=[0.0, 5.0, 8.0, 9.0, 13.0, 19.0],
        ego_lateral_m=[0.0, 0.0, 0.2, 1.2, 1.4, 1.0],
        officer_gesture_id="YIELD",
    )


def clean_rule_hierarchy_proceed_with_care() -> list[dict[str, Any]]:
    return make_rows(
        speeds=[8.0, 6.0, 2.5, 2.0, 5.0, 8.0],
        distance_to_stopline_m=[35.0, 28.0, 22.0, 15.0, 5.0, -4.0],
        ego_forward_m=[0.0, 5.0, 8.0, 10.0, 17.0, 25.0],
        in_junction=[False, False, False, False, True, True],
        distance_to_hazard_m=[25.0, 16.0, 14.0, 10.0, 20.0, 30.0],
        hazard_forward_m=[18.0] * 6,
        officer_gesture_id="PROCEED",
    )


def copy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
