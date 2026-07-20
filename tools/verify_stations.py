"""Full verification of ALL registered MARSHAL scenarios at their curated fixed
locations (configs/stations.json). Runs each with the oracle controller and
reports: did it play, did the officer + scene actors spawn, the marshal_metrics,
and any fatal. Flags scenarios that need station/scene fixes.

    C:/.../envs/marshal/python.exe tools/verify_stations.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, os.pardir))
sys.path.insert(0, _ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PY = sys.executable
# The episode runner lives under scripts/, not next to this tool.
RUNNER = os.path.join(_ROOT, "scripts", "run_marshal_officer_demo.py")
if not os.path.isfile(RUNNER):
    sys.exit(f"episode runner not found: {RUNNER}")
OUT = os.path.join(_ROOT, "outputs", "verify_stations")
# Derive from the benchmark registry so this tool can never drift from the
# real suite again (it was stuck at the original 14 while the suite grew).
from start import ALL_SCENARIOS as SCEN  # noqa: E402


def events_summary(run_dir):
    ev = os.path.join(run_dir, "events.json")
    officer = False; extra = 0; fatal = None
    try:
        data = json.load(open(ev, encoding="utf-8"))
        for e in data:
            n = e.get("name")
            if n == "officer_spawned":
                officer = True
            elif n == "extra_actors_spawned":
                extra = e.get("payload", {}).get("count", e.get("count", 0))
            elif n == "fatal":
                fatal = e.get("payload", {}).get("error", "fatal")
    except Exception:
        pass
    return officer, extra, fatal


def main() -> int:
    rows = []
    for s in SCEN:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        cmd = [PY, RUNNER, "--scenario", s, "--town", "Town03",
               "--controller", "oracle", "--out", OUT]
        print(f"verifying {s} ...", flush=True)
        try:
            subprocess.run(cmd, env=env, cwd=_ROOT, timeout=300,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"  run error: {e}", flush=True)
        # locate run dir
        rd = None
        if os.path.isdir(OUT):
            for d in os.listdir(OUT):
                if s.replace("_", "") in d.replace("_", ""):
                    rd = os.path.join(OUT, d); break
        if rd is None:
            rows.append((s, "NO_DIR", "-", "-", "-", "-")); continue
        officer, extra, fatal = events_summary(rd)
        term = "-"; passed = "-"; metric = "-"
        try:
            r = json.load(open(os.path.join(rd, "result.json"), encoding="utf-8"))
            r = r.get("result", r)
            term = r.get("terminated_reason", "-")
            mm = r.get("marshal_metrics") or {}
            passed = mm.get("passed", "-")
            metric = {k: v for k, v in mm.items()
                      if k in ("aoc", "foa", "taa", "sbo", "cri", "occ", "apr",
                               "drm", "rhc", "agi") and v is not None}
        except Exception:
            pass
        rows.append((s, fatal or term, passed, officer, extra, metric))

    print("\n================ STATION VERIFICATION ================")
    print(f"{'scenario':24s} {'outcome':28s} {'pass':5s} {'offcr':5s} {'scene':5s} metrics")
    for s, outcome, passed, officer, extra, metric in rows:
        print(f"{s:24s} {str(outcome)[:28]:28s} {str(passed):5s} "
              f"{str(officer):5s} {str(extra):5s} {metric}")
    json.dump([{"scenario": r[0], "outcome": r[1], "passed": r[2],
                "officer": r[3], "scene_actors": r[4], "metrics": r[5]}
               for r in rows],
              open(os.path.join(_ROOT, "outputs", "verify_stations.json"),
                   "w", encoding="utf-8"), indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
