"""Copy-paste template: plug YOUR model into the MARSHAL benchmark.

A MARSHAL controller is the *agent under test*. The benchmark drives it
identically every episode::

    setup(world, ego, ground_truth, carla)   # once, before the loop
    step(observation, dt) -> carla.VehicleControl   # every sim tick (~20 Hz)
    teardown()                                # once, after the loop
    report_target() -> Optional[str]          # optional, for the TAA metric

To benchmark your model:

1. Copy this file (or subclass :class:`EpisodeController` anywhere importable).
2. Fill in ``setup`` (load your weights once) and ``step`` (inference -> control).
3. Run::

       python start.py --controller marshal_bench.controllers.example_model:ExampleController --tag example

   or point at your own module::

       python start.py --controller my_pkg.my_agent:MyController --tag my_model

The ``observation`` dict you receive each tick
-----------------------------------------------
============== =========================================================
key            meaning
============== =========================================================
sim_time       seconds since episode start
ego_x/y/z      ego world location (m)
ego_yaw        ego heading (deg)
ego_speed      ego speed (m/s);  ego_speed_kmh in km/h
tl_state       nearest traffic-light state: "Red"/"Green"/"Yellow"/...
in_junction    bool — is the ego inside the intersection box
image          latest ego front-camera RGB frame (H,W,3 uint8), or None
image_hwc      image shape tuple (H, W, 3), or None before first frame
frames_ego_dir absolute path to the recorded ego camera PNG frames
ground_truth   the privileged episode E-tuple (see note below)
============== =========================================================

Camera frames: ``observation["image"]`` is the latest ego dashcam RGB frame
as a NumPy array, and can be ``None`` during the first ticks before CARLA has
delivered a camera sample. The same stream is also recorded under
``observation["frames_ego_dir"]`` for models that prefer reading PNG files.

IMPORTANT — fair evaluation
---------------------------
``observation["ground_truth"]`` contains the *answer* (the officer's true
gesture, authority validity, expected action). Only the **oracle** (Track A,
the upper-bound reference) is allowed to read it. A real model under test
(Track B sensor-E2E, Track C VLM) must derive its decision from ``ego_*`` state
+ ``tl_state`` + ``observation["image"]`` — NOT from ``ground_truth``. The template
below ignores ``ground_truth`` on purpose.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from marshal_bench.controllers.base import EpisodeController


class ExampleController(EpisodeController):
    """A trivial reference agent: obey the traffic light, ignore everything else.

    This is deliberately *not* authority-aware — it is the kind of light-only /
    perception-only policy that fails the high-tier MARSHAL scenarios. Replace
    the body of :meth:`step` with your model's inference to benchmark it.
    """

    name = "example"
    track = "B"  # "A" oracle (privileged) | "B" sensor/E2E | "C" VLM

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._carla = None
        self._ego = None
        self._target_kmh = 25.0
        self._predicted_target: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------
    def setup(self, world: Any, ego: Any, ground_truth: Dict[str, Any],
              carla: Any) -> None:
        """Called once. Cache handles and load your model weights here."""
        self._carla = carla
        self._ego = ego
        self._target_kmh = float(ground_truth.get("target_speed_kmh", 25.0))
        # e.g. self.model = torch.load(...); self.model.eval()

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        """Called every tick. Return a carla.VehicleControl.

        Replace this with: read camera frame(s) -> your model -> control.
        """
        carla = self._carla
        light = str(observation.get("tl_state", "Unknown")).lower()
        speed_kmh = float(observation.get("ego_speed_kmh", 0.0))
        frame = observation.get("image")
        # if frame is not None:
        #     pred = self.model(frame)

        # --- trivial light-only policy (REPLACE ME) ------------------------
        stop = light.startswith("red") or light.startswith("yellow")
        if stop:
            return carla.VehicleControl(throttle=0.0, brake=1.0, steer=0.0)
        # simple speed keeper
        throttle = 0.5 if speed_kmh < self._target_kmh else 0.0
        return carla.VehicleControl(throttle=throttle, brake=0.0, steer=0.0)

    def teardown(self) -> None:
        """Optional cleanup (free GPU memory, close files, ...)."""

    def report_target(self) -> Optional[str]:
        """Optional: your model's predicted gesture target ("ego" /
        "adjacent_lane" / ...) for the Target-Attribution-Accuracy metric.
        Return None to abstain (TAA falls back to a behavioural proxy)."""
        return self._predicted_target
