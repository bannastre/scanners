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
    conid: number;
    symbol: string;
  }>;
}

export class IBKRClient {
  private baseUrl: string;
  private conidCache = new Map<string, number>();

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  private async json<T>(
    method: 'GET' | 'POST',
    path: string,
    opts: { params?: Record<string, string | number | boolean>; body?: unknown } = {},
  ): Promise<T> {
    const url = new URL(this.baseUrl + path);
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
      throw new IBKRError(res.statusText || 'request failed', res.status, `${method} ${path}`);
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
      conid: c.conid,
      rank: i + 1,
    }));
  }
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
