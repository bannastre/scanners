/**
 * Scanner engine (browser version).
 *
 * Evaluates user-authored filter expressions against indicator rows using
 * filtrex — a small, sandboxed expression compiler (~5KB). Unlike mathjs
 * or expr-eval, filtrex has no access to globals, no function-definition
 * syntax, and no way to reach the runtime, so it's safe to run arbitrary
 * expressions from YAML loaded out of localStorage.
 *
 * Compiled expressions are cached per string so repeated scans don't pay
 * the compile cost on every tick.
 */

import { compileExpression } from 'filtrex';
import { IBKRClient, formatError } from './ibkr';
import { computeIndicators, IndicatorValues } from './indicators';
import {
  ScannerConfig,
  WatchlistConfig,
  IBKRScanConfig,
  ScanResult,
  ScanRow,
} from './types';

type CompiledExpr = (ctx: Record<string, unknown>) => unknown;

const exprCache = new Map<string, CompiledExpr>();

function compile(expr: string): CompiledExpr {
  const cached = exprCache.get(expr);
  if (cached) return cached;
  const fn = compileExpression(expr) as CompiledExpr;
  exprCache.set(expr, fn);
  return fn;
}

export async function runScanner(
  config: ScannerConfig,
  ibkr: IBKRClient,
): Promise<ScanResult> {
  const start = Date.now();

  const rows = config.type === 'ibkr_scan'
    ? await runIBKRScan(config, ibkr)
    : await runWatchlist(config, ibkr);

  return {
    name: config.name,
    rows,
    ranAt: new Date().toLocaleTimeString('en-GB'),
    durationS: Math.round((Date.now() - start) / 100) / 10,
    matches: rows.filter(r => r.matched).length,
  };
}

async function runWatchlist(
  config: WatchlistConfig,
  ibkr: IBKRClient,
): Promise<ScanRow[]> {
  const rows: ScanRow[] = [];

  for (const symbol of config.symbols) {
    try {
      const bars = await ibkr.fetchBars(symbol, config.duration, config.barSize);
      if (!bars.length) {
        rows.push(errorRow(symbol, 'no data'));
        continue;
      }
      const { latest, prev } = computeIndicators(bars);
      const names = buildNames(symbol, latest, prev);
      rows.push({
        symbol,
        matched: evaluate(config.conditions, names),
        cells: formatCells(names, config.columns),
      });
    } catch (err) {
      rows.push(errorRow(symbol, formatError(err)));
    }
  }

  return rows;
}

async function runIBKRScan(
  config: IBKRScanConfig,
  ibkr: IBKRClient,
): Promise<ScanRow[]> {
  const scanResults = await ibkr.runScan({
    instrument: config.instrument,
    locationCode: config.locationCode,
    scanCode: config.scanCode,
    filters: config.filters,
    maxResults: config.maxResults,
  });

  const rows: ScanRow[] = [];
  const needsBars = config.enrich || config.postConditions.length > 0;

  for (const result of scanResults) {
    const base: Record<string, number | string | null> = {
      symbol: result.symbol,
      rank:   result.rank,
    };

    if (!needsBars) {
      rows.push({
        symbol: result.symbol,
        matched: true,
        cells: formatCells(base, config.columns),
      });
      continue;
    }

    try {
      const bars = await ibkr.fetchBars(result.symbol, config.duration, config.barSize);
      if (!bars.length) {
        rows.push(errorRow(result.symbol, 'no bars'));
        continue;
      }
      const { latest, prev } = computeIndicators(bars);
      const names = { ...base, ...buildNames(result.symbol, latest, prev) };
      rows.push({
        symbol:  result.symbol,
        matched: evaluate(config.postConditions, names),
        cells:   formatCells(names, config.columns),
      });
    } catch (err) {
      rows.push(errorRow(result.symbol, formatError(err)));
    }
  }

  return rows;
}

function buildNames(
  symbol: string,
  latest: IndicatorValues,
  prev: IndicatorValues,
): Record<string, number | string | null> {
  const names: Record<string, number | string | null> = { symbol };
  for (const [k, v] of Object.entries(latest)) {
    names[k] = v;
    names[`prev_${k}`] = prev[k] ?? null;
  }
  return names;
}

function evaluate(conditions: string[], names: Record<string, unknown>): boolean {
  if (!conditions.length) return true;
  try {
    return conditions.every(cond => {
      const fn = compile(cond);
      // filtrex returns number/boolean; treat non-zero / true as match.
      const result = fn(names);
      return Boolean(result) && result !== 0;
    });
  } catch {
    return false;
  }
}

function formatCells(
  values: Record<string, unknown>,
  columns: string[],
): Record<string, string> {
  return Object.fromEntries(columns.map(col => [col, fmt(values[col])]));
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') {
    const abs = Math.abs(v);
    if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
    if (abs >= 1_000) {
      return v.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    }
    return v.toFixed(4);
  }
  return String(v);
}

function errorRow(symbol: string, error: string): ScanRow {
  return { symbol, matched: false, cells: {}, error };
}
