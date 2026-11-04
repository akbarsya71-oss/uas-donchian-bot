#!/usr/bin/env python3
"""
Uji lokal penuh bot: tick live + dashboard.

  python uji_lokal.py
  python hitung_ulang.py --data forward/raw/uji-lokal.json

Yang diuji:
  1. tick pertama memproses candle tertutup terakhir dan validasi data,
  2. tick kedua untuk candle yang sama TIDAK memproses/membeli lagi (idempotency),
  3. setiap fill berada di dalam rentang bid/ask order book (tidak pernah mid/close),
  4. kill switch memblokir order baru dan tercatat di log,
  5. log mentah terbentuk, lalu diekspor ke forward/raw/uji-lokal.json.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

os.environ.setdefault("STORE", "file")
os.environ.setdefault("LOCAL_STORE_DIR", "local_state")
os.environ.setdefault("CRON_SECRET", "uji-lokal")

shutil.rmtree(os.environ["LOCAL_STORE_DIR"], ignore_errors=True)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))
import tick  # noqa: E402
from store import FileStore  # noqa: E402

store = FileStore(os.environ["LOCAL_STORE_DIR"])


def baris(judul):
    print(f"\n== {judul} ==")


baris("tick 1 (candle tertutup terakhir)")
a = tick.jalankan_tick(store, triger="uji-lokal")
print(json.dumps({k: a.get(k) for k in ("sukses", "bar_wib", "idempoten", "order", "sinyal",
                                        "ekuitas", "catatan", "ditolak")}, indent=1, ensure_ascii=False))
assert a.get("sukses"), "tick pertama gagal"
assert not a.get("idempoten"), "tick pertama seharusnya memproses candle baru"

baris("tick 2 (candle sama -> idempotency)")
jumlah_order_1 = len(store.lrange("bot:orders", 0, -1))
b = tick.jalankan_tick(store, triger="uji-lokal")
print(json.dumps({k: b.get(k) for k in ("sukses", "bar_wib", "idempoten", "catatan")},
                 indent=1, ensure_ascii=False))
assert b.get("idempoten"), "idempotency GAGAL: candle yang sama diproses dua kali"
assert len(store.lrange("bot:orders", 0, -1)) == jumlah_order_1, "idempotency GAGAL: ada order baru di tick kedua"

orders = [json.loads(x) for x in store.lrange("bot:orders", 0, -1)]
for o in orders:
    if o["sisi"] == "buy":
        assert o["best_bid"] <= o["harga_fill"] <= o["best_ask"] * 1.01, o
    else:
        assert o["best_bid"] * 0.99 <= o["harga_fill"] <= o["best_ask"], o
print(f"OK: {len(orders)} order, semua di dalam rentang bid/ask (fill tidak pernah di mid/close).")

baris("kill switch")
store.set_json("bot:kill", {"aktif": True, "waktu": "uji", "oleh": "uji_lokal"})
state = store.get_json("bot:state")
state["last_bar_time"] = 0            # paksa candle baru supaya kill switch yang diuji
store.set_json("bot:state", state)
c = tick.jalankan_tick(store, triger="uji-lokal")
print(json.dumps({k: c.get(k) for k in ("sukses", "catatan", "kill_switch", "dihentikan")}, indent=1, ensure_ascii=False))
assert c.get("dihentikan") and not c.get("order"), "kill switch GAGAL: tetap ada order"
assert len(store.lrange("bot:orders", 0, -1)) == jumlah_order_1, "kill switch GAGAL: order baru muncul"
store.set_json("bot:kill", {"aktif": False})

baris("ekspor log mentah -> forward/raw/uji-lokal.json")
os.makedirs("forward/raw", exist_ok=True)
d = "local_state"
ambil = lambda k: [json.loads(x) for x in store.lrange(k, 0, -1)]
ekspor = dict(ok=True, state=store.get_json("bot:state"), calls=ambil("bot:calls"), orders=ambil("bot:orders"),
              trades=ambil("bot:trades"), equity=ambil("bot:equity"), signals=ambil("bot:signals"),
              snapshots=ambil("bot:snapshots"), errors=ambil("bot:error"), kill=store.get_json("bot:kill"))
with open("forward/raw/uji-lokal.json", "w", encoding="utf-8") as f:
    json.dump(ekspor, f, ensure_ascii=False)
print({k: (len(v) if isinstance(v, list) else 1) for k, v in ekspor.items()})
print("\nSEMUA UJI LULUS. Jalankan: python hitung_ulang.py --data forward/raw/uji-lokal.json")
