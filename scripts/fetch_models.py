"""Download the ONNX feature models used for stabilization and verify their SHA-1."""

import hashlib
import sys
import urllib.request

from thalweg.paths import models_dir

BASE = "https://raw.githubusercontent.com/YangGuanyuhan/lightglue_opencv_project/main/model/"
MODELS = {
    "aliked-n16rot-top1k-640.onnx": "41faa7bf5d7eb68a2851471ba03aa20c9db30e4c",
    "aliked_lightglue.onnx": "02723aa521990e57fe33d90b67977590c460e351",
}


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    out = models_dir()
    ok = True
    for name, digest in MODELS.items():
        dst = out / name
        if not dst.exists() or sha1(dst) != digest:
            print(f"downloading {name}")
            urllib.request.urlretrieve(BASE + name, dst)
        got = sha1(dst)
        status = "ok" if got == digest else f"SHA-1 MISMATCH {got}"
        ok &= got == digest
        print(f"{name}: {status} ({dst.stat().st_size / 1e6:.1f} MB)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
