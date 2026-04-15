"""Textual TUI: tabs of scanners with a fundamentals + news detail column."""

from __future__ import annotations

import asyncio
import html
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)

from .config import AppConfig, ScannerConfig, TabConfig
from .engine import NewsSummary, ScanRow, ScannerEngine, ScanResult
from .ib_client import IBClient


# macOS-only system sound used for new-HOT-news alerts. Ships with every
# OS X install, so no bundled assets. On non-macOS we fall back to the
# terminal bell, which is inaudible in most modern terminals — but also
# harmless, so the code path is simpler than a conditional import.
_GLASS_SOUND_PATH = "/System/Library/Sounds/Glass.aiff"
# Minimum seconds between consecutive alerts — stops a fast refresh
# cycle (or a news-heavy open) from machine-gunning the speakers.
_ALERT_COOLDOWN_S = 5.0


class AlertManager:
    """App-wide sound player for new-HOT-news alerts.

    Playback is fire-and-forget: we spawn ``afplay`` without awaiting
    it, so a slow audio subsystem never blocks the event loop. One
    global cooldown gates the whole app — multiple scanners firing at
    once collapse into a single beep rather than overlapping audio.
    """

    def __init__(self) -> None:
        # macOS: afplay is always on PATH. Other platforms: fall back
        # to the terminal bell, which *some* terminals pipe to audio
        # and most don't — good enough as a no-dependency default.
        self._afplay = shutil.which("afplay") if sys.platform == "darwin" else None
        self._last_played: float = 0.0

    def alert(self) -> None:
        now = time.monotonic()
        if now - self._last_played < _ALERT_COOLDOWN_S:
            return
        self._last_played = now
        if self._afplay:
            try:
                subprocess.Popen(
                    [self._afplay, _GLASS_SOUND_PATH],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                # PATH lied or the sound file is missing — fail silent,
                # no point pestering the UI over a missed ding.
                pass
        else:
            # Terminal bell. Flush so it fires even when Textual is
            # holding stdout open.
            try:
                sys.stdout.write("\a")
                sys.stdout.flush()
            except OSError:
                pass


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

    def __init__(
        self,
        scanner: ScannerConfig,
        engine: ScannerEngine,
        alerts: AlertManager | None = None,
    ) -> None:
        super().__init__()
        self.scanner = scanner
        self.engine = engine
        self.alerts = alerts
        self.summary = Static("idle")
        self.table: DataTable = DataTable(zebra_stripes=True, cursor_type="row")
        self._refresh_task: asyncio.Task[None] | None = None
        # Keep the last result so the `n` keybinding can map the cursor
        # row index back to a ScanRow (and its NewsSummary).
        self._current_result: ScanResult | None = None
        # Seen (symbol, article_id) pairs that were in the HOT! bucket.
        # Alerts fire only when the diff gains a new pair, so a symbol
        # parked in the scanner on the same story stays silent.
        self._seen_hot: set[tuple[str, str]] = set()
        # First result is a priming pass — we load _seen_hot without
        # alerting, else every HOT row at startup would ding.
        self._primed_hot: bool = False

    def compose(self) -> ComposeResult:
        yield self.summary
        yield self.table

    def on_mount(self) -> None:
        self.table.add_column("Symbol", width=10)
        self.table.add_column("Match", width=6)
        for col in self.scanner.columns:
            # The "news" column renders short age-bucket labels
            # ("HOT!", "8hrs", "24hrs", "48hrs"); sized for the longest.
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
        hot_now: set[tuple[str, str]] = set()
        for row in result.rows:
            if row.matched:
                matches += 1
            if _is_hot(row.news):
                hot_now.add((row.symbol, row.news.article_id))  # type: ignore[union-attr]
            cells: list[Any] = [row.symbol, "✓" if row.matched else ""]
            for col in self.scanner.columns:
                if col == "news":
                    cells.append(_news_label(row.news))
                elif col in _INT_COLUMNS:
                    cells.append(_fmt_int(row.values.get(col)))
                else:
                    cells.append(_fmt(row.values.get(col)))
            cells.append(row.error or "")
            self.table.add_row(*cells, key=row.symbol)
        self._handle_hot_diff(hot_now)
        ts = result.ran_at.strftime("%H:%M:%S")
        self._set_summary(
            f"{self.scanner.name}  |  symbols: {len(result.rows)}  |  "
            f"matches: {matches}  |  refresh: {self.scanner.refresh_seconds}s  |  "
            f"last: {ts} ({result.duration_s:.1f}s)"
        )

    def _handle_hot_diff(self, hot_now: set[tuple[str, str]]) -> None:
        """Fire an alert when the HOT set gains a pair the previous
        result didn't contain. Prime the state on first call so existing
        HOT rows at startup don't all ding at once.
        """
        if not self.scanner.alert_on_hot_news or self.alerts is None:
            # Keep state in sync regardless so enabling the flag
            # mid-session doesn't immediately blast the backlog.
            self._seen_hot = hot_now
            self._primed_hot = True
            return
        if not self._primed_hot:
            self._seen_hot = hot_now
            self._primed_hot = True
            return
        new_pairs = hot_now - self._seen_hot
        if new_pairs:
            self.alerts.alert()
        self._seen_hot = hot_now

    def row_at(self, idx: int | None) -> ScanRow | None:
        """Look up the ScanRow for a cursor index. Returns None when the
        pane has never rendered or when the index is out of bounds.
        """
        if self._current_result is None:
            return None
        if idx is None or idx < 0 or idx >= len(self._current_result.rows):
            return None
        return self._current_result.rows[idx]

    def news_at_cursor(self) -> NewsSummary | None:
        row = self.row_at(self.table.cursor_row)
        return row.news if row else None

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


# Columns that represent discrete counts. Volume is served as float by
# pandas (and so picks up a spurious ".00") but is always a whole
# number in practice — _fmt_int renders those without decimals.
_INT_COLUMNS = {"volume", "volume_sma_20"}


def _fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return str(v)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:,.2f}M"
    return f"{n:,}"


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


