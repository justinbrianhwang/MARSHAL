"""Build Town03Landmarks prop — three vertical signpost landmarks for the
central roundabout fountain in Town03.

Geometry per sign:
 - 0.20 m square pole rising from z=0 to z=1.6
 - 3.0 x 1.5 m panel from z=1.6 to z=3.1, textured with a lab logo
 - Panel face normal points RADIALLY OUTWARD from the fountain centre,
   so drivers approaching on the surrounding road read the logo head-on

The panel is a single DOUBLE-SIDED quad — a thin-box / single-sided
approach broke under UE's default opaque material (back-faces black). We
accept that the INNER side will appear mirrored; pedestrians can't get
inside the fountain pool to see it anyway.

Mesh is baked at LOCAL origin (0, 0, 0) — the CARLA spawn transform
locates the prop at the actual fountain centre, so re-positioning only
needs to re-run the spawn script (no FBX re-import).
"""
from __future__ import annotations

import math
import os
import shutil
import sys

_lib = r"C:/Users/sunju/miniconda3/envs/marshal/Library/bin"
if os.path.isdir(_lib):
    os.add_dll_directory(_lib)
    os.environ["PATH"] = _lib + os.pathsep + os.environ.get("PATH", "")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                 os.pardir)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pyassimp  # noqa: E402
from PIL import Image  # noqa: E402

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
STAGE = os.path.join(_REPO, "assets", "props", "Town03Landmarks")
IMPORT_DIR = r"F:/carla/Import/MarshalProps"
PROP_DIR = os.path.join(IMPORT_DIR, "Props", "Town03Landmarks")

# Local origin — the spawn transform locates the prop in the world.
CX, CY = 0.0, 0.0

SIGN_RADIUS = 8.0           # m — inner green ring (water disc r~7m + 1m grass margin)
# The Town03 sculpture extends most of the way to the disc edge in the SW
# quadrant, so 210° puts the panel inside the concrete. Use 90° (N), 270°
# (S), 330° (SE) — three angles where the disc edge is clear water.
SIGN_ANGLES_DEG = (90.0, 270.0, 330.0)
POLE_HALF = 0.10            # 0.20 m square pole
POLE_TOP = 1.6              # m — eye-level top so panel doesn't overhang water
PANEL_W, PANEL_H = 3.0, 1.5  # smaller than before so signs don't crowd sculpture
PANEL_Z0, PANEL_Z1 = POLE_TOP, POLE_TOP + PANEL_H

LOGOS = [
    ("Logo1", os.path.join(_REPO, "assets", "lab_logos", "remove_background.png"),
     "logo1.png"),  # SJJB
    ("Logo2", os.path.join(_REPO, "assets", "lab_logos", "RAISE.png"),
     "logo2.png"),
    ("Logo3", os.path.join(_REPO, "assets", "lab_logos", "mps_logo_nobg.png"),
     "logo3.png"),
]


def _flatten_to_white(src, dst):
    im = Image.open(src).convert("RGBA")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, mask=im.split()[3])
    bg.save(dst)


class Builder:
    def __init__(self):
        self.v: list = []
        self.vt: list = []
        self.faces: dict = {"Pole": [], "Logo1": [], "Logo2": [], "Logo3": []}

    def av(self, x, y, z):
        self.v.append((x, y, z)); return len(self.v)

    def at(self, u, w):
        self.vt.append((u, w)); return len(self.vt)

    def quad(self, mat, pts, uvs):
        """Double-sided quad — visible from both sides."""
        p = [self.av(*q) for q in pts]
        t = [self.at(*u) for u in uvs]
        f = self.faces[mat]
        f.append((p[0], t[0], p[1], t[1], p[2], t[2]))
        f.append((p[0], t[0], p[2], t[2], p[3], t[3]))
        f.append((p[0], t[0], p[2], t[2], p[1], t[1]))
        f.append((p[0], t[0], p[3], t[3], p[2], t[2]))

    def write_obj(self, path, mtl):
        cm = 100.0
        out = ["# Town03 landmarks (cm, Y<->Z pre-swap)",
               f"mtllib {mtl}", "o Town03Landmarks"]
        for x, y, z in self.v:
            out.append(f"v {x*cm:.3f} {z*cm:.3f} {y*cm:.3f}")
        for u, w in self.vt:
            out.append(f"vt {u:.4f} {w:.4f}")
        for mat, tris in self.faces.items():
            if not tris:
                continue
            out.append(f"g Town03Landmarks_{mat}")
            out.append(f"usemtl {mat}")
            for a in tris:
                out.append(f"f {a[0]}/{a[1]} {a[4]}/{a[5]} {a[2]}/{a[3]}")
        open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")


