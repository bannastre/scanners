/**
 * Runtime types used by the scanner library. The UI layer has its own
 * `client/src/types.ts` with a narrower `ScannerInfo` — those two files
 * will get unified in Step 3 once App.tsx is rewritten against this lib.
 */

export interface IBKRConfig {
  baseUrl: string;
}

interface ScannerConfigBase {
  name: string;
  refreshSeconds: number;
  columns: string[];
  barSize: string;
  duration: string;
  whatToShow: string;
  useRth: boolean;
}

export interface WatchlistConfig extends ScannerConfigBase {
  type: 'watchlist';
  symbols: string[];
  conditions: string[];
}

export interface IBKRScanConfig extends ScannerConfigBase {
  type: 'ibkr_scan';
  scanCode: string;
  instrument: string;
  locationCode: string;
  filters: Record<string, string | number>;
  postConditions: string[];
  enrich: boolean;
  maxResults: number;
}

export type ScannerConfig = WatchlistConfig | IBKRScanConfig;

export interface AppConfig {
  ibkr: IBKRConfig;
  scanners: ScannerConfig[];
  theme?: string;
}

export interface Bar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface NewsSummary {
  /** Story id from /iserver/news. Used to open the body via /iserver/news/{id}. */
  latestStoryId: string;
  latestHeadline: string;
  latestSource: string;
  /** Epoch ms of the most recent story. */
  latestDate: number;
  /** Number of stories returned in the last fetch (within gateway's default window). */
  count: number;
}

export interface ScanRow {
  symbol: string;
  matched: boolean;
  cells: Record<string, string>;
  error?: string;
  /** Only populated when the scanner opted into news enrichment. */
  news?: NewsSummary | null;
}

export interface ScanResult {
  name: string;
  rows: ScanRow[];
  ranAt: string;
  durationS: number;
  matches: number;
}
