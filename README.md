# ibscanner

A terminal-UI stock market scanner for **Interactive Brokers**. Define one
or more scanners in YAML, each with its own watchlist, refresh interval,
and a list of expression conditions over technical indicators. The TUI
shows one tab per scanner with a live-updating table of matches.

```
┌─ IBKR Scanners ─────────────────────────────── connected to 127.0.0.1:7497 ─┐
│ [oversold-bounce]  [volume-breakout]  [macd-cross-up]                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ oversold-bounce  |  symbols: 8  |  matches: 2  |  refresh: 30s  |  14:23:11 │
│ Symbol  Match  close    rsi_14  volume    pct_change  sma_20    Note        │
│ AAPL    ✓      175.20   28.40   2.10M     -1.23       176.45                │
│ NVDA    ✓      420.50   25.10   5.81M     -2.30       425.10                │
│ MSFT           330.10   42.80   3.20M     -0.81       332.00                │
│ ...                                                                         │
└─ q quit  r refresh now ─────────────────────────────────────────────────────┘
```

## Stack

- [`ib_async`](https://github.com/ib-api-reloaded/ib_async) — async IBKR client
- [`textual`](https://textual.textualize.io/) — TUI framework
- `pandas` + [`ta`](https://github.com/bukosabino/ta) — indicator pipeline
- `simpleeval` — safe expression evaluation for scanner conditions

## Install

Requires Python 3.10+. Using a virtualenv is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Set up Interactive Brokers

You need either **IB Gateway** (lighter, headless-ish) or **TWS** (full
trading platform) running locally and logged in. IB Gateway is the right
choice for a scanner.

### 1. Get an IBKR account

- Sign up at <https://www.interactivebrokers.com/>. A **paper trading**
  account is free and gives you everything you need to develop scanners.
- After your live account is created, log into Account Management and
  enable a paper trading account from **Settings → Account Settings →
  Paper Trading Account**.

### 2. Install IB Gateway

- Download the **stable** build of IB Gateway:
  <https://www.interactivebrokers.com/en/trading/ibgateway-stable.php>
- Install and launch it.
- Choose **IB API** (not FIX) at the login screen.
- Log in with your **paper** credentials first. The window title should
  read "IB Gateway — Paper Trading".

### 3. Enable the API

In IB Gateway, open **Configure → Settings → API → Settings** and:

- ✅ Check **"Enable ActiveX and Socket Clients"**
- ✅ Check **"Read-Only API"** (this scanner only reads data; leaving it
  read-only protects you from accidental orders)
- ⬜ Uncheck **"Allow connections from localhost only"** *only* if you
  need remote access. Leave it checked for local use.
- **Socket port**: note this number. Defaults are:
  - `4002` — IB Gateway paper
  - `4001` — IB Gateway live
  - `7497` — TWS paper
  - `7496` — TWS live
- **Master API client ID**: leave blank.
- Under **Trusted IPs**, add `127.0.0.1` if it isn't already there.

Click **OK** and let IB Gateway restart if prompted.

### 4. Market data

For testing without paid market data subscriptions, the app defaults to
**delayed data** (`market_data_type: 3` in `scanners.yaml`). Delayed
historical bars for US equities are available without subscriptions.

If you want live data, subscribe in **Account Management → Settings →
User Settings → Market Data Subscriptions** and set
`market_data_type: 1` in your config.

## Configure your scanners

```bash
cp scanners.example.yaml scanners.yaml
$EDITOR scanners.yaml
```

Each scanner has:

| field             | meaning                                                              |
| ----------------- | -------------------------------------------------------------------- |
| `name`            | Tab title in the TUI                                                 |
| `symbols`         | Watchlist of US stock tickers (SMART/USD)                            |
| `bar_size`        | IBKR bar size, e.g. `"1 min"`, `"5 mins"`, `"15 mins"`, `"1 hour"`  |
| `duration`        | How much history to pull, e.g. `"1 D"`, `"2 D"`, `"5 D"`             |
| `refresh_seconds` | How often to re-scan this scanner                                    |
| `conditions`      | List of expression strings, AND-ed together                          |
| `columns`         | Indicator columns to show in the table                               |

### Conditions

Conditions are short Python-style expressions evaluated against the
**latest bar** for each symbol. The previous bar is exposed under
`prev_*` so you can express crosses.

Available names (auto-computed by `ibscanner/indicators.py`):

```
close, open, high, low, volume
sma_5, sma_10, sma_20, sma_50, sma_200
ema_9, ema_12, ema_26
rsi_14
macd, macd_signal, macd_hist
bb_upper, bb_mid, bb_lower
atr_14
volume_sma_20, volume_ratio
pct_change
```

…and the same names with a `prev_` prefix for the previous bar.

Examples:

```yaml
conditions:
  # Oversold pullback that's still above its 20-bar mean
  - rsi_14 < 30
  - close > sma_20 * 0.97

  # MACD cross up between previous bar and this bar
  - macd > macd_signal
  - prev_macd <= prev_macd_signal

  # Volume spike with positive price action
  - volume_ratio > 2
  - close > prev_close

  # Trend filter
  - close > sma_50
  - sma_50 > sma_200
```

All conditions in a scanner must be true for the symbol to be marked
as a match.

### Scanner types

Two types are supported via the `type` field:

#### `watchlist` (default)

A fixed list of symbols. Bars are fetched per refresh, indicators
computed locally, and `conditions` evaluated. Everything described
above is the watchlist scanner.

#### `ibkr_scan`

A market-wide universe scan via IBKR's `reqScannerSubscription`. You
pick a `scan_code` (the primary sort key, e.g. `TOP_PERC_GAIN`) and
narrow it with `filters` — a dict of IBKR filter tag codes to values.
IBKR returns up to 50 ranked results.

```yaml
- name: low-float-runners
  type: ibkr_scan
  scan_code: TOP_PERC_GAIN
  instrument: STK
  location_code: STK.US.MAJOR
  refresh_seconds: 10
  filters:
    priceAbove: 2
    priceBelow: 12
    changePercAbove: 10
    floatSharesBelow: 20000000
    volumeVsAvgAbove: 500   # appears to be percent: 500 = 5x avg
  columns: [rank, projection, exchange]
```

| field           | meaning                                                                |
| --------------- | ---------------------------------------------------------------------- |
| `scan_code`     | IBKR scan code, e.g. `TOP_PERC_GAIN`, `HOT_BY_VOLUME`, `MOST_ACTIVE`   |
| `instrument`    | Instrument type, e.g. `STK`, `STOCK.HK`, `BOND`                        |
| `location_code` | Universe to scan, e.g. `STK.US.MAJOR` (NYSE/NASDAQ/AMEX), `STK.US.MINOR` (OTC) |
| `filters`       | Dict of `<filterCode>: <value>` pairs (passed as IBKR `TagValue`s)     |

The full catalog of valid `scan_code`s and `filters` keys lives inside
IBKR — dump it with:

```bash
.venv/bin/python scripts/explore_scanner_params.py --port 4002
```

This writes `scanner_params.xml` (raw) and `scanner_params.md`
(focused summary by category) so you can look up exact tag names
without guessing.

Optionally add `post_conditions` — a list of expressions just like
`conditions` for watchlist scanners. When present, the engine will
fetch historical bars for each IBKR result and evaluate the expressions
against the indicator pipeline, so you can layer custom rules on top of
the IBKR universe scan:

```yaml
- name: gainers-with-rsi
  type: ibkr_scan
  scan_code: TOP_PERC_GAIN
  filters: { priceAbove: 5, changePercAbove: 5 }
  post_conditions:
    - rsi_14 < 70           # filter out already-overbought ones
    - volume_ratio > 3
  columns: [projection, rsi_14, volume_ratio]
```

Available column names for `ibkr_scan` (without post_conditions):
`symbol`, `rank`, `projection` (the value the scan sorted by, e.g. %
change for `TOP_PERC_GAIN`), `exchange`, `distance`, `benchmark`. With
`post_conditions`, all the indicator columns from the watchlist
section are also available.

> **Note on cadence**: IBKR's scanner refreshes server-side roughly
> every 30 seconds. A `refresh_seconds: 10` will mostly return identical
> data — useful for tight iteration during dev, wasteful in steady state.

## Run

Make sure IB Gateway is running and logged in, then:

```bash
ibscanner            # uses ./scanners.yaml
ibscanner -c my.yaml # custom config path
```

Hotkeys:

- `q` — quit
- `r` — refresh every scanner now (don't wait for interval)
- Click a tab or use arrow keys to switch scanners.

## Project layout

```
.
├── pyproject.toml
├── README.md
├── scanners.example.yaml
└── src/ibscanner/
    ├── __init__.py
    ├── __main__.py     # CLI entry point
    ├── config.py       # YAML loader → dataclasses
    ├── ib_client.py    # ib_async wrapper, fetches historical bars
    ├── indicators.py   # OHLCV → enriched DataFrame with indicators
    ├── engine.py       # runs each scanner: fetch → enrich → evaluate
    └── tui.py          # Textual app: one tab per scanner
```

## Notes & limitations

- **Sequential fetches per scanner.** Symbols within a scanner are
  fetched one at a time to stay polite with IBKR's rate limits. Two
  scanners with overlapping symbols will fetch the same data twice;
  shared cache is a future improvement.
- **Historical bars only.** The current refresh model re-pulls
  historical bars on each cycle rather than subscribing to streaming
  ticks. This keeps the design simple and works fine for refresh
  intervals ≥ ~10s. A streaming mode would be a worthwhile addition.
- **No alerting.** Matches are shown in the TUI only — no sound, no
  push, no logging. Easy to add hooks in `ScannerPane._render`.
- **US equities only by default.** `IBClient._qualify` constructs
  `Stock(symbol, "SMART", "USD")`. Extend it if you want futures, FX,
  or non-US stocks.
