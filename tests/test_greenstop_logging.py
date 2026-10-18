from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image

from marshal_bench.controllers.interfuser_model import InterFuserController
from marshal_bench.controllers.tcp_model import TCPController
from marshal_bench.controllers.vlm_model import VLMController, _frame_to_data_url
from scripts import _run_more_e2e_sweep as e2e_sweep
from scripts import _run_vlm_test as vlm_runner
from scripts import _shared_staging as staging


ROOT = Path(__file__).resolve().parents[1]


def test_tcp_postprocess_env_only_disables_on_zero(monkeypatch):
    monkeypatch.delenv("MARSHAL_TCP_POSTPROCESS", raising=False)
    assert TCPController({}).postprocess_control is True
    monkeypatch.setenv("MARSHAL_TCP_POSTPROCESS", "0")
    assert TCPController({}).postprocess_control is False


def test_interfuser_native_safety_metadata_is_extracted():
    meta = (
        "speed: 4.00, target_speed: 2.50",
        "on_road_prob: 0.99",
        "stop_steps:0",
        np.float32(7.25),
    )
    assert InterFuserController._safety_trace_meta(meta) == {
        "desired_speed": 2.5,
        "brake_reason": "",
        "safe_distance": 7.25,
    }


def test_timeout_env_wins_after_spawn_back(monkeypatch):
    monkeypatch.setenv("MARSHAL_SPAWN_BACK_M", "20")
    monkeypatch.setenv("MARSHAL_TIMEOUT_S", "31.5")
    spec = vlm_runner.SCENARIOS["green_stop"]
    cfg = staging.load_staged_config(
        str(ROOT), "green_stop", spec, "vlm"
    )
    assert cfg["timeout_sec"] == 31.5


def test_tcp_no_postprocess_flag_parses():
    assert e2e_sweep._parse_args(["--tcp-no-postprocess"]).tcp_no_postprocess is True


def test_saved_vlm_png_matches_exact_transmitted_jpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("MARSHAL_VLM_SAVE_INPUTS", "1")
    controller = VLMController({"vlm": {"backend": "mock"}})
    controller.set_episode_dir(str(tmp_path))
    frame = np.arange(24 * 32 * 3, dtype=np.uint8).reshape(24, 32, 3)
    data_url = _frame_to_data_url(frame)

    filename = controller._save_query_input(data_url, 2, 1.25)

    assert filename == "vlm_inputs/q2_t1.250.png"
    transmitted = Image.open(
        io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))
    ).convert("RGB")
    saved = Image.open(tmp_path / filename).convert("RGB")
    assert np.array_equal(np.asarray(saved), np.asarray(transmitted))
    assert _frame_to_data_url(data_url) == data_url
