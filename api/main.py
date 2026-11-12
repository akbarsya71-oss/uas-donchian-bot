"""Entrypoint tunggal untuk runtime Python Vercel.

Semua URL diarahkan ke sini; /api/* dipasal ke handler yang sama yang dulu
berdiri sendiri (tick, data, kontrol). Aturan strategi TIDAK berubah.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data  # noqa: E402
import kontrol  # noqa: E402
import tick  # noqa: E402

INDEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")


def statis(environ, start_response):
    try:
        with open(INDEX, "rb") as f:
            body = f.read()
    except OSError:
        body = b"<h1>dashboard tidak tersedia</h1>"
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
    return [body]


def app(environ, start_response):
    path = environ.get("PATH_INFO", "").rstrip("/")
    if path.startswith("/api/tick"):
        return tick.app(environ, start_response)
    if path.startswith("/api/data"):
        return data.app(environ, start_response)
    if path.startswith("/api/kontrol"):
        return kontrol.app(environ, start_response)
    if path in ("", "/", "/index.html"):
        return statis(environ, start_response)
    start_response("404 Not Found", [("Content-Type", "application/json")])
    return [b'{"ok": false, "error": "not found"}']
