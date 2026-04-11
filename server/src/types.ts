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
}

export interface Bar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ScanRow {
  symbol: string;
  matched: boolean;
  cells: Record<string, string>;
  error?: string;
}

export interface ScanResult {
  name: string;
  rows: ScanRow[];
  ranAt: string;
  durationS: number;
  matches: number;
}
