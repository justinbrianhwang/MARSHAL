"""CARLA-free checks for the green_stop validation suite: scripted fixture state machine,
front-gap geometry, the nearest-vehicle telemetry helper, controller overrides, and the A->B metrics."""
import importlib.util
import json
import os
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from marshal_bench.controllers import make_controller
from marshal_bench.controllers.scripted_behaviors import (
    BEHAVIORS,
    BehaviorPolicy,
    ScriptedBehaviorController,
    front_gap_and_travel,
)
from marshal_bench.scenarios._common import _nearest_other_vehicle_distance_m

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _drive(behavior, gap0=7.41, dt=0.05, horizon=25.0, accel=2.5, decel=7.0):
    """1-D point-mass rollout of the policy: speed tracks the target with bounded accel; HOLD = decel."""
    pol = BehaviorPolicy(behavior)
    v = travelled = vmax = 0.0
    for _ in range(int(horizon / dt)):
        target = pol.command(v, gap0 - travelled, travelled)
        if target is None:
            v = max(0.0, v - decel * dt)
        else:
            v = min(target / 3.6, v + accel * dt)
        travelled += v * dt
        vmax = max(vmax, v)
    return gap0 - travelled, travelled, vmax * 3.6, pol.holding


def test_fixture_profiles_land_in_their_bands():
    gap, _, vmax, hold = _drive("normal_stop")
    assert hold and gap >= 1.0 and vmax >= 5.0
    gap, _, vmax, hold = _drive("late_stop")
    assert hold and 0.0 <= gap < 1.0 and vmax >= 5.0
    gap, _, _, hold = _drive("overshoot")
    assert hold and -3.5 < gap < -1.5
    gap, travelled, vmax, hold = _drive("no_start")
    assert hold and travelled == 0.0 and vmax == 0.0
    _, travelled, vmax, hold = _drive("lurch_then_stop")
    assert hold and 1.6 <= travelled < 2.5 and 5.0 <= vmax <= 8.0 + 1e-9
    gap, travelled, vmax, hold = _drive("constant_go")
    assert not hold and gap < -100 and abs(vmax - 20.0) < 1e-6


def test_hold_is_latched_and_unknown_behavior_rejected():
    pol = BehaviorPolicy("normal_stop")
    assert pol.command(2.0, 2.0, 5.0) is None
    assert pol.command(0.0, 50.0, 5.0) is None  # gap reopening does not release HOLD
    with pytest.raises(ValueError):
        BehaviorPolicy("teleport")


def test_front_gap_geometry_matches_stopline_front_convention():
    yaw = math.radians(30.0)
    fwd = (math.cos(yaw), math.sin(yaw))
    origin = (10.0, -5.0)
    stop = (origin[0] + 9.83 * fwd[0], origin[1] + 9.83 * fwd[1])
    gap, t = front_gap_and_travel(origin, origin, fwd, stop, 2.42)
    assert t == pytest.approx(0.0) and gap == pytest.approx(7.41)
    ego = (origin[0] + 12.0 * fwd[0], origin[1] + 12.0 * fwd[1])
    gap, t = front_gap_and_travel(ego, origin, fwd, stop, 2.42)
    assert t == pytest.approx(12.0) and gap == pytest.approx(9.83 - 12.0 - 2.42)
    assert front_gap_and_travel(ego, origin, fwd, None, 2.42)[0] is None


def test_registry_builds_every_fixture_as_privileged():
    for b in BEHAVIORS:
        c = make_controller(f"scripted_{b}", config={})
        assert isinstance(c, ScriptedBehaviorController)
        assert c.name == f"scripted_{b}" and c.requests_privileged_gt and c.track != "A"


def test_nearest_other_vehicle_excludes_ego():
    loc = lambda x, y: SimpleNamespace(x=x, y=y, z=0.0)  # noqa: E731
    actor = lambda i, x, y: SimpleNamespace(id=i, get_location=lambda: loc(x, y))  # noqa: E731
    ego_loc = loc(0.0, 0.0)
    vehicles = [actor(1, 0.0, 0.0), actor(2, 30.0, 40.0), actor(3, 6.0, 8.0)]
    assert _nearest_other_vehicle_distance_m(vehicles, 1, ego_loc) == pytest.approx(10.0)
    assert _nearest_other_vehicle_distance_m([actor(1, 0.0, 0.0)], 1, ego_loc) is None
    assert _nearest_other_vehicle_distance_m([], 1, ego_loc) is None


