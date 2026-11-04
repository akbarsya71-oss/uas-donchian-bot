#!/usr/bin/env python3
"""
/api/tick — satu-satunya endpoint bot dry run.

Dipanggil penjadwal (GitHub Actions / cron luar) setiap 4 jam, 7 menit setelah
candle 4 jam tutup. Satu panggilan:
  baca state -> ambil data live -> validasi -> hitung sinyal (fungsi sama
  dengan backtest) -> eksekusi simulasi di bid/ask -> simpan state & log.

Tidak memakai API key Indodax. Tidak ada uang sungguhan.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indodax  # noqa: E402
import strategy as st  # noqa: E402
from store import Store, get_store, hari_wib, ts_wib  # noqa: E402

TF_MS = st.P["tf_ms"]
MAKS_SPREAD = 0.02          # tolak order book dengan spread > 2%
MIN_BARS = 210


# ------------------------------------------------------------------ util
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_awal() -> dict:
    now = iso_now()
    return dict(
        versi=1, dibuat=now, diubah=now, last_bar_time=0,
        cash=float(st.P["modal"]), posisi={}, hari=None, ekuitas_awal_hari=float(st.P["modal"]),
        beku=False, ekuitas_terakhir=float(st.P["modal"]), jumlah_tick=0,
        cache_pair=None, cache_pair_ts=0, equity_hari=None, trade_terakhir=None,
        error_beruntun=0, mulai_forward=None,
    )


def log(store: Store, key: str, rec: dict) -> None:
    store.append_json(key, rec)


def validasi_pair(pair: str, bars: list[dict], bar_s: int, now_ms: int, book: dict | None):
    """Mengembalikan (ok, alasan). Aturan wajib dari soal."""
    if len(bars) < MIN_BARS:
        return False, f"bar kurang ({len(bars)} < {MIN_BARS})"
    if bars[-1]["time"] != bar_s:
        umur = (now_ms // 1000) - bars[-1]["time"]
        return False, f"candle tertutup terakhir {bars[-1]['time']} bukan {bar_s} (umur {umur}s)"
    if bars[-1]["time"] + TF_MS // 1000 > now_ms // 1000:
        return False, "bar terakhir belum tutup"
    # celah candle pada jendela indikator
    for a, b in zip(bars[-MIN_BARS:-1], bars[-MIN_BARS + 1:]):
        if b["time"] - a["time"] != TF_MS // 1000:
            return False, f"candle hilang di {a['time']} -> {b['time']}"
    for b in bars[-MIN_BARS:]:
        if b["open"] <= 0 or b["high"] <= 0 or b["low"] <= 0 or b["close"] <= 0 or b["volume"] <= 0:
            return False, f"harga/volume nol di bar {b['time']}"
    if not book or not book.get("buy") or not book.get("sell"):
        return False, "order book kosong"
    bid, ask = book["buy"][0][0], book["sell"][0][0]
    if ask <= bid or bid <= 0:
        return False, f"bid/ask tidak wajar ({bid}/{ask})"
    if indodax.spread_bps(bid, ask) > MAKS_SPREAD * 1e4:
        return False, f"spread {indodax.spread_bps(bid, ask):.0f} bps > 200 bps"
    return True, ""


def snapshot(pair: str, book: dict, ts_ms: int, notional=10_000_000) -> dict:
    bid, ask = book["buy"][0][0], book["sell"][0][0]
    _, avg_b = indodax.isi_order(book["sell"], notional / ask, "buy")
    _, avg_s = indodax.isi_order(book["buy"], notional / bid, "sell")
    return dict(ts=ts_ms, waktu=iso_now(), pair=pair, bid=bid, ask=ask, mid=(bid + ask) / 2,
                spread_bps=round(indodax.spread_bps(bid, ask), 3),
                bid_qty=book["buy"][0][1], ask_qty=book["sell"][0][1],
                notional_uji=notional,
                slippage_beli_bps=round((avg_b / ask - 1) * 1e4, 3) if avg_b else None,
                slippage_jual_bps=round((1 - avg_s / bid) * 1e4, 3) if avg_s else None,
                level_bid=len(book["buy"]), level_ask=len(book["sell"]))


# ------------------------------------------------------------------ inti
def jalankan_tick(store: Store, triger: str = "cron") -> dict:
    mulai = time.time()
    now_ms = indodax.server_time_ms()
    bar_time = (now_ms // TF_MS) * TF_MS - TF_MS
    day = hari_wib(now_ms)
    call = dict(ts=now_ms, waktu=ts_wib(now_ms), trigger=triger, bar_time=bar_time,
                bar_wib=ts_wib(bar_time), waktu_panggilan=iso_now(), sukses=False)
    state = store.get_json("bot:state") or state_awal()
    if not state.get("mulai_forward"):
        state["mulai_forward"] = iso_now()

    # ---------------- kill switch (env atau flag di storage, tanpa deploy ulang)
    kill_env = os.environ.get("KILL_SWITCH", "").lower() in ("1", "true", "yes", "on")
    kill_store = bool(store.get_json("bot:kill", {}).get("aktif"))
    if kill_env or kill_store:
        call.update(sukses=True, catatan="kill switch aktif: tidak ada order baru",
                    kill_switch=True, dihentikan=True)
        log(store, "bot:calls", call)
        log(store, "bot:error", dict(ts=now_ms, waktu=ts_wib(now_ms), jenis="kill_switch",
                                     pesan="tick dipanggil saat kill switch aktif"))
        store.set_json("bot:state", state)
        return call

    # ---------------- idempotency: satu candle hanya diproses sekali
    if state["last_bar_time"] >= bar_time:
        call.update(sukses=True, idempoten=True,
                    catatan=f"candle {bar_time} sudah diproses (last_bar_time={state['last_bar_time']})")
        call["durasi_ms"] = int((time.time() - mulai) * 1000)
        log(store, "bot:calls", call)
        return call

    # ---------------- daftar pair & increment (cache 1 hari)
    if not state.get("cache_pair") or now_ms - state.get("cache_pair_ts", 0) > 86_400_000:
        try:
            daftar = {p["symbol"]: p for p in indodax.pairs()}
            inc = indodax.price_increments()
            state["cache_pair"] = {
                s: dict(trade_min=float(daftar[s].get("trade_min_base_currency", 10_000)),
                        qty_step=float(daftar[s].get("quantity_increment", 1e-8)),
                        harga_step=float(inc.get(s.lower(), 1)),
                        taker=float(daftar[s].get("trade_fee_percent_taker", 0.2)) / 100)
                for s in st.UNIVERSE if s in daftar
            }
            state["cache_pair_ts"] = now_ms
        except Exception as e:  # noqa: BLE001
            call.update(sukses=False, catatan=f"gagal ambil /api/pairs: {e}")
            log(store, "bot:error", dict(ts=now_ms, waktu=ts_wib(now_ms), jenis="api_pairs", pesan=str(e)))
            log(store, "bot:calls", call)
            return call
    info_pair = state["cache_pair"]

    # ---------------- ambil data & validasi
    data, buku, snap, ditolak = {}, {}, [], {}
    for pair in st.UNIVERSE:
        try:
            book = indodax.depth(pair.lower())
            buku[pair] = book
            snap.append(snapshot(pair, book, now_ms))
            bars = indodax.candle_tertutup(pair, st.P["tf"], TF_MS, now_ms, perlu=260)
            ok, alasan = validasi_pair(pair, bars, bar_time // 1000, now_ms, book)
            if ok:
                data[pair] = bars
            else:
                ditolak[pair] = alasan
                log(store, "bot:error", dict(ts=now_ms, waktu=ts_wib(now_ms), jenis="data_tidak_valid",
                                             pair=pair, pesan=alasan))
        except Exception as e:  # noqa: BLE001
            ditolak[pair] = f"error API: {e}"
            log(store, "bot:error", dict(ts=now_ms, waktu=ts_wib(now_ms), jenis="api", pair=pair, pesan=str(e)))

    if not data:
        call.update(sukses=False, catatan="semua pasangan gagal validasi", ditolak=ditolak,
                    durasi_ms=int((time.time() - mulai) * 1000))
        log(store, "bot:calls", call)
        state["error_beruntun"] = state.get("error_beruntun", 0) + 1
        store.set_json("bot:state", state)
        return call

    ind = {p: st.indikator(b, st.P) for p, b in data.items()}
    i_akhir = {p: len(data[p]) - 1 for p in data}

    # ---------------- ekuitas saat ini (posisi di best bid)
    def harga_bid(pair):
        if pair in buku:
            return buku[pair]["buy"][0][0]
        return state["posisi"].get(pair, {}).get("harga_terakhir", 0)

    def ekuitas():
        mv = sum(v["qty"] * harga_bid(p) for p, v in state["posisi"].items())
        return state["cash"] + mv

    ek = ekuitas()

    # ---------------- batas hari WIB & daily loss limit
    if state.get("hari") != day:
        state["hari"] = day
        state["ekuitas_awal_hari"] = ek
        state["beku"] = False
        state["equity_hari"] = day
        log(store, "bot:equity", dict(hari=day, ts=now_ms, waktu=ts_wib(now_ms),
                                      ekuitas=round(ek), cash=round(state["cash"]),
                                      nilai_posisi=round(ek - state["cash"]),
                                      posisi={p: v["qty"] for p, v in state["posisi"].items()}))
    elif ek <= state["ekuitas_awal_hari"] * (1 - st.P["daily_loss"]):
        if not state["beku"]:
            log(store, "bot:error", dict(ts=now_ms, waktu=ts_wib(now_ms), jenis="daily_loss_limit",
                                         pesan=f"ekuitas {ek:,.0f} <= {state['ekuitas_awal_hari'] * (1 - st.P['daily_loss']):,.0f}"))
        state["beku"] = True

    order_baru, sinyal_log = [], []

    # ---------------- 1. KELUAR (pada close bar terakhir, jual di bid)
    for pair in list(state["posisi"].keys()):
        if pair not in data:
            continue
        i = i_akhir[pair]
        pos = state["posisi"][pair]
        pos["harga_terakhir"] = data[pair][i]["close"]
        alasan = st.alasan_keluar(ind[pair], i, pos["stop"], st.P)
        if not alasan:
            pos["stop"] = st.update_trailing(pos["stop"], data[pair][i]["close"], ind[pair]["atr"][i], st.P)
            continue
        book = buku[pair]
        qty = pos["qty"]
        terisi, harga = indodax.isi_order(book["buy"], qty, "sell")
        if not harga:
            log(store, "bot:error", dict(ts=now_ms, waktu=ts_wib(now_ms), jenis="likuiditas",
                                         pair=pair, pesan=f"bid tidak cukup untuk jual {qty}"))
            continue
        notional = terisi * harga
        fee = notional * st.P["fee_jual"]
        state["cash"] += notional - fee
        pnl = notional - fee - qty * pos["harga_masuk"] - pos["fee_masuk"]
        rec = dict(ts=now_ms, waktu=ts_wib(now_ms), pair=pair, sisi="sell", qty=terisi,
                   best_bid=book["buy"][0][0], best_ask=book["sell"][0][0], harga_fill=harga,
                   fee=fee, harga_sinyal=data[pair][i]["close"],
                   slippage_bps=(1 - harga / data[pair][i]["close"]) * 1e4,
                   notional=notional, alasan=alasan, bar_time=bar_time, mode="dry_run",
                   pnl=pnl, entry=pos["harga_masuk"], bar_masuk=pos["bar_masuk"])
        order_baru.append(rec)
        log(store, "bot:trades", rec)
        log(store, "bot:orders", rec)
        state["trade_terakhir"] = rec
        del state["posisi"][pair]

    # ---------------- 2. MASUK (sinyal close bar terakhir, beli di ask)
    ek = ekuitas()
    for pair in st.UNIVERSE:
        if pair not in data or pair in state["posisi"]:
            continue
        i = i_akhir[pair]
        D = data[pair]
        masuk = st.sinyal_masuk(ind[pair], i, st.P)
        sinyal_log.append(dict(ts=now_ms, bar_time=bar_time, pair=pair, close=D[i]["close"],
                               ma20=ind[pair]["ma_c"][i], ma50=ind[pair]["ma_m"][i],
                               ma200=ind[pair]["ma_l"][i], atr=ind[pair]["atr"][i],
                               adv=ind[pair]["adv"][i], hh=ind[pair]["hh"][i],
                               sinyal=masuk, dieksekusi=False))
        if not masuk:
            continue
        if state["beku"]:
            sinyal_log[-1]["alasan_tidak_eksekusi"] = "daily_loss_limit"
            continue
        if len(state["posisi"]) >= st.P["maks_posisi"]:
            sinyal_log[-1]["alasan_tidak_eksekusi"] = "maks_posisi"
            continue
        book = buku[pair]
        ask = book["sell"][0][0]
        step = info_pair.get(pair, {}).get("qty_step", st.QTY_STEP.get(pair, 1e-8))
        qty = st.ukuran_posisi(ek, state["cash"], ask, ind[pair]["atr"][i],
                               ind[pair]["adv"][i], step, st.P)
        if qty <= 0:
            sinyal_log[-1]["alasan_tidak_eksekusi"] = "ukuran 0 / di bawah minimum"
            continue
        terisi, harga = indodax.isi_order(book["sell"], qty, "buy")
        if not harga:
            sinyal_log[-1]["alasan_tidak_eksekusi"] = "likuiditas ask tidak cukup"
            continue
        qty = st.bulat_step(terisi, step)
        if qty * harga < st.P["min_order"]:
            sinyal_log[-1]["alasan_tidak_eksekusi"] = "di bawah minimum order Rp25.000"
            continue
        notional = qty * harga
        fee = notional * st.P["fee_beli"]
        if notional + fee > state["cash"]:
            sinyal_log[-1]["alasan_tidak_eksekusi"] = "kas tidak cukup"
            continue
        state["cash"] -= notional + fee
        state["posisi"][pair] = dict(pair=pair, qty=qty, harga_masuk=harga, fee_masuk=fee,
                                     stop=st.stop_awal(harga, ind[pair]["atr"][i], st.P),
                                     bar_masuk=bar_time, harga_terakhir=harga,
                                     sinyal_harga=D[i]["close"])
        rec = dict(ts=now_ms, waktu=ts_wib(now_ms), pair=pair, sisi="buy", qty=qty,
                   best_bid=book["buy"][0][0], best_ask=book["sell"][0][0], harga_fill=harga,
                   fee=fee, harga_sinyal=D[i]["close"],
                   slippage_bps=(harga / D[i]["close"] - 1) * 1e4,
                   notional=notional, alasan="breakout", bar_time=bar_time, mode="dry_run")
        order_baru.append(rec)
        log(store, "bot:orders", rec)
        sinyal_log[-1]["dieksekusi"] = True

    # ---------------- 3. simpan state & log
    state["last_bar_time"] = bar_time
    state["ekuitas_terakhir"] = ekuitas()
    state["jumlah_tick"] = state.get("jumlah_tick", 0) + 1
    state["diubah"] = iso_now()
    state["error_beruntun"] = 0

    store.append_many("bot:signals", sinyal_log)
    store.append_many("bot:snapshots", snap)
    store.set_json("bot:state", state)

    call.update(sukses=True, ekuitas=round(state["ekuitas_terakhir"]), cash=round(state["cash"]),
                posisi=len(state["posisi"]), order=len(order_baru), trade=len(order_baru),
                sinyal=sum(1 for s in sinyal_log if s["sinyal"]), ditolak=ditolak,
                beku=state["beku"], catatan="ok",
                durasi_ms=int((time.time() - mulai) * 1000))
    log(store, "bot:calls", call)
    return call


# ------------------------------------------------------------------ WSGI
def _secret_ok(environ) -> tuple[bool, str]:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return False, "CRON_SECRET belum di-set di environment"
    qs = parse_qs(environ.get("QUERY_STRING", ""))
    diberikan = (qs.get("secret") or [""])[0]
    if not diberikan:
        auth = environ.get("HTTP_AUTHORIZATION", "")
        if auth.lower().startswith("bearer "):
            diberikan = auth[7:]
    if not diberikan:
        diberikan = environ.get("HTTP_X_CRON_SECRET", "")
    return hmac.compare_digest(diberikan, secret), "secret salah"


def app(environ, start_response):
    def balas(data, status="200 OK"):
        body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        start_response(status, [("Content-Type", "application/json; charset=utf-8"),
                                ("Cache-Control", "no-store")])
        return [body]

    if environ.get("REQUEST_METHOD") == "GET" and environ.get("PATH_INFO", "").endswith("/health"):
        return balas({"ok": True, "waktu": iso_now()})

    ok, pesan = _secret_ok(environ)
    if not ok:
        return balas({"ok": False, "error": pesan}, "401 Unauthorized")

    store = get_store()
    try:
        hasil = jalankan_tick(store, triger="cron")
        return balas({"ok": hasil.get("sukses", False), "tick": hasil})
    except Exception as e:  # noqa: BLE001
        try:
            log(store, "bot:error", dict(ts=int(time.time() * 1000), waktu=iso_now(),
                                         jenis="exception", pesan=str(e), trace=traceback.format_exc()[-2000:]))
            call = dict(ts=int(time.time() * 1000), waktu=iso_now(), sukses=False,
                        catatan=f"exception: {e}", trigger="cron")
            log(store, "bot:calls", call)
        except Exception:  # noqa: BLE001
            pass
        return balas({"ok": False, "error": str(e)}, "500 Internal Server Error")


handler = app
