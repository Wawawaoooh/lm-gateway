import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


async def up_models(request):
    return JSONResponse({"object": "list",
                         "data": [{"id": "mock/model-1", "object": "model"}]})


async def sse_gen():
    for i in range(3):
        obj = {"choices": [{"delta": {"content": f"tok{i} "}}]}
        yield ("data: " + json.dumps(obj) + "\n\n").encode()
        await asyncio.sleep(0.02)
    final = {"choices": [], "usage": {"prompt_tokens": 17, "completion_tokens": 9}}
    yield ("data: " + json.dumps(final) + "\n\n").encode()
    yield b"data: [DONE]\n\n"


async def up_chat(request):
    body = await request.json()
    if body.get("stream"):
        return StreamingResponse(sse_gen(), media_type="text/event-stream")
    return JSONResponse({"choices": [{"message": {"content": "hi"}}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": 2}})


up = Starlette(routes=[Route("/v1/models", up_models, methods=["GET"]),
                       Route("/v1/chat/completions", up_chat, methods=["POST"])])

STOP = asyncio.Event()


def serve():
    loop = asyncio.new_event_loop()
    config = uvicorn.Config(up, host="127.0.0.1", port=9020, log_level="warning")
    server = uvicorn.Server(config)

    async def run():
        task = asyncio.ensure_future(server.serve())
        await STOP.wait()
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        except Exception as exc:  # noqa: BLE001
            print("upstream shutdown:", type(exc).__name__, exc)
    try:
        loop.run_until_complete(run())
    finally:
        loop.close()


th = threading.Thread(target=serve, daemon=True)
th.start()
time.sleep(2.5)

from gateway.config import GatewayConfig, Upstream
from gateway.keystore import KeyStore
from gateway.logger import RequestLogger
from gateway.limiter import RateLimiter
from gateway.proxy import create_app
from fastapi.testclient import TestClient

cfg = GatewayConfig(upstreams=[Upstream("mock", "http://localhost:9020/v1")])
cfg.model_routes["mock/model-1"] = {"up": "mock", "model": "mock/model-1"}
ks, lg, lim = KeyStore(), RequestLogger(), RateLimiter()
lim.configure(3)
info, tok = ks.create(f"stream-test-{int(time.time()*10)%99}")
app = create_app(cfg, ks, lg, lim)
c = TestClient(app)
H = {"Authorization": f"Bearer {tok}"}

chunks = []
with c.stream("POST", "/v1/chat/completions", json={"model": "mock/model-1",
                                                    "stream": True}, headers=H) as r:
    print("status:", r.status_code, "content-type:", r.headers.get("content-type"))
    for raw in r.iter_raw():
        chunks.append(raw.decode())
joined = "".join(chunks)
print("streamed bytes:", len(joined), "| tok0/tok2 present:", ("tok0" in joined and "tok2" in joined))
assert "[DONE]" in joined, "no DONE marker"

time.sleep(1.5)  # generator finally + DB commit settle
rows = lg.recent(limit=5)
print("active after stream:", lim.active)
match = [row for row in rows if row[3] == "mock/model-1"]
print("log rows:", match)
assert match, "stream request not logged"
latest = max(match, key=lambda row: row[0])
# recent() columns: ts, ip, key_id, model, upstream, latency, prompt, completion, cache, status
status = latest[9]
tokens = (latest[6] or 0) + (latest[7] or 0)
print("logged status/tokens:", status, tokens)
assert status == 200 and tokens >= 9, "usage parsing failed"
ks.revoke(key_id=info.id)

STOP.set()
th.join(timeout=8)
print("stream ok")
