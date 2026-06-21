"""Episode logging helpers for MARSHAL benchmark runs.

Implements the persistence layout required by Prompt.txt Step 12.E::

    outputs/marshal_runs/<episode_id>/
        metadata.json   # single JSON object
        events.json     # list of {t, name, payload} records
        metrics.csv     # header: t,key,value
        ...other artefacts written by callers via EpisodeLogger.path(...)

Also exposes:
  * ``setup_root_logger`` — one-shot stdlib logging configuration.
  * ``JSONLEventLogger`` — streaming JSONL writer for long-running episodes
    that cannot afford to buffer events in memory.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("marshal_bench.utils.logging_utils")


# ---------------------------------------------------------------------------
# Stdlib logging convenience
# ---------------------------------------------------------------------------
def setup_root_logger(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    fmt: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
) -> None:
    """Configure the root logger with a sensible default format.

    Safe to call multiple times — existing handlers are replaced so the latest
    call always wins, which is useful for swapping log files between episodes.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(fmt)

    # On Windows the console stream often defaults to a legacy codec (e.g.
    # cp949), which raises UnicodeEncodeError on non-ASCII log text (em dashes,
    # Korean, …) and drops the line. Force UTF-8 with lossy fallback so logging
    # never crashes regardless of the active code page.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)) or ".", exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except Exception as e:
            root.warning("Could not attach file handler at %s: %s", log_file, e)


# ---------------------------------------------------------------------------
# EpisodeLogger
# ---------------------------------------------------------------------------
class EpisodeLogger:
    """Collects metadata, events, and scalar metrics for one benchmark episode.

    Events and metrics are buffered in memory and persisted on ``flush()`` /
    ``close()``. ``save_metadata`` writes immediately so the metadata file
    always reflects the latest snapshot even if the process crashes.
    """

    def __init__(self, episode_id: str, output_root: str = "outputs/marshal_runs") -> None:
        if not episode_id:
            raise ValueError("episode_id must be a non-empty string")
        self.episode_id = episode_id
        self.output_root = os.path.abspath(output_root)
        self.episode_dir = os.path.join(self.output_root, episode_id)
        os.makedirs(self.episode_dir, exist_ok=True)

        self._events: List[Dict[str, Any]] = []
        self._metrics: List[Dict[str, Any]] = []
        self._closed = False
        self._t0 = time.time()

    # --- recording -------------------------------------------------------
    def log_event(
        self,
        name: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        t: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        """Append an event record.

        Accepts both ``log_event(name, payload_dict)`` and
        ``log_event(name, key=value, ...)`` styles. When both are supplied,
        ``payload`` keys win on conflict.
        """
        if self._closed:
            log.warning("log_event on closed EpisodeLogger ignored (event=%s)", name)
            return
        merged: Dict[str, Any]
        if payload is None and not kwargs:
            merged = {}
        elif payload is None:
            merged = dict(kwargs)
        elif not kwargs:
            merged = dict(payload)
        else:
            merged = {**kwargs, **payload}
        self._events.append(
            {
                "t": float(t) if t is not None else time.time(),
                "name": str(name),
                "payload": merged,
            }
        )

    def log_metric(self, key: str, value: Any, t: Optional[float] = None) -> None:
        """Append a (t, key, value) scalar-metric row."""
        if self._closed:
            log.warning("log_metric on closed EpisodeLogger ignored (key=%s)", key)
            return
        self._metrics.append(
            {
                "t": float(t) if t is not None else time.time(),
                "key": str(key),
                "value": value,
            }
        )

    def log_metric_row(self, t: Optional[float] = None, **kwargs: Any) -> None:
        """Convenience: log many metrics at one timestamp.

        Each kwarg becomes its own ``(t, key, value)`` row, preserving the
        long-format ``metrics.csv`` schema. Useful for per-tick scenario
        telemetry like ``log_metric_row(t=sim_time, speed_kmh=..., in_junction=...)``.
        """
        if self._closed:
            log.warning("log_metric_row on closed EpisodeLogger ignored")
            return
        ts = float(t) if t is not None else time.time()
        for key, value in kwargs.items():
            self._metrics.append({"t": ts, "key": str(key), "value": value})

    # --- persistence -----------------------------------------------------
    def save_metadata(self, metadata: Dict[str, Any], name: str = "metadata.json") -> None:
        """Write ``<episode_dir>/<name>`` immediately (overwriting any previous version).

        ``name`` defaults to ``metadata.json`` per Prompt.txt Step 12.E but
        callers may pass e.g. ``result.json`` to drop additional snapshots
        alongside the canonical metadata file.
        """
        path = os.path.join(self.episode_dir, name)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, default=_json_default, sort_keys=True)
        except Exception as e:
            log.error("Failed to write %s: %s", path, e)

    def flush(self) -> None:
        """Persist buffered events and metrics to disk."""
        events_path = os.path.join(self.episode_dir, "events.json")
        try:
            with open(events_path, "w", encoding="utf-8") as f:
                json.dump(self._events, f, indent=2, default=_json_default)
        except Exception as e:
            log.error("Failed to write %s: %s", events_path, e)

        metrics_path = os.path.join(self.episode_dir, "metrics.csv")
        try:
            with open(metrics_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["t", "key", "value"])
                for row in self._metrics:
                    writer.writerow([row["t"], row["key"], _csv_value(row["value"])])
        except Exception as e:
            log.error("Failed to write %s: %s", metrics_path, e)

    def close(self) -> None:
        """Flush and mark the logger closed. Idempotent."""
        if self._closed:
            return
        self.flush()
        self._closed = True

    # --- helpers ---------------------------------------------------------
    def path(self, name: str) -> str:
        """Return an absolute path inside the episode directory for arbitrary artefacts."""
        return os.path.join(self.episode_dir, name)

    @property
    def t0(self) -> float:
        """Wall-clock timestamp captured at logger construction."""
        return self._t0

    # context-manager sugar (handy in scripts)
    def __enter__(self) -> "EpisodeLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Streaming JSONL writer for long-running runs
