"""Offline input ablation for InterFuser (no CARLA): which input produces the object-map cells that block the ego.

    <interfuser env python> reports/scoring_validity_audit/interfuser_debug/input_ablation.py <model_input_stepNNNN.pt> [...]

Loads the checkpoint the adapter uses, runs the network on a saved adapter input (written by the adapter's debug-only
verbose trace) and on single-input variants, and prints the raw object-map cells with probability > 0.4 (as
(x lateral m, y forward m) in the render.py grid), the light/stop/junction heads, and the native controller's d_0 /
d_1.5 safe distances on the variant's own waypoints (fresh controller, no tracker smoothing).
"""
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from marshal_bench.controllers.interfuser_model import InterFuserController  # noqa: E402

ctl = InterFuserController({})
ctl._prepare_import_paths()
ctl._load_model_stack()
from team_code.interfuser_controller import downsample_waypoints, get_max_safe_distance  # noqa: E402


def run(inp):
    with torch.no_grad():
        meta, wps, junc, light, stop, _ = ctl.net({k: v.to(ctl.device) for k, v in inp.items()})
    meta = meta.cpu().numpy()[0]
    wps = wps.cpu().numpy()[0]
    sm = lambda t: float(torch.softmax(t, 1).cpu().numpy().reshape(-1)[0])  # noqa: E731
    grid = meta[:, 0].reshape(20, 20)
    cells = [(float(j) - 9.5, 17.5 - float(i), round(float(p), 2)) for (i, j), p in np.ndenumerate(grid) if p > 0.4]
    dw = downsample_waypoints(wps)
    d = {t: get_max_safe_distance(meta, dw, t=t, collision_buffer=np.array([2.5, 1.2]), threshold=0.04)
         for t in (0, 1.5)}
    return dict(n=len(cells), near=sum(1 for x, y, _ in cells if abs(x) <= 8 and -2 <= y <= 3),
                junction=round(sm(junc), 3), red=round(sm(light), 3), not_stop=round(sm(stop), 3),
                d0=round(float(d[0]), 2), d15=round(float(d[1.5]), 2),
                cells=sorted(cells, key=lambda c: -c[2])[:6])


for path in sys.argv[1:]:
    base = torch.load(path)
    print("==", Path(path).name)
    variants = {"as recorded": {}}
    variants["lidar zero"] = {"lidar": torch.zeros_like(base["lidar"])}
    variants["lidar mirrored (lateral)"] = {"lidar": torch.flip(base["lidar"], dims=[2])}
    for cam in ("rgb", "rgb_left", "rgb_right", "rgb_center"):
        variants[f"{cam} blank (mean grey)"] = {cam: torch.zeros_like(base[cam])}
    variants["left<->right cameras swapped"] = {"rgb_left": base["rgb_right"], "rgb_right": base["rgb_left"]}
    m = base["measurements"].clone(); m[0, 6] = 4.0
    variants["speed 4 m/s"] = {"measurements": m}
    variants["target (0,-50)"] = {"target_point": torch.tensor([[0.0, -50.0]])}
    for name, change in variants.items():
        inp = dict(base, **change)
        r = run(inp)
        print(f"  {name:32s} n={r['n']:3d} near={r['near']:3d} d0={r['d0']:5.2f} d1.5={r['d15']:5.2f} "
              f"J={r['junction']} red={r['red']} notstop={r['not_stop']} top={r['cells']}")
