r"""Run the gateway without the PyQt6 console window (same config.json / gateway.db).

用法（在本机任意目录）：
    F:\Peterpertrilli\.venv\Scripts\python.exe F:\Peterpertrilli\scripts\headless_server.py
按 Ctrl+C 停止。GUI 与控制台是同一套配置，二者只能同时运行一个（端口会冲突）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from gateway.config import load_config  # noqa: E402
from gateway.keystore import KeyStore  # noqa: E402
from gateway.limiter import RateLimiter  # noqa: E402
from gateway.logger import RequestLogger  # noqa: E402
from gateway.proxy import create_app  # noqa: E402


def main() -> None:
    cfg = load_config()
    limiter = RateLimiter()
    limiter.configure(int(cfg.max_concurrency))
    app = create_app(cfg, KeyStore(), RequestLogger(), limiter)
    uvicorn.run(app, host=cfg.bind_host, port=int(cfg.port), log_level="info")


if __name__ == "__main__":
    main()
