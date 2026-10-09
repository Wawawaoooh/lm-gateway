"""OpenAI-compatible reverse-proxy gateway with auth, limits and logging."""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import GatewayConfig
from .keystore import KeyInfo, KeyStore
from .limiter import RateLimiter
from .logger import RequestLogger

MAX_BODY_BYTES = 32 * 1024 * 1024  # defense-in-depth for the public tunnel endpoint


def _client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for") or req.headers.get("cf-connecting-ip")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.client.host if req.client else "?"


def _token_of(auth_header: str) -> str:
    auth = (auth_header or "").strip()
    return auth[7:].strip() if auth[:7].lower() == "bearer " else auth


async def bearer_key(req: Request) -> KeyInfo:
    store: KeyStore = req.app.state.keystore
    info = store.verify(_token_of(req.headers.get("authorization", "")))
    if not info:
        req.app.state.log.log(ip=_client_ip(req), key_id=None, model=None, upstream=None,
                              latency_ms=0.0, status=401)
        raise HTTPException(401, "invalid_api_key")
    return info


async def _read_json(req: Request) -> tuple[dict, int]:
    length = req.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise HTTPException(413, "payload_too_large")
    raw = await req.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "payload_too_large")
    try:
        return json.loads(raw or b"{}"), len(raw)
    except ValueError as exc:
        raise HTTPException(400, "invalid_json") from exc


def _extract_usage(text: str) -> dict | None:
    usage: dict | None = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        u = obj.get("usage")
        if isinstance(u, dict):
            usage = u
    return usage


def _authorize_model(cfg: GatewayConfig, log: RequestLogger, *, ip: str, key: KeyInfo,
                     model_id: str):
    """Route + permission checks; logs and raises HTTPException on denial."""
    route = cfg.model_routes.get(model_id)

    def deny(status: int, detail: str):
        log.log(ip=ip, key_id=key.id, model=model_id or None, upstream=None,
                latency_ms=0.0, status=status)
        return HTTPException(status, detail)

    if not route:
        raise deny(404, "model_not_found")
    allow = None if not key.models else [m.strip() for m in key.models.split(",")]
    if allow is not None and model_id not in allow:
        raise deny(403, "model_not_permitted")
    upstream = cfg.find_upstream(route["up"])
    if not upstream:
        raise deny(503, "no_upstream_configured")
    return route, upstream


def _upstream_headers(upstream) -> dict[str, str]:
    headers = {"content-type": "application/json"}
    if upstream.api_key:
        headers["authorization"] = f"Bearer {upstream.api_key}"
    return headers


async def _forward_json(client: httpx.AsyncClient, log: RequestLogger, *, url: str,
                        headers: dict, fwd_body: dict, ip: str, key: KeyInfo,
                        model_id: str, route: dict) -> JSONResponse:
    """Non-streaming POST + pass-through with usage logging."""
    started = time.perf_counter()
    resp = await client.post(url, json=fwd_body, headers=headers)
    payload = None
    if resp.headers.get("content-type", "").startswith("application/json"):
        try:
            payload = resp.json()
        except ValueError:
            payload = None
    usage = (payload or {}).get("usage") or {}
    log.log(ip=ip, key_id=key.id, model=model_id, upstream=route["up"],
            latency_ms=(time.perf_counter() - started) * 1000,
            ptok=int(usage.get("prompt_tokens", 0)),
            ctok=int(usage.get("completion_tokens", 0)), status=resp.status_code)
    if payload is None:
        payload = {"raw": resp.text}
    return JSONResponse(payload, status_code=resp.status_code)


