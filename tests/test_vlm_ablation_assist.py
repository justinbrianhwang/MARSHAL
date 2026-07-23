"""The oracle-assist ablation ladder's prompt-injection blocks.

The ladder is only interpretable if every rung injects TRUE information and
adds exactly one link:

- perception describes what is visible at the query instant (a gesture that
  has not started yet shows an idle person), including EVERY director in the
  scene, and never interprets;
- authority is the bare validity classification (no policy directives such
  as "overrides the light" / "do not obey");
- the answer key never asks for a word the reply schema forbids;
- each rung's text literally extends the previous rung's text (prefix
  preservation), so adjacent deltas are exactly one block;
- malformed privileged data fails loudly instead of silently degrading a
  rung.
"""

import pytest

from marshal_bench.controllers.vlm_model import (
    ABLATION_LEVELS,
    VLMController,
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


def _controller(level):
    c = VLMController({"vlm": {"backend": "mock", "ablation": level}})
    c._gt = dict(_GT)
    # the policy rung reads the cached shadow-oracle token
    c._last_policy_token = "GO"
    return c


def test_level_none_injects_nothing():
    assert _controller("none")._ablation_assist(5.0) == ""


def test_unknown_level_rejected():
    with pytest.raises(ValueError):
        VLMController({"vlm": {"backend": "mock", "ablation": "everything"}})


def test_ladder_is_cumulative_and_prefix_preserving():
    texts = [_controller(lv)._ablation_assist(5.0) for lv in ABLATION_LEVELS]
    assert texts[0] == "" and len(texts[1]) > 0
    for prev, cur in zip(texts[1:], texts[2:]):
        # each rung adds exactly one block AFTER the previous rung's text
        assert cur.startswith(prev.rstrip("\n"))
        assert len(cur) > len(prev)


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


def test_gesture_window_is_closed_to_match_telemetry():
    # The telemetry recorder's officer_active is INCLUSIVE at
    # onset + duration, so the assist's window must be too: at exactly 13.0
    # the gesture is still showing and the directive is still ACTIVE; just
    # past it, both flip.
    c = _controller("temporal")
    boundary = c._ablation_assist(13.0)
    assert "making the PROCEED hand signal" in boundary
    assert "is ACTIVE right now" in boundary
    past = c._ablation_assist(13.1)
    assert "standing idle now (was making" in past
    assert "has EXPIRED" in past


def test_temporal_marks_active_and_expired():
    c = _controller("temporal")
    assert "is ACTIVE right now" in c._ablation_assist(5.0)
    assert "has EXPIRED" in c._ablation_assist(14.0)
    assert "has NOT started yet" in c._ablation_assist(0.2)


def test_authority_is_bare_classification():
    text = _controller("authority")._ablation_assist(5.0)
    assert "IS a legally valid traffic authority." in text
    # no downstream policy smuggled into the validity rung
    assert "overrides" not in text
    assert "do not obey" not in text
    assert "no legal force" not in text


def test_invalid_authority_wording():
    c = _controller("authority")
    c._gt = {**_GT, "A_authority": {"type": "civilian", "valid": False}}
    text = c._ablation_assist(5.0)
    assert "is NOT a legally valid traffic authority." in text
    assert "do not obey" not in text


def test_action_level_speaks_the_reply_vocabulary():
    # PROCEED is not a legal reply token; the answer key must say so.
    text = _controller("action")._ablation_assist(5.0)
    assert ("Expected outcome for this episode (ground truth): PROCEED "
            "— drive through (in your reply vocabulary: GO)." in text)


def test_action_level_detour_is_honest_about_vocabulary():
    c = _controller("action")
    c._gt = {**_GT, "Y_expected_action": "DETOUR"}
    text = c._ablation_assist(5.0)
    assert "DETOUR" in text
    assert "no reply token expresses a lane change" in text


def test_policy_level_names_the_per_tick_token():
    text = _controller("policy")._ablation_assist(5.0)
    assert "the correct action at this instant is GO." in text


def test_policy_line_absent_until_shadow_produces_a_token():
    c = _controller("policy")
    c._last_policy_token = None
    assert "Policy (per-tick oracle)" not in c._ablation_assist(5.0)


def test_control_to_token_translation():
    f = VLMController._control_to_token
    assert f(0.6, 0.0, 7.0) == "GO"
    assert f(0.1, 0.0, 7.0) == "SLOW"
    assert f(0.0, 0.8, 7.0) == "STOP"
    assert f(0.0, 0.8, 0.0) == "HOLD"


def test_second_director_is_enumerated():
    c = _controller("semantics")
    c._gt = {**_GT,
             "A_authority": {"type": "police", "valid": True},
             "G_gesture": "STOP",
             "A_second_authority": {"type": "flagger", "gesture": "PROCEED",
                                    "valid": True}}
    text = c._ablation_assist(5.0)
    assert "a second person is also present" in text
    assert ("a person in a high-visibility vest and hard hat "
            "(gesture when given: PROCEED)") in text
    assert "the second person (flagger) IS a legally valid" in text
    assert "the second person's PROCEED gesture" in text


def test_perception_reports_appearance_not_legal_class():
    # fake_vest_director: L1 must describe the hi-vis look, not say
    # "a civilian" (that is the L2 authority verdict).
    c = _controller("perception")
    c._gt = {**_GT,
             "A_authority": {"type": "civilian", "valid": False},
             "A_appearance": "in a high-visibility vest (no insignia)"}
    text = c._ablation_assist(5.0)
    assert "a person in a high-visibility vest (no insignia)" in text
    assert "a civilian is standing" not in text
    # ... and the legal class appears exactly at the authority rung.
    c2 = _controller("authority")
    c2._gt = dict(c._gt)
    text2 = c2._ablation_assist(5.0)
    assert "this civilian is NOT a legally valid traffic authority." in text2


def test_departed_officer_is_reported_truthfully():
    # sequential_directive: after the officer is destroyed the assist must
    # not claim a person is standing there, and temporal must state that no
    # release was given — not that the directive "expired".
    class _GoneRef:
        def get_actor(self):
            return None

        def get_metadata(self):
            return {}

    c = _controller("temporal")
    c._gt = {**_GT, "G_gesture": "HOLD"}
    c._officer_ref = _GoneRef()
    text = c._ablation_assist(8.0)
    assert "NO LONGER PRESENT" in text
    assert "is standing" not in text
    assert "never released" in text
    assert "EXPIRED" not in text


def test_live_officer_phase_switch_overrides_snapshot():
    # flagger_slow_then_stop: the snapshot says SLOW(1..6) but the live
    # flagger shows STOP(6..16) — the assist must describe the live phase.
    class _LiveRef:
        class _Actor:
            is_alive = True

        def get_actor(self):
            return self._Actor()

        def get_metadata(self):
            return {"gesture_id": "STOP", "onset_time": 6.0, "duration": 10.0}

    c = _controller("temporal")
    c._gt = {**_GT, "G_gesture": "SLOW",
             "G_gesture_onset_sec": 1.0, "G_gesture_duration_sec": 5.0}
    c._officer_ref = _LiveRef()
    text = c._ablation_assist(8.0)
    assert "making the STOP hand signal" in text
    assert "is ACTIVE right now" in text
    assert "SLOW" not in text.split("Semantics")[0]  # perception shows live phase


def test_other_lane_relation_reads_naturally():
    c = _controller("semantics")
    c._gt = {**_GT, "T_target_relation": "other_lane"}
    text = c._ablation_assist(5.0)
    assert "directed at vehicles in the other lane, NOT at your" in text
    assert "other_lane" not in text.replace("vehicles in the other lane", "")


def test_distance_is_not_claimed_as_ahead():
    text = _controller("perception")._ablation_assist(5.0)
    assert "from your spawn position" in text
    assert "ahead" not in text


def test_malformed_gt_fails_loudly():
    c = _controller("perception")
    c._gt = {**_GT, "A_authority": {"type": "police", "valid": None}}
    with pytest.raises(ValueError, match="real bool"):
        c._validate_ablation_gt()

    c = _controller("action")
    c._gt = {**_GT, "Y_expected_action": "TELEPORT"}
    with pytest.raises(ValueError, match="no honest description"):
        c._validate_ablation_gt()

    c = _controller("perception")
    c._gt = {k: v for k, v in _GT.items() if k != "L_light_state"}
    with pytest.raises(ValueError, match="L_light_state"):
        c._validate_ablation_gt()


def test_placeholder_director_reads_as_no_director():
    # No-director scenes use a placeholder actor whose metadata says
    # authority_type="none" (a truthy STRING); the assist must not render
    # "a none is standing ...".
    c = _controller("temporal")
    c._gt = {**_GT,
             "A_authority": {"type": "none", "valid": False},
             "G_gesture": "IDLE", "officer_transform": None}
    text = c._ablation_assist(5.0)
    assert "no human director is present" in text
    assert "a none" not in text
    c._validate_ablation_gt()  # placeholder must not trip the bool check


def test_privileged_optin_only_when_ablated():
    assert _controller("none").requests_privileged_gt is False
    assert _controller("perception").requests_privileged_gt is True
