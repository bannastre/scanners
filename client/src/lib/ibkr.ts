/**
 * IBKR Client Portal Web API client (browser version).
 *
 * The Client Portal Gateway runs at https://localhost:5000 by default and
 * uses a self-signed TLS certificate. The user must visit the gateway URL
 * once in their browser to accept the cert and log in; after that, the
 * cookies are reused automatically via `credentials: 'include'`.
 *
 * This client does not handle auth itself — it assumes the user has an
 * active session. `checkAuth()` and `initBrokerageSession()` let the UI
 * detect and recover from stale sessions.
 */

import { Bar } from './types';

/**
 * Error thrown for any non-2xx response or network failure. Mirrors the
 * server's `formatAxiosError` output so the UI can render the same kind
 * of one-line grep-friendly summary.
 */
export class IBKRError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly url?: string,
  ) {
    super(message);
    this.name = 'IBKRError';
  }
}

/**
 * Produce a one-line summary of an unknown error. `TypeError: Failed to
 * fetch` is the browser's signal for either (a) the gateway isn't running,
 * (b) the self-signed cert hasn't been accepted, or (c) CORS — all of
 * which require the same user action (visit the gateway in a tab).
 */
export function formatError(err: unknown): string {
  if (err instanceof IBKRError) {
    const loc = err.url ? ` ${err.url}` : '';
    return err.status ? `${err.status}${loc} — ${err.message}` : `${err.message}${loc}`;
  }
  if (err instanceof TypeError && /fetch/i.test(err.message)) {
    return 'cannot reach gateway (is it running? have you accepted the self-signed cert?)';
  }
  return err instanceof Error ? err.message : String(err);
}

interface SecDefResult {
  conid: number;
  symbol: string;
}

interface HistoryResponse {
  data: Array<{
    t: number; // epoch ms
    o: number;
    h: number;
    l: number;
    c: number;
    v: number;
  }>;
}

interface ScannerResult {
  contracts: Array<{
    // IBKR has shipped both `conid` and `con_id` across CP builds; accept
    // either so snapshot/news lookups don't silently no-op when the
    // gateway returns the underscored form.
    conid?: number;
    con_id?: number;
    symbol: string;
  }>;
}

/** Fundamentals (float, shares-outstanding, etc.) only move on corporate
 *  actions — once per day is plenty. Cache per conid so a 10-second scan
 *  loop doesn't hammer the gateway for data that changed weeks ago. */
const FUNDAMENTALS_TTL_MS = 24 * 60 * 60 * 1000; // 24h

interface FundamentalsSnapshot {
  /** Share float (freely tradable shares), raw count. null if the
   *  gateway didn't return a recognisable field for this conid. */
  float: number | null;
}

