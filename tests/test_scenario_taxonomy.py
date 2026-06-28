"""Validate marshal_bench/configs/scenario_taxonomy.yaml against the 14
implemented scenarios.

Run: ``pytest tests/test_scenario_taxonomy.py`` (requires PyYAML).

These tests check the machine-readable taxonomy stays consistent with the
benchmark's 14 implemented scenarios and the allowed enums documented in
docs/design_principles.md and docs/scenario_taxonomy.md. Planned scenarios live
under the separate ``planned_scenarios:`` key and are intentionally NOT validated
as implemented (they are not scored or counted in the 14).
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TAXONOMY_PATH = _REPO_ROOT / "marshal_bench" / "configs" / "scenario_taxonomy.yaml"

# The 14 implemented scenarios (must match marshal_bench/scenarios + README).
IMPLEMENTED_SCENARIOS = {
    "green_stop",
    "red_proceed",
    "signal_off",
    "crash_detour",
    "fallen_person",
    "unauthorized_go",
    "adjacent_lane",
    "flagger_control",
    "ambulance_yield",
    "occluded_officer",
    "conflicting_authorities",
    "sequential_directive",
    "rule_hierarchy",
    "ambiguous_gesture",
}

VALID_TIERS = {"low", "mid", "high"}
VALID_ACTIONS = {"STOP", "PROCEED", "SLOW", "HOLD", "YIELD", "DETOUR"}
REQUIRED_FIELDS = (
    "expected_action",
    "tier",
    "authority_type",
    "principles",
    "reasoning_requirements",
)


@pytest.fixture(scope="module")
def taxonomy():
    with _TAXONOMY_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "taxonomy YAML must parse to a mapping"
    assert "scenarios" in data, "taxonomy must have a top-level 'scenarios' key"
    return data


@pytest.fixture(scope="module")
def scenarios(taxonomy):
    return taxonomy["scenarios"]


def test_all_14_implemented_scenarios_present(scenarios):
    missing = IMPLEMENTED_SCENARIOS - set(scenarios)
    assert not missing, f"missing implemented scenarios in taxonomy: {sorted(missing)}"


def test_no_unexpected_scenarios_in_implemented_block(scenarios):
    extra = set(scenarios) - IMPLEMENTED_SCENARIOS
    assert not extra, (
        "unexpected scenarios under 'scenarios:' (planned ones belong under "
        f"'planned_scenarios:'): {sorted(extra)}"
    )


@pytest.mark.parametrize("name", sorted(IMPLEMENTED_SCENARIOS))
def test_scenario_has_required_fields(scenarios, name):
    entry = scenarios[name]
    for field in REQUIRED_FIELDS:
        assert field in entry, f"{name} is missing required field '{field}'"


@pytest.mark.parametrize("name", sorted(IMPLEMENTED_SCENARIOS))
def test_tier_is_valid(scenarios, name):
    assert scenarios[name]["tier"] in VALID_TIERS, (
        f"{name} has invalid tier {scenarios[name]['tier']!r}"
    )


@pytest.mark.parametrize("name", sorted(IMPLEMENTED_SCENARIOS))
def test_expected_action_is_valid(scenarios, name):
    assert scenarios[name]["expected_action"] in VALID_ACTIONS, (
        f"{name} has invalid expected_action {scenarios[name]['expected_action']!r}"
    )


@pytest.mark.parametrize("name", sorted(IMPLEMENTED_SCENARIOS))
def test_at_least_one_principle(scenarios, name):
    principles = scenarios[name]["principles"]
    assert isinstance(principles, list) and len(principles) >= 1, (
        f"{name} must list at least one principle"
    )


@pytest.mark.parametrize("name", sorted(IMPLEMENTED_SCENARIOS))
def test_reasoning_requirements_nonempty(scenarios, name):
    reqs = scenarios[name]["reasoning_requirements"]
    assert isinstance(reqs, list) and len(reqs) >= 1, (
        f"{name} must list at least one reasoning requirement"
    )
