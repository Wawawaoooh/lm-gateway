"""API key storage: SHA-256 hashed bearer keys with per-key limits."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "gateway.db"


@dataclass
class KeyInfo:
    id: int
    name: str
    prefix: str          # display hint, e.g. sk-abc1…
    rate_limit_rpm: int | None   # requests per minute; None = unlimited
    models: str | None           # comma-separated allowlist or None (all)
    enabled: bool


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return "sk-" + secrets.token_urlsafe(32)


class KeyStore:
    def __init__(self, path: Path = DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS keys(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              token_hash TEXT NOT NULL UNIQUE,
              prefix TEXT NOT NULL,
              rate_limit_rpm INTEGER,
              models TEXT,
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at REAL NOT NULL
            );
        """)
        self.conn.commit()

    def create(self, name: str, rate_rpm: int | None = None, models: list[str] | None = None) -> tuple[KeyInfo, str]:
        token = new_token()
        cur = self.conn.execute(
            "INSERT INTO keys(name,token_hash,prefix,rate_limit_rpm,models,enabled,created_at)"
            " VALUES(?,?,?,?,?,1,?)",
            (name, _hash(token), token[:10], rate_rpm, ",".join(models) if models else None, time.time()),
        )
        self.conn.commit()
        return KeyInfo(cur.lastrowid, name, token[:10] + "…", rate_rpm,
                       ",".join(models) if models else None, True), token

    def verify(self, token: str | None) -> KeyInfo | None:
        if not token or not token.startswith("sk-"):
            return None
        row = self.conn.execute(
            "SELECT id,name,prefix,rate_limit_rpm,models,enabled FROM keys WHERE token_hash=?",
            (_hash(token),)).fetchone()
        if not row or not row[5]:
            return None
        return KeyInfo(row[0], row[1], row[2], row[3], row[4], True)

    def list(self) -> list[KeyInfo]:
        rows = self.conn.execute(
            "SELECT id,name,prefix,rate_limit_rpm,models,enabled FROM keys ORDER BY id").fetchall()
        return [KeyInfo(*r) for r in rows]

    def revoke(self, key_id: int | None = None, name: str | None = None) -> None:
        if key_id is not None:
            self.conn.execute("DELETE FROM keys WHERE id=?", (key_id,))
        elif name is not None:
            self.conn.execute("DELETE FROM keys WHERE name=?", (name,))
        self.conn.commit()

    def set_enabled(self, key_id: int, enabled: bool) -> None:
        self.conn.execute("UPDATE keys SET enabled=? WHERE id=?", (int(enabled), key_id))
        self.conn.commit()
