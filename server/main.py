from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from agents.mcp import MCPServerStdio, MCPServerStreamableHttp
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from starlette.middleware.trustedhost import TrustedHostMiddleware

from server.agent.runtime import Runtime
from server.agent.settings import AppConfig, mcp_configs
from server.agent.storage import ConflictError, NotFoundError
from server.quality.service import QualityService
from server.routes import chat, quality, settings
from server.routes.schemas import HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = AppConfig()
    redis = Redis.from_url(
        config.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )
    async with AsyncExitStack() as stack:
        stack.push_async_callback(redis.aclose)
        servers, missing = [], []
        for item in mcp_configs():
            if item.get("url"):
                params = {"url": item["url"]}
                if item.get("bearer_token_env"):
                    token = os.environ.get(item["bearer_token_env"])
                    if not token:
                        missing.append(item["name"])
                        continue
                    params["headers"] = {"Authorization": f"Bearer {token}"}
                server = MCPServerStreamableHttp(
                    name=item["name"], params=params, cache_tools_list=False
                )
            else:
                server = MCPServerStdio(
                    name=item["name"],
                    params={
                        key: value
                        for key, value in item.items()
                        if key in ("command", "args", "cwd", "env")
                    },
                    cache_tools_list=False,
                )
            try:
                servers.append(await stack.enter_async_context(server))
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "MCP startup failed for %s (%s)", item["name"], type(error).__name__
                )
                missing.append(item["name"])
        runtime = Runtime(config, redis, servers, missing)
        stack.push_async_callback(runtime.close)
        app.state.runtime = runtime
        runtime.quality = QualityService(runtime.ledger)
        stack.push_async_callback(runtime.quality.close)
        await runtime.quality.start()
        yield


app = FastAPI(
    title="EdgeDispatch local-first agent system", version="1.0.0", lifespan=lifespan
)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)
origins = ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Last-Event-ID"],
)


@app.middleware("http")
async def local_writes(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return JSONResponse({"detail": "Untrusted origin"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"detail": "JSON requests required"}, status_code=415)
    return await call_next(request)


@app.exception_handler(ConflictError)
async def conflict(request: Request, error: ConflictError) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.exception_handler(NotFoundError)
async def not_found(request: Request, error: NotFoundError) -> JSONResponse:
    return JSONResponse({"detail": "Conversation or turn not found"}, status_code=404)


@app.get("/api/health", response_model=HealthResponse)
async def health(request: Request):
    return await request.app.state.runtime.health()


app.include_router(chat.router)
app.include_router(settings.router)
app.include_router(quality.router)
