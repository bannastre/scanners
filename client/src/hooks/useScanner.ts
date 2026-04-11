/**
 * Runs a single scanner on its own refresh interval.
 *
 * Uses a chained setTimeout rather than setInterval so a slow scan
 * cannot overlap itself — the next tick is scheduled only after the
 * current one finishes. The loop is gated on `enabled` (driven by auth
 * status) so we don't hammer the gateway while logged out.
 *
 * This is the per-scanner analogue of the SSE stream from the server-
 * based dev branch. Each pane manages its own lifecycle independently.
 */

import { useEffect, useState } from 'react';
import { IBKRClient, formatError } from '../lib/ibkr';
import { runScanner } from '../lib/scanner';
import { ScannerConfig, ScanResult } from '../lib/types';

export interface ScannerState {
  result: ScanResult | null;
  error: string | null;
  running: boolean;
}

export function useScanner(
  config: ScannerConfig,
  ibkr: IBKRClient,
  enabled: boolean,
): ScannerState {
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (cancelled) return;
      setRunning(true);
      try {
        const res = await runScanner(config, ibkr);
        if (cancelled) return;
        setResult(res);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(formatError(err));
      } finally {
        if (!cancelled) {
          setRunning(false);
          timer = setTimeout(tick, config.refreshSeconds * 1000);
        }
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [config, ibkr, enabled]);

  return { result, error, running };
}
