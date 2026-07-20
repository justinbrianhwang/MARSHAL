"""Unattended full MARSHAL sweep: 14 models x 21 scenarios = 294 episodes.

Runs each (model, scenario) as an ISOLATED subprocess (a client-side libcarla
segfault then loses only one episode, not the sweep). Health-checks CARLA and
restarts the PROCESS (CarlaUE4.exe) every RESTART_EVERY episodes and on any
crash (CARLA degrades after ~17 episodes; load_world is not enough). Fully
RESUMABLE: an episode whose strict_scoring.json already exists + parses is
skipped, so the orchestrator can be re-launched after an interruption.

Run from the marshal env:
    python scripts/_run_full_sweep.py            # all models
    python scripts/_run_full_sweep.py --only oracle baseline
    python scripts/_run_full_sweep.py --scenarios civilian_warning_accident ...
"""
from __future__ import annotations
import os, sys, json, time, argparse, subprocess

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))
sys.path.insert(0, THIS); sys.path.insert(0, ROOT)
import _carla_manager as cm
import _run_vlm_test as vlm
from marshal_bench.utils.conditions import parse_weather_params

SCEN = list(vlm.SCENARIO_ORDER)  # full registered suite (derived, currently 25)
RESTART_EVERY = 12
PER_EP_TIMEOUT = 900  # seconds (VLM API episodes can be slow)
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONPATH=ROOT)

# Each model's sweep runs in the conda env that has its deps:
#   marshal       -> pure-python + carla + HF-API VLMs (no torch)
#   transfuser_ui -> torch+cv2+timm+transformers (all E2E + classical adapters)
#   openemma      -> Qwen2-VL trajectory planner
def cr(env, *args):
    """conda run command for <env> (proper CUDA DLL activation on Windows)."""
    return ["conda", "run", "--no-capture-output", "-n", env, "python", *args]


def R(*p):
    return os.path.join(ROOT, *p)


# (label, out_root, episode_id(sc)->str, cmd(sc)->list)
MODELS = [
    ("baseline",   R("tmp", "_codex_reference_sweep_runs"),
     lambda sc: f"reference_baseline_{sc}",
     lambda sc: cr("marshal", "scripts/_run_reference_staging_sweep.py", sc, "--controller", "baseline")),
    ("oracle",     R("tmp", "_codex_reference_sweep_runs"),
     lambda sc: f"reference_oracle_{sc}",
     lambda sc: cr("marshal", "scripts/_run_reference_staging_sweep.py", sc, "--controller", "oracle")),
    ("transfuser", R("tmp", "_codex_transfuser_sweep_runs"),
     lambda sc: f"transfuser_{sc}",
     lambda sc: cr("transfuser_ui", "scripts/_run_transfuser_sweep.py", sc)),
    ("tcp",        R("tmp", "_codex_phase3_new_runs"),
     lambda sc: f"tcp_{sc}",
     lambda sc: cr("transfuser_ui", "scripts/_run_more_e2e_sweep.py", sc, "--models", "tcp")),
    ("interfuser", R("tmp", "_codex_phase3_new_runs"),
     lambda sc: f"interfuser_{sc}",
     lambda sc: cr("interfuser_fl", "scripts/_run_more_e2e_sweep.py", sc, "--models", "interfuser")),
    ("cilrs",      R("tmp", "_codex_phase3_new_runs"),
     lambda sc: f"cilrs_{sc}",
     lambda sc: cr("transfuser_ui", "scripts/_run_more_e2e_sweep.py", sc, "--models", "cilrs")),
    ("aim",        R("tmp", "_codex_phase3_new_runs"),
     lambda sc: f"aim_{sc}",
     lambda sc: cr("transfuser_ui", "scripts/_run_more_e2e_sweep.py", sc, "--models", "aim")),
    ("neat",       R("tmp", "_codex_phase3_new_runs"),
     lambda sc: f"neat_{sc}",
     lambda sc: cr("transfuser_ui", "scripts/_run_more_e2e_sweep.py", sc, "--models", "neat")),
    ("pid",        R("tmp", "_codex_phase3_new_runs"),
     lambda sc: f"pid_{sc}",
     lambda sc: cr("transfuser_ui", "scripts/_run_more_e2e_sweep.py", sc, "--models", "pid")),
    ("mpc",        R("tmp", "_codex_phase3_new_runs"),
     lambda sc: f"mpc_{sc}",
     lambda sc: cr("transfuser_ui", "scripts/_run_more_e2e_sweep.py", sc, "--models", "mpc")),
    ("openemma",   R("tmp", "_codex_openemma_runs"),
     lambda sc: f"openemma_{sc}",
     lambda sc: cr("openemma", "scripts/_run_fullplanner_sweep.py", sc, "--controller", "openemma")),
    ("glm-4.5v",   R("tmp", "vlm_runs"),
     lambda sc: f"vlm_zai-org_GLM-4.5V_{sc}",
     lambda sc: cr("marshal", "scripts/_run_vlm_test.py", sc, "--model", "zai-org/GLM-4.5V")),
    ("qwen2.5-vl", R("tmp", "vlm_runs"),
     lambda sc: f"vlm_Qwen_Qwen2.5-VL-72B-Instruct_{sc}",
     lambda sc: cr("marshal", "scripts/_run_vlm_test.py", sc, "--model", "Qwen/Qwen2.5-VL-72B-Instruct")),
    ("qwen3-vl",   R("tmp", "vlm_runs"),
     lambda sc: f"vlm_Qwen_Qwen3-VL-235B-A22B-Instruct_{sc}",
     lambda sc: cr("marshal", "scripts/_run_vlm_test.py", sc, "--model", "Qwen/Qwen3-VL-235B-A22B-Instruct")),
]


