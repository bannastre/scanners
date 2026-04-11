/**
 * Technical indicator pipeline.
 *
 * Given an array of OHLCV bars (oldest-first), returns the indicator
 * values for the latest bar and the one before it (used for cross
 * conditions like prev_macd <= prev_macd_signal).
 *
 * All values are null when insufficient history is available.
 */

import { SMA, EMA, RSI, MACD, BollingerBands, ATR } from 'technicalindicators';
import { Bar } from './types';

export type IndicatorValues = Record<string, number | null>;

export interface EnrichedBars {
  latest: IndicatorValues;
  prev: IndicatorValues;
}

const last = <T>(arr: T[]): T | null => arr.length > 0 ? arr[arr.length - 1] : null;
const prevOf = <T>(arr: T[]): T | null => arr.length > 1 ? arr[arr.length - 2] : null;

export function computeIndicators(bars: Bar[]): EnrichedBars {
  const closes  = bars.map(b => b.close);
  const highs   = bars.map(b => b.high);
  const lows    = bars.map(b => b.low);
  const volumes = bars.map(b => b.volume);

  // Simple / exponential moving averages
  const sma: Record<string, number[]> = {};
  for (const p of [5, 10, 20, 50, 200]) {
    sma[`sma_${p}`] = SMA.calculate({ period: p, values: closes });
  }

  const ema: Record<string, number[]> = {};
  for (const p of [9, 12, 26]) {
    ema[`ema_${p}`] = EMA.calculate({ period: p, values: closes });
  }

  const rsi  = RSI.calculate({ period: 14, values: closes });
  const macd = MACD.calculate({
    values: closes,
    fastPeriod: 12,
    slowPeriod: 26,
    signalPeriod: 9,
    SimpleMAOscillator: false,
    SimpleMASignal: false,
  });
  const bb = BollingerBands.calculate({ period: 20, values: closes, stdDev: 2 });
  const atr = ATR.calculate({ period: 14, high: highs, low: lows, close: closes });

  const volSma20 = SMA.calculate({ period: 20, values: volumes });

  function buildRow(
    bar: Bar | undefined,
    smaFn: (k: string) => number | null,
    emaFn: (k: string) => number | null,
    rsiVal: number | null,
    macdVal: (typeof macd)[number] | null,
    bbVal: (typeof bb)[number] | null,
    atrVal: number | null,
    volSma: number | null,
    prevBar: Bar | undefined,
  ): IndicatorValues {
    return {
      open:   bar?.open   ?? null,
      high:   bar?.high   ?? null,
      low:    bar?.low    ?? null,
      close:  bar?.close  ?? null,
      volume: bar?.volume ?? null,

      sma_5:   smaFn('sma_5'),
      sma_10:  smaFn('sma_10'),
      sma_20:  smaFn('sma_20'),
      sma_50:  smaFn('sma_50'),
      sma_200: smaFn('sma_200'),

      ema_9:  emaFn('ema_9'),
      ema_12: emaFn('ema_12'),
      ema_26: emaFn('ema_26'),

      rsi_14: rsiVal,

      macd:        macdVal?.MACD      ?? null,
      macd_signal: macdVal?.signal    ?? null,
      macd_hist:   macdVal?.histogram ?? null,

      bb_upper: bbVal?.upper  ?? null,
      bb_lower: bbVal?.lower  ?? null,
      bb_mid:   bbVal?.middle ?? null,

      atr_14: atrVal,

      volume_sma_20: volSma,
      volume_ratio:  (bar && volSma) ? bar.volume / volSma : null,

      pct_change: (bar && prevBar)
        ? ((bar.close - prevBar.close) / prevBar.close) * 100
        : null,
    };
  }

  const n = bars.length;

  const latestRow = buildRow(
    bars[n - 1],
    k => last(sma[k]),
    k => last(ema[k]),
    last(rsi),
    last(macd),
    last(bb),
    last(atr),
    last(volSma20),
    bars[n - 2],
  );

  const prevRow = buildRow(
    bars[n - 2],
    k => prevOf(sma[k]),
    k => prevOf(ema[k]),
    prevOf(rsi),
    prevOf(macd),
    prevOf(bb),
    prevOf(atr),
    prevOf(volSma20),
    bars[n - 3],
  );

  return { latest: latestRow, prev: prevRow };
}