# Tied to _NEWS_BUCKETS[0]: the "HOT!" age threshold in hours. Pulled
# out so _is_hot and the bucket loop agree without re-indexing.
_HOT_THRESHOLD_H = 2.0


def _is_hot(news: NewsSummary | None) -> bool:
    if news is None:
        return False
    age_hours = (datetime.now(timezone.utc) - news.time_utc).total_seconds() / 3600
    return age_hours < _HOT_THRESHOLD_H


def _news_label(news: NewsSummary | None) -> Any:
    """Render the news column cell. Returns a styled Rich Text label
    ("HOT!", "8hrs", "24hrs", "48hrs") when we have a recent story, or
    an em-dash when there's nothing (or the story is older than the
    longest bucket).
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


# ReportSnapshot fields worth showing first. Paths are relative to the
# XML root; we fall back to a stripped-tags view if none of them match
# (which is what happens for report types other than ReportSnapshot,
# or for accounts whose entitlement returns a differently-shaped doc).
_FUNDAMENTAL_FIELDS: tuple[tuple[str, str], ...] = (
    (".//CoIDs/CoID[@Type='CompanyName']", "Company"),
    (".//Issues/Issue/IssueID[@Type='Ticker']", "Ticker"),
    (".//Issues/Issue/Exchange", "Exchange"),
    (".//CoGeneralInfo/Employees", "Employees"),
    (".//CoGeneralInfo/CommonShareholders", "Shareholders"),
    (".//CoGeneralInfo/SharesOut", "Shares Out"),
    (".//CoGeneralInfo/MarketCap", "Market Cap"),
    (".//peerInfo/IndustryInfo/Industry[@type='TRBC']", "Industry"),
    (".//peerInfo/IndustryInfo/Industry[@type='NAICS']", "NAICS"),
)


# Order matters — this is also the display order in the panel.
# Labels are user-facing; keys match ib_async ContractDetails/Contract
# attribute names (which fetch_company_profile mirrors verbatim).
_PROFILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("longName", "Name"),
    ("industry", "Industry"),
    ("category", "Category"),
    ("subcategory", "Subcategory"),
    ("stockType", "Stock Type"),
    ("primaryExchange", "Primary Exchange"),
    ("marketName", "Market"),
    ("currency", "Currency"),
    ("timeZoneId", "Timezone"),
    ("validExchanges", "Routes"),
)


def _format_profile(fields: dict[str, str]) -> list[str]:
    """Turn a fetch_company_profile dict into a list of aligned
    ``Label: value`` lines, preserving _PROFILE_FIELDS order.
    """
    if not fields:
        return []
    label_width = max(
        len(label) for key, label in _PROFILE_FIELDS if key in fields
    )
    lines = []
    for key, label in _PROFILE_FIELDS:
        value = fields.get(key)
        if value:
            lines.append(f"{label:<{label_width}}  {value}")
    return lines


def _format_fundamentals(xml: str) -> str:
    """Turn a ReportSnapshot XML blob into a readable key:value block.
    Best-effort — parses a short list of well-known fields; if none
    match (different report type, malformed XML) falls back to a
    tag-stripped preview so the user still sees *something*.
    """
    try:
        from xml.etree import ElementTree as ET

        root = ET.fromstring(xml)
    except Exception:  # noqa: BLE001
        root = None

    lines: list[str] = []
    if root is not None:
        for path, label in _FUNDAMENTAL_FIELDS:
            el = root.find(path)
            if el is None:
                continue
            value = (el.text or "").strip()
            if value:
                lines.append(f"{label}: {value}")
    if lines:
        return "\n".join(lines)

    # Fallback: strip tags and show a preview. Snapshot reports are
    # usually small; cap at 4000 chars so we never flood the panel.
    text = _HTML_TAG_RE.sub(" ", xml)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000] or "(empty)"


class DetailPanel(Vertical):
    """Right-hand column shared by all scanners in a tab. The top half
    renders a fundamentals snapshot for the currently-highlighted
    symbol; the bottom half shows the latest news article inline.

    Loads are debounced — a new ``set_symbol`` cancels any in-flight
    fetches from a previous row so fast cursor movement doesn't pile
    up IB calls.
    """

    DEFAULT_CSS = """
    DetailPanel {
        height: 1fr;
        border-left: solid $primary 30%;
    }
    DetailPanel > Static.section-title {
        background: $primary 20%;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    DetailPanel > ScrollableContainer {
        height: 1fr;
        padding: 0 1;
    }
    DetailPanel > ScrollableContainer > Static {
        width: 100%;
    }
    """

    def __init__(self, ib: IBClient) -> None:
        super().__init__()
        self.ib = ib
        self._profile_title = Static("Company", classes="section-title")
        self._profile_body = Static("(no symbol selected)")
        self._news_title = Static("News", classes="section-title")
        self._news_body = Static("(no symbol selected)")
        self._current_symbol: str | None = None
        self._current_news_id: str | None = None
        self._profile_task: asyncio.Task[None] | None = None
        self._news_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield self._profile_title
        yield ScrollableContainer(self._profile_body, id="profile-scroll")
        yield self._news_title
        yield ScrollableContainer(self._news_body, id="news-scroll")

    def set_symbol(self, symbol: str, news: NewsSummary | None) -> None:
        news_id = news.article_id if news else None
        if symbol == self._current_symbol and news_id == self._current_news_id:
            return
        symbol_changed = symbol != self._current_symbol
        news_changed = news_id != self._current_news_id
        self._current_symbol = symbol
        self._current_news_id = news_id

        if symbol_changed:
            if self._profile_task and not self._profile_task.done():
                self._profile_task.cancel()
            self._profile_title.update(f"Company — {symbol}")
            self._profile_body.update("loading…")
            self._profile_task = asyncio.create_task(self._load_profile(symbol))

        if symbol_changed or news_changed:
            if self._news_task and not self._news_task.done():
                self._news_task.cancel()
            self._news_task = asyncio.create_task(self._load_news(symbol, news))

    async def _load_profile(self, symbol: str) -> None:
        # reqContractDetails is entitlement-free and covers the "who is
        # this company" bits (longName, industry, sector, exchange).
        # We also try reqFundamentalData for richer metrics — silently
        # skipped when the account lacks a subscription (error 10358).
        profile = await self.ib.fetch_company_profile(symbol)
        if symbol != self._current_symbol:
            return
        lines = _format_profile(profile)
        if not lines:
            self._profile_body.update(
                Text("no contract details returned", style="dim")
            )
        else:
            self._profile_body.update(Text("\n".join(lines)))

        ok, text = await self.ib.fetch_fundamentals(symbol)
        if symbol != self._current_symbol:
            return
        if ok:
            extra = _format_fundamentals(text)
            if extra:
                self._profile_body.update(
                    Text("\n".join(lines) + "\n\n" + extra)
                )

    async def _load_news(self, symbol: str, news: NewsSummary | None) -> None:
        if news is None:
            if symbol == self._current_symbol:
                self._news_title.update(f"News — {symbol}")
                self._news_body.update(Text("(no recent news)", style="dim"))
            return
        local_dt = news.time_utc.astimezone()
        stories = f"{news.count} stor{'y' if news.count == 1 else 'ies'}"
        header = (
            f"News — {symbol}  ·  {news.provider_code}  ·  "
            f"{local_dt.strftime('%Y-%m-%d %H:%M')}  ·  {stories}"
        )
        if symbol == self._current_symbol:
            self._news_title.update(header)
            self._news_body.update(Text(f"{news.headline}\n\nloading article…"))
        try:
            article_type, raw = await self.ib.fetch_article(
                news.provider_code, news.article_id
            )
        except Exception as exc:  # noqa: BLE001
            if symbol == self._current_symbol:
                self._news_body.update(
                    Text(f"{news.headline}\n\narticle fetch failed: {exc}", style="dim")
                )
            return
        if symbol != self._current_symbol:
            return
        raw = raw or ""
        body = _strip_html(raw) if (article_type == 1 or _looks_like_html(raw)) else raw
        self._news_body.update(
            Text(f"{news.headline}\n\n{body or '(empty body)'}")
        )


class TabLayout(Horizontal):
    """One tab's contents: a vertical stack of scanner panes on the
    left, a DetailPanel on the right. Width split is configured per
    tab via ``left_ratio``.
    """

    DEFAULT_CSS = """
    TabLayout {
        height: 1fr;
    }
    TabLayout > #scanners-col {
        height: 1fr;
    }
    """

    def __init__(
        self,
        tab_config: TabConfig,
        engine: ScannerEngine,
        alerts: AlertManager | None = None,
    ) -> None:
        super().__init__()
        self.tab_config = tab_config
        self.engine = engine
        self.panes: list[ScannerPane] = [
            ScannerPane(s, engine, alerts=alerts) for s in tab_config.scanners
        ]
        self.detail = DetailPanel(engine.ib)
        # Which pane most recently fired a RowHighlighted event. Used by
        # the `n` keybinding to route "show news" to the right table when
        # a tab has multiple stacked scanners.
        self.last_active_pane: ScannerPane | None = (
            self.panes[0] if self.panes else None
        )

    def compose(self) -> ComposeResult:
        left_pct = int(round(self.tab_config.left_ratio * 100))
        right_pct = 100 - left_pct
        left_col = Vertical(*self.panes, id="scanners-col")
        left_col.styles.width = f"{left_pct}%"
        self.detail.styles.width = f"{right_pct}%"
        yield left_col
        yield self.detail

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        # Events bubble from every DataTable inside this tab; resolve
        # back to the owning pane so we can look up the ScanRow (and
        # its NewsSummary) by cursor index.
        for pane in self.panes:
            if pane.table is event.data_table:
                self.last_active_pane = pane
                row = pane.row_at(event.cursor_row)
                if row is not None:
                    self.detail.set_symbol(row.symbol, row.news)
                event.stop()
                return


class NewsModal(ModalScreen[None]):
    """Fullscreen-ish overlay that shows one article's body. Dismissed
    with Escape. Kept alongside the inline news panel for readers who
    want more room — the inline panel is always live; this one opens
    on demand via the ``n`` keybinding.
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
        self.alerts = AlertManager()
        self.tab_layouts: list[TabLayout] = []

    @property
    def panes(self) -> list[ScannerPane]:
        """Flat view of every ScannerPane across every tab. Used by
        ``action_refresh_all`` and by ``on_mount``'s initial kick.
        """
        return [p for layout in self.tab_layouts for p in layout.panes]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            for t in self.app_config.tabs:
                layout = TabLayout(t, self.engine, alerts=self.alerts)
                self.tab_layouts.append(layout)
                with TabPane(t.name, id=f"tab-{_slug(t.name)}"):
                    yield layout
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
        """Open the latest news article for the row under the cursor of
        the most-recently-active scanner in the current tab. Silent
        no-op when the row has no news attached (likely: scanner config
        doesn't include a "news" column, account has no news
        entitlement, or the symbol genuinely has no recent stories).
        """
        layout = self._active_layout()
        if layout is None:
            self.notify("no active scanner tab", severity="warning")
            return
        pane = layout.last_active_pane
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

    def _active_layout(self) -> "TabLayout | None":
        """Look up the TabLayout inside the currently-active TabPane."""
        tabs = self.query_one(TabbedContent)
        active_id = tabs.active
        if not active_id:
            return None
        for tab_cfg, layout in zip(self.app_config.tabs, self.tab_layouts):
            if f"tab-{_slug(tab_cfg.name)}" == active_id:
                return layout
        return None


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s)
