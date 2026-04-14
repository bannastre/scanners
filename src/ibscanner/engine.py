"""Scanner engine: fetches bars, enriches with indicators, evaluates rules."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from simpleeval import SimpleEval

from .config import ScannerConfig
from .ib_client import IBClient
from .indicators import enrich


@dataclass
class NewsSummary:
    """Reduced view of a symbol's recent news.

    Mirrors the shape used by the React scanner: one "latest" story
    pointer (so the TUI can pop the body on demand), plus a count of
    how many stories came back in the most recent fetch window. That
    count feeds the "n stories in feed" hint in the modal.
    """

    article_id: str
    provider_code: str
    headline: str
    time_utc: datetime
    count: int


@dataclass
class ScanRow:
    symbol: str
    matched: bool
    values: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # Populated only when the scanner opted into news enrichment via a
    # "news" entry in `columns`. None means "column requested but nothing
    # came back" (no entitlement, no stories, or fetch failed).
    news: NewsSummary | None = None


@dataclass
class ScanResult:
    name: str
    rows: list[ScanRow]
    ran_at: datetime
    duration_s: float


class ScannerEngine:
    def __init__(self, ib: IBClient) -> None:
        self.ib = ib

    async def run(self, cfg: ScannerConfig) -> ScanResult:
        if cfg.type == "ibkr_scan":
            return await self._run_ibkr_scan(cfg)
        return await self._run_watchlist(cfg)

    async def _run_watchlist(self, cfg: ScannerConfig) -> ScanResult:
        start = datetime.now()
        rows: list[ScanRow] = []
        needs_news = "news" in cfg.columns
        needs_last = "last" in cfg.columns

        for symbol in cfg.symbols:
            contract = None
            try:
                df = await self.ib.fetch_bars(
                    symbol,
                    duration=cfg.duration,
                    bar_size=cfg.bar_size,
                    what_to_show=cfg.what_to_show,
                    use_rth=cfg.use_rth,
                )
                if df.empty:
                    row = ScanRow(symbol, False, {}, "no data")
                else:
                    names = self._enriched_names(df, symbol)
                    if needs_last:
                        contract = await self.ib._qualify(symbol)
                        last = await self.ib.fetch_last_price(contract)
                        if last is not None:
                            names["last"] = last
                    matched = self._evaluate(cfg.conditions, names)
                    row = ScanRow(symbol, matched, names)
            except Exception as exc:  # noqa: BLE001 - surface to UI
                row = ScanRow(symbol, False, {}, f"{type(exc).__name__}: {exc}")

            # News is best-effort — don't let a provider flake sink the
            # row. Runs even on the "no data" path so the dot can still
            # flag recent catalysts for symbols whose bars errored out.
            if needs_news:
                row.news = await self._fetch_news(contract or symbol)
            rows.append(row)

        return ScanResult(
            name=cfg.name,
            rows=rows,
            ran_at=start,
            duration_s=(datetime.now() - start).total_seconds(),
        )

    async def _run_ibkr_scan(self, cfg: ScannerConfig) -> ScanResult:
        start = datetime.now()
        rows: list[ScanRow] = []

        try:
            scan_data = await self.ib.scan(
                instrument=cfg.instrument,
                location_code=cfg.location_code,
                scan_code=cfg.scan_code,
                filters=cfg.filters,
            )
        except Exception as exc:  # noqa: BLE001 - surface to UI
            return ScanResult(
                name=cfg.name,
                rows=[
                    ScanRow(
                        symbol="(scan failed)",
                        matched=False,
                        values={},
                        error=f"{type(exc).__name__}: {exc}",
                    )
                ],
                ran_at=start,
                duration_s=(datetime.now() - start).total_seconds(),
            )

        # IBKR's stock scans only populate symbol/rank/marketName — most
        # other ScanData fields (projection, distance, benchmark) come back
        # empty. So we enrich each result with a historical-bar fetch when
        # `enrich` is on (default) or when post_conditions need indicator
        # data, capping at max_results to keep request volume sane.
        needs_bars = cfg.enrich or bool(cfg.post_conditions)
        needs_news = "news" in cfg.columns
        # Any snapshot-derivable column triggers the one batched snapshot
        # call; we also pull it when the user didn't ask for `last` but
        # did ask for `pct_change`, because the snapshot-derived pct_change
        # matches IBKR's ranking metric (bar-derived doesn't, outside RTH).
        snapshot_columns = {"last", "prev_close", "pct_change"}
        needs_snapshot = bool(snapshot_columns & set(cfg.columns))
        candidates = scan_data[: cfg.max_results]

        # Batched snapshot upfront: one reqTickers round-trip instead
        # of one per row. Matters because every IB call goes through
        # a shared lock, so per-row snapshots block the bar/news calls
        # queued behind them.
        snapshots: dict[int, dict[str, float | None]] = {}
        if needs_snapshot:
            snapshots = await self.ib.fetch_last_prices(
                [sd.contractDetails.contract for sd in candidates]
            )

        for sd in candidates:
            contract = sd.contractDetails.contract
            cd = sd.contractDetails
            symbol = contract.symbol
            market_name = getattr(cd, "marketName", "") or ""
            base: dict[str, Any] = {
                "symbol": symbol,
                "rank": sd.rank,
                "exchange": (
                    contract.primaryExchange or market_name or contract.exchange
                ),
                "market_name": market_name,
                "projection": _to_number(sd.projection),
                "distance": sd.distance,
                "benchmark": sd.benchmark,
            }

            snap = snapshots.get(contract.conId) or {}
            if snap.get("last") is not None:
                base["last"] = snap["last"]
            if snap.get("prev_close") is not None:
                base["prev_close"] = snap["prev_close"]

            if not needs_bars:
                row = ScanRow(symbol, True, base)
            else:
                try:
                    df = await self.ib.fetch_bars(
                        symbol,
                        duration=cfg.duration,
                        bar_size=cfg.bar_size,
                        what_to_show=cfg.what_to_show,
                        use_rth=cfg.use_rth,
                    )
                    if df.empty:
                        row = ScanRow(symbol, False, base, "no bars")
                    else:
                        base.update(self._enriched_names(df, symbol))
                        matched = self._evaluate(cfg.post_conditions, base)
                        row = ScanRow(symbol, matched, base)
                except Exception as exc:  # noqa: BLE001
                    row = ScanRow(
                        symbol, False, base, f"{type(exc).__name__}: {exc}"
                    )

            # Override bar-derived pct_change with snapshot-derived when
            # both snapshot fields are present. Same formula IBKR uses
            # server-side for TOP_PERC_GAIN ranking, so the column now
            # agrees with the row order. Applied after bar enrichment
            # so it wins over the stale-daily-bar computation.
            last = snap.get("last")
            prev = snap.get("prev_close")
            if last is not None and prev:
                base["pct_change"] = (last - prev) / prev * 100

            # Contract came back already qualified from the scan result,
            # so pass it through and skip the qualify round-trip.
            if needs_news:
                row.news = await self._fetch_news(contract)
            rows.append(row)

        return ScanResult(
            name=cfg.name,
            rows=rows,
            ran_at=start,
            duration_s=(datetime.now() - start).total_seconds(),
        )

    async def _fetch_news(self, symbol_or_contract) -> NewsSummary | None:
        """Pull the latest few news items and reduce to a NewsSummary.
        Returns None for any failure path (no entitlement, no stories,
        unqualifiable symbol) — callers render that as "no dot".
        """
        items = await self.ib.fetch_news(symbol_or_contract, total_results=5)
        if not items:
            return None
        latest = max(items, key=lambda n: n["time_utc"])
        return NewsSummary(
            article_id=latest["article_id"],
            provider_code=latest["provider_code"],
            headline=latest["headline"],
            time_utc=latest["time_utc"],
            count=len(items),
        )

    @staticmethod
    def _enriched_names(df, symbol: str) -> dict[str, Any]:
        enriched = enrich(df)
        latest = enriched.iloc[-1]
        prev = enriched.iloc[-2] if len(enriched) >= 2 else latest
        names: dict[str, Any] = {"symbol": symbol}
        for col in enriched.columns:
            if col == "time":
                continue
            names[col] = _safe(latest[col])
            names[f"prev_{col}"] = _safe(prev[col])
        return names

    @staticmethod
    def _evaluate(conditions: list[str], names: dict[str, Any]) -> bool:
        if not conditions:
            return True
        evaluator = SimpleEval(names=names)
        for expr in conditions:
            try:
                if not _truthy(evaluator.eval(expr)):
                    return False
            except Exception:
                return False
        return True


def _safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    return bool(value)


def _to_number(value: Any) -> Any:
    """IBKR scan projections come back as strings — coerce to float when possible."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value
