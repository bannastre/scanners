import { ScannerInfo, ScanResult } from '../types';

interface Props {
  scanner: ScannerInfo;
  result?: ScanResult;
}

export default function ScannerPane({ scanner, result }: Props) {
  const summary = result
    ? `${result.name}  |  symbols: ${result.rows.length}  |  matches: ${result.matches}` +
      `  |  refresh: ${scanner.refreshSeconds}s  |  last: ${result.ranAt} (${result.durationS}s)`
    : 'waiting for first scan…';

  return (
    <div className="pane">
      <p className="summary">{summary}</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>✓</th>
              {scanner.columns.map(col => <th key={col}>{col}</th>)}
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {result?.rows.map(row => (
              <tr key={row.symbol} className={row.matched ? 'matched' : 'unmatched'}>
                <td>{row.symbol}</td>
                <td>{row.matched ? '✓' : ''}</td>
                {scanner.columns.map(col => (
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
