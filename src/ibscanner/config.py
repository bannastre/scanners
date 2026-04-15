"""Config loading for ibscanner.

Reads a YAML file describing the IBKR connection and one or more named
scanners. Two scanner types are supported:

- ``watchlist`` (default): a fixed list of symbols whose bars are fetched
  every refresh and evaluated against ``conditions``.
- ``ibkr_scan``: a market-wide scan via ``reqScannerSubscription`` using
  an IBKR scan code plus filter tags. Optional ``post_conditions`` will
  trigger a per-result historical-bar fetch and run the same indicator
  pipeline on top, so you can layer custom rules on the IBKR universe.

Scanners are grouped into tabs. Each tab holds up to 3 stacked scanners
on its left pane, with a fundamentals + news detail column on the right
driven by the cursor-row symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


MAX_SCANNERS_PER_TAB = 3


@dataclass
class ScannerConfig:
    name: str
    type: str = "watchlist"  # "watchlist" | "ibkr_scan"

    # Common
    refresh_seconds: int = 30
    columns: list[str] = field(
        default_factory=lambda: ["close", "rsi_14", "volume", "pct_change"]
    )

    # Watchlist type
    symbols: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)

    # Bar fetching (used by watchlist, and by ibkr_scan when post_conditions
    # are present so we can compute indicators on candidates).
    bar_size: str = "5 mins"
    duration: str = "2 D"
    what_to_show: str = "TRADES"
    use_rth: bool = True

    # ibkr_scan type
    scan_code: str = ""
    instrument: str = "STK"
    location_code: str = "STK.US.MAJOR"
    filters: dict[str, Any] = field(default_factory=dict)
    post_conditions: list[str] = field(default_factory=list)
    # When True, fetch historical bars for each scan result and populate
    # indicator columns. Required for any column beyond {symbol, rank,
    # exchange, market_name}, since IBKR's scan API doesn't return
    # projection/last/volume on stock scans.
    enrich: bool = True
    # Cap how many of the scan results we enrich (IBKR returns up to 50).
    max_results: int = 50

    # Play the system "Glass" sound when a row appears whose news story
    # is <2h old (the "HOT!" bucket) and wasn't present in the previous
    # refresh. No-op without `news` in `columns`. Per-scanner so you
    # can wire alerts on a runners feed but stay quiet on watchlists.
    alert_on_hot_news: bool = False

    def __post_init__(self) -> None:
        if self.type not in ("watchlist", "ibkr_scan"):
            raise ValueError(
                f"scanner {self.name!r}: unknown type {self.type!r} "
                "(expected 'watchlist' or 'ibkr_scan')"
            )
        if self.type == "watchlist" and not self.symbols:
            raise ValueError(
                f"scanner {self.name!r}: watchlist type requires `symbols`"
            )
        if self.type == "ibkr_scan" and not self.scan_code:
            raise ValueError(
                f"scanner {self.name!r}: ibkr_scan type requires `scan_code`"
            )


@dataclass
class TabConfig:
    name: str
    scanners: list[ScannerConfig] = field(default_factory=list)
    # Left-pane width as a fraction of the tab's full width (0, 1).
    # Right pane gets the remainder and hosts fundamentals + news.
    left_ratio: float = 0.6

    def __post_init__(self) -> None:
        if not self.scanners:
            raise ValueError(f"tab {self.name!r}: needs at least one scanner")
        if len(self.scanners) > MAX_SCANNERS_PER_TAB:
            raise ValueError(
                f"tab {self.name!r}: at most {MAX_SCANNERS_PER_TAB} scanners "
                f"per tab, got {len(self.scanners)}"
            )
        if not 0.1 <= self.left_ratio <= 0.9:
            raise ValueError(
                f"tab {self.name!r}: left_ratio must be between 0.1 and 0.9, "
                f"got {self.left_ratio}"
            )


@dataclass
class IBConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 17
    market_data_type: int = 3  # 3 = delayed, safe default w/o subscriptions


@dataclass
class AppConfig:
    ib: IBConfig = field(default_factory=IBConfig)
    tabs: list[TabConfig] = field(default_factory=list)
    # Textual theme name (e.g. "nord", "gruvbox", "tokyo-night", "dracula",
    # "monokai", "solarized-light", "flexoki", "catppuccin-mocha",
    # "catppuccin-latte", "textual-dark", "textual-light"). None = Textual default.
    theme: str | None = None

    @property
    def scanners(self) -> list[ScannerConfig]:
        """Flat view across all tabs — useful for callers that just want
        every scanner (e.g. "refresh all"). Preserves declaration order.
        """
        return [s for t in self.tabs for s in t.scanners]


# Type-specific defaults so users don't have to spell out bar_size/
# duration/columns differently for watchlist vs ibkr_scan.
_TYPE_DEFAULTS = {
    "watchlist": {
        "bar_size": "5 mins",
        "duration": "2 D",
        "columns": ["close", "rsi_14", "volume", "pct_change"],
    },
    "ibkr_scan": {
        "bar_size": "1 day",
        "duration": "30 D",
        "columns": ["close", "pct_change", "volume", "volume_ratio"],
    },
}


def _load_scanner(s: dict[str, Any]) -> ScannerConfig:
    scanner_type = str(s.get("type", "watchlist"))
    d = _TYPE_DEFAULTS.get(scanner_type, _TYPE_DEFAULTS["watchlist"])
    return ScannerConfig(
        name=str(s["name"]),
        type=scanner_type,
        refresh_seconds=int(s.get("refresh_seconds", 30)),
        columns=[str(c) for c in s.get("columns", [])] or list(d["columns"]),
        symbols=[str(sym).upper() for sym in s.get("symbols", [])],
        conditions=[str(c) for c in s.get("conditions", [])],
        bar_size=str(s.get("bar_size", d["bar_size"])),
        duration=str(s.get("duration", d["duration"])),
        what_to_show=str(s.get("what_to_show", "TRADES")),
        use_rth=bool(s.get("use_rth", True)),
        scan_code=str(s.get("scan_code", "")),
        instrument=str(s.get("instrument", "STK")),
        location_code=str(s.get("location_code", "STK.US.MAJOR")),
        filters=dict(s.get("filters", {}) or {}),
        post_conditions=[str(c) for c in s.get("post_conditions", [])],
        enrich=bool(s.get("enrich", True)),
        max_results=int(s.get("max_results", 50)),
        alert_on_hot_news=bool(s.get("alert_on_hot_news", False)),
    )


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text()) or {}
    ib_raw = raw.get("ib", {}) or {}
    ib = IBConfig(
        host=ib_raw.get("host", "127.0.0.1"),
        port=int(ib_raw.get("port", 7497)),
        client_id=int(ib_raw.get("client_id", 17)),
        market_data_type=int(ib_raw.get("market_data_type", 3)),
    )

    tabs: list[TabConfig] = []

    # New shape: explicit `tabs:` list, each with its own `scanners:`.
    raw_tabs = raw.get("tabs")
    if raw_tabs:
        for t in raw_tabs:
            scanners = [_load_scanner(s) for s in t.get("scanners", []) or []]
            tabs.append(
                TabConfig(
                    name=str(t["name"]),
                    scanners=scanners,
                    left_ratio=float(t.get("left_ratio", 0.6)),
                )
            )
    else:
        # Legacy flat shape: each top-level scanner becomes its own tab.
        # Preserves existing configs; the TUI just renders one scanner per
        # tab in that case, with the detail column still driven by cursor.
        for s in raw.get("scanners", []) or []:
            scanner = _load_scanner(s)
            tabs.append(TabConfig(name=scanner.name, scanners=[scanner]))

    theme = raw.get("theme")
    return AppConfig(ib=ib, tabs=tabs, theme=str(theme) if theme else None)
