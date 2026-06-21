# Importing the custom Police officer as a CARLA walker (Layer B, this source tree)

Goal: register `assets/characters/Police.fbx` as a spawnable CARLA walker so the
existing MARSHAL gesture engine drives it. **No gesture code change is needed** —
`gesture_engine.infer_upper_limb_bones` already maps Mixamo bone names
(`mixamorig:RightArm` → r_upper_arm, `RightForeArm` → r_forearm, `RightHand` →
r_hand, etc.), confirmed by inspecting the FBX.

> The stock `walker.pedestrian.0030` is already a police-uniform walker and runs
> all gestures TODAY. This procedure only swaps in the project's own higher-detail
> Police mesh. It is editor-GUI work.

## Facts established for this build
- Engine: `F:\UE4_carla` (has `PythonScriptPlugin` available, not enabled by default).
- Project: `F:\carla\Unreal\CarlaUE4\CarlaUE4.uproject`.
- Walkers are registered in **`Content/Carla/Blueprints/Walkers/WalkerFactory`**
  (a Blueprint asset). Each stock walker is a `BP_Walker` child + an entry in
  the factory's definitions array, exposed to Python as `walker.pedestrian.NNNN`.
- `Police.fbx`: Mixamo rig (`mixamorig:` prefix, 66 bones), embedded skin,
  1 baked clip (ignored — we drive gestures via `set_bones`).

## Procedure (UE4 editor; ego/PIE must be stopped)
1. **Stop PIE.** Open the editor on `CarlaUE4.uproject` if not already.
2. **Import the skeletal mesh.** Content Browser → folder
   `Content/Carla/Static/Walker/PoliceOfficer/` (create it) → *Import* →
   select `F:\coding\Autonomous Vehicle\MARSHAL\assets\characters\Police.fbx`.
   In the FBX import dialog:
   - Skeletal Mesh = **On**, Import Mesh = On, Import Materials/Textures = On.
   - Skeleton = **None** (create a new one from this FBX — it is a Mixamo
     skeleton, kept as-is; our gesture engine matches it by name).
   - Import Animations = Off (we don't use the baked clip).
   → produces `SK_Police`, `SK_Police_Skeleton`, `SK_Police_PhysicsAsset`, materials.
3. **Create the walker Blueprint.** Duplicate an existing
   `Content/Carla/Static/Walker/Walker00xx/BP_Walker_00xx` (e.g. 0030) →
   name it `BP_Walker_Police`. Open it → set its `SkeletalMeshComponent`
   mesh = `SK_Police`, AnimClass = the same walker AnimBP the source used
   (idle/walk; gestures come from `set_bones`). Keep the capsule unchanged.
4. **Register in the factory.** Open
   `Content/Carla/Blueprints/Walkers/WalkerFactory`. In its walker definitions
   array, **add a new element**: Id/Class pointing to `BP_Walker_Police`, gender/age
   tags as you like. Give it a recognizable id suffix (e.g. it will appear as
   `walker.pedestrian.<next-index>` — note the index it gets).
5. **Compile & Save** all edited assets. Press **Play** (PIE) to bring the RPC
   server back up.

## Python validation (run in the `marshal` env once PIE is up)
```
C:/Users/sunju/miniconda3/envs/marshal/python.exe scripts/_validate_police_walker.py
```
This finds the new walker blueprint, spawns it, checks `get_bones()` returns the
Mixamo bones our engine needs, and applies a STOP pose to confirm `set_bones`
animates it. Then point any scenario at it:
```
... run_marshal_officer_demo.py --scenario green_stop --town Town03 \
    --officer-blueprint walker.pedestrian.<index>
```
(or set `officer.blueprint_id` in the scenario config.)

## Scriptable alternative (step 2 only)
If `PythonScriptPlugin` is enabled for the project (add `{"Name":
"PythonScriptPlugin","Enabled": true}` to the `.uproject` Plugins array, restart
editor), the skeletal-mesh **import** in step 2 can be run from the editor's
Python console with `scripts/ue_import_police.py` (uses `unreal.AssetImportTask`).
Steps 3–4 (BP child + factory entry) remain GUI work — editing the WalkerFactory
Blueprint reliably is not worth scripting blind.

## Gotchas
- If `get_bones()` names come back WITHOUT `mixamorig`/`RightArm`-style tokens,
  `set_bones` gestures won't animate — re-check the import kept the Mixamo skeleton.
- Hot-reload of skeletal meshes is flaky; if the new walker doesn't appear,
  fully restart the editor.
- Keep the collision capsule identical to stock walkers so planner pedestrian
  logic is unchanged.
