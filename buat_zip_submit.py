#!/usr/bin/env python3
"""Buat berkas ZIP pengumpulan UAS.

  python buat_zip_submit.py                # ZIP di folder induk, nama uas-<tanggal>.zip
  python buat_zip_submit.py --out D:\\kirim.zip

Yang dimasukkan:
  - seluruh source, dokumen, dashboard, log mentah, data snapshot
  - folder .git (supaya commit freeze terlihat)
Yang dikeluarkan:
  - local_state/, data/candles_4h.csv.gz, data/raw_history/, __pycache__, .vercel/
  - file sementara (_*.py) bila ada
"""
from __future__ import annotations

import argparse
import os
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))

SKIP_DIR = {".vercel", "local_state", "raw_history", "__pycache__", ".pytest_cache"}
SKIP_FILE = {"candles_4h.csv.gz"}
SKIP_PREFIX = ("_",)   # file sementara _*.py


def harus_dilewati(rel: str) -> bool:
    parts = rel.split(os.sep)
    if any(p in SKIP_DIR for p in parts):
        return True
    base = os.path.basename(rel)
    if base in SKIP_FILE:
        return True
    if base.startswith(SKIP_PREFIX) and base.endswith(".py"):
        return True
    if base.endswith((".pyc",)):
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    tanggal = datetime.now().strftime("%Y%m%d")
    out = args.out or os.path.join(os.path.dirname(ROOT), f"uas-algoritma-trading-{tanggal}.zip")

    jumlah = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            # jangan masuk folder yang dilewati
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT)
                if harus_dilewati(rel):
                    continue
                z.write(full, os.path.join("uas-algoritma-trading", rel))
                jumlah += 1
        # .git folder (os.walk di atas melewati .git? tidak, tapi pastikan masuk)
    print(f"tersimpan: {out}")
    print(f"berkas: {jumlah}")
    print("ukuran:", round(os.path.getsize(out) / 1e6, 2), "MB")


if __name__ == "__main__":
    main()