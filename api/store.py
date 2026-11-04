#!/usr/bin/env python3
"""Penyimpanan state & log.

- Produksi : Upstash Redis (REST) / Vercel Marketplace (Neon tidak dipakai di sini).
- Lokal    : FileStore (folder local_state/), untuk uji tanpa kredensial.

Tidak ada kredensial yang ditulis di kode: hanya dari environment variable.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone


class Store:
    def get(self, key): ...
    def set(self, key, val): ...
    def rpush(self, key, *vals): ...
    def lrange(self, key, start=0, stop=-1): ...
    def llen(self, key): ...

    # -- helper JSON ---------------------------------------------------------
    def get_json(self, key, default=None):
        raw = self.get(key)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    def set_json(self, key, obj):
        self.set(key, json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    def append_json(self, key, obj):
        self.rpush(key, json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    def append_many(self, key, objs):
        if objs:
            self.rpush(key, *[json.dumps(o, ensure_ascii=False, separators=(",", ":")) for o in objs])


class RedisStore(Store):
    """Upstash Redis REST API (kompatibel Vercel KV / Upstash for Redis)."""

    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def _pipeline(self, cmds):
        import urllib.request
        req = urllib.request.Request(
            self.url + "/pipeline",
            data=json.dumps(cmds).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode("utf-8"))
        return [x.get("result") if isinstance(x, dict) else None for x in out]

    def get(self, key):
        return self._pipeline([["GET", key]])[0]

    def set(self, key, val):
        self._pipeline([["SET", key, val]])
        return True

    def rpush(self, key, *vals):
        if not vals:
            return 0
        return self._pipeline([["RPUSH", key, *vals]])[0]

    def lrange(self, key, start=0, stop=-1):
        return self._pipeline([["LRANGE", key, start, stop]])[0] or []

    def llen(self, key):
        return self._pipeline([["LLEN", key]])[0] or 0

    def pipeline(self, cmds):
        return self._pipeline(cmds)


class FileStore(Store):
    """Penyimpanan lokal untuk uji: satu file JSON + file JSONL per key."""

    def __init__(self, base="local_state"):
        self.base = base
        os.makedirs(base, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key):
        safe = key.replace(":", "_").replace("/", "_")
        return os.path.join(self.base, safe)

    def get(self, key):
        path = self._path(key)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def set(self, key, val):
        with self._lock:
            tmp = self._path(key) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(val)
            os.replace(tmp, self._path(key))
        return True

    def rpush(self, key, *vals):
        with self._lock:
            with open(self._path(key) + ".jsonl", "a", encoding="utf-8") as f:
                for v in vals:
                    f.write(v.replace("\n", " ") + "\n")
        return len(vals)

    def lrange(self, key, start=0, stop=-1):
        path = self._path(key) + ".jsonl"
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]
        if start < 0:
            start = max(0, len(lines) + start)
        if stop < 0:
            stop = len(lines) + stop
        return lines[start:stop + 1]

    def llen(self, key):
        return len(self.lrange(key, 0, -1))


def get_store() -> Store:
    url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if os.environ.get("STORE", "").lower() == "file" or not (url and token):
        return FileStore(os.environ.get("LOCAL_STORE_DIR", "local_state"))
    return RedisStore(url, token)


def sekarang_wib() -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S WIB")


def hari_wib(ts_ms: int) -> str:
    from datetime import timedelta
    return (datetime.fromtimestamp(ts_ms / 1000, timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d")


def ts_wib(ts_ms: int) -> str:
    from datetime import timedelta
    return (datetime.fromtimestamp(ts_ms / 1000, timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S WIB")
