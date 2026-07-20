"""Render a top-down Town03 map annotated with every MARSHAL scenario station
(the suite is derived from start.ALL_SCENARIOS, currently 25).

Works OFFLINE — no CARLA server needed. It parses the Town03 OpenDRIVE (.xodr)
into a ``carla.Map`` client-side, scatters the road network, and overlays each
scenario's fixed station (from ``marshal_bench/configs/stations.json``) as a
numbered marker coloured by reasoning tier (low/mid/high).

    C:/.../envs/marshal/python.exe tools/render_station_map.py

Output: ``docs/figures/station_map.png``.
"""
from __future__ import annotations

import json
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.criteria.marshal_metrics import REASONING_TIER  # noqa: E402

# Candidate OpenDRIVE locations (source build + local copy).
XODR_CANDIDATES = [
    r"F:/carla/Unreal/CarlaUE4/Content/Carla/Maps/OpenDrive/Town03.xodr",
    os.path.join(_ROOT, "CARLA_0.9.16", "CarlaUE4", "Content", "Carla",
                 "Maps", "OpenDrive", "Town03.xodr"),
    os.path.join(_ROOT, "assets", "maps", "Town03.xodr"),
]

# Benchmark order — derived from start.py so the map always covers the full
# registered suite (the old hardcoded list silently stuck at the original 14).
from start import ALL_SCENARIOS as ORDER  # noqa: E402
TIER_COLOR = {"low": "#2e7d32", "mid": "#ef6c00", "high": "#c62828"}
TIER_LABEL = {"low": "low (perception/rule-engine)",
              "mid": "mid", "high": "high (LLM-required)"}


def _find_xodr() -> str:
    for p in XODR_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "Town03.xodr not found. Put a copy at assets/maps/Town03.xodr or edit "
        "XODR_CANDIDATES.")


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    carla = import_carla()
    xodr_path = _find_xodr()
    print(f"OpenDRIVE: {xodr_path}")
    with open(xodr_path, "r", encoding="utf-8") as fh:
        xodr = fh.read()
    cmap = carla.Map("Town03", xodr)

    # Road network as a light scatter (every ~2 m along every lane).
    wps = cmap.generate_waypoints(2.0)
    rx = [w.transform.location.x for w in wps]
    ry = [w.transform.location.y for w in wps]
    print(f"road waypoints: {len(wps)}")

    with open(os.path.join(_ROOT, "marshal_bench", "configs", "stations.json"),
              encoding="utf-8") as fh:
        stations = json.load(fh)["stations"]

    fig, ax = plt.subplots(figsize=(13, 13))
    ax.scatter(rx, ry, s=1.0, c="#c7ccd1", linewidths=0, zorder=1)

    for i, scen in enumerate(ORDER, 1):
        st = stations.get(scen)
        if st is None:
            continue
        tier = REASONING_TIER.get(scen, "mid")
        col = TIER_COLOR.get(tier, "#555")
        x, y = st["x"], st["y"]
        ax.scatter([x], [y], s=420, c=col, edgecolors="white", linewidths=1.6,
                   zorder=3)
        ax.text(x, y, str(i), color="white", ha="center", va="center",
                fontsize=11, fontweight="bold", zorder=4)
        ax.annotate(f" {i}. {scen}", (x, y), textcoords="offset points",
                    xytext=(12, 6), fontsize=9, color=col, fontweight="bold",
                    zorder=4)

    ax.set_aspect("equal")
    ax.invert_yaxis()  # CARLA top-down convention
    ax.set_title(f"MARSHAL benchmark — {len(ORDER)} scenario stations on Town03",
                 fontsize=15, fontweight="bold", pad=14)
    ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)")
    ax.grid(True, alpha=0.25)

    legend = [Line2D([0], [0], marker="o", color="w", markerfacecolor=c,
                     markersize=12, label=TIER_LABEL[t])
              for t, c in TIER_COLOR.items()]
    ax.legend(handles=legend, loc="upper right", title="reasoning tier",
              framealpha=0.9)

    out_dir = os.path.join(_ROOT, "docs", "figures")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "station_map.png")
    fig.tight_layout()
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"wrote {out}")

    # Also copy into the distributable repo if present.
    repo_fig = os.path.join(_ROOT, "MARSHAL", "docs", "figures")
    if os.path.isdir(os.path.join(_ROOT, "MARSHAL")):
        os.makedirs(repo_fig, exist_ok=True)
        import shutil
        shutil.copy(out, os.path.join(repo_fig, "station_map.png"))
        print(f"copied -> {os.path.join(repo_fig, 'station_map.png')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
