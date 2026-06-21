# Unreal Asset Extension (Layer B)

## Purpose

Layer A of MARSHAL (the Python-only path) reuses one of the stock
`walker.pedestrian.*` blueprints as the traffic officer and animates it via
`set_bones` + an optional debug overlay. This works for the benchmark
question, but the walker visually looks like a civilian pedestrian rather
than a police officer, which weakens scenarios where vision-based perception
is part of the system under test.

Layer B is the optional path that replaces the default walker with a real
police skeletal mesh: uniform, reflective vest, optional baton or flag,
distinct semantic-segmentation label. It is implemented entirely on the
Unreal side of CARLA (CarlaUE4 project) and exposed to Python as a new
`walker.pedestrian.*` blueprint id.

## Status

**NOT IMPLEMENTED.**

This document is a checklist for whoever does the Unreal-side work. No
`.uasset` files have been added to this repo. No `Config/*.ini` entries have
been edited. No semantic-segmentation palette extension has been performed.
No `CarlaUE4` rebuild has been triggered. Do not treat anything in this
document as completed work.

Layer A is fully functional without any of the items below; Layer B only
improves visual realism and (optionally) semantic-segmentation labeling.

## Required asset components

The custom officer needs all of the following to slot into the existing
MARSHAL pipeline without code changes:

- **Skeletal mesh.** Must be retargeted to CARLA's pedestrian skeleton so
  that bone names follow the `crl_*__{C,L,R}` convention used by the default
  walkers (e.g. `crl_arm__R`, `crl_foreArm__R`, `crl_hand__R`,
  `crl_spine__C`, `crl_neck__C`, `crl_head__C`, `crl_thigh__L`, etc.). This
  is non-negotiable: if bone names diverge, `Walker.set_bones()` from
  `marshal_bench.actors.gesture_engine.GestureEngine` no longer animates the
  gestures and Layer A's skeleton path silently degrades to the debug-only
  fallback.
- **Police uniform material.** Either Substrate (UE5.2+) or standard
  Unreal PBR. Dark blue / black base color, matte cloth roughness.
- **Reflective vest material.** Standard Unreal PBR with high emissive on
  the reflective strips, or a Substrate slab with a retroreflective layer.
  Should respond visibly under headlights at night.
- **Optional accessories.** Any subset of: baton (static mesh socketed to
  `crl_hand__R`), flashlight (with optional `PointLightComponent` for
  night scenarios), hand flag (cloth or static mesh), whistle. None of
  these are required for the benchmark — they are visual polish.
- **Collision capsule.** Sized identically to the default `BP_Walker`
  capsule (radius ~34 cm, half height ~88 cm) so existing collision-based
  logic (and pedestrian avoidance heuristics in third-party planners)
  behave the same way.
- **Semantic-segmentation tag.** Ideally a new label distinct from the
  generic `Pedestrian` class so segmentation-based perception can
  distinguish officers from civilians. Two options:
  1. Add a new entry to CARLA's CityScapes-like palette (in
     `LibCarla/source/carla/image/CityScapesPalette.h` plus the matching
     Unreal stencil enum), then rebuild via
     `Tools/Build/Build.sh` / `Build.bat`. This affects every consumer of
     the segmentation camera.
  2. Override the actor's stencil id in the Blueprint
     (`SetCustomDepthStencilValue`) to a value not currently used by
     `Pedestrian`. This is local to the BP and does not require a CARLA
     rebuild, but it requires the consumer to know the custom id.

## Where to put assets

Suggested location, mirroring CARLA's existing walker layout:

```
CARLA_0.9.16/CarlaUE4/Content/Carla/Static/Walker/PoliceOfficer/
  Meshes/        SK_PoliceOfficer.uasset
  Skeletons/    SK_PoliceOfficer_Skeleton.uasset
  Animations/   AnimBP_PoliceOfficer.uasset
  Materials/    M_PoliceUniform.uasset, M_ReflectiveVest.uasset, ...
  Textures/     T_PoliceUniform_BC.uasset, T_PoliceUniform_N.uasset, ...
  Blueprints/   BP_Walker_PoliceOfficer.uasset
```

`AnimBP_PoliceOfficer` should derive from (or duplicate) the existing
walker AnimBP so that the idle/walk states match civilian walkers — the
gestures themselves come from `set_bones` overrides, not from the AnimBP.

