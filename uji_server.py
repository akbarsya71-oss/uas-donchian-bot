"""Server WSGI lokal (hanya untuk uji): meniru routing /api/* dan POST JSON Vercel."""
import json
import os
import sys
from wsgiref.simple_server import make_server

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))
import main  # noqa: E402
app = main.app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    make_server("127.0.0.1", port, app).serve_forever()
