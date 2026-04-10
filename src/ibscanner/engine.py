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
class ScanRow:
    symbol: str
    matched: bool
    values: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


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

        for symbol in cfg.symbols:
            try:
                df = await self.ib.fetch_bars(
                    symbol,
                    duration=cfg.duration,
                    bar_size=cfg.bar_size,
                    what_to_show=cfg.what_to_show,
                    use_rth=cfg.use_rth,
                )
                if df.empty:
                    rows.append(ScanRow(symbol, False, {}, "no data"))
                    continue

                names = self._enriched_names(df, symbol)
                matched = self._evaluate(cfg.conditions, names)
                rows.append(ScanRow(symbol, matched, names))
            except Exception as exc:  # noqa: BLE001 - surface to UI
                rows.append(ScanRow(symbol, False, {}, f"{type(exc).__name__}: {exc}"))

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
        candidates = scan_data[: cfg.max_results]

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

            if not needs_bars:
                # Pure IBKR scan, no enrichment: row is the scan data only.
                rows.append(ScanRow(symbol, True, base))
                continue

            try:
                df = await self.ib.fetch_bars(
                    symbol,
                    duration=cfg.duration,
                    bar_size=cfg.bar_size,
                    what_to_show=cfg.what_to_show,
                    use_rth=cfg.use_rth,
                )
                if df.empty:
                    rows.append(ScanRow(symbol, False, base, "no bars"))
                    continue
                base.update(self._enriched_names(df, symbol))
                matched = self._evaluate(cfg.post_conditions, base)
                rows.append(ScanRow(symbol, matched, base))
            except Exception as exc:  # noqa: BLE001
                rows.append(ScanRow(symbol, False, base, f"{type(exc).__name__}: {exc}"))

        return ScanResult(
            name=cfg.name,
            rows=rows,
            ran_at=start,
            duration_s=(datetime.now() - start).total_seconds(),
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