def create_app(cfg: GatewayConfig, keystore: KeyStore, log: RequestLogger,
               limiter: RateLimiter) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.http.aclose()

    app = FastAPI(title="LLM Gateway", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.keystore = keystore
    app.state.log = log
    app.state.limiter = limiter
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.get("/v1/models")
    async def models(key: KeyInfo = Depends(bearer_key)):
        routes = app.state.cfg.model_routes
        allow = None if not key.models else [m.strip() for m in key.models.split(",")]
        data = [{"id": eid, "object": "model", "owned_by": v["up"]}
                for eid, v in routes.items() if allow is None or eid in allow]
        return {"object": "list", "data": data}

    async def _admit(req: Request, key: KeyInfo):
        """Shared pre-flight: parse body, authorize model, consume concurrency slot.

        RPM quota is consumed only after the slot is granted, so queued requests
        don't burn their minute-window while waiting.
        """
        app = req.app
        limiter: RateLimiter = app.state.limiter

        body, raw_size = await _read_json(req)
        model_id = str(body.get("model", ""))
        ip = _client_ip(req)
        route, upstream = _authorize_model(app.state.cfg, app.state.log, ip=ip, key=key,
                                           model_id=model_id)

        await limiter.acquire()
        if not limiter.check_key(key.id, key.rate_limit_rpm):
            limiter.release()
            app.state.log.log(ip=ip, key_id=key.id, model=model_id, upstream=None,
                              latency_ms=0.0, status=429)
            raise HTTPException(429, "rate_limit_exceeded")
        return body, raw_size, ip, route, upstream

    @app.post("/v1/chat/completions")
    async def chat(req: Request, key: KeyInfo = Depends(bearer_key)):
        app = req.app
        log: RequestLogger = app.state.log
        limiter: RateLimiter = app.state.limiter
        client: httpx.AsyncClient = app.state.http

        body, raw_size, ip, route, upstream = await _admit(req, key)
        model_id = str(body.get("model", ""))

        fwd_body = dict(body)
        fwd_body["model"] = route.get("model", model_id)
        url = upstream.base_url.rstrip("/") + "/chat/completions"
        headers = _upstream_headers(upstream)

        started = time.perf_counter()
        done = False  # concurrency slot handed to the streaming generator once True

        def _release():
            nonlocal done
            if not done:
                done = True
                limiter.release()

        try:
            if not body.get("stream"):
                return await _forward_json(client, log, url=url, headers=headers,
                                           fwd_body=fwd_body, ip=ip, key=key,
                                           model_id=model_id, route=route)

            http_req = client.build_request("POST", url, json=fwd_body, headers=headers)
            resp = await client.send(http_req, stream=True)
            try:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode(errors="replace")[:800]
                    log.log(ip=ip, key_id=key.id, model=model_id, upstream=route["up"],
                            latency_ms=(time.perf_counter() - started) * 1000, status=resp.status_code)
                    _release()
                    return JSONResponse({"error": {"message": text}}, status_code=resp.status_code)
            except BaseException:
                await resp.aclose()
                raise

            async def sse_stream() -> AsyncIterator[bytes]:
                ptok, ctok, seen_bytes, saw_usage = 0, 0, 0, False
                status = resp.status_code
                buf = b""  # carry a partial SSE event across chunk boundaries
                try:
                    async for chunk in resp.aiter_raw():
                        seen_bytes += len(chunk)
                        yield chunk
                        buf = (buf + chunk)[-65536:]
                        if b"usage" not in buf:
                            continue
                        parts = buf.split(b"\n\n")
                        buf = parts.pop()  # keep the possibly-incomplete tail
                        usage = _extract_usage(b"\n\n".join(parts).decode("utf-8", errors="ignore"))
                        if usage:
                            ptok = int(usage.get("prompt_tokens", ptok))
                            ctok = int(usage.get("completion_tokens", ctok))
                            saw_usage = True
                except BaseException:
                    status = 599
                    raise
                finally:
                    await resp.aclose()
                    limiter.release()
                    if not saw_usage:
                        ptok, ctok = max(raw_size // 4, 0), seen_bytes // 4
                    log.log(ip=ip, key_id=key.id, model=model_id, upstream=route["up"],
                            latency_ms=(time.perf_counter() - started) * 1000,
                            ptok=ptok, ctok=ctok, status=status)

            done = True  # generator owns the slot; releases when stream ends or client drops
            return StreamingResponse(sse_stream(), media_type="text/event-stream",
                                     headers={"cache-control": "no-cache"})
        finally:
            if not done:
                limiter.release()

    @app.post("/v1/embeddings")
    async def embeddings(req: Request, key: KeyInfo = Depends(bearer_key)):
        app = req.app
        log: RequestLogger = app.state.log
        limiter: RateLimiter = app.state.limiter
        client: httpx.AsyncClient = app.state.http

        body, _raw_size, ip, route, upstream = await _admit(req, key)
        model_id = str(body.get("model", ""))

        fwd_body = dict(body)
        fwd_body["model"] = route.get("model", model_id)
        url = upstream.base_url.rstrip("/") + "/embeddings"

        try:
            return await _forward_json(client, log, url=url,
                                       headers=_upstream_headers(upstream),
                                       fwd_body=fwd_body, ip=ip, key=key,
                                       model_id=model_id, route=route)
        finally:
            limiter.release()

    return app
