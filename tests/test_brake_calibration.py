from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "_brake_calibration", ROOT / "scripts" / "_brake_calibration.py"
)
assert SPEC is not None and SPEC.loader is not None
brake_calibration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brake_calibration)


class FakeCarla:
    class Vector3D:
        def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
            self.x, self.y, self.z = x, y, z

    class VehicleControl:
        def __init__(self, **values: object):
            self.__dict__.update(
                throttle=0.0,
                brake=0.0,
                steer=0.0,
                hand_brake=False,
                gear=0,
                reverse=False,
                manual_gear_shift=False,
            )
            self.__dict__.update(values)

    class Location:
        def __init__(self, x: float, y: float, z: float):
            self.x, self.y, self.z = x, y, z

    class Transform:
        def __init__(self, location: object, rotation: object):
            self.location, self.rotation = location, rotation

    class LaneType:
        Driving = "driving"


class FakeVehicle:
    def __init__(self) -> None:
        self.location = SimpleNamespace(x=0.0, y=0.0, z=3.0)
        self.velocity = FakeCarla.Vector3D()
        self.transform = SimpleNamespace(
            get_forward_vector=lambda: FakeCarla.Vector3D(x=1.0)
        )
        self.target_velocities: list[object] = []
        self.controls: list[object] = []

    def set_transform(self, transform: object) -> None:
        self.location = SimpleNamespace(
            x=transform.location.x, y=transform.location.y, z=transform.location.z
        )

    def set_target_velocity(self, velocity: object) -> None:
        self.target_velocities.append(
            FakeCarla.Vector3D(x=velocity.x, y=velocity.y, z=velocity.z)
        )
        self.velocity = velocity

    def set_target_angular_velocity(self, _velocity: object) -> None:
        pass

    def apply_control(self, control: object) -> None:
        self.controls.append(control)

    def get_location(self) -> object:
        return SimpleNamespace(
            x=self.location.x, y=self.location.y, z=self.location.z
        )

    def get_velocity(self) -> object:
        return self.velocity

    def get_transform(self) -> object:
        return self.transform


def test_settle_requires_ten_consecutive_grounded_ticks() -> None:
    vehicle = FakeVehicle()
    samples = [(2.0, -1.0), (1.0, -1.0)] + [(1.0, 0.0)] * 10

    class World:
        def __init__(self) -> None:
            self.ticks = 0

        def tick(self) -> None:
            z, vz = samples[self.ticks]
            vehicle.location.z = z
            vehicle.velocity.z = vz
            self.ticks += 1

    transform = SimpleNamespace(location=SimpleNamespace(x=0.0, y=0.0, z=3.0))
    world = World()

    assert brake_calibration._settle(world, vehicle, FakeCarla, transform) == 12
    assert world.ticks == 12


def test_reach_speed_timeout_returns_trace_and_maximum() -> None:
    vehicle = FakeVehicle()

    class World:
        def tick(self) -> None:
            vehicle.velocity.x = min(vehicle.velocity.x + 0.0005, 0.2)

    reached, max_speed, trace = brake_calibration._reach_speed(
        World(), vehicle, FakeCarla, v0=5.0, prior_throttle=0.65
    )

    assert reached is False
    assert max_speed == 0.2
    assert len(trace) == int(
        brake_calibration.RUNUP_TIME_CAP_S * brake_calibration.FPS
    )
    assert trace[-1]["horizontal_speed_mps"] == max_speed


def test_reach_speed_seed_matches_oracle_first_tick_then_uses_prior_throttle() -> None:
    vehicle = FakeVehicle()

    class World:
        def tick(self) -> None:
            vehicle.velocity.x += 0.1

    reached, max_speed, trace = brake_calibration._reach_speed(
        World(),
        vehicle,
        FakeCarla,
        v0=2.0,
        prior_throttle=0.65,
        seed_velocity=1.8,
    )

    assert reached is True
    assert max_speed == 2.0
    assert len(vehicle.target_velocities) == 1
    assert vehicle.target_velocities[0].x == 1.8
    assert [control.throttle for control in vehicle.controls] == [0.70, 0.65]
    assert trace[-1]["horizontal_speed_mps"] == 2.0


