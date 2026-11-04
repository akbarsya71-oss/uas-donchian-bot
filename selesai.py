#!/usr/bin/env python3
"""
selesai.py — SATU perintah untuk membangun semua bahan pengumpulan:

  1. tarik/segarkan candle Indodax (opsional --tarik)
  2. ambil beberapa snapshot order book sekarang
  3. backtest 3+ tahun + validasi            -> hasil_backtest.json
  4. backtest jendela forward (sampai sekarang) -> backtest_forward.json
  5. ekspor log bot dari Vercel (opsional --url) -> forward/raw/
  6. hitung_ulang -> dashboard.html + angkadashboard.json
  7. periksa tidak ada secret di repo

  python selesai.py --url https://<app>.vercel.app/api/data
  python selesai.py --tarik --url https://<app>.vercel.app/api/data
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))


def jalankan(*cmd):
    print("\n$ " + " ".join(cmd))
    r = subprocess.run([sys.executable, *cmd], cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"gagal: {' '.join(cmd)}")


def cek_secret():
    pola = ["CRON_SECRET=", "UPSTASH_REDIS_REST_TOKEN=", "ghp_", "sk-", "Bearer ey"]
    temuan = []
    for path in glob.glob(os.path.join(ROOT, "**", "*"), recursive=True):
        if not os.path.isfile(path) or path.endswith((".gz", ".png", ".pdf")):
            continue
        if os.sep + ".git" + os.sep in path or os.sep + "local_state" + os.sep in path:
            continue
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        if os.path.basename(path) in (".env.example", "selesai.py"):
            continue
        try:
            teks = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for p in pola:
            if p in teks:
                temuan.append((os.path.relpath(path, ROOT), p))
    if temuan:
        print("\nPERINGATAN: temuan yang mirip secret:")
        for t in temuan:
            print("  ", t)
        sys.exit(1)
    print("\nOK: tidak ada pola secret di repo (selain .env.example).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="/api/data di Vercel untuk diekspor")
    ap.add_argument("--tarik", action="store_true", help="tarik ulang candle (perlu waktu)")
    ap.add_argument("--mulai-forward", default="2026-10-25")
    ap.add_argument("--tanpa-backtest", action="store_true")
    args = ap.parse_args()

    if args.tarik:
        jalankan("fetch_history.py", "--start", "2023-08-01")
    jalankan("snapshot.py", "--putaran", "3", "--jeda", "20")
    if not args.tanpa_backtest:
        jalankan("backtest.py", "--out", "hasil_backtest.json")
        hari_ini = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jalankan("backtest.py", "--mulai", args.mulai_forward, "--selesai", hari_ini,
                 "--tanpa-validasi", "--rekam-sinyal", "--out", "backtest_forward.json")
    if args.url:
        jalankan("ekspor_log.py", "--url", args.url)
    if glob.glob(os.path.join(ROOT, "forward", "raw", "*.json")):
        jalankan("hitung_ulang.py")
    else:
        print("\nbelum ada log forward (forward/raw/) — dashboard dibuat dari log lokal saja")
        jalankan("hitung_ulang.py", "--raw", os.path.join(ROOT, "forward", "raw"))
    if glob.glob(os.path.join(ROOT, "forward", "raw", "*signals*")):
        jalankan("cek_kecocokan.py")
    cek_secret()


if __name__ == "__main__":
    main()
