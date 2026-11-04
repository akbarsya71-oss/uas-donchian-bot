#!/usr/bin/env python3
"""
ekspor_log.py — mengekspor log mentah bot dari storage (Vercel) ke folder lokal
forward/raw/, supaya bisa dihitung ulang secara offline.

  python ekspor_log.py --url https://<app>.vercel.app/api/data
  python ekspor_log.py --url ... --label 2026-11-14   # label berkas
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--label", default=None)
    ap.add_argument("--out", default=os.path.join(ROOT, "forward", "raw"))
    args = ap.parse_args()

    req = urllib.request.Request(args.url, headers={"User-Agent": "ekspor-log/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8"))
    if not d.get("ok"):
        sys.exit(f"gagal: {d.get('error')}")

    os.makedirs(args.out, exist_ok=True)
    label = args.label or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")

    # 1) seluruh isi sebagai satu berkas ekspor
    with open(os.path.join(args.out, label + ".json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)

    # 2) dipecah per jenis log (jsonl) — bentuk "mentah" yang dipakai hitung_ulang
    for jenis in ("calls", "orders", "trades", "equity", "signals", "snapshots", "errors"):
        with open(os.path.join(args.out, f"{label}__{jenis}.jsonl"), "w", encoding="utf-8") as f:
            for rec in d.get(jenis) or []:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(os.path.join(args.out, f"{label}__state.jsonl"), "w", encoding="utf-8") as f:
        if d.get("state"):
            f.write(json.dumps(d["state"], ensure_ascii=False) + "\n")
    with open(os.path.join(args.out, f"{label}__kill.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(d.get("kill") or {"aktif": False}, ensure_ascii=False) + "\n")

    print(f"tersimpan di {args.out}: {len(d.get('calls') or [])} call, {len(d.get('orders') or [])} order, "
          f"{len(d.get('signals') or [])} sinyal, {len(d.get('snapshots') or [])} snapshot")


if __name__ == "__main__":
    main()
