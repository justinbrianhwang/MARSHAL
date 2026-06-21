"""Episode-level controller interface for the MARSHAL benchmark.

A *controller* is the agent under test. Every controller is driven identically
by :func:`marshal_bench.scenarios._common.run_scenario`:

    setup(world, ego, ground_truth, carla)   # once, before the loop
    step(observation, dt) -> carla.VehicleControl   # every sim tick
    teardown()                               # once, after the loop
    report_target() -> Optional[str]         # optional, for the TAA metric

``ground_truth`` is the privileged episode E-tuple ⟨M,J,L,A,G,T,S,V,W,Y⟩
(see ``_build_ground_truth``). The PPTX track design maps onto this interface:

* **Track A — Oracle**: reads ``ground_truth`` directly and executes the
  correct authority-aware behaviour. Upper-bound reference.
* **Track B — Sensor-only (E2E)**: ignores ``ground_truth``; consumes RGB/state
  from ``observation`` only. (TransFuser / InterFuser / TCP wrappers.)
* **Track C — VLM**: turns ``observation`` images+state into a prompt and maps
  the answer to a control. (Optional.)

The TrafficManager autopilot baseline (B0, traffic-light-only) is represented
by passing ``controller=None`` to ``run_scenario`` — it is NOT a controller
object, since the TM drives the vehicle itself.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class EpisodeController:
    name: str = "base"
    track: str = "?"  # "A" | "B" | "C"

    def setup(self, world: Any, ego: Any, ground_truth: Dict[str, Any],
              carla: Any) -> None:
        """Called once before the sim loop. Cache handles, load weights, etc."""
        raise NotImplementedError

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        """Return a ``carla.VehicleControl`` for this tick (or ``None`` to coast)."""
        raise NotImplementedError

    def teardown(self) -> None:
        """Optional cleanup."""

    def report_target(self) -> Optional[str]:
        """Optional: the controller's predicted gesture target relation
        ("ego" / "adjacent_lane" / ...) for the TAA metric. ``None`` = abstain."""
        return None
