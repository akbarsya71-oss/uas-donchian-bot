# Bot Dry Run Indodax — UAS Algoritma Trading

Bot trading otomatis **dry run** (semua order simulasi, tanpa uang sungguhan, tanpa API key)
yang berjalan di Vercel dengan penjadwal GitHub Actions setiap 4 jam. Strategi, parameter,
dan universe dibekukan pada **4 November 2026 12.00 WIB**.

M Syahrul Akbar · 25110200047

---

## Isi repo

| Berkas | Fungsi |
|---|---|
| `api/strategy.py` | **ATURAN STRATEGI** (indikator, sinyal, sizing, stop, portofolio). Dipakai bersama backtest & bot. |
| `api/indodax.py` | Klien API publik Indodax (retry terbatas) + simulasi fill order book |
| `api/tick.py` | Endpoint `/api/tick` (WSGI): satu panggilan = satu candle |
| `api/data.py` | Endpoint `/api/data` (baca log untuk dashboard) |
| `api/kontrol.py` | Endpoint `/api/kontrol` (kill switch manual) |
| `api/store.py` | Penyimpanan state & log (Upstash Redis / file lokal) |
| `fetch_history.py` | Penarik candle historis 4 jam (mundur ~650 bar/request, ≤3 req/dtk) |
| `snapshot.py` | Kolektor snapshot order book (spread & slippage terukur) |
| `backtest.py` | Backtest 3+ tahun + validasi (IS/OOS, walk-forward, sensitivitas, stress, look-ahead) |
| `ekspor_log.py` | Ekspor log mentah dari Vercel ke `forward/raw/` |
| `hitung_ulang.py` | Menghitung ulang semua angka & membuat `dashboard.html` dari log mentah |
| `uji_lokal.py` | Uji idempotency, fill bid/ask, kill switch di laptop |
| `uji_server.py` | Server WSGI lokal yang meniru routing Vercel (`/api/*`, POST JSON) |
| `cek_kecocokan.py` | Bukti sinyal bot = sinyal backtest candle demi candle |
| `selesai.py` | Satu perintah: snapshot → backtest → ekspor log → dashboard → cek secret |
| `STRATEGI.md` | Strategi tertulis + perubahan dari UTS |
| `dashboard.html` | Dashboard offline mandiri (dibuat oleh `hitung_ulang.py`) |
| `REFLEKSI.md` | Refleksi backtest vs forward test |

## 1. Menjalankan di laptop

Butuh Python 3.9+ (hanya pustaka standar).

```bash
# data historis 3+ tahun (sekali; hati-hati: ~55 request, jangan paralel)
python fetch_history.py --start 2023-08-01

# snapshot order book (jalankan berkala minimal 3x sehari)
python snapshot.py --putaran 3 --jeda 60

# backtest penuh + validasi
python backtest.py

# backtest pada jendela forward test (untuk pembanding dashboard)
python backtest.py --mulai 2026-10-25 --selesai 2026-11-04 --tanpa-validasi --out backtest_forward.json

# uji bot tanpa Vercel (store file di local_state/)
python uji_lokal.py

# uji endpoint seperti di Vercel (routing /api/*, POST JSON, secret)
set STORE=file && set CRON_SECRET=<nilai-rahasia-lokal> && python uji_server.py
#   lalu: curl -H "Authorization: Bearer rahasia-uji" -X POST http://127.0.0.1:8765/api/tick

# bangun dashboard dari log lokal
python hitung_ulang.py --data forward/raw/uji-lokal.json

# semua bahan sekaligus (setelah forward test)
python selesai.py --url https://<app>.vercel.app/api/data
```

## 2. Deploy ke Vercel

1. Import repo ke Vercel (framework preset: *Other*, tidak ada build step).
2. **Storage**: Vercel → Storage → Marketplace → **Upstash for Redis** (plan gratis) →
   hubungkan ke project. Env `UPSTASH_REDIS_REST_URL` dan `UPSTASH_REDIS_REST_TOKEN`
   terisi otomatis.
3. **Environment Variables**:
   * `CRON_SECRET` — string acak panjang (`python -c "import secrets;print(secrets.token_urlsafe(32))"`).
   * `KILL_SWITCH` — `0` (normal) atau `1` (blokir order baru).
