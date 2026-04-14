"""Textual TUI: one tab per scanner, each on its own refresh interval."""

from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime, timezone
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)

from .config import AppConfig, ScannerConfig
from .engine import NewsSummary, ScannerEngine, ScanResult
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
    /* Default .datatable--cursor sets both background and color, which
     * repaints every cell on the cursor row in $text — that's what turns
     * the coloured news dot white when the row is selected. Override to
     * change only the background so cell-level Rich styles (the dot's
     * red/orange/yellow/grey) stay visible.
     */
    ScannerPane > DataTable > .datatable--cursor {
        background: $accent 40%;
        text-style: bold;
    }
    ScannerPane > DataTable > .datatable--hover {
        background: $accent 20%;
    }
    """

    def __init__(self, scanner: ScannerConfig, engine: ScannerEngine) -> None:
        super().__init__()
        self.scanner = scanner
        self.engine = engine
        self.summary = Static("idle")
        self.table: DataTable = DataTable(zebra_stripes=True, cursor_type="row")
        self._refresh_task: asyncio.Task[None] | None = None
        # Keep the last result so the `n` keybinding can map the cursor
        # row index back to a ScanRow (and its NewsSummary).
        self._current_result: ScanResult | None = None

    def compose(self) -> ComposeResult:
        yield self.summary
        yield self.table

    def on_mount(self) -> None:
        self.table.add_column("Symbol", width=10)
        self.table.add_column("Match", width=6)
        for col in self.scanner.columns:
            # The "news" column renders short age-bucket labels
            # ("HOT!", "NEW", "TODAY", "RECENT"); sized for the longest.
            width = 7 if col == "news" else 14
            self.table.add_column(col, width=width)
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
        # Without this, the summary stays on whatever set_summary last
        # wrote — which for the very first kick is the "waiting for
        # connection…" line from on_mount, making a long-running scan
        # look frozen.
        self._set_summary(f"{self.scanner.name}  |  refreshing…")
        try:
            result = await self.engine.run(self.scanner)
        except Exception as exc:  # noqa: BLE001
            self._set_summary(f"error: {exc}")
            return
        self._apply_result(result)

    def _apply_result(self, result: ScanResult) -> None:
        self.table.clear()
        self._current_result = result
        matches = 0
        for row in result.rows:
            if row.matched:
                matches += 1
            cells: list[Any] = [row.symbol, "✓" if row.matched else ""]
            for col in self.scanner.columns:
                if col == "news":
                    cells.append(_news_label(row.news))
                else:
                    cells.append(_fmt(row.values.get(col)))
            cells.append(row.error or "")
            self.table.add_row(*cells, key=row.symbol)
        ts = result.ran_at.strftime("%H:%M:%S")
        self._set_summary(
            f"{self.scanner.name}  |  symbols: {len(result.rows)}  |  "
            f"matches: {matches}  |  refresh: {self.scanner.refresh_seconds}s  |  "
            f"last: {ts} ({result.duration_s:.1f}s)"
        )

    def news_at_cursor(self) -> NewsSummary | None:
        """Look up the NewsSummary for whichever row the cursor is on.
        Returns None when the pane has never rendered, when the index
        is out of bounds (shouldn't happen normally), or when the
        current row has no news attached.
        """
        if self._current_result is None:
            return None
        idx = self.table.cursor_row
        if idx is None or idx < 0 or idx >= len(self._current_result.rows):
            return None
        return self._current_result.rows[idx].news

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


# Age buckets for the news column — short text labels rather than a
# coloured dot so the age reads correctly even when Textual's
# DataTable cursor-row styling clobbers the cell foreground colour
# (which is theme-dependent and not reliably fixable via CSS alone).
# Colour is still applied for themes that do preserve it.
_NEWS_BUCKETS: tuple[tuple[float, str, str], ...] = (
    (2.0,  "red",          "HOT!"),
    (8.0,  "dark_orange",  "8hrs"),
    (24.0, "yellow",       "24hrs"),
    (48.0, "grey50",       "48hrs"),
)


def _news_label(news: NewsSummary | None) -> Any:
    """Render the news column cell. Returns a styled Rich Text label
    ("HOT!", "NEW", "TODAY", "RECENT") when we have a recent story,
    or an em-dash when there's nothing (or the story is older than
    the longest bucket).
    """
    if news is None:
        return "—"
    age_hours = (datetime.now(timezone.utc) - news.time_utc).total_seconds() / 3600
    for max_h, colour, label in _NEWS_BUCKETS:
        if age_hours < max_h:
            return Text(label, style=f"bold {colour}")
    return "—"


# Block-level tags — opening *or* closing — become a paragraph break.
# Keeping both sides catches e.g. "<pre>...<pre>" (no closer) as well
# as well-formed "<p>...</p>", which TWS news bodies produce a mix of.
_HTML_BLOCK_RE = re.compile(
    r"</?(?:p|div|h[1-6]|li|tr|pre|blockquote|section|article"
    r"|ul|ol|table|thead|tbody|nav|header|footer|figure|figcaption)"
    r"\b[^>]*/?>",
    flags=re.IGNORECASE,
)
_HTML_BR_RE = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RUN_RE = re.compile(r"[ \t]+")
_NEWLINE_RUN_RE = re.compile(r"\n{3,}")
# Heuristic: is this body HTML-ish regardless of what articleType says?
# Providers occasionally flag HTML bodies as plain text (or vice versa),
# which is what lets raw tags leak into the modal.
_LOOKS_HTML_RE = re.compile(r"<\s*[a-zA-Z/]")


def _looks_like_html(s: str) -> bool:
    return bool(s and _LOOKS_HTML_RE.search(s))


def _strip_html(s: str) -> str:
    """Rough HTML → text for the news modal. Block tags become paragraph
    breaks, ``<br>`` becomes a single newline, everything else is
    dropped, entities are unescaped. Good enough for TWS news bodies;
    not a general HTML renderer.
    """
    if not s:
        return ""
    text = _HTML_BLOCK_RE.sub("\n\n", s)
    text = _HTML_BR_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _SPACE_RUN_RE.sub(" ", text)
    text = _NEWLINE_RUN_RE.sub("\n\n", text)
    # Per-line strip catches the leading whitespace that block tags on
    # their own line used to produce — without it the body looks ragged.
    return "\n".join(line.strip() for line in text.splitlines()).strip()


class NewsModal(ModalScreen[None]):
    """Fullscreen-ish overlay that shows one article's body. Dismissed
    with Escape. Takes plain text — caller is responsible for stripping
    HTML when needed.
    """

    BINDINGS = [("escape", "dismiss", "Close")]
    DEFAULT_CSS = """
    NewsModal {
        align: center middle;
    }
    NewsModal > Vertical {
        width: 85%;
        max-width: 110;
        height: 80%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }
    NewsModal #news-title {
        text-style: bold;
        padding-bottom: 1;
    }
    NewsModal #news-meta {
        color: $text-muted;
        padding-bottom: 1;
    }
    NewsModal #news-body {
        height: 1fr;
    }
    """

    def __init__(self, headline: str, meta: str, body: str) -> None:
        super().__init__()
        self.headline = headline
        self.meta = meta
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.headline, id="news-title")
            yield Static(self.meta, id="news-meta")
            # Wrap in Text() so that bodies containing "[something]"
            # (not uncommon in news) aren't misparsed as Rich markup.
            yield ScrollableContainer(Static(Text(self.body)), id="news-body")

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.app.pop_screen()


class ScannerApp(App):
    CSS = """
    Screen { layout: vertical; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_all", "Refresh now"),
        ("n", "show_news", "News"),
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

    async def action_show_news(self) -> None:
        """Open the latest news article for the row under the cursor in
        the currently-active scanner tab. Silent no-op when the row has
        no news attached (likely: scanner config doesn't include a
        "news" column, account has no news entitlement, or the symbol
        genuinely has no recent stories).
        """
        pane = self._active_pane()
        if pane is None:
            self.notify("no active scanner tab", severity="warning")
            return
        news = pane.news_at_cursor()
        if news is None:
            self.notify("no news for this row", severity="warning")
            return
        try:
            article_type, body = await self.ib_client.fetch_article(
                news.provider_code, news.article_id
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"article fetch failed: {exc}", severity="error")
            return

        # Strip whenever the body looks HTML-ish, not just when the
        # provider flagged articleType=1 — some feeds flag HTML bodies
        # as plain text, which is what leaks raw <pre> etc. into the UI.
        body = body or ""
        text = _strip_html(body) if (article_type == 1 or _looks_like_html(body)) else body
        local_dt = news.time_utc.astimezone()
        stories = f"{news.count} stor{'y' if news.count == 1 else 'ies'} in feed"
        meta = (
            f"{news.provider_code}  ·  "
            f"{local_dt.strftime('%Y-%m-%d %H:%M')}  ·  "
            f"{stories}"
        )
        self.push_screen(NewsModal(news.headline or "(no headline)", meta, text or "(empty body)"))

    def _active_pane(self) -> "ScannerPane | None":
        """Look up the ScannerPane inside the currently-active TabPane."""
        tabs = self.query_one(TabbedContent)
        active_id = tabs.active
        if not active_id:
            return None
        for scanner, pane in zip(self.app_config.scanners, self.panes):
            if f"tab-{_slug(scanner.name)}" == active_id:
                return pane
        return None


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s)
