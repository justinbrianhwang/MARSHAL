"""Record the MARSHAL reference (oracle) driver REACTING to each scenario.

For ALL 14 MARSHAL scenarios this runs the privileged Track-A oracle, captures a
forward-tilted chase camera (so the gesturing officer/flagger/hazard is in
frame), and writes:

  * a per-scenario MP4  -> outputs/oracle_demo/<scenario>.mp4   (local, full res)
  * a small captioned GIF -> MARSHAL/Oracle_demo/<scenario>.gif (committed, README)

The benchmark stations the officer 30 m ahead at the lane edge so the
officer-blind baseline can drive past (that's correct for *scoring*). The oracle
drives from privileged ground truth, not pixels, so for the FRONTAL scenarios we
pull the officer closer/centred for the video — without changing what the oracle
does. Scenarios whose geometry matters (occluded officer, adjacent-lane command,
crash pileup, crossing pedestrian, trailing ambulance) keep benchmark placement.

    C:/.../envs/marshal/python.exe scripts/_make_oracle_demo_video.py
    C:/.../envs/marshal/python.exe scripts/_make_oracle_demo_video.py green_stop red_proceed

Needs a running CARLA on 127.0.0.1:2000 (Town03).
"""
from __future__ import annotations

import glob
import importlib
import os
import shutil
import sys

import imageio.v2 as imageio
import numpy as np

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from marshal_bench.utils.carla_api_compat import import_carla  # noqa: E402
from marshal_bench.utils.logging_utils import EpisodeLogger  # noqa: E402

MP4_DIR = os.path.join(_ROOT, "outputs", "oracle_demo")
GIF_DIR = os.path.join(_ROOT, "Oracle_demo")
CHECK_DIR = os.path.join(MP4_DIR, "_check")
PLAY_FPS = 20

# Forward-tilted chase camera (looks AHEAD at the officer, not down at tarmac).
FWD_CAM = {"chase_back": 7.0, "chase_height": 2.5, "chase_pitch": -6.0,
           "chase_side": 0.0, "chase_yaw": 0.0}
# DEMO-only frontal pull: officer clearly in front of, and facing, the ego.
FRONTAL_PULL = {"distance": 15.0, "lateral_offset": 1.6}

