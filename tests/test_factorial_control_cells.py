"""Factorial control cells for the oracle-assist ablation: neutral / policy_only.

The cumulative ladder (pinned by tests/test_vlm_ablation_assist.py) leaves two
reviewer-attacked caveats open: prompt-length/wording effects between rungs
are uncontrolled, and the policy rung confounds knowledge + plan. Two
non-cumulative control cells close both, and this file pins their contract:

- ``neutral`` emits a FIXED filler block with zero scene content, length-
  matched to the ladder's strongest knowledge block, and requests NO
  privileged ground truth on either wiring;
- ``policy_only`` emits ONLY the per-tick oracle token line (byte-identical
  to the line the policy rung appends), requests privileged GT, and arms the
  shadow oracle exactly like the policy rung;
- neither cell is a ladder rung: the ladder's levels, order, and text are
  untouched, and unknown levels are still rejected.
"""

import re

import pytest

from marshal_bench.controllers.ablation_assist import (
    ABLATION_LEVELS,
    CONTROL_LEVELS,
    NEUTRAL_ASSIST_TEXT,
    AblationAssist,
)
from marshal_bench.controllers.openemma_model import OpenEMMAController
from marshal_bench.controllers.vlm_model import VLMController

# Mean character length of the `action` (L5) assist blocks actually injected
# in the committed VLM v2 run, measured over the per-decision `assist` audit
# field of MARSHAL/outputs/oracle_ablation/_ablation_v2_action.json:
# 604.7257 chars across 226 decisions (min 227, max 955). The neutral block
# must land within +/-10% of this mean.
_VLM_V2_ACTION_ASSIST_MEAN_CHARS = 604.7

# Scene words the neutral block must NOT contain (case-insensitive,
# word-boundary): no directors, no gestures, no signals, no action guidance.
_BANNED_SCENE_WORDS = (
    "officer", "gesture", "director", "authority", "signal", "light",
    "stop", "go", "hold", "slow", "proceed", "intersection", "vest",
    "police",
)

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

_POLICY_LINE = ("- Policy (per-tick oracle): the correct action at this "
                "instant is GO.")


def _vlm(level):
    c = VLMController({"vlm": {"backend": "mock", "ablation": level}})
    c._gt = dict(_GT)
    c._last_policy_token = "GO"
    return c


def _openemma(level):
    c = OpenEMMAController({"openemma": {"ablation": level}})
    c._assist.gt = dict(_GT)
    c._assist.last_policy_token = "GO"
    return c


# ---------------------------------------------------------------------------
# Level parsing / ladder separation
# ---------------------------------------------------------------------------
def test_control_levels_are_not_ladder_rungs():
    assert CONTROL_LEVELS == ("neutral", "policy_only")
    # The cumulative ladder is untouched: same seven rungs, same order.
    assert ABLATION_LEVELS == ("none", "perception", "authority", "semantics",
                               "temporal", "action", "policy")
    assert set(CONTROL_LEVELS).isdisjoint(ABLATION_LEVELS)
    for level in CONTROL_LEVELS:
        assert AblationAssist(level).rank is None


def test_unknown_level_still_rejected():
    for bad in ("everything", "neutralx", "policy-only", "policyonly"):
        with pytest.raises(ValueError):
            AblationAssist(bad)
        with pytest.raises(ValueError):
            VLMController({"vlm": {"backend": "mock", "ablation": bad}})
        with pytest.raises(ValueError):
            OpenEMMAController({"openemma": {"ablation": bad}})


def test_env_var_fallback_accepts_control_levels(monkeypatch):
    for level in CONTROL_LEVELS:
        monkeypatch.setenv("MARSHAL_VLM_ABLATION", level)
        assert VLMController({"vlm": {"backend": "mock"}}).ablation == level
        assert OpenEMMAController(
            {"openemma": {}})._assist.level == level


# ---------------------------------------------------------------------------
# neutral: fixed, length-matched, scene-free, no privileged GT
# ---------------------------------------------------------------------------
def test_neutral_block_is_constant():
    c = _vlm("neutral")
    texts = {c._ablation_assist(t) for t in (0.0, 0.2, 5.0, 13.0, 14.0)}
    assert texts == {NEUTRAL_ASSIST_TEXT}
    # ... independent of ground truth and officer state, and identical
    # across instances (it is one module-level constant).
    c._gt = {}
    c._officer_ref = None
    assert c._ablation_assist(5.0) == NEUTRAL_ASSIST_TEXT
    assert _vlm("neutral")._ablation_assist(5.0) is NEUTRAL_ASSIST_TEXT


