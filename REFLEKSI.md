# Refleksi: Backtest vs Forward Test

> Berkas ini diisi **setelah** forward test selesai (14 Nov 2026), memakai angka dari
> `dashboard.html` / `angkadashboard.json` yang dihasilkan `hitung_ulang.py` dari log mentah.
> Kerangka di bawah sudah memuat rumus dan sumber angka, sehingga tinggal diisi angka akhir.

## 1. Ringkasan angka

| Metrik | Backtest (jendela forward) | Forward test | Selisih | Penyebab |
|---|---|---|---|---|
| Return total | | | | |
| Max drawdown | | | | |
| Jumlah trade | | | | |
| Win rate | | | | |
| Profit factor | | | | |
| Rata-rata untung / trade | | | | |
| Rata-rata rugi / trade | | | | |
| Porsi biaya / laba kotor | | | | |
| Benchmark BTC buy & hold | | | | |

## 2. Kecocokan sinyal candle demi candle

Target 100%: backtest dijalankan ulang pada jendela yang sama dengan candle yang sama
(`backtest_forward.json`) dan memakai fungsi `api/strategy.py` yang sama.

* Candle forward test: … candle, diproses … (`uptime` dari `bot:calls`).
* Sinyal bot vs sinyal backtest: … identik dari … (bandingkan `bot:signals` dengan
  `_sinyal`/`trades` di `backtest_forward.json` pada `bar_time` yang sama).
* Perbedaan yang diizinkan: ukuran posisi dan harga fill, karena keduanya bergantung pada
  harga bid/ask nyata saat tick, bukan pada harga close/open candle.

## 3. Sumber selisih harga fill (angka)

| Komponen | Backtest | Forward | Penjelasan |
|---|---|---|---|
| Fill beli | open bar + ½ spread median + slippage median (… bps/pasangan) | ask order book live (multi-level) | spread backtest berasal dari snapshot sebelum freeze; bandingkan dengan median spread seluruh snapshot (termasuk selama forward) |
| Fill jual | close + ½ spread + slippage | bid order book live | idem |
| Fee | 0,20555% / 0,41555% (taker all-in) | tarif yang sama di kode | tidak ada selisih fee |
| Keterlambatan | eksekusi di bar berikutnya | tick 7 menit setelah candle tutup (± keterlambatan GitHub Actions, tercatat di `bot:calls.waktu`) | bertambahnya slippage saat pasar bergerak di 7 menit itu |

Median spread yang dipakai backtest per pasangan: BTC 11,16 · ETH 11,63 · XRP 6,51 ·
SOL 13,30 · DOGE 14,88 bps (186 snapshot, 4 Nov 2026). Median seluruh snapshot setelah
forward test: … bps (perbandingan wajib di bagian ini).

## 4. Uptime dan dampaknya

* Panggilan terjadwal: … (6×/hari × 10 hari = 60 diharapkan).
* Candle diproses tepat waktu: … / …, uptime …%.
* Panggilan terlambat/gagal: … — penyebab dari log (`jenis`: api, data_tidak_valid,
  daily_loss_limit, kill_switch, exception).
* Dampak: candle yang tidak diproses berarti posisi lama tetap tidak dikelola (stop tidak
  dievaluasi) dan sinyal candle itu hilang; jumlah tersebut dihitung di atas.

## 5. Kejadian selama forward test

* Kill switch dipakai: … kali (alasan).
* Daily loss limit aktif: … kali.
* Pasangan ditolak validasi (spread > 2%, candle hilang, dll.): … kali.

## 6. Pembelajaran

* …
