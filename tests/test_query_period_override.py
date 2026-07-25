"""Pins for the diagnostic query-cadence override (common-cadence control).

The override must never touch the leaderboard wiring (pinned 1.5 s), must
tag diagnostic episode ids, and must be part of row identity so a
cadence-override row can never overwrite a native-cadence row.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

runner = importlib.import_module("_run_vlm_test")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("MARSHAL_VLM_QUERY_PERIOD_S", raising=False)
    monkeypatch.delenv("MARSHAL_VLM_ABLATION", raising=False)


def test_leaderboard_wiring_is_pinned_even_with_env_set(monkeypatch):
    monkeypatch.setenv("MARSHAL_VLM_QUERY_PERIOD_S", "3.0")
    period, tag = runner._query_period_override(diagnostic=False)
    assert period == 1.5
    assert tag == ""


def test_diagnostic_without_env_stays_native():
    period, tag = runner._query_period_override(diagnostic=True)
    assert period == 1.5
    assert tag == ""


def test_diagnostic_override_parses_and_tags(monkeypatch):
    monkeypatch.setenv("MARSHAL_VLM_QUERY_PERIOD_S", "3.0")
    period, tag = runner._query_period_override(diagnostic=True)
    assert period == 3.0
    assert tag == "qp3.0_"


def test_tag_is_formatted_from_the_parsed_float(monkeypatch):
    monkeypatch.setenv("MARSHAL_VLM_QUERY_PERIOD_S", " 1.50 ")
    period, tag = runner._query_period_override(diagnostic=True)
    assert period == 1.5
    assert tag == "qp1.5_"


def test_nonpositive_override_is_refused(monkeypatch):
    monkeypatch.setenv("MARSHAL_VLM_QUERY_PERIOD_S", "0")
    with pytest.raises(SystemExit):
        runner._query_period_override(diagnostic=True)


def test_failure_rows_share_the_tagged_episode_id(monkeypatch):
    monkeypatch.setenv("MARSHAL_VLM_ABLATION", "policy_only")
    monkeypatch.setenv("MARSHAL_VLM_QUERY_PERIOD_S", "3.0")
    diagnostic, ablation, qp_tag = runner._diagnostic_id_parts()
    assert (diagnostic, ablation, qp_tag) == (True, "policy_only", "qp3.0_")
    row = runner._native_failure_row("zai-org/GLM-4.5V", "green_stop", "boom")
    assert "ablate-policy_only_qp3.0_" in row["episode_dir"]
    assert row["query_period_s"] == 3.0


def test_row_key_includes_cadence_with_legacy_default():
    base = {"model": "m", "scenario": "s", "ablation": "policy_only"}
    legacy = runner._row_key(dict(base))
    native = runner._row_key(dict(base, query_period_s=1.5))
    slowed = runner._row_key(dict(base, query_period_s=3.0))
    assert legacy == native, "rows without the field must key as native 1.5 s"
    assert slowed != native, "a cadence-override row must never merge over a native row"
