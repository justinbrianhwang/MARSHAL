"""INVALID episodes are EXCLUDED from R6 means, never scored 0.0.

Regression for the Kimi round-2 medium finding: a quarantined INVALID
episode (scene setup broke, e.g. the staging integrity guard fired) used to
re-score as graded 0.0 and stay inside the R6 means — silently understating
a condition run, or depressing the baseline into clamp-inflated (lucky)
retentions.
"""
import json

from scripts import report_robustness as rr


def _write_episode(run_dir, name, result):
    d = run_dir / name
    d.mkdir(parents=True)
    (d / "result.json").write_text(
        json.dumps({"result": result}), encoding="utf-8"
    )
    return d


def test_invalid_by_strict_flag_is_excluded(tmp_path):
    d = _write_episode(tmp_path, "marshal_esb_001", {
        "strict_scoring": {"invalid": True, "verdict": "INVALID"},
        "scene_setup": {"errors": []},
    })
    credit, invalid = rr._episode_graded(d)
    assert credit is None
    assert invalid is True


def test_invalid_by_setup_errors_is_excluded(tmp_path):
    d = _write_episode(tmp_path, "marshal_esb_001", {
        "strict_scoring": {"invalid": False},
        "scene_setup": {
            "errors": ["staging integrity: blocking actor 42 moved 76.0 m"]
        },
    })
    credit, invalid = rr._episode_graded(d)
    assert credit is None
    assert invalid is True


def test_missing_artifacts_are_not_counted_as_invalid(tmp_path):
    d = tmp_path / "marshal_esb_001"
    d.mkdir()
    credit, invalid = rr._episode_graded(d)
    assert credit is None
    assert invalid is False


def test_run_mean_counts_invalid_exclusions(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "BENCH_ROOT", tmp_path)
    run = tmp_path / "sometag"
    _write_episode(run, "marshal_a_001", {
        "strict_scoring": {"invalid": True},
        "scene_setup": {"errors": []},
    })
    # Valid but unscoreable (no telemetry): unscored, NOT invalid.
    _write_episode(run, "marshal_b_001", {
        "strict_scoring": {"invalid": False},
        "scene_setup": {"errors": []},
    })
    mean, n, invalid, board = rr._run_graded_mean("sometag")
    assert mean is None
    assert n == 0
    assert invalid == 1
    assert board is None
