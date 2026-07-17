from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import yaml

from marshal_bench.criteria.marshal_metrics import SCENARIO_SPEC
from marshal_bench.utils.station_search import (
    GENERATION_REQUIREMENT_FIELDS,
    HARD_REQUIREMENT_FIELDS,
    classify_requirements,
    validate_requirements,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "marshal_bench" / "configs"
SCENARIO_DIR = ROOT / "marshal_bench" / "scenarios"

MODULES = {
    "green_stop": "marshal_green_stop_demo.py",
    "red_proceed": "marshal_red_proceed_demo.py",
    "signal_off": "marshal_signal_officer_control_demo.py",
    **{
        name: f"marshal_{name}_demo.py"
        for name in SCENARIO_SPEC
        if name not in {"green_stop", "red_proceed", "signal_off"}
    },
}
CONFIGS = {name: CONFIG_DIR / f"demo_{name}.yaml" for name in SCENARIO_SPEC}


def _shared_staging_module():
    path = ROOT / "scripts" / "_shared_staging.py"
    spec = importlib.util.spec_from_file_location("_requirements_shared_staging", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _called_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_staging_requirements_cover_exact_canonical_scenarios():
    payload = json.loads(
        (CONFIG_DIR / "staging_requirements.json").read_text(encoding="utf-8")
    )
    assert set(payload["criterion_classes"]["hard"]) == set(HARD_REQUIREMENT_FIELDS)
    assert set(payload["criterion_classes"]["generation"]) == set(
        GENERATION_REQUIREMENT_FIELDS
    )
    requirements = payload["scenarios"]
    assert set(requirements) == set(SCENARIO_SPEC)
    assert len(requirements) == 21
    for scenario, entry in requirements.items():
        classified = classify_requirements(
            entry, payload["criterion_classes"], payload["criterion_defaults"]
        )
        assert validate_requirements(classified) == [], scenario


def test_requirements_match_executable_stagers_and_configs():
    requirements = json.loads(
        (CONFIG_DIR / "staging_requirements.json").read_text(encoding="utf-8")
    )["scenarios"]
    shared = _shared_staging_module()
    for scenario, entry in requirements.items():
        module_path = SCENARIO_DIR / MODULES[scenario]
        config = yaml.safe_load(CONFIGS[scenario].read_text(encoding="utf-8")) or {}
        calls = _called_functions(module_path)

        # A non-empty traffic_light.state is a signal episode. The 28 m picker
        # is candidate-generation policy and is skipped for curated stations.
        has_signal = bool((config.get("traffic_light") or {}).get("state"))
        assert entry["needs_traffic_light"] is has_signal, scenario
        assert entry["needs_junction_approach"] is has_signal, scenario
        assert entry["min_runup_m"] == (28.0 if has_signal else 0.0), scenario

        has_officer = bool(config.get("officer"))
        # _common.py:395-428 and :489-525 apply a raw route-relative lateral
        # transform and spawn the officer without a Sidewalk/Shoulder query.
        assert entry["needs_sidewalk_point"] is False, scenario
        assert entry["prefers_sidewalk_point"] is has_officer, scenario
        expected_offset = (
            3.2 if scenario in shared.AUTHORITY_FIGURE_SCENARIOS else 2.2
        ) if has_officer else 0.0
        assert entry["officer_lateral_offset_m"] == expected_offset, scenario

        assert entry["needs_adjacent_same_road_lane"] is (
            "spawn_adjacent_vehicle" in calls
        ), scenario
        assert entry["needs_detour_room"] is (
            SCENARIO_SPEC[scenario]["expected"] == "DETOUR"
        ), scenario
        assert entry["min_detour_clearance_m"] == (
            1.5 if SCENARIO_SPEC[scenario]["expected"] == "DETOUR" else 0.0
        ), scenario
        assert entry["needs_offroad_shoulder"] is ("spawn_ambulance" in calls), scenario

        # Every entry cites its concrete stager, not merely a prose rationale.
        assert MODULES[scenario].removesuffix(".py") in entry["notes"], scenario


def test_requirement_note_python_line_references_exist():
    requirements = json.loads(
        (CONFIG_DIR / "staging_requirements.json").read_text(encoding="utf-8")
    )["scenarios"]
    reference = re.compile(r"([A-Za-z0-9_]+\.py):(\d+)(?:-(\d+))?")
    by_name = {}
    for path in ROOT.rglob("*.py"):
        by_name.setdefault(path.name, []).append(path)
    for scenario, entry in requirements.items():
        matches = reference.findall(entry["notes"])
        assert matches, scenario
        for filename, first, last in matches:
            assert filename in by_name, (scenario, filename)
            line_count = max(len(path.read_text(encoding="utf-8").splitlines()) for path in by_name[filename])
            assert 1 <= int(first) <= line_count, (scenario, filename, first)
            if last:
                assert int(first) <= int(last) <= line_count, (scenario, filename, last)
