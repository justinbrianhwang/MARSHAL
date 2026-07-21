"""Light-only baseline controller (B0): obeys the signal, blind to humans.

The original B0 was CARLA's TrafficManager autopilot. TM's red-light
compliance depends on the map's traffic-light TRIGGER VOLUMES, and at several
curated stations no volume covers the ego's approach lane — so the
"light-following" baseline silently ignored the pinned signal and sailed
through reds (found as a live stimulus-integrity bug: ego.get_traffic_light()
stayed None across the whole approach). This controller implements the
baseline CONTRACT directly from the per-tick observation instead:

* obey the scenario's pinned signal (``tl_state`` + ``distance_to_stopline_m``
  from the observation — the same light the dashcam shows);
* stay completely blind to every human directive, gesture, or hazard.

It drives the straight station approach at ~25 km/h, brakes to a hold before
the stop line while the light reads Red/Yellow, and drives on Green/Off. No
ground-truth field is read.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from marshal_bench.controllers.base import EpisodeController

log = logging.getLogger(__name__)

_CRUISE_MPS = 25.0 / 3.6
_STOP_ATTENTION_M = 30.0   # start caring about a red this far from the line
_BRAKE_DECEL_MPS2 = 2.5
_HOLD_MARGIN_M = 4.0


class LightOnlyBaselineController(EpisodeController):
    name = "baseline"
    track = "A"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.carla: Any = None
        self.ego: Any = None

    def setup(self, world: Any, ego: Any, ground_truth: Dict[str, Any],
              carla: Any) -> None:
        self.carla = carla
        self.ego = ego

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        carla = self.carla
        if carla is None:
            return None
        obs = observation or {}
        speed = float(obs.get("ego_speed") or 0.0)  # m/s
        tl_state = str(obs.get("tl_state") or "").strip().capitalize()
        dstop = obs.get("distance_to_stopline_m")
        dstop = float(dstop) if isinstance(dstop, (int, float)) else None

        ctrl = carla.VehicleControl()
        ctrl.steer = 0.0

        red_ahead = (
            tl_state in ("Red", "Yellow")
            and dstop is not None
            and 0.0 < dstop <= _STOP_ATTENTION_M
        )
        if red_ahead:
            brake_envelope = max(
                _HOLD_MARGIN_M,
                (speed * speed) / (2.0 * _BRAKE_DECEL_MPS2) + 2.0,
            )
            if dstop <= brake_envelope:
                ctrl.throttle = 0.0
                ctrl.brake = 1.0 if speed > 0.25 else 0.7
                return ctrl

        if speed > _CRUISE_MPS:
            ctrl.throttle = 0.0
        elif speed > _CRUISE_MPS - 1.0:
            ctrl.throttle = 0.35
        else:
            ctrl.throttle = 0.65
        ctrl.brake = 0.0
        return ctrl

    def teardown(self) -> None:
        pass


__all__ = ["LightOnlyBaselineController"]
