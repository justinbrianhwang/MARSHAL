#!/usr/bin/env python
"""Measure Tesla Model 3 braking at the curated Town03 green-stop station."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402


FPS = 20.0
DT = 1.0 / FPS
V0_VALUES = (3.0, 5.0, 7.0, 10.0)
BRAKES = (0.5, 1.0)
REPEATS = 3
SETTLE_CONSECUTIVE_TICKS = 10
SETTLE_MAX_TICKS = 200
RUNUP_TIME_CAP_S = 20.0
GROUND_WAIT_TICKS = 20
GROUND_HEIGHT_TOLERANCE_M = 0.10
CSV_FIELDS = (
    "run_idx",
    "repeat",
    "v0_mps",
    "brake",
    "sim_time",
    "brake_elapsed_s",
    "horizontal_speed_mps",
    "vz_mps",
    "x",
    "y",
    "z",
    "distance_m",
    "throttle",
    "applied_brake",
    "steer",
    "hand_brake",
    "gear",
    "reverse",
    "manual_gear_shift",
    "distance_origin",
)
DISTANCE_ORIGIN = "position_at_brake_command_send_time"


def _speed(velocity: Any) -> float:
    return math.hypot(float(velocity.x), float(velocity.y))


def _station_transform(carla: Any) -> Any:
    payload = json.loads(
        (ROOT / "marshal_bench" / "configs" / "stations.json").read_text(
            encoding="utf-8"
        )
    )
    station = payload["stations"]["green_stop"]
    return carla.Transform(
        carla.Location(
            x=float(station["x"]), y=float(station["y"]), z=3.0
        ),
        carla.Rotation(yaw=float(station["yaw"])),
    )


def _settle(world: Any, ego: Any, carla: Any, transform: Any) -> int:
    ego.set_transform(transform)
    ego.set_target_velocity(carla.Vector3D())
    ego.set_target_angular_velocity(carla.Vector3D())
    ego.apply_control(
        carla.VehicleControl(throttle=0.0, brake=1.0, steer=0.0, hand_brake=False)
    )
    previous_z = float(ego.get_location().z)
    consecutive = 0
    for tick in range(1, SETTLE_MAX_TICKS + 1):
        world.tick()
        location = ego.get_location()
        vz = float(ego.get_velocity().z)
        dz = abs(float(location.z) - previous_z)
        consecutive = consecutive + 1 if abs(vz) < 0.05 and dz < 0.005 else 0
        previous_z = float(location.z)
        if consecutive >= SETTLE_CONSECUTIVE_TICKS:
            break
    ego.apply_control(
        carla.VehicleControl(throttle=0.0, brake=0.0, steer=0.0, hand_brake=False)
    )
    if consecutive < SETTLE_CONSECUTIVE_TICKS:
        raise RuntimeError(f"vehicle did not settle within {SETTLE_MAX_TICKS} ticks")
    return tick


def _reach_speed(
    world: Any,
    ego: Any,
    carla: Any,
    v0: float,
    prior_throttle: float | None,
    seed_velocity: float | None = None,
) -> tuple[bool, float, list[dict[str, float]]]:
    if prior_throttle is None:
        max_speed = 0.0
        for _ in range(5):
            forward = ego.get_transform().get_forward_vector()
            ego.set_target_velocity(
                carla.Vector3D(x=forward.x * v0, y=forward.y * v0, z=0.0)
            )
            world.tick()
            max_speed = max(max_speed, _speed(ego.get_velocity()))
        return True, max_speed, []

    prior_control = carla.VehicleControl(
        throttle=float(prior_throttle), brake=0.0, steer=0.0, hand_brake=False
    )
    trace: list[dict[str, float]] = []
    max_speed = 0.0
    for tick in range(1, int(RUNUP_TIME_CAP_S * FPS) + 1):
        if tick == 1 and seed_velocity is not None:
            forward = ego.get_transform().get_forward_vector()
            ego.set_target_velocity(
                carla.Vector3D(
                    x=forward.x * seed_velocity,
                    y=forward.y * seed_velocity,
                    z=0.0,
                )
            )
            ego.apply_control(
                carla.VehicleControl(throttle=0.70, brake=0.0, steer=0.0)
            )
        else:
            ego.apply_control(prior_control)
        world.tick()
        speed = _speed(ego.get_velocity())
        max_speed = max(max_speed, speed)
        trace.append(
            {
                "tick": tick,
                "runup_elapsed_s": tick * DT,
                "horizontal_speed_mps": speed,
            }
        )
        if speed >= v0:
            return True, max_speed, trace
    return False, max_speed, trace


def _runup_transform(world: Any, carla: Any, station: Any, back_m: float) -> Any:
    forward = station.get_forward_vector()
    candidate = carla.Transform(
        carla.Location(
            x=float(station.location.x) - float(forward.x) * back_m,
            y=float(station.location.y) - float(forward.y) * back_m,
            z=float(station.location.z),
        ),
        station.rotation,
    )
    waypoint = world.get_map().get_waypoint(
        candidate.location,
        project_to_road=False,
        lane_type=carla.LaneType.Driving,
    )
    if waypoint is not None:
        return candidate
    print(
        f"Run-up point {back_m:.1f} m behind station is not on a driving lane; "
        "falling back to the station spawn.",
        file=sys.stderr,
    )
    return station


def _run_brake(
    world: Any,
    ego: Any,
    carla: Any,
    run_idx: int,
    repeat: int,
    v0: float,
    brake: float,
    settled_height_above_road_m: float | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    height_above_road, vz_at_brake, grounded_at_brake = _ground_contact(
        world, ego, settled_height_above_road_m
    )
    throttle_control = ego.get_control()
    for _ in range(GROUND_WAIT_TICKS):
        if grounded_at_brake:
            break
        ego.apply_control(throttle_control)
        world.tick()
        height_above_road, vz_at_brake, grounded_at_brake = _ground_contact(
            world, ego, settled_height_above_road_m
        )
    control = carla.VehicleControl(
        throttle=0.0, brake=float(brake), steer=0.0, hand_brake=False
    )
    start = ego.get_location()
    previous = start
    previous_speed = _speed(ego.get_velocity())
    initial_speed = previous_speed
    brake_command_sim_time = float(
        world.get_snapshot().timestamp.elapsed_seconds
    )
    speed_at_brake_command_mps = previous_speed
    first_applied_brake_sim_time = None
    first_applied_brake_value = None
    distance = 0.0
    fast_decels: list[float] = []
    rows: list[dict[str, Any]] = []

    for tick in range(1, int(6.0 * FPS) + 1):
        ego.apply_control(control)
        world.tick()
        snapshot = world.get_snapshot()
        velocity = ego.get_velocity()
        location = ego.get_location()
        applied = ego.get_control()
        if tick == 1:
            first_applied_brake_sim_time = float(
                snapshot.timestamp.elapsed_seconds
            )
            first_applied_brake_value = float(applied.brake)
        speed = _speed(velocity)
        distance += math.hypot(location.x - previous.x, location.y - previous.y)
        if previous_speed > 2.5:
            fast_decels.append((previous_speed - speed) / DT)
        elapsed = tick * DT
        rows.append(
            {
                "run_idx": run_idx,
                "repeat": repeat,
                "v0_mps": v0,
                "brake": brake,
                "sim_time": float(snapshot.timestamp.elapsed_seconds),
                "brake_elapsed_s": elapsed,
                "horizontal_speed_mps": speed,
                "vz_mps": float(velocity.z),
                "x": float(location.x),
                "y": float(location.y),
                "z": float(location.z),
                "distance_m": distance,
                "throttle": float(applied.throttle),
                "applied_brake": float(applied.brake),
                "steer": float(applied.steer),
                "hand_brake": bool(applied.hand_brake),
                "gear": int(applied.gear),
                "reverse": bool(applied.reverse),
                "manual_gear_shift": bool(applied.manual_gear_shift),
                "distance_origin": DISTANCE_ORIGIN,
            }
        )
        previous = location
        previous_speed = speed
        if speed < 0.05:
            break

    elapsed = rows[-1]["brake_elapsed_s"]
    final_speed = rows[-1]["horizontal_speed_mps"]
    summary = {
        "run_idx": run_idx,
        "repeat": repeat,
        "v0_mps": v0,
        "brake": brake,
        "initial_speed_mps": initial_speed,
        "final_speed_mps": final_speed,
        "stopping_distance_m": distance,
        "stopping_time_s": elapsed,
        "mean_decel_mps2": (initial_speed - final_speed) / elapsed,
        "mean_decel_above_2_5_mps2": (
            sum(fast_decels) / len(fast_decels) if fast_decels else None
        ),
        "stopped": final_speed < 0.05,
        "height_above_road_m": height_above_road,
        "vz_at_brake_mps": vz_at_brake,
        "grounded_at_brake": grounded_at_brake,
        "brake_command_sim_time": brake_command_sim_time,
        "speed_at_brake_command_mps": speed_at_brake_command_mps,
        "first_applied_brake_sim_time": first_applied_brake_sim_time,
        "first_applied_brake_value": first_applied_brake_value,
    }
    return rows, summary


def _height_above_road(world: Any, ego: Any) -> float | None:
    location = ego.get_location()
    waypoint = world.get_map().get_waypoint(location, project_to_road=True)
    if waypoint is None:
        return None
    return float(location.z) - float(waypoint.transform.location.z)


def _ground_contact(
    world: Any,
    ego: Any,
    settled_height_above_road_m: float | None,
) -> tuple[float | None, float, bool]:
    velocity = ego.get_velocity()
    vz = float(velocity.z)
    horizontal_speed = _speed(velocity)
    height_above_road = _height_above_road(world, ego)
    grounded = (
        height_above_road is not None
        and settled_height_above_road_m is not None
        and abs(height_above_road - settled_height_above_road_m)
        <= GROUND_HEIGHT_TOLERANCE_M
        and abs(vz) <= max(0.1, 0.15 * horizontal_speed)
    )
    return height_above_road, vz, grounded


def _unreached_summary(
    run_idx: int,
    repeat: int,
    v0: float,
    brake: float,
    max_speed: float,
) -> dict[str, Any]:
    return {
        "run_idx": run_idx,
        "repeat": repeat,
        "v0_mps": v0,
        "brake": brake,
        "initial_speed_mps": max_speed,
        "final_speed_mps": max_speed,
        "stopping_distance_m": None,
        "stopping_time_s": None,
        "mean_decel_mps2": None,
        "mean_decel_above_2_5_mps2": None,
        "stopped": False,
        "height_above_road_m": None,
        "vz_at_brake_mps": None,
        "grounded_at_brake": False,
        "brake_command_sim_time": None,
        "speed_at_brake_command_mps": None,
        "first_applied_brake_sim_time": None,
        "first_applied_brake_value": None,
    }


def _write_outputs(
    out_dir: Path,
    rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    prior_throttle: float | None,
    seed_velocity: float | None = None,
    *,
    speeds: tuple[float, ...] = V0_VALUES,
    brakes: tuple[float, ...] = BRAKES,
    repeats: int = REPEATS,
    runup_back_m: float = 40.0,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "brake_calibration.csv"
    csv_tmp = csv_path.with_suffix(".csv.tmp")
    with csv_tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    csv_tmp.replace(csv_path)
    payload = {
        "town": "Town03",
        "vehicle": "vehicle.tesla.model3",
        "fps": FPS,
        "prior_throttle": prior_throttle,
        "seed_velocity_mps": seed_velocity,
        "speeds": list(speeds),
        "brakes": list(brakes),
        "repeats": repeats,
        "runup_back_m": runup_back_m,
        "distance_origin": DISTANCE_ORIGIN,
        "runs": summaries,
    }
    summary_path = out_dir / "brake_calibration_summary.json"
    summary_tmp = summary_path.with_suffix(".json.tmp")
    summary_tmp.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    summary_tmp.replace(summary_path)


def _float_list(value: str) -> tuple[float, ...]:
    try:
        values = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a comma-separated list of numbers") from exc
    if not values:
        raise argparse.ArgumentTypeError("must contain at least one number")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--prior-throttle", type=float, default=None)
    parser.add_argument("--seed-velocity", type=float, default=None)
    parser.add_argument("--runup-back-m", type=float, default=40.0)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--speeds", type=_float_list, default=V0_VALUES)
    parser.add_argument("--brakes", type=_float_list, default=BRAKES)
    args = parser.parse_args()
    if args.prior_throttle is not None and not 0.0 <= args.prior_throttle <= 1.0:
        parser.error("--prior-throttle must be between 0 and 1")
    if args.seed_velocity is not None and args.prior_throttle is None:
        parser.error("--seed-velocity requires --prior-throttle")
    if args.seed_velocity is not None and args.seed_velocity < 0.0:
        parser.error("--seed-velocity must be non-negative")
    if args.runup_back_m < 0.0:
        parser.error("--runup-back-m must be non-negative")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if any(speed <= 0.0 for speed in args.speeds):
        parser.error("--speeds values must be positive")
    if any(not 0.0 <= brake <= 1.0 for brake in args.brakes):
        parser.error("--brakes values must be between 0 and 1")

    carla = import_carla()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(10.0)
    world = client.get_world()
    town = world.get_map().name.rsplit("/", 1)[-1]
    if town.lower() != "town03":
        raise RuntimeError(f"CARLA is on {town!r}; expected Town03")

    original_settings = world.get_settings()
    calibration_settings = world.get_settings()
    calibration_settings.synchronous_mode = True
    calibration_settings.fixed_delta_seconds = DT
    ego = None
    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    try:
        world.apply_settings(calibration_settings)
        station_transform = _station_transform(carla)
        transform = (
            _runup_transform(world, carla, station_transform, args.runup_back_m)
            if args.prior_throttle is not None
            else station_transform
        )
        blueprint = world.get_blueprint_library().find("vehicle.tesla.model3")
        ego = world.spawn_actor(blueprint, transform)
        run_idx = 0
        _write_outputs(
            args.out,
            rows,
            summaries,
            args.prior_throttle,
            args.seed_velocity,
            speeds=args.speeds,
            brakes=args.brakes,
            repeats=args.repeats,
            runup_back_m=args.runup_back_m,
        )
        for v0 in args.speeds:
            for brake in args.brakes:
                for repeat in range(1, args.repeats + 1):
                    run_idx += 1
                    settle_ticks = _settle(world, ego, carla, transform)
                    settled_height_above_road_m = _height_above_road(world, ego)
                    reached, max_speed, runup_trace = _reach_speed(
                        world,
                        ego,
                        carla,
                        v0,
                        args.prior_throttle,
                        args.seed_velocity,
                    )
                    if reached:
                        run_rows, summary = _run_brake(
                            world,
                            ego,
                            carla,
                            run_idx,
                            repeat,
                            v0,
                            brake,
                            settled_height_above_road_m,
                        )
                        rows.extend(run_rows)
                    else:
                        summary = _unreached_summary(
                            run_idx, repeat, v0, brake, max_speed
                        )
                        print(
                            f"Run {run_idx}: reached {max_speed:.3f} m/s, below "
                            f"target {v0:.1f} m/s; skipping braking.",
                            file=sys.stderr,
                        )
                    summary.update(
                        {
                            "settle_ticks": settle_ticks,
                            "reached": reached,
                            "max_speed_reached_mps": max_speed,
                            "runup_speed_trace": runup_trace,
                            "seed_velocity_mps": args.seed_velocity,
                        }
                    )
                    summaries.append(summary)
                    _write_outputs(
                        args.out,
                        rows,
                        summaries,
                        args.prior_throttle,
                        args.seed_velocity,
                        speeds=args.speeds,
                        brakes=args.brakes,
                        repeats=args.repeats,
                        runup_back_m=args.runup_back_m,
                    )
        print(f"Wrote {args.out / 'brake_calibration.csv'}")
        print(f"Wrote {args.out / 'brake_calibration_summary.json'}")
        return 0
    finally:
        if ego is not None:
            try:
                ego.destroy()
            except Exception:
                pass
        try:
            world.apply_settings(original_settings)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
