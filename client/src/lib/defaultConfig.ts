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
#   2. Visit https://localhost:5000 in this browser, accept the
#      self-signed cert warning, and log in to your IBKR account.
#   3. Leave that tab open — the session idles out after ~6 minutes
#      without activity, and ibscanner will tickle it on your behalf.
#
# Conditions are filtrex expressions evaluated against per-bar indicator
# values. Available names: close, open, high, low, volume,
# sma_5/10/20/50/200, ema_9/12/26, rsi_14, macd, macd_signal, macd_hist,
# bb_upper/bb_lower/bb_mid, atr_14, volume_sma_20, volume_ratio,
# pct_change. Each also has a prev_ prefix for the previous bar.

ibkr:
  base_url: https://localhost:5001

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
`;
