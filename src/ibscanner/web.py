"""FastAPI web app: one tab per scanner, live-updating via SSE."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from .config import AppConfig, ScannerConfig
from .engine import ScannerEngine, ScanResult
from .ib_client import IBClient


def create_app(config: AppConfig) -> FastAPI:
    subscribers: list[asyncio.Queue[str]] = []

    async def broadcast(data: dict) -> None:
        msg = f"event: scanner_update\ndata: {json.dumps(data)}\n\n"
        for q in subscribers:
            await q.put(msg)

    async def run_scanner(scanner: ScannerConfig, engine: ScannerEngine) -> None:
        while True:
            try:
                result = await engine.run(scanner)
                await broadcast(_serialise(result, scanner))
            except Exception as exc:  # noqa: BLE001
                await broadcast({
                    "name": scanner.name,
                    "error": str(exc),
                    "ran_at": datetime.now().strftime("%H:%M:%S"),
                    "rows": [],
                    "matches": 0,
                    "duration_s": 0,
                })
            await asyncio.sleep(scanner.refresh_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ib = IBClient(
            config.ib.host,
            config.ib.port,
            config.ib.client_id,
            config.ib.market_data_type,
        )
        engine = ScannerEngine(ib)

        try:
            await ib.connect()
        except Exception as exc:  # noqa: BLE001
            print(f"IB connection failed: {exc}")

        tasks = [
            asyncio.create_task(run_scanner(s, engine))
            for s in config.scanners
        ]

        yield

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await ib.disconnect()

    app = FastAPI(title="IBKR Scanners", lifespan=lifespan)

    @app.get("/api/scanners")
    async def get_scanners():
        return [
            {
                "name": s.name,
                "columns": s.columns,
                "refresh_seconds": s.refresh_seconds,
            }
            for s in config.scanners
        ]

    @app.get("/api/stream")
    async def stream():
        queue: asyncio.Queue[str] = asyncio.Queue()
        subscribers.append(queue)

        async def generate():
            try:
                while True:
                    try:
                        yield await asyncio.wait_for(queue.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                if queue in subscribers:
                    subscribers.remove(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/")
    async def index():
        html = (Path(__file__).parent / "static" / "index.html").read_text()
        return HTMLResponse(html)

    return app


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:,.2f}M"
        if abs(v) >= 1_000:
            return f"{v:,.2f}"
        return f"{v:,.4f}"
    return str(v)


def _serialise(result: ScanResult, scanner: ScannerConfig) -> dict:
    rows = []
    for row in result.rows:
        rows.append({
            "symbol": row.symbol,
            "matched": row.matched,
            "cells": {col: _fmt(row.values.get(col)) for col in scanner.columns},
            "error": row.error,
        })
    return {
        "name": result.name,
        "rows": rows,
        "ran_at": result.ran_at.strftime("%H:%M:%S"),
        "duration_s": round(result.duration_s, 1),
        "matches": sum(1 for r in result.rows if r.matched),
    }
