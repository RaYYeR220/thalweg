"""Download the public datasets into $THALWEG_DATA.

    python scripts/fetch_data.py vgc2020     # Video Globe Challenge 2020, 1.1 GB (etalab-2.0)
    python scripts/fetch_data.py dart-meta   # River Dart survey, stage and file lists (CC BY 4.0)

The River Dart videos (24 + 39 GB 7z archives) are best fetched on a cloud instance; see worker/.
"""

import sys
import urllib.request
import zipfile

from thalweg.paths import data_dir

VGC_URL = "https://entrepot.recherche.data.gouv.fr/api/access/datafile/159147"
DART_FILES = {
    "fileListIn_validation.csv": 35110741, "fileListIn_calibration.csv": 35110744, "gcpLocal_v2.csv": 35110747,
    "cal_val_stage_discharge_combined.csv": 47551256, "transect_2.474max.csv": 47551913,
}


def fetch(url, dst):
    if dst.exists():
        print(f"have {dst}")
        return
    print(f"downloading {url} -> {dst}")
    urllib.request.urlretrieve(url, dst)


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "vgc2020"
    root = data_dir()
    if what == "vgc2020":
        d = root / "vgc2020"
        d.mkdir(parents=True, exist_ok=True)
        z = d / "vgc2020.zip"
        fetch(VGC_URL, z)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(d)
        print(f"extracted to {d}")
    elif what == "dart-meta":
        d = root / "dart"
        d.mkdir(parents=True, exist_ok=True)
        for name, fid in DART_FILES.items():
            fetch(f"https://ndownloader.figshare.com/files/{fid}", d / name)
    else:
        raise SystemExit(f"unknown dataset {what}")


if __name__ == "__main__":
    main()
