"""Thin async wrapper around ib_async for bars and market scanner queries."""

from __future__ import annotations

import asyncio
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
