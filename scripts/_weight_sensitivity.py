"""Measure model-ranking sensitivity to scenario authority weights."""
from __future__ import annotations

import json
import math
import os
import random
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from marshal_bench.criteria.graded_episode_scoring import SCENARIO_AUTHORITY_WEIGHTS

try:
    from scripts._analysis_common import kendall_tau, load_runs, mean_credit
except ImportError:
    from _analysis_common import kendall_tau, load_runs, mean_credit

def _score_and_rank(credits, weights):
    denominator = sum(weights.values())
    scores = {
        model: 100.0 * sum(weights[scenario] * cells[scenario] for scenario in weights) / denominator
        for model, cells in credits.items()
    }
    ranking = sorted(scores, key=lambda model: (-scores[model], model))
    ranks = {model: rank for rank, model in enumerate(ranking, 1)}
    return scores, ranking, ranks


def _rank_vector(reference_models, ranking):
    positions = {model: index for index, model in enumerate(ranking)}
    return [positions[model] for model in reference_models]


def analyze(runs, weights=None, random_draws=1000, seed=0):
    if not runs:
        raise ValueError("at least one run is required")
    scenarios = list(runs[0]["scenarios"])
    models = list(runs[0]["summary"])
    base_weights = dict(weights or SCENARIO_AUTHORITY_WEIGHTS)
    missing = [scenario for scenario in scenarios if scenario not in base_weights]
    if missing:
        raise ValueError(f"missing weights for scenarios: {missing}")
    base_weights = {scenario: float(base_weights[scenario]) for scenario in scenarios}
    credits = {
        model: {scenario: mean_credit(runs, model, scenario) for scenario in scenarios}
        for model in models
    }
    current_scores, current_order, current_ranks = _score_and_rank(credits, base_weights)
    uniform = {scenario: 1.0 for scenario in scenarios}
    uniform_scores, uniform_order, uniform_ranks = _score_and_rank(credits, uniform)

    oat_records = []
    per_model_max = {model: {"absolute_rank_change": 0, "scenario": None, "direction": None} for model in models}
    for scenario in scenarios:
        for direction, factor in (("down_25pct", 0.75), ("up_25pct", 1.25)):
            varied = dict(base_weights)
            varied[scenario] *= factor
            scores, ranking, ranks = _score_and_rank(credits, varied)
            changes = {model: ranks[model] - current_ranks[model] for model in models}
            record = {"scenario": scenario, "direction": direction, "ranking": ranking, "rank_changes": changes}
            oat_records.append(record)
            for model, change in changes.items():
                if abs(change) > per_model_max[model]["absolute_rank_change"]:
                    per_model_max[model] = {
                        "absolute_rank_change": abs(change), "scenario": scenario,
                        "direction": direction, "signed_rank_change": change,
                    }
    worst_model = min(models, key=lambda model: (-per_model_max[model]["absolute_rank_change"], model))

    rng = random.Random(seed)
    taus = []
    adjacent_flips = {f"{a} vs {b}": 0 for a, b in zip(current_order, current_order[1:])}
    explicit_pair = ("transfuser", "interfuser")
    explicit_flips = 0
    non_oracle_order = [model for model in current_order if model != "oracle"]
    top_non_oracle = non_oracle_order[0]
    top_changes = oracle_first = 0
    current_vector = _rank_vector(models, current_order)
    current_position = {model: rank for rank, model in enumerate(current_order)}
    for _ in range(random_draws):
        varied = {scenario: weight * rng.uniform(0.75, 1.25) for scenario, weight in base_weights.items()}
        _, ranking, _ = _score_and_rank(credits, varied)
        taus.append(kendall_tau(current_vector, _rank_vector(models, ranking)))
        position = {model: rank for rank, model in enumerate(ranking)}
        for a, b in zip(current_order, current_order[1:]):
            if position[a] > position[b]:
                adjacent_flips[f"{a} vs {b}"] += 1
        if all(model in position for model in explicit_pair):
            a, b = explicit_pair
            if (position[a] - position[b]) * (current_position[a] - current_position[b]) < 0:
                explicit_flips += 1
        random_top = next(model for model in ranking if model != "oracle")
        top_changes += random_top != top_non_oracle
        oracle_first += ranking[0] == "oracle"
    tau_mean = sum(taus) / len(taus) if taus else 0.0
    tau_std = math.sqrt(sum((tau - tau_mean) ** 2 for tau in taus) / len(taus)) if taus else 0.0
    divisor = random_draws or 1
    comparison = {
        model: {
            "current": {"graded": current_scores[model], "rank": current_ranks[model]},
            "uniform": {"graded": uniform_scores[model], "rank": uniform_ranks[model]},
            "rank_delta_uniform_minus_current": uniform_ranks[model] - current_ranks[model],
        }
        for model in models
    }
    return {
        "n_runs": len(runs),
        "current_ranking": current_order,
        "uniform_ranking": uniform_order,
        "current_vs_uniform": comparison,
        "one_at_a_time": {
            "schemes": oat_records,
            "per_model_max_absolute_rank_change": per_model_max,
            "maximum_absolute_rank_change": {"model": worst_model, **per_model_max[worst_model]},
        },
        "random": {
            "draws": random_draws,
            "seed": seed,
            "kendall_tau_mean": tau_mean,
            "kendall_tau_std": tau_std,
            "adjacent_pair_flip_fractions": {key: count / divisor for key, count in adjacent_flips.items()},
            "transfuser_vs_interfuser_flip_fraction": explicit_flips / divisor,
            "top_non_oracle_model_current": top_non_oracle,
            "top_non_oracle_change_fraction": top_changes / divisor,
            "oracle_rank_1_fraction": oracle_first / divisor,
        },
        "notes": [
            "VLM cells are single-sample.",
            "This analysis varies weights only, not measurement noise.",
        ],
    }


