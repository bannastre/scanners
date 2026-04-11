import { useState, useEffect } from 'react';
import { ScannerInfo, ScanResult } from './types';
import ScannerPane from './components/ScannerPane';

type ConnectionState = 'connecting' | 'connected' | 'disconnected';

export default function App() {
  const [scanners, setScanners]       = useState<ScannerInfo[]>([]);
  const [results, setResults]         = useState<Record<string, ScanResult>>({});
  const [activeTab, setActiveTab]     = useState<string>('');
  const [connection, setConnection]   = useState<ConnectionState>('connecting');

  // Load scanner metadata once on mount
  useEffect(() => {
    fetch('/api/scanners')
      .then(r => r.json())
      .then((data: ScannerInfo[]) => {
        setScanners(data);
        if (data.length) setActiveTab(data[0].name);
      })
      .catch(() => setConnection('disconnected'));
  }, []);

  // Open SSE stream and keep it alive on reconnect
  useEffect(() => {
    let es: EventSource;

    function connect() {
      es = new EventSource('/api/stream');

      es.addEventListener('scanner_update', e => {
        setConnection('connected');
        const result: ScanResult = JSON.parse((e as MessageEvent).data);
        setResults(prev => ({ ...prev, [result.name]: result }));
      });

      es.onerror = () => {
        setConnection('disconnected');
        es.close();
        setTimeout(connect, 5_000);
      };
    }

    connect();
    return () => es.close();
  }, []);

  const statusLabel: Record<ConnectionState, string> = {
    connecting:   'connecting…',
    connected:    'connected',
    disconnected: 'reconnecting…',
  };

  return (
    <>
      <header>
        <h1>IBKR Scanners</h1>
        <span className={`status ${connection}`}>{statusLabel[connection]}</span>
      </header>

      <nav>
        {scanners.map(s => (
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
        {scanners.map(s => (
          <div key={s.name} style={{ display: activeTab === s.name ? 'contents' : 'none' }}>
            <ScannerPane scanner={s} result={results[s.name]} />
          </div>
        ))}
      </main>
    </>
  );
}
