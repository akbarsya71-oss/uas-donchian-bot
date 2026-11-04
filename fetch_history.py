#!/usr/bin/env python3
"""
Penarik data candle historis Indodax (API publik, tanpa API key).

API history_v2 hanya mengembalikan sekitar 650 bar terakhir sebelum `to`,
jadi penarikan dilakukan MUNDUR: mulai dari sekarang, lalu geser `to` ke
waktu bar paling awal yang sudah didapat, ulangi sampai tanggal awal.

Aturan aman: maksimal 3 request/detik, sekuensial (tidak paralel).

Contoh:
  python fetch_history.py --start 2023-08-01
  python fetch_history.py --start 2023-08-01 --pairs BTCIDR ETHIDR
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw_history")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) riset-uas-algo-trading"}
BASE = "https://indodax.com/tradingview/history_v2"

# Universe beku (dipilih dari kriteria likuiditas pada AWAL periode backtest,
# lihat STRATEGI.md). Jangan diubah setelah freeze date.
UNIVERSE = ["BTCIDR", "ETHIDR", "XRPIDR", "SOLIDR", "DOGEIDR"]
TF = "240"          # 4 jam
TF_SEC = 240 * 60


def http_json(url: str, retries: int = 3, pause: float = 1.5):
    last = None
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - error jaringan apa pun -> retry terbatas
            last = e
            time.sleep(pause * (k + 1))
    raise RuntimeError(f"gagal mengambil {url}: {last}")


def fetch_symbol(symbol: str, start_ts: int, end_ts: int) -> list[dict]:
    """Tarik semua bar 4 jam dari start_ts sampai end_ts, mundur per ~650 bar."""
    bars: dict[int, dict] = {}
    to = end_ts
    page = 0
    while to > start_ts:
        frm = to - 700 * TF_SEC
        url = f"{BASE}?symbol={symbol}&tf={TF}&from={frm}&to={to}"
        data = http_json(url)
        page += 1
        if not isinstance(data, list) or not data:
            print(f"  {symbol}: halaman {page} kosong, berhenti")
            break
        for b in data:
            t = int(b["Time"])
            if start_ts <= t <= end_ts:
                bars[t] = {
                    "time": t,
                    "open": float(b["Open"]),
                    "high": float(b["High"]),
                    "low": float(b["Low"]),
                    "close": float(b["Close"]),
                    "volume": float(b["Volume"]),
                }
        earliest = min(int(b["Time"]) for b in data)
        print(f"  {symbol}: halaman {page}, {len(data)} bar, mundur ke "
              f"{datetime.fromtimestamp(earliest, timezone.utc):%Y-%m-%d}")
        os.makedirs(RAW, exist_ok=True)
        rawname = f"{symbol}_{to}.json.gz"
        with gzip.open(os.path.join(RAW, rawname), "wt", encoding="utf-8") as f:
            json.dump(data, f)
        if earliest <= start_ts:
            break
        if earliest >= to:          # tidak ada kemajuan -> hentikan (hindari loop)
            break
        to = earliest - TF_SEC
        time.sleep(0.34)            # <= 3 request/detik
    return [bars[t] for t in sorted(bars)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2023-08-01", help="tanggal awal (YYYY-MM-DD, UTC)")
    ap.add_argument("--end", default=None, help="tanggal akhir (default: sekarang)")
    ap.add_argument("--pairs", nargs="*", default=UNIVERSE)
    args = ap.parse_args()

    start_ts = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    if args.end:
        end_ts = int(datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    else:
        end_ts = int(datetime.now(timezone.utc).timestamp())

    os.makedirs(DATA, exist_ok=True)
    out_path = os.path.join(DATA, "candles_4h.csv.gz")
    existing: dict[tuple[str, int], dict] = {}
    if os.path.exists(out_path):
        with gzip.open(out_path, "rt", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing[(r["Ticker"], int(r["Time"]))] = r
        print(f"data lama: {len(existing)} bar")

    for sym in args.pairs:
        print(f"menarik {sym} ...")
        bars = fetch_symbol(sym, start_ts, end_ts)
        for b in bars:
            existing[(sym, b["time"])] = {
                "Ticker": sym, "Time": b["time"], "Open": b["open"], "High": b["high"],
                "Low": b["low"], "Close": b["close"], "Volume": b["volume"],
            }
        if bars:
            print(f"  -> {len(bars)} bar, {datetime.fromtimestamp(bars[0]['time'], timezone.utc):%Y-%m-%d} s/d "
                  f"{datetime.fromtimestamp(bars[-1]['time'], timezone.utc):%Y-%m-%d}")

    rows = sorted(existing.values(), key=lambda r: (r["Ticker"], int(r["Time"])))
    tmp = out_path + ".tmp"
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Ticker", "Time", "Open", "High", "Low", "Close", "Volume"])
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out_path)
    print(f"tersimpan: {out_path} ({len(rows)} bar)")

    with open(os.path.join(DATA, "fetch_log.json"), "w", encoding="utf-8") as f:
        json.dump({
            "ditarik_pada_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sumber": BASE + "?symbol=<PAIR>&tf=240&from=<unix>&to=<unix>",
            "pairs": args.pairs, "timeframe": TF,
            "mulai": args.start, "selesai": args.end or "sekarang",
            "jumlah_bar": len(rows),
        }, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
