"""Config loading for ibscanner.

Reads a YAML file describing the IBKR connection and one or more named
scanners. Two scanner types are supported:

- ``watchlist`` (default): a fixed list of symbols whose bars are fetched
  every refresh and evaluated against ``conditions``.
- ``ibkr_scan``: a market-wide scan via ``reqScannerSubscription`` using
  an IBKR scan code plus filter tags. Optional ``post_conditions`` will
  trigger a per-result historical-bar fetch and run the same indicator
  pipeline on top, so you can layer custom rules on the IBKR universe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
class IBConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 17
    market_data_type: int = 3  # 3 = delayed, safe default w/o subscriptions


@dataclass
class AppConfig:
    ib: IBConfig = field(default_factory=IBConfig)
    scanners: list[ScannerConfig] = field(default_factory=list)
    # Textual theme name (e.g. "nord", "gruvbox", "tokyo-night", "dracula",
    # "monokai", "solarized-light", "flexoki", "catppuccin-mocha",
    # "catppuccin-latte", "textual-dark", "textual-light"). None = Textual default.
    theme: str | None = None


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text()) or {}
    ib_raw = raw.get("ib", {}) or {}
    ib = IBConfig(
        host=ib_raw.get("host", "127.0.0.1"),
        port=int(ib_raw.get("port", 7497)),
        client_id=int(ib_raw.get("client_id", 17)),
        market_data_type=int(ib_raw.get("market_data_type", 3)),
    )

    # Type-specific defaults so users don't have to spell out bar_size/
    # duration/columns differently for watchlist vs ibkr_scan.
    type_defaults = {
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

    scanners: list[ScannerConfig] = []
    for s in raw.get("scanners", []) or []:
        scanner_type = str(s.get("type", "watchlist"))
        d = type_defaults.get(scanner_type, type_defaults["watchlist"])
        scanners.append(
            ScannerConfig(
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
            )
        )

    theme = raw.get("theme")
    return AppConfig(ib=ib, scanners=scanners, theme=str(theme) if theme else None)