export class IBKRClient {
  private baseUrl: string;
  private conidCache = new Map<string, number>();
  private fundamentalsCache = new Map<number, { value: FundamentalsSnapshot; expiresAt: number }>();

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  private async json<T>(
    method: 'GET' | 'POST',
    path: string,
    opts: { params?: Record<string, string | number | boolean>; body?: unknown } = {},
  ): Promise<T> {
    // When baseUrl is empty (dev proxy mode) paths like /v1/api/... are
    // relative to the current origin, which Vite proxies to the gateway.
    const raw = this.baseUrl ? this.baseUrl + path : path;
    const url = new URL(raw, window.location.origin);
    if (opts.params) {
      for (const [k, v] of Object.entries(opts.params)) {
        url.searchParams.set(k, String(v));
      }
    }

    let res: Response;
    try {
      res = await fetch(url.toString(), {
        method,
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      });
    } catch (err) {
      // Network-level failure (gateway down, cert not trusted, CORS).
      throw new IBKRError(formatError(err), undefined, `${method} ${path}`);
    }

    if (!res.ok) {
      // IBKR frequently returns the real error in the response body
      // (JSON with `error` / `message`, or plain text). Surface that so
      // 500s don't show up as a blank "Internal Server Error".
      const bodyText = await res.text().catch(() => '');
      let detail = '';
      if (bodyText) {
        try {
          const parsed = JSON.parse(bodyText);
          detail = parsed?.error ?? parsed?.message ?? parsed?.errorMsg ?? bodyText;
        } catch {
          detail = bodyText;
        }
      }
      const msg = detail
        ? `${res.statusText || 'request failed'} — ${String(detail).slice(0, 300)}`
        : res.statusText || 'request failed';
      throw new IBKRError(msg, res.status, `${method} ${path}`);
    }

    // Some CP endpoints return 200 with empty body (e.g. tickle). Guard
    // against JSON parse errors so callers can use json<void>().
    const text = await res.text();
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new IBKRError('invalid JSON response', res.status, `${method} ${path}`);
    }
  }

  async checkAuth(): Promise<boolean> {
    try {
      const data = await this.json<{ authenticated: boolean }>(
        'GET',
        '/v1/api/iserver/auth/status',
      );
      return data?.authenticated ?? false;
    } catch {
      return false;
    }
  }

  /**
   * Initialize the brokerage session. Required after login — without this
   * call, subsequent iserver/* endpoints (including scanner/run) can hang
   * or return empty because the session is "auth'd but not initialized".
   * Idempotent; safe to call repeatedly.
   */
  async initBrokerageSession(): Promise<void> {
    await this.json<unknown>('GET', '/v1/api/iserver/accounts');
  }

  /**
   * Keep the Client Portal session alive. Sessions idle out after ~6
   * minutes without activity, so the UI should tickle every ~60s while
   * the app is open.
   */
  async tickle(): Promise<void> {
    await this.json<unknown>('POST', '/v1/api/tickle');
  }

  async resolveConid(symbol: string): Promise<number> {
    const cached = this.conidCache.get(symbol);
    if (cached !== undefined) return cached;

    const data = await this.json<SecDefResult[]>(
      'POST',
      '/v1/api/iserver/secdef/search',
      { body: { symbol, secType: 'STK', name: false } },
    );

    const match = data.find(r => r.symbol === symbol) ?? data[0];
    if (!match) throw new IBKRError(`no contract found for ${symbol}`);

    this.conidCache.set(symbol, match.conid);
    return match.conid;
  }

  async fetchBars(symbol: string, duration: string, barSize: string): Promise<Bar[]> {
    const conid = await this.resolveConid(symbol);
    const period = durationToPeriod(duration);
    const bar = barSizeToBar(barSize);

    const data = await this.json<HistoryResponse>(
      'GET',
      '/v1/api/iserver/marketdata/history',
      { params: { conid, period, bar, outsideRth: false } },
    );

    return (data.data ?? []).map(b => ({
      time: new Date(b.t).toISOString(),
      open: b.o,
      high: b.h,
      low: b.l,
      close: b.c,
      volume: b.v,
    }));
  }

  async runScan(params: {
    instrument: string;
    locationCode: string;
    scanCode: string;
    filters: Record<string, string | number>;
    maxResults: number;
  }): Promise<Array<{ symbol: string; conid: number; rank: number }>> {
    const filter = Object.entries(params.filters).map(([code, value]) => ({ code, value }));

    const data = await this.json<ScannerResult>(
      'POST',
      '/v1/api/iserver/scanner/run',
      {
        body: {
          instrument: params.instrument,
          location: params.locationCode,
          type: params.scanCode,
          filter,
          size: String(params.maxResults),
        },
      },
    );

    return (data.contracts ?? []).map((c, i) => ({
      symbol: c.symbol,
      conid: (c.conid ?? c.con_id) as number,
      rank: i + 1,
    }));
  }

  /**
   * Batched market-data snapshot. Field 31 is last price; other common
   * fields are 84 (bid), 86 (ask), 82 (change), 83 (change %), 87 (volume).
   *
   * The CP snapshot endpoint famously returns partial data on the first
   * call after a fresh session — the gateway subscribes to the feed on
   * call 1 and only populates on call 2+. Callers that need a warm
   * response should accept that the first tick of a scan cycle may come
   * back blank for a new conid.
   */
  async fetchSnapshot(
    conids: number[],
    fields: number[] = [31],
  ): Promise<Record<number, Record<string, string>>> {
    if (!conids.length) return {};
    const data = await this.json<Array<Record<string, string>>>(
      'GET',
      '/v1/api/iserver/marketdata/snapshot',
      {
        params: {
          conids: conids.join(','),
          fields: fields.join(','),
        },
      },
    );
    const out: Record<number, Record<string, string>> = {};
    for (const row of data ?? []) {
      const conid = Number(row.conid ?? row.conidex);
      if (Number.isFinite(conid)) out[conid] = row;
    }
    return out;
  }

  /**
   * Fetch the fundamentals summary for a conid, and distil out fields we
   * care about (currently: float). Results are cached in-memory for
   * FUNDAMENTALS_TTL_MS because these values don't move intraday — at
   * refresh_seconds: 10 we'd otherwise hit the gateway needlessly.
   *
   * The CP "summary" endpoint's field names vary wildly across builds and
   * across data providers (Reuters vs. Refinitiv vs. AltaVista), so the
   * parser is intentionally defensive: it walks a list of known keys and
   * takes the first that produces a parseable number. Values can be raw
   * counts (45600000), scaled strings ("45.6M"), or nested objects —
   * `coerceShareCount` handles each.
   */
  async fetchFundamentals(conid: number): Promise<FundamentalsSnapshot> {
    const cached = this.fundamentalsCache.get(conid);
    if (cached && cached.expiresAt > Date.now()) return cached.value;

    let raw: Record<string, unknown> = {};
    try {
      raw = await this.json<Record<string, unknown>>(
        'GET',
        `/v1/api/iserver/fundamentals/${conid}/summary`,
      );
    } catch {
      // Fundamentals aren't life-or-death for a scan row; cache the miss
      // briefly so we don't retry on every cycle.
      const miss: FundamentalsSnapshot = { float: null };
      this.fundamentalsCache.set(conid, {
        value: miss,
        expiresAt: Date.now() + 60_000,
      });
      return miss;
    }

    const floatKeys = [
      'float',
      'sharesFloat',
      'freeFloat',
      'FloatShares',
      'FloatedShares',
      'sharesFloating',
      'NSHRFL',   // Refinitiv: number of shares floated
      'NSHRFQ',   // Refinitiv quarterly variant
    ];
    let floatVal: number | null = null;
    for (const k of floatKeys) {
      floatVal = coerceShareCount((raw as Record<string, unknown>)[k]);
      if (floatVal !== null) break;
    }

    const value: FundamentalsSnapshot = { float: floatVal };
    this.fundamentalsCache.set(conid, {
      value,
      expiresAt: Date.now() + FUNDAMENTALS_TTL_MS,
    });
    return value;
  }

  /**
   * Fetch news headlines for a given contract. Field names on the
   * response vary slightly across CP API builds, so parsing is
   * defensive — we coerce whichever of date / dateTime / datetime
   * (epoch ms or ISO string) the gateway returns.
   */
  async fetchNews(conid: number, pageSize = 10): Promise<NewsItem[]> {
    const data = await this.json<RawNewsItem[] | null>(
      'GET',
      '/v1/api/iserver/news',
      { params: { conids: conid, pageSize } },
    );

    return (data ?? []).map(n => {
      const rawDate = n.date ?? n.dateTime ?? n.datetime ?? n.updated ?? 0;
      const date = typeof rawDate === 'number' ? rawDate : Date.parse(String(rawDate));
      return {
        id:       String(n.id ?? n.storyId ?? ''),
        headline: String(n.headline ?? n.title ?? ''),
        source:   String(n.source ?? n.provider ?? ''),
        date:     Number.isFinite(date) ? date : 0,
      };
    });
  }

  /**
   * Fetch the full body of a single news story. Returns whatever the
   * gateway provides — typically an object with a `body` HTML field —
   * so the caller can render or inject as it sees fit.
   */
  async fetchStory(storyId: string): Promise<NewsStory> {
    return this.json<NewsStory>(
      'GET',
      `/v1/api/iserver/news/${encodeURIComponent(storyId)}`,
    );
  }
}

