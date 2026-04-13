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
  NewsSummary,
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
  const needsNews = config.columns.includes('news');
  const needsLast =
    config.columns.includes('last') ||
    config.postConditions.some(c => /\blast\b/.test(c));
  const needsFloat =
    config.columns.includes('float') ||
    config.postConditions.some(c => /\bfloat\b/.test(c));

  // Batched snapshot for last price — one gateway call per cycle instead
  // of per-symbol. IBKR's scanner "priceAbove/priceBelow" filter already
  // operates on last price; fetching it here lets the UI surface the
  // same value the filter saw.
  const conids = scanResults
    .map(r => r.conid)
    .filter((c): c is number => typeof c === 'number' && Number.isFinite(c) && c > 0);
  const snapshotByConid = needsLast && conids.length
    ? await ibkr.fetchSnapshot(conids, [31]).catch(() => ({}))
    : {};

  for (const result of scanResults) {
    const snap = typeof result.conid === 'number' ? snapshotByConid[result.conid] : undefined;
    const last = snap ? parseSnapshotNumber(snap['31']) : null;

    // Fundamentals are cached in IBKRClient for 24h, so this is cheap on
    // repeat cycles — only the first scan of the day actually hits the
    // gateway per conid.
    const fundamentals = needsFloat && typeof result.conid === 'number'
      ? await ibkr.fetchFundamentals(result.conid).catch(() => ({ float: null }))
      : null;

    const base: Record<string, number | string | null> = {
      symbol: result.symbol,
      rank:   result.rank,
      last,
      float:  fundamentals?.float ?? null,
    };

    // News fetching is independent of bar enrichment — run it here so
    // the "no bars" / "no data" paths can still surface a news icon.
    const news = needsNews ? await fetchNewsFor(result.conid, ibkr) : undefined;

    if (!needsBars) {
      rows.push({
        symbol: result.symbol,
        matched: true,
        cells: formatCells(base, config.columns),
        news,
      });
      continue;
    }

    try {
      const bars = await ibkr.fetchBars(result.symbol, config.duration, config.barSize);
      if (!bars.length) {
        rows.push({ ...errorRow(result.symbol, 'no bars'), news });
        continue;
      }
      const { latest, prev } = computeIndicators(bars);
      const names = { ...base, ...buildNames(result.symbol, latest, prev) };
      rows.push({
        symbol:  result.symbol,
        matched: evaluate(config.postConditions, names),
        cells:   formatCells(names, config.columns),
        news,
      });
    } catch (err) {
      rows.push({ ...errorRow(result.symbol, formatError(err)), news });
    }
  }

  return rows;
}

/**
 * Fetch the latest news stories for a conid and reduce them to a
 * summary suitable for the UI. Returns null on fetch error or when
 * the gateway has no stories — the UI treats both cases as "no dot".
 * News fetches are best-effort; a failure here should not sink the
 * surrounding scan row.
 */
async function fetchNewsFor(
  conid: number | undefined | null,
  ibkr: IBKRClient,
): Promise<NewsSummary | null> {
  // Some scan hits (OTC tickers, foreign ADRs, odd instrument types)
  // come back without a conid. Hitting /iserver/news?conids=undefined
  // makes IBKR 500, and empirically that 500 flusters the gateway
  // enough that the next /iserver/scanner/run call also 500s with
  // "EMPTY response is received" — so skip the call entirely.
  if (typeof conid !== 'number' || !Number.isFinite(conid) || conid <= 0) {
    return null;
  }
  try {
    // pageSize 3 — we only need the latest story for the dot; keeping
    // it low reduces per-cycle gateway load now that news runs for
    // every scan hit.
    const items = await ibkr.fetchNews(conid, 3);
    if (!items.length) return null;
    const latest = items.reduce((a, b) => (b.date > a.date ? b : a));
    if (!latest.date) return null;
    return {
      latestStoryId: latest.id,
      latestHeadline: latest.headline,
      latestSource: latest.source,
      latestDate: latest.date,
      count: items.length,
    };
  } catch {
    return null;
  }
}

/**
 * Coerce a snapshot field to a number. IBKR prefixes certain values with
 * markers like "C" (last close, when market closed) or "H"/"L" (day
 * high/low); strip anything non-numeric before parsing so "C12.34" still
 * reads as 12.34.
 */
function parseSnapshotNumber(v: string | undefined | null): number | null {
  if (v === undefined || v === null || v === '') return null;
  const cleaned = String(v).replace(/[^0-9.\-]/g, '');
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
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
