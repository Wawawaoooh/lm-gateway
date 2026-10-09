import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import py_compile
for f in (Path("gateway").glob("[a-z]*.py")):
    py_compile.compile(str(f), doraise=True)
print("compile ok")

from gateway.config import load_config, GatewayConfig, Upstream, slug_for
from gateway.keystore import KeyStore
from gateway.logger import RequestLogger
from gateway.limiter import RateLimiter
from gateway.proxy import create_app

cfg = load_config()
ks, lg, lim = KeyStore(), RequestLogger(), RateLimiter()
lim.configure(4)
app = create_app(cfg, ks, lg, lim)

routes = {"lm-studio/demo": {"up": "lm-studio", "model": "demo"}}
cfg.model_routes.update(routes)  # in-memory for test only
info, token = ks.create(f"smoke-test-{int(time.time()*100)%99999}")

from fastapi.testclient import TestClient
c = TestClient(app)
print("health:", c.get("/healthz").status_code)
print("no key /v1/models ->", c.get("http://test/v1/models").status_code)
r = c.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
print("models:", r.status_code, [m["id"] for m in r.json().get("data", [])])
bad = c.post("/v1/chat/completions", json={"model": "demo"}, headers={"Authorization": "Bearer sk-nope"})
print("bad key chat ->", bad.status_code)
unk = c.post("/v1/chat/completions", json={"model": "ghost"}, headers={"Authorization": f"Bearer {token}"})
print("unknown model ->", unk.status_code, unk.json().get("detail"))
ks.revoke(key_id=info.id)

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PyQt6.QtWidgets import QApplication
    a = QApplication([])
    print("PyQt6 ok")
except Exception as exc:  # noqa: BLE001
    print("PyQt6 FAIL:", type(exc).__name__, exc)

print("done")
