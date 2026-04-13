import { useEffect, useMemo, useState } from 'react';
import { parseConfig } from './lib/config';
import { IBKRClient } from './lib/ibkr';
import { DEFAULT_CONFIG_YAML } from './lib/defaultConfig';
import { useAuthStatus } from './hooks/useAuthStatus';
import ScannerPane from './components/ScannerPane';
import ConfigEditor from './components/ConfigEditor';

const STORAGE_KEY = 'ibscanner.config.yaml';
const CONFIG_TAB  = '__config__';

export default function App() {
  // YAML is sourced from localStorage, with the bundled default as the
  // first-load fallback. The ConfigEditor tab writes back through
  // handleSave / handleReset below.
  const [yamlText, setYamlText] = useState<string>(
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
  const baseUrl = parsed.config?.ibkr.baseUrl ?? '';
  const ibkr = useMemo(() => new IBKRClient(baseUrl), [baseUrl]);
  const { authenticated, checked } = useAuthStatus(ibkr);

  // First render: land on the first scanner if the initial parse
  // produced any; otherwise the config tab (so the user has somewhere
  // to fix an invalid config).
  const [activeTab, setActiveTab] = useState<string>(
    () => parsed.config?.scanners[0]?.name ?? CONFIG_TAB,
  );

  // Keep the active tab valid across config changes. If the user saves
  // a new config that drops the currently-visible scanner, fall back
  // to the first scanner (or the config tab if there are none).
  useEffect(() => {
    if (!parsed.config) return;
    if (activeTab === CONFIG_TAB) return;
    const names = parsed.config.scanners.map(s => s.name);
    if (names.length === 0) {
      setActiveTab(CONFIG_TAB);
    } else if (!names.includes(activeTab)) {
      setActiveTab(names[0]);
    }
  }, [parsed.config, activeTab]);

  function handleSave(newText: string) {
    localStorage.setItem(STORAGE_KEY, newText);
    setYamlText(newText);
  }

  function handleReset() {
    localStorage.removeItem(STORAGE_KEY);
    setYamlText(DEFAULT_CONFIG_YAML);
  }

  if (parsed.error) {
    return (
      <>
        <header>
          <h1>IBKR Scanners</h1>
          <span className="status disconnected">config error</span>
        </header>
        <main>
          <ConfigEditor
            yamlText={yamlText}
            onSave={handleSave}
            onReset={handleReset}
            parseError={parsed.error}
          />
        </main>
      </>
    );
  }

  const config = parsed.config!;

  const statusLabel = !checked
    ? 'checking gateway…'
    : authenticated
      ? 'connected'
      : `not authenticated — visit ${baseUrl || 'https://localhost:5001'}`;
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
        <button
          key={CONFIG_TAB}
          className={`tab-btn config-tab${activeTab === CONFIG_TAB ? ' active' : ''}`}
          onClick={() => setActiveTab(CONFIG_TAB)}
        >
          ⚙ config
        </button>
      </nav>

      <main>
        {config.scanners.map(s => (
          <div key={s.name} style={{ display: activeTab === s.name ? 'contents' : 'none' }}>
            <ScannerPane config={s} ibkr={ibkr} enabled={authenticated} />
          </div>
        ))}
        <div style={{ display: activeTab === CONFIG_TAB ? 'contents' : 'none' }}>
          <ConfigEditor
            yamlText={yamlText}
            onSave={handleSave}
            onReset={handleReset}
          />
        </div>
      </main>
    </>
  );
}
