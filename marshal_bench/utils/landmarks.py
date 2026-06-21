"""Persistent benchmark landmarks for the MARSHAL Town03 world.

The three lab-logo signposts (SJB / RAISE / MPS-LAB) on the Town03 fountain
inner green ring are a *custom prop* (``static.prop.town03landmarks``), not a
baked part of the stock map — so every fresh ``load_world("Town03")`` drops
them. The benchmark wants them present in every episode (they are part of the
scene the agent sees), so :func:`ensure_town03_landmarks` is called once during
world setup and is idempotent.

Geometry rationale lives in ``scripts/_make_town03_landmarks.py``: the mesh is
baked at LOCAL origin, so the spawn transform alone places it at the fountain
centre ``(0, 4)``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("marshal_bench.utils.landmarks")

LANDMARKS_BP_ID = "static.prop.town03landmarks"
FOUNTAIN_X, FOUNTAIN_Y, FOUNTAIN_Z = 0.0, 4.0, 0.0


def ensure_town03_landmarks(world: Any, carla: Optional[Any] = None) -> Any:
    """Spawn the fountain lab-logo signposts if not already present.

    Idempotent: if an instance already exists in the world it is returned
    unchanged. Only acts on Town03 (the prop is calibrated to that fountain);
    on other maps it is a no-op and returns ``None``.
    """
    if carla is None:
        from marshal_bench.utils.carla_api_compat import import_carla
        carla = import_carla()

    try:
        map_name = world.get_map().name
    except Exception as e:
        log.debug("ensure_town03_landmarks: get_map failed: %s", e)
        return None
    if "Town03" not in map_name:
        log.debug("Landmarks are Town03-only; current map is %s — skipping.",
                  map_name)
        return None

    # Already spawned?
    existing = world.get_actors().filter(LANDMARKS_BP_ID)
    if len(existing):
        return existing[0]

    bp = world.get_blueprint_library().find(LANDMARKS_BP_ID)
    if bp is None:
        log.warning("Blueprint %s not registered in this CARLA build — "
                    "fountain landmarks will be absent.", LANDMARKS_BP_ID)
        return None

    tf = carla.Transform(carla.Location(FOUNTAIN_X, FOUNTAIN_Y, FOUNTAIN_Z))
    actor = world.try_spawn_actor(bp, tf)
    if actor is None:
        log.warning("Failed to spawn %s at fountain (%.1f, %.1f).",
                    LANDMARKS_BP_ID, FOUNTAIN_X, FOUNTAIN_Y)
        return None
    log.info("Spawned fountain landmarks (id=%s) at (%.1f, %.1f).",
             actor.id, FOUNTAIN_X, FOUNTAIN_Y)
    return actor
