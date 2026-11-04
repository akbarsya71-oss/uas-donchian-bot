# Bukti Freeze — UAS Algoritma Trading

Aturan, parameter, universe, dan waktu mulai forward test dibekukan di commit ini.
Setelah commit ini isi `api/strategy.py`, `STRATEGI.md`, `backtest.py`, `api/tick.py`,
`backtest_forward.json`, dan `data/snapshots.jsonl` **tidak diubah** sampai pengumpulan
(14 November 2026 23.59 WIB). Perbaikan bug yang tidak mengubah aturan (jika ada) dicatat
di bagian bawah berkas ini.

| Hal | Nilai |
|---|---|
| Waktu freeze (WIB) | 2026-11-04 (lihat waktu commit di bawah) |
| Commit freeze | `C2` (lihat tabel riwayat) |
| Waktu mulai forward test | 2026-11-04 — tick pertama setelah commit freeze; setiap candle 4 jam berikutnya diproses pada menit ke-7 UTC |
| Selesai forward test | 2026-11-14 12.00 WIB |
| Universe | BTCIDR, ETHIDR, XRPIDR, SOLIDR, DOGEIDR |
| Modal virtual | Rp100.000.000 |

## Riwayat commit

Setelah repo di-push ke GitHub, isi tabel ini (jalankan `git log --pretty=format:"%h %cI %s"`):

| Commit | Waktu (UTC) | Isi |
|---|---|---|
| `C1` | (otomatis) | Source awal: strategi, backtest, bot, dokumen |
| `C2` | (otomatis) | **FREEZE** — hasil backtest final + snapshot spread + strategi tertulis |
| `C3` | — | Hanya dashboard/refleksi/log (tidak mengubah aturan) |

## Perbaikan bug setelah freeze (catatan wajib)

| Waktu | Commit | Bug | Dampak pada aturan |
|---|---|---|---|
| — | — | belum ada | — |
