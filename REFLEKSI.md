# Refleksi: Backtest vs Forward Test

> Berkas ini diisi setelah forward test selesai (14 Nov 2026), memakai angka dari
> `dashboard.html` / `angkadashboard.json` yang dihasilkan `hitung_ulang.py` dari log mentah.
> Semua angka di bawah berasal dari `angkadashboard.json` (forward) dan `hasil_backtest.json` (backtest 3 tahun)
> serta `backtest_forward.json` (backtest pada jendela yang sama).

## 1. Ringkasan angka

| Metrik | Backtest (jendela forward) | Forward test | Selisih | Penyebab |
|---|---|---|---|---|
| Return total | 0,00% | 0,00% | 0,00 pp | tidak ada sinyal breakout yang memenuhi syarat pada 13 candle (4 jam) |
| Max drawdown | 0,00% | 0,00% | 0,00 pp | tidak ada posisi terbuka, kas tetap Rp100 jt |
| Jumlah trade | 0 | 0 | 0 | strategi selektif, menunggu breakout + filter tren |
| Win rate | n/a | n/a | — | tidak ada trade |
| Profit factor | n/a | n/a | — | tidak ada trade |
| Rata-rata untung / trade | n/a | n/a | — | tidak ada trade |
| Rata-rata rugi / trade | n/a | n/a | — | tidak ada trade |
| Porsi biaya / laba kotor | n/a | n/a | — | tidak ada order, biaya 0 |
| Benchmark BTC buy & hold | −8,01% | −8,01% | 0,00 pp | sama-sama buy & hold BTC pada jendela 12–14 Nov 2026 |

Catatan: window forward test yang benar-benar berjalan adalah **12 Nov 2026 11:00 WIB → 14 Nov 2026 11:00 WIB**
(13 candle × 4 jam), bukan 10 hari seperti rencana awal, karena deployment baru selesai 12 Nov.
Forward test "pendek" ini tidak cukup untuk menilai strategi; ia hanya membuktikan pipeline berjalan
(idempotency, fill, log, dashboard) tanpa error.

Untuk konteks, backtest 3 tahun (2023-08-01 → 2026-11-04, 7148 bar): Return +67,4%, CAGR 17,1%,
MaxDD −17,3%, PF 1,82, WR 35,3%, 187 trade, porsi biaya/laba kotor 3,9%.

## 2. Kecocokan sinyal candle demi candle

Target 100%: backtest dijalankan ulang pada jendela yang sama dengan candle yang sama
(`backtest_forward.json`) dan memakai fungsi `api/strategy.py` yang sama.

- Candle forward test: **13 candle** (12 Nov 11:00 → 14 Nov 11:00 WIB), diproses **34 panggilan**, uptime **100,0%** (13/13 candle, 0 terlambat/gagal).
- Sinyal bot vs sinyal backtest: **65 dari 65 candle/pasangan identik (100,0%)**, 0 berbeda
  (`kecocokan_sinyal.json`). Backtest pada window yang sama menghasilkan 0 trade juga.
- Perbedaan yang diizinkan: ukuran posisi dan harga fill, karena keduanya bergantung pada
  harga bid/ask nyata saat tick, bukan pada harga close/open candle. Pada window ini tidak ada
  order, jadi tidak ada perbedaan fill yang bisa dibandingkan.

## 3. Sumber selisih harga fill (angka)

| Komponen | Backtest | Forward | Penjelasan |
|---|---|---|---|
| Fill beli | open bar + ½ spread median + slippage median | ask order book live (multi-level) | pada window ini tidak ada order, sehingga tidak ada selisih fill yang terukur |
| Fill jual | close − ½ spread − slippage | bid order book live | idem |
| Fee | 0,20555% / 0,41555% (taker all-in) | tarif yang sama di kode | tidak ada selisih fee |
| Keterlambatan | eksekusi di bar berikutnya | tick 7 menit setelah candle tutup (± keterlambatan GitHub Actions, tercatat di `bot:calls.waktu`) | pada window ini semua 13 tick datang 4–8 menit setelah candle tutup (tercatat), tidak ada yang terlambat > 1 candle |

Median spread yang dipakai backtest per pasangan: BTC 14,54 · ETH 9,44 · XRP 3,48 ·
SOL 12,69 · DOGE 14,88 bps (298 snapshot / 61 waktu berbeda, diambil 4 Nov 2026).
Median spread seluruh snapshot setelah forward test (358 snapshot / 73 waktu berbeda): BTC 9,97 · ETH 9,44 ·
XRP 3,91 · SOL 11,20 · DOGE 14,88 bps — tidak berubah signifikan; spread pasar stabil selama forward test.
Median spread khusus 9 waktu sampling selama forward test (12–14 Nov): BTC 1,34 · ETH 6,15 ·
XRP 6,26 · SOL 7,17 · DOGE 16,30 bps — lebih ketat dari median backtest, tetapi volume sampling kecil (9 waktu).

## 4. Uptime dan dampaknya

- Panggilan terjadwal: 6×/hari × 2 hari (12–14 Nov) = 12 diharapkan; tercatat **34 panggilan** total
  (termasuk panggilan manual untuk uji dan tick tambahan).
- Candle diproses tepat waktu: **13 / 13**, uptime **100,0%**. Semua tick datang 4–8 menit setelah
  candle tutup (menit ke-7 UTC sesuai jadwal), tidak ada yang terlambat lebih dari 1 candle.
- Panggilan terlambat/gagal: **0** (status `diproses` 34, `gagal` 0, `kill_switch` 0, `idempoten` 21).
- Dampak: tidak ada candle yang terlewat, jadi tidak ada sinyal yang hilang dan posisi lama
  (tidak ada) tidak pernah tidak terkelola. Idempotency bekerja: 21 panggilan kedua untuk candle
  yang sama tidak memproses ulang dan tidak menambah order.

## 5. Kejadian selama forward test

- Kill switch dipakai: **0 kali**.
- Daily loss limit aktif: **0 kali** (tidak ada posisi, tidak ada rugi).
- Pasangan ditolak validasi (spread > 2%, candle hilang, dll.): **0 kali** (semua pasangan lolos validasi
  pada setiap candle).
- Error API: **0** (`bot:error` kosong).
- Order simulasi: **0** (tidak ada sinyal breakout yang memenuhi syarat).
- Trade: **0**.

## 6. Pembelajaran

- Pipeline end-to-end terbukti berjalan: tick → validasi → sinyal → (tidak ada order) → log →
  ekspor → dashboard, dengan idempotency, kill switch, dan daily loss limit yang siap dipakai.
- Forward test terlalu singkat (2 hari, 13 candle) dan tidak ada sinyal yang muncul, sehingga
  **tidak bisa menilai kinerja strategi**. Ini konsisten dengan karakteristik Donchian breakout
  (jarang sinyal) dan dengan hasil backtest (187 trade dalam 3,2 tahun ≈ 1 trade/5 hari).
- Kecocokan sinyal 100% (65/65) membuktikan bahwa bot dan backtest memakai aturan yang sama;
  perbedaan kinerja di masa depan (jika ada) akan berasal dari eksekusi (bid/ask nyata, slippage,
  keterlambatan tick), bukan dari logika.
- Keterbatasan yang diakui: universe 5 pasangan dan parameter dipilih sebelum forward test; backtest
  menunjukkan OOS yang lemah (PF IS 2,40 vs OOS 0,44), jadi ekspektasi terhadap strategi ini
  harus realistis. Forward test yang lebih panjang (≥ 1 bulan, ≥ 180 candle) diperlukan untuk
  menilai apakah edge-nya nyata.
PY