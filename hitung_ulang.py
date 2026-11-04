#!/usr/bin/env python3
"""
hitung_ulang.py — menghasilkan ULANG semua angka dashboard dari log mentah.

Sumber data (bukan angka hardcode):
  backtest_forward.json   : backtest pada jendela forward test (candle sama)
  hasil_backtest.json     : backtest 3+ tahun
  data/snapshots.jsonl    : snapshot order book (spread terukur)
  forward/raw/*.jsonl     : log mentah hasil ekspor dari storage bot:
                            state, calls, orders, trades, equity, signals,
                            snapshots, errors, kill

  python hitung_ulang.py                       # log mentah lokal di forward/raw
  python hitung_ulang.py --url https://<app>/api/data      # ekspor dulu, lalu hitung
  python hitung_ulang.py --data forward/raw/2026-11-14.json

Menghasilkan:
  dashboard.html   (fail mandiri, bisa dibuka offline)
  angkadashboard.json

Semua angka di dashboard berasal dari data di bawah ini; skrip ini bisa
dijalankan kapan pun dan hasilnya harus identik.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "api"))
import strategy as st  # noqa: E402

TF_MS = st.P["tf_ms"]
DASAR = {"backtest_forward.json", "hasil_backtest.json", "data/snapshots.jsonl"}


def wib(ms) -> str:
    """milidetik epoch -> teks WIB. Angka < 1e12 dianggap detik epoch."""
    if ms is None:
        return ""
    ms = float(ms)
    if ms < 1e12:
        ms *= 1000
    return (datetime.fromtimestamp(ms / 1000, timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------ pembacaan log
def baca_log_mentah(folder: str) -> dict:
    log = {k: [] for k in ("state", "calls", "orders", "trades", "equity", "signals", "snapshots", "errors", "kill")}
    if not os.path.isdir(folder):
        return log
    for nama in os.listdir(folder):
        path = os.path.join(folder, nama)
        if not os.path.isfile(path):
            continue
        if nama.endswith(".jsonl"):
            jenis = nama.split("__")[-1].replace(".jsonl", "")
            if jenis not in log:
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        log[jenis].append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        elif nama.endswith(".json"):
            # berkas ekspor /api/data
            with open(path, encoding="utf-8") as f:
                try:
                    d = json.load(f)
                except json.JSONDecodeError:
                    continue
            if isinstance(d, dict) and "calls" in d:
                for k in log:
                    if k == "state":
                        if isinstance(d.get("state"), dict) and d["state"]:
                            log["state"].append(d["state"])
                    elif isinstance(d.get(k), list):
                        log[k].extend(d[k])
    # normalisasi: API history_v2 memberi Time dalam DETIK; bot memakai milidetik
    for k in ("calls", "orders", "trades", "signals"):
        for r in log[k]:
            for f in ("bar_time", "bar_masuk"):
                v = r.get(f)
                if isinstance(v, (int, float)) and 0 < v < 1e12:
                    r[f] = int(v) * 1000
    for k in log:
        if k in ("calls", "orders", "trades", "equity", "signals", "snapshots", "errors"):
            log[k].sort(key=lambda r: r.get("ts", 0))
    return log


def ambil_url(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "hitung-ulang/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- metrik
def metrik_trade(trades, eq):
    wins = [t for t in trades if t["pnl"] > 0]
    loss = [t for t in trades if t["pnl"] <= 0]
    sw = sum(t["pnl"] for t in wins)
    sl = -sum(t["pnl"] for t in loss)
    out = dict(
        n=len(trades), n_menang=len(wins), n_rugi=len(loss),
        win_rate=len(wins) / len(trades) if trades else 0.0,
        pf=(sw / sl) if sl else (99.0 if sw else 0.0),
        avg_win=sw / len(wins) if wins else 0.0,
        avg_loss=(-sl / len(loss)) if loss else 0.0,
        laba_kotor=sw, rugi_kotor=-sl, total_pnl=sum(t["pnl"] for t in trades),
    )
    if eq:
        v0, v1 = eq[0][1], eq[-1][1]
        out["return_total"] = v1 / v0 - 1 if v0 else 0.0
        puncak, dd = 0.0, 0.0
        for _, v in eq:
            puncak = max(puncak, v)
            dd = min(dd, v / puncak - 1 if puncak else 0.0)
        out["max_dd"] = dd
        out["ekuitas_akhir"] = v1
    return out


def curve_state(state_log):
    # ekuitas berbasis equity harian pada panggilan pertama tiap hari WIB
    return [(int(e["ts"]), float(e["ekuitas"])) for e in state_log]


def biaya_rinci(orders, trades_bt):
    """Komposisi biaya: fee, spread (fill vs harga sinyal), slippage sisa."""
    fee = spread = 0.0
    for o in orders:
        fee += float(o.get("fee") or 0)
        if o.get("harga_sinyal") and o.get("qty"):
            spread += float(o["qty"]) * abs(float(o["harga_fill"]) - float(o["harga_sinyal"])) * 0.5
    return dict(fee=fee, spread_estimasi=spread)


# ------------------------------------------------------------------ inti
def hitung(log: dict) -> dict:
    calls = log["calls"]
    orders = log["orders"]
    trades_ft = log["trades"]
    eq_harian = log["equity"]
    errors = log["errors"]

    state = log["state"][-1] if log["state"] else {}
    modal = float(st.P["modal"])

    # jendela forward test = dari tick pertama sampai tick terakhir yang diproses
    sukses = [c for c in calls if isinstance(c.get("bar_time"), (int, float)) and c.get("sukses")
              and not c.get("idempoten") and not c.get("dihentikan")]
    bar_candle = sorted({int(c["bar_time"]) for c in sukses})
    awal_ts = bar_candle[0] if bar_candle else None
    akhir_ts = bar_candle[-1] if bar_candle else None
    langkah_aman = TF_MS
    status = {}
    for c in calls:
        if "idempot" in c and c.get("idempoten"):
            s = "idempoten"
        elif c.get("dihentikan"):
            s = "kill_switch"
        elif not c.get("sukses"):
            s = "gagal"
        else:
            s = "diproses"
        status[s] = status.get(s, 0) + 1

    # uptime: candle yang diproses sebelum candle berikutnya tutup.
    # Batas = 23.59 WIB 14 Nov 2026 (tenggat pengumpulan); candle setelah batas tidak dihitung.
    uptime, jadwal = 0, {}
    batas = int(datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc).timestamp() * 1000)   # 00.00 WIB 14 Nov
    if len(bar_candle) > 1:
        rata = (bar_candle[-1] - bar_candle[0]) // (len(bar_candle) - 1)
        langkah = rata if abs(rata - TF_MS) <= 60_000 else TF_MS
        candle_cal = list(range(awal_ts, min(batas, bar_candle[-1] + langkah), langkah))
        diproses = set(bar_candle)
        uptime = sum(1 for b in candle_cal if b in diproses) / len(candle_cal) if candle_cal else 0.0
        jadwal = dict(candle_harapan=len(candle_cal), candle_diproses=sum(1 for b in candle_cal if b in diproses),
                      batas_wib="2026-11-14 23:59 WIB", langkah_ms=langkah)

    # ekuitas forward: modal awal + equity harian WIB (dalam jendela) + equity terakhir dari tick
    if awal_ts:
        eq_ft = [(awal_ts - TF_MS, modal)]
        for e in eq_harian:
            if awal_ts - TF_MS < int(e["ts"]) <= batas:
                eq_ft.append((int(e["ts"]), float(e["ekuitas"])))
        akhir_eq = next((int(c["ekuitas"]) for c in reversed(sukses) if c.get("ekuitas") is not None), None)
        if akhir_eq is not None and (len(eq_ft) == 1 or eq_ft[-1][1] != float(akhir_eq)):
            eq_ft.append((akhir_ts + langkah_aman, float(akhir_eq)))
    else:
        eq_ft = [(0, modal)]
    m_ft = metrik_trade(trades_ft, eq_ft)

    # porsi biaya forward
    br = biaya_rinci(orders, trades_ft)
    fee_ft = br["fee"]
    biaya_ft = fee_ft + br["spread_estimasi"]
    m_ft["fee_total"] = fee_ft
    m_ft["biaya_total"] = biaya_ft
    m_ft["porsi_biaya"] = (biaya_ft / m_ft["laba_kotor"]) if m_ft["laba_kotor"] else 0.0

    # benchmark BTC buy & hold pada jendela sama (dari sinyal log: bar_time sama)
    btc = [s for s in log["signals"] if s.get("pair") == "BTCIDR"
           and (awal_ts is None or awal_ts <= s.get("bar_time", 0) <= akhir_ts)]
    btc.sort(key=lambda s: s["bar_time"])
    bench = {}
    if btc:
        close0, close1 = btc[0]["close"], btc[-1]["close"]
        puncak, dd = 0.0, 0.0
        for s in btc:
            v = s["close"] / close0
            puncak = max(puncak, v)
            dd = min(dd, v / puncak - 1)
        bench = dict(pair="BTCIDR", close_awal=close0, close_akhir=close1,
                     return_total=close1 / close0 - 1, max_dd=dd,
                     kurva=[[wib(s["bar_time"]), s["close"] / close0] for s in btc])

    # backtest jendela sama
    bt_jendela = None
    p = os.path.join(ROOT, "backtest_forward.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        bt_jendela = dict(
            modal=d["meta"]["modal_awal"],
            trades=d["trades"],
            biaya=d["meta"]["asumsi_biaya"],
            ekuitas=d["ekuitas"],
        )

    # kecocokan sinyal: log sinyal bot vs sinyal backtest pada bar yang sama
    sinyal_ft = [s for s in log["signals"]]
    bar_ft = sorted({s["bar_time"] for s in sinyal_ft})

    # snapshot: seluruh snapshot (lokal + bot) untuk membandingkan spread
    draft = []

    return dict(
        meta=dict(
            modal=modal, bar_candle=len(bar_candle), awal=awal_ts, akhir=akhir_ts,
            state=state, universe=st.UNIVERSE,
            jumlah_calls=len(calls), status_calls=status, uptime=uptime, jadwal=jadwal,
            jumlah_error=len(errors),
            error_jenis={k: sum(1 for e in errors if e.get("jenis") == k) for k in
                         sorted({e.get("jenis") for e in errors if e.get("jenis")})},
            kill=log["kill"][-1] if log["kill"] else {"aktif": False},
            kill_dari_log=sum(1 for e in errors if e.get("jenis") == "kill_switch"),
        ),
        forward=dict(metrik=m_ft, trades=trades_ft, orders=orders,
                     ekuitas=[(wib(t), round(v)) for t, v in eq_ft],
                     equity_harian=eq_harian, benchmark=bench, sinyal=sinyal_ft, bar=sorted(set(bar_ft))),
        backtest_jendela=bt_jendela,
        calls=calls, errors=errors,
        biaya_forward=br,
    )


# ------------------------------------------------------------------ HTML
def rupiah(x, ringkas=True):
    if x is None:
        return "-"
    if ringkas and abs(x) >= 1e9:
        return f"Rp{x / 1e9:,.2f} M"
    if ringkas and abs(x) >= 1e6:
        return f"Rp{x / 1e6:,.2f} jt"
    return f"Rp{x:,.0f}".replace(",", ".")


def persen(x, d=2):
    return "-" if x is None else f"{x * 100:,.{d}f}%".replace(".", ",")


def bangun_html(a: dict) -> str:
    m = a["forward"]["metrik"]
    bt = a["backtest_jendela"]
    meta = a["meta"]
    bench = a["forward"]["benchmark"]

    btm = None
    if bt:
        btm = metrik_trade(bt["trades"], [(t[1], t[2]) for t in bt["ekuitas"]])
        btm["n"] = len(bt["trades"])

    def baris(label, nilai_bt, nilai_ft, beda=""):
        return f"<tr><td>{label}</td><td>{nilai_bt}</td><td>{nilai_ft}</td><td>{beda}</td></tr>"

    ekuitas_bt = [(aky[1], aky[2]) for aky in (bt["ekuitas"] if bt else [])]
    tabel_banding = ""
    if btm:
        tabel_banding += baris("Return total", persen(btm.get("return_total")), persen(m.get("return_total")),
                               persen((m.get("return_total") or 0) - (btm.get("return_total") or 0)))
        tabel_banding += baris("Maximum drawdown", persen(btm.get("max_dd")), persen(m.get("max_dd")),
                               persen((m.get("max_dd") or 0) - (btm.get("max_dd") or 0)))
        tabel_banding += baris("Jumlah trade", btm["n"], m["n"], m["n"] - btm["n"])
        tabel_banding += baris("Jumlah trade", btm["n"], m["n"])
        tabel_banding += baris("Win rate", persen(btm["win_rate"]), persen(m["win_rate"]),
                               persen((m["win_rate"] or 0) - (btm["win_rate"] or 0)))
        tabel_banding += baris("Profit factor", f"{btm['pf']:.2f}", f"{m['pf']:.2f}",
                               f"{m['pf'] - btm['pf']:+.2f}")
        tabel_banding += baris("Rata-rata untung / trade", rupiah(btm["avg_win"], False), rupiah(m["avg_win"], False),
                               rupiah((m["avg_win"] or 0) - (btm["avg_win"] or 0), False))
        tabel_banding += baris("Rata-rata rugi / trade", rupiah(btm["avg_loss"], False), rupiah(m["avg_loss"], False),
                               rupiah((m["avg_loss"] or 0) - (btm["avg_loss"] or 0), False))
        tabel_banding += baris("Porsi biaya / laba kotor", persen(btm.get("porsi_biaya")),
                               persen(m.get("porsi_biaya")),
                               persen((m.get("porsi_biaya") or 0) - (btm.get("porsi_biaya") or 0)))
        tabel_banding += baris("Biaya total (fee+spread)", rupiah(btm.get("biaya_total"), False),
                               rupiah(m.get("biaya_total"), False), "")
    else:
        tabel_banding = "<tr><td colspan=4>backtest_forward.json belum ada</td></tr>"

    baris_sinyal = ""
    kec = os.path.join(ROOT, "kecocokan_sinyal.json")
    if os.path.exists(kec):
        with open(kec, encoding="utf-8") as f5:
            kc = json.load(f5)
        baris_sinyal = (f"<p><b>Kecocokan sinyal candle demi candle (cek_kecocokan.py):</b> "
                        f"{kc.get('identik')} dari {kc.get('total')} candle/pasangan identik "
                        f"({persen(kc.get('persen'), 1)}), {len(kc.get('beda') or [])} berbeda.</p>")

    trade_rows = ""
    for t in a["forward"]["trades"]:
        trade_rows += (f"<tr><td>{wib(t.get('ts'))}</td><td>{t.get('pair')}</td><td>{t.get('sisi')}</td>"
                       f"<td class=n>{t.get('qty')}</td><td class=n>{t.get('harga_fill'):,.2f}</td>"
                       f"<td class=n>{t.get('harga_sinyal', 0):,.2f}</td><td class=n>{t.get('slippage_bps', 0):+.1f}</td>"
                       f"<td class=n>{rupiah(t.get('fee'), False)}</td><td>{t.get('alasan', '')}</td></tr>")
    if not trade_rows:
        trade_rows = "<tr><td colspan=9>belum ada trade</td></tr>"

    call_rows = ""
    for c in a["calls"][-60:]:
        st_ = ("idempoten" if c.get("idempoten") else "kill switch" if c.get("dihentikan")
               else "diproses" if c.get("sukses") else "gagal")
        call_rows += (f"<tr><td>{c.get('waktu') or wib(c.get('ts'))}</td><td>{wib(c.get('bar_time'))}</td>"
                      f"<td>{st_}</td><td class=n>{c.get('order') if c.get('order') is not None else '-'}</td>"
                      f"<td class=n>{c.get('ekuitas', 0):,.0f}</td><td>{c.get('catatan', '')}</td></tr>")

    err_rows = ""
    for e in a["errors"][-60:]:
        err_rows += (f"<tr><td>{e.get('waktu') or wib(e.get('ts'))}</td><td>{e.get('jenis')}</td>"
                     f"<td>{e.get('pair', '')}</td><td>{e.get('pesan', '')}</td></tr>")
    if not err_rows:
        err_rows = "<tr><td colspan=4>tidak ada error</td></tr>"

    eq_rows = ""
    for e in a["forward"]["equity_harian"]:
        eq_rows += (f"<tr><td>{e.get('hari')}</td><td class=n>{rupiah(e.get('ekuitas'))}</td>"
                    f"<td class=n>{rupiah(e.get('cash'))}</td><td class=n>{rupiah((e.get('nilai_posisi') or 0))}</td></tr>")
    if not eq_rows:
        eq_rows = "<tr><td colspan=4>belum ada equity harian</td></tr>"

    st_ = meta["state"] or {}
    pos = st_.get("posisi") or {}
    pos_rows = "".join(f"<tr><td>{k}</td><td class=n>{v.get('qty')}</td><td class=n>{v.get('harga_masuk'):,.2f}</td>"
                       f"<td class=n>{v.get('stop'):,.2f}</td><td class=n>{v.get('harga_terakhir'):,.2f}</td></tr>"
                       for k, v in pos.items()) or "<tr><td colspan=5>tidak ada posisi terbuka</td></tr>"

    chart_eq = json.dumps(a["forward"]["ekuitas"])
    chart_bt = json.dumps([[wib(t), v] for t, v in ekuitas_bt])
    chart_bench = json.dumps(bench.get("kurva", []))

    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard Bot Dry Run Indodax — UAS Algoritma Trading</title>
<style>
:root{{--bg:#0e1117;--card:#161b22;--line:#283040;--txt:#e6edf3;--dim:#8b949e;--ok:#3fb950;--bad:#f85149;--acc:#58a6ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif}}
header{{padding:24px 32px;border-bottom:1px solid var(--line)}}
h1{{margin:0;font-size:22px}}h2{{font-size:16px;margin:0 0 12px;color:var(--acc)}}
.sub{{color:var(--dim);margin-top:4px}}
main{{padding:24px 32px;max-width:1280px;margin:0 auto}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.kpi .l{{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.kpi .v{{font-size:22px;font-weight:600;margin-top:6px}}
.pos{{color:var(--ok)}}.neg{{color:var(--bad)}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:18px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;font-size:13px;vertical-align:top}}
th{{color:var(--dim);font-weight:500}}.n{{text-align:right;font-variant-numeric:tabular-nums}}
.legend span{{margin-right:14px;color:var(--dim);font-size:12px}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}}
small{{color:var(--dim)}}
canvas{{width:100%;height:280px}}
</style></head><body>
<header>
<h1>Bot Dry Run Indodax — Dashboard</h1>
<div class="sub">M Syahrul Akbar · 25110200047 · UAS Algoritma Trading · Donchian Breakout + Filter Tren (4 jam) ·
universe {", ".join(st.UNIVERSE)} · dibuat dari log mentah: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</div>
</header><main>

<div class="grid">
  <div class="kpi"><div class="l">Forward: return total</div><div class="v {('pos' if (m.get('return_total') or 0) >= 0 else 'neg')}">{persen(m.get('return_total'))}</div><small>{wib(meta['awal'])} → {wib(meta['akhir'])}</small></div>
  <div class="kpi"><div class="l">Forward: max drawdown</div><div class="v neg">{persen(m.get('max_dd'))}</div><small>dari equity harian WIB</small></div>
  <div class="kpi"><div class="l">Ekuitas akhir</div><div class="v">{rupiah(m.get('ekuitas_akhir'))}</div><small>posisi dinilai di best bid</small></div>
  <div class="kpi"><div class="l">Uptime candle</div><div class="v">{persen(meta['uptime'],1)}</div><small>{meta['jadwal'].get('candle_diproses',0)} / {meta['jadwal'].get('candle_harapan',0)} candle</small></div>
  <div class="kpi"><div class="l">Panggilan / error</div><div class="v">{meta['jumlah_calls']} / {meta['jumlah_error']}</div><small>kill switch: {meta['kill_dari_log']}x</small></div>
  <div class="kpi"><div class="l">Benchmark BTC buy &amp; hold</div><div class="v {('pos' if (bench.get('return_total') or 0) >= 0 else 'neg')}">{persen(bench.get('return_total'))}</div><small>max DD {persen(bench.get('max_dd'))}</small></div>
</div>

<section><h2>Backtest vs forward test (jendela yang sama)</h2>
<table><tr><th>Metrik</th><th>Backtest (candle sama)</th><th>Forward test</th><th>Selisih</th></tr>{tabel_banding}</table>
{baris_sinyal}
<small>Backtest jendela forward memakai candle Indodax yang sama, mesin aturan yang sama (api/strategy.py), spread median snapshot + slippage terukur, fee taker 0,20555% / 0,41555%. Fill backtest = open bar + ½ spread + slippage (beli) / close − ½ spread − slippage (jual); fill bot = harga rata-rata level ask/bid order book live.</small></section>

<section><h2>Ekuitas forward test</h2>
<div class="legend"><span><i style="background:#58a6ff"></i>Bot (equity harian WIB)</span><span><i style="background:#8b949e"></i>BTC buy &amp; hold</span><span><i style="background:#3fb950"></i>Backtest jendela sama</span></div>
<canvas id="eq" width="1200" height="280"></canvas></section>

<section><h2>Metrik forward test</h2>
<table>
<tr><th>Trade</th><th>Win rate</th><th>Profit factor</th><th>Rata-rata untung</th><th>Rata-rata rugi</th><th>Porsi biaya / laba kotor</th><th>Fee total</th></tr>
<tr><td class=n>{m['n']}</td><td class=n>{persen(m['win_rate'],1)}</td><td class=n>{m['pf']:.2f}</td>
<td class=n>{rupiah(m['avg_win'],False)}</td><td class=n>{rupiah(m['avg_loss'],False)}</td>
<td class=n>{persen(m.get('porsi_biaya'))}</td><td class=n>{rupiah(m.get('fee_total'),False)}</td></tr></table>
<small>Porsi biaya = (fee + spread eksekusi + slippage) ÷ laba kotor. Lihat rincian di bagian "Biaya".</small></section>

<section><h2>Posisi terbuka saat ini</h2>
<table><tr><th>Pair</th><th>Qty</th><th>Harga masuk</th><th>Stop</th><th>Harga terakhir</th></tr>{pos_rows}</table>
<small>Kas {rupiah(st_.get('cash'))} · ekuitas terakhir {rupiah(st_.get('ekuitas_terakhir'))} · daily loss limit {persen(st.P['daily_loss'],0)} · beku: {st_.get('beku')}</small></section>

<section><h2>Order simulasi forward test</h2>
<table><tr><th>Waktu</th><th>Pair</th><th>Sisi</th><th>Qty</th><th>Harga fill</th><th>Harga sinyal</th><th>Slip (bps)</th><th>Fee</th><th>Alasan</th></tr>{trade_rows}</table></section>

<section><h2>Equity harian (panggilan pertama setelah 00.00 WIB)</h2>
<table><tr><th>Hari</th><th>Ekuitas</th><th>Kas</th><th>Nilai posisi</th></tr>{eq_rows}</table></section>

<section><h2>Panel operasional — panggilan terakhir</h2>
<table><tr><th>Waktu panggil</th><th>Candle</th><th>Status</th><th>Order</th><th>Ekuitas</th><th>Catatan</th></tr>{call_rows}</table>
<small>Ringkasan: {json.dumps(meta['status_calls'])}. Panggilan idempoten = candle yang sama, tidak ada order baru.</small></section>

<section><h2>Error / peristiwa tertolak</h2>
<table><tr><th>Waktu</th><th>Jenis</th><th>Pair</th><th>Pesan</th></tr>{err_rows}</table>
<small>Jenis: {json.dumps(meta['error_jenis'])}</small></section>

<section><h2>Cara mengulang angka ini</h2>
<small>Semua angka di halaman ini dihitung oleh <code>hitung_ulang.py</code> dari log mentah (<code>forward/raw/*.jsonl</code>),
<code>backtest_forward.json</code>, dan <code>data/snapshots.jsonl</code>. Ekspor log terbaru: <code>python ekspor_log.py --url https://&lt;app&gt;/api/data</code>,
lalu <code>python hitung_ulang.py --data forward/raw/&lt;tanggal&gt;.json</code>.</small></section>
</main>
<script>
const EQ={chart_eq}, BT={chart_bt}, BM={chart_bench};
function draw(){{
  const c=document.getElementById('eq'), ctx=c.getContext('2d');
  const W=c.width,H=c.height,P=40;
  const series=[EQ.map(p=>p[1]),BT.map(p=>p[1]),BM.map(p=>p[1]*{float(st.P['modal'])})].filter(s=>s.length>1);
  if(!series.length){{ctx.fillStyle='#8b949e';ctx.fillText('belum ada data ekuitas',P,H/2);return;}}
  const all=series.flat(); const lo=Math.min(...all), hi=Math.max(...all)+1;
  const n=Math.max(...series.map(s=>s.length));
  const X=i=>P+(W-P-10)*i/(n-1), Y=v=>H-P+( -v+lo)*(-(H-2*P)/(hi-lo));
  ctx.clearRect(0,0,W,H); ctx.strokeStyle='#283040'; ctx.fillStyle='#8b949e'; ctx.font='11px system-ui';
  for(let k=0;k<=4;k++){{const v=lo+(hi-lo)*k/4; ctx.beginPath();ctx.moveTo(P,Y(v));ctx.lineTo(W-10,Y(v));ctx.stroke();
    ctx.fillText((v/1e6).toFixed(1)+' jt',4,Y(v)+4);}}
  const line=(s,col)=>{{ if(s.length<2)return; ctx.strokeStyle=col;ctx.lineWidth=2;ctx.beginPath();
    s.forEach((v,i)=>{{i?ctx.lineTo(X(i),Y(v)):ctx.moveTo(X(i),Y(v));}}); ctx.stroke();}};
  if(BT.length)line(BT.map(p=>p[1]),'#3fb950');
  if(BM.length)line(BM.map(p=>p[1]*{float(st.P['modal'])}),'#8b949e');
  line(EQ.map(p=>p[1]),'#58a6ff');
}}
draw();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="URL /api/data di Vercel (diekspor dulu)")
    ap.add_argument("--data", help="berkas JSON hasil ekspor atau folder log mentah")
    ap.add_argument("--raw", default=os.path.join(ROOT, "forward", "raw"))
    ap.add_argument("--out", default=os.path.join(ROOT, "dashboard.html"))
    ap.add_argument("--json-out", default=os.path.join(ROOT, "angkadashboard.json"))
    args = ap.parse_args()

    log = baca_log_mentah(args.raw)
    if args.data and os.path.isdir(args.data):
        log = baca_log_mentah(args.data)
    elif args.data and os.path.isfile(args.data):
        log = baca_log_mentah(os.path.dirname(args.data) or ".")
    if args.url:
        d = ambil_url(args.url)
        os.makedirs(args.raw, exist_ok=True)
        nama = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ") + ".json"
        with open(os.path.join(args.raw, nama), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        log = baca_log_mentah(args.raw)

    a = hitung(log)
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=1, default=str)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(bangun_html(a))
    m = a["forward"]["metrik"]
    print(f"calls {a['meta']['jumlah_calls']} | candle {a['meta']['bar_candle']} | uptime {a['meta']['uptime']:.1%} | "
          f"trade {m['n']} | return {m.get('return_total', 0):.2%} | maxDD {m.get('max_dd', 0):.2%} -> {args.out}")


if __name__ == "__main__":
    main()
