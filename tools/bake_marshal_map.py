"""Bake the fountain lab-logo landmarks into a Town03_MARSHAL map variant.

This is an **Unreal Editor Python script** — it runs *inside* the CARLA UE4
editor (which ships the PythonScriptPlugin), not in your normal Python env. It
duplicates Town03, drops the Town03Landmarks static mesh at the fountain, and
saves the result as Town03_MARSHAL.

Run it headless from a CARLA source build:

    "<UE4>/Engine/Binaries/Win64/UE4Editor-Cmd.exe" \\
        "<carla>/Unreal/CarlaUE4/CarlaUE4.uproject" \\
        -run=pythonscript -script="<this file>" -unattended -nosplash

Then cook/package the map for distribution (`make package`), zip it, and host it
(see tools/download_map.py). The landmark mesh must already be imported at
``/Game/MarshalProps/Static/Static/Town03Landmarks/Town03Landmarks`` (run
``tools/make_town03_landmarks.py`` + the CARLA prop import once).

CARLA(0,4,0) m  ->  UE(0,-400,0) cm   (UE = carla.x*100, -carla.y*100, carla.z*100)
"""
SRC_MAP = "/Game/Carla/Maps/Town03"
DST_MAP = "/Game/Carla/Maps/Town03_MARSHAL"
MESH = "/Game/MarshalProps/Static/Static/Town03Landmarks/Town03Landmarks"
FOUNTAIN_UE = (0.0, -400.0, 0.0)  # CARLA (0,4,0) m -> UE cm


def bake() -> None:
    import unreal  # available only inside the UE editor

    if unreal.EditorAssetLibrary.does_asset_exist(DST_MAP):
        unreal.log_warning(f"{DST_MAP} already exists — deleting and rebuilding.")
        unreal.EditorAssetLibrary.delete_asset(DST_MAP)

    if not unreal.EditorAssetLibrary.duplicate_asset(SRC_MAP, DST_MAP):
        unreal.log_error(f"Failed to duplicate {SRC_MAP} -> {DST_MAP}")
        return

    unreal.EditorLevelLibrary.load_level(DST_MAP)
    mesh = unreal.EditorAssetLibrary.load_asset(MESH)
    if mesh is None:
        unreal.log_error(f"Landmark mesh not found: {MESH} — import it first.")
        return

    loc = unreal.Vector(*FOUNTAIN_UE)
    actor = unreal.EditorLevelLibrary.spawn_actor_from_object(
        mesh, loc, unreal.Rotator(0.0, 0.0, 0.0))
    actor.set_actor_label("MarshalLandmarks")
    # Static, baked-in: no movement, casts shadows like the rest of the map.
    try:
        actor.set_mobility(unreal.ComponentMobility.STATIC)
    except Exception:  # noqa: BLE001
        pass

    unreal.EditorLevelLibrary.save_current_level()
    unreal.log(f"Baked Town03_MARSHAL with landmarks at UE{FOUNTAIN_UE}.")


if __name__ == "__main__":
    bake()
