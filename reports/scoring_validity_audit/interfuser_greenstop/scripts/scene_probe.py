"""Scene probe at InterFuser A->B stations (needs a running CARLA 0.9.16 server with Town03 on 127.0.0.1:2000).

    python reports/scoring_validity_audit/interfuser_greenstop/scripts/scene_probe.py --out <dir> [--ego vehicle.tesla.model3]

For each station: spawn the ego exactly as the harness does (station transform, z lifted to 3.0 m), synchronous 20 Hz,
let it settle for 3 s, then record
  * the settled ego pose (z, pitch, roll, yaw) and the ground clearance of the camera mount,
  * every environment object (map mesh: building, pole, static car, ...) and every actor whose bounding box centre lies
    within 25 m, as (label, x right m, y forward m, size) in the ego frame of InterFuser's object grid,
  * RGB + semantic-segmentation images from the three InterFuser camera poses (same size / FOV / mount), and the
    per-class pixel share of each semantic image.
Writes <out>/<station>/... and <out>/scene_probe.json. Destroys everything it spawned.
"""
from __future__ import annotations

import argparse
import json
import math
import queue
from pathlib import Path

import carla
import numpy as np

STATIONS_FILE = Path("marshal_bench/configs/stations.json")
CAMS = {"rgb": (800, 600, 0.0), "rgb_left": (400, 300, -60.0), "rgb_right": (400, 300, 60.0)}  # InterFuser spec
SEM = {0: "unlabeled", 1: "road", 2: "sidewalk", 3: "building", 4: "wall", 5: "fence", 6: "pole", 7: "traffic_light",
       8: "traffic_sign", 9: "vegetation", 10: "terrain", 11: "sky", 12: "pedestrian", 13: "rider", 14: "car",
       15: "truck", 16: "bus", 17: "train", 18: "motorcycle", 19: "bicycle", 20: "static", 21: "dynamic", 22: "other",
       23: "water", 24: "roadline", 25: "ground", 26: "bridge", 27: "railtrack", 28: "guardrail"}


def ego_frame(ego_tf, loc):
    """(x right, y forward) in metres, InterFuser object-grid convention."""
    d = loc - ego_tf.location
    f, r = ego_tf.get_forward_vector(), ego_tf.get_right_vector()
    return d.x * r.x + d.y * r.y, d.x * f.x + d.y * f.y


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--ego", default="vehicle.tesla.model3")
    ap.add_argument("--stations", nargs="*", default=["green_stop", "signal_off", "ambiguous_gesture",
                                                       "fake_vest_director", "red_proceed", "unauthorized_go"])
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stations = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))["stations"]
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(60)
    world = client.get_world()
    old = world.get_settings()
    s = world.get_settings()
    s.synchronous_mode, s.fixed_delta_seconds = True, 0.05
    world.apply_settings(s)
    bp = world.get_blueprint_library()
    env = world.get_environment_objects(carla.CityObjectLabel.Any)
    report = {"ego_blueprint": a.ego, "stations": {}}
    try:
        for name in a.stations:
            st = stations[name]
            tf = carla.Transform(carla.Location(st["x"], st["y"], max(float(st.get("z", 0.5)), 3.0)),
                                 carla.Rotation(yaw=st["yaw"]))
            ego = world.spawn_actor(bp.find(a.ego), tf)
            spawned = [ego]
            sensors = {}
            for cid, (w, h, yaw) in CAMS.items():
                for kind in ("rgb", "semantic_segmentation"):
                    cbp = bp.find(f"sensor.camera.{kind}")
                    cbp.set_attribute("image_size_x", str(w))
                    cbp.set_attribute("image_size_y", str(h))
                    cbp.set_attribute("fov", "100")
                    cam = world.spawn_actor(cbp, carla.Transform(carla.Location(1.3, 0, 2.3), carla.Rotation(yaw=yaw)),
                                            attach_to=ego)
                    q = queue.Queue()
                    cam.listen(q.put)
                    sensors[(cid, kind)] = q
                    spawned.append(cam)
            for _ in range(60):  # 3 s settle
                world.tick()
            frame = world.tick()
            sd = out / name
            sd.mkdir(exist_ok=True)
            sem_share = {}
            for (cid, kind), q in sensors.items():
                img = None
                while True:
                    img = q.get(timeout=10)
                    if img.frame >= frame:
                        break
                arr = np.frombuffer(img.raw_data, dtype=np.uint8).reshape(img.height, img.width, 4)
                if kind == "rgb":
                    from PIL import Image
                    Image.fromarray(arr[:, :, 2::-1].copy()).save(sd / f"{cid}.png")
                else:
                    tags = arr[:, :, 2]
                    from PIL import Image
                    Image.fromarray((tags * 9).astype(np.uint8)).save(sd / f"{cid}_semantic.png")
                    u, c = np.unique(tags, return_counts=True)
                    sem_share[cid] = {SEM.get(int(k), str(k)): round(float(n) / tags.size, 4) for k, n in zip(u, c)}
            etf = ego.get_transform()
            near = []
            for o in env:
                x, y = ego_frame(etf, o.bounding_box.location)
                if math.hypot(x, y) <= 25:
                    e = o.bounding_box.extent
                    near.append({"kind": "env", "label": str(o.type), "name": o.name, "x_right": round(x, 2),
                                 "y_fwd": round(y, 2), "z": round(o.bounding_box.location.z - etf.location.z, 2),
                                 "extent": [round(e.x, 2), round(e.y, 2), round(e.z, 2)]})
            for act in world.get_actors():
                if act.id in {x.id for x in spawned} or act.type_id.startswith(("spectator", "sensor", "traffic.")):
                    continue
                x, y = ego_frame(etf, act.get_location())
                if math.hypot(x, y) <= 25:
                    near.append({"kind": "actor", "label": act.type_id, "id": act.id, "x_right": round(x, 2),
                                 "y_fwd": round(y, 2)})
            near.sort(key=lambda d: math.hypot(d["x_right"], d["y_fwd"]))
            wp = world.get_map().get_waypoint(etf.location)
            report["stations"][name] = {
                "spawn": st, "settled": {"x": round(etf.location.x, 3), "y": round(etf.location.y, 3),
                                         "z": round(etf.location.z, 3), "pitch": round(etf.rotation.pitch, 3),
                                         "roll": round(etf.rotation.roll, 3), "yaw": round(etf.rotation.yaw, 3)},
                "lane": {"road": wp.road_id, "lane": wp.lane_id, "lane_width": round(wp.lane_width, 2),
                         "right_lane_marking": str(wp.right_lane_marking.type),
                         "offset_from_centre": round(ego_frame(etf, wp.transform.location)[0], 2)},
                "semantic_share": sem_share, "near": near}
            for x in reversed(spawned):
                x.destroy()
            world.tick()
    finally:
        world.apply_settings(old)
    (out / "scene_probe.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    for n, r in report["stations"].items():
        print(n, r["settled"], r["lane"], "near(<=10 m):",
              [(d["label"], d["x_right"], d["y_fwd"]) for d in r["near"] if math.hypot(d["x_right"], d["y_fwd"]) <= 10][:12])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
