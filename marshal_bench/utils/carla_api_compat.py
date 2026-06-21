"""CARLA API compatibility / capability detection layer for MARSHAL.

All other MARSHAL modules should import CARLA through this module. This isolates
version-dependent API surface in one place and provides graceful fallbacks when
features are unavailable.

Target: CARLA 0.9.16 (with Python 3.12 wheel under PythonAPI/carla/dist/).
Older or newer CARLA versions may still work via the capability flags below.
"""

from __future__ import annotations

import dataclasses
import importlib
import logging
import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator, Optional

log = logging.getLogger("marshal_bench.compat")

_CARLA_MODULE: Optional[Any] = None


# ---------------------------------------------------------------------------
# Import carla, with fallback to the wheel shipped in PythonAPI/carla/dist/
# ---------------------------------------------------------------------------
def import_carla() -> Any:
    """Return the imported `carla` module, attempting bundled-wheel install if needed.

    Resolution order:
      1. Already imported `carla` module.
      2. `import carla` (system / venv install).
      3. Look for `CARLA_ROOT/PythonAPI/carla/dist/carla-*.egg` or `.whl` and add to sys.path.
    """
    global _CARLA_MODULE
    if _CARLA_MODULE is not None:
        return _CARLA_MODULE

    try:
        _CARLA_MODULE = importlib.import_module("carla")
        return _CARLA_MODULE
    except ImportError:
        pass

    carla_root = os.environ.get("CARLA_ROOT")
    candidate_roots = []
    if carla_root:
        candidate_roots.append(carla_root)
    # repo-relative guess (this file is at <repo>/marshal_bench/utils/...)
    here = os.path.dirname(os.path.abspath(__file__))
    candidate_roots.append(os.path.abspath(os.path.join(here, "..", "..", "CARLA_0.9.16")))

    for root in candidate_roots:
        dist_dir = os.path.join(root, "PythonAPI", "carla", "dist")
        if not os.path.isdir(dist_dir):
            continue
        for fn in sorted(os.listdir(dist_dir)):
            if fn.startswith("carla-") and (fn.endswith(".egg") or fn.endswith(".whl")):
                full = os.path.join(dist_dir, fn)
                if full not in sys.path:
                    sys.path.insert(0, full)
                try:
                    _CARLA_MODULE = importlib.import_module("carla")
                    log.info("Loaded carla module from %s", full)
                    return _CARLA_MODULE
                except ImportError:
                    continue

    raise ImportError(
        "Could not import 'carla'. Install the wheel under "
        "CARLA_0.9.16/PythonAPI/carla/dist/carla-0.9.16-cp312-cp312-win_amd64.whl "
        "(matching your Python version) or set CARLA_ROOT."
    )


# ---------------------------------------------------------------------------
# CARLA navigation `agents` package (GlobalRoutePlanner, etc.)
# ---------------------------------------------------------------------------
def ensure_agents_on_path() -> str:
    """Put CARLA's PythonAPI ``agents`` package on ``sys.path`` and return its
    parent dir.

    The pip/whl-installed ``carla`` module does NOT bundle the ``agents``
    navigation helpers (GlobalRoutePlanner, BasicAgent, …) — those live under
    ``<CARLA_ROOT>/PythonAPI/carla/agents``. This locates that directory and
    inserts ``<...>/PythonAPI/carla`` on ``sys.path`` so ``import
    agents.navigation.global_route_planner`` works.
    """
    # Already importable?
    try:
        importlib.import_module("agents.navigation.global_route_planner")
        return ""
    except ImportError:
        pass

    candidate_roots = []
    carla_root = os.environ.get("CARLA_ROOT")
    if carla_root:
        candidate_roots.append(carla_root)
    here = os.path.dirname(os.path.abspath(__file__))
    candidate_roots.append(
        os.path.abspath(os.path.join(here, "..", "..", "CARLA_0.9.16")))
    candidate_roots.append(r"F:\carla")  # source build

    for root in candidate_roots:
        pyapi = os.path.join(root, "PythonAPI", "carla")
        if os.path.isdir(os.path.join(pyapi, "agents")):
            if pyapi not in sys.path:
                sys.path.insert(0, pyapi)
            try:
                importlib.import_module(
                    "agents.navigation.global_route_planner")
                log.info("Loaded CARLA agents package from %s", pyapi)
                return pyapi
            except ImportError:
                continue

    raise ImportError(
        "Could not locate CARLA's PythonAPI 'agents' package. Set CARLA_ROOT "
        "to a CARLA install that contains PythonAPI/carla/agents/."
    )


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class Capabilities:
    """Feature flags reported once per-process after introspecting `carla`."""

    carla_version: str = "unknown"
    has_walker_set_bones: bool = False
    has_walker_blend_pose: bool = False
    has_walker_show_pose: bool = False
    has_walker_get_bones: bool = False
    has_traffic_light_freeze: bool = False
    has_traffic_light_set_state: bool = False
    has_scenario_runner: bool = False
    has_walker_ai_controller: bool = False
    custom_asset_walker: bool = False  # set externally if custom UE asset registered

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


