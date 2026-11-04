#!/usr/bin/env python3
"""Klien API publik Indodax (tanpa API key) + simulasi fill order book."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; uas-algo-trading-dryrun/1.0)"}
BASE = "https://indodax.com"
TF_MS = {"1": 60_000, "15": 900_000, "30": 1_800_000, "60": 3_600_000,
         "240": 14_400_000, "1D": 86_400_000}


class ApiError(RuntimeError):
    pass


def get_json(url: str, timeout: int = 20, retries: int = 2, backoff: float = 1.0):
    """GET + retry terbatas (total percobaan = retries + 1)."""
    last = None
    for k in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    raise ApiError(f"HTTP {r.status}")
                body = r.read()
            return json.loads(body.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            if k < retries:
                time.sleep(backoff * (2 ** k))
    raise ApiError(f"gagal {url}: {last}")


def server_time_ms() -> int:
    return int(get_json(f"{BASE}/api/server_time")["server_time"])


def pairs() -> list[dict]:
    return get_json(f"{BASE}/api/pairs")


def price_increments() -> dict:
    return get_json(f"{BASE}/api/price_increments").get("increments", {})


def depth(pair_lower: str) -> dict:
    """Order book: buy = bid (tertinggi dulu), sell = ask (terendah dulu)."""
    d = get_json(f"{BASE}/api/depth/{pair_lower}")
    if not isinstance(d, dict) or not d.get("buy") or not d.get("sell"):
        raise ApiError("order book kosong")
    return {"buy": [[float(p), float(q)] for p, q in d["buy"]],
            "sell": [[float(p), float(q)] for p, q in d["sell"]]}


def history(symbol: str, tf: str = "240", frm: int | None = None, to: int | None = None) -> list[dict]:
    """Candle mentah (bisa memuat candle yang masih berjalan)."""
    to = to or int(time.time())
    frm = frm or (to - 700 * TF_MS[tf] // 1000)
    url = (f"{BASE}/tradingview/history_v2?symbol={urllib.parse.quote(symbol)}"
           f"&tf={tf}&from={frm}&to={to}")
    data = get_json(url)
    if not isinstance(data, list):
        raise ApiError("format history tidak dikenal")
    return [{"time": int(b["Time"]), "open": float(b["Open"]), "high": float(b["High"]),
             "low": float(b["Low"]), "close": float(b["Close"]),
             "volume": float(b["Volume"])} for b in data]


def candle_tertutup(symbol: str, tf: str, tf_ms: int, now_ms: int, perlu: int = 260) -> list[dict]:
    """Candle yang SUDAH tutup saja.

    API ikut mengirim candle berjalan sebagai elemen terakhir. Bar dibuang bila
    Time + durasi > server_time. Diambil beberapa halaman bila perlu.
    """
    bars = history(symbol, tf, to=now_ms // 1000)
    bars = [b for b in bars if b["time"] + tf_ms // 1000 <= now_ms // 1000]
    bars.sort(key=lambda b: b["time"])
    # buang duplikat
    out, seen = [], set()
    for b in bars:
        if b["time"] not in seen:
            seen.add(b["time"])
            out.append(b)
    return out[-perlu:]


# ------------------------------------------------------------- simulasi order
def isi_order(levels: list[list[float]], qty: float, sisi: str):
    """Jalankan order terhadap level order book.

    levels: [[harga, qty], ...] sudah terurut (ask naik untuk beli,
    bid turun untuk jual). Mengembalikan (qty_terisi, harga_rata2) atau
    (qty_terisi, None) bila likuiditas tidak cukup.
    """
    sisa = qty
    nilai = 0.0
    for harga, tersedia in levels:
        ambil = min(sisa, tersedia)
        nilai += ambil * harga
        sisa -= ambil
        if sisa <= 1e-12:
            break
    terisi = qty - sisa
    if terisi <= 0:
        return 0.0, None
    if sisa > 1e-9 * max(qty, 1e-9):
        return terisi, None       # likuiditas tidak cukup -> order tidak dieksekusi penuh
    return terisi, nilai / terisi


def spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 1e4 if mid else 0.0
