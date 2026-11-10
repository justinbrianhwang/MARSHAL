"""Re-run the (unchanged) candidate scorer on each stored telemetry and compare with the stored strict_scoring.json.
python rescore_check.py <runs_root> <out.csv>   (read-only on episodes)"""
import csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from marshal_bench.criteria.strict_episode_scoring import score_green_stop_candidate
root, out = Path(sys.argv[1]), Path(sys.argv[2])
rows_out = []
for ep in sorted(root.glob("*/*/s*/*green_stop")):
    # result.json exists for 26/56 episodes (written by some controller paths only); otherwise the scorer gets an
    # empty result dict and no controller/setup errors -- consistent with no stored verdict being INVALID.
    rj = ep / "result.json"
    res = json.loads(rj.read_text(encoding="utf-8"))["result"] if rj.exists() else {}
    t = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
    tel = t.get("telemetry", t) if isinstance(t, dict) else t
    ev = json.loads((ep / "events.json").read_text(encoding="utf-8"))
    ev = ev.get("events", ev) if isinstance(ev, dict) else ev
    oc = next(e["payload"] for e in ev if e["name"] == "approach_protocol_outcome")
    ap = next(e["payload"] for e in ev if e["name"] == "approach_protocol_applied")
    stored = json.loads((ep / "strict_scoring.json").read_text(encoding="utf-8"))
    new = score_green_stop_candidate(
        res, tel, signal_onset_s=oc.get("signal_onset_s"), post_signal_observation_s=24.0,
        result_label=stored.get("protocol_version"), scenario="marshal_green_stop", expected_action="STOP",
        max_reaction_time=3.0, controller_errors=res.get("controller_errors") or [],
        setup_errors=res.get("scene_setup_errors") or [])
    rows_out.append({"condition": ep.parts[-4], "model": ep.parts[-3], "seed": ep.parts[-2], "result_json_present": rj.exists(),
                     "stored_verdict": stored.get("verdict"), "rescored_verdict": new.get("verdict"),
                     "verdict_match": stored.get("verdict") == new.get("verdict"),
                     "reason_match": stored.get("reason") == new.get("reason"),
                     "stored_reason": stored.get("reason"), "rescored_reason": new.get("reason")})
with out.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows_out[0])); w.writeheader(); w.writerows(rows_out)
n = len(rows_out); v = sum(r["verdict_match"] for r in rows_out); r_ = sum(r["reason_match"] for r in rows_out)
print(f"{n} episodes; verdict match {v}/{n}; reason match {r_}/{n}")
raise SystemExit(0 if v == n and r_ == n else 1)
