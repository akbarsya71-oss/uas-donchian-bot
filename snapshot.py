#!/usr/bin/env python3
"""
Kolektor snapshot order book Indodax.

Dijalankan berkala (minimal 3x sehari, disarankan tiap jam) SEBELUM dan SELAMA
forward test. Data mentah disimpan apa adanya ke data/snapshots.jsonl dan
dipakai untuk mengukur spread + slippage yang dipakai backtest.

  python snapshot.py                 # satu putaran
  python snapshot.py --putaran 3 --jeda 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))
import indodax  # noqa: E402
import strategy as st  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "data", "snapshots.jsonl")
NOTIONAL_UJI = 10_000_000      # ukuran order acuan untuk mengukur slippage (Rp10 juta)


def satu_putaran(f):
    sekarang = datetime.now(timezone.utc)
    for pair in st.UNIVERSE:
        lower = pair.lower()
        try:
            book = indodax.depth(lower)
            bid, ask = book["buy"][0][0], book["sell"][0][0]
            sp = indodax.spread_bps(bid, ask)
            qty_beli, avg_beli = indodax.isi_order(book["sell"], NOTIONAL_UJI / ask, "buy")
            qty_jual, avg_jual = indodax.isi_order(book["buy"], NOTIONAL_UJI / bid, "sell")
            rec = dict(
                ts=int(sekarang.timestamp() * 1000),
                waktu=sekarang.isoformat(timespec="seconds"),
                pair=pair,
                bid=bid, ask=ask, mid=(bid + ask) / 2,
                spread_bps=round(sp, 3),
                bid_qty=book["buy"][0][1], ask_qty=book["sell"][0][1],
                bid_depth_idr=round(bid * book["buy"][0][1]),
                ask_depth_idr=round(ask * book["sell"][0][1]),
                notional_uji=NOTIONAL_UJI,
                slippage_beli_bps=round((avg_beli / ask - 1) * 1e4, 3) if avg_beli else None,
                slippage_jual_bps=round((1 - avg_jual / bid) * 1e4, 3) if avg_jual else None,
                level_bid=len(book["buy"]), level_ask=len(book["sell"]),
            )
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{pair:9s} bid {bid:>14,.0f} ask {ask:>14,.0f} spread {sp:6.2f} bps "
                  f"slip beli {rec['slippage_beli_bps']} bps")
        except Exception as e:  # noqa: BLE001
            print(f"{pair}: GAGAL {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--putaran", type=int, default=1)
    ap.add_argument("--jeda", type=float, default=60.0)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "a", encoding="utf-8") as f:
        for k in range(args.putaran):
            print(f"putaran {k + 1}/{args.putaran}")
            satu_putaran(f)
            f.flush()
            if k + 1 < args.putaran:
                time.sleep(args.jeda)
    print(f"tersimpan: {args.out}")


if __name__ == "__main__":
    main()
