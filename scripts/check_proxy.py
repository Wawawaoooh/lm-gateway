import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from gateway.config import GatewayConfig, Upstream
from gateway.keystore import KeyStore
from gateway.logger import RequestLogger
from gateway.limiter import RateLimiter
from gateway.proxy import create_app
from fastapi.testclient import TestClient

cfg = GatewayConfig(upstreams=[Upstream("dead", "http://localhost:9/v1")])
cfg.model_routes["dead/ghost"] = {"up": "dead", "model": "ghost"}
ks, lg, lim = KeyStore(), RequestLogger(), RateLimiter()
lim.configure(2)
info3, tok = ks.create(f"proxy-test-{int(time.time()*10)%99}")
app = create_app(cfg, ks, lg, lim)
c = TestClient(app, raise_server_exceptions=False)

H = {"Authorization": f"Bearer {tok}"}

print("active before:", lim.active)
for i in range(4):
    try:
        r = c.post("/v1/chat/completions", json={"model": "dead/ghost"}, headers=H)
        print(i, r.status_code, str(r.text)[:80])
    except Exception as exc:  # noqa: BLE001
        print(i, "server error:", type(exc).__name__, str(exc)[:60])
    print("   active during/after:", lim.active)
print("active after connect errors:", lim.active)

r = c.get("/v1/models", headers=H)
print("models:", [m["id"] for m in r.json()["data"]])

# embeddings endpoint: same routing/auth path, upstream is dead so expect 5xx-ish
try:
    r = c.post("/v1/embeddings", json={"model": "dead/ghost", "input": "hi"}, headers=H)
    print("embeddings:", r.status_code, str(r.text)[:80])
    print("   active after embeddings:", lim.active)
except Exception as exc:  # noqa: BLE001
    print("embeddings server error:", type(exc).__name__, str(exc)[:60])

# payload size guard
r = c.post("/v1/chat/completions", headers={**H, "content-length": str(64 * 1024 * 1024)},
           content=b"{}")
print("oversize guard:", r.status_code)

# no/invalid auth
print("no-auth:", c.post("/v1/embeddings", json={"model": "dead/ghost"}).status_code)
ks.revoke(key_id=info3.id)
print("proxy ok")
