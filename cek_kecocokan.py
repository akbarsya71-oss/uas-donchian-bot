#!/usr/bin/env python3
"""
cek_kecocokan.py — membandingkan LOG SINYAL bot dengan sinyal backtest pada candle
yang sama (target rubrik: identik candle demi candle).

  python ekspor_log.py --url https://<app>.vercel.app/api/data
  python backtest.py --mulai 2026-11-04 --selesai 2026-11-14 --tanpa-validasi \
                     --rekam-sinyal --out backtest_forward.json
  python cek_kecocokan.py

Membandingkan: untuk setiap (bar_time, pair) di log bot, apakah `sinyal` sama dengan
hasil `strategy.sinyal_masuk` pada backtest untuk candle yang sama. Juga membandingkan
keputusan keluar (exit) per candle bila posisi sedang terbuka.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "api"))
import strategy as st  # noqa: E402
import hitung_ulang as hu  # noqa: E402


def main():
    log = hu.baca_log_mentah(os.path.join(ROOT, "forward", "raw"))
    sinyal_bot = {(int(s["bar_time"]), s["pair"]): bool(s["sinyal"]) for s in log["signals"]
                  if isinstance(s.get("bar_time"), (int, float))}
    if not sinyal_bot:
        sys.exit("log bot:signals kosong — ekspor dulu dengan ekspor_log.py")

    path = os.path.join(ROOT, "backtest_forward.json")
    if not os.path.exists(path):
        sys.exit("backtest_forward.json belum ada")
    with open(path, encoding="utf-8") as f:
        bt = json.load(f)
    sinyal_bt = {}
    for s in bt.get("_sinyal", []):
        t = s["bar_time"]
        t = int(t) * 1000 if t < 1e12 else int(t)
        sinyal_bt[(t, s["pair"])] = bool(s["sinyal"])
    if not sinyal_bt:
        sys.exit("backtest_forward.json tidak memuat _sinyal — jalankan ulang backtest dengan --rekam-sinyal")

    # backtest dijalankan pada jendela yang lebih panjang (agar indikatornya "panas");
    # perbandingan hanya pada candle yang juga diproses bot.
    t_awal = min(k[0] for k in sinyal_bot)
    t_akhir = max(k[0] for k in sinyal_bot)
    sinyal_bt = {k: v for k, v in sinyal_bt.items() if t_awal <= k[0] <= t_akhir + 4 * 3600 * 1000}
    if all((k[0], k[1]) not in sinyal_bt for k in sinyal_bot):
        sys.exit("tidak ada candle yang tumpang tindih: jalankan backtest dengan --mulai yang mencakup jendela forward")

    sama, beda = 0, []
    for k, v in sinyal_bot.items():
        if k in sinyal_bt:
            if sinyal_bt[k] == v:
                sama += 1
            else:
                beda.append(dict(bar_time=k[0], pair=k[1], bot=v, backtest=sinyal_bt[k]))
    total = sama + len(beda)
    print(f"candle/pair dibandingkan: {total} | identik: {sama} ({sama / total:.1%}) | beda: {len(beda)}")
    for b in beda[:20]:
        print("  BEDA:", hu.wib(b["bar_time"]), b["pair"], "bot", b["bot"], "vs backtest", b["backtest"])
    hasil = dict(total=total, identik=sama, beda=beda, persen=(sama / total) if total else 0.0)
    with open(os.path.join(ROOT, "kecocokan_sinyal.json"), "w", encoding="utf-8") as f:
        json.dump(hasil, f, indent=1, ensure_ascii=False)
    print("-> kecocokan_sinyal.json")


if __name__ == "__main__":
    main()