def _pole(b, cx, cy):
    corners = [(cx - POLE_HALF, cy - POLE_HALF), (cx + POLE_HALF, cy - POLE_HALF),
               (cx + POLE_HALF, cy + POLE_HALF), (cx - POLE_HALF, cy + POLE_HALF)]
    for i in range(4):
        a, q = corners[i], corners[(i + 1) % 4]
        b.quad("Pole", [(a[0], a[1], 0.0), (q[0], q[1], 0.0),
                        (q[0], q[1], POLE_TOP), (a[0], a[1], POLE_TOP)],
               [(0, 0), (1, 0), (1, 1), (0, 1)])


def _panel(b, cx, cy, angle_rad, mat):
    """Single double-sided panel quad. Empirical UV calibration (per-sign
    A/B/C/D test) showed UV "B" = [(1,0),(0,0),(0,1),(1,1)] reads
    upright + non-mirrored from the outward side. (Other options: A
    mirrors L-R, C flips upside-down, D rotates 180°.)
    Inside view will show the texture U-flipped — acceptable since
    pedestrians can't get inside the fountain pool to see the back."""
    tx, ty = -math.sin(angle_rad), math.cos(angle_rad)
    hx = PANEL_W / 2.0
    pts = [(cx - tx * hx, cy - ty * hx, PANEL_Z0),
           (cx + tx * hx, cy + ty * hx, PANEL_Z0),
           (cx + tx * hx, cy + ty * hx, PANEL_Z1),
           (cx - tx * hx, cy - ty * hx, PANEL_Z1)]
    uvs = [(1, 0), (0, 0), (0, 1), (1, 1)]
    b.quad(mat, pts, uvs)


def main() -> int:
    os.makedirs(STAGE, exist_ok=True)
    b = Builder()
    logos_assigned = list(zip(SIGN_ANGLES_DEG, LOGOS))
    for angle_deg, (mat, src, dst) in logos_assigned:
        if not os.path.exists(src):
            print(f"ERROR: logo not found: {src}")
            return 1
        _flatten_to_white(src, os.path.join(STAGE, dst))
        a = math.radians(angle_deg)
        cx = CX + SIGN_RADIUS * math.cos(a)
        cy = CY + SIGN_RADIUS * math.sin(a)
        _pole(b, cx, cy)
        _panel(b, cx, cy, a, mat)
        print(f"  {mat}: {dst} at angle={angle_deg:.0f}° local=({cx:.1f}, {cy:.1f})")

    obj = os.path.join(STAGE, "Town03Landmarks.obj")
    b.write_obj(obj, "Town03Landmarks.mtl")
    print(f"wrote {obj}  {len(b.v)} verts, "
          f"{sum(len(t) for t in b.faces.values())} tris")

    _common = ("Ka 0.000000 0.000000 0.000000\n"
               "Ks 0.020000 0.020000 0.020000\n"
               "Ke 0.000000 0.000000 0.000000\n"
               "Ns 0.000000\nNi 1.000000\nd 1.000000\nillum 1\n")
    with open(os.path.join(STAGE, "Town03Landmarks.mtl"), "w",
              encoding="utf-8") as fh:
        fh.write("newmtl Pole\nKd 0.560000 0.560000 0.540000\n"
                 + _common + "\n")
        for _angle, (mat, _src, dst) in logos_assigned:
            fh.write(f"newmtl {mat}\nKd 1.000000 1.000000 1.000000\n"
                     + _common + f"map_Kd {dst}\n\n")

    fbx = os.path.join(STAGE, "Town03Landmarks.fbx")
    with pyassimp.load(obj) as scene:
        print(f"  assimp: meshes={len(scene.meshes)} "
              f"materials={len(scene.materials)}")
        pyassimp.export(scene, fbx, file_type="fbx")
    print(f"wrote {fbx}  {os.path.getsize(fbx)} bytes")

    os.makedirs(PROP_DIR, exist_ok=True)
    shutil.copy(fbx, os.path.join(PROP_DIR, "Town03Landmarks.fbx"))
    for _angle, (_mat, _src, dst) in logos_assigned:
        shutil.copy(os.path.join(STAGE, dst), os.path.join(PROP_DIR, dst))
    print("staged ->", PROP_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
