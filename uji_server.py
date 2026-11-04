"""Server WSGI lokal (hanya untuk uji): meniru routing /api/* dan POST JSON Vercel."""
import json
import os
import sys
from wsgiref.simple_server import make_server

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))
import data  # noqa: E402
import kontrol  # noqa: E402
import tick  # noqa: E402


def app(environ, start_response):
    path = environ.get("PATH_INFO", "").rstrip("/")
    n = int(environ.get("CONTENT_LENGTH") or 0)
    if n:
        environ["wsgi.input"].read(n)
    for nama, mod in (("/api/tick", tick), ("/api/data", data), ("/api/kontrol", kontrol)):
        if path == nama or path.startswith(nama + "/"):
            return mod.app(environ, start_response)
    if path in ("", "/index.html"):
        with open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8") as f:
            body = f.read().encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]
    start_response("404 Not Found", [("Content-Type", "application/json")])
    return [json.dumps({"ok": False, "error": "not found"}).encode()]


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    make_server("127.0.0.1", port, app).serve_forever()
