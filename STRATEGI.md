# Strategi Tertulis — Bot Dry Run Indodax

**M Syahrul Akbar · 25110200047 · UAS Algoritma Trading**

> Dokumen ini adalah isi aturan yang **dibekukan**. Salinan yang dieksekusi kode ada di
> `api/strategy.py` (dipakai bersama oleh backtest dan bot). Tanggal & hash commit freeze
> dicatat di `FREEZE.md` setelah commit freeze dibuat.

---

## 1. Ringkasan

| Hal | Nilai |
|---|---|
| Nama | Donchian Breakout + Filter Tren (kripto 4 jam) |
| Pasar | Indodax, pasar IDR, long-only, tanpa short/leverage |
| Timeframe | 4 jam (`tf=240`). Candle tutup 03, 07, 11, 15, 19, 23 WIB |
| Universe | `BTCIDR`, `ETHIDR`, `XRPIDR`, `SOLIDR`, `DOGEIDR` (5 pasangan, dibekukan) |
| Modal virtual | Rp100.000.000 |
| Keputusan | pada **close** bar yang sudah tutup; eksekusi pada tick sesudahnya di **bid/ask live** |
| Data | `history_v2?tf=240` (candle tertutup saja), `/api/depth` (order book), `/api/pairs`, `/api/price_increments`, `/api/server_time` |

## 2. Aturan masuk (semua harus benar pada bar t yang sudah tutup)

1. **Filter tren**: `SMA20 > SMA50 > SMA200` (close).
2. **Breakout Donchian**: `close[t] > max(high[t-20 .. t-1])` — high bar t **tidak** ikut.
3. **Likuiditas**: `ADV20 = SMA20(close × volume)` dihitung dalam rupiah ≥ **Rp50.000.000**.
4. Posisi pada pasangan itu belum ada, jumlah posisi < **8**, dan daily loss limit tidak aktif.
5. Indikator butuh ≥ 200 bar; sinyal tidak dihitung sebelum itu.

Jika beberapa pasangan sinyal pada bar yang sama, prioritas = `close / SMA50` terbesar.

## 3. Ukuran posisi (sizing)

```
R per posisi    = 2% × ekuitas
jarak stop      = 3 × ATR14 (di close bar sinyal)
qty risiko      = R / (3 × ATR)
batas alokasi   = 10% ekuitas / harga fill
batas likuiditas= 5% × ADV20 / harga fill
qty             = min(qty risiko, batas alokasi, batas likuiditas, kas yang tersedia)
qty             = dibulatkan ke bawah sesuai quantity_increment pasangan
tolak bila nilai order < Rp25.000 (minimum Pro Indodax)
```

Ekuitas dinilai pada **best bid saat itu** (nilai jika dijual sekarang). Harga fill beli
adalah harga rata-rata eksekusi di level **ask** order book.

## 4. Aturan keluar (dievaluasi setiap tick, pada close bar terakhir yang tutup)

1. **Stop awal** = harga masuk − 3 × ATR. Jika harga ≤ stop → jual **di bid**.
2. **Trailing stop**: setiap bar, `stop = max(stop, close − 3 × ATR)` (stop hanya naik).
3. **Exit tren**: bila `SMA20 < SMA50` → jual di bid pada close bar itu.
4. Take profit parsial tersedia di kode (`rp_mult`) tetapi **dimatikan** (`rp_mult = 99`)
   karena menurunkan profit factor (uji: PF 1,47 → 1,38 vs 1,82 tanpa RP).

Satu-satunya cara posisi ditutup: stop, trailing stop, atau exit tren.

## 5. Eksekusi & biaya

