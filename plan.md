# ibscanner — Project Plan

## Background

The original repo is a successful POC: a terminal UI (Textual) stock market scanner for Interactive Brokers, driven by a YAML config file. It uses `ib_async` to connect to IB Gateway or TWS over a socket.

Two problems prevent it moving beyond local use:

1. **No venv management** — every session requires activating/deactivating a virtual environment.
2. **IB Gateway requires a GUI login** — it cannot run headlessly, which blocks cloud hosting.

The goal is to convert this into a web app hosted on AWS.

---

## Key Decisions

### Replace `ib_async` with the IBKR Client Portal Web API

IB Gateway (and TWS) require a desktop GUI login. The **IBKR Client Portal Web API** is a REST API served by the Client Portal Gateway — a lightweight Java process that authenticates via a browser once and then accepts REST calls. This unblocks headless / cloud deployment.

All market data operations (`secdef/search`, `marketdata/history`, `scanner/run`) are available as REST endpoints at `https://localhost:5000` by default.

### Full TypeScript stack

The app is simple enough that Python brings no advantage. TypeScript across the stack means:
- Shared types between frontend and backend
- No Python dependency management or venv
- Trivial containerisation (Node image)

### Backend: Koa

Koa was chosen as the Node.js server framework — lightweight, middleware-based, clean async model.

### Frontend: React + Vite

React for the UI, Vite for the dev/build toolchain. SSE (`EventSource`) for live scanner updates pushed from the server.

### Pure client-side (in progress — `react-app` branch)

The app may not need a server at all. `technicalindicators` and `expr-eval` are both pure JS libraries that run in the browser. The React app can call the IBKR Client Portal API directly via `fetch`, run scanner loops with `setInterval`, and compute indicators entirely client-side.

The only prerequisite is configuring CORS on the Client Portal Gateway (`conf.yaml`) to allow the app's origin, and accepting the self-signed cert in the browser once.

If viable, this eliminates the Node server entirely — the app becomes a static bundle deployable to S3, Netlify, or GitHub Pages.

---

## Roadmap

The agreed implementation order:

### Step 1 — Convert TUI to web app ✅
Replace Textual with a FastAPI (Python) backend + SSE + plain HTML frontend. Proved the architecture before committing to the language swap.

### Step 2 — Full TypeScript rewrite ✅ (`dev` branch)
Replace the entire Python stack with TypeScript:

```
server/                        client/
  src/                           src/
    types.ts                       types.ts
    config.ts   ← js-yaml          App.tsx
    ibkr.ts     ← axios            components/
    indicators.ts ← technicalindicators  ScannerPane.tsx
    scanner.ts  ← expr-eval        index.css
    index.ts    ← Koa            index.html
                                 vite.config.ts   ← proxies /api to Koa
```

Config change: `ib: host/port/client_id` → `ibkr: base_url: https://localhost:5000`.
Scanner YAML format (symbols, conditions, bar sizes, etc.) is unchanged.

To run in development:
```sh
cd server && npm install && npm run dev   # Koa on :8000
cd client && npm install && npm run dev  # Vite on :5173, proxies /api
```

### Step 3 — Pure client-side React app 🚧 (`react-app` branch)
Evaluate removing the server entirely. The React app calls the IBKR Client Portal API directly from the browser. Requires:

- CORS enabled on Client Portal Gateway (`conf.yaml`: `cors: "http://localhost:5173"`)
- User visits `https://localhost:5000` once to accept the self-signed TLS cert

Benefits: no Node process, no SSE plumbing, trivially hostable as static files.
Tradeoff: browser tab throttling may affect scan intervals when backgrounded.

### Step 4 — Containerise
Once the app shape is settled (client-only or client+server), add a `Dockerfile` and `docker-compose.yml`. A web app containerises cleanly — a static build served by nginx, or a Node container for the server variant.

### Step 5 — Deploy to AWS
Options depending on outcome of Step 3:
- **Client-only**: S3 + CloudFront (static hosting). Client Portal Gateway runs locally or on a small EC2.
- **Client + server**: ECS Fargate (serverless containers) or EC2 + Docker.

---

## Current State

| Branch | Status |
|---|---|
| `main` | Original Python TUI (POC) |
| `dev` | Full TypeScript rewrite (Koa + React + Client Portal API) |
| `react-app` | In progress — evaluating pure client-side approach |