# ---------------------------------------------------------------------------
class JSONLEventLogger:
    """Append-only JSON-lines writer.

    One record per ``write`` call, flushed immediately so that ``tail -f`` and
    crash-survivors observe events as they happen. Intended for long-running
    scenarios where buffering everything in ``EpisodeLogger`` would be costly.
    """

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._fp: Optional[io.TextIOBase] = open(self.path, "a", encoding="utf-8")

    def write(self, event_dict: Dict[str, Any]) -> None:
        """Append one record. Silently drops if the writer has been closed."""
        if self._fp is None:
            return
        try:
            self._fp.write(json.dumps(event_dict, default=_json_default))
            self._fp.write("\n")
            self._fp.flush()
        except Exception as e:
            log.error("JSONLEventLogger.write failed (%s): %s", self.path, e)

    def close(self) -> None:
        """Close the underlying file handle. Idempotent."""
        if self._fp is None:
            return
        try:
            self._fp.close()
        except Exception:
            pass
        self._fp = None

    def __enter__(self) -> "JSONLEventLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def _json_default(obj: Any) -> Any:
    """Fallback serialiser for non-JSON-native objects (carla types, enums, ...)."""
    for attr in ("as_dict", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    name = getattr(obj, "name", None)
    if name and not callable(name):
        return str(name)
    if all(hasattr(obj, c) for c in ("x", "y", "z")):
        return {"x": float(obj.x), "y": float(obj.y), "z": float(obj.z)}
    if all(hasattr(obj, c) for c in ("x", "y")):
        return {"x": float(obj.x), "y": float(obj.y)}
    return repr(obj)


def _csv_value(v: Any) -> Any:
    """Stringify non-scalar metric values so they survive CSV serialisation."""
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    try:
        return json.dumps(v, default=_json_default)
    except Exception:
        return repr(v)


__all__ = [
    "EpisodeLogger",
    "JSONLEventLogger",
    "setup_root_logger",
]
