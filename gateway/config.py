"""Configuration for the gateway: upstream providers and model routing."""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class Upstream:
    name: str
    base_url: str                       # OpenAI-compatible, e.g. http://localhost:1234/v1
    api_key: str | None = None          # upstream-side key (LM Studio/Ollama usually none)

    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "up"


@dataclass
class GatewayConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8787                    # LM Studio owns :1234 by default; keep them separate
    max_concurrency: int = 8
    upstreams: list[Upstream] = field(default_factory=list)
    model_routes: dict[str, dict[str, str]] = field(default_factory=dict)  # exposed_id -> {up, model}
    # per-model price in ¥ per 1M tokens; keys are fnmatch patterns, "default" is the fallback
    pricing: dict[str, dict[str, float]] = field(
        default_factory=lambda: {"default": {"input": 4.0, "output": 16.0}})
    # public base URL of the tunnel endpoint (e.g. https://your.domain.tld), empty = local only
    public_url: str = ""

    def find_upstream(self, name: str) -> Upstream | None:
        return next((u for u in self.upstreams if u.name == name), None)


def slug_for(name: str, model_id: str) -> str:
    up = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "up"
    mid = re.sub(r"^/+", "", f"/{model_id}").replace("/", "-")
    return f"{up}/{mid}"


def load_config() -> GatewayConfig:
    if not CONFIG_PATH.exists():
        cfg = GatewayConfig(upstreams=[
            Upstream("lm-studio", "http://localhost:1234/v1"),
            Upstream("ollama", "http://localhost:11434/v1", api_key="ollama"),
        ])
        save_config(cfg)
        return cfg
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    ups = [Upstream(**u) for u in raw.pop("upstreams", [])]
    return GatewayConfig(upstreams=ups, **raw)


_lock = threading.Lock()


def save_config(cfg: GatewayConfig) -> None:
    with _lock:
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        tmp.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)  # atomic on the same volume
