import { IBKRClient } from '../lib/ibkr';
import { ScannerConfig } from '../lib/types';
import { useScanner } from '../hooks/useScanner';

interface Props {
  config: ScannerConfig;
  ibkr: IBKRClient;
  enabled: boolean;
}

export default function ScannerPane({ config, ibkr, enabled }: Props) {
  const { result, error, running } = useScanner(config, ibkr, enabled);

  const summary = !enabled
    ? 'waiting for gateway authentication…'
    : result
      ? `${result.name}  |  symbols: ${result.rows.length}  |  matches: ${result.matches}` +
        `  |  refresh: ${config.refreshSeconds}s  |  last: ${result.ranAt} (${result.durationS}s)` +
        (running ? '  ·  refreshing…' : '')
      : running
        ? 'running first scan…'
        : 'waiting…';

  return (
    <div className="pane">
      <p className="summary">{summary}</p>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>✓</th>
              {config.columns.map(col => <th key={col}>{col}</th>)}
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {result?.rows.map(row => (
              <tr key={row.symbol} className={row.matched ? 'matched' : 'unmatched'}>
                <td>{row.symbol}</td>
                <td>{row.matched ? '✓' : ''}</td>
                {config.columns.map(col => (
                  <td key={col}>{row.cells[col] ?? '—'}</td>
                ))}
                <td>{row.error ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
