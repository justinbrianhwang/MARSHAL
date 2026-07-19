"""start.py _run_episode crash-retry matrix (Kimi finding 6.2)."""
import subprocess
import time
from types import SimpleNamespace

import start


def _args(timeout=14):
    return SimpleNamespace(
        town="Town03", host="127.0.0.1", port=2000, fps=20.0,
        episode_timeout=timeout, debug=False,
        weather=None, weather_params=None,
    )


def _patch(monkeypatch, run_effects, results):
    """run_effects: per-attempt returncode int, or an exception instance.
    results: per-attempt value _collect_result should return."""
    calls = {"run": 0}

    def fake_run(*_a, **_k):
        effect = run_effects[min(calls["run"], len(run_effects) - 1)]
        calls["run"] += 1
        if isinstance(effect, Exception):
            raise effect
        return SimpleNamespace(returncode=effect)

    collected = {"n": 0}

    def fake_collect():
        value = results[min(collected["n"], len(results) - 1)]
        collected["n"] += 1
        return value

    monkeypatch.setattr(start.subprocess, "run", fake_run)
    monkeypatch.setattr(start.os.path, "isdir", lambda _p: True)
    monkeypatch.setattr(start.os, "listdir", lambda _p: [])
    # A result.json written during this invocation (fresh by default; the
    # stale-artifact test overrides this with an old timestamp).
    monkeypatch.setattr(start.os.path, "getmtime", lambda _p: time.time() + 60)
    return calls, fake_collect


def test_crash_without_result_retries_once_and_uses_retry_result(monkeypatch):
    calls, _ = _patch(monkeypatch, [3221226505, 0], [None])
    good = {"scenario": "green_stop"}
    # Attempt 1: crash, no episode dir yet. Attempt 2: result.json appears.
    results = iter([[], ["marshal_green_stop_run"]])
    monkeypatch.setattr(start.os, "listdir", lambda _p: next(results))
    monkeypatch.setattr(start.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(
        start.json, "load", lambda _f: {"result": good})
    import builtins
    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open",
        lambda *a, **k: SimpleNamespace(__enter__=lambda s: s, __exit__=lambda *x: None)
        if str(a[0]).endswith("result.json") else real_open(*a, **k),
    )
    out = start._run_episode("oracle", "green_stop", _args(), "outdir")
    assert out == good
    assert calls["run"] == 2  # exactly one retry


def test_crash_with_result_present_is_not_retried(monkeypatch):
    calls, _ = _patch(monkeypatch, [3221226505], [None])
    good = {"scenario": "green_stop"}
    monkeypatch.setattr(start.os, "listdir", lambda _p: ["marshal_green_stop_run"])
    monkeypatch.setattr(start.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(start.json, "load", lambda _f: {"result": good})
    import builtins
    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open",
        lambda *a, **k: SimpleNamespace(__enter__=lambda s: s, __exit__=lambda *x: None)
        if str(a[0]).endswith("result.json") else real_open(*a, **k),
    )
    out = start._run_episode("oracle", "green_stop", _args(), "outdir")
    assert out == good
    assert calls["run"] == 1


def test_timeout_is_never_retried(monkeypatch):
    calls, _ = _patch(
        monkeypatch,
        [subprocess.TimeoutExpired(cmd="x", timeout=14)],
        [None],
    )
    monkeypatch.setattr(start.os, "listdir", lambda _p: [])
    out = start._run_episode("oracle", "green_stop", _args(), "outdir")
    assert out is None
    assert calls["run"] == 1


def test_clean_failure_without_result_is_not_retried(monkeypatch):
    calls, _ = _patch(monkeypatch, [0], [None])
    monkeypatch.setattr(start.os, "listdir", lambda _p: [])
    out = start._run_episode("oracle", "green_stop", _args(), "outdir")
    assert out is None
    assert calls["run"] == 1


def test_stale_result_from_prior_run_is_ignored_and_crash_retried(monkeypatch):
    """A double-crash on a reused --tag must NOT return the previous run's
    result.json: its mtime predates this invocation, so it is skipped and
    the crash is retried (then reported as a real failure)."""
    calls, _ = _patch(monkeypatch, [3221226505, 3221226505], [None])
    stale = {"scenario": "green_stop", "stale": True}
    monkeypatch.setattr(start.os, "listdir", lambda _p: ["marshal_green_stop_run"])
    monkeypatch.setattr(start.os.path, "isfile", lambda _p: True)
    # The artifact is older than the run start -> must be treated as stale.
    monkeypatch.setattr(start.os.path, "getmtime", lambda _p: time.time() - 3600)
    monkeypatch.setattr(start.json, "load", lambda _f: {"result": stale})
    import builtins
    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open",
        lambda *a, **k: SimpleNamespace(__enter__=lambda s: s, __exit__=lambda *x: None)
        if str(a[0]).endswith("result.json") else real_open(*a, **k),
    )
    out = start._run_episode("oracle", "green_stop", _args(), "outdir")
    assert out is None            # stale result rejected
    assert calls["run"] == 2      # crash retried once
