"""Run the protocol-v2 evaluation (or a subset of it) episode by episode.

    python scripts/run_protocol_v2.py --models oracle tcp qwen3-vl --scenarios green_stop red_proceed --seeds 0
    python scripts/run_protocol_v2.py --all                      # full v2 suite (VLMs seeds 0-4, InterFuser 0-2, others 0)
    python scripts/run_protocol_v2.py --vlm-cap-comparison       # green_stop, VLMs, 3-query cap, seeds 0-4
    python scripts/run_protocol_v2.py --ablation-study           # oracle-assist ladder + controls, seed 0
    python scripts/run_protocol_v2.py --factorial-study          # GLM-4.5V, role x gesture GT, seeds 0-4
    python scripts/run_protocol_v2.py --models tcp --scenarios green_stop --env MARSHAL_OFFICER_CONDITION=absent --tag _noofficer

Run from the workspace root (the directory holding scripts/, marshal_bench/ and CARLA_0.9.16/). Every episode
runs in its own child process with MARSHAL_PROTOCOL=v2 and MARSHAL_SEED=<seed>; output goes to
<out-root>/<model>[_cap3]/s<seed>/. A finished, scored episode is skipped on re-run. CARLA is restarted before
the first episode, every --restart-every episodes, after any failure, and whenever a world check finds a
leftover walker or vehicle.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path.cwd()
UTIL = str(Path(__file__).resolve().parent / "dac2027_carla_util.py")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_protocol_v2 as C  # noqa: E402  (InterFuser adapter check shared with the collector)
CARLA_DIR = Path(os.environ.get("MARSHAL_CARLA_DIR", "CARLA_0.9.16"))
def _env_python(env: str) -> str:
    """Interpreter of a conda env: MARSHAL_PY_<ENV>, else <conda root>/envs/<env>/python, else this interpreter."""
    override = os.environ.get("MARSHAL_PY_" + env.upper())
    if override:
        return override
    here = Path(sys.executable).resolve()
    root = here.parents[2] if here.parent.parent.name == "envs" else here.parent  # .../envs/<name>/python.exe
    if os.environ.get("CONDA_EXE"):
        root = Path(os.environ["CONDA_EXE"]).resolve().parents[1]
    for cand in (root / "envs" / env / "python.exe", root / "envs" / env / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


PY = {env: _env_python(env) for env in ("marshal", "transfuser_ui", "interfuser_fl", "openemma")}  # one env per model family
VLM = {"glm-4.5v": "zai-org/GLM-4.5V", "qwen2.5-vl": "Qwen/Qwen2.5-VL-72B-Instruct",
       "qwen3-vl": "Qwen/Qwen3-VL-235B-A22B-Instruct"}
MODELS = ["oracle", "baseline", "transfuser", "tcp", "interfuser", "cilrs", "aim", "neat", "pid", "mpc",
          "openemma", "glm-4.5v", "qwen2.5-vl", "qwen3-vl"]


# Oracle-assist ablation cells (docs/oracle_ablation.md): (model, level, query period or None = native cadence).
ABLATION_CELLS = (
    [("glm-4.5v", lvl, None) for lvl in ("none", "perception", "authority", "semantics", "temporal", "action", "policy",
                                          "neutral", "policy_only")]
    + [("glm-4.5v", "policy_only", 3.0)]
    + [("openemma", lvl, None) for lvl in ("none", "perception", "semantics", "action", "policy", "neutral", "policy_only")]
    + [("openemma", "policy_only", 1.5)]
)


# Role x gesture factorial (docs/oracle_ablation.md): GLM-4.5V, four cells, the leaderboard's five seeds.
FACTORIAL_CELLS = [("glm-4.5v", lvl, None) for lvl in ("none", "role", "gesture", "role_gesture")]


def cell_name(model: str, level: str, period) -> str:
    return f"{model}_abl-{level}" + (f"_qp{period:g}" if period else "")


def command(model: str, scenario: str, out: Path, ablation=None, period=None) -> list:
    if model in ("oracle", "baseline") or model.startswith("scripted_"):
        return [PY["marshal"], "scripts/_run_reference_staging_sweep.py", scenario, "--controller", model]
    if model == "transfuser":
        return [PY["transfuser_ui"], "scripts/_run_transfuser_sweep.py", scenario, "--wall-timeout", "1800",
                "--results-json", str(out / "_results.json"), "--report", str(out / "_report.md")]
    if model in ("tcp", "cilrs", "aim", "neat", "pid", "mpc", "interfuser"):
        env = "interfuser_fl" if model == "interfuser" else "transfuser_ui"
        return [PY[env], "scripts/_run_more_e2e_sweep.py", scenario, "--models", model, "--wall-timeout", "1800",
                "--results-json", str(out / "_results.json"), "--report", str(out / "_report.md")]
    diag = (["--ablation", ablation] if ablation else []) + (["--query-period-s", f"{period:g}"] if period else [])
    if model == "openemma":
        return [PY["openemma"], "scripts/_run_fullplanner_sweep.py", scenario, "--controller", "openemma",
                "--out-root", str(out), "--results-json", str(out / "_results.json"), "--report", str(out / "_report.md"),
                *diag]
    if model in VLM:
        return [PY["marshal"], "scripts/_run_vlm_test.py", scenario, "--model", VLM[model],
                "--results-json", str(out / "_results.json"), "--report", str(out / "_report.md"), *diag]
    raise SystemExit(f"unknown model {model}")


def episode_dir(out: Path, scenario: str) -> Path | None:
    hits = [p for p in out.glob(f"*{scenario}*") if p.is_dir() and (p / "events.json").exists()]
    hits = [p for p in hits if p.name.endswith(scenario) or p.name.endswith(f"{scenario}_s0")
            or p.name.rsplit("_s", 1)[0].endswith(scenario)]
    return sorted(hits)[-1] if hits else None


def complete(out: Path, scenario: str) -> bool:
    ep = episode_dir(out, scenario)
    if ep is None:
        return False
    try:  # only a v2 episode observed over the full 25 s horizon counts as done
        if json.loads((ep / "strict_scoring.json").read_text(encoding="utf-8")).get("protocol_version") != "v2":
            return False
        tel = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
        rows = tel.get("telemetry") if isinstance(tel, dict) else tel
        if not rows or float(rows[-1]["sim_time"]) < 25.0 - 1e-6:
            return False
        # an InterFuser episode from other adapter settings is not done: the collector would refuse it
        if out.parent.name == "interfuser" and C.interfuser_config_problem(
                json.loads((ep / "metadata.json").read_text(encoding="utf-8"))):
            return False
    except Exception:
        return False
    r = subprocess.run([PY["marshal"], UTIL, "episode-complete", str(ep)],
                       capture_output=True)
    return r.returncode == 0


def kill_carla() -> None:
    for exe in ("CarlaUE4-Win64-Shipping.exe", "CarlaUE4.exe"):
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)


def start_carla(log) -> bool:
    kill_carla()
    time.sleep(5)
    subprocess.Popen([str((ROOT / CARLA_DIR / "CarlaUE4.exe").resolve()), "-carla-rpc-port=2000", "-quality-level=Epic",
                      "-windowed", "-ResX=640", "-ResY=480"], cwd=str((ROOT / CARLA_DIR).resolve()),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(40)
    for _ in range(4):
        try:
            r = subprocess.run([PY["marshal"], UTIL, "load-town03"],
                               capture_output=True, timeout=240)
            if r.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(20)
    log({"event": "carla_start_failed"})
    return False


def world_clean(tag: str, log) -> bool:
    try:
        r = subprocess.run([PY["marshal"], UTIL, "world-check", tag],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False
    log({"event": "world_check", "tag": tag, "rc": r.returncode, "out": r.stdout.strip()[-300:]})
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="*", default=None)
    ap.add_argument("--scenarios", nargs="*", default=None)
    ap.add_argument("--seeds", nargs="*", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="full suite: VLMs seeds 0-4, InterFuser 0-2, every other model seed 0")
    ap.add_argument("--vlm-cap-comparison", action="store_true", help="green_stop, the three VLMs, 3-query cap, seeds 0-4")
    ap.add_argument("--ablation-study", action="store_true",
                    help="oracle-assist ladder and control cells (ABLATION_CELLS), 25 scenarios, seed 0; "
                         "default out root tmp/_protocol_v2_ablation")
    ap.add_argument("--factorial-study", action="store_true",
                    help="role x gesture factorial (FACTORIAL_CELLS), 25 scenarios, seeds 0-4, cells interleaved; "
                         "default out root tmp/_protocol_v2_factorial")
    ap.add_argument("--out-root", default="tmp/_protocol_v2")
    ap.add_argument("--restart-every", type=int, default=8)
    ap.add_argument("--log", default="outputs/protocol_v2/run_log.jsonl")
    ap.add_argument("--env", action="append", default=[], metavar="MARSHAL_KEY=VALUE",
                    help="diagnostic MARSHAL_* variable passed to every episode (repeatable), e.g. "
                         "MARSHAL_OFFICER_CONDITION=absent, MARSHAL_TIMEOUT_S=60, MARSHAL_CONTROLLER_OVERRIDES=<json>")
    ap.add_argument("--tag", default="", help="suffix for the model output directory (keeps diagnostic cells apart)")
    a = ap.parse_args()
    extra_env = dict(kv.split("=", 1) for kv in a.env if "=" in kv)
    bad = [k for k in extra_env if not k.startswith("MARSHAL_") or k in ("MARSHAL_SEED", "MARSHAL_OUT_ROOT")]
    if bad or len(extra_env) != len(a.env):
        raise SystemExit(f"--env takes MARSHAL_KEY=VALUE (not MARSHAL_SEED / MARSHAL_OUT_ROOT); got {a.env}")

    sys.path.insert(0, str(ROOT / "scripts"))
    import _run_vlm_test as vlm  # scenario registry
    scenarios = a.scenarios or list(vlm.SCENARIO_ORDER)
    jobs = []  # (model, scenario, seed, cap3, ablation, period)
    if a.factorial_study:
        if a.out_root == "tmp/_protocol_v2":
            a.out_root = "tmp/_protocol_v2_factorial"
        # cells innermost, so API drift over the run hits all four cells alike
        jobs = [(m, sc, s, False, lvl, per) for s in range(5) for sc in scenarios for (m, lvl, per) in FACTORIAL_CELLS]
    elif a.ablation_study:
        if a.out_root == "tmp/_protocol_v2":
            a.out_root = "tmp/_protocol_v2_ablation"
        jobs = [(m, sc, 0, False, lvl, per) for (m, lvl, per) in ABLATION_CELLS for sc in scenarios]
    elif a.vlm_cap_comparison:
        jobs = [(m, "green_stop", s, True, None, None) for s in range(5) for m in VLM]
    else:
        for m in (a.models or MODELS):
            seeds = a.seeds if a.seeds is not None else (list(range(5)) if (a.all and m in VLM)
                                                         else [0, 1, 2] if (a.all and m == "interfuser") else [0])
            jobs += [(m, sc, s, False, None, None) for s in seeds for sc in scenarios]
    Path(a.log).parent.mkdir(parents=True, exist_ok=True)

    def log(rec):
        rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
        with open(a.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("MARSHAL_")}
    base_env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT), "MARSHAL_PROTOCOL": "v2"})
    since_restart, carla_up, failures = 10 ** 6, False, 0
    log({"event": "batch_start", "jobs": len(jobs)})
    for i, (model, sc, seed, cap3, ablation, period) in enumerate(jobs, 1):
        name = (cell_name(model, ablation, period) if ablation else model + ("_cap3" if cap3 else "")) + a.tag
        out = Path(a.out_root) / name / f"s{seed}"
        out.mkdir(parents=True, exist_ok=True)
        if complete(out, sc):
            continue
        if not carla_up or since_restart >= a.restart_every or not world_clean(f"pre:{model}:{sc}:s{seed}", log):
            carla_up = start_carla(log) and world_clean(f"fresh:{model}:{sc}:s{seed}", log)
            since_restart = 0
            if not carla_up:
                failures += 1
                continue
        env = dict(base_env, **extra_env, MARSHAL_SEED=str(seed), MARSHAL_OUT_ROOT=str(out))
        if cap3:
            env["MARSHAL_VLM_MAX_QUERIES"] = "3"
        t0 = time.time()
        with open(out / f"_{sc}.log", "w", encoding="utf-8") as fh:
            try:
                rc = subprocess.run(command(model, sc, out, ablation, period), env=env, stdout=fh, stderr=subprocess.STDOUT,
                                    timeout=3600).returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
        ok = complete(out, sc)
        since_restart += 1
        log({"event": "episode", "i": i, "of": len(jobs), "model": model, "scenario": sc, "seed": seed,
             "cap3": cap3, "ablation": ablation, "period": period, "env": extra_env, "tag": a.tag,
             "rc": rc, "complete": ok,
             "wall_s": round(time.time() - t0, 1)})
        if not ok:
            failures += 1
            carla_up = False  # fresh server before the next episode
    kill_carla()
    log({"event": "batch_end", "failures": failures})
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