def test_slope_vertical_speed_is_treated_as_grounded() -> None:
    vehicle = FakeVehicle()
    vehicle.location.z = 3.5
    vehicle.velocity.x = 10.0
    vehicle.velocity.z = 1.0
    waypoint = SimpleNamespace(
        transform=SimpleNamespace(location=SimpleNamespace(z=2.5))
    )
    world = SimpleNamespace(
        get_map=lambda: SimpleNamespace(get_waypoint=lambda *args, **kwargs: waypoint)
    )

    height, vz, grounded = brake_calibration._ground_contact(
        world, vehicle, settled_height_above_road_m=1.0
    )

    assert height == 1.0
    assert vz == 1.0
    assert grounded is True


def test_runup_transform_falls_back_when_point_is_not_driving_lane() -> None:
    station = SimpleNamespace(
        location=FakeCarla.Location(x=10.0, y=20.0, z=3.0),
        rotation=object(),
        get_forward_vector=lambda: FakeCarla.Vector3D(x=0.0, y=1.0),
    )
    map_ = SimpleNamespace(get_waypoint=lambda *args, **kwargs: None)
    world = SimpleNamespace(get_map=lambda: map_)

    assert brake_calibration._runup_transform(world, FakeCarla, station, 40.0) is station


def test_run_brake_records_command_and_first_applied_timing() -> None:
    class Vehicle(FakeVehicle):
        def __init__(self) -> None:
            super().__init__()
            self.velocity.x = 3.0
            self.control = FakeCarla.VehicleControl(throttle=0.65)

        def apply_control(self, control: object) -> None:
            super().apply_control(control)
            self.control = control

        def get_control(self) -> object:
            return self.control

    vehicle = Vehicle()
    waypoint = SimpleNamespace(
        transform=SimpleNamespace(location=SimpleNamespace(z=2.0))
    )

    class World:
        def __init__(self) -> None:
            self.elapsed = 10.0

        def get_map(self) -> object:
            return SimpleNamespace(get_waypoint=lambda *args, **kwargs: waypoint)

        def get_snapshot(self) -> object:
            return SimpleNamespace(
                timestamp=SimpleNamespace(elapsed_seconds=self.elapsed)
            )

        def tick(self) -> None:
            self.elapsed += brake_calibration.DT
            vehicle.location.x += 0.15
            vehicle.velocity.x = 0.0

    rows, summary = brake_calibration._run_brake(
        World(), vehicle, FakeCarla, 1, 1, 3.0, 0.5, 1.0
    )

    assert summary["brake_command_sim_time"] == 10.0
    assert summary["speed_at_brake_command_mps"] == 3.0
    assert summary["first_applied_brake_sim_time"] == 10.05
    assert summary["first_applied_brake_value"] == 0.5
    assert rows[0]["distance_m"] == 0.15
    assert rows[0]["distance_origin"] == brake_calibration.DISTANCE_ORIGIN


def test_empty_incremental_outputs_keep_cli_values_and_csv_header(
    tmp_path: Path,
) -> None:
    brake_calibration._write_outputs(
        tmp_path,
        [],
        [],
        prior_throttle=0.65,
        seed_velocity=1.8,
        speeds=(2.25, 4.9),
        brakes=(0.3, 0.7),
    )

    with (tmp_path / "brake_calibration.csv").open(
        encoding="utf-8", newline=""
    ) as fh:
        assert next(csv.reader(fh)) == list(brake_calibration.CSV_FIELDS)
    payload = json.loads(
        (tmp_path / "brake_calibration_summary.json").read_text(encoding="utf-8")
    )
    assert payload["runs"] == []
    assert payload["prior_throttle"] == 0.65
    assert payload["seed_velocity_mps"] == 1.8
    assert payload["speeds"] == [2.25, 4.9]
    assert payload["brakes"] == [0.3, 0.7]
    assert payload["distance_origin"] == "position_at_brake_command_send_time"


def test_float_list_preserves_non_integer_speeds() -> None:
    assert brake_calibration._float_list("2.25,4.9") == (2.25, 4.9)
