"""Textual TUI: one tab per scanner, each on its own refresh interval."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)

from .config import AppConfig, ScannerConfig
from .engine import ScannerEngine, ScanResult
from .ib_client import IBClient


class ScannerPane(Vertical):
    DEFAULT_CSS = """
    ScannerPane {
        height: 1fr;
    }
    ScannerPane > Static {
        padding: 0 1;
        color: $text-muted;
    }
    ScannerPane > DataTable {
        height: 1fr;
    }
    """

    def __init__(self, scanner: ScannerConfig, engine: ScannerEngine) -> None:
        super().__init__()
        self.scanner = scanner
        self.engine = engine
        self.summary = Static("idle")
        self.table: DataTable = DataTable(zebra_stripes=True, cursor_type="row")
        self._refresh_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield self.summary
        yield self.table

    def on_mount(self) -> None:
        self.table.add_column("Symbol", width=10)
        self.table.add_column("Match", width=6)
        for col in self.scanner.columns:
            self.table.add_column(col, width=14)
        self.table.add_column("Note", width=24)
        self._set_summary("waiting for connection…")
        self.set_interval(self.scanner.refresh_seconds, self.kick_refresh)

    def kick_refresh(self) -> None:
        if not self.engine.ib.connected:
            self._set_summary("not connected")
            return
        if self._refresh_task and not self._refresh_task.done():
            return  # previous run still in flight
        self._refresh_task = asyncio.create_task(self._run_scan())

    async def _run_scan(self) -> None:
        # self._set_summary(f"{self.scanner.name} | refreshing…")
        try:
            result = await self.engine.run(self.scanner)
        except Exception as exc:  # noqa: BLE001
            self._set_summary(f"error: {exc}")
            return
        self._apply_result(result)

    def _apply_result(self, result: ScanResult) -> None:
        self.table.clear()
        matches = 0
        for row in result.rows:
            if row.matched:
                matches += 1
            cells: list[str] = [row.symbol, "✓" if row.matched else ""]
            for col in self.scanner.columns:
                cells.append(_fmt(row.values.get(col)))
            cells.append(row.error or "")
            self.table.add_row(*cells, key=row.symbol)
        ts = result.ran_at.strftime("%H:%M:%S")
        self._set_summary(
            f"{self.scanner.name}  |  symbols: {len(result.rows)}  |  "
            f"matches: {matches}  |  refresh: {self.scanner.refresh_seconds}s  |  "
            f"last: {ts} ({result.duration_s:.1f}s)"
        )

    def _set_summary(self, msg: str) -> None:
        self.summary.update(msg)


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


class ScannerApp(App):
    CSS = """
    Screen { layout: vertical; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_all", "Refresh now"),
    ]

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.app_config = config
        self.ib_client = IBClient(
            config.ib.host,
            config.ib.port,
            config.ib.client_id,
            config.ib.market_data_type,
        )
        self.engine = ScannerEngine(self.ib_client)
        self.panes: list[ScannerPane] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            for s in self.app_config.scanners:
                pane = ScannerPane(s, self.engine)
                self.panes.append(pane)
                with TabPane(s.name, id=f"tab-{_slug(s.name)}"):
                    yield pane
        yield Footer()

    async def on_mount(self) -> None:
        if self.app_config.theme:
            self.theme = self.app_config.theme
        self.title = "IBKR Scanners"
        self.sub_title = (
            f"connecting to {self.app_config.ib.host}:{self.app_config.ib.port}…"
        )
        try:
            await self.ib_client.connect()
        except Exception as exc:  # noqa: BLE001
            self.sub_title = f"connection failed: {exc}"
            return
        self.sub_title = (
            f"connected to {self.app_config.ib.host}:{self.app_config.ib.port}"
        )
        # Kick off the first scan immediately rather than waiting for the interval.
        for pane in self.panes:
            pane.kick_refresh()

    async def on_unmount(self) -> None:
        await self.ib_client.disconnect()

    def action_refresh_all(self) -> None:
        for pane in self.panes:
            pane.kick_refresh()


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s)