/** Wire format for a news list item — fields vary between builds. */
interface RawNewsItem {
  id?: string;
  storyId?: string;
  headline?: string;
  title?: string;
  source?: string;
  provider?: string;
  date?: number | string;
  dateTime?: number | string;
  datetime?: number | string;
  updated?: number | string;
}

export interface NewsItem {
  id: string;
  headline: string;
  source: string;
  date: number; // epoch ms
}

export interface NewsStory {
  body?: string;
  headline?: string;
  source?: string;
  date?: number | string;
  [key: string]: unknown;
}

/**
 * Coerce a fundamentals field (raw count, scaled string, or nested object)
 * into a number of shares. Returns null if the value isn't recognisable.
 * Handles: 45600000, "45600000", "45.6M", "45.6m", "45.6 million",
 * "456,000,000", { value: 45.6, unit: "M" }.
 */
function coerceShareCount(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'number') return Number.isFinite(v) && v > 0 ? v : null;

  if (typeof v === 'object') {
    const o = v as Record<string, unknown>;
    const base = coerceShareCount(o.value ?? o.amount ?? o.raw);
    if (base === null) return null;
    const unit = String(o.unit ?? o.scale ?? '').toLowerCase();
    const mul =
      unit.startsWith('b') ? 1e9 :
      unit.startsWith('m') ? 1e6 :
      unit.startsWith('k') || unit.startsWith('t') ? 1e3 :
      1;
    return base * mul;
  }

  if (typeof v === 'string') {
    const s = v.trim().toLowerCase();
    if (!s) return null;
    const m = s.match(/^([+-]?[\d,]*\.?\d+)\s*(b|bn|billion|m|mil|million|k|thousand)?/);
    if (!m) return null;
    const n = Number(m[1].replace(/,/g, ''));
    if (!Number.isFinite(n)) return null;
    const unit = m[2] ?? '';
    const mul =
      unit.startsWith('b') ? 1e9 :
      unit.startsWith('m') ? 1e6 :
      unit.startsWith('k') || unit.startsWith('t') ? 1e3 :
      1;
    return n * mul;
  }

  return null;
}

/** Convert IB-style duration ("2 D", "30 D") to Client Portal period ("2d", "1m"). */
function durationToPeriod(duration: string): string {
  const m = duration.trim().match(/^(\d+)\s*([DMWY])$/i);
  if (!m) return '1d';
  const n = parseInt(m[1]);
  switch (m[2].toUpperCase()) {
    case 'D': return `${n}d`;
    case 'W': return `${n}w`;
    case 'M': return `${n}m`;
    case 'Y': return `${n}y`;
    default:  return '1d';
  }
}

/** Convert IB-style bar size ("5 mins", "1 day") to Client Portal bar ("5min", "1d"). */
function barSizeToBar(barSize: string): string {
  const map: Record<string, string> = {
    '1 min':   '1min',
    '2 mins':  '2min',
    '3 mins':  '3min',
    '5 mins':  '5min',
    '10 mins': '10min',
    '15 mins': '15min',
    '20 mins': '20min',
    '30 mins': '30min',
    '1 hour':  '1h',
    '2 hours': '2h',
    '3 hours': '3h',
    '4 hours': '4h',
    '8 hours': '8h',
    '1 day':   '1d',
    '1 week':  '1w',
    '1 month': '1m',
  };
  return map[barSize] ?? '5min';
}
