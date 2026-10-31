"""Protocol v2 report: every published v2 table and the leaderboard figure, from the collected v2 cells.

    python scripts/protocol_v2_report.py [--cells outputs/protocol_v2] [--out reports/protocol_v2]
                                         [--figures assets/figures] [--map outputs/dac2027_audit/map]
                                         [--check-readme README.md] [--update-readme README.md]

Inputs are the collector outputs (scripts/collect_protocol_v2.py: cells_v2.csv, leaderboard_v2.json,
vlm_query_cap_green_stop_v2.csv) and the episode directories they reference. Writes one Markdown file per table
under <out>/ plus <out>/tables.md (all of them), and <figures>/leaderboard.png/.svg. With --check-readme every
generated table must appear verbatim in the given file (exit 1 otherwise). The per-tick green_stop lane margins
need the exported Town03 OpenDRIVE map (scripts/dac2027_carla_util.py export-map); without it they are left out.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS))
sys.path.insert(0, str(THIS.parent))

from marshal_bench.criteria.marshal_metrics import CONFLICT_TYPE, CONFLICT_TYPE_ORDER  # noqa: E402

ORDER = ["oracle", "baseline", "transfuser", "tcp", "interfuser", "cilrs", "aim", "neat", "pid", "mpc",
         "openemma", "glm-4.5v", "qwen2.5-vl", "qwen3-vl"]
NAME = {"oracle": "oracle (privileged)", "baseline": "baseline (light-only, officer-blind)", "transfuser": "TransFuser",
        "tcp": "TCP", "interfuser": "InterFuser", "cilrs": "CILRS", "aim": "AIM", "neat": "NEAT", "pid": "PID (control)",
        "mpc": "MPC (control)", "openemma": "OpenEMMA", "glm-4.5v": "GLM-4.5V", "qwen2.5-vl": "Qwen2.5-VL-72B",
        "qwen3-vl": "Qwen3-VL-235B-A22B"}
SHORT = {"oracle": "oracle", "baseline": "baseline", "transfuser": "TransFuser", "tcp": "TCP", "interfuser": "InterFuser",
         "cilrs": "CILRS", "aim": "AIM", "neat": "NEAT", "pid": "PID", "mpc": "MPC", "openemma": "OpenEMMA",
         "glm-4.5v": "GLM-4.5V", "qwen2.5-vl": "Qwen2.5-VL", "qwen3-vl": "Qwen3-VL"}
KIND = {"oracle": "A — privileged reference", "baseline": "reference (no authority input)",
        "openemma": "B — VLM trajectory planner", "glm-4.5v": "C — VLM-based closed-loop controller",
        "qwen2.5-vl": "C — VLM-based closed-loop controller", "qwen3-vl": "C — VLM-based closed-loop controller"}
INPUT = {"oracle": "privileged ground truth", "baseline": "own traffic light", "transfuser": "3 RGB + LiDAR + IMU + GNSS",
         "tcp": "3 RGB", "interfuser": "3 RGB + LiDAR + IMU", "cilrs": "1 RGB + IMU", "aim": "1 RGB + IMU",
         "neat": "3 RGB + IMU", "pid": "route + speed", "mpc": "route + speed", "openemma": "1 RGB + IMU + GNSS",
         "glm-4.5v": "1 dash-cam RGB + speed, light state (text)", "qwen2.5-vl": "1 dash-cam RGB + speed, light state (text)",
         "qwen3-vl": "1 dash-cam RGB + speed, light state (text)"}
VLMS = ("glm-4.5v", "qwen2.5-vl", "qwen3-vl")
HALF_WIDTH_M = 1.0817  # measured tesla.model3 bounding-box half-width


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _num(x, nd=1):
    return "—" if x is None else f"{x:.{nd}f}"


def md(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(header))) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def load(cells_dir: Path):
    with (cells_dir / "cells_v2.csv").open(encoding="utf-8") as fh:
        cells = list(csv.DictReader(fh))
    board = json.loads((cells_dir / "leaderboard_v2.json").read_text(encoding="utf-8"))
    with (cells_dir / "vlm_query_cap_green_stop_v2.csv").open(encoding="utf-8") as fh:  # required
        cap = list(csv.DictReader(fh))
    for c in cells:
        c["seed"] = int(c["seed"])
        c["credit"] = _f(c["credit"]) or 0.0
    cells = [c for c in cells if not c["model"].endswith("_cap3")]
    return cells, board, cap


def leaderboard(board):
    rows = []
    for r in board["leaderboard"]:
        m = r["model"]
        n = r["seeds"]
        graded = f"{r['graded_mean']:.1f}" + (f" ± {r['graded_sd']:.1f}" if r["graded_sd"] is not None else "")
        strict = (f"{r['strict_pass_mean']:.1f} ± {r['strict_pass_sd']:.1f}" if r["strict_pass_sd"] is not None
                  else f"{r['strict_pass_mean']:.0f}")
        rows.append([f"**{NAME[m]}**" if m == "oracle" else NAME[m], KIND.get(m, "B — closed-loop driving stack"),
                     n, graded, strict, r["strict_pass_total"], r["override_cells"]])
    return md(["model", "evaluated as", "runs", "MARSHAL-Graded (mean ± SD)", "strict PASS per run (/25)",
               "strict PASS, all runs", "override cells, all runs"], rows)


def per_seed(board):
    rows = []
    for r in board["leaderboard"]:
        if r["model"] in VLMS:
            g = [float(x) for x in r["per_seed_graded"].split(";")]
            rows.append([NAME[r["model"]], *[f"{x:.2f}" for x in g], f"{r['graded_mean']:.2f}", f"{r['graded_sd']:.2f}"])
    return md(["model", "seed 0", "seed 1", "seed 2", "seed 3", "seed 4", "mean", "SD"], rows)


def profile(board):
    rows = []
    for r in board["leaderboard"]:
        rows.append([NAME[r["model"]], *r["profile"].split(";")])
    return md(["model", *[f"{k} ({n})" for k, n in zip(CONFLICT_TYPE_ORDER, (6, 6, 5, 3, 2, 3))]], rows)


def matrix(cells, board):
    weights = board["weights"]
    by = defaultdict(list)
    for c in cells:
        by[(c["scenario"], c["model"])].append(c)
    scen = list(weights)
    models = [m for m in ORDER if any(c["model"] == m for c in cells)]
    rows = []
    for s in scen:
        row = [s, CONFLICT_TYPE.get(s, ""), next(c["expected_action"] for c in cells if c["scenario"] == s),
               f"{weights[s]:g}"]
        for m in models:
            cs = by[(s, m)]
            k = sum(c["strict"] == "PASS" for c in cs)
            mean = 100 * st.mean(c["credit"] for c in cs)
            if len(cs) > 1:
                row.append(f"{mean:.0f} ({k}/{len(cs)})")
            else:
                row.append(f"{mean:.0f}" + (" P" if k else ""))
        rows.append(row)
    lb = {r["model"]: r for r in board["leaderboard"]}
    rows.append(["**Graded**", "", "", f"**{sum(weights.values()):g}**",
                 *[f"**{lb[m]['graded_mean']:.1f}**" for m in models]])
    return md(["Scenario", "Family", "Action", "Weight", *[SHORT[m] for m in models]], rows)


def cap_table(cap):
    if not cap:
        return ""
    rows = [[NAME[c["model"]], c["wiring"], c["runs"], c["queries_mean"], c["strict_pass"],
             f"{float(c['credit_mean']):.3f}", "—" if c["credit_sd"] in ("", None) else f"{float(c['credit_sd']):.3f}"]
            for c in cap]
    return md(["model", "query schedule", "runs (seeds 0–4)", "queries per episode", "strict PASS",
               "credit mean", "credit SD"], rows)


def credit_distribution(cells, board):
    """Episodes by credit: 1, strictly between 0 and 1, and 0 split by the first zero factor
    (movement gate, then action credit, then latency, then safety; credit is their product)."""
    def bucket(c):
        if c.get("graded_invalid") == "True":
            return "invalid"
        if c["credit"] >= 0.999999:
            return "full"
        if c["credit"] > 0:
            return "partial"
        if c["movement_gate"] == "0":
            return "move"
        for k in ("action_credit", "latency_factor", "safety_factor"):
            if (_f(c[k]) or 0.0) == 0:
                return k
        return "invalid"
    keys = ("full", "partial", "move", "action_credit", "latency_factor", "safety_factor", "invalid")
    def row(label, cs):
        n = collections.Counter(bucket(c) for c in cs)
        return [label, len(cs), *[n[k] for k in keys]]
    models = [r["model"] for r in board["leaderboard"]]  # leaderboard rows only (no cap-3 or ablation cells)
    rows = [row(NAME[m], [c for c in cells if c["model"] == m]) for m in models]
    rows.append(row("**non-privileged, all**", [c for c in cells if c["model"] in models and c["model"] != "oracle"]))
    rows.append(row("**all**", [c for c in cells if c["model"] in models]))
    return md(["model", "episodes", "credit = 1", "0 < credit < 1", "0: movement gate", "0: action credit",
               "0: latency", "0: safety", "invalid"], rows)


def proceed_table(cells):
    scen = [s for s in dict.fromkeys(c["scenario"] for c in cells) if
            next(c["expected_action"] for c in cells if c["scenario"] == s) == "PROCEED"]
    rows = []
    for m in ORDER:
        row = [SHORT[m]]
        for s in scen:
            cs = [c for c in cells if c["model"] == m and c["scenario"] == s]
            if not cs:
                row.append("—")
                continue
            k = sum(c["strict"] == "PASS" for c in cs)
            late = sum(c["failure_class"] == "late_start" for c in cs)
            nodec = sum(c["failure_class"] == "no_decision_opportunity" for c in cs)
            delays = [(_f(c["start_s"]) - _f(c["t_safe_s"])) for c in cs
                      if c["strict"] == "PASS" and _f(c["start_s"]) is not None and _f(c["t_safe_s"]) is not None]
            txt = f"{k}/{len(cs)} PASS"
            if delays:
                txt += f", start +{st.mean(delays):.2f} s"
            if late:
                txt += f", {late} late start"
            if nodec:
                txt += f", {nodec} no decision"
            row.append(txt)
        rows.append(row)
    return md(["model", *scen], rows)


def release_table(cells):
    rows = []
    for m in ORDER:
        cs = [c for c in cells if c["model"] == m and c["release_start_within_3s"] not in ("", None)]
        if not cs:
            continue
        held = [c for c in cs if c["strict"] == "PASS"]
        rows.append([SHORT[m], len(cs), len(held),
                     sum(c["release_start_within_3s"] == "True" for c in held),
                     sum(c["release_entered"] == "True" for c in held),
                     sum(c["release_collision"] == "True" for c in cs)])
    return md(["model", "STOP/HOLD episodes with a release before 25 s", "of these, strict PASS",
               "PASS and started ≥ 2 km/h within 3 s of the release", "PASS and entered the junction after the release",
               "collision after the release (any verdict)"], rows)


# ---------------------------------------------------------------- green_stop
def _geometry(map_dir: Path):
    if not (map_dir / "Town03.xodr").exists():
        return None
    import dac2027_audit as A
    return A._Geometry(map_dir)


def green_stop_rows(ep: Path, geo):
    tel = json.loads((ep / "strict_telemetry.json").read_text(encoding="utf-8"))
    rows = tel.get("telemetry") if isinstance(tel, dict) else tel
    out = []
    for r in rows:
        left = right = None
        if geo is not None:
            left, right, _ = geo.lane_margins(r["ego_x"], r["ego_y"], _f(r.get("ego_yaw_deg")))
        out.append({"t": float(r["sim_time"]), "v": float(r["ego_speed_kmh"]), "front": _f(r.get("stopline_front_m")),
                    "officer": _f(r.get("distance_to_officer_m")), "active": r.get("officer_active") is True,
                    "gesture": r.get("officer_gesture_id"), "left": left, "right": right,
                    "thr": _f(r.get("applied_throttle")), "brk": _f(r.get("applied_brake"))})
    return out


def green_stop_summary(cells, geo):
    rows = []
    for m in ORDER:
        cs = sorted((c for c in cells if c["model"] == m and c["scenario"] == "green_stop"), key=lambda c: c["seed"])
        for c in cs:
            g = green_stop_rows(Path(c["episode"]), geo)
            act = [x for x in g if 1.0 <= x["t"] <= 13.0]
            enf = [x for x in g if 4.0 <= x["t"] <= 13.0]
            fronts = [x["front"] for x in act if x["front"] is not None]
            label = SHORT[m] + (f" s{c['seed']}" if m in VLMS else "")
            rows.append([label, INPUT[m], _num(g[0]["front"], 2), _num(min(fronts) if fronts else None, 2),
                         _num(max(x["v"] for x in enf), 1),
                         _num(min((x["officer"] for x in act if x["officer"] is not None), default=None), 1),
                         _num(min((x["left"] for x in act if x["left"] is not None), default=None), 2),
                         _num(min((x["right"] for x in act if x["right"] is not None), default=None), 2),
                         c["strict"], f"{100 * c['credit']:.1f}", c["strict_reason"]])
    return md(["run", "input", "front to line at spawn (m)", "front to line, min 1–13 s (m)",
               "max speed 4–13 s (km/h)", "officer, min 1–13 s (m)", "left lane margin, min (m)",
               "right lane margin, min (m)", "strict", "credit × 100", "strict reason"], rows)


def green_stop_seconds(cells, geo, model, seed=0):
    c = next(c for c in cells if c["model"] == model and c["scenario"] == "green_stop" and c["seed"] == seed)
    g = green_stop_rows(Path(c["episode"]), geo)
    rows, whole = [], 0
    for i, x in enumerate(g):
        if x["t"] + 1e-9 >= whole:
            rows.append([f"{whole}" if whole else "0 (0.05)", _num(x["v"]), _num(x["front"], 2),
                         f"{_num(x['left'], 2)} / {_num(x['right'], 2)}", _num(x["officer"]),
                         "STOP" if x["active"] and str(x["gesture"]).upper() == "STOP" else "—",
                         f"{_num(x['thr'], 2)} / {_num(x['brk'], 2)}"])
            whole = math.floor(x["t"] + 1e-9) + 1
    return md(["t (s)", "speed (km/h)", "front to line (m)", "left / right lane margin (m)", "officer (m)",
               "directive", "throttle / brake"], rows)


def weight_sensitivity(cells, board, draws=1000, spread=0.25, seed=0):
    """Ranking under uniform weights and under random +/- spread perturbations (Kendall tau vs the shipped weights)."""
    import random
    weights = board["weights"]
    models = [r["model"] for r in board["leaderboard"]]
    mean = {(m, s): st.mean(c["credit"] for c in cells if c["model"] == m and c["scenario"] == s)
            for m in models for s in weights}

    def scores(w):
        return {m: 100 * sum(w[s] * mean[(m, s)] for s in w) / sum(w.values()) for m in models}

    def rank(sc):
        return sorted(models, key=lambda m: -sc[m])

    def tau(a, b):
        pos = {m: i for i, m in enumerate(b)}
        n, conc = len(a), 0
        for i in range(n):
            for j in range(i + 1, n):
                conc += 1 if pos[a[i]] < pos[a[j]] else -1
        return conc / (n * (n - 1) / 2)

    base = rank(scores(weights))
    uni = scores({s: 1.0 for s in weights})
    uni_rank = rank(uni)
    swaps = [f"{SHORT[a]}/{SHORT[b]}" for i, a in enumerate(base) for b in base[i + 1:] if uni_rank.index(a) > uni_rank.index(b)]
    rng = random.Random(seed)
    taus = [tau(base, rank(scores({s: w * rng.uniform(1 - spread, 1 + spread) for s, w in weights.items()})))
            for _ in range(draws)]
    rows = [["shipped weights (Σ = %g)" % sum(weights.values()), "—", "1.000", "—"],
            ["uniform weights", ", ".join(swaps) or "none", f"{tau(base, uni_rank):.3f}", "—"],
            [f"{draws} random ±{int(spread * 100)}% perturbations (seed {seed})", "—",
             f"{st.mean(taus):.3f} ± {st.stdev(taus):.3f}", f"{min(taus):.3f}"]]
    return md(["weighting", "pairs that swap vs shipped ranking", "Kendall τ vs shipped (mean ± SD)", "min τ"], rows)


def timing(cells):
    """Clock, decision cadence and input age per model, from every v2 episode (reuses the audit timing reader)."""
    import dac2027_audit as A
    from collections import Counter
    rows = []
    for m in ORDER:
        eps = [c for c in cells if c["model"] == m]
        if not eps:
            continue
        tr = [A.timing_row(A.Episode(Path(c["episode"]), m, "protocol_v2")) for c in eps]
        steps = Counter(x.get("world_frame_step", "") for x in tr)
        ages = Counter()
        for x in tr:
            for a in str(x.get("vlm_input_frame_age_ticks") or "").split(";"):
                if a:
                    ages[int(a)] += 1
        intervals = sorted({v for x in tr for v in str(x.get("decision_interval_sim_s") or "").split(";") if v})
        lag = Counter(str(x.get("applied_readback_lag_ticks")) for x in tr)
        sow = [x["sim_over_wall"] for x in tr if isinstance(x.get("sim_over_wall"), (int, float))]
        rate = [x["new_decision_rate_hz"] for x in tr if isinstance(x.get("new_decision_rate_hz"), (int, float))]
        fall = sum(1 for x in tr if (x.get("no_synced_ticks_after_first") or 0) > 0)
        one = all(k and ";" not in k and k.split("x")[0] == "1" for k in steps)
        rows.append([SHORT[m], len(tr), "1 on every tick" if one else ", ".join(f"{k} ×{v}" for k, v in steps.most_common()),
                     f"{st.median(sow):.3f}" if sow else "—",
                     f"{st.median(rate):.2f}" if rate else "—",
                     (", ".join(intervals[:4]) + (" …" if len(intervals) > 4 else "")) if intervals else "—",
                     ", ".join(f"{k} ×{v}" for k, v in sorted(ages.items())) if ages else "—",
                     ", ".join(f"{k} ×{v}" for k, v in lag.most_common()), fall])
    return md(["model", "episodes", "world frames per tick", "sim / wall time (median)", "new decisions per sim s (median)",
               "VLM query interval (sim s)", "VLM input frame age (ticks: queries)", "control readback lag (ticks: episodes)",
               "episodes with sensor fallback after t0"], rows)


def figure(board, out_dir: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = sorted(board["leaderboard"], key=lambda r: r["graded_mean"])
    fig, ax = plt.subplots(figsize=(8.2, 5.6), dpi=150)
    colors = ["#c9a227" if r["model"] == "oracle" else "#8e44ad" if r["model"] in VLMS else
              "#7f8c8d" if r["model"] == "baseline" else "#2e86c1" for r in rows]
    y = range(len(rows))
    ax.barh(y, [r["graded_mean"] for r in rows], color=colors,
            xerr=[r["graded_sd"] or 0 for r in rows], error_kw={"elinewidth": 1, "capsize": 3})
    ax.set_yticks(list(y), [SHORT[r["model"]] + ("†" if r["model"] in VLMS else "") for r in rows])
    for i, r in enumerate(rows):
        txt = f"{r['graded_mean']:.1f}" + (f" ± {r['graded_sd']:.1f}" if r["graded_sd"] is not None else "")
        ax.text(r["graded_mean"] + (r["graded_sd"] or 0) + 1, i, txt, va="center", fontsize=8)
    ax.set_xlim(0, 110)
    ax.set_xlabel("MARSHAL-Graded (0–100), protocol v2")
    ax.set_title("25 scenarios, 25 s fixed horizon; † VLM-based closed-loop controller; mean ± SD over seeds "
                 "(VLMs 0–4, InterFuser 0–2)",
                 fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "leaderboard.png")
    fig.savefig(out_dir / "leaderboard.svg")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", default="outputs/protocol_v2")
    ap.add_argument("--out", default="reports/protocol_v2")
    ap.add_argument("--figures", default="assets/figures")
    ap.add_argument("--map", default="outputs/dac2027_audit/map")
    ap.add_argument("--check-readme", default=None)
    ap.add_argument("--update-readme", default=None, help="rewrite the <!-- v2:name --> table blocks of this file")
    a = ap.parse_args()
    cells, board, cap = load(Path(a.cells))
    if board.get("protocol_version") != "v2" or any(not r["complete"] for r in board["leaderboard"]):
        raise SystemExit("leaderboard_v2.json is not a complete protocol-v2 leaderboard")
    geo = _geometry(Path(a.map))
    tables = {
        "leaderboard": leaderboard(board),
        "vlm_per_seed": per_seed(board),
        "failure_profile": profile(board),
        "matrix": matrix(cells, board),
        "query_cap_green_stop": cap_table(cap),
        "credit_distribution": credit_distribution(cells, board),
        "proceed_start": proceed_table(cells),
        "release_followup": release_table(cells),
        "green_stop_runs": green_stop_summary(cells, geo),
        "green_stop_seconds_oracle": green_stop_seconds(cells, geo, "oracle"),
        "green_stop_seconds_tcp": green_stop_seconds(cells, geo, "tcp"),
        "green_stop_seconds_qwen3-vl_s0": green_stop_seconds(cells, geo, "qwen3-vl", 0),
        "weight_sensitivity": weight_sensitivity(cells, board),
        "timing": timing(cells),
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for k, v in tables.items():
        (out / f"{k}.md").write_text(v, encoding="utf-8")
    (out / "tables.md").write_text("".join(f"## {k}\n\n{v}\n" for k, v in tables.items()), encoding="utf-8")
    figure(board, Path(a.figures))
    print(f"wrote {len(tables)} tables to {out} and the leaderboard figure to {a.figures}")
    if a.update_readme:  # refresh every <!-- v2:name --> ... <!-- /v2:name --> block in place
        import re
        p = Path(a.update_readme)
        text = p.read_text(encoding="utf-8")
        for k, v in tables.items():
            text = re.sub(rf"(<!-- v2:{re.escape(k)} -->\n).*?(<!-- /v2:{re.escape(k)} -->)",
                          lambda mt: mt.group(1) + "\n" + v + "\n" + mt.group(2), text, flags=re.S)
        p.write_text(text, encoding="utf-8")
        a.check_readme = a.update_readme
    if a.check_readme:
        text = Path(a.check_readme).read_text(encoding="utf-8")
        missing = [k for k, v in tables.items() if not v or v.strip() not in text]
        if missing:
            print(f"not in {a.check_readme} verbatim: {missing}")
            return 1
        print(f"all {len(tables)} tables appear verbatim in {a.check_readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
