#!/usr/bin/env python3
"""
Backtest portofolio multi-pasangan, memakai mesin aturan yang sama dengan bot
(api/strategy.py).

  python backtest.py                       # 3+ tahun penuh + validasi
  python backtest.py --mulai 2026-10-25 --selesai 2026-11-04 --tanpa-validasi
  python backtest.py --out hasil_10hari.json --mulai ... --selesai ...

Asumsi biaya (diukur, bukan dikira-kira):
  - fee taker all-in IDR: beli 0,20555%, jual 0,41555%  (help.indodax.com, 4 Nov 2026)
  - spread: median spread order book per pasangan (data/snapshots.jsonl)
  - slippage: 2 bps per sisi (konservatif; order <= 5% ADV)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))
import strategy as st  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SLIP_BPS = 2.0


# ------------------------------------------------------------------ pemuatan
def muat(pairs, path):
    per = {s: [] for s in pairs}
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            s = r["Ticker"]
            if s not in per:
                continue
            per[s].append(dict(time=int(r["Time"]), open=float(r["Open"]), high=float(r["High"]),
                               low=float(r["Low"]), close=float(r["Close"]), volume=float(r["Volume"])))
    for s in per:
        per[s].sort(key=lambda b: b["time"])
    return per


def biaya_terukur(path):
    """Median spread & slippage per pasangan dari snapshot order book nyata."""
    vals = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    v = vals.setdefault(r["pair"], {"spread": [], "slip": []})
                    v["spread"].append(r["spread_bps"])
                    for k in ("slippage_beli_bps", "slippage_jual_bps"):
                        if r.get(k) is not None:
                            v["slip"].append(r[k])
                except Exception:  # noqa: BLE001
                    continue
    spread, slip, sumber = {}, {}, {}
    for s in st.UNIVERSE:
        sp = sorted(vals.get(s, {}).get("spread", []))
        sl = sorted(vals.get(s, {}).get("slip", []))
        spread[s] = sp[len(sp) // 2] if sp else 10.0
        slip[s] = sl[len(sl) // 2] if sl else 2.0
        sumber[s] = dict(n=len(sp), spread_bps=spread[s], slippage_bps=slip[s])
    return spread, slip, sumber


# ------------------------------------------------------------------- mesin
def siapkan(S, p):
    out = {}
    for pair, bars in S.items():
        out[pair] = dict(bars=bars, ind=st.indikator(bars, p),
                         ix={b["time"]: i for i, b in enumerate(bars)})
    return out


def jalankan(D, kalender, p, spread, slip=None, delay=0, biaya_x=1.0, rekam_sinyal=False):
    """Replay satu portofolio pada kalender bar gabungan.

    Urutan per bar (sama dengan urutan bot):
      1. eksekusi order masuk dari sinyal bar sebelumnya (di OPEN bar ini)
      2. kelola posisi pada CLOSE bar ini (stop / trailing / MA20<MA50)
      3. hitung sinyal CLOSE bar ini -> order untuk bar berikutnya
      4. nilai ekuitas pada bid
    """
    pf = st.Portofolio(p["modal"], p)
    eq = [(kalender[0], p["modal"])]
    pending = []
    sinyal_log = []
    hari_ini = None

    for ts in kalender:
        # ---------------- 1. eksekusi masuk
        sisa = []
        for g in pending:
            if ts <= g["bar_time"]:
                sisa.append(g)
                continue
            if g["umur"] < delay:
                g["umur"] += 1
                sisa.append(g)
                continue
            # order tidak bersifat forever: berlaku 2 bar sejak sinyal, lalu batal
            if (ts - g["bar_time"]) / 86400000 > 2 * 4 / 24 - 1e-9:
                continue
            pair = g["pair"]
            Dp = D.get(pair)
            if not Dp or ts not in Dp["ix"]:
                sisa.append(g)
                continue
            if pair in pf.posisi or len(pf.posisi) >= p["maks_posisi"] or pf.beku:
                continue          # order dibatalkan, sama seperti bot
            i = Dp["ix"][ts]
            bar = Dp["bars"][i]
            half = spread[pair] / 2 / 1e4
            sl = (slip or {}).get(pair, SLIP_BPS) / 1e4
            fill = bar["open"] * (1 + half + sl)
            equity = pf.nilai({k: v["harga_terakhir"] for k, v in pf.posisi.items()})
            q = st.ukuran_posisi(equity, pf.cash, fill, g["atr"], Dp["ind"]["adv"][i - 1],
                                 g.get("step", 1e-8), p)
            if q > 0:
                tr = pf.beli(pair, q, fill, ts, ts, bar["open"], g["atr"],
                             alasan="breakout", fee=p["fee_beli"] * biaya_x)
                if tr:
                    tr["biaya_spread"] = q * (fill - bar["open"])
                    tr["bar_sinyal"] = g["bar_time"]
        pending = sisa

        # ---------------- 2. kelola posisi
        for pair in list(pf.posisi.keys()):
            Dp = D.get(pair)
            if not Dp or ts not in Dp["ix"]:
                continue
            i = Dp["ix"][ts]
            ind, bar = Dp["ind"], Dp["bars"][i]
            pos = pf.posisi[pair]
            pos["harga_terakhir"] = bar["close"]
            half = spread[pair] / 2 / 1e4
            sl = (slip or {}).get(pair, SLIP_BPS) / 1e4
            if (not pos["rp"]) and bar["high"] >= pos["target_rp"]:
                # take profit parsial: jual 50% di harga target (bid = target - half spread - slippage)
                fill_rp = pos["target_rp"] * (1 - half - sl)
                rec_rp = pf.jual(pair, fill_rp, ts, ts, "take_profit_2R",
                                 fee=p["fee_jual"] * biaya_x, parsial=p["rp_porsi"])
                if rec_rp:
                    rec_rp["biaya_spread"] = rec_rp["qty"] * (pos["target_rp"] - fill_rp)
                pos["stop"] = max(pos["stop"], pos["harga_masuk"])
            alasan = st.alasan_keluar(ind, i, pos["stop"], p)
            if alasan:
                half = spread[pair] / 2 / 1e4
                sl = (slip or {}).get(pair, SLIP_BPS) / 1e4
                fill = bar["close"] * (1 - half - sl)
                rec = pf.jual(pair, fill, ts, ts, alasan, fee=p["fee_jual"] * biaya_x)
                if rec:
                    rec["biaya_spread"] = rec["qty"] * (bar["close"] - fill)
            else:
                pos["stop"] = st.update_trailing(pos["stop"], bar["close"], ind["atr"][i], p)

        # ---------------- 3. sinyal
        for pair, Dp in D.items():
            if ts not in Dp["ix"]:
                continue
            i = Dp["ix"][ts]
            ind, bar = Dp["ind"], Dp["bars"][i]
            masuk = st.sinyal_masuk(ind, i, p)
            if rekam_sinyal and st.siap(ind, i, p):
                sinyal_log.append(dict(bar_time=ts, pair=pair, close=bar["close"],
                                       ma20=ind["ma_c"][i], ma50=ind["ma_m"][i], ma200=ind["ma_l"][i],
                                       atr=ind["atr"][i], adv=ind["adv"][i], hh=ind["hh"][i],
                                       sinyal=masuk))
            if masuk:
                pending.append(dict(pair=pair, atr=ind["atr"][i], bar_time=ts, umur=0,
                                    prioritas=bar["close"] / ind["ma_m"][i],
                                    step=st.QTY_STEP.get(pair, 1e-8)))
        pending.sort(key=lambda g: -g["prioritas"])

        # ---------------- 4. ekuitas (posisi dinilai di bid)
        bid = {}
        for pair, pos in pf.posisi.items():
            Dp = D.get(pair)
            if Dp and ts in Dp["ix"]:
                bid[pair] = Dp["bars"][Dp["ix"][ts]]["close"] * (1 - spread[pair] / 2 / 1e4)
        equity_now = pf.nilai(bid)

        h = (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d")
        if hari_ini != h:
            hari_ini = h
            pf.hari = h
            pf.ekuitas_awal_hari = equity_now
            pf.beku = False
        elif equity_now <= pf.ekuitas_awal_hari * (1 - p["daily_loss"]):
            pf.beku = True

        eq.append((ts, equity_now))

    return pf, eq, (sinyal_log if rekam_sinyal else None)


# ------------------------------------------------------------------- metrik
def metrik(trades, eq, modal, label="strategi", order=None):
    """trades: round-trip penutup posisi; order: semua order (untuk biaya)."""
    order = trades if order is None else order
    if not eq:
        # metrik subset trade (IS/OOS) tanpa kurva ekuitas
        wins0 = [t for t in trades if t["pnl"] > 0]
        loss0 = [t for t in trades if t["pnl"] <= 0]
        sw0 = sum(t["pnl"] for t in wins0)
        sl0 = -sum(t["pnl"] for t in loss0)
        return dict(label=label, n=len(trades), win_rate=len(wins0) / len(trades) if trades else 0.0,
                    pf=(sw0 / sl0) if sl0 else (99.0 if sw0 else 0.0),
                    avg_win=sw0 / len(wins0) if wins0 else 0.0,
                    avg_loss=(-sl0 / len(loss0)) if loss0 else 0.0,
                    total_pnl=sum(t["pnl"] for t in trades), return_total=0.0, cagr=0.0,
                    max_dd=0.0, sharpe=0.0, sortino=0.0, calmar=0.0, biaya_total=0.0,
                    porsi_biaya=0.0, ekuitas_akhir=modal, mulai=None, selesai=None)
    wins = [t for t in trades if t["pnl"] > 0]
    loss = [t for t in trades if t["pnl"] <= 0]
    sw = sum(t["pnl"] for t in wins)
    sl = -sum(t["pnl"] for t in loss)
    r = [eq[i][1] / eq[i - 1][1] - 1 for i in range(1, len(eq)) if eq[i - 1][1]]
    m = sum(r) / len(r) if r else 0.0
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) if len(r) > 2 else 0.0
    dn = math.sqrt(sum(min(x, 0) ** 2 for x in r) / len(r)) if r else 0.0
    puncak = dd = 0.0
    for _, v in eq:
        puncak = max(puncak, v)
        dd = min(dd, v / puncak - 1 if puncak else 0.0)
    hari = max((eq[-1][0] - eq[0][0]) / 86400, 1)
    cagr = (eq[-1][1] / eq[0][1]) ** (365.25 / hari) - 1 if eq[0][1] > 0 else 0.0
    biaya = sum(t.get("fee", 0) for t in order) + sum(t.get("biaya_spread", 0) for t in order)
    return dict(
        label=label, n=len(trades),
        win_rate=len(wins) / len(trades) if trades else 0.0,
        pf=(sw / sl) if sl else (99.0 if sw else 0.0),
        avg_win=sw / len(wins) if wins else 0.0,
        avg_loss=(-sl / len(loss)) if loss else 0.0,
        total_pnl=sum(t["pnl"] for t in trades),
        return_total=eq[-1][1] / eq[0][1] - 1 if eq else 0.0,
        cagr=cagr, max_dd=dd,
        sharpe=(m / sd * math.sqrt(365.25 * 6)) if sd else 0.0,
        sortino=(m / dn * math.sqrt(365.25 * 6)) if dn else 0.0,
        calmar=(cagr / -dd) if dd else 0.0,
        biaya_total=biaya,
        porsi_biaya=(biaya / sw) if sw else 0.0,
        ekuitas_akhir=eq[-1][1] if eq else modal,
        mulai=eq[0][0] if eq else None, selesai=eq[-1][0] if eq else None,
    )


def dd_episode(eq, top=5):
    if len(eq) < 2:
        return []
    epis, puncak, t_puncak, dasar, t_dasar, in_dd = [], eq[0][1], eq[0][0], eq[0][1], eq[0][0], False
    for t, v in eq:
        if v >= puncak:
            if in_dd and dasar / puncak - 1 < -0.001:
                epis.append(dict(puncak=t_puncak, dasar=t_dasar, pulih=t, kedalaman=dasar / puncak - 1))
            puncak, t_puncak, dasar, t_dasar, in_dd = v, t, v, t, False
        else:
            in_dd = True
            if v < dasar:
                dasar, t_dasar = v, t
    if in_dd and dasar / puncak - 1 < -0.001:
        epis.append(dict(puncak=t_puncak, dasar=t_dasar, pulih=None, kedalaman=dasar / puncak - 1))
    return sorted(epis, key=lambda e: e["kedalaman"])[:top]


def eq_harian(eq):
    out, hari = [], None
    for ts, v in eq:
        h = (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d")
        if hari != h:
            out.append([h, ts, round(v)])
            hari = h
        else:
            out[-1] = [h, ts, round(v)]
    return out


def benchmark_btc(S, awal, akhir):
    bars = [b for b in S["BTCIDR"] if awal <= b["time"] <= akhir]
    if len(bars) < 2:
        return dict(return_total=0.0, max_dd=0.0)
    eq, puncak, dd = [(bars[0]["time"], 1.0)], 0.0, 0.0
    for b in bars:
        v = b["close"] / bars[0]["close"]
        puncak = max(puncak, v)
        dd = min(dd, v / puncak - 1)
        eq.append((b["time"], v))
    return dict(return_total=bars[-1]["close"] / bars[0]["close"] - 1, max_dd=dd,
                eq=[[t, round(v, 6)] for t, v in eq])


def potong_kalender(cal, mulai, selesai):
    if not cal:
        return cal
    a = int(datetime.strptime(mulai, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if mulai else cal[0]
    z = int(datetime.strptime(selesai, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86399 if selesai else cal[-1]
    return [t for t in cal if a <= t <= z]


# ------------------------------------------------------------------ validasi
def hitung_validasi(S, cal, p, spread, slip, hasil, trades, eq):
    K = dict(rasio_pf_oos_min=0.6, wf_profit_min=0.6, sens_profit_min=0.6, trade_oos_min=20)
    n = len(cal)
    cut = cal[int(n * 0.7)]
    is_ = [t for t in trades if t["bar_time"] < cut]
    oos = [t for t in trades if t["bar_time"] >= cut]
    qi = metrik(is_, [], p["modal"])
    qo = metrik(oos, [], p["modal"])
    rasio = qo["pf"] / qi["pf"] if qi["pf"] else 0.0

    W = 5
    wf = []
    for k in range(W):
        a, z = cal[int(n * k / W)], cal[min(n - 1, int(n * (k + 1) / W))]
        t = [x for x in trades if a <= x["bar_time"] < z]
        q = metrik(t, [], p["modal"])
        wf.append(dict(dari=a, sampai=z, trade=len(t), pf=q["pf"], pnl=q["total_pnl"]))
    wf_ok = sum(1 for w in wf if w["pf"] > 1)

    sens = []
    for key, muls in (("donchian", [0.5, 0.8, 1.2, 1.5]), ("atr_mult", [0.5, 0.8, 1.2, 1.5]),
                      ("risiko", [0.5, 0.8, 1.2, 1.5]), ("rp_mult", [0.5, 0.8, 1.2, 1.5])):
        for m in muls:
            val = max(1, math.floor(p[key] * m + 0.5)) if key == "donchian" else round(p[key] * m, 4)
            pp = dict(p, **{key: val})
            if key == "donchian":
                DD = {}
                for s, v in S.items():
                    bars = v["bars"]
                    hh = [None if i < val else max(b["high"] for b in bars[i - val:i]) for i in range(len(bars))]
                    DD[s] = dict(bars=bars, ind=dict(v["ind"], hh=hh), ix=v["ix"])
            else:
                DD = S
            pf2, eq2, _ = jalankan(DD, cal, pp, spread, slip)
            q = metrik(pf2.trade, eq2, p["modal"], order=pf2.order)
            sens.append(dict(parameter=key, pengali=m, nilai=val, cagr=q["cagr"], pf=q["pf"], trade=q["n"]))
    s_ok = sum(1 for x in sens if x["cagr"] > 0 and x["pf"] > 1)

    pf_d, eq_d, _ = jalankan(S, cal, p, spread, slip, delay=1)
    pf_c, eq_c, _ = jalankan(S, cal, p, spread, slip, biaya_x=2.0)
    qd = metrik(pf_d.trade, eq_d, p["modal"], order=pf_d.order)
    qc = metrik(pf_c.trade, eq_c, p["modal"], order=pf_c.order)

    # uji truncation: backtest yang dipotong harus menghasilkan trade awal identik
    bias = []
    for frac in (0.5, 0.7, 0.9):
        c = cal[int(n * frac)]
        S2 = {s: dict(bars=[b for b in v["bars"] if b["time"] <= c]) for s, v in S.items()}
        S2 = {s: v for s, v in siapkan({k: v["bars"] for k, v in S2.items()}, p).items() if len(v["bars"]) > 220}
        pf_t, _, _ = jalankan(S2, [t for t in cal if t <= c], p, spread, slip)
        a = sorted((t["pair"], t["bar_time"], t["sisi"], round(t["qty"], 8), round(t["pnl"]))
                   for t in pf_t.trade)
        b = sorted((t["pair"], t["bar_time"], t["sisi"], round(t["qty"], 8), round(t["pnl"]))
                   for t in trades if t["bar_time"] <= c)
        bias.append(dict(titik=c, trade=len(b), identik=a == b))

    kontribusi = {}
    for t in trades:
        k = kontribusi.setdefault(t["pair"], dict(pair=t["pair"], trade=0, menang=0, pnl=0.0))
        k["trade"] += 1
        k["menang"] += t["pnl"] > 0
        k["pnl"] += t["pnl"]
    kontribusi = sorted(kontribusi.values(), key=lambda x: -x["pnl"])
    top = [x["pair"] for x in kontribusi[:2]]
    S_ex = {s: v for s, v in S.items() if s not in top}
    pf_e, eq_e, _ = jalankan(S_ex, cal, p, spread, slip)
    qe = metrik(pf_e.trade, eq_e, p["modal"], order=pf_e.order)

    return dict(
        is_oos=dict(split=cut, trade_is=len(is_), trade_oos=len(oos), pf_is=qi["pf"], pf_oos=qo["pf"],
                    rasio=rasio, lulus=rasio >= K["rasio_pf_oos_min"] and len(oos) >= K["trade_oos_min"]),
        walk_forward=dict(jendela=wf, jendela_profit=wf_ok, lulus=wf_ok / W >= K["wf_profit_min"]),
        sensitivitas=dict(variasi=sens, variasi_profit=s_ok, lulus=s_ok / len(sens) >= K["sens_profit_min"]),
        stress=dict(delay_1_bar=dict(cagr=qd["cagr"], pf=qd["pf"], lulus=qd["cagr"] > 0 and qd["pf"] > 1),
                    biaya_2x=dict(cagr=qc["cagr"], pf=qc["pf"], lulus=qc["cagr"] > 0 and qc["pf"] > 1)),
        look_ahead_truncation=dict(uji=bias, lulus=all(b["identik"] for b in bias)),
        buang_saham_teratas=dict(pair=top, cagr=qe["cagr"], pf=qe["pf"], max_dd=qe["max_dd"],
                                 trade=qe["n"], lulus=qe["cagr"] > 0 and qe["pf"] > 1),
    )


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(DATA, "candles_4h.csv.gz"))
    ap.add_argument("--out", default=os.path.join(ROOT, "hasil_backtest.json"))
    ap.add_argument("--mulai", default=None)
    ap.add_argument("--selesai", default=None)
    ap.add_argument("--tanpa-validasi", action="store_true")
    ap.add_argument("--rekam-sinyal", action="store_true")
    args = ap.parse_args()

    S = muat(st.UNIVERSE, args.data)
    S = {s: b for s, b in S.items() if len(b) > 250}
    spread, slip, sumber_biaya = biaya_terukur(os.path.join(DATA, "snapshots.jsonl"))
    p = dict(st.P)
    cal = sorted({b["time"] for bars in S.values() for b in bars})
    cal = potong_kalender(cal, args.mulai, args.selesai)
    if len(cal) < 3:
        sys.exit("data pada jendela ini kosong")

    D = siapkan(S, p)
    pf, eq, sinyal = jalankan(D, cal, p, spread, slip, rekam_sinyal=True)
    q = metrik(pf.trade, eq, p["modal"], order=pf.order)
    bm = benchmark_btc(S, cal[0], cal[-1])

    hasil = dict(
        meta=dict(
            strategi="Donchian Breakout + Filter Tren (kripto 4 jam)",
            pemilik=dict(nama="M Syahrul Akbar", nim="25110200047"),
            periode=[datetime.fromtimestamp(cal[0], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                     datetime.fromtimestamp(cal[-1], timezone.utc).strftime("%Y-%m-%d %H:%M")],
            jumlah_bar=len(cal), timeframe="4 jam (240 menit)", universe=st.UNIVERSE,
            modal_awal=p["modal"], parameter={k: p[k] for k in
                ("donchian", "atr_periode", "atr_mult", "rp_mult", "rp_porsi", "ma_cepat", "ma_menengah", "ma_lambat",
                 "risiko", "maks_posisi", "maks_alokasi", "adv_min", "adv_persen", "min_order",
                 "fee_beli", "fee_jual", "daily_loss")},
            asumsi_biaya=dict(fee_beli=p["fee_beli"], fee_jual=p["fee_jual"],
                              spread_median_bps=spread, slippage_median_bps=slip, sampel_biaya=sumber_biaya,
                              sumber_fee="help.indodax.com Rincian Biaya Transaksi (all-in, efektif 1 Okt 2026), akses 4 Nov 2026",
                              sumber_data="https://indodax.com/tradingview/history_v2 (tf=240)"),
        ),
        ringkasan=dict(backtest=q, benchmark_btc=dict(return_total=bm["return_total"], max_dd=bm["max_dd"]),
                       ekuitas_akhir=eq[-1][1]),
        drawdown_terdalam=dd_episode([(t[1], t[2]) for t in eq_harian(eq)]),
        kontribusi_per_pair=[],
        trades=pf.trade,
        order=pf.order,
        ekuitas=eq_harian(eq),
    )
    # hitung kontribusi dengan benar
    kont = {}
    for t in pf.trade:
        k = kont.setdefault(t["pair"], dict(pair=t["pair"], trade=0, menang=0, pnl=0.0))
        k["trade"] += 1
        k["menang"] += t["pnl"] > 0
        k["pnl"] += t["pnl"]
    hasil["kontribusi_per_pair"] = sorted(kont.values(), key=lambda x: -x["pnl"])

    if not args.tanpa_validasi:
        hasil["validasi"] = hitung_validasi(D, cal, p, spread, slip, hasil, pf.trade, eq)

    if sinyal:
        hasil["_sinyal"] = sinyal

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(hasil, f, ensure_ascii=False, indent=1, default=str)

    print(f"Trade {q['n']} | Return {q['return_total']:.1%} | CAGR {q['cagr']:.1%} | "
          f"MaxDD {q['max_dd']:.1%} | PF {q['pf']:.2f} | WR {q['win_rate']:.1%} | "
          f"biaya/laba kotor {q['porsi_biaya']:.1%} -> {args.out}")


if __name__ == "__main__":
    main()
