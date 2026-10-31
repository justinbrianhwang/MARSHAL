"""Minimal closed-loop probe of the InterFuser adapter outside the benchmark harness (diagnostic only).

    <interfuser env python> reports/scoring_validity_audit/interfuser_greenstop/scripts/adapter_probe.py \
        --station green_stop --ego vehicle.tesla.model3 --out <dir> [--seconds 12] [--overrides '{"...": ...}']
        [--spawn-z 3.0] [--settle-ticks 0] [--back-m 0]

Needs CARLA 0.9.16 with Town03 on 127.0.0.1:2000. Spawns the ego at the station (as the harness does: station x/y/yaw,
z lifted to --spawn-z), freezes every traffic light Green, runs the real InterFuserController (adapter defaults unless
--overrides) in synchronous 20 Hz with no other actors, applies its control every tick, and writes the adapter's
debug trace + verbose trace into --out, plus probe.json (pose after the run, travel, v_max, first time >= 5 km/h).
--settle-ticks lets the ego settle with handbrake before the controller starts (default 0 = harness-like rest start
from the lifted spawn).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import carla

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from marshal_bench.controllers.interfuser_model import InterFuserController  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", required=True)
    ap.add_argument("--ego", default="vehicle.tesla.model3")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--overrides", default="{}")
    ap.add_argument("--spawn-z", type=float, default=3.0)
    ap.add_argument("--settle-ticks", type=int, default=0)
    ap.add_argument("--back-m", type=float, default=0.0)
    ap.add_argument("--lead-ticks", type=int, default=0,
                    help="ticks between sensor setup and the first controller step (shifts the LiDAR half-sweep phase)")
    ap.add_argument("--weather", default="", help="carla.WeatherParameters preset, e.g. ClearNoon (default: leave as is)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    st = json.loads((ROOT / "marshal_bench/configs/stations.json").read_text(encoding="utf-8"))["stations"][a.station]
    yaw = math.radians(st["yaw"])
    x, y = st["x"] - a.back_m * math.cos(yaw), st["y"] - a.back_m * math.sin(yaw)
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(60)
    world = client.get_world()
    old = world.get_settings()
    s = world.get_settings()
    s.synchronous_mode, s.fixed_delta_seconds = True, 0.05
    world.apply_settings(s)
    if a.weather:
        world.set_weather(getattr(carla.WeatherParameters, a.weather))
    for tl in world.get_actors().filter("traffic.traffic_light"):
        tl.set_state(carla.TrafficLightState.Green)
        tl.freeze(True)
    ego = world.spawn_actor(world.get_blueprint_library().find(a.ego),
                            carla.Transform(carla.Location(x, y, a.spawn_z), carla.Rotation(yaw=st["yaw"])))
    ctl = None
    try:
        if a.settle_ticks:
            ego.apply_control(carla.VehicleControl(hand_brake=True, brake=1.0))
            for _ in range(a.settle_ticks):
                world.tick()
            ego.apply_control(carla.VehicleControl())
        world.tick()
        cfg = {"interfuser": {"debug_dir": str(out.resolve()), "verbose_trace": True, "max_debug_frames": 4,
                              "save_debug_every_n": 40, **json.loads(a.overrides)}}
        ctl = InterFuserController(cfg)
        ctl.setup(world, ego, {}, carla)
        for _ in range(a.lead_ticks):
            world.tick()
        start = ego.get_location()
        fwd0 = ego.get_transform().get_forward_vector()
        v_max, t5, travel, t = 0.0, None, 0.0, 0.0
        while t < a.seconds:
            world.tick()
            t += 0.05
            c = ctl.step({"sim_time": t}, 0.05)
            if c is not None:
                ego.apply_control(c)
            v = ego.get_velocity()
            kmh = 3.6 * math.hypot(v.x, v.y)
            v_max = max(v_max, kmh)
            if t5 is None and kmh >= 5.0:
                t5 = round(t, 2)
            d = ego.get_location() - start
            travel = max(travel, d.x * fwd0.x + d.y * fwd0.y)
        tf = ego.get_transform()
        res = {"station": a.station, "ego": a.ego, "overrides": json.loads(a.overrides), "spawn_z": a.spawn_z,
               "settle_ticks": a.settle_ticks, "back_m": a.back_m, "weather": a.weather or str(world.get_weather()), "lead_ticks": a.lead_ticks, "t_5kmh_s": t5, "v_max_kmh": round(v_max, 2),
               "travel_m": round(travel, 2), "final_pitch": round(tf.rotation.pitch, 3), "final_roll": round(tf.rotation.roll, 3)}
        (out / "probe.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(json.dumps(res))
    finally:
        if ctl is not None:
            ctl.teardown()
        ego.destroy()
        for tl in world.get_actors().filter("traffic.traffic_light"):
            tl.freeze(False)
        world.apply_settings(old)
        world.tick() if old.synchronous_mode else None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
