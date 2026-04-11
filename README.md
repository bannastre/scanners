# ibscanner

A real-time stock scanner for **Interactive Brokers**. Define one or more
scanners in YAML — each either a fixed watchlist with custom indicator
rules, or a universe-wide IBKR market scan — and watch the results stream
into a React front-end over Server-Sent Events. Each scanner refreshes on
its own interval.

## Stack

```
┌─────────┐    SSE    ┌─────────┐   HTTPS   ┌──────────────┐   HTTPS   ┌─────────────┐
│  React  │◀──────────│   Koa   │──────────▶│ Client Portal│──────────▶│ IBKR cloud  │
│ client  │           │  server │           │   Gateway    │           │             │
└─────────┘           └─────────┘           └──────────────┘           └─────────────┘
 client/                server/               localhost:5000
                                              (you run this
                                              alongside ibscanner)
```

- **Server** — TypeScript + Koa, REST client against IBKR's Client Portal
  Web API. Runs one scan loop per scanner and broadcasts results to SSE
  subscribers.
- **Client** — React, subscribes to `/api/stream`, renders one tab per
  scanner.
- **Indicators** — [`technicalindicators`](https://www.npmjs.com/package/technicalindicators)
  (SMA, EMA, RSI, MACD, Bollinger, ATR).
- **Expression eval** — [`mathjs`](https://mathjs.org/), with the
  runtime-reaching functions (`import`, `createUnit`, `evaluate`, `parse`,
  `simplify`, `derivative`) stubbed out so user-authored YAML expressions
  are sandboxed.
- **YAML config** — [`js-yaml`](https://www.npmjs.com/package/js-yaml).

## Prerequisites

- **Node 20+**
- **An Interactive Brokers account.** A paper-trading account is free
  after you sign up at <https://www.interactivebrokers.com/> and gives
  you everything you need for development.
- **Client Portal Gateway** (see [Auth](#auth)) — a Java bundle IBKR
  ships separately that terminates a local REST endpoint for the Web
  API.

## Install

```bash
# server
cd server && npm install && npm run build

# client (in a second terminal, or sequentially)
cd client && npm install && npm run build
```

## Auth

IBKR's Web API does **not** expose a retail-accessible always-on cloud
endpoint. All REST calls for non-institutional accounts go through
**Client Portal Gateway** — a Java app you run locally that handles
session auth, terminates its own TLS on `https://localhost:5000`, and
proxies your REST calls, upgraded to an authenticated session, to IBKR's
cloud.

```
ibscanner  →  https://localhost:5000  →  IBKR cloud
              (Client Portal Gateway,
               running + browser-authed)
```

### One-time gateway setup

1. Download **Client Portal Gateway** from IBKR. On the IBKR site navigate
   to *Trading → Tools → APIs → Client Portal API* and grab
   `clientportal.gw.zip`. (The download URL changes; search "IBKR Client
   Portal Gateway" if in doubt.)
2. Unzip it wherever you keep tools:
   ```bash
   unzip clientportal.gw.zip -d ~/ibkr-gateway
   ```

### Running the gateway

You need to start the gateway and log in every time you use ibscanner
(or whenever the session expires):

```bash
cd ~/ibkr-gateway && bin/run.sh root/conf.yaml     # macOS/Linux
# or: bin\run.bat root\conf.yaml                    # Windows
```

You should see log lines ending in something like `App demo started` and
a listener on port 5000.

Then **open `https://localhost:5000` in a browser**, click through the
self-signed certificate warning, and log in with your IBKR credentials
(paper account works). You should see "Client login succeeds".

### Verifying auth

From a terminal:

```bash
curl -sk https://localhost:5000/v1/api/iserver/auth/status | jq
# { "authenticated": true, "connected": true, "competing": false, ... }
```

If `authenticated: false`, go back to the browser and log in. If the
request hangs or errors with "connection refused", the gateway process
isn't running.

### Session lifetime

Client Portal sessions idle out after about **6 minutes** without
activity. ibscanner automatically:

- Calls `GET /v1/api/iserver/accounts` on startup and every 60s to
  initialize the brokerage session (required once after login — without
  it, `iserver/*` endpoints can hang or return empty even when
  `auth/status` reports `authenticated: true`).
- `POST /v1/api/tickle` every 60s as a keepalive.

So a running ibscanner holds its own session alive indefinitely as long
as the gateway process stays up. If you quit the gateway or log out of
the browser tab, ibscanner will log concise timeouts until you restore
the session — no ibscanner restart needed.

### OAuth / institutional access

IBKR does offer OAuth 1.0a access that avoids the local gateway, but it
is gated behind IBKR's formal **Third Party Platform** application
process and is **not available** to personal or small-volume retail
projects. Don't go down that path unless you are building something IBKR
will approve as a registered platform.

### Containerization

Client Portal Gateway is not natively container-friendly — the daily
browser login is the main obstacle. The community solution is
**[voyz/ibeam](https://github.com/voyz/ibeam)**, a Docker image that
bundles the gateway with a headless Chrome that auto-logs in using
`IBEAM_ACCOUNT` / `IBEAM_PASSWORD` env vars.

For a future hosted deployment of ibscanner, run ibeam in a sibling
container and point `ibkr.base_url` at it — the REST shape is identical
because ibeam *is* the Client Portal Gateway, just wrapped. No ibscanner
code changes.

**2FA caveat**: if your IBKR account requires 2FA on API login, ibeam has
workarounds (mobile-app confirmation, TOTP) but they are fiddly. Check
ibeam's docs before committing.

## Configure scanners

```bash
cp server/scanners.example.yaml server/scanners.yaml
$EDITOR server/scanners.yaml
```

### Top-level shape

```yaml
ibkr:
  base_url: https://localhost:5000   # or your ibeam container URL

theme: monokai                        # optional React UI theme

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

For each symbol, the server fetches historical bars, computes indicators,
and evaluates every `conditions` expression. All conditions must be true
for the symbol to be marked as a match.

#### `ibkr_scan` — universe scan via IBKR

```yaml
- name: small-cap-momentum
  type: ibkr_scan
  scan_code: TOP_PERC_GAIN
  instrument: STK
  location_code: STK.US.MAJOR
  refresh_seconds: 10
  filters:
    priceAbove: 2
    priceBelow: 12
    changePercAbove: 10
    stVolumeVsAvg10minAbove: 0.499
  post_conditions:
    - rsi_14 < 70
  columns: [close, pct_change, volume_ratio, rsi_14]
```

`scan_code`, `instrument`, `location_code`, and the `filters` keys all
come from IBKR's scanner parameter catalog. To dump the catalog, hit
`GET /v1/api/iserver/scanner/params` against the running gateway and
pipe the response through `jq`.

Common scan codes: `TOP_PERC_GAIN`, `TOP_PERC_LOSE`, `HOT_BY_VOLUME`,
`MOST_ACTIVE`, `SCAN_stVolumeVsAvg10min_DESC`.

When `post_conditions` are set (or `enrich: true`, the default), the
server fetches per-result historical bars and evaluates the expressions
on top of the IBKR filter. Without enrichment, an `ibkr_scan` row is
just symbol + rank — IBKR's stock scan endpoint does not return
projection/last/volume for stocks.

> **Note on cadence.** IBKR's scanner refreshes server-side roughly
> every 30 seconds. `refresh_seconds: 10` will often return identical
> data — useful for tight iteration during dev, wasteful in steady
> state.

### Expression reference

Both `conditions` (watchlist) and `post_conditions` (ibkr_scan) are
mathjs expressions evaluated against the latest bar's indicator values.
Variables available (from `server/src/indicators.ts`):

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

Each variable also has a `prev_` prefixed version for the previous bar
(e.g. `prev_rsi_14`, `prev_macd`) so you can express crosses.

Operators: `<`, `>`, `<=`, `>=`, `==`, `!=`, `+`, `-`, `*`, `/`, `^`,
plus `and` / `or` / `not`, grouped with parentheses. Examples:

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

All conditions in a scanner are AND-ed by the engine.

The mathjs instance is locked down — `import`, `createUnit`, `evaluate`,
`parse`, `simplify`, `derivative` are disabled — so user-authored YAML
cannot reach the runtime.

## Run

```bash
# terminal 1 — start the gateway
cd ~/ibkr-gateway && bin/run.sh root/conf.yaml

# browser — https://localhost:5000 → log in (once per session)

# terminal 2 — start ibscanner
cd server && npm start
```

Startup log on success:

```
ibscanner listening on http://localhost:8000
IBKR Client Portal API: https://localhost:5000
Config: scanners.yaml (3 scanners)
Authenticated with IBKR Client Portal Gateway at https://localhost:5000
```

On failure:

```
  Not authenticated with IBKR Client Portal Gateway.
  → Visit https://localhost:5000 in a browser, accept the self-signed
    certificate, and log in with your IBKR credentials.
  Scan loops will keep retrying — they will succeed once the session is live.

[small-cap-momentum] POST /v1/api/iserver/scanner/run → timeout after 30000ms (is the IBKR Client Portal Gateway authenticated? visit https://localhost:5000 and log in)
```

Open the browser tab, log in, and the next scan cycle will start
succeeding — no ibscanner restart.

### Client dev server

For front-end development with hot reload:

```bash
cd client && npm run dev    # Vite at http://localhost:5173
```

In production, the built client is served from the Koa server at
`http://localhost:8000`.

## Project layout

```
scanners/
├── README.md                       ← you are here
├── server/
│   ├── package.json
│   ├── tsconfig.json
│   ├── scanners.yaml               ← your local config (gitignored)
│   ├── scanners.example.yaml       ← template
│   └── src/
│       ├── index.ts                ← Koa entry; SSE broadcast; keepalive
│       ├── config.ts               ← YAML loader → typed AppConfig
│       ├── ibkr.ts                 ← Client Portal REST wrapper
│       ├── indicators.ts           ← OHLCV → indicator values
│       ├── scanner.ts              ← runs each scanner; mathjs eval
│       └── types.ts
└── client/
    ├── package.json
    └── src/
        ├── main.js
        ├── App.js
        ├── types.js
        └── components/
            └── ScannerPane.js
```

## Caveats

- **Retail auth only.** OAuth institutional access is not practical for
  personal projects. For 24/7 deployment, run ibeam in Docker — see
  [Containerization](#containerization).
- **Historical bars on each cycle, not streaming.** The server
  re-fetches bars on every refresh rather than holding a WebSocket
  subscription. Fine for refresh intervals ≥ ~10s. A streaming mode
  would be a worthwhile addition.
- **Sequential fetches within a scanner.** Symbols in a watchlist are
  resolved and fetched one at a time to stay polite with IBKR's rate
  limits. Two scanners with overlapping symbols will fetch the same data
  twice — shared cache is a future improvement.
- **US equities only by default.** `secType: STK` is hardcoded in
  `ibkr.ts:resolveConid`. Extend there for FX, futures, or non-US
  stocks.
- **No alerting.** Matches appear in the SSE stream and the React UI
  only — no sound, webhook, or persistent log. Easy hook point inside
  the `broadcast` function in `server/src/index.ts`.
