"""Request logging to SQLite + aggregate stats for the dashboard."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
DB_PATH = Path(__file__).resolve().parent.parent / "gateway.db"
RETENTION_DAYS = 30
RECENT_WINDOW_DAYS = 7


class RequestLogger:
    def __init__(self, path: Path = DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS requests(
            ts REAL, ip TEXT, key_id INTEGER, model TEXT, upstream TEXT,
            latency_ms REAL, prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0, status INTEGER)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts)")
        self.conn.commit()
        self._last_purge = time.time()
        self._purge()

    def _purge(self) -> None:
        self.conn.execute("DELETE FROM requests WHERE ts < ?",
                          (time.time() - RETENTION_DAYS * 86400,))
        self.conn.commit()

    def log(self, *, ip: str, key_id: int | None, model: str | None, upstream: str | None,
            latency_ms: float, ptok: int = 0, ctok: int = 0, status: int):
        self.conn.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)",
                          (time.time(), ip, key_id, model, upstream, latency_ms, ptok, ctok, status))
        self.conn.commit()
        if time.time() - self._last_purge > 86400:  # cheap retention check on the hot path
            self._last_purge = time.time()
            self._purge()

    def recent(self, limit: int = 200) -> list[tuple]:
        # cache est: prefix-reuse approximation — consecutive same-key requests
        # re-send prior history, which the upstream KV cache serves without re-prefill.
        return self.conn.execute(
            """SELECT ts, ip, key_id, model, upstream, latency_ms,
                      prompt_tokens, completion_tokens,
                      CASE WHEN key_id IS NULL OR status>=400 THEN 0
                           ELSE MAX(0, COALESCE(MIN(prompt_tokens, prev_total), 0)) END,
                      status
               FROM (SELECT *,
                            LAG(prompt_tokens + completion_tokens)
                                OVER (PARTITION BY key_id ORDER BY ts) AS prev_total
                     FROM requests WHERE ts > ?)
               ORDER BY ts DESC LIMIT ?""",
            (time.time() - RECENT_WINDOW_DAYS * 86400, limit)).fetchall()

    def key_usage(self) -> list[tuple]:
        return self.conn.execute(
            """SELECT k.id, k.name, k.prefix, COUNT(r.ts),
                      COALESCE(SUM(r.prompt_tokens), 0),
                      COALESCE(SUM(r.completion_tokens), 0),
                      COALESCE(SUM(r.prompt_tokens + r.completion_tokens), 0)
               FROM keys k LEFT JOIN requests r ON r.key_id = k.id
               GROUP BY k.id ORDER BY 7 DESC""").fetchall()

    def key_model_usage(self) -> list[tuple]:
        """(key_id, model, input_tokens, output_tokens) per key+model, for cost math."""
        return self.conn.execute(
            """SELECT key_id, model, COALESCE(SUM(prompt_tokens), 0),
                      COALESCE(SUM(completion_tokens), 0)
               FROM requests GROUP BY key_id, model""").fetchall()

    def stats(self) -> dict:
        now = time.time()
        day_start = now - 86400
        c = self.conn.execute
        per_min = c("SELECT COUNT(*) FROM requests WHERE ts>?", (now - 60,)).fetchone()[0]
        avg_lat = c("SELECT AVG(latency_ms) FROM requests WHERE ts>?", (now - 300,)).fetchone()[0] or 0.0
        ptok = c("SELECT SUM(prompt_tokens) FROM requests WHERE ts>?", (day_start,)).fetchone()[0] or 0
        ctok = c("SELECT SUM(completion_tokens) FROM requests WHERE ts>?", (day_start,)).fetchone()[0] or 0
        # 24h cache-hit estimate: per-key prefix growth, successful requests only
        cached = c("""SELECT COALESCE(SUM(cache_est), 0) FROM (
                          SELECT CASE WHEN key_id IS NULL OR status>=400 THEN 0
                                      ELSE MAX(0, COALESCE(MIN(prompt_tokens, prev_total), 0)) END AS cache_est
                          FROM (SELECT prompt_tokens, key_id, status,
                                       LAG(prompt_tokens + completion_tokens)
                                           OVER (PARTITION BY key_id ORDER BY ts) AS prev_total
                                FROM requests WHERE ts>?)
                      )""", (day_start,)).fetchone()[0] or 0
        hit_rate = round(cached / ptok * 100, 1) if ptok else 0.0
        errs = c("SELECT COUNT(*) FROM requests WHERE ts>? AND status>=400", (day_start,)).fetchone()[0]
        total = c("SELECT COUNT(*) FROM requests").fetchone()[0]
        return {"req_per_min": per_min, "avg_latency_ms": round(avg_lat, 1),
                "tokens_in_24h": ptok, "tokens_out_24h": ctok,
                "cache_hit_24h": hit_rate, "errors_24h": errs, "total_requests": total}
