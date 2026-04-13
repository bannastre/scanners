import { IBKRClient } from '../lib/ibkr';
import { ScannerConfig, NewsSummary } from '../lib/types';
import { useScanner } from '../hooks/useScanner';

interface Props {
  config: ScannerConfig;
  ibkr: IBKRClient;
  enabled: boolean;
}

/**
 * Age-bucket thresholds for the news dot, in hours. The bucket name
 * maps to a CSS class (.news-dot.red / .orange / .yellow / .grey).
 * Anything older than the final threshold renders no dot.
 */
const NEWS_BUCKETS: Array<{ maxHours: number; cls: string }> = [
  { maxHours: 2,  cls: 'red'    },
  { maxHours: 8,  cls: 'orange' },
  { maxHours: 24, cls: 'yellow' },
  { maxHours: 48, cls: 'grey'   },
];

function newsBucket(ageHours: number): string | null {
  for (const b of NEWS_BUCKETS) {
    if (ageHours < b.maxHours) return b.cls;
  }
  return null;
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

  /**
   * Fetch the story body for the given ID and open it in a new window.
   * The CP API returns JSON wrapping an HTML `body` field; we write
   * that body into a blank window so the user gets a readable article
   * rather than raw JSON.
   */
  async function openStory(news: NewsSummary) {
    // Open synchronously so popup blockers allow the window (async
    // opens after an await get suppressed). We fill it after the
    // fetch resolves.
    const win = window.open('', '_blank', 'width=720,height=800');
    if (!win) return;
    win.document.write(
      `<title>loading story…</title><pre style="font-family:system-ui;padding:20px">Loading ${news.latestHeadline || news.latestStoryId}…</pre>`,
    );
    try {
      const story = await ibkr.fetchStory(news.latestStoryId);
      const body = story.body ?? '';
      const heading = news.latestHeadline ? `<h1 style="font-family:system-ui">${escapeHtml(news.latestHeadline)}</h1>` : '';
      const byline = news.latestSource
        ? `<p style="font-family:system-ui;color:#666">${escapeHtml(news.latestSource)} · ${new Date(news.latestDate).toLocaleString()}</p>`
        : '';
      win.document.open();
      win.document.write(
        `<!doctype html><meta charset="utf-8"><title>${escapeHtml(news.latestHeadline || news.latestStoryId)}</title>` +
        `<div style="max-width:720px;margin:24px auto;padding:0 16px;line-height:1.5">${heading}${byline}${body}</div>`,
      );
      win.document.close();
    } catch (err) {
      win.document.open();
      win.document.write(
        `<pre style="font-family:system-ui;padding:20px;color:#b00">Failed to load story: ${escapeHtml(String(err))}</pre>`,
      );
      win.document.close();
    }
  }

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
                  <td key={col}>
                    {col === 'news'
                      ? renderNewsCell(row.news, openStory)
                      : row.cells[col] ?? '—'}
                  </td>
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

function renderNewsCell(
  news: NewsSummary | null | undefined,
  onOpen: (news: NewsSummary) => void,
) {
  if (!news || !news.latestStoryId || !news.latestDate) return '—';
  const ageHours = (Date.now() - news.latestDate) / 3_600_000;
  const bucket = newsBucket(ageHours);
  if (!bucket) return '—';
  const tooltip = `${news.latestHeadline}\n${news.latestSource} · ${new Date(news.latestDate).toLocaleString()}\n${news.count} stor${news.count === 1 ? 'y' : 'ies'} in feed`;
  return (
    <button
      type="button"
      className={`news-dot ${bucket}`}
      title={tooltip}
      onClick={() => onOpen(news)}
      aria-label={`Open latest story: ${news.latestHeadline}`}
    />
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
