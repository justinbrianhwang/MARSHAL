"""Test whether the low/mid/high taxonomy predicts empirical difficulty."""
from __future__ import annotations

import json
import os
import statistics

try:
    from scripts._analysis_common import load_runs, mean_credit, spearman, strict_pass_frac
except ImportError:
    from _analysis_common import load_runs, mean_credit, spearman, strict_pass_frac

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))


def analyze(runs, tier_map=None):
    if not runs:
        raise ValueError("at least one run is required")
    scenarios = list(runs[0]["scenarios"])
    tiers = dict(tier_map or runs[0]["tier"])
    models = [model for model in runs[0]["summary"] if model != "oracle"]
    tier_names = ("low", "mid", "high")
    per_model = {}
    violations = {"strict_pass": [], "mean_credit": []}
    for model in models:
        model_tiers = {}
        for tier in tier_names:
            members = [scenario for scenario in scenarios if tiers[scenario] == tier]
            pass_values = [strict_pass_frac(runs, model, scenario) for scenario in members]
            credits = [mean_credit(runs, model, scenario) for scenario in members]
            model_tiers[tier] = {
                "strict_pass_fraction": sum(pass_values) / len(pass_values),
                "mean_credit": sum(credits) / len(credits),
                "scenario_count": len(members),
            }
        per_model[model] = model_tiers
        for metric in ("strict_pass_fraction", "mean_credit"):
            values = [model_tiers[tier][metric] for tier in tier_names]
            reasons = []
            if values[0] < values[1]:
                reasons.append("low < mid")
            if values[1] < values[2]:
                reasons.append("mid < high")
            if reasons:
                key = "strict_pass" if metric == "strict_pass_fraction" else "mean_credit"
                violations[key].append({"model": model, "violations": reasons})

    scenario_rows = []
    for scenario in scenarios:
        credits = [mean_credit(runs, model, scenario) for model in models]
        passes = [strict_pass_frac(runs, model, scenario) for model in models]
        scenario_rows.append({
            "scenario": scenario,
            "tier": tiers[scenario],
            "difficulty": 1.0 - sum(credits) / len(credits),
            "strict_pass_rate": sum(passes) / len(passes),
        })
    scenario_rows.sort(key=lambda row: (-row["difficulty"], row["scenario"]))
    for rank, row in enumerate(scenario_rows, 1):
        row["difficulty_rank"] = rank

    ordinal = {"low": 0, "mid": 1, "high": 2}
    rho = spearman(
        [ordinal[row["tier"]] for row in scenario_rows],
        [row["difficulty"] for row in scenario_rows],
    )
    low_mid = [row["difficulty"] for row in scenario_rows if row["tier"] in {"low", "mid"}]
    high = [row["difficulty"] for row in scenario_rows if row["tier"] == "high"]
    median_low_mid, median_high = statistics.median(low_mid), statistics.median(high)
    misplaced = []
    for row in scenario_rows:
        reason = None
        if row["tier"] == "high" and row["difficulty"] < median_low_mid:
            reason = "high easier than median low/mid"
        elif row["tier"] in {"low", "mid"} and row["difficulty"] > median_high:
            reason = "low/mid harder than median high"
        if reason:
            misplaced.append({**row, "reason": reason})

    return {
        "n_runs": len(runs),
        "models_excluding_oracle": models,
        "per_model_by_tier": per_model,
        "monotonicity_violations": violations,
        "scenarios_by_difficulty": scenario_rows,
        "tier_difficulty_spearman": rho,
        "difficulty_medians": {"low_mid": median_low_mid, "high": median_high},
        "misplaced_scenarios": misplaced,
    }


def markdown(result):
    lines = [
        "| Tier analysis | Result |",
        "|---|---:|",
        f"| Non-oracle models | {len(result['models_excluding_oracle'])} |",
        f"| Tier vs difficulty Spearman rho | {result['tier_difficulty_spearman']:.3f} |",
        f"| Strict monotonicity violations | {len(result['monotonicity_violations']['strict_pass'])} |",
        f"| Credit monotonicity violations | {len(result['monotonicity_violations']['mean_credit'])} |",
        f"| Misplaced scenarios | {len(result['misplaced_scenarios'])} |",
        "",
        "| Difficulty rank | Scenario | Tier | Difficulty | Strict pass |",
        "|---:|---|---|---:|---:|",
    ]
    for row in result["scenarios_by_difficulty"]:
        lines.append(f"| {row['difficulty_rank']} | {row['scenario']} | {row['tier']} | {row['difficulty']:.3f} | {row['strict_pass_rate']:.3f} |")
    return "\n".join(lines)


def main():
    runs = load_runs(ROOT)
    if not runs:
        print("no runs found in outputs/multirun/")
        return 1
    result = analyze(runs)
    path = os.path.join(ROOT, "outputs", "tier_analysis.json")
    json.dump(result, open(path, "w", encoding="utf-8"), indent=2)
    print(markdown(result))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
