#!/usr/bin/env python3
"""
ATURAN STRATEGI — SATU-SATUNYA SUMBER KEBENARAN.

Modul ini dipakai oleh backtest (backtest.py) dan bot dry run (api/tick.py),
sehingga fungsi sinyal, sizing, dan exit-nya benar-benar sama.

Strategi : Donchian Breakout + Filter Tren (adaptasi kripto dari strategi UTS)
Timeframe: 4 jam (tf=240 menit), candle tertutup saja
Pasar    : Indodax IDR, long-only, tanpa leverage

Semua fungsi di bawah hanya membaca bar yang SUDAH TUTUP.
"""
from __future__ import annotations

import math

# --------------------------------------------------------------- parameter beku
P = dict(
    tf="240",
    tf_ms=240 * 60 * 1000,
    donchian=20,          # breakout: close > high tertinggi 20 bar SEBELUM bar ini
    atr_periode=14,
    atr_mult=3.0,         # jarak stop / trailing = 3 x ATR
    rp_mult=99.0,         # take profit parsial OPSIONAL (2.0) — dimatikan: rp_mult=99 = tidak aktif
    rp_porsi=0.50,        # porsi yang dijual saat take profit parsial
    ma_cepat=20,
    ma_menengah=50,
    ma_lambat=200,
    risiko=0.02,          # risiko 2% ekuitas per trade (jarak stop 3 ATR)
    maks_posisi=8,
    maks_alokasi=0.10,    # maks 10% ekuitas per posisi
    adv_min=50_000_000,   # likuiditas minimum: nilai transaksi 20 bar >= Rp50 juta
    adv_persen=0.05,      # ukuran order maks 5% dari ADV20
    min_order=25_000,     # minimum order Pro Indodax (help.indodax.com, akses 4 Nov 2026)
    fee_beli=0.0020555,   # taker all-in IDR BUY 0,20555% (efektif 1 Okt 2026)
    fee_jual=0.0041555,   # taker all-in IDR SELL 0,41555%
    daily_loss=0.03,      # daily loss limit 3% ekuitas awal hari (WIB)
    modal=100_000_000,
)

# Universe beku: 5 pasangan IDR likuid yang sudah listing sebelum 2023-08-01.
# Kriteria + bukti likuiditas ada di STRATEGI.md.
UNIVERSE = ["BTCIDR", "ETHIDR", "XRPIDR", "SOLIDR", "DOGEIDR"]

# quantity_increment dari /api/pairs (akses 2026-11-04). Bot menyegarkan ini
# setiap tick; nilai di bawah hanya fallback bila endpoint gagal.
QTY_STEP = {
    "BTCIDR": 1e-08,
    "ETHIDR": 1e-08,
    "XRPIDR": 1e-08,
    "SOLIDR": 1e-08,
    "DOGEIDR": 1e-08,
}


