export interface ScannerInfo {
  name: string;
  columns: string[];
  refreshSeconds: number;
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
