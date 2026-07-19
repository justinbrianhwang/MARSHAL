"""Tests for the offline direction-pivot analyses.

Kendall tests exercise tau-b: joint ties are ignored, while one-sided ties are
included in the appropriate denominator. Spearman uses average ranks for ties.
"""
import json
import random

import pytest

from scripts._analysis_common import (
    SCENARIO_ACTIONS,
    SCENARIO_PRINCIPLES,
    load_runs,
    kendall_tau,
    mean_credit,
    spearman,
    strict_pass_frac,
)
from scripts._analyze_tiers import analyze as analyze_tiers
from scripts._failure_profiles import analyze as analyze_failures
from scripts._weight_sensitivity import analyze as analyze_weights


def test_analysis_tables_cover_every_registered_scenario():
    """_failure_profiles.analyze hard-raises on a scenario missing from these
    tables, so they must track start.ALL_SCENARIOS (the 23-scenario suite),
    not a stale subset."""
    import start

    missing_principles = [s for s in start.ALL_SCENARIOS if s not in SCENARIO_PRINCIPLES]
    missing_actions = [s for s in start.ALL_SCENARIOS if s not in SCENARIO_ACTIONS]
    assert not missing_principles, f"SCENARIO_PRINCIPLES missing: {missing_principles}"
    assert not missing_actions, f"SCENARIO_ACTIONS missing: {missing_actions}"


def _fixture_runs():
    scenarios = ["easy_stop", "middle_go", "hard_detour"]
    tiers = {"easy_stop": "low", "middle_go": "mid", "hard_detour": "high"}
    values = [
        {
            "oracle": [("PASS", 1.0), ("PASS", 1.0), ("PASS", 1.0)],
            "candidate": [("PASS", 0.9), ("FAIL", 0.4), ("FAIL", 0.1)],
        },
        {
            "oracle": [("PASS", 1.0), ("PASS", 1.0), ("PASS", 1.0)],
            "candidate": [("PASS", 0.7), ("PASS", 0.6), ("FAIL", 0.2)],
        },
    ]
    runs = []
    for run_values in values:
        matrix = {
            model: {
                scenario: {"strict": cell[0], "credit": cell[1]}
                for scenario, cell in zip(scenarios, cells)
            }
            for model, cells in run_values.items()
        }
        runs.append({
            "summary": {model: {} for model in run_values},
            "matrix": matrix,
            "scenarios": scenarios,
            "tier": tiers,
        })
    return runs


def test_common_cell_aggregates():
    runs = _fixture_runs()
    assert mean_credit(runs, "candidate", "easy_stop") == pytest.approx(0.8)
    assert strict_pass_frac(runs, "candidate", "middle_go") == pytest.approx(0.5)


def test_load_runs_uses_sorted_glob(tmp_path):
    multirun = tmp_path / "outputs" / "multirun"
    multirun.mkdir(parents=True)
    for name, marker in (("run_2.json", 2), ("run_1.json", 1)):
        (multirun / name).write_text(json.dumps({"marker": marker}), encoding="utf-8")
    (multirun / "ignore.json").write_text("{}", encoding="utf-8")
    assert [run["marker"] for run in load_runs(str(tmp_path))] == [1, 2]


def test_kendall_tau_b_small_cases_and_ties():
    assert kendall_tau([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert kendall_tau([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert kendall_tau([1, 2, 3], [1, 3, 2]) == pytest.approx(1.0 / 3.0)
    assert kendall_tau([1, 1, 2], [1, 2, 2]) == pytest.approx(0.5)
    assert kendall_tau([1, 1], [2, 2]) == 0.0


def test_spearman_small_cases_and_average_rank_ties():
    assert spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2, 3], [1, 3, 2]) == pytest.approx(0.5)
    assert spearman([1, 1, 3], [1, 2, 2]) == pytest.approx(0.5)
    assert spearman([7, 7], [1, 2]) == 0.0


def test_three_analyses_on_tiny_fixture():
    runs = _fixture_runs()
    tiers = runs[0]["tier"]
    tier_result = analyze_tiers(runs, tiers)
    assert tier_result["tier_difficulty_spearman"] == pytest.approx(1.0)
    assert tier_result["monotonicity_violations"] == {"strict_pass": [], "mean_credit": []}

    weights = {"easy_stop": 1.0, "middle_go": 1.2, "hard_detour": 1.4}
    weight_result = analyze_weights(runs, weights, random_draws=5, seed=0)
    assert weight_result["current_ranking"] == ["oracle", "candidate"]
    assert weight_result["random"]["oracle_rank_1_fraction"] == 1.0

    principles = {
        "easy_stop": "stopping", "middle_go": "release",
        "hard_detour": "maneuver",
    }
    actions = {"easy_stop": "STOP", "middle_go": "PROCEED", "hard_detour": "DETOUR"}
    failure_result = analyze_failures(runs, principles, actions)
    candidate = failure_result["models"]["candidate"]
    assert candidate["by_principle"]["maneuver"]["failure_rate"] == 1.0
    assert candidate["stop_bias_index"] == pytest.approx(0.75)


def test_seeded_first_random_draw_tau_is_deterministic_from_fixture():
    runs = _fixture_runs()
    weights = {"easy_stop": 1.0, "middle_go": 1.2, "hard_detour": 1.4}
    result = analyze_weights(runs, weights, random_draws=1, seed=0)

    rng = random.Random(0)
    first_weights = {
        scenario: weight * rng.uniform(0.75, 1.25)
        for scenario, weight in weights.items()
    }
    credits = {
        "oracle": [1.0, 1.0, 1.0],
        "candidate": [0.8, 0.5, 0.15],
    }
    scores = {
        model: sum(first_weights[scenario] * credit for scenario, credit in zip(weights, cells))
        for model, cells in credits.items()
    }
    first_ranking = sorted(scores, key=lambda model: (-scores[model], model))
    current = result["current_ranking"]
    current_positions = [current.index(model) for model in credits]
    first_positions = [first_ranking.index(model) for model in credits]
    expected_tau = kendall_tau(current_positions, first_positions)
    assert result["random"]["kendall_tau_mean"] == pytest.approx(expected_tau)
