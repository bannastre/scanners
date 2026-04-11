/**
 * YAML config parser for ibscanner (browser version).
 *
 * The browser can't read from disk, so this exposes `parseConfig(yaml)`
 * that takes the raw YAML string. The caller (UI layer) decides where
 * the string comes from — localStorage, file upload, a bundled default.
 */

import yaml from 'js-yaml';
import { AppConfig, IBKRConfig, ScannerConfig, WatchlistConfig, IBKRScanConfig } from './types';

const WATCHLIST_DEFAULTS = {
  barSize: '5 mins',
  duration: '2 D',
  columns: ['close', 'rsi_14', 'volume', 'pct_change'],
};

const IBKR_SCAN_DEFAULTS = {
  barSize: '1 day',
  duration: '30 D',
  columns: ['close', 'pct_change', 'volume', 'volume_ratio'],
};

type RawConfig = Record<string, unknown>;

export function parseConfig(yamlText: string): AppConfig {
  const raw = (yaml.load(yamlText) as RawConfig) ?? {};

  const ibkrRaw = (raw.ibkr ?? {}) as RawConfig;
  const ibkr: IBKRConfig = {
    baseUrl: String(ibkrRaw.base_url ?? 'https://localhost:5000'),
  };

  const scanners: ScannerConfig[] = [];

  for (const s of ((raw.scanners ?? []) as RawConfig[])) {
    const type = String(s.type ?? 'watchlist');
    if (type !== 'watchlist' && type !== 'ibkr_scan') {
      throw new Error(`Unknown scanner type: ${type}`);
    }

    const defaults = type === 'ibkr_scan' ? IBKR_SCAN_DEFAULTS : WATCHLIST_DEFAULTS;
    const rawColumns = s.columns as string[] | undefined;

    const base = {
      name: String(s.name),
      refreshSeconds: Number(s.refresh_seconds ?? 30),
      columns: rawColumns?.length ? rawColumns : defaults.columns,
      barSize: String(s.bar_size ?? defaults.barSize),
      duration: String(s.duration ?? defaults.duration),
      whatToShow: String(s.what_to_show ?? 'TRADES'),
      useRth: s.use_rth !== false,
    };

    if (type === 'ibkr_scan') {
      const cfg: IBKRScanConfig = {
        ...base,
        type: 'ibkr_scan',
        scanCode: String(s.scan_code ?? ''),
        instrument: String(s.instrument ?? 'STK'),
        locationCode: String(s.location_code ?? 'STK.US.MAJOR'),
        filters: ((s.filters ?? {}) as Record<string, string | number>),
        postConditions: ((s.post_conditions ?? []) as string[]),
        enrich: s.enrich !== false,
        maxResults: Number(s.max_results ?? 50),
      };
      scanners.push(cfg);
    } else {
      const cfg: WatchlistConfig = {
        ...base,
        type: 'watchlist',
        symbols: ((s.symbols ?? []) as string[]).map(sym => String(sym).toUpperCase()),
        conditions: ((s.conditions ?? []) as string[]),
      };
      scanners.push(cfg);
    }
  }

  const theme = raw.theme ? String(raw.theme) : undefined;
  return { ibkr, scanners, theme };
}
