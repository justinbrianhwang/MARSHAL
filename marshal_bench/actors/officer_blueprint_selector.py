"""Blueprint-selection heuristics for the MARSHAL traffic-officer actor.

Recent CARLA 0.9.16 content DOES include native police-officer walkers
(`BP_Walker_MaleAmer_Cop`, `BP_Walker_FemaleAfro02_Cop`). They are exposed in
the actor catalog under numeric ids like `walker.pedestrian.0030`, so an
id-substring scan for "cop"/"police" can never find them — instead we keep an
explicit ``_KNOWN_POLICE_WALKERS`` list of verified police walker ids and try
those first. The id-substring heuristic remains as a fallback for custom
content whose blueprints are named descriptively.

Verified on a CARLA 0.9.16 source build with content package 20250912:
`walker.pedestrian.0030` is a navy-uniformed officer (cap + badge).

This module also provides helpers for selecting police-like vehicle, traffic
cone, and warning-prop blueprints used to dress the scene.

All functions are defensive: if the blueprint library is unreachable or no
candidate matches, they return safe empties (None / []) rather than raising.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from marshal_bench.utils.carla_api_compat import filter_blueprints

log = logging.getLogger("marshal_bench.actors.officer_blueprint_selector")


# Verified native police-officer walker ids, tried before any heuristic.
# CARLA exposes walkers under numeric ids so the substring scan below cannot
# detect a uniformed officer by name — this explicit list bridges that gap.
# Order = preference. Verified on CARLA 0.9.16 source build, content 20250912.
_KNOWN_POLICE_WALKERS: tuple[str, ...] = (
    "walker.pedestrian.0030",  # navy-uniformed officer, cap + badge
)

# Officer-like substrings to scan blueprint ids for (fallback for custom,
# descriptively-named content).
_OFFICER_HINTS: tuple[str, ...] = (
    "police", "cop", "officer", "flagger", "worker",
    "construction", "security", "vest", "uniform", "traffic",
)

# Preferred stable pedestrian fallback IDs (present in vanilla CARLA 0.9.x).
_PREFERRED_FALLBACK_WALKERS: tuple[str, ...] = tuple(
    f"walker.pedestrian.{i:04d}" for i in range(1, 11)
)

# Vehicle-blueprint substrings for police-like cars.
_POLICE_VEHICLE_HINTS: tuple[str, ...] = (
    "police", "dodge.charger_police", "ford.crown", "patrol",
)

# Cone-blueprint substrings.
_CONE_HINTS: tuple[str, ...] = ("cone", "trafficcone", "constructioncone")

# Warning-prop categories -> substring hints (case-insensitive).
_WARNING_PROP_HINTS: dict[str, tuple[str, ...]] = {
    "warning_sign": ("warning", "sign"),
    "barrier":      ("barrier", "fence", "guardrail"),
    "barrel":       ("barrel", "drum"),
    "flag":         ("flag",),
}


# ---------------------------------------------------------------------------
def _by_id(bps: list[Any], target: str) -> Optional[Any]:
    for bp in bps:
        if getattr(bp, "id", "") == target:
            return bp
    return None


def _first_id_containing(bps: list[Any], hints: tuple[str, ...]) -> Optional[Any]:
    for bp in bps:
        bid = getattr(bp, "id", "").lower()
        if any(h in bid for h in hints):
            return bp
    return None


# ---------------------------------------------------------------------------
def select_officer_blueprint(
    world: Any, preferred: Optional[str] = None
) -> tuple[Optional[Any], str]:
    """Pick the best walker blueprint to represent a traffic officer.

    Returns (blueprint, reason). If nothing at all is available the blueprint
    is None — callers must guard against this.
    """
    walkers = filter_blueprints(world, "walker.pedestrian.*")
    if not walkers:
        log.warning("No walker.pedestrian.* blueprints available.")
        return None, "no walker blueprints available"

    if preferred:
        bp = _by_id(walkers, preferred)
        if bp is not None:
            return bp, f"matched preferred='{preferred}'"
        log.info("Preferred walker '%s' not found; falling back to heuristics.", preferred)

    # verified native police-officer walkers (numeric ids the scan can't catch)
    for police_id in _KNOWN_POLICE_WALKERS:
        bp = _by_id(walkers, police_id)
        if bp is not None:
            return bp, f"matched known police walker {police_id}"

    # heuristic scan
    for hint in _OFFICER_HINTS:
        bp = _first_id_containing(walkers, (hint,))
        if bp is not None:
            return bp, f"matched '{hint}' heuristic on {bp.id}"

    # preferred stable fallback IDs
    for stable in _PREFERRED_FALLBACK_WALKERS:
        bp = _by_id(walkers, stable)
        if bp is not None:
            return bp, f"fallback to stable walker {stable}"

    return walkers[0], f"fallback to first walker {walkers[0].id}"


def select_police_vehicle_blueprint(world: Any) -> Optional[Any]:
    """Return a police-like vehicle blueprint, or None when none matches."""
    vehicles = filter_blueprints(world, "vehicle.*")
    if not vehicles:
        return None
    for hint in _POLICE_VEHICLE_HINTS:
        bp = _first_id_containing(vehicles, (hint,))
        if bp is not None:
            return bp
    return None


def select_cone_blueprints(world: Any) -> list[Any]:
    """Return all static-prop blueprints whose id looks like a traffic cone."""
    props = filter_blueprints(world, "static.prop.*")
    if not props:
        return []
    out: list[Any] = []
    for bp in props:
        bid = getattr(bp, "id", "").lower()
        if any(h in bid for h in _CONE_HINTS):
            out.append(bp)
    return out


def select_warning_prop_blueprints(world: Any) -> dict[str, Optional[Any]]:
    """Return {category: blueprint|None} for warning_sign / barrier / barrel / flag."""
    props = filter_blueprints(world, "static.prop.*")
    out: dict[str, Optional[Any]] = {k: None for k in _WARNING_PROP_HINTS}
    if not props:
        return out
    for category, hints in _WARNING_PROP_HINTS.items():
        for bp in props:
            bid = getattr(bp, "id", "").lower()
            if any(h in bid for h in hints):
                out[category] = bp
                break
    return out
