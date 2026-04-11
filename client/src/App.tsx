import { useEffect, useMemo, useState } from 'react';
import { parseConfig } from './lib/config';
import { IBKRClient } from './lib/ibkr';
import { DEFAULT_CONFIG_YAML } from './lib/defaultConfig';
import { useAuthStatus } from './hooks/useAuthStatus';
import ScannerPane from './components/ScannerPane';

const STORAGE_KEY = 'ibscanner.config.yaml';

export default function App() {
  // YAML is sourced from localStorage, with the bundled default as the
  // first-load fallback. Step 4 wires an editor up to this state.
  const [yamlText] = useState<string>(
    () => localStorage.getItem(STORAGE_KEY) ?? DEFAULT_CONFIG_YAML,
  );

  const parsed = useMemo(() => {
    try {
      return { config: parseConfig(yamlText), error: null as string | null };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { config: null, error: msg };
    }
  }, [yamlText]);

  // Always build a client, even on parse error, so the useAuthStatus
  // hook call stays unconditional. Base URL falls back to the gateway
  // default; if parsing failed the user will see the error banner and
  // nothing will actually hit the network.
  const baseUrl = parsed.config?.ibkr.baseUrl ?? 'https://localhost:5000';
  const ibkr = useMemo(() => new IBKRClient(baseUrl), [baseUrl]);
  const { authenticated, checked } = useAuthStatus(ibkr);

  const [activeTab, setActiveTab] = useState<string>('');
  useEffect(() => {
    if (parsed.config && parsed.config.scanners.length && !activeTab) {
      setActiveTab(parsed.config.scanners[0].name);
    }
  }, [parsed.config, activeTab]);

  if (parsed.error) {
    return (
      <>
        <header>
          <h1>IBKR Scanners</h1>
          <span className="status disconnected">config error</span>
        </header>
        <main>
          <pre className="error">{parsed.error}</pre>
        </main>
      </>
    );
  }

  const config = parsed.config!;

  const statusLabel = !checked
    ? 'checking gateway…'
    : authenticated
      ? 'connected'
      : 'not authenticated — visit https://localhost:5000';
  const statusClass = !checked ? 'connecting' : authenticated ? 'connected' : 'disconnected';

  return (
    <>
      <header>
        <h1>IBKR Scanners</h1>
        <span className={`status ${statusClass}`}>{statusLabel}</span>
      </header>

      <nav>
        {config.scanners.map(s => (
          <button
            key={s.name}
            className={`tab-btn${activeTab === s.name ? ' active' : ''}`}
            onClick={() => setActiveTab(s.name)}
          >
            {s.name}
          </button>
        ))}
      </nav>

      <main>
        {config.scanners.map(s => (
          <div key={s.name} style={{ display: activeTab === s.name ? 'contents' : 'none' }}>
            <ScannerPane config={s} ibkr={ibkr} enabled={authenticated} />
          </div>
        ))}
      </main>
    </>
  );
}