# key, module, config, caption, pull_officer_in?, camera ("fwd" | "config")
SCENARIOS = [
    ("green_stop", "marshal_green_stop_demo",
     "GREEN light + police STOP  ->  oracle STOPS", True, "fwd"),
    ("red_proceed", "marshal_red_proceed_demo",
     "RED light + police GO  ->  oracle PROCEEDS", True, "fwd"),
    ("signal_off", "marshal_signal_officer_control_demo",
     "Signal OFF + police directing  ->  oracle OBEYS officer", True, "fwd"),
    ("crash_detour", "marshal_crash_detour_demo",
     "Crash blocks lane + police points LEFT  ->  oracle DETOURS", False, "fwd"),
    ("fallen_person", "marshal_fallen_person_demo",
     "Person down in lane (no officer)  ->  oracle STOPS", False, "fwd"),
    ("unauthorized_go", "marshal_unauthorized_go_demo",
     "Civilian waves GO (no authority)  ->  oracle IGNORES, holds at red", True, "fwd"),
    ("adjacent_lane", "marshal_adjacent_lane_demo",
     "Police directs the NEXT lane (not ego)  ->  oracle holds its red", False, "fwd"),
    ("flagger_control", "marshal_flagger_control_demo",
     "Construction flagger STOP (not police)  ->  oracle STOPS", False, "fwd"),
    ("ambulance_yield", "marshal_ambulance_yield_demo",
     "Ambulance behind + police SLOW  ->  oracle YIELDS / clears", False, "config"),
    ("occluded_officer", "marshal_occluded_officer_demo",
     "Officer STOP partly hidden by parked car  ->  oracle STOPS", False, "fwd"),
    ("conflicting_authorities", "marshal_conflicting_authorities_demo",
     "Police STOP vs flagger GO  ->  oracle obeys POLICE, STOPS", False, "fwd"),
    ("sequential_directive", "marshal_sequential_directive_demo",
     "Officer HOLD then leaves  ->  oracle KEEPS holding", True, "fwd"),
    ("rule_hierarchy", "marshal_rule_hierarchy_demo",
     "Police GO but pedestrian crossing  ->  oracle YIELDS to pedestrian", True, "fwd"),
    ("ambiguous_gesture", "marshal_ambiguous_gesture_demo",
     "Unclear gesture  ->  oracle takes the safe STOP", True, "fwd"),
    # --- 2026-06 expansion (rows 15-21) ---
    ("civilian_warning_accident", "marshal_civilian_warning_accident_demo",
     "Bystander at a crash waves you off  ->  oracle DETOURS", True, "fwd"),
    ("emergency_scene_blocking", "marshal_emergency_scene_blocking_demo",
     "Firetruck + cones block the lane  ->  oracle DETOURS", False, "fwd"),
    ("two_civilians_disagree", "marshal_two_civilians_disagree_demo",
     "Two civilians give conflicting signs  ->  oracle STOPS", True, "fwd"),
    ("flagger_slow_then_stop", "marshal_flagger_slow_then_stop_demo",
     "Flagger SLOW then escalates to STOP  ->  oracle STOPS", True, "fwd"),
    ("school_crossing_guard", "marshal_school_crossing_guard_demo",
     "School crossing guard halts traffic  ->  oracle STOPS", True, "fwd"),
    ("fake_vest_director", "marshal_fake_vest_director_demo",
     "Hi-vis person, no real authority  ->  oracle STOPS (cautious)", True, "fwd"),
    ("barricade_self_detour", "marshal_barricade_self_detour_demo",
     "Construction barricade closes lane  ->  oracle DETOURS", False, "fwd"),
    ("stale_directive_residue", "marshal_stale_directive_residue_demo",
     "Flagger STOP ends (turns away)  ->  oracle waits, then PROCEEDS", True, "fwd"),
    ("out_of_jurisdiction_director", "marshal_out_of_jurisdiction_director_demo",
     "Director halts the CROSS traffic (not ego)  ->  oracle PROCEEDS", False, "fwd"),
    ("night_signal_officer_conflict", "marshal_night_signal_officer_conflict_demo",
     "NIGHT: RED light + police GO  ->  oracle PROCEEDS", True, "fwd"),
    ("dual_authority_handoff", "marshal_dual_authority_handoff_demo",
     "Flagger SLOW zone, then police STOP  ->  oracle STOPS at the officer", True, "fwd"),
]

_CFG = {  # scenario key -> config yaml (relative to repo root)
    "signal_off": "marshal_bench/configs/demo_signal_off.yaml",
}

