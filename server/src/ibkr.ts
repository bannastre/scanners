/**
 * IBKR Client Portal Web API client.
 *
 * The Client Portal Gateway runs at https://localhost:5000 by default and
 * uses a self-signed TLS certificate. axios is configured to skip cert
 * verification — do not expose this server to untrusted networks.
 */

import axios, { AxiosInstance } from 'axios';
import https from 'https';
import { Bar } from './types';

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
  private http: AxiosInstance;
  private conidCache = new Map<string, number>();

  constructor(baseUrl: string) {
    this.http = axios.create({
      baseURL: baseUrl.replace(/\/$/, ''),
      httpsAgent: new https.Agent({ rejectUnauthorized: false }),
      headers: { 'Content-Type': 'application/json' },
      timeout: 30_000,
    });
  }

  async checkAuth(): Promise<boolean> {
    try {
      const { data } = await this.http.get<{ authenticated: boolean }>(
        '/v1/api/iserver/auth/status',
      );
      return data.authenticated ?? false;
    } catch {
      return false;
    }
  }

  async resolveConid(symbol: string): Promise<number> {
    const cached = this.conidCache.get(symbol);
    if (cached !== undefined) return cached;

    const { data } = await this.http.post<SecDefResult[]>(
      '/v1/api/iserver/secdef/search',
      { symbol, secType: 'STK', name: false },
    );

    const match = data.find(r => r.symbol === symbol) ?? data[0];
    if (!match) throw new Error(`No contract found for ${symbol}`);

    this.conidCache.set(symbol, match.conid);
    return match.conid;
  }

  async fetchBars(symbol: string, duration: string, barSize: string): Promise<Bar[]> {
    const conid = await this.resolveConid(symbol);
    const period = durationToPeriod(duration);
    const bar = barSizeToBar(barSize);

    const { data } = await this.http.get<HistoryResponse>(
      `/v1/api/iserver/marketdata/history`,
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

    const { data } = await this.http.post<ScannerResult>(
      '/v1/api/iserver/scanner/run',
      {
        instrument: params.instrument,
        location: params.locationCode,
        type: params.scanCode,
        filter,
        size: String(params.maxResults),
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
