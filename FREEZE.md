# Bukti Freeze — UAS Algoritma Trading

Aturan, parameter, universe, dan waktu mulai forward test dibekukan di commit ini.
Setelah commit freeze isi `api/strategy.py`, `STRATEGI.md`, `backtest.py`, `api/tick.py`,
`backtest_forward.json`, dan `data/snapshots.jsonl` **tidak diubah** sampai pengumpulan
(14 November 2026 23.59 WIB). Perbaikan bug yang tidak mengubah aturan (jika ada) dicatat
di bagian bawah berkas ini.

| Hal | Nilai |
|---|---|
| Commit freeze | `3746ecab2269c6e8bfd6221f5623ce313e442de9` (short `3746eca`) |
| Waktu commit freeze | 2026-11-04 13:28:23 +07:00 (06:28 UTC) |
| Waktu mulai forward test | tick pertama setelah commit freeze; setiap candle 4 jam berikutnya diproses pada menit ke-7 UTC |
| Selesai forward test | 2026-11-14 12.00 WIB |
| Universe | BTCIDR, ETHIDR, XRPIDR, SOLIDR, DOGEIDR |
| Modal virtual | Rp100.000.000 |

## Riwayat commit (cek ulang: `git log --pretty=format:"%h %cI %s"`)

| Commit | Waktu | Isi |
|---|---|---|
| `a0dd812` | 2026-11-04T12:24:08+07:00 | Source awal: strategi, backtest, bot, dokumen, dashboard |
| `79c0f9d` | 2026-11-04T12:25:11+07:00 | Commit FREEZE pertama (tertutup versi final di bawah) |
| `3746eca` | 2026-11-04T13:28:23+07:00 | **FREEZE final** — hasil backtest & snapshot spread terakhir + angka final STRATEGI.md |
| berikutnya | — | Hanya dashboard/refleksi/log (tidak mengubah aturan) |

## Perbaikan bug setelah freeze (catatan wajib)

| Waktu | Commit | Bug | Dampak pada aturan |
|---|---|---|---|
| 2026-11-12 | e21b413 | Entrypoint tunggal Vercel (api/main.py, pyproject.toml, vercel.json) + .vercelignore; tidak mengubah aturan strategi | tidak ada |
