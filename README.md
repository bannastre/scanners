# ibscanner

A real-time stock scanner for **Interactive Brokers**. Define scanners in
YAML — fixed watchlists with custom indicator rules, or universe-wide
IBKR market scans — and watch the results cycle in a React UI. Each
scanner refreshes on its own interval. Everything runs in the browser;
there is no backend.

## Stack

```
┌─────────────────────┐       HTTPS        ┌──────────────────┐   HTTPS   ┌─────────────┐
│  React app (Vite)   │───────────────────▶│  Client Portal   │──────────▶│ IBKR cloud  │
│  localhost:5173     │                    │  Gateway         │           │             │
│  (dev server)       │                    │  localhost:5001  │           │             │
└─────────────────────┘                    └──────────────────┘           └─────────────┘
   │
   ├─ parses scanner config in-browser (js-yaml)
   ├─ fetches bars + scanner results directly (fetch)
   ├─ computes indicators in-browser (technicalindicators)
   ├─ evaluates filter expressions (filtrex)
   └─ runs each scanner on its own setInterval loop
```

- **UI** — React 18 + Vite. Tabs-and-table layout, one tab per scanner
  plus an in-app YAML config editor.
- **Indicators** —
  [`technicalindicators`](https://www.npmjs.com/package/technicalindicators)
  (SMA, EMA, RSI, MACD, Bollinger Bands, ATR).
- **Expression eval** —
  [`filtrex`](https://www.npmjs.com/package/filtrex) (~5KB). User-
  authored filter expressions are compiled against a named-value scope
  with no access to globals or the runtime.
- **YAML config** —
  [`js-yaml`](https://www.npmjs.com/package/js-yaml). Config is edited
  in-app and persisted to `localStorage`.

## Prerequisites

- **Node 20+**
- **An Interactive Brokers account.** A paper-trading account is free
  after sign-up at <https://www.interactivebrokers.com/> and gives you
  everything needed for development.
- **Client Portal Gateway** — a Java bundle IBKR ships separately that
  terminates a local REST endpoint for the Web API. See
  [Gateway setup](#gateway-setup).

## Install

```bash
cd client && npm install
```

## Gateway setup

IBKR's Web API does **not** expose a retail-accessible cloud endpoint.
All REST calls go through **Client Portal Gateway** — a Java app you run
locally that handles session auth, terminates TLS on
`https://localhost:5001`, and proxies requests to IBKR's cloud.

### Download

1. On the IBKR site, navigate to *Trading > Tools > APIs > Client Portal
   API* and download `clientportal.gw.zip`. (The URL changes; search
   "IBKR Client Portal Gateway" if in doubt.)
2. Unzip:
   ```bash
   unzip clientportal.gw.zip -d ~/ibkr-gateway
   ```

### Configure CORS

The React app makes `fetch()` calls from `http://localhost:5173` (Vite
dev server) to the gateway on a different port. The gateway must allow
this origin.

Edit `~/ibkr-gateway/root/conf.yaml`:

```yaml
ips:
  allow:
    - 127.0.0.1
cors: "*"            # or "http://localhost:5173" for tighter lockdown
```

### Run the gateway

```bash
cd ~/ibkr-gateway && bin/run.sh root/conf.yaml     # macOS / Linux
# or: bin\run.bat root\conf.yaml                    # Windows
```

You should see log lines ending with a listener on port 5001.

### Log in

Open `https://localhost:5001` in the **same browser** where you'll use
ibscanner. Accept the self-signed certificate warning, then log in with
your IBKR credentials (paper account works).

> **Same browser** matters — the gateway session is cookie-based and
> ibscanner reuses it via `credentials: 'include'` on every `fetch()`.

### Verify

```bash
curl -sk https://localhost:5001/v1/api/iserver/auth/status | jq
# { "authenticated": true, "connected": true, "competing": false, ... }
```

If `authenticated: false`, go back to the browser and log in. If the
request hangs or errors with "connection refused", the gateway isn't
running.

### Session lifetime

Client Portal sessions idle out after about **6 minutes** without
activity. ibscanner automatically:

- Calls `GET /v1/api/iserver/accounts` every 60s to initialize the
  brokerage session (required once after login — without it,
  `iserver/*` endpoints can hang even when `auth/status` reports
  `authenticated: true`).
- `POST /v1/api/tickle` every 60s as a keepalive.

So a running ibscanner holds its own session alive indefinitely. If you
quit the gateway or log out of the browser tab, ibscanner surfaces a
"not authenticated" banner and pauses scanner loops until you restore
the session — no reload needed.

### OAuth / institutional access

IBKR offers OAuth 1.0a access that avoids the local gateway, but it is
gated behind a formal **Third Party Platform** application process and
is **not available** to personal or small-volume retail projects.

### Containerization

Client Portal Gateway is not natively container-friendly — the daily
browser login is the main obstacle. The community solution is
**[voyz/ibeam](https://github.com/voyz/ibeam)**, a Docker image that
bundles the gateway with headless Chrome for auto-login using
`IBEAM_ACCOUNT` / `IBEAM_PASSWORD` env vars.

For hosted deployment, serve the ibscanner `dist/` as static files and
point `ibkr.base_url` in the config at the ibeam container's address.
The REST surface is identical because ibeam *is* the gateway, just
wrapped.

**2FA caveat**: if your account requires 2FA on API login, ibeam has
workarounds (mobile-app confirmation, TOTP) but they are fiddly. Check
ibeam's docs first.

## Run

```bash
# terminal 1 — start the gateway
cd ~/ibkr-gateway && bin/run.sh root/conf.yaml

# browser — https://localhost:5001 → log in (once per session)

# terminal 2 — start ibscanner
cd client && npm run dev    # Vite at http://localhost:5173
```

Open `http://localhost:5173`. If the gateway is authenticated you'll see
scanner results cycling. If not, the header will read "not authenticated
— visit https://localhost:5001".

For a production build:

```bash
cd client && npm run build   # outputs dist/
```

The `dist/` folder is a static bundle — serve it from any web server,
S3 bucket, or `file://` path.

## Configure scanners

Click the **⚙ config** tab in the app to edit the YAML config. Changes
are validated against the parser before saving and persisted to
`localStorage` under the key `ibscanner.config.yaml`. A bundled default
config ships with the app for first-run.

### Top-level shape

```yaml
ibkr:
  base_url: https://localhost:5001   # or your ibeam container URL

scanners:
  - name: ...
    type: watchlist | ibkr_scan
    ...
```

### Two scanner types

#### `watchlist` — fixed symbol list

```yaml
- name: oversold-bounce
  type: watchlist
  symbols: [AAPL, MSFT, NVDA, AMD, TSLA]
  bar_size: "5 mins"
  duration: "2 D"
  refresh_seconds: 30
  conditions:
    - rsi_14 < 35
    - close > sma_20 * 0.97
  columns: [close, rsi_14, volume, pct_change, sma_20]
```

For each symbol, ibscanner fetches historical bars, computes indicators,
and evaluates every `conditions` expression. All conditions must be true
for the symbol to be marked as a match.

#### `ibkr_scan` — universe scan via IBKR

```yaml
- name: small-cap-momentum
  type: ibkr_scan
  scan_code: TOP_PERC_GAIN
  instrument: STK
  location_code: STK.US.MAJOR
  refresh_seconds: 30
  filters:
    priceAbove: 2
    priceBelow: 12
    changePercAbove: 10
    volumeVsAvgAbove: 500
  post_conditions:
    - rsi_14 < 70
  columns: [close, pct_change, volume_ratio, rsi_14]
```

`scan_code`, `instrument`, `location_code`, and the `filters` keys come
from IBKR's scanner parameter catalog. To dump it:

```bash
curl -sk https://localhost:5001/v1/api/iserver/scanner/params | jq
```

Common scan codes: `TOP_PERC_GAIN`, `TOP_PERC_LOSE`, `HOT_BY_VOLUME`,
`MOST_ACTIVE`.

When `post_conditions` are set (or `enrich: true`, the default),
ibscanner fetches per-result historical bars and evaluates the
expressions. Without enrichment, an `ibkr_scan` row is just symbol +
rank.

> **Cadence note.** IBKR's scanner refreshes server-side roughly every
> 30s. Lower `refresh_seconds` will often return identical data.

### Expression reference

Both `conditions` (watchlist) and `post_conditions` (ibkr_scan) are
[filtrex](https://www.npmjs.com/package/filtrex) expressions evaluated
against the latest bar's indicator values. Available names:

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

Each variable also has a `prev_` prefix for the previous bar (e.g.
`prev_rsi_14`, `prev_macd`) so you can express crosses.

Operators: `<`, `>`, `<=`, `>=`, `==`, `!=`, `+`, `-`, `*`, `/`,
`and`, `or`, `not`, grouped with parentheses. Examples:

```yaml
conditions:
  # Oversold pullback still above its 20-bar mean
  - rsi_14 < 30
  - close > sma_20 * 0.97

  # MACD cross up between prev and current bar
  - macd > macd_signal
  - prev_macd <= prev_macd_signal

  # Volume spike with positive price action
  - volume_ratio > 2
  - close > prev_close
```

All conditions in a scanner are AND-ed.

## Project layout

```
scanners/
├── README.md
├── plan.md                         ← architecture plan and roadmap
└── client/
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts
    ├── index.html
    └── src/
        ├── main.tsx                ← React entry
        ├── App.tsx                 ← config parsing, auth, tab router
        ├── index.css               ← dark theme
        ├── hooks/
        │   ├── useAuthStatus.ts    ← 60s auth poll + session keepalive
        │   └── useScanner.ts       ← per-scanner refresh loop
        ├── lib/
        │   ├── types.ts            ← shared data shapes
        │   ├── config.ts           ← YAML parser → typed AppConfig
        │   ├── ibkr.ts             ← Client Portal REST wrapper (fetch)
        │   ├── indicators.ts       ← OHLCV → indicator values
        │   ├── scanner.ts          ← runs each scanner; filtrex eval
        │   └── defaultConfig.ts    ← bundled first-run YAML
        └── components/
            ├── ScannerPane.tsx      ← table view per scanner
            └── ConfigEditor.tsx     ← in-app YAML editor
```

## Caveats

- **Retail auth only.** OAuth institutional access is not practical for
  personal projects. For 24/7 deployment, use ibeam — see
  [Containerization](#containerization).
- **Historical bars on each cycle, not streaming.** ibscanner re-fetches
  bars on every refresh rather than holding a WebSocket subscription.
  Fine for intervals >= ~10s. A streaming mode would be a worthwhile
  addition.
- **Sequential fetches within a scanner.** Symbols in a watchlist are
  resolved one at a time to stay polite with IBKR's rate limits. Two
  scanners with overlapping symbols fetch the same data twice — a
  shared cache is a future improvement.
- **US equities only by default.** `secType: STK` is hardcoded in
  `ibkr.ts:resolveConid`. Extend there for FX, futures, or non-US
  stocks.
- **No alerting.** Matches appear in the UI only — no sound, webhook,
  or persistent log.
- **Self-signed cert.** The browser must have accepted the gateway's TLS
  cert in the same session. If `fetch()` silently fails, revisit
  `https://localhost:5001` and click through the warning.