def test_neutral_block_length_matches_l5_mean():
    tolerance = 0.10 * _VLM_V2_ACTION_ASSIST_MEAN_CHARS
    assert abs(len(NEUTRAL_ASSIST_TEXT)
               - _VLM_V2_ACTION_ASSIST_MEAN_CHARS) <= tolerance


def test_neutral_block_contains_no_scene_words():
    for word in _BANNED_SCENE_WORDS:
        assert not re.search(rf"\b{re.escape(word)}\b", NEUTRAL_ASSIST_TEXT,
                             re.IGNORECASE), f"banned scene word: {word}"


def test_neutral_requests_no_privileged_gt_on_either_wiring():
    assert AblationAssist("neutral").requests_privileged_gt is False
    assert _vlm("neutral").requests_privileged_gt is False
    assert _openemma("neutral").requests_privileged_gt is False


def test_neutral_arms_no_shadow_oracle():
    assert AblationAssist("neutral").arms_shadow_oracle is False


# ---------------------------------------------------------------------------
# policy_only: the per-tick oracle line alone
# ---------------------------------------------------------------------------
def test_policy_only_emits_exactly_the_policy_line():
    c = _vlm("policy_only")
    text = c._ablation_assist(5.0)
    assert text == _POLICY_LINE + "\n"
    # Byte-identical to the line the policy rung appends, lead-in included...
    policy_text = _vlm("policy")._ablation_assist(5.0)
    assert _POLICY_LINE in policy_text.split("\n")
    # ... and nothing else: no ladder header, no L1-L5 knowledge text.
    assert "GROUND-TRUTH ASSISTS" not in text
    for knowledge in ("Perception:", "Authority:", "Semantics:", "Temporal:",
                      "Expected outcome"):
        assert knowledge not in text


def test_policy_only_emits_nothing_before_the_shadow_token_exists():
    for controller in (_vlm("policy_only"), _openemma("policy_only")):
        controller._assist.last_policy_token = None
        assert controller._ablation_assist(5.0) == ""


def test_policy_only_requests_privileged_gt_on_either_wiring():
    assert AblationAssist("policy_only").requests_privileged_gt is True
    assert _vlm("policy_only").requests_privileged_gt is True
    assert _openemma("policy_only").requests_privileged_gt is True


def test_policy_only_arms_the_shadow_oracle_like_policy():
    assert AblationAssist("policy_only").arms_shadow_oracle is True
    assert AblationAssist("policy").arms_shadow_oracle is True
    # ... and no other rung arms it.
    for level in ("none", "perception", "authority", "semantics", "temporal",
                  "action", "neutral"):
        assert AblationAssist(level).arms_shadow_oracle is False


def test_policy_only_validates_base_gt_but_never_the_l5_answer_key():
    # policy_only renders no L5 text, so a missing/unusable answer key must
    # NOT fail its validation (the ladder's action rung still fails loudly).
    c = AblationAssist("policy_only")
    c.set_ground_truth({**_GT, "Y_expected_action": "TELEPORT"})
    c.validate_gt()  # no raise
    c.set_ground_truth({k: v for k, v in _GT.items()
                        if k != "Y_expected_action"})
    c.validate_gt()  # no raise
    # ... while the privileged base keys are still enforced.
    c.set_ground_truth({k: v for k, v in _GT.items() if k != "L_light_state"})
    with pytest.raises(ValueError, match="L_light_state"):
        c.validate_gt()


# ---------------------------------------------------------------------------
# Cross-wiring byte identity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("level", CONTROL_LEVELS)
def test_control_cells_are_byte_identical_across_wirings(level):
    vlm = _vlm(level)
    openemma = _openemma(level)
    for sim_time in (0.2, 5.0, 13.0, 14.0):
        assert openemma._ablation_assist(sim_time) == vlm._ablation_assist(sim_time)
    # ... including the no-token-yet state.
    vlm._last_policy_token = None
    openemma._assist.last_policy_token = None
    assert openemma._ablation_assist(5.0) == vlm._ablation_assist(5.0) == (
        NEUTRAL_ASSIST_TEXT if level == "neutral" else "")
