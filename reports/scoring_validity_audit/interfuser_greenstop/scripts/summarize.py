"""Summarise the green_stop diagnosis runs (offline). Run from the audit checkout root.

    python reports/scoring_validity_audit/interfuser_greenstop/scripts/summarize.py

Reads tmp/_greenstop_interfuser/{ab,officer} (current adapter defaults) and, for comparison, the previous fixed-adapter
A->B runs tmp/_remeasure_interfuser/ab. Writes ../data/ab_runs.csv and ../data/officer_runs.csv.
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUD = HERE.parents[1]
sys.path[:0] = [str(AUD / "interfuser_remeasure" / "scripts"), str(AUD / "credit_v21" / "scripts"), str(AUD / "scripts"),
                ".", "scripts", "scripts/validation"]
import analyze as A  # noqa: E402  (capture(), brake_cause(), kinematics(); the remeasure analysis helpers)
import summarize_validation as SV  # noqa: E402

DATA = HERE.parent / "data"
STATIONS = ("green_stop", "red_proceed", "signal_off", "unauthorized_go", "ambiguous_gesture", "fake_vest_director")


def rows_of(ep):
    tel = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
    return tel.get("telemetry") if isinstance(tel, dict) else tel


def first_rest_brake(ep):
    """First tick after 1 s at rest with a requested brake, and the controller's state there (verbose trace)."""
    p = ep / "interfuser_debug" / "interfuser_verbose.jsonl"
    if not p.exists():
        return ""
    for r in (json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()):
        if r["t"] > 1.0 and r["speed"] < 0.5 and r["requested"]["brake"] > 0.1:
            blk = [k for k in ("d_0", "d_0.5", "d_1", "d_1.5", "d_2") if r[k] < 3.0]
            return f"t={r['t']} {r['brake_reasons'] or 'desired_speed_0'} d<3 at {blk} red={r['red_light_p']}"
    return "none"


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    ab = []
    for label, root, cell in (("current", Path("tmp/_greenstop_interfuser/ab"), "interfuser_noofficer_green"),
                              ("previous_fixed", Path("tmp/_remeasure_interfuser/ab"), "interfuser_noofficer_green")):
        for sd in sorted((root / cell).glob("s*")):
            for sc in STATIONS:
                ep = A.C.find_episode(sd, sc)
                if ep is None or not (ep / "strict_scoring.json").exists():
                    continue
                si = A.check_speed_input(ep, "forward" if label == "current" else "3d")  # refuses mixed settings
                m = SV.ab_metrics(rows_of(ep))
                ab.append({"adapter": label, "speed_input": si, "scenario": sc, "seed": int(sd.name[1:]), **m,
                           "brake_at_rest": A.brake_cause(ep), "first_rest_brake": first_rest_brake(ep)})
    off = []
    for sd in sorted(Path("tmp/_greenstop_interfuser/officer/interfuser").glob("s*")):
        ep = A.C.find_episode(sd, "green_stop")
        if ep is None or not (ep / "strict_scoring.json").exists():
            continue
        si = A.check_speed_input(ep, "forward")
        res, rows, kw, r = A.capture(ep, "green_stop")
        m = SV.ab_metrics(rows)
        off.append({"seed": int(sd.name[1:]), "speed_input": si, "strict": r["strict"], "strict_reason": r["strict_reason"],
                    "credit_v2": r["credit"], **{k: m[k] for k in ("t_start_s", "max_speed_kmh", "travel_m",
                                                                   "min_front_to_line_m", "final_front_to_line_m",
                                                                   "collisions")},
                    "brake_at_rest": A.brake_cause(ep), "first_rest_brake": first_rest_brake(ep)})
    for name, rows in (("ab_runs.csv", ab), ("officer_runs.csv", off)):
        if rows:
            with (DATA / name).open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
    for r in ab:
        print(r["adapter"][:7], r["scenario"][:18].ljust(18), r["seed"], r["t_start_s"], r["t_B_s"], r["ab_pass"],
              r["max_speed_kmh"], r["travel_m"], r["brake_at_rest"][:40])
    for r in off:
        print("officer", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