def test_controller_overrides(monkeypatch):
    import _shared_staging as staging

    cfg = {"transfuser": {"ckpt_dir": "x", "action_repeat": 2}}
    monkeypatch.delenv("MARSHAL_CONTROLLER_OVERRIDES", raising=False)
    assert staging.apply_controller_overrides(dict(cfg)) == cfg
    monkeypatch.setenv("MARSHAL_CONTROLLER_OVERRIDES", json.dumps({"transfuser": {"action_repeat": 1}}))
    out = staging.apply_controller_overrides({"transfuser": {"ckpt_dir": "x", "action_repeat": 2}})
    assert out["transfuser"] == {"ckpt_dir": "x", "action_repeat": 1}
    assert out["controller_overrides"] == {"transfuser": {"action_repeat": 1}}
    monkeypatch.setenv("MARSHAL_CONTROLLER_OVERRIDES", "[1]")
    with pytest.raises(ValueError):
        staging.apply_controller_overrides({})


def _summ():
    spec = importlib.util.spec_from_file_location("summ", ROOT / "scripts" / "validation" / "summarize_validation.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rows(n=500, v=0.3, **extra):
    return [{"sim_time": (t + 1) / 20, "ego_speed_kmh": min(t, 25.0), "stopline_forward_m": 9.83 - v * t,
             "stopline_front_m": 7.41 - v * t, "ego_forward_m": v * t, "ego_lateral_m": 0.1, "collision_count": 0,
             "distance_to_vehicle_m": 50.0 - t / 10, **extra} for t in range(n)]


def test_ab_metrics():
    m = _summ()
    rows = _rows()
    r = m.ab_metrics(rows)
    assert r["t_start_s"] == pytest.approx(0.30) and r["ab_pass"]
    assert r["t_B_s"] == pytest.approx(101 / 20)  # forward <= -20 at t = 99.43 -> tick 100 (sim_time 5.05)
    parked = [{**row, "ego_speed_kmh": 0.0, "stopline_forward_m": 9.83, "ego_forward_m": 0.0} for row in rows]
    assert not m.ab_metrics(parked)["ab_pass"]


def test_ab_uses_v2_horizon_only():
    m = _summ()
    slow = _rows(n=1200, v=0.03)  # 60 s episode; crosses B at tick 995 -> 49.8 s, past the 25 s horizon
    r = m.ab_metrics(slow)
    assert r["t_B_s"] is None and not r["ab_pass"]
    assert r["t_B_full_s"] == pytest.approx(996 / 20)
    late_start = [{**row, "ego_speed_kmh": 0.0 if row["sim_time"] <= 30 else 20.0} for row in _rows(n=1200)]
    r = m.ab_metrics(late_start)
    assert r["t_start_s"] is None and r["t_start_full_s"] == pytest.approx(30.05) and not r["ab_pass"]


def test_goal_b_needs_lateral_bound_and_no_collision():
    m = _summ()
    assert m.ab_metrics(_rows(ego_lateral_m=2.0))["ab_pass"]
    assert not m.ab_metrics(_rows(ego_lateral_m=-2.5))["ab_pass"]  # crossed the B line off the approach path
    assert not m.ab_metrics(_rows(ego_lateral_m=None))["ab_pass"]
    crashed = [{**row, "collision_count": 1 if row["sim_time"] >= 2.0 else 0} for row in _rows()]
    r = m.ab_metrics(crashed)
    assert r["t_B_s"] is None and not r["ab_pass"] and r["collisions"] == 1


def _fixture_rows(m, credits=None, verdicts=None, seeds=("s0", "s1", "s2")):
    credits = credits or {"scripted_normal_stop": 0.9, "scripted_late_stop": 0.5, "scripted_overshoot": 0.0,
                          "scripted_no_start": 0.0, "scripted_lurch_then_stop": 0.8, "scripted_constant_go": 0.0}
    verdicts = verdicts or {}
    out = []
    for cell, exp in m.EXPECTED_STRICT.items():
        for s in seeds:
            strict = verdicts.get((cell, s), exp)
            out.append({"phase": "23_fixtures", "cell": cell, "seed": s, "scored": True, "strict": strict,
                        "credit": credits[cell], "expected_strict": exp, "matches_expected": strict == exp,
                        "ab_pass": False})
    return out


def _complete(m, **kw):
    out = _fixture_rows(m, **kw)
    for phase in ("1_ab", "1b_variants"):
        for cell in m.EXPECTED_CELLS[phase]:  # A->B failures are findings, never suite problems
            out.append({"phase": phase, "cell": cell, "seed": "s0", "scored": True, "strict": "FAIL", "credit": 0.0,
                        "expected_strict": None, "matches_expected": None, "ab_pass": False})
    return out


def test_suite_complete_and_consistent_incl_lurch_pass():
    m = _summ()
    assert m.check_suite(_complete(m)) == []


def test_suite_flags_missing_and_unscored_cells():
    m = _summ()
    out = [r for r in _complete(m) if not (r["cell"] == "qwen3-vl_noofficer")]
    out[0] = {**out[0], "scored": False, "strict": None, "strict_reason": "boom"}
    out[-1] = {**out[-1], "strict": "INVALID", "strict_reason": "controller adapter error"}
    probs = m.check_suite(out)
    assert any(p.startswith(f"INVALID episode: {out[-1]['phase']}/{out[-1]['cell']}/s0") for p in probs)
    assert any(p.startswith("missing: 1_ab/qwen3-vl_noofficer/s0") for p in probs)
    assert any(p.startswith("not scored") for p in probs)
    assert len(m.check_suite([])) == 14 + 6 + 18


def test_suite_flags_fixture_mismatch_seed_disagreement_and_credit_order():
    m = _summ()
    probs = m.check_suite(_complete(m, verdicts={("scripted_late_stop", "s1"): "PASS"}))
    assert any(p.startswith("fixture mismatch: 23_fixtures/scripted_late_stop/s1") for p in probs)
    assert any(p.startswith("seed disagreement: 23_fixtures/scripted_late_stop") for p in probs)
    bad = {"scripted_normal_stop": 0.4, "scripted_late_stop": 0.5, "scripted_overshoot": 0.6,
           "scripted_no_start": 0.0, "scripted_lurch_then_stop": 0.8, "scripted_constant_go": 0.4}
    probs = m.check_suite(_complete(m, credits=bad))
    assert any("normal_stop 0.4 < late_stop 0.5" in p for p in probs)
    assert any("late_stop 0.5 < scripted_overshoot 0.6" in p for p in probs)
    assert any("normal_stop 0.4 not above scripted_constant_go 0.4" in p for p in probs)


def test_expected_matrix_matches_launcher():
    import re

    m = _summ()
    sh = (ROOT / "scripts" / "validation" / "run_all_validation.sh").read_text(encoding="utf-8")
    arr = lambda name: tuple(re.search(rf"^{name}=\(([^)]*)\)", sh, re.M).group(1).split())  # noqa: E731
    assert arr("ALL_MODELS") == m.ALL_MODELS and arr("FIXTURES") == m.FIXTURES
    assert re.search(r'--out-root "\$OUT/1_ab" --models "\$\{ALL_MODELS\[@\]\}" .*--tag _noofficer( \|\| RC=1)?$', sh, re.M)
    assert re.search(r'--seeds 0 1 2 [^\n]*\\\n\s*--out-root "\$OUT/23_fixtures" --models "\$\{FIXTURES\[@\]\}"', sh)
    variants = []
    for models, tag in re.findall(r'--out-root "\$OUT/1b_variants" --models ([\w .-]+?) "\$\{NOOFF\[@\]\}" --tag (\S+)', sh):
        variants += [mod + tag for mod in models.split()]
    assert tuple(variants) == m.VARIANTS


def test_launcher_fails_when_summary_incomplete(tmp_path):
    """The launcher's exit status reflects the summary: an empty run root must fail, not pass."""
    import shutil
    import subprocess
    import sys

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, OUT=str(tmp_path), MARSHAL_PY_MARSHAL=sys.executable)
    r = subprocess.run([bash, "scripts/validation/run_all_validation.sh", "summary"], cwd=root, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode != 0
