"""The oracle-assist ablation ladder's prompt-injection blocks.

The ladder is only interpretable if every rung injects TRUE information:
perception must describe what is visible at the query instant (a gesture
that has not started yet shows an idle person), and each rung must add
exactly one link.
"""

import pytest

from marshal_bench.controllers.vlm_model import ABLATION_LEVELS, VLMController

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


def _controller(level):
    c = VLMController({"vlm": {"backend": "mock", "ablation": level}})
    c._gt = dict(_GT)
    return c


def test_level_none_injects_nothing():
    assert _controller("none")._ablation_assist(5.0) == ""


def test_unknown_level_rejected():
    with pytest.raises(ValueError):
        VLMController({"vlm": {"backend": "mock", "ablation": "everything"}})


def test_ladder_is_cumulative():
    lengths = [len(_controller(lv)._ablation_assist(5.0))
               for lv in ABLATION_LEVELS]
    assert lengths == sorted(lengths)
    assert lengths[0] == 0 and lengths[1] > 0


def test_perception_is_time_aware():
    c = _controller("perception")
    before = c._ablation_assist(0.2)
    during = c._ablation_assist(5.0)
    after = c._ablation_assist(14.0)
    assert "standing idle (not signalling" in before
    assert "making the PROCEED hand signal" in during
    assert "standing idle now (was making" in after
    # Perception never interprets: no authority/semantics/temporal claims.
    for text in (before, during, after):
        assert "valid traffic authority" not in text
        assert "means:" not in text
        assert "directive" not in text


def test_temporal_marks_active_and_expired():
    c = _controller("temporal")
    assert "is ACTIVE right now" in c._ablation_assist(5.0)
    assert "has EXPIRED" in c._ablation_assist(14.0)
    assert "has NOT started yet" in c._ablation_assist(0.2)


def test_action_level_names_the_expected_action():
    text = _controller("action")._ablation_assist(5.0)
    assert "Expected action (ground truth): PROCEED." in text


def test_invalid_authority_wording():
    c = _controller("authority")
    c._gt = {**_GT, "A_authority": {"type": "civilian", "valid": False}}
    text = c._ablation_assist(5.0)
    assert "NOT a valid traffic authority" in text


def test_privileged_optin_only_when_ablated():
    assert _controller("none").requests_privileged_gt is False
    assert _controller("perception").requests_privileged_gt is True