# ------------------------------------------------------------------- indikator
def sma(values, n):
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def indikator(bars, p=P):
    """bars: list dict {time,open,high,low,close,volume} terurut naik.

    Mengembalikan dict berisi list sejajar panjang bars.
    """
    n = len(bars)
    o = [b["open"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]
    v = [b["volume"] for b in bars]

    tr = [0.0] * n
    for i in range(n):
        if i == 0:
            tr[i] = h[i] - l[i]
        else:
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

    don = p["donchian"]
    hh = [None] * n
    for i in range(don, n):
        hh[i] = max(h[i - don:i])          # TIDAK termasuk bar i

    return dict(
        o=o, h=h, l=l, c=c, v=v,
        ma_c=sma(c, p["ma_cepat"]),
        ma_m=sma(c, p["ma_menengah"]),
        ma_l=sma(c, p["ma_lambat"]),
        atr=sma(tr, p["atr_periode"]),
        adv=sma([c[i] * v[i] for i in range(n)], 20),
        hh=hh,
        don=don,
    )


def siap(ind, i, p=P):
    """True bila semua indikator yang dibutuhkan sudah terisi di bar i."""
    if i < max(p["ma_lambat"], p["donchian"], p["atr_periode"]) or i < 1:
        return False
    return (
        ind["ma_l"][i] is not None
        and ind["ma_m"][i] is not None
        and ind["ma_c"][i] is not None
        and ind["atr"][i] is not None
        and ind["adv"][i] is not None
        and ind["hh"][i] is not None
    )


# ---------------------------------------------------------------------- sinyal
def sinyal_masuk(ind, i, p=P):
    """Aturan masuk, dievaluasi pada CLOSE bar i (bar sudah tutup).

    Tren   : MA20 > MA50 > MA200
    Breakout: close > high tertinggi 20 bar sebelumnya
    Likuid : ADV20 (nilai transaksi rupiah) >= Rp2 miliar
    """
    if not siap(ind, i, p):
        return False
    return (
        ind["adv"][i] >= p["adv_min"]
        and ind["ma_c"][i] > ind["ma_m"][i] > ind["ma_l"][i]
        and ind["c"][i] > ind["hh"][i]
    )


def stop_awal(harga_masuk, atr, p=P):
    return harga_masuk - p["atr_mult"] * atr


def update_trailing(stop, close, atr, p=P):
    """Trailing stop hanya boleh naik."""
    return max(stop, close - p["atr_mult"] * atr)


def alasan_keluar(ind, i, stop, p=P):
    """Keputusan keluar pada CLOSE bar i. Stop dievaluasi intra-bar pada bot
    dengan harga bid saat tick; di backtest memakai close (konservatif & sama
    dengan cara bot memeriksa posisi: sekali per candle)."""
    if stop is not None and ind["c"][i] <= stop:
        return "stop"
    if ind["ma_c"][i] is not None and ind["ma_m"][i] is not None and ind["ma_c"][i] < ind["ma_m"][i]:
        return "ma20<ma50"
    return None


# ---------------------------------------------------------------------- sizing
def bulat_step(q, step):
    if step <= 0:
        return q
    return round(math.floor(q / step + 1e-9) * step, 10)


def ukuran_posisi(equity, cash, harga, atr, adv, step, p=P):
    """Risiko 2% ekuitas / (3 x ATR), dibatasi 10% ekuitas, 5% ADV, dan kas."""
    if harga <= 0 or atr <= 0:
        return 0.0
    q = (p["risiko"] * equity) / (p["atr_mult"] * atr)
    q = min(q, p["maks_alokasi"] * equity / harga)
    if adv:
        q = min(q, p["adv_persen"] * adv / harga)
    q = bulat_step(q, step)
    # batas kas: harga beli + fee
    q = min(q, bulat_step(cash / (harga * (1 + p["fee_beli"])), step))
    if q * harga < p["min_order"]:
        return 0.0
    return q


# ------------------------------------------------------------------ portofolio
class Portofolio:
    """Mesin eksekusi yang sama untuk backtest dan bot.

    Harga fill selalu harga yang benar-benar dibayar/diterima:
      - beli di ask / harga open bar berikutnya + half-spread + slippage
      - jual di bid / close + half-spread
    """

    def __init__(self, cash=None, p=P):
        self.p = p
        self.cash = float(p["modal"] if cash is None else cash)
        self.cash_awal = self.cash
        self.posisi = {}        # pair -> dict
        self.trade = []         # riwayat round-trip (ditutup)
        self.order = []         # setiap order simulasi (beli & jual)
        self.hari = None
        self.ekuitas_awal_hari = self.cash
        self.beku = False       # daily loss limit aktif
        self.alasan_beku = None

    # -- penilaian -----------------------------------------------------------
    def nilai(self, bid_map):
        mv = 0.0
        for pair, pos in self.posisi.items():
            harga = bid_map.get(pair)
            if harga is None:
                harga = pos["harga_terakhir"]
            mv += pos["qty"] * harga
        return self.cash + mv

    # -- beli ----------------------------------------------------------------
    def beli(self, pair, qty, harga_fill, bar_time, waktu, sinyal_harga, atr, alasan="breakout", fee=None):
        p = self.p
        fee_rate = p["fee_beli"] if fee is None else fee
        notional = qty * harga_fill
        biaya = notional * fee_rate
        if qty <= 0 or biaya + notional > self.cash + 1e-6:
            return None
        self.cash -= notional + biaya
        pos = dict(pair=pair, qty=qty, harga_masuk=harga_fill, fee_masuk=biaya,
                   stop=stop_awal(harga_fill, atr, p), rp=False,
                   target_rp=harga_fill + p["rp_mult"] * (p["atr_mult"] * atr),
                   bar_masuk=bar_time, harga_terakhir=harga_fill, sinyal_harga=sinyal_harga)
        self.posisi[pair] = pos
        rec = dict(pair=pair, sisi="buy", bar_time=bar_time, waktu=waktu, qty=qty,
                   harga_fill=harga_fill, fee=biaya, harga_sinyal=sinyal_harga,
                   slippage_bps=((harga_fill / sinyal_harga - 1) * 1e4) if sinyal_harga else 0.0,
                   notional=notional, alasan=alasan)
        self.order.append(rec)
        return pos

    # -- jual ----------------------------------------------------------------
    def jual(self, pair, harga_fill, bar_time, waktu, alasan, fee=None, parsial=None):
        """Jual penuh (posisi dihapus) atau parsial (parsial=0<p<1)."""
        p = self.p
        pos = self.posisi.get(pair)
        if not pos:
            return None
        fee_rate = p["fee_jual"] if fee is None else fee
        porsi = 1.0 if parsial is None else parsial
        qty_keluar = pos["qty"] * porsi if porsi < 1 else pos["qty"]
        fee_masuk_bagian = pos["fee_masuk"] * porsi
        notional = qty_keluar * harga_fill
        biaya = notional * fee_rate
        self.cash += notional - biaya
        pnl = notional - biaya - qty_keluar * pos["harga_masuk"] - fee_masuk_bagian
        rec = dict(pair=pair, sisi="sell", bar_time=bar_time, waktu=waktu, qty=qty_keluar,
                   harga_fill=harga_fill, fee=biaya, harga_sinyal=harga_fill,
                   slippage_bps=0.0, notional=notional, alasan=alasan,
                   pnl=pnl, pnl_pct=pnl / (qty_keluar * pos["harga_masuk"]),
                   entry=pos["harga_masuk"],
                   bar_masuk=pos["bar_masuk"], fee_masuk=fee_masuk_bagian)
        self.trade.append(rec)          # setiap penjualan = 1 trade tercatat
        if porsi < 1:
            pos["qty"] -= qty_keluar
            pos["fee_masuk"] -= fee_masuk_bagian
            pos["rp"] = True
            self.order.append(rec)
            return rec
        del self.posisi[pair]
        return rec

    # -- daily loss limit ----------------------------------------------------
    def cek_hari(self, hari, equity, p=None):
        """Dipanggil pada tick pertama setelah 00.00 WIB."""
        p = p or self.p
        if self.hari != hari:
            self.hari = hari
            self.ekuitas_awal_hari = equity
            self.beku = False
            self.alasan_beku = None
        elif equity <= self.ekuitas_awal_hari * (1 - p["daily_loss"]):
            self.beku = True
            self.alasan_beku = "daily_loss_limit"
        return self.beku
