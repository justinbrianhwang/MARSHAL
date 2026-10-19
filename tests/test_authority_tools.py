from pathlib import Path
import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts._capture_authority_actors import _look_at_pitch_yaw
from scripts._make_episode_videos import (
    align_frames_to_ticks,
    format_overlay,
    frame_ids_for_ticks,
)


@pytest.mark.parametrize(
    ("camera", "target", "expected_pitch", "expected_yaw"),
    [
        ((0.0, 0.0, 2.5), (6.0, 0.0, 1.0), -14.036243, 0.0),
        ((1.0, 1.0, 3.0), (-1.0, 3.0, 1.0), -35.264390, 135.0),
    ],
)
def test_external_camera_look_at_math(
    camera, target, expected_pitch, expected_yaw
):
    camera_location = SimpleNamespace(x=camera[0], y=camera[1], z=camera[2])
    target_location = SimpleNamespace(x=target[0], y=target[1], z=target[2])

    pitch, yaw = _look_at_pitch_yaw(camera_location, target_location)

    assert math.isclose(pitch, expected_pitch, abs_tol=1e-6)
    assert math.isclose(yaw, expected_yaw, abs_tol=1e-6)


def test_overlay_formats_signed_front_bumper_distance_and_action():
    lines = format_overlay(
        "qwen|civilian_stop|7",
        {
            "t": "1.5",
            "speed_kmh": "12.345",
            "stopline_forward_m": "1.3959",
            "officer_active": "True",
            "officer_gesture_id": "STOP",
            "officer_duration_sec": "12.0",
        },
        "STOP",
    )

    assert lines[0] == "model=qwen  condition=civilian_stop  seed=7"
    assert lines[1] == (
        "t=1.50s  speed=12.35 km/h  front_bumper_to_stopline=-1.000 m"
        "  officer=STOP active=true  VLM=STOP"
    )


def test_overlay_shows_no_officer_for_null_officer_placeholder():
    # MARSHAL_OFFICER_CONDITION=absent logs officer_active=True, gesture IDLE, no duration
    lines = format_overlay(
        "tcp|no_police|1",
        {"t": "2.0", "speed_kmh": "5", "stopline_forward_m": "5", "officer_active": "True",
         "officer_gesture_id": "IDLE", "officer_duration_sec": ""},
    )
    assert "officer=none" in lines[1] and "officer_active" not in lines[1]


def test_alignment_uses_frame_ids_ignores_preroll_and_handles_gap_and_ego_lag():
    external = [
        Path("frames/00000008.png"),
        Path("frames/00000010.png"),
        Path("frames/00000011.png"),
        Path("frames/00000013.png"),
    ]
    ego = [
        Path("frames_ego/00000009.png"),
        Path("frames_ego/00000010.png"),
        Path("frames_ego/00000011.png"),
        Path("frames_ego/00000012.png"),
    ]
    ticks = [{"t": 0.05}, {"t": 0.10}, {"t": 0.15}, {"t": 0.20}]

    aligned, n_fallback_ego, n_skipped = align_frames_to_ticks(
        external, ego, ticks, [10, 11, 12, 13]
    )

    assert aligned == [
        (external[1], ego[1], ticks[0]),
        (external[2], ego[2], ticks[1]),
        (external[3], ego[3], ticks[3]),
    ]
    assert n_fallback_ego == 1
    assert n_skipped == 1


def test_tick_frame_mapping_prefers_trace_and_validates_anchor():
    episode = Path("episode")
    trace = episode / "tcp_debug" / "tcp_trace.csv"
    external = [Path(f"frames/{frame:08d}.png") for frame in (98, 99, 101, 102)]
    ticks = [{"t": "0.05000000000001"}, {"t": "0.1"}, {"t": "0.15"}]

    with patch.object(Path, "glob", return_value=[trace]), patch(
        "scripts._make_episode_videos._trace_frame_ids", return_value=[100, 101, 102]
    ):
        frame_ids, rule, disagreement = frame_ids_for_ticks(episode, external, ticks)

    assert frame_ids == [100, 101, 102]
    assert rule == "controller_trace"
    assert disagreement == 0


def test_tick_frame_mapping_anchors_last_tick_to_max_external():
    external = [Path(f"frames/{frame:08d}.png") for frame in (8, 10, 11, 13)]
    ticks = [{"t": 0.05}, {"t": 0.1}, {"t": 0.15}, {"t": 0.2}]

    frame_ids, rule, disagreement = frame_ids_for_ticks(Path("missing"), external, ticks)

    assert frame_ids == [10, 11, 12, 13]
    assert rule == "anchor_last_tick_to_max_external"
    assert disagreement is None
