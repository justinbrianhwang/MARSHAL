"""Offline: which input makes InterFuser's object forecast block the path (no CARLA).

    <interfuser env python> reports/scoring_validity_audit/interfuser_greenstop/scripts/forecast_ablation.py <model_input.pt> ...

For a saved adapter input (debug-only dump), runs the checkpoint and reports the native safety distances on the raw
object map (no tracker smoothing) at t = 0, 0.5, 1, 1.5, 2 s, the occupied cells (p > 0.4) with their predicted speed
(channel 6, m/s after the render reweighting) and the variants: LiDAR zeroed, LiDAR 'above -2 m' channel zeroed,
LiDAR 'below' channel zeroed, right camera blanked.
"""
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from marshal_bench.controllers.interfuser_model import InterFuserController  # noqa: E402

ctl = InterFuserController({})
ctl._prepare_import_paths()
ctl._load_model_stack()
from team_code.interfuser_controller import downsample_waypoints, get_max_safe_distance  # noqa: E402


def run(inp):
    with torch.no_grad():
        meta, wps, *_ = ctl.net({k: v.to(ctl.device) for k, v in inp.items()})
    meta, wps = meta.cpu().numpy()[0], wps.cpu().numpy()[0]
    dw = downsample_waypoints(wps)
    d = {t: round(float(get_max_safe_distance(meta, dw, t=t, collision_buffer=np.array([2.5, 1.2]), threshold=0.04)), 2)
         for t in (0, 0.5, 1, 1.5, 2)}
    g = meta.reshape(20, 20, 7)
    cells = [(float(j) - 9.5, 17.5 - float(i), round(float(g[i, j, 0]), 2), round(float(g[i, j, 6]), 2))
             for i in range(20) for j in range(20) if g[i, j, 0] > 0.4]
    return d, sorted(cells, key=lambda c: -c[2])[:5]


for path in sys.argv[1:]:
    base = torch.load(path)
    print("==", "/".join(Path(path).parts[-4:-2]), Path(path).name)
    lid = base["lidar"]
    variants = {"as recorded": {}, "lidar zero": {"lidar": torch.zeros_like(lid)}}
    for ch, nm in ((1, "above"), (0, "below")):
        l2 = lid.clone(); l2[0, ch] = 0; l2[0, 2] = l2[0, 0] + l2[0, 1]
        variants[f"lidar {nm} zeroed"] = {"lidar": l2}
    variants["right camera blanked"] = {"rgb_right": torch.zeros_like(base["rgb_right"])}
    for name, ch in variants.items():
        d, cells = run(dict(base, **ch))
        print(f"  {name:22s} d={d} cells(x,y,p,speed)={cells}")
