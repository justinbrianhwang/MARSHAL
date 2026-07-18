import json
import logging

import pytest

import start
from marshal_bench.scenarios import _common


@pytest.fixture(autouse=True)
def _clear_station_caches():
    _common._STATIONS_CACHE = None
    _common._STATIONS_BY_TOWN_CACHE.clear()
    yield
    _common._STATIONS_CACHE = None
    _common._STATIONS_BY_TOWN_CACHE.clear()


def _write_station_file(path, scenario, x):
    path.write_text(json.dumps({
        "_comment": "synthetic",
        "map": path.stem,
        "stations": {
            scenario: {"x": x, "y": x + 1, "z": 0.5, "yaw": x + 2, "tl_id": 1, "lanes": 2}
        },
    }), encoding="utf-8")


def test_town03_default_and_explicit_paths_return_identical_curated_station():
    absent = _common._load_station("green_stop")
    explicit = _common._load_station("green_stop", town="Town03")
    map_variant = _common._load_station("green_stop", town="/Game/Carla/Maps/Town03_MARSHAL")

    assert absent == explicit == map_variant
    assert absent == {"x": -74.3, "y": 29.6, "z": 0.5, "yaw": 269.8}


def test_per_town_file_selection_and_cache_isolation(tmp_path, monkeypatch):
    _write_station_file(tmp_path / "stations.json", "green_stop", 3)
    _write_station_file(tmp_path / "stations_town01.json", "green_stop", 1)
    _write_station_file(tmp_path / "stations_town05.json", "green_stop", 5)
    monkeypatch.setattr(_common, "_CONFIGS_DIR", str(tmp_path))

    town01 = _common._load_station("green_stop", town="Town01")
    town05 = _common._load_station("green_stop", town="/Game/Carla/Maps/Town05")
    town03 = _common._load_station("green_stop", town="Town03")

    assert town01["x"] == 1.0
    assert town05["x"] == 5.0
    assert town03["x"] == 3.0
    assert len(_common._STATIONS_BY_TOWN_CACHE) == 2


def test_non_town03_missing_file_and_key_warn_without_fallback(tmp_path, monkeypatch, caplog):
    _write_station_file(tmp_path / "stations.json", "green_stop", 3)
    _write_station_file(tmp_path / "stations_town05.json", "red_proceed", 5)
    monkeypatch.setattr(_common, "_CONFIGS_DIR", str(tmp_path))
    caplog.set_level(logging.WARNING)

    assert _common._load_station("green_stop", town="Town01") is None
    assert "stations_town01.json" in caplog.text
    caplog.clear()
    assert _common._load_station("green_stop", town="Town05") is None
    assert "green_stop" in caplog.text
    assert "stations_town05.json" in caplog.text


def test_unknown_town_keeps_legacy_station_lookup(tmp_path, monkeypatch):
    _write_station_file(tmp_path / "stations.json", "green_stop", 9)
    monkeypatch.setattr(_common, "_CONFIGS_DIR", str(tmp_path))

    assert _common._load_station("green_stop", town="custom_map")["x"] == 9.0


def _passing_result(scenario):
    return {
        "episode_id": f"episode-{scenario}",
        "scenario": scenario,
        "strict_scoring": {"passed": True, "verdict": "PASS", "invalid": False, "collision_count": 0},
        "compliance": {"passed": True, "collision": False, "crossed_stop_line": False},
        "latency": {"detected": True, "latency": 1.0},
        "officer_metadata": {"authority_valid": True, "target_relation": "ego"},
        "traffic_light_state": "Green",
    }


def test_feasibility_skip_is_visible_and_excluded_from_all_denominators(
    tmp_path, monkeypatch, capsys
):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "feasibility_town01.json").write_text(json.dumps({
        "green_stop": {"feasible": False, "reason": "off-road shoulder required"},
        "red_proceed": {"feasible": True, "reason": "ok"},
    }), encoding="utf-8")
    monkeypatch.setattr(start, "CONFIGS_DIR", str(configs))
    calls = []

    def fake_run(controller, scenario, args, out_root):
        calls.append(scenario)
        return _passing_result(scenario)

    monkeypatch.setattr(start, "_run_episode", fake_run)
    out = tmp_path / "out"
    assert start.main([
        "--controller", "oracle", "--town", "Town01",
        "--scenarios", "green_stop", "red_proceed", "--out", str(out),
    ]) == 0

    board = json.loads((out / "oracle" / "scoreboard.json").read_text(encoding="utf-8"))
    assert calls == ["red_proceed"]
    assert board["n_episodes"] == 1
    assert board["per_scenario_pass"]["green_stop"] == {
        "status": "infeasible_on_map",
        "reason": "off-road shoulder required",
    }
    assert board["conflict_type_profile"]["override"]["total"] == 1
    assert "SKIP (infeasible on Town01): green_stop — off-road shoulder required" in capsys.readouterr().out


def test_town03_ignores_generated_feasibility_mask(tmp_path, monkeypatch):
    (tmp_path / "feasibility_town03.json").write_text(json.dumps({
        "green_stop": {"feasible": False, "reason": "must not alter legacy run"}
    }), encoding="utf-8")
    monkeypatch.setattr(start, "CONFIGS_DIR", str(tmp_path))

    assert start._shape_infeasible_entries(["green_stop"], "Town03") == {}
