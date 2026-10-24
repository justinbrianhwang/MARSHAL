"""CARLA helpers for the evaluation/timing audit runs.

    python scripts/dac2027_carla_util.py load-town03
    python scripts/dac2027_carla_util.py world-check <tag>      # exit 3 if any walker or vehicle is left in the world
    python scripts/dac2027_carla_util.py export-map <out_dir>   # Town03.xodr + traffic-light geometry for offline tables
    python scripts/dac2027_carla_util.py episode-complete <ep>  # exit 0 if the episode finished and was scored (offline)

Needs a running CARLA 0.9.16 server on 127.0.0.1:2000.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def _client(timeout: float = 120.0):
    import carla  # imported lazily so episode-complete works without the CARLA package
    c = carla.Client("127.0.0.1", 2000)
    c.set_timeout(timeout)
    return c


def load_town03() -> int:
    c = _client()
    w = c.get_world() if "Town03" in c.get_world().get_map().name else c.load_world("Town03")
    print("map:", w.get_map().name)
    return 0


def world_check(tag: str) -> int:
    w = _client(30).get_world()
    acts = w.get_actors()
    walkers = [[a.id, a.type_id] for a in acts if a.type_id.startswith("walker.")]
    vehicles = [[a.id, a.type_id] for a in acts if a.type_id.startswith("vehicle.")]
    print(json.dumps({"t": time.strftime("%H:%M:%S"), "tag": tag, "map": w.get_map().name,
                      "walkers": walkers, "vehicles": vehicles}))
    return 3 if walkers or vehicles else 0


def export_map(out_dir: str) -> int:
    w = _client(60).get_world()
    m = w.get_map()
    if "Town03" not in m.name:
        print(f"expected Town03, got {m.name}")
        return 2
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "Town03.xodr").write_text(m.to_opendrive(), encoding="utf-8")
    lights = []
    for tl in w.get_actors().filter("traffic.traffic_light"):
        loc = tl.get_transform().location
        lights.append({
            "actor_id": tl.id, "opendrive_id": tl.get_opendrive_id(), "x": loc.x, "y": loc.y, "z": loc.z,
            "stop_waypoints": [{"x": wp.transform.location.x, "y": wp.transform.location.y,
                                "road_id": wp.road_id, "lane_id": wp.lane_id} for wp in tl.get_stop_waypoints()],
        })
    (out / "town03_traffic_lights.json").write_text(json.dumps({"map": m.name, "traffic_lights": lights}, indent=1))
    print(f"exported {out / 'Town03.xodr'} and {len(lights)} traffic lights")
    return 0


def episode_complete(ep_dir: str) -> int:
    """0 if the episode finished, was scored PASS/FAIL (not INVALID) and has telemetry; 1 otherwise (offline)."""
    ep = Path(ep_dir)
    try:
        events = json.loads((ep / "events.json").read_text(encoding="utf-8"))
        verdict = json.loads((ep / "strict_scoring.json").read_text(encoding="utf-8")).get("verdict")
        tel = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
        rows = tel.get("telemetry") if isinstance(tel, dict) else tel
    except (OSError, ValueError, AttributeError):
        return 1
    finished = isinstance(events, list) and any(isinstance(e, dict) and e.get("name") == "scenario_finished"
                                                for e in events)
    rows_ok = isinstance(rows, list) and bool(rows) and all(isinstance(r, dict) and "sim_time" in r for r in rows)
    return 0 if finished and verdict in ("PASS", "FAIL") and rows_ok else 1


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "load-town03":
        return load_town03()
    if cmd == "world-check":
        return world_check(rest[0] if rest else "")
    if cmd == "episode-complete":
        return episode_complete(rest[0]) if rest else 2
    if cmd == "export-map":
        return export_map(rest[0] if rest else "outputs/dac2027_audit/map")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
