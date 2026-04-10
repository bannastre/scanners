"""Indicator enrichment.

Given an OHLCV DataFrame from IBKR, return a copy with extra columns
that scanner conditions can reference by name.
"""

from __future__ import annotations

import pandas as pd
import ta


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    for n in (5, 10, 20, 50, 200):
        out[f"sma_{n}"] = close.rolling(n).mean()

    for n in (9, 12, 26):
        out[f"ema_{n}"] = close.ewm(span=n, adjust=False).mean()

    out["rsi_14"] = ta.momentum.RSIIndicator(close, window=14).rsi()

    macd = ta.trend.MACD(close)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()

    bb = ta.volatility.BollingerBands(close, window=20)
    out["bb_upper"] = bb.bollinger_hband()
    out["bb_lower"] = bb.bollinger_lband()
    out["bb_mid"] = bb.bollinger_mavg()

    out["atr_14"] = ta.volatility.AverageTrueRange(
        high, low, close, window=14
    ).average_true_range()

    out["volume_sma_20"] = volume.rolling(20).mean()
    out["volume_ratio"] = volume / out["volume_sma_20"]

    out["pct_change"] = close.pct_change() * 100

    return out