| Komponen | Nilai | Sumber / bukti |
|---|---|---|
| Fee beli (taker all-in IDR) | **0,20555%** | `/api/pairs` → `trade_fee_percent_taker = 0,2%`; halaman resmi "Rincian Biaya Transaksi di INDODAX" (help.indodax.com/hc/id/articles/4416646599705) → IDR BUY Taker 0,20555% (service 0,20% + pajak 0% + CFX 0,00555%), berlaku efektif 1 Okt 2026. Diambil **yang lebih mahal** (0,20555%). Diakses 4 Nov 2026. |
| Fee jual (taker all-in IDR) | **0,41555%** | halaman resmi yang sama: IDR SELL Taker 0,41555% (0,20% + pajak 0,21% + CFX 0,00555%). Diakses 4 Nov 2026. |
| Minimum order | **Rp25.000** | Soal & artikel resmi: "transaksi langsung di INDODAX Pro minimum Rp25.000" (`trade_min_base_currency` = 10.000 untuk Lite). Diambil yang lebih ketat. |
| Spread backtest | **median per pasangan dari snapshot** | BTC 14.54 · ETH 9.44 · XRP 3.48 · SOL 12.69 · DOGE 14.88 bps (298 snapshot / 61 waktu berbeda, 4 Nov 2026, `data/snapshots.jsonl`) |
| Slippage backtest | **median per pasangan** (ukur Rp10 juta per order) | 0–0,6 bps; order bot ≤ 5% ADV sehingga umumnya hanya level terbaik terpakai |
| Fill beli | harga rata-rata level **ask** (boros level) | rule order book, `api/indodax.py:isi_order` |
| Fill jual | harga rata-rata level **bid** | idem |
| Mid / close sebagai harga fill | **dilarang** | backtest memakai spread+slippage di atas, bot memakai bid/ask live |

## 6. Manajemen risiko

* **Maks posisi** 8, **maks ukuran per posisi** 10% ekuitas (dalam satu pasangan).
* **Daily loss limit 3%**: ekuitas ≤ ekuitas awal hari × 97% → tidak membuka posisi baru
  sampai 00.00 WIB. Hari memakai WIB (UTC+7); equity harian dicatat pada panggilan pertama
  setelah 00.00 WIB.
* **Kill switch** tanpa deploy ulang: env `KILL_SWITCH=1` atau `GET /api/kontrol?aksi=kill&secret=…`
  (flag di storage). Setiap pemakaian tercatat di `bot:error` (`jenis=kill_switch`).
* **Timeout/error/retry**: HTTP timeout 20 dtk, retry 3x dengan jeda berlipat; respons kosong,
  format aneh, atau order book kosong → pasangan dilewati dan dicatat di log.
* **Idempotency**: `state.last_bar_time`; tick kedua untuk candle yang sama tidak memproses
  apa pun (log `idempoten=true`).
* **Validasi data tiap tick** (pasangan ditolak bila): candle tutup terakhir lebih tua dari
  2× timeframe, ada candle hilang pada 210 bar terakhir, ada harga/volume nol, order book
  kosong, spread > 2%, atau bid ≥ ask. Semua alasan dicatat.

## 7. Universe & cara menghindari survivorship bias

Kriteria:
1. Pasangan IDR di Indodax dengan candle 4 jam tersedia **sejak 2023-08-01** (awal backtest)
   — bukan hanya hari ini. Kelima pasangan mengembalikan 7148 bar identik 2023-08-01 s/d
   2026-11-04 (bukti penarikan `data/fetch_log.json`).
2. Likuiditas diukur pada **awal periode backtest** (ADV20 tanggal 1 Sep 2023):
   BTC Rp1,80 M · ETH Rp0,72 M · XRP Rp0,65 M · DOGE Rp0,35 M · SOL Rp0,09 M — kelima
   terbesar pada saat itu di Indodax, semuanya > Rp50 juta.
3. Kelima pasangan adalah aset besar yang sudah diperdagangkan jauh sebelum 2023 (bukan
   proyek yang "menjadi besar" setelah backtest), sehingga daftar universe yang dibekukan
   tidak dipilih dari kinerjanya di masa depan.