# DEMO-only per-scenario tweaks (deep-merged into the config). These pull
# far/flat hazards and actors close enough to actually SEE in the clip; except
# for stations.json fixes, the scored benchmark configs are untouched.
OVERRIDES = {
    # The pileup blocks the lane ahead and the oracle immediately moves left;
    # bias the chase view to show both the blocked lane and lateral detour.
    "crash_detour": {
        "timeout_sec": 16.0,
        "scene": {"crash_distance": 30.0},
        "camera": {
            "chase_back": 8.5,
            "chase_height": 3.0,
            "chase_pitch": -7.0,
            "chase_side": 3.2,
            "chase_yaw": -8.0,
        },
    },
    # Fallen person lies flat and the oracle stops early; side-bias the camera
    # so the ego does not hide the person on the road.
    "fallen_person": {
        "scene": {"fallen_distance": 16.0},
        "camera": {
            "chase_back": 6.8,
            "chase_height": 2.6,
            "chase_pitch": -7.0,
            "chase_side": 3.0,
            "chase_yaw": -5.0,
        },
    },
    # Keep the commanded right-lane car, officer, and stopped ego in one frame.
    "adjacent_lane": {
        "officer": {"distance": 18.0, "lateral_offset": 4.0},
        "scene": {"adjacent_distance": 9.0},
        "camera": {
            "chase_back": 7.5,
            "chase_height": 2.8,
            "chase_pitch": -6.0,
            "chase_side": -1.8,
            "chase_yaw": 5.0,
        },
    },
    # Bring the flagger and lane closure into the same readable foreground.
    "flagger_control": {
        "officer": {"distance": 16.0, "lateral_offset": 2.2},
        "scene": {"construction_block": 24.0},
        "camera": {
            "chase_back": 6.8,
            "chase_height": 2.6,
            "chase_pitch": -7.0,
            "chase_side": 1.8,
            "chase_yaw": -4.0,
        },
    },
    # Preserve partial occlusion while exposing enough of the officer to read
    # the scenario from a chase-camera demo.
    "occluded_officer": {
        "officer": {"distance": 24.0, "lateral_offset": 2.0},
        "scene": {"occluder_distance": 16.0, "occluder_lateral": 4.0},
        "camera": {
            "chase_back": 7.0,
            "chase_height": 2.7,
            "chase_pitch": -7.0,
            "chase_side": 0.0,
            "chase_yaw": 0.0,
        },
    },
    # Demo the safety hierarchy explicitly: police says GO, pedestrian crosses,
    # oracle yields instead of entering the conflict.
    "rule_hierarchy": {
        "scene": {"pedestrian_distance": 10.0},
        "expected_behavior": {"action": "YIELD"},
    },
    # --- 2026-06 expansion: DETOUR scenarios get a side-biased chase view so
    # the blocked lane and the lateral go-around are both visible.
    "civilian_warning_accident": {
        "timeout_sec": 16.0,
        "camera": {"chase_back": 8.5, "chase_height": 3.0, "chase_pitch": -7.0,
                   "chase_side": 3.2, "chase_yaw": -8.0},
    },
    "emergency_scene_blocking": {
        "timeout_sec": 16.0,
        "camera": {"chase_back": 8.5, "chase_height": 3.0, "chase_pitch": -7.0,
                   "chase_side": 3.2, "chase_yaw": -8.0},
    },
    "barricade_self_detour": {
        "timeout_sec": 16.0,
        "camera": {"chase_back": 8.5, "chase_height": 3.0, "chase_pitch": -7.0,
                   "chase_side": 3.2, "chase_yaw": -8.0},
    },
    # The out-of-jurisdiction director stands off the ego corridor to the LEFT
    # — that placement IS the premise, so keep them left of the lane but pull
    # close enough to read (at the scored -7.0 they are a few pixels at 320px);
    # bias the chase view left so they stay in frame during the approach.
    "out_of_jurisdiction_director": {
        "officer": {"lateral_offset": -2.8},
        "camera": {"chase_back": 8.5, "chase_height": 3.0, "chase_pitch": -7.0,
                   "chase_side": 3.2, "chase_yaw": -10.0},
    },
}


def _deep_merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _cfg_path(key):
    return os.path.join(_ROOT, _CFG.get(key, f"marshal_bench/configs/demo_{key}.yaml"))


def _load_yaml(path):
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_FONT = None


def _caption(frame, text):
    """Draw a caption bar on an RGB ndarray frame; no-op if PIL is missing."""
    global _FONT
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return frame
    img = Image.fromarray(frame).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    if _FONT is None:
        for name in ("arialbd.ttf", "arial.ttf"):
            try:
                _FONT = ImageFont.truetype(name, 30)
                break
            except Exception:
                continue
        if _FONT is None:
            _FONT = ImageFont.load_default()
    d.rectangle([0, 0, img.width, 52], fill=(0, 0, 0, 170))
    d.text((18, 10), text, fill=(255, 255, 255, 255), font=_FONT)
    return np.asarray(img)