4. Deploy. Uji koneksi (wajib sebelum freeze):

```bash
curl https://<app>.vercel.app/api/tick/health
curl -H "Authorization: Bearer $CRON_SECRET" https://<app>.vercel.app/api/tick
```

## 3. Penjadwal (GitHub Actions)

`.github/workflows/bot-tick.yml` memanggil `/api/tick` pada menit ke-7 UTC untuk
jam 0,4,8,12,16,20 (candle 4 jam tutup 00,04,08,12,16,20 UTC; 3 menit kemudian).

Set di repo GitHub → Settings → Secrets and variables → Actions:

* `VERCEL_URL` = `https://<app>.vercel.app`
* `CRON_SECRET` = sama dengan di Vercel

`vercel.json` sengaja **tidak** memakai `crons` bawaan Vercel (Hobby hanya 1x/hari; penjadwal
luar memberi presisi lebih baik; setiap keterlambatan tercatat di log). `maxDuration` 300 dtk
untuk paket berbayar; Hobby otomatis dibatasi platform.

## 4. Kill switch

```bash
# nyalakan (blokir order baru, posisi lama tetap dinilai)
curl "https://<app>.vercel.app/api/kontrol?secret=$CRON_SECRET&aksi=kill"
# matikan
curl "https://<app>.vercel.app/api/kontrol?secret=$CRON_SECRET&aksi=hidup"
# atau tanpa deploy ulang lewat env KILL_SWITCH=1 (ubah di dashboard Vercel)
```
Setiap pemakaian tercatat di log `bot:error` (`jenis=kill_switch`).

## 5. Alur satu panggilan `/api/tick`

1. `server_time` Indodax → candle yang sudah tutup (`bar_time` terakhir).
2. Cek `state.last_bar_time`: candle sama → **idempoten**, tidak ada order.
3. Cek kill switch (env + flag storage) → berhenti, tercatat.
4. Ambil order book + candle; **validasi** (umur candle > 2× TF, candle hilang, harga/volume
   nol, order book kosong, spread > 2% → pasangan ditolak + log).
5. Eksekusi **keluar** (stop/trailing/SMA20<SMA50) → jual di **bid** order book.
6. Hitung sinyal close bar terakhir → **masuk** → beli di **ask** order book (multi-level).
7. Batas hari WIB + **daily loss limit 3%**; ukuran posisi ≤ 10% ekuitas, ≤ 5% ADV, ≥ Rp25.000.
8. Simpan state + log (`calls`, `orders`, `trades`, `signals`, `snapshots`, `equity`, `error`).

## 6. Log mentah & dashboard

| Key storage | Isi |
|---|---|
| `bot:calls` | setiap panggilan: waktu, candle, diproses/gagal/idempoten, durasi |
| `bot:orders` / `bot:trades` | setiap order simulasi: sisi, qty, best bid, best ask, harga fill, fee, slippage vs harga sinyal, alasan |
| `bot:equity` | equity harian (panggilan pertama setelah 00.00 WIB) |
| `bot:signals` | sinyal per pasangan per candle (termasuk yang tidak dieksekusi + alasannya) |
| `bot:snapshots` | order book saat bot berjalan (spread & slippage) |
| `bot:error` | error API, data tidak valid, daily loss limit, kill switch |

```bash
python ekspor_log.py --url https://<app>.vercel.app/api/data
python hitung_ulang.py
# -> dashboard.html + angkadashboard.json
```

Dashboard juga tersedia online di `https://<app>.vercel.app/` (membaca `/api/data`).

## 7. Keamanan

* Tidak ada secret di repo: hanya `.env.example` (lihat keamanan: `CRON_SECRET` tidak ada di kode).
* `.env` masuk `.gitignore`.
* `/api/tick` dan `/api/kontrol` mewajibkan `CRON_SECRET` (header `Authorization: Bearer`,
  `X-Cron-Secret`, atau query `secret`); `/api/data` hanya baca.
* Tidak memakai API key Indodax sama sekali.

## 8. Riwayat & freeze

Commit freeze beserta hash dan waktu dicatat di [`FREEZE.md`](FREEZE.md). Jangan
force-push setelah freeze.
