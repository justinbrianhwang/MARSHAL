#!/usr/bin/env python
"""Compute the R6 condition-robustness report from finished benchmark runs.

R6 = mean over conditions c of graded(c) / graded(baseline), each retention
clamped to [0, 1] (docs/generalization_plan.md). Reads the episode artifacts
of already-completed ``start.py`` runs — no CARLA required.

Example::

    python scripts/report_robustness.py \
        --baseline-tag townfix3_final_t03 \
        --condition-tags axisb_t03_WetNoon,axisb_t03_HardRainNoon,axisb_t03_FogMorning,axisb_t03_ClearSunset,axisb_t03_ClearNight
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marshal_bench.criteria.graded_episode_scoring import (  # noqa: E402
    score_episode_from_telemetry as score_graded_episode,
)
from marshal_bench.criteria.marshal_metrics import (  # noqa: E402
    EpisodeMetrics,
    aggregate,
    condition_retention_r6,
)

BENCH_ROOT = ROOT / "outputs" / "benchmark"


def _load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _episode_graded(episode_dir: Path) -> Optional[float]:
    """Re-score one finished episode's graded credit from its artifacts."""
    blob = _load_json(episode_dir / "result.json")
    if blob is None:
        return None
    result = blob.get("result", blob)
    telemetry = _load_json(episode_dir / "strict_telemetry.json") or {}
    rows = telemetry.get("telemetry") or telemetry.get("rows") or []
    if not rows:
        return None
    strict = result.get("strict_scoring") or {}
    graded = score_graded_episode(
        dict(result),
        rows,
        scenario=result.get("scenario"),
        expected_action=strict.get("expected_action") or result.get("expected_action"),
        setup_errors=(result.get("scene_setup") or {}).get("errors") or (),
    )
    try:
        return float(graded.get("credit"))
    except (TypeError, ValueError):
        return None


def _run_graded_mean(tag: str) -> tuple[Optional[float], int, Optional[dict]]:
    """Mean graded credit over a run's episodes + its scoreboard, by tag."""
    run_dir = BENCH_ROOT / tag
    board = _load_json(run_dir / "scoreboard.json")
    credits = []
    for episode_dir in sorted(run_dir.glob("marshal_*_*")):
        if not episode_dir.is_dir():
            continue
        credit = _episode_graded(episode_dir)
        if credit is not None:
            credits.append(credit)
    mean = round(sum(credits) / len(credits), 6) if credits else None
    return mean, len(credits), board


def _condition_label(board: Optional[dict], fallback: str) -> str:
    condition = ((board or {}).get("condition") or {}).get("weather") or {}
    return str(condition.get("preset") or fallback)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-tag", required=True,
                        help="run tag of the baseline-condition run (ClearNoon)")
    parser.add_argument("--condition-tags", required=True,
                        help="comma-separated run tags for the condition grid")
    parser.add_argument("--out", default=None,
                        help="output JSON path (default: outputs/benchmark/"
                             "robustness_<baseline-tag>.json)")
    args = parser.parse_args()

    base_mean, base_n, base_board = _run_graded_mean(args.baseline_tag)
    if base_mean is None or base_board is None:
        print(f"ERROR: baseline run {args.baseline_tag!r} has no scored episodes "
              f"or no scoreboard.json under {BENCH_ROOT}")
        return 2

    per_condition: dict[str, Optional[float]] = {}
    rows = []
    for tag in [t for t in args.condition_tags.split(",") if t.strip()]:
        tag = tag.strip()
        mean, n, board = _run_graded_mean(tag)
        label = _condition_label(board, tag)
        per_condition[label] = mean
        rows.append((label, tag, n, mean))
        if n != base_n:
            # A missing/extra episode silently shifts the condition mean —
            # surface it so a partial run is never mistaken for the grid.
            print(f"WARNING: {tag} scored {n} episodes but baseline has "
                  f"{base_n}; retention for {label!r} is not like-for-like")

    robustness = condition_retention_r6(base_mean, per_condition)
    if robustness is None:
        print("ERROR: no condition run produced a graded mean — R6 not computable")
        return 2

    metrics = [EpisodeMetrics.from_dict(row) for row in base_board["per_episode"]]
    combined = aggregate(metrics, condition_robustness=robustness)
    combined["baseline_tag"] = args.baseline_tag
    combined["baseline_condition"] = _condition_label(base_board, "ClearNoon")
    combined["condition_tags"] = {label: tag for label, tag, _n, _m in rows}

    out_path = Path(args.out) if args.out else (
        BENCH_ROOT / f"robustness_{args.baseline_tag}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")

    print(f"baseline {args.baseline_tag} ({combined['baseline_condition']}): "
          f"graded mean {base_mean} over {base_n} episodes")
    print(f"{'condition':<16} {'episodes':>8} {'graded':>8} {'retention':>10}")
    for label, _tag, n, mean in rows:
        detail = robustness["per_condition"].get(label) or {}
        print(f"{label:<16} {n:>8} "
              f"{'-' if mean is None else format(mean, '.4f'):>8} "
              f"{'-' if detail.get('retention') is None else format(detail['retention'], '.4f'):>10}")
    print(f"\nR6 (condition retention) = {robustness['R6']}")
    print(f"MARSHAL Score (partial)  = {combined['marshal_score_partial']} "
          f"(was {base_board.get('marshal_score_partial')}; "
          f"unmeasured now {combined['r_unmeasured']})")
    print(f"report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
