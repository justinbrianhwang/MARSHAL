"""MARSHAL benchmark controllers (the agents under test).

``make_controller(name, ...)`` resolves a controller by name:

* ``baseline`` / ``tm`` / ``autopilot`` / ``none`` -> ``None`` — ``run_scenario``
  drives with the TrafficManager autopilot (officer-blind, light-only baseline B0).
* ``oracle`` -> the privileged Track-A reference controller.
* **Any dotted/colon path** ``your_pkg.your_module:YourController`` (or
  ``your_pkg.your_module.YourController``) -> imported and instantiated. This is
  how a **third party plugs in their own model**: write a subclass of
  :class:`marshal_bench.controllers.base.EpisodeController` and point
  ``--controller`` at it. The class is constructed as ``Cls(config=config)`` if
  it accepts a ``config`` kwarg, else ``Cls()``.

See ``marshal_bench/controllers/example_model.py`` for a copy-paste template and
``docs/benchmarking_your_model.md`` for the full third-party guide.
"""
from __future__ import annotations

import importlib
import inspect
from typing import Any, Optional

_TM_ALIASES = {"baseline", "tm", "autopilot", "none", None}


def _instantiate(cls: Any, config: Optional[dict]) -> Any:
    """Construct a controller, passing ``config=`` only if the ctor accepts it."""
    try:
        sig = inspect.signature(cls)
        if "config" in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            return cls(config=config)
    except (TypeError, ValueError):
        pass
    return cls()


def _resolve_dotted(path: str, config: Optional[dict]) -> Any:
    """Import ``module:ClassName`` or ``module.ClassName`` and instantiate it."""
    if ":" in path:
        mod_name, cls_name = path.split(":", 1)
    else:
        mod_name, _, cls_name = path.rpartition(".")
    if not mod_name or not cls_name:
        raise ValueError(
            f"Controller path {path!r} must be 'module:ClassName' or "
            "'module.ClassName'."
        )
    try:
        module = importlib.import_module(mod_name)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Could not import controller module {mod_name!r}: {e}") from e
    try:
        cls = getattr(module, cls_name)
    except AttributeError as e:
        raise ValueError(
            f"Module {mod_name!r} has no class {cls_name!r}."
        ) from e
    return _instantiate(cls, config)


def make_controller(name: Optional[str], config: Optional[dict] = None) -> Any:
    if name in _TM_ALIASES:
        return None
    key = str(name).lower()
    if key in _TM_ALIASES:
        return None
    if key == "oracle":
        from marshal_bench.controllers.oracle import OracleController
        return OracleController(config=config)
    # A dotted/colon path -> a third-party (or built-in) controller class.
    if ":" in name or "." in name:
        return _resolve_dotted(name, config)
    raise ValueError(
        f"Unknown controller '{name}'. Use 'baseline', 'oracle', or a "
        "'module:ClassName' path to your own EpisodeController subclass."
    )


__all__ = ["make_controller"]