def markdown(result):
    lines = [
        "| Model | Current graded | Rank | Uniform graded | Rank | Delta |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for model in result["current_ranking"]:
        row = result["current_vs_uniform"][model]
        lines.append(f"| {model} | {row['current']['graded']:.2f} | {row['current']['rank']} | {row['uniform']['graded']:.2f} | {row['uniform']['rank']} | {row['rank_delta_uniform_minus_current']:+d} |")
    random_result = result["random"]
    oat = result["one_at_a_time"]["maximum_absolute_rank_change"]
    lines += [
        "",
        "| Random-weight sensitivity | Result |",
        "|---|---:|",
        f"| Worst one-at-a-time rank change | {oat['absolute_rank_change']} ({oat['model']}, {oat['scenario']} {oat['direction']}) |",
        f"| Kendall tau vs current | {random_result['kendall_tau_mean']:.4f} +/- {random_result['kendall_tau_std']:.4f} |",
        f"| Top non-oracle changes | {random_result['top_non_oracle_change_fraction']:.3%} |",
        f"| TransFuser vs InterFuser flips | {random_result['transfuser_vs_interfuser_flip_fraction']:.3%} |",
        f"| Oracle rank 1 | {random_result['oracle_rank_1_fraction']:.3%} |",
        "",
        "| Current adjacent pair | Random flip fraction |",
        "|---|---:|",
    ]
    for pair, fraction in random_result["adjacent_pair_flip_fractions"].items():
        lines.append(f"| {pair} | {fraction:.3%} |")
    return "\n".join(lines)


def main():
    runs = load_runs(ROOT)
    if not runs:
        print("no runs found in outputs/multirun/")
        return 1
    result = analyze(runs)
    path = os.path.join(ROOT, "outputs", "weight_sensitivity.json")
    json.dump(result, open(path, "w", encoding="utf-8"), indent=2)
    print(markdown(result))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