def strict_ok(epdir: str) -> bool:
    p = os.path.join(epdir, "strict_scoring.json")
    if not os.path.exists(p):
        return False
    try:
        d = json.load(open(p, encoding="utf-8"))
        # Only a real PASS/FAIL counts as done; INVALID episodes (e.g. the
        # wrong-env adapter failures) must be re-run.
        return d.get("verdict") in ("PASS", "FAIL") and not d.get("invalid")
    except Exception:
        return False


def carla_healthy() -> bool:
    if not cm.port_open():
        return False
    try:
        sys.path.insert(0, ROOT)
        from marshal_bench.utils.carla_api_compat import import_carla
        carla = import_carla()
        c = carla.Client(cm.HOST, cm.PORT); c.set_timeout(8.0)
        return c.get_world().get_map().name.endswith("Town03")
    except Exception:
        return False


def log(msg: str) -> None:
    print(msg, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", help="run only these model labels")
    ap.add_argument("--scenarios", nargs="+", help="restrict scenarios")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--weather", default=None,
                    help="CARLA WeatherParameters preset name for every episode")
    ap.add_argument("--weather-params", type=parse_weather_params, default=None,
                    metavar="K=V,K=V",
                    help="Float weather parameters applied over --weather")
    args = ap.parse_args(argv)

    run_env = dict(ENV)
    for key in (
        "MARSHAL_SWEEP_CONDITION_ACTIVE",
        "MARSHAL_SWEEP_WEATHER",
        "MARSHAL_SWEEP_WEATHER_PARAMS",
    ):
        run_env.pop(key, None)
    if args.weather is not None or args.weather_params is not None:
        run_env["MARSHAL_SWEEP_CONDITION_ACTIVE"] = "1"
    if args.weather is not None:
        run_env["MARSHAL_SWEEP_WEATHER"] = args.weather
    if args.weather_params is not None:
        run_env["MARSHAL_SWEEP_WEATHER_PARAMS"] = json.dumps(args.weather_params)

    models = [m for m in MODELS if (not args.only or m[0] in args.only)]
    scenarios = args.scenarios or SCEN
    total = len(models) * len(scenarios)
    log(f"=== FULL SWEEP: {len(models)} models x {len(scenarios)} scenarios = {total} episodes ===")
    log(f"models: {[m[0] for m in models]}")

    log("initial CARLA restart ...")
    log("CARLA map: " + cm.restart())
    since = 0
    done = skipped = crashed = 0
    t0 = time.time()

    for label, out_root, epid_fn, cmd_fn in models:
        for sc in scenarios:
            epdir = os.path.join(out_root, epid_fn(sc))
            if not args.no_resume and strict_ok(epdir):
                skipped += 1
                log(f"[skip] {label}/{sc} (already scored)")
                continue
            # health / proactive restart
            if since >= RESTART_EVERY or not carla_healthy():
                log(f"... restarting CARLA (since={since}) ...")
                try:
                    log("CARLA map: " + cm.restart())
                except Exception as e:
                    log(f"!! restart failed: {e}; retrying"); time.sleep(5); cm.restart()
                since = 0
            n = done + skipped + crashed + 1
            log(f"[{n}/{total}] {label}/{sc} ...")
            ok = False
            for attempt in (1, 2):
                try:
                    rc = subprocess.run(cmd_fn(sc), cwd=ROOT, env=run_env,
                                        timeout=PER_EP_TIMEOUT).returncode
                except subprocess.TimeoutExpired:
                    rc = -9
                    log(f"    TIMEOUT (attempt {attempt})")
                since += 1
                if rc == 0 and strict_ok(epdir):
                    ok = True
                    break
                log(f"    rc={rc} strict_ok={strict_ok(epdir)} (attempt {attempt}); restarting CARLA")
                try:
                    cm.restart()
                except Exception:
                    time.sleep(5); cm.restart()
                since = 0
            if ok:
                v = json.load(open(os.path.join(epdir, "strict_scoring.json"), encoding="utf-8")).get("verdict")
                done += 1
                log(f"    OK {label}/{sc}: {v}")
            else:
                crashed += 1
                log(f"    GAVE UP {label}/{sc}")
            el = time.time() - t0
            log(f"    progress: done={done} skip={skipped} crash={crashed} elapsed={el/60:.1f}min")

    log(f"=== SWEEP COMPLETE: done={done} skipped={skipped} crashed={crashed} in {(time.time()-t0)/60:.1f}min ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
