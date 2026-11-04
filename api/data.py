#!/usr/bin/env python3
"""
/api/data — endpoint baca untuk dashboard Vercel (tanpa secret, hanya baca).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import get_store  # noqa: E402


def ambil(store, key, maks=5000):
    raw = store.lrange(key, -maks, -1)
    out = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception:  # noqa: BLE001
            continue
    return out


def app(environ, start_response):
    def balas(data, status="200 OK"):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        start_response(status, [("Content-Type", "application/json; charset=utf-8"),
                                ("Cache-Control", "no-store"),
                                ("Access-Control-Allow-Origin", "*")])
        return [body]

    try:
        store = get_store()
        data = dict(
            ok=True,
            state=store.get_json("bot:state", {}),
            calls=ambil(store, "bot:calls", 3000),
            orders=ambil(store, "bot:orders", 3000),
            trades=ambil(store, "bot:trades", 3000),
            equity=ambil(store, "bot:equity", 1000),
            signals=ambil(store, "bot:signals", 8000),
            snapshots=ambil(store, "bot:snapshots", 8000),
            errors=ambil(store, "bot:error", 1000),
            kill=store.get_json("bot:kill", {"aktif": False}),
        )
        return balas(data)
    except Exception as e:  # noqa: BLE001
        return balas({"ok": False, "error": str(e)}, "500 Internal Server Error")


handler = app
