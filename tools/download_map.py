"""Download + install the logo-baked Town03_MARSHAL map from Google Drive.

A packaged CARLA map is too large to commit to git, so it is hosted on Google
Drive. This script downloads the archive and unpacks it into your CARLA
installation so ``--town Town03_MARSHAL`` works.

    python tools/download_map.py --carla-root /path/to/CARLA_0.9.16

Status: set ``MAP_DRIVE_ID`` below once the packaged map is published. Until
then, the benchmark runs on stock Town03 (no download needed).
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile

# Google Drive file id for the Town03_MARSHAL package zip. Empty until published.
MAP_DRIVE_ID = ""
MAP_ARCHIVE_NAME = "Town03_MARSHAL.zip"


def _download_gdrive(file_id: str, dest: str) -> None:
    try:
        import gdown  # type: ignore
    except ImportError:
        sys.exit("Please `pip install gdown` to download from Google Drive, or "
                 "download the archive manually and pass --archive.")
    url = f"https://drive.google.com/uc?id={file_id}"
    gdown.download(url, dest, quiet=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--carla-root", required=True,
                    help="Path to your CARLA install (the folder with CarlaUE4).")
    ap.add_argument("--archive", default=None,
                    help="Path to an already-downloaded Town03_MARSHAL.zip "
                         "(skips the Google Drive download).")
    args = ap.parse_args()

    archive = args.archive
    if archive is None:
        if not MAP_DRIVE_ID:
            sys.exit("Town03_MARSHAL is not published yet (MAP_DRIVE_ID is "
                     "empty). Use the default stock map: --town Town03.")
        archive = os.path.join(os.getcwd(), MAP_ARCHIVE_NAME)
        print(f"Downloading Town03_MARSHAL -> {archive}")
        _download_gdrive(MAP_DRIVE_ID, archive)

    if not os.path.isfile(archive):
        sys.exit(f"Archive not found: {archive}")
    if not os.path.isdir(args.carla_root):
        sys.exit(f"CARLA root not found: {args.carla_root}")

    print(f"Unpacking {archive} -> {args.carla_root}")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(args.carla_root)
    print("Done. Start CARLA and run: python start.py --town Town03_MARSHAL ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