## Blueprint registration

CARLA exposes walker blueprints to Python via the actor factory. The
canonical pattern is:

- Either add an entry to `Unreal/CarlaUE4/Config/DefaultGame.ini` (or
  whichever `Default*.ini` your CARLA fork uses for actor registration)
  under the walker section.
- Or extend `LibCarla/source/carla/client/detail/ActorFactory.cpp` (and the
  equivalent UE-side `UWalkerActorFactory` if your CARLA version uses one)
  to register the new BP under id `walker.pedestrian.police_officer`.

The exact file varies by CARLA version; inspect what the existing
`walker.pedestrian.0001` … `0049` registrations do in your installed
`CARLA_0.9.16` source tree before editing.

## Validation steps

After the Unreal-side work is done, validate end-to-end:

1. Rebuild CARLA (`Tools/Build/Build.sh`/`Build.bat`) or hot-reload the
   `CarlaUE4` Editor.
2. From Python, confirm the blueprint is discoverable:
   ```python
   bp = world.get_blueprint_library().find("walker.pedestrian.police_officer")
   ```
   This call must succeed (no `IndexError` / `RuntimeError`).
3. Confirm the actor spawns:
   ```python
   walker = world.try_spawn_actor(bp, transform)
   assert walker is not None
   ```
4. Confirm the skeleton is wired up:
   ```python
   bones = walker.get_bones()
   assert len(bones.bone_transforms) >= 60
   names = {b.name for b in bones.bone_transforms}
   assert "crl_arm__R" in names and "crl_foreArm__R" in names
   ```
5. Dump bones for diffing:
   ```bash
   python scripts/dump_walker_bones.py \
     --blueprint walker.pedestrian.police_officer
   ```
   The resulting `outputs/walker_bones.json` should match the default
   walker's bone set 1:1 (same names, same hierarchy). Any mismatch
   indicates retargeting issues that will break `set_bones`-driven
   gestures.
6. Re-run the green_stop demo with the new officer:
   ```bash
   python scripts/run_marshal_officer_demo.py \
     --scenario green_stop \
     --config marshal_bench/configs/demo_green_stop.yaml \
     --officer-blueprint walker.pedestrian.police_officer \
     --debug
   ```
   Visually verify that the new mesh appears (uniform + vest), that the
   STOP gesture still animates the upper arm, and that
   `metadata.json` reports `custom_asset: true`.

## What is NOT done in this repo

To be unambiguous:

- No `.uasset` files (mesh, skeleton, AnimBP, materials, textures,
  blueprint) have been created or committed.
- No `Config/Default*.ini` entries have been edited to register a new
  walker.
- No `LibCarla` / `CarlaUE4` C++ files have been edited.
- No `Tools/Build/Build.sh` or `Build.bat` invocation has been performed.
- The semantic-segmentation palette has not been extended; the custom
  stencil id strategy has not been applied.
- No screenshots or videos of a real police-officer mesh in CARLA exist
  in this repo.

If a future contributor or the user themselves completes the Layer B work,
update this document and the **Status** section above accordingly. Until
then, MARSHAL runs entirely on Layer A.

## Risk notes

- **UE material pipeline.** CARLA 0.9.16 ships against a specific Unreal
  Engine 5.x revision. Substrate is enabled by default in some UE5
  branches and disabled in others; a Substrate-authored material will not
  render correctly on a non-Substrate build, and vice versa. Pick the
  pipeline that matches your CarlaUE4 project settings, not the latest
  UE5 default.
- **Bone name divergence.** Any custom mesh that does not retarget to the
  `crl_*__{C,L,R}` convention will silently break `set_bones`-driven
  gestures. `infer_upper_limb_bones` does best-effort substring matching,
  but it does not fix arbitrary skeletons.
- **Rebuild times.** A full CarlaUE4 rebuild from scratch is on the order
  of an hour on a developer workstation. Plan iteration time accordingly;
  hot-reload of just the new BP/mesh is much faster but is not always
  reliable for skeletal-mesh changes.
- **Semantic palette compatibility.** If you add a new segmentation
  class, every previously trained perception model in the repo that
  consumes segmentation will need its class table updated.
- **License of source assets.** Police uniforms / patches / badge designs
  may be jurisdiction-specific and trademarked. Use generic uniform
  designs for published benchmark releases.
