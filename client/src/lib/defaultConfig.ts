/**
 * Default scanner config embedded in the bundle.
 *
 * On first load (or after the user clears localStorage), the app falls
 * back to this YAML so there's always something running. Step 4 of the
 * plan adds an in-app editor that lets the user override and persist
 * their own config into localStorage.
 */

export const DEFAULT_CONFIG_YAML = `# ibscanner default config.
#
# Before this works:
#   1. Start the IBKR Client Portal Gateway (Java bundle from IBKR).
#   2. Visit https://localhost:5001 in this browser, accept the
#      self-signed cert warning, and log in to your IBKR account.
#   3. Leave that tab open — the session idles out after ~6 minutes
#      without activity, and ibscanner will tickle it on your behalf.
#
# Conditions are filtrex expressions evaluated against per-bar indicator
# values. Available names: close, open, high, low, volume,
# sma_5/10/20/50/200, ema_9/12/26, rsi_14, macd, macd_signal, macd_hist,
# bb_upper/bb_lower/bb_mid, atr_14, volume_sma_20, volume_ratio,
# pct_change. Each also has a prev_ prefix for the previous bar.

# Browser calls the gateway directly at base_url. Cookies set by the
# gateway login are origin-scoped to https://localhost:5001, so the
# fetch target has to match that origin or the browser sends no cookies
# and every request reads as unauthenticated. Make sure the gateway's
# CORS block (ibPortal/root/conf.yaml) names http://localhost:5173 and
# sets allowCredentials: true.
ibkr:
  base_url: "https://localhost:5001"

scanners:
  - name: oversold-bounce
    symbols: [AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AMD]
    bar_size: "5 mins"
    duration: "2 D"
    refresh_seconds: 30
    conditions:
      - rsi_14 < 35
      - close > sma_20 * 0.97
    columns: [close, rsi_14, volume, pct_change, sma_20]

  - name: volume-breakout
    symbols: [SPY, QQQ, IWM, DIA]
    bar_size: "1 min"
    duration: "1 D"
    refresh_seconds: 15
    conditions:
      - volume_ratio > 2
      - close > prev_close
    columns: [close, volume, volume_ratio, pct_change]

  - name: macd-cross-up
    symbols: [AAPL, MSFT, NVDA, AMD, TSLA]
    bar_size: "15 mins"
    duration: "5 D"
    refresh_seconds: 60
    conditions:
      - macd > macd_signal
      - prev_macd <= prev_macd_signal
    columns: [close, macd, macd_signal, rsi_14]

  # IBKR market-wide scanner — searches the full US equity universe
  # filtered by price/change/volume, sorted by top % gain. With
  # enrich: true (default), historical bars are fetched per result
  # so indicator columns are populated.
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
      # Multiplier vs. average daily volume — 2 means "trading at
      # 2x typical volume". The short-term variant (stVolumeVsAvg10min)
      # requires an extra IBKR market-data entitlement and 500s
      # without it.
      volumeVsAvgAbove: 5
      # Low-float runners: floats under 20M are the classic momentum
      # setup (thin supply + a catalyst = outsized moves). Raise the
      # floor above 1M to skip reverse-split shells with near-zero
      # borrowable float. IBKR takes raw share counts.
      floatSharesAbove: 1000000
      floatSharesBelow: 20000000
    # 'last' is the live traded price (IBKR snapshot field 31) — this is
    # what priceAbove/priceBelow filter on, so it should always sit
    # inside [2, 12]. 'close' is the last bar's close; on an intraday
    # bar it lags 'last' by up to one bar interval.
    columns: [news, close, last, pct_change, volume_ratio, macd, float]
`;
