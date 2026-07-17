"""Summarize what principles and expected actions each model fails."""
from __future__ import annotations

import json
import os

try:
    from scripts._analysis_common import (
        SCENARIO_ACTIONS, SCENARIO_PRINCIPLES, load_runs,
    )
except ImportError:
    from _analysis_common import SCENARIO_ACTIONS, SCENARIO_PRINCIPLES, load_runs

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))


def _group_profile(runs, model, scenarios, mapping):
    groups = {}
    for scenario in scenarios:
        group = mapping[scenario]
        bucket = groups.setdefault(group, {"failures": 0, "cells": 0, "credit_sum": 0.0, "scenarios": []})
        if scenario not in bucket["scenarios"]:
            bucket["scenarios"].append(scenario)
        for run in runs:
            cell = run["matrix"][model][scenario]
            bucket["failures"] += cell["strict"] == "FAIL"
            bucket["cells"] += 1
            bucket["credit_sum"] += float(cell["credit"])
    return {
        group: {
            "failure_rate": bucket["failures"] / bucket["cells"],
            "mean_credit": bucket["credit_sum"] / bucket["cells"],
            "failures": bucket["failures"],
            "cells": bucket["cells"],
            "scenarios": bucket["scenarios"],
        }
        for group, bucket in groups.items()
    }


def analyze(runs, principle_map=None, action_map=None):
    if not runs:
        raise ValueError("at least one run is required")
    scenarios = list(runs[0]["scenarios"])
    principles = dict(principle_map or SCENARIO_PRINCIPLES)
    actions = dict(action_map or SCENARIO_ACTIONS)
    for name, mapping in (("principle", principles), ("action", actions)):
        missing = [scenario for scenario in scenarios if scenario not in mapping]
        if missing:
            raise ValueError(f"missing {name} mappings for scenarios: {missing}")
    models = list(runs[0]["summary"])
    profiles = {}
    stop_scenarios = [scenario for scenario in scenarios if actions[scenario] == "STOP"]
    non_stop_scenarios = [scenario for scenario in scenarios if actions[scenario] != "STOP"]
    for model in models:
        by_principle = _group_profile(runs, model, scenarios, principles)
        by_action = _group_profile(runs, model, scenarios, actions)
        stop_passes = sum(
            run["matrix"][model][scenario]["strict"] == "PASS"
            for run in runs for scenario in stop_scenarios
        )
        non_stop_passes = sum(
            run["matrix"][model][scenario]["strict"] == "PASS"
            for run in runs for scenario in non_stop_scenarios
        )
        stop_rate = stop_passes / (len(runs) * len(stop_scenarios))
        non_stop_rate = non_stop_passes / (len(runs) * len(non_stop_scenarios))
        top_three = sorted(
            by_principle,
            key=lambda principle: (-by_principle[principle]["failure_rate"], principle),
        )[:3]
        profiles[model] = {
            "by_principle": by_principle,
            "by_expected_action": by_action,
            "stop_expected_pass_rate": stop_rate,
            "non_stop_expected_pass_rate": non_stop_rate,
            "stop_bias_index": stop_rate - non_stop_rate,
            "top_3_failing_principles": [
                {"principle": principle, **by_principle[principle]} for principle in top_three
            ],
        }
    return {
        "n_runs": len(runs),
        "scenario_principles": {scenario: principles[scenario] for scenario in scenarios},
        "scenario_expected_actions": {scenario: actions[scenario] for scenario in scenarios},
        "models": profiles,
    }


def markdown(result):
    lines = [
        "| Model | Top 3 failing principles (failure rate) | Stop-bias index |",
        "|---|---|---:|",
    ]
    for model, profile in result["models"].items():
        top = "; ".join(f"{row['principle']} ({row['failure_rate']:.0%})" for row in profile["top_3_failing_principles"])
        lines.append(f"| {model} | {top} | {profile['stop_bias_index']:+.3f} |")
    return "\n".join(lines)


def main():
    runs = load_runs(ROOT)
    if not runs:
        print("no runs found in outputs/multirun/")
        return 1
    result = analyze(runs)
    path = os.path.join(ROOT, "outputs", "failure_profiles.json")
    json.dump(result, open(path, "w", encoding="utf-8"), indent=2)
    print(markdown(result))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
