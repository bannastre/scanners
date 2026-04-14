"""Thin async wrapper around ib_async for bars and market scanner queries."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from ib_async import IB, ScanData, ScannerSubscription, Stock, TagValue, util


class IBClient:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        market_data_type: int = 3,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.market_data_type = market_data_type
        self.ib = IB()
        # ib_async qualifyContracts is not perfectly thread-safe across
        # concurrent callers; serialise per-symbol fetches with a lock.
        self._lock = asyncio.Lock()
        self._qualified: dict[str, Stock] = {}
        # News-provider codes ("BRFG+BRFUPDN+…") are constant per account
        # and the lookup call isn't free; fetch lazily and cache.
        self._news_provider_codes: str | None = None

    async def connect(self) -> None:
        await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
        # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
        self.ib.reqMarketDataType(self.market_data_type)

    async def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()

    async def _qualify(self, symbol: str) -> Stock:
        if symbol in self._qualified:
            return self._qualified[symbol]
        contract = Stock(symbol, "SMART", "USD")
        await self.ib.qualifyContractsAsync(contract)
        self._qualified[symbol] = contract
        return contract

    async def fetch_bars(
        self,
        symbol: str,
        duration: str,
        bar_size: str,
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> pd.DataFrame:
        async with self._lock:
            contract = await self._qualify(symbol)
            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
            )
        if not bars:
            return pd.DataFrame()
        df = util.df(bars)
        if df is None:
            return pd.DataFrame()
        return df.rename(columns={"date": "time"})

    async def scan(
        self,
        instrument: str,
        location_code: str,
        scan_code: str,
        filters: dict[str, Any],
        number_of_rows: int = 50,
    ) -> list[ScanData]:
        """One-shot market scan via reqScannerData.

        ``filters`` is a dict of IBKR scanner filter codes (e.g.
        ``priceAbove``, ``floatSharesBelow``) → values. Values are
        coerced to strings as IBKR's TagValue API expects.
        """
        sub = ScannerSubscription(
            numberOfRows=number_of_rows,
            instrument=instrument,
            locationCode=location_code,
            scanCode=scan_code,
        )
        tag_values = [TagValue(str(k), str(v)) for k, v in filters.items()]
        async with self._lock:
            return await self.ib.reqScannerDataAsync(sub, [], tag_values)

    async def fetch_last_prices(
        self, contracts: list[Stock]
    ) -> dict[int, dict[str, float | None]]:
        """Batched snapshot keyed by ``conId``.

        ``reqTickersAsync`` accepts many contracts in one call and does
        the whole batch under a single lock acquisition, so a 15-row
        scan goes from 15 sequential round-trips to one. That matters
        because ``IBClient._lock`` serialises every IB call app-wide:
        every per-row snapshot blocks news/bar calls behind it.

        Returns ``{conId: {"last": …, "prev_close": …}}`` with an entry
        for every input contract. Both fields are ``None`` when the
        snapshot didn't carry them (halt, no subscription, etc.).

        Why both: ``ticker.close`` in ib_async is TWS tick type 9 —
        the *prior day's* official close, which is exactly the reference
        IBKR uses for its server-side change-% metrics. Pairing that
        with the live ``last`` lets callers compute an intraday change %
        that matches IBKR's scanner ranking, whereas a bar-derived
        pct_change drifts outside RTH (no in-progress daily bar yet).
        """
        if not contracts:
            return {}
        result: dict[int, dict[str, float | None]] = {
            c.conId: {"last": None, "prev_close": None} for c in contracts
        }
        try:
            async with self._lock:
                tickers = await self.ib.reqTickersAsync(*contracts)
        except Exception:  # noqa: BLE001
            return result
        for t in tickers or []:
            conid = getattr(getattr(t, "contract", None), "conId", 0)
            if not conid:
                continue
            result[conid]["last"] = _finite(getattr(t, "last", None))
            result[conid]["prev_close"] = _finite(getattr(t, "close", None))
        return result

    async def fetch_last_price(self, contract: Stock) -> float | None:
        """Convenience single-contract wrapper returning just ``last``.
        Used by the watchlist path where there's no natural batching
        point; ibkr_scan calls ``fetch_last_prices`` directly so it
        can also use ``prev_close`` for the change-% column.
        """
        snapshots = await self.fetch_last_prices([contract])
        snap = snapshots.get(contract.conId) or {}
        return snap.get("last")

    async def _news_providers(self) -> str:
        """Return the "+"-joined provider code string TWS expects for
        reqHistoricalNews. Empty string if the account has no news
        entitlements — callers can treat that as "news not available".
        """
        if self._news_provider_codes is not None:
            return self._news_provider_codes
        try:
            providers = await self.ib.reqNewsProvidersAsync()
        except Exception:  # noqa: BLE001 — absence of news ≠ hard error
            providers = []
        codes = "+".join(p.code for p in providers or [])
        self._news_provider_codes = codes
        return codes

    async def fetch_news(
        self,
        symbol_or_contract: str | Stock,
        total_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Latest news items for a symbol or a pre-qualified contract.

        Each item is a plain dict with ``article_id``, ``provider_code``,
        ``headline``, ``time_utc`` (aware ``datetime``). Empty list when
        the account has no news entitlement, the symbol is unknown, or
        TWS rejects the call — the scanner surfaces this as "no dot"
        rather than failing the row.
        """
        providers = await self._news_providers()
        if not providers:
            return []

        if isinstance(symbol_or_contract, str):
            async with self._lock:
                contract = await self._qualify(symbol_or_contract)
        else:
            contract = symbol_or_contract

        conid = getattr(contract, "conId", 0)
        if not conid:
            return []

        try:
            async with self._lock:
                raw = await self.ib.reqHistoricalNewsAsync(
                    conid, providers, "", "", total_results
                )
        except Exception:  # noqa: BLE001
            return []

        out: list[dict[str, Any]] = []
        for n in raw or []:
            t = _parse_news_time(getattr(n, "time", ""))
            if t is None:
                continue
            out.append(
                {
                    "article_id": n.articleId,
                    "provider_code": n.providerCode,
                    "headline": n.headline,
                    "time_utc": t,
                }
            )
        return out

    async def fetch_article(
        self,
        provider_code: str,
        article_id: str,
    ) -> tuple[int, str]:
        """Fetch the full body of a news article.

        Returns ``(article_type, text)`` where ``article_type`` is 0 for
        plain text and 1 for HTML (per the TWS API).
        """
        async with self._lock:
            article = await self.ib.reqNewsArticleAsync(provider_code, article_id)
        return int(getattr(article, "articleType", 0)), getattr(article, "articleText", "") or ""


def _finite(v: Any) -> float | None:
    """Coerce ``v`` to a finite float or return None. ib_async fills
    unset ticker fields with NaN (not None), so a straight float()
    conversion would happily return NaN and propagate it through the
    downstream math.
    """
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv):
        return None
    return fv


def _parse_news_time(value: Any) -> datetime | None:
    """TWS historical-news timestamps are UTC, but the wire type drifts
    across ib_async builds: newer releases hand back a ``datetime`` while
    older ones send the raw TWS string ("YYYY-MM-DD HH:MM:SS[.f]" or the
    dateless "YYYYMMDD HH:MM:SS" variant). Accept both and coerce to an
    aware UTC datetime; return None for anything we can't parse so the
    row just renders as "no dot".
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        # Naive datetimes from ib_async are UTC by convention.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d %H:%M:%S.%f",
        "%Y%m%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
