"""Shared strict_telemetry data check (used by analysis_fix_v1/c1_analysis.py and batch_validate.py).

Policy (one rule everywhere, no silent conversion):
  * a numeric value must be a JSON number: int/float, not bool, finite. Strings -- including numeric strings
    such as "25.0" -- are REJECTED, never converted. The same `num()` is used for checking and for computing, so
    a value can never pass a check in one form and be computed in another.
  * required per row: sim_time, ego_speed_kmh, stopline_front_m, ego_forward_m.
  * every row is checked; the first, middle and last rows are labelled as such in the issue list.
  * time axis: non-negative, strictly increasing, no gap > 1.5 x nominal dt.
The check runs BEFORE any computation; a run with any issue is a data error and nothing is derived from it.
"""
from __future__ import annotations

import math

REQUIRED_FIELDS = ("sim_time", "ego_speed_kmh", "stopline_front_m", "ego_forward_m")
GAP_FACTOR = 1.5
MAX_LISTED = 50


def num(x):
    """float(x) for a finite int/float that is not a bool; anything else (str, None, bool, NaN/inf) -> None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def value_problem(x) -> str | None:
    if x is None:
        return "none"
    if isinstance(x, bool):
        return "bool"
    if isinstance(x, str):
        try:
            float(x)
            return "numeric_string"
        except ValueError:
            return "non_numeric_string"
    if not isinstance(x, (int, float)):
        return f"wrong_type:{type(x).__name__}"
    if not math.isfinite(float(x)):
        return "non_finite"
    return None


def telemetry_rows(obj):
    """Rows from a strict_telemetry.json object ({'telemetry': [...]} or [...]); (rows, problem)."""
    rows = obj.get("telemetry") if isinstance(obj, dict) else obj
    if not isinstance(rows, list):
        return None, f"telemetry_not_a_list:{type(rows).__name__}"
    if not rows:
        return None, "telemetry_empty"
    return rows, None


def check_telemetry(obj) -> dict:
    """Return {'ok', 'n_rows', 'issues': [{row, position, field, problem}], 'counts': {problem: n}, 'summary'}."""
    issues, counts = [], {}

    def add(row, pos, field, problem):
        counts[problem] = counts.get(problem, 0) + 1
        if len(issues) < MAX_LISTED:
            issues.append({"row": row, "position": pos, "field": field, "problem": problem})

    rows, prob = telemetry_rows(obj)
    if prob:
        add(None, None, None, prob)
        return {"ok": False, "n_rows": 0, "issues": issues, "counts": counts, "summary": prob}
    n = len(rows)
    label = {0: "first", n // 2: "middle", n - 1: "last"}
    times = []
    for i, r in enumerate(rows):
        pos = label.get(i, "row")
        if not isinstance(r, dict):
            add(i, pos, None, f"row_not_object:{type(r).__name__}")
            times.append(None)
            continue
        for f in REQUIRED_FIELDS:
            if f not in r:
                add(i, pos, f, "missing_key")
            else:
                p = value_problem(r[f])
                if p:
                    add(i, pos, f, p)
        times.append(num(r.get("sim_time")))
    prev = None
    for i, t in enumerate(times):
        if t is None:
            prev = None
            continue
        if t < 0:
            add(i, label.get(i, "row"), "sim_time", "negative")
        if prev is not None and t <= prev:
            add(i, label.get(i, "row"), "sim_time", "non_increasing")
        prev = t
    d = sorted(y - x for x, y in zip(times, times[1:]) if x is not None and y is not None and y > x)
    if d:
        dt = d[len(d) // 2]
        for i, (x, y) in enumerate(zip(times, times[1:]), 1):
            if x is not None and y is not None and y - x > GAP_FACTOR * dt + 1e-9:
                add(i, label.get(i, "row"), "sim_time", "gap_gt_1p5dt")
    summary = ";".join(f"{k}={v}" for k, v in sorted(counts.items()))
    return {"ok": not counts, "n_rows": n, "issues": issues, "counts": counts, "summary": summary}
