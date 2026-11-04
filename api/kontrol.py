#!/usr/bin/env python3
"""
/api/kontrol — kill switch manual tanpa deploy ulang.

  GET /api/kontrol?secret=...&aksi=status
  GET /api/kontrol?secret=...&aksi=kill      # blokir order baru
  GET /api/kontrol?secret=...&aksi=hidup     # aktifkan lagi

Setiap pemakaian dicatat di log bot:error (jenis=kill_switch).
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import get_store  # noqa: E402


def wib(ms=None):
    ms = ms or int(time.time() * 1000)
    return (datetime.fromtimestamp(ms / 1000, timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S WIB")


def app(environ, start_response):
    def balas(data, status="200 OK"):
        body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        start_response(status, [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")])
        return [body]

    qs = parse_qs(environ.get("QUERY_STRING", ""))
    secret = os.environ.get("CRON_SECRET", "")
    diberikan = (qs.get("secret") or [""])[0] or environ.get("HTTP_X_CRON_SECRET", "")
    if not secret or not hmac.compare_digest(diberikan, secret):
        return balas({"ok": False, "error": "secret salah"}, "401 Unauthorized")

    aksi = (qs.get("aksi") or ["status"])[0]
    store = get_store()
    sekarang = int(time.time() * 1000)

    if aksi == "kill":
        flag = dict(aktif=True, waktu=wib(sekarang), ts=sekarang, oleh="manual")
        store.set_json("bot:kill", flag)
        store.append_json("bot:error", dict(ts=sekarang, waktu=wib(sekarang), jenis="kill_switch",
                                            pesan="kill switch DINYALAKAN manual: order baru diblokir"))
    elif aksi == "hidup":
        flag = dict(aktif=False, waktu=wib(sekarang), ts=sekarang, oleh="manual")
        store.set_json("bot:kill", flag)
        store.append_json("bot:error", dict(ts=sekarang, waktu=wib(sekarang), jenis="kill_switch",
                                            pesan="kill switch DIMATIKAN manual"))
    elif aksi == "status":
        flag = store.get_json("bot:kill", {"aktif": False})
    else:
        return balas({"ok": False, "error": f"aksi tidak dikenal: {aksi}"}, "400 Bad Request")

    return balas({"ok": True, "kill": flag})


handler = app