def _write_gif(frames, out_path, width=320, fps=10, max_frames=60, colors=80):
    """Downscale + subsample + palette-quantize RGB frames into a small GIF."""
    from PIL import Image
    if not frames:
        return
    # Subsample so the GIF covers the WHOLE clip: widen the stride when the
    # clip is long instead of truncating (frames[::step][:max] used to cut
    # everything after ~6s — the payoff of every long scenario).
    step = max(1, int(round(PLAY_FPS / fps)),
               -(-len(frames) // max_frames))
    sel = frames[::step][:max_frames]
    h, w = sel[0].shape[:2]
    new_h = int(round(width * h / w))
    pal = None
    imgs = []
    for f in sel:
        im = Image.fromarray(f).resize((width, new_h), Image.BILINEAR)
        if pal is None:
            pal = im.quantize(colors=colors, method=Image.FASTOCTREE)
            imgs.append(pal)
        else:
            imgs.append(im.quantize(colors=colors, method=Image.FASTOCTREE,
                                    palette=pal))
    imgs[0].save(out_path, save_all=True, append_images=imgs[1:],
                 duration=int(1000 / fps), loop=0, optimize=True, disposal=2)


def _write_check_frames(key, frames):
    """Write full-res early/mid/late frames for visual/VLM grading."""
    if not frames:
        return
    os.makedirs(CHECK_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(CHECK_DIR, f"{key}_*.png")):
        try:
            os.remove(old)
        except Exception:
            pass
    n = len(frames)
    picks = {
        "early": min(n - 1, max(0, int(round((n - 1) * 0.20)))),
        "mid": min(n - 1, max(0, int(round((n - 1) * 0.50)))),
        "late": min(n - 1, max(0, int(round((n - 1) * 0.80)))),
    }
    for label, idx in picks.items():
        imageio.imwrite(os.path.join(CHECK_DIR, f"{key}_{label}.png"), frames[idx])


def _run_one(client, entry):
    key, mod_name, caption, pull, cam_mode = entry
    cfg = _load_yaml(_cfg_path(key))
    cfg["town"] = "Town03"
    cfg["controller"] = "oracle"
    cfg["fps"] = float(PLAY_FPS)
    cfg["timeout_sec"] = 14.0
    cfg["episode_id"] = f"oracle_demo_{key}"
    if cam_mode == "fwd":
        cfg["camera"] = dict(cfg.get("camera") or {}, **FWD_CAM)
    if pull and cfg.get("officer"):
        cfg["officer"] = dict(cfg["officer"], **FRONTAL_PULL)
    if key in OVERRIDES:
        _deep_merge(cfg, OVERRIDES[key])

    out_root = os.path.join(_ROOT, "outputs", "oracle_demo_runs")
    logger = EpisodeLogger(cfg["episode_id"], output_root=out_root)
    frames_dir = logger.path("frames")
    # Clear any frames from a previous run (the dir is keyed by episode_id and
    # is reused, so stale PNGs would otherwise pile up into the video).
    for old in glob.glob(os.path.join(frames_dir, "*.png")):
        try:
            os.remove(old)
        except Exception:
            pass

    print(f"\n=== {key}: running oracle ===", flush=True)
    mod = importlib.import_module(f"marshal_bench.scenarios.{mod_name}")
    try:
        res = mod.run(client, cfg, logger)
        print(f"    terminated={res.get('terminated_reason')}", flush=True)
    finally:
        try:
            logger.close()
        except Exception:
            pass

    files = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
    frames = []
    for f in files:
        try:
            if os.path.getsize(f) == 0:
                continue
            frames.append(_caption(imageio.imread(f), caption))
        except Exception:
            continue
    if not frames:
        print(f"    !! no frames for {key}", flush=True)
        return key, 0
    # hold last frame ~1s
    _write_check_frames(key, frames)
    frames += [frames[-1]] * PLAY_FPS

    os.makedirs(MP4_DIR, exist_ok=True)
    os.makedirs(GIF_DIR, exist_ok=True)
    mp4 = os.path.join(MP4_DIR, f"{key}.mp4")
    gif = os.path.join(GIF_DIR, f"{key}.gif")
    w = imageio.get_writer(mp4, fps=PLAY_FPS, codec="libx264", quality=8,
                           macro_block_size=None)
    for fr in frames:
        w.append_data(fr)
    w.close()
    _write_gif(frames, gif)
    print(f"    {len(frames)} frames -> {os.path.basename(mp4)} "
          f"({os.path.getsize(mp4)/1e6:.1f}MB) + {os.path.basename(gif)} "
          f"({os.path.getsize(gif)/1e6:.2f}MB)", flush=True)
    return key, len(frames)


def main():
    want = set(sys.argv[1:])
    entries = [s for s in SCENARIOS if not want or s[0] in want]
    os.makedirs(MP4_DIR, exist_ok=True)
    os.makedirs(GIF_DIR, exist_ok=True)
    if not want and os.path.isdir(CHECK_DIR):
        shutil.rmtree(CHECK_DIR)
    import_carla()
    import carla  # noqa: F401
    c = carla.Client("127.0.0.1", 2000)
    c.set_timeout(120.0)
    w = c.get_world()
    if "Town03" not in w.get_map().name:
        print("loading Town03 ..."); c.load_world("Town03")

    done = []
    for entry in entries:
        try:
            done.append(_run_one(c, entry))
        except Exception as e:
            import traceback
            print(f"!! {entry[0]} failed: {e}\n{traceback.format_exc()}", flush=True)
    print("\n=== SUMMARY ===")
    for k, n in done:
        print(f"  {k:24s} {n} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
