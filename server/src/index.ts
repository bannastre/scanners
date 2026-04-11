import Koa from 'koa';
import Router from '@koa/router';
import serve from 'koa-static';
import { PassThrough } from 'stream';
import path from 'path';
import { loadConfig } from './config';
import { IBKRClient } from './ibkr';
import { runScanner } from './scanner';
import { ScanResult } from './types';

const configPath = process.argv[2] ?? 'scanners.yaml';
const port = parseInt(process.env.PORT ?? '8000', 10);

const config = loadConfig(configPath);
const ibkr = new IBKRClient(config.ibkr.baseUrl);

// --- SSE broadcast ----------------------------------------------------------

type Subscriber = (result: ScanResult) => void;
const subscribers = new Set<Subscriber>();

function broadcast(result: ScanResult): void {
  for (const sub of subscribers) sub(result);
}

// --- Scanner loops ----------------------------------------------------------

async function scanLoop(name: string, refreshSeconds: number, run: () => Promise<ScanResult>) {
  while (true) {
    try {
      broadcast(await run());
    } catch (err) {
      console.error(`[${name}]`, err);
    }
    await new Promise(r => setTimeout(r, refreshSeconds * 1_000));
  }
}

for (const scanner of config.scanners) {
  scanLoop(scanner.name, scanner.refreshSeconds, () => runScanner(scanner, ibkr)).catch(
    err => console.error(`[${scanner.name}] fatal:`, err),
  );
}

// --- Koa --------------------------------------------------------------------

const app = new Koa();
const router = new Router();

router.get('/api/scanners', ctx => {
  ctx.body = config.scanners.map(s => ({
    name:           s.name,
    columns:        s.columns,
    refreshSeconds: s.refreshSeconds,
  }));
});

router.get('/api/stream', ctx => {
  ctx.set({
    'Content-Type':    'text/event-stream',
    'Cache-Control':   'no-cache',
    'Connection':      'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  ctx.status = 200;

  const stream = new PassThrough();
  ctx.body = stream;

  const send: Subscriber = result => {
    stream.write(`event: scanner_update\ndata: ${JSON.stringify(result)}\n\n`);
  };

  subscribers.add(send);

  const keepalive = setInterval(() => stream.write(': keepalive\n\n'), 30_000);

  ctx.req.on('close', () => {
    clearInterval(keepalive);
    subscribers.delete(send);
    stream.destroy();
  });
});

// Serve the built React app; SPA fallback for client-side routes
const clientDist = path.resolve(__dirname, '../../client/dist');
app.use(serve(clientDist));

app.use(router.routes()).use(router.allowedMethods());

// SPA fallback — serve index.html for any route not matched above
import { readFileSync } from 'fs';
router.get('/(.*)', ctx => {
  try {
    ctx.type = 'html';
    ctx.body = readFileSync(path.join(clientDist, 'index.html'), 'utf8');
  } catch {
    ctx.status = 404;
    ctx.body = 'Client not built — run `npm run build` in the client directory.';
  }
});

app.listen(port, () => {
  console.log(`ibscanner listening on http://localhost:${port}`);
  console.log(`IBKR Client Portal API: ${config.ibkr.baseUrl}`);
  console.log(`Config: ${configPath} (${config.scanners.length} scanners)`);
});
