"""Role x gesture factorial cells: each factor injects only its own fact, and
role_gesture is exactly the two single-factor blocks."""

from marshal_bench.controllers.ablation_assist import (
    ABLATION_LEVELS, CONTROL_LEVELS, FACTOR_LEVELS, AblationAssist)

_GT = {
    "A_authority": {"type": "civilian", "valid": False},
    "A_appearance": "in a high-visibility vest",
    "G_gesture": "PROCEED",
    "G_gesture_onset_sec": 2.0,
    "G_gesture_duration_sec": 10.0,
    "L_light_state": "Red",
    "Y_expected_action": "STOP",
    "ego_spawn": {"x": 0.0, "y": 0.0, "z": 0.5},
}
_HEADER = "GROUND-TRUTH ASSISTS (diagnostic ablation study):"


def _text(level, t, gt=_GT):
    a = AblationAssist(level)
    a.set_ground_truth(gt)
    a.validate_gt()
    return a.assist(t)


def test_levels_outside_ladder_and_request_gt():
    assert set(FACTOR_LEVELS).isdisjoint(ABLATION_LEVELS + CONTROL_LEVELS)
    for lvl in FACTOR_LEVELS:
        a = AblationAssist(lvl)
        assert a.rank is None and a.requests_privileged_gt and not a.arms_shadow_oracle


def test_each_factor_carries_only_its_fact():
    role, sign = _text("role", 5.0), _text("gesture", 5.0)
    assert "high-visibility vest" in role and "is NOT a legally valid" in role
    assert "PROCEED" not in role and "Red" not in role
    assert "PROCEED hand signal is being shown" in sign
    assert "vest" not in sign and "civilian" not in sign and "legally" not in sign and "Red" not in sign
    for txt in (role, sign):  # no meaning, no answer key
        assert "means" not in txt and "Expected" not in txt and "STOP" not in txt


def test_both_is_concatenation_of_singles():
    for t in (0.5, 5.0, 20.0):
        role, sign, both = (_text(lvl, t).splitlines() for lvl in FACTOR_LEVELS)
        assert both == [_HEADER] + role[1:] + sign[1:]


def test_gesture_tracks_the_instant_and_no_director():
    assert "no hand signal is being shown at this moment" in _text("gesture", 0.5)
    assert "was shown earlier" in _text("gesture", 20.0)
    gt = dict(_GT, A_authority={"type": "none"})
    assert "no human director" in _text("role", 5.0, gt)
    assert "no hand signal is being shown in this scene" in _text("gesture", 5.0, gt)


def test_malformed_gesture_gt_is_rejected():
    import pytest
    bad = (dict(_GT, G_gesture=""),
           dict(_GT, A_second_authority={"type": "flagger", "valid": True, "gesture": ""}),
           dict(_GT, A_authority={"type": "none"},
                A_second_authority={"type": "flagger", "valid": True, "gesture": "STOP"}))
    for gt in bad:
        a = AblationAssist("gesture")
        a.set_ground_truth(gt)
        with pytest.raises(ValueError):
            a.validate_gt()