_CAPS: Optional[Capabilities] = None


def detect_capabilities(world: Optional[Any] = None) -> Capabilities:
    """Inspect the loaded `carla` module (and optionally a `world`) to detect features.

    `world` is accepted for future capability checks that need a live connection,
    but currently this works purely off the imported module.
    """
    global _CAPS
    if _CAPS is not None:
        return _CAPS

    carla = import_carla()
    caps = Capabilities()

    # version
    caps.carla_version = getattr(carla, "__version__", None) or _infer_version_from_path(carla)

    # walker skeleton APIs
    Walker = getattr(carla, "Walker", None)
    if Walker is not None:
        caps.has_walker_set_bones = callable(getattr(Walker, "set_bones", None))
        caps.has_walker_blend_pose = callable(getattr(Walker, "blend_pose", None))
        caps.has_walker_show_pose = callable(getattr(Walker, "show_pose", None))
        caps.has_walker_get_bones = callable(getattr(Walker, "get_bones", None))
    caps.has_walker_ai_controller = getattr(carla, "WalkerAIController", None) is not None

    # traffic light freeze / set_state
    TL = getattr(carla, "TrafficLight", None)
    if TL is not None:
        caps.has_traffic_light_freeze = callable(getattr(TL, "freeze", None))
        caps.has_traffic_light_set_state = callable(getattr(TL, "set_state", None))

    # scenario runner
    try:
        importlib.import_module("srunner.scenariomanager.scenarioatomics.atomic_behaviors")
        caps.has_scenario_runner = True
    except Exception:
        caps.has_scenario_runner = False

    _CAPS = caps
    log.info("Detected CARLA capabilities: %s", caps.as_dict())
    return caps


def _infer_version_from_path(carla_module: Any) -> str:
    p = getattr(carla_module, "__file__", "") or ""
    for token in p.replace("\\", "/").split("/"):
        if token.startswith("carla-") and ".dist" in token or token.startswith("carla-0."):
            return token.split("-")[1] if "-" in token else token
    if "CARLA_0.9.16" in p:
        return "0.9.16"
    return "unknown"


# ---------------------------------------------------------------------------
# Synchronous mode context manager (mirrors examples/synchronous_mode.py style)
# ---------------------------------------------------------------------------
class SyncModeContext:
    """Minimal synchronous-mode context for deterministic ticking.

    Usage:
        with SyncModeContext(world, fps=20) as sync:
            sync.tick(timeout=2.0)
    """

    def __init__(self, world: Any, fps: float = 20.0, no_rendering_mode: bool = False):
        self.world = world
        self.fps = fps
        self.delta = 1.0 / fps
        self._original_settings = None
        self.no_rendering_mode = no_rendering_mode

    def __enter__(self) -> "SyncModeContext":
        carla = import_carla()
        self._original_settings = self.world.get_settings()
        self.world.apply_settings(
            carla.WorldSettings(
                synchronous_mode=True,
                fixed_delta_seconds=self.delta,
                no_rendering_mode=self.no_rendering_mode,
            )
        )
        return self

    def tick(self, timeout: float = 2.0) -> int:
        return self.world.tick()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._original_settings is not None:
            try:
                self.world.apply_settings(self._original_settings)
            except Exception as e:
                log.warning("Failed to restore world settings on exit: %s", e)


# ---------------------------------------------------------------------------
# Blueprint helpers
# ---------------------------------------------------------------------------
def safe_get_blueprint_library(world: Any) -> Any:
    """`world.get_blueprint_library()` with a clearer error on failure."""
    try:
        return world.get_blueprint_library()
    except Exception as e:
        raise RuntimeError(
            "Failed to fetch blueprint library — is the CARLA server running?"
        ) from e


def filter_blueprints(world: Any, pattern: str) -> list:
    """Return blueprint actors matching a wildcard pattern, or []."""
    try:
        return list(safe_get_blueprint_library(world).filter(pattern))
    except Exception:
        return []
