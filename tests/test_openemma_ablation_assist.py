"""The OpenEMMA planner wiring consumes the SAME oracle-assist ladder as the
per-tick VLM wiring.

The ladder is only comparable across wirings if the injected text is
IDENTICAL, so for the same ground truth, sim time, and live-officer state the
OpenEMMA path must produce byte-identical assist text to the VLM path. It must
also opt into privileged ground truth only when ablated, and reject unknown
levels just as loudly.
"""

import pytest

from marshal_bench.controllers.ablation_assist import ABLATION_LEVELS
from marshal_bench.controllers.openemma_model import (
    OpenEMMAController,
    _OpenEMMAQwenBackend,
)
from marshal_bench.controllers.vlm_model import VLMController

_GT = {
    "A_authority": {"type": "police", "valid": True},
    "G_gesture": "PROCEED",
    "G_gesture_onset_sec": 1.0,
    "G_gesture_duration_sec": 12.0,
    "T_target_relation": "ego",
    "L_light_state": "Red",
    "Y_expected_action": "PROCEED",
    "officer_transform": {"x": 24.0, "y": 2.0, "z": 0.5},
    "ego_spawn": {"x": 0.0, "y": 0.0, "z": 0.5},
}


def _vlm(level):
    c = VLMController({"vlm": {"backend": "mock", "ablation": level}})
    c._gt = dict(_GT)
    # the policy rung reads the cached shadow-oracle token
    c._last_policy_token = "GO"
    return c


def _openemma(level):
    c = OpenEMMAController({"openemma": {"ablation": level}})
    c._assist.gt = dict(_GT)
    c._assist.last_policy_token = "GO"
    return c


@pytest.mark.parametrize("level", ABLATION_LEVELS)
def test_assist_text_is_byte_identical_to_vlm(level):
    vlm = _vlm(level)
    openemma = _openemma(level)
    # sweep the time-aware branches (before onset / inside / past the window)
    for sim_time in (0.2, 5.0, 13.0, 14.0):
        assert openemma._ablation_assist(sim_time) == vlm._ablation_assist(sim_time)


def test_none_level_injects_nothing():
    assert _openemma("none")._ablation_assist(5.0) == ""


def test_policy_line_absent_until_shadow_produces_a_token():
    c = _openemma("policy")
    c._assist.last_policy_token = None
    assert "Policy (per-tick oracle)" not in c._ablation_assist(5.0)


def test_live_officer_state_is_shared():
    # flagger_slow_then_stop: the snapshot says SLOW(1..6) but the live
    # flagger shows STOP(6..16) — both wirings must describe the live phase.
    class _LiveRef:
        class _Actor:
            is_alive = True

        def get_actor(self):
            return self._Actor()

        def get_metadata(self):
            return {"gesture_id": "STOP", "onset_time": 6.0, "duration": 10.0}

    vlm = _vlm("temporal")
    vlm._gt = {**_GT, "G_gesture": "SLOW",
               "G_gesture_onset_sec": 1.0, "G_gesture_duration_sec": 5.0}
    vlm._officer_ref = _LiveRef()
    openemma = _openemma("temporal")
    openemma._assist.gt = dict(vlm._gt)
    openemma.set_officer_ref(_LiveRef())
    assert openemma._ablation_assist(8.0) == vlm._ablation_assist(8.0)
    assert "making the STOP hand signal" in openemma._ablation_assist(8.0)


def test_privileged_optin_only_when_ablated():
    assert OpenEMMAController(
        {"openemma": {"ablation": "none"}}).requests_privileged_gt is False
    for level in ABLATION_LEVELS[1:]:
        assert OpenEMMAController(
            {"openemma": {"ablation": level}}).requests_privileged_gt is True


def test_unknown_level_rejected():
    with pytest.raises(ValueError):
        OpenEMMAController({"openemma": {"ablation": "everything"}})


def test_env_var_fallback(monkeypatch):
    monkeypatch.setenv("MARSHAL_VLM_ABLATION", "authority")
    c = OpenEMMAController({"openemma": {}})
    assert c._assist.level == "authority"
    assert c.requests_privileged_gt is True


def test_malformed_gt_fails_loudly():
    c = _openemma("perception")
    c._assist.set_ground_truth(
        {**_GT, "A_authority": {"type": "police", "valid": None}})
    with pytest.raises(ValueError, match="real bool"):
        c._assist.validate_gt()


def test_prepend_assist_delimits_block_without_touching_prompt():
    prompt = "You are an autonomous driving motion planner in CARLA simulator."
    # no assist -> the prompt object is passed through unchanged
    assert _OpenEMMAQwenBackend._prepend_assist(prompt, "") == prompt
    assist = _openemma("perception")._ablation_assist(5.0)
    out = _OpenEMMAQwenBackend._prepend_assist(prompt, assist)
    assert out == f"{assist}---\n{prompt}"
    assert out.startswith("GROUND-TRUTH ASSISTS (diagnostic ablation study):")
    assert out.endswith(prompt)