Keterbatasan yang diakui: universe 5 pasangan dipilih hari ini dari daftar pasangan yang
ada, jadi tidak sepenuhnya bebas survivorship bias untuk aset yang sudah delisting; kriteria
di atas mengurangi bias dengan mewajibkan data dan likuiditas sejak awal periode.

## 8. Perubahan dari versi UTS (dicatat)

| UTS | UAS (kripto) | Alasan |
|---|---|---|
| Timeframe harian, universe 101 saham Kompas100 | 4 jam, 5 pasangan IDR | forward test 10 hari perlu cukup trade (soal: 4 jam disarankan) |
| ADV20 ≥ Rp2 miliar | ADV20 ≥ Rp50 juta | skala likuiditas pasar kripto berbeda |
| Maks alokasi 20% ekuitas/posisi | 10% | volatilitas kripto; DD backtest turun dari ~30% ke 17% |
| 8 posisi | 8 posisi (dipertahankan) | — |
| Fee beli 0,15% / jual 0,25%, tick saham 1/2/5/10/25 | fee taker IDR 0,20555% / 0,41555%, spread + slippage dari snapshot order book | Indodax memakai fee all-in & order book |
| Sinyal close t → beli di open t+1 + 1 tick | sinyal close bar t → beli di ask live pada tick 4 jam berikutnya | eksekusi dry run memakai harga nyata |
| Tanpa exit tren tambahan | sama: trailing 3×ATR + exit SMA20<SMA50 | — |
| Tanpa daily loss limit | daily loss limit 3% WIB | wajib di soal UAS |
| Take profit parsial tidak ada | ditambahkan di kode, **dimatikan** (`rp_mult=99`) | menurunkan PF pada uji historis |

## 9. Hasil backtest (2023-08-01 → 2026-11-04, 7148 bar 4 jam)

| Metrik | Strategi | Benchmark BTC buy & hold |
|---|---|---|
| Return total | **+67.4%** | +220.5% |
| CAGR | **17.1%** | — |
| Max drawdown | **−17.3%** | −49.4% |
| Sharpe / Sortino | 1.23 / 1.73 | — |
| Jumlah trade | 187 (win 35.3%) | — |
| Profit factor | **1.82** | — |
| Rata-rata untung / rugi | Rp2.27 jt / −Rp0.68 jt | — |
| Porsi biaya / laba kotor | 3.9% | — |

Validasi (`hasil_backtest.json` → `validasi`):

| Uji | Hasil |
|---|---|
| In-sample / out-of-sample (70/30) | PF IS 2.40 vs OOS 0.44 (rasio 0.18, 49 trade OOS) → **tidak lulus** (OOS 2025-11 s/d 2026-11 lemah) |
| Walk-forward 5 jendela | 3/5 jendela profit → lulus |
| Sensitivitas 4×4 parameter | 16/16 variasi CAGR>0 & PF>1 → lulus |
| Stress delay 1 bar | CAGR 16.93%, PF 1.85 → lulus |
| Stress biaya 2× | CAGR 13.00%, PF 1.56 → lulus |
| Uji look-ahead (truncation) | 3/3 identik → lulus |
| Buang 2 pasangan terbaik (XRPIDR, SOLIDR) | CAGR 5.5%, PF 1.51 → lulus (profit ditopang lebih dari dua aset) |

Kesimpulan jujur: strategi **tidak** mengalahkan buy & hold BTC pada periode ini dan
kinerjanya menurun di tahun terakhir (OOS). Keunggulannya adalah drawdown jauh lebih kecil
(−17% vs −49%). Kinerja yang lemah di OOS dicatat sebagai risiko yang harus terlihat di
forward test.

## 10. Jendela forward test & backtest pembanding

* Mulai: tick pertama setelah commit freeze (4 Nov 2026, menjelang 12.00 WIB).
* Selesai: **14 Nov 2026 12.00 WIB**.
* `backtest_forward.json` = backtest dengan aturan yang sama pada candle jendela tersebut,
  dipakai untuk membandingkan sinyal candle demi candle.
