import pytest

from marshal_bench.criteria.marshal_metrics import (
    EpisodeMetrics,
    R_WEIGHTS,
    aggregate,
    condition_retention_r6,
)


def _metrics():
    return [
        EpisodeMetrics(episode_id="e1", scenario="green_stop", aoc=1.0,
                       sbo=1.0, occ=1.0, taa=1.0, passed=True),
        EpisodeMetrics(episode_id="e2", scenario="red_proceed", aoc=1.0,
                       sbo=1.0, occ=1.0, taa=1.0, passed=True),
    ]


def test_retention_is_mean_of_clamped_ratios():
    out = condition_retention_r6(0.8, {
        "WetNoon": 0.8,        # retention 1.0
        "HardRainNoon": 0.4,   # retention 0.5
        "ClearNight": 1.0,     # improves -> clamped to 1.0
    })
    assert out is not None
    assert out["R6"] == pytest.approx((1.0 + 0.5 + 1.0) / 3, abs=1e-4)
    assert out["per_condition"]["ClearNight"]["retention"] == 1.0


def test_retention_requires_positive_baseline_and_condition_data():
    assert condition_retention_r6(None, {"WetNoon": 0.5}) is None
    assert condition_retention_r6(0.0, {"WetNoon": 0.5}) is None
    assert condition_retention_r6(0.9, {}) is None
    assert condition_retention_r6(0.9, {"WetNoon": None}) is None


def test_aggregate_without_robustness_keeps_r6_unmeasured():
    board = aggregate(_metrics())
    assert "R6" not in board["r_scores"]
    assert "R6" in board["r_unmeasured"]
    assert "condition_robustness" not in board


def test_aggregate_with_robustness_scores_r6_and_reweights():
    robustness = condition_retention_r6(1.0, {"WetNoon": 0.9, "ClearNight": 0.7})
    without = aggregate(_metrics())
    board = aggregate(_metrics(), condition_robustness=robustness)

    assert board["r_scores"]["R6"] == pytest.approx(0.8, abs=1e-4)
    assert "R6" not in board["r_unmeasured"]
    assert board["condition_robustness"]["per_condition"]["WetNoon"]["retention"] == 0.9

    # Weighted score must now include R6 mass; with all other measured R at
    # 1.0 and R6 at 0.8, the partial score dips below the R6-less score.
    assert board["marshal_score_partial"] < without["marshal_score_partial"]
    measured = {r: R_WEIGHTS[r] for r in board["r_scores"]}
    expected = 100.0 * sum(
        board["r_scores"][r] * w for r, w in measured.items()
    ) / sum(measured.values())
    assert board["marshal_score_partial"] == pytest.approx(expected, abs=0.01)


def test_episode_metrics_from_dict_roundtrip():
    original = _metrics()[0]
    rebuilt = EpisodeMetrics.from_dict(original.as_dict())
    assert rebuilt == original
    # Unknown keys (future scoreboard fields) are ignored, not fatal.
    payload = dict(original.as_dict(), some_future_field=123)
    assert EpisodeMetrics.from_dict(payload) == original
