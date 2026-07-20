"""Validate marshal_bench/configs/scenario_taxonomy.yaml against the live
benchmark registry.

Run: ``pytest tests/test_scenario_taxonomy.py`` (requires PyYAML).

The implemented-scenario set is DERIVED from ``start.ALL_SCENARIOS`` (the same
registry every runner and tool uses), so the taxonomy can never again drift
behind the suite the way it froze at the original 14 while the suite grew.
Expected actions and tiers are cross-checked against the scoring registry in
``marshal_bench.criteria.marshal_metrics``. Planned scenarios live under the
separate ``planned_scenarios:`` key and are intentionally NOT validated as
implemented (they are not scored or counted).
"""

from pathlib import Path
import sys

import pytest

yaml = pytest.importorskip("yaml")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from start import ALL_SCENARIOS  # noqa: E402
from marshal_bench.criteria import marshal_metrics as mm  # noqa: E402

_TAXONOMY_PATH = _REPO_ROOT / "marshal_bench" / "configs" / "scenario_taxonomy.yaml"

IMPLEMENTED_SCENARIOS = set(ALL_SCENARIOS)

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


def test_registry_tables_match_runner_suite_exactly():
    """Both directions: a scenario missing from a registry table AND a typo'd
    extra key in a registry table are caught (round-4 LOW: extras escaped)."""
    assert set(mm.SCENARIO_SPEC) == IMPLEMENTED_SCENARIOS
    assert set(mm.SECONDARY_ATTRIBUTES) == IMPLEMENTED_SCENARIOS
    assert set(mm.CONFLICT_TYPE) == IMPLEMENTED_SCENARIOS
    assert set(mm.REASONING_TIER) == IMPLEMENTED_SCENARIOS


def test_docs_scenarios_reference_every_scenario_and_attribute():
    """Light drift guard for docs/scenarios.md (round-4 LOW: no docs guard):
    the reference doc must mention every scenario id and every secondary
    attribute tag. Content-level drift is still reviewed by hand."""
    doc = (_REPO_ROOT / "docs" / "scenarios.md").read_text(encoding="utf-8")
    missing = [name for name in IMPLEMENTED_SCENARIOS if f"`{name}`" not in doc]
    assert not missing, f"docs/scenarios.md is missing scenario ids: {sorted(missing)}"
    missing_tags = [
        tag for tag in mm.SECONDARY_ATTRIBUTE_VOCABULARY if f"`{tag}`" not in doc
    ]
    assert not missing_tags, (
        f"docs/scenarios.md is missing secondary attributes: {missing_tags}"
    )


def test_all_implemented_scenarios_present(scenarios):
    missing = IMPLEMENTED_SCENARIOS - set(scenarios)
    assert not missing, f"missing implemented scenarios in taxonomy: {sorted(missing)}"


def test_no_unexpected_scenarios_in_implemented_block(scenarios):
    extra = set(scenarios) - IMPLEMENTED_SCENARIOS
    assert not extra, (
        "unexpected scenarios under 'scenarios:' (planned ones belong under "
        f"'planned_scenarios:'): {sorted(extra)}"
    )


def test_planned_block_never_shadows_an_implemented_scenario(taxonomy):
    planned = taxonomy.get("planned_scenarios") or {}
    shadowed = set(planned) & IMPLEMENTED_SCENARIOS
    assert not shadowed, (
        f"scenarios listed as planned but already implemented: {sorted(shadowed)}"
    )


@pytest.mark.parametrize("name", sorted(IMPLEMENTED_SCENARIOS))
def test_scenario_has_required_fields(scenarios, name):
    entry = scenarios[name]
    for field in REQUIRED_FIELDS:
        assert field in entry, f"{name} is missing required field '{field}'"


@pytest.mark.parametrize("name", sorted(IMPLEMENTED_SCENARIOS))
def test_tier_matches_scoring_registry(scenarios, name):
    tier = scenarios[name]["tier"]
    assert tier in VALID_TIERS, f"{name} has invalid tier {tier!r}"
    assert tier == mm.REASONING_TIER[name], (
        f"{name}: taxonomy tier {tier!r} != registry tier {mm.REASONING_TIER[name]!r}"
    )


@pytest.mark.parametrize("name", sorted(IMPLEMENTED_SCENARIOS))
def test_expected_action_matches_scoring_registry(scenarios, name):
    action = scenarios[name]["expected_action"]
    assert action in VALID_ACTIONS, (
        f"{name} has invalid expected_action {action!r}"
    )
    assert action == mm.SCENARIO_SPEC[name]["expected"], (
        f"{name}: taxonomy action {action!r} != registry action "
        f"{mm.SCENARIO_SPEC[name]['expected']!r}"
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
