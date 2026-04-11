# ibscanner — Project Plan (react-app branch)

## Background

The original repo was a successful POC: a Textual TUI stock scanner driven
by a YAML config, talking to IB Gateway over `ib_async`. Two problems
blocked it from growing beyond local use:

1. **No venv management** — every session required activating/deactivating
   a virtualenv.
2. **IB Gateway requires a GUI login** — headless-impossible, which blocks
   cloud hosting.

A follow-up iteration (`dev` branch) rewrote the stack in TypeScript as a
Koa + React + SSE app against IBKR's Client Portal Web API. That worked
but hit a fundamental wall:

**IBKR's Web API is not natively cloud-accessible for retail accounts.**
OAuth 1.0a Third-Party Platform access is gated behind a formal IBKR
application process and is not available to personal projects. The only
retail-accessible path is running the **Client Portal Gateway** — a Java
process that terminates a local REST endpoint at `https://localhost:5000`
after a browser login.

Given that constraint, the Koa server in the `dev` branch is pure
middleware — it just proxies REST calls from the React client to the
gateway, computes indicators, and evaluates expressions. Every one of
those things can happen in the browser. The server earns its place only
if it's hiding IBKR credentials or aggregating multi-user state, and
neither applies.

**Decision: drop the server entirely.** Build ibscanner as a pure
client-side React app that talks directly to the Client Portal Gateway.

---

## Target architecture

```
┌─────────────────────┐       HTTPS        ┌──────────────────┐   HTTPS   ┌─────────────┐
│  React app (Vite)   │───────────────────▶│  Client Portal   │──────────▶│ IBKR cloud  │
│  http://localhost:  │                    │  Gateway         │           │             │
│  5173  (dev)        │                    │  localhost:5000  │           │             │
└─────────────────────┘                    └──────────────────┘           └─────────────┘
   │
   ├─ parses scanners.yaml in-browser (js-yaml)
   ├─ fetches bars + scanner results directly (fetch)
   ├─ computes indicators in-browser (technicalindicators)
   ├─ evaluates expressions in a tiny sandbox
   └─ runs each scanner on its own setInterval loop
```

No Node server. No SSE. No middleware. The build artifact is a static
bundle (`dist/`) deployable anywhere — S3, Netlify, Pages, `file://`.

### Prerequisites for this to work

1. **CORS enabled on the gateway.** Edit `clientportal.gw/root/conf.yaml`
   to allow requests from the app's origin:
   ```yaml
   ips:
     allow:
       - 127.0.0.1
   cors: "http://localhost:5173"   # or your deployed origin
   ```
2. **User accepts the gateway's self-signed TLS cert once.** Visit
   `https://localhost:5000` in the browser tab where you'll also load
   ibscanner, click through the warning. Same-tab is important so the
   exception persists for subsequent `fetch()` calls.
3. **User has logged into the gateway's browser session.** Same flow as
   before — `https://localhost:5000` → login. ibscanner can detect and
   surface the unauthenticated state but cannot perform the login itself
   from JS.

---

## Revisiting earlier decisions

The `dev` branch made technology choices that were correct for a server
context but that need re-evaluation now that everything runs in the
browser.

| decision | `dev` branch | `react-app` branch | why |
|---|---|---|---|
| **Web server** | Koa | none | server is pure middleware; nothing to host |
| **HTTP client** | axios | native `fetch` | fetch is built-in; ~40KB bundle savings |
| **Expression eval** | mathjs | **filtrex** (proposed) | mathjs is ~500KB in a browser; filtrex is ~5KB and purpose-built for user-authored filter expressions over a row of named values — exactly our use case |
| **YAML parsing** | js-yaml | js-yaml | still valid; no native browser YAML parser, and YAML config is nicer than JSON for humans |
| **Indicator library** | technicalindicators | technicalindicators | pure JS, works in browser unchanged |
| **Real-time updates** | Koa SSE → EventSource | `setInterval` per scanner | no server means no stream; each scanner re-runs itself on its own refresh interval in a React effect |
| **Config delivery** | read `scanners.yaml` from disk | in-app YAML editor persisted to `localStorage`, with file-upload fallback | browser has no filesystem access; editor is the simplest UX |

### Why filtrex over mathjs

On `dev`, mathjs replaced `expr-eval` after an audit flagged two high
severity CVEs (prototype pollution + unrestricted function access).
mathjs fixed the security concern but is ~500KB, which is heavy in a
client bundle where users will see it every page load.

**filtrex** is ~5KB, explicitly built for "let a user write filter
expressions against a scope of named values," supports exactly the
operators we need (`<`, `>`, `<=`, `>=`, `==`, `!=`, `and`, `or`, `not`,
arithmetic, parentheses), and has no RCE surface because the parser
never produces function calls the caller hasn't whitelisted.

Alternatives considered:
- **expr-eval-fork** — maintained fork of `expr-eval` with the CVEs
  patched. ~20KB. Drop-in API. Fine if we want zero friction with the
  `dev` branch's existing condition strings.
- **Hand-rolled tokenizer** — ~60 lines for the subset we actually use.
  Zero deps. Most control. Good fallback if filtrex turns out to have
  an issue.

Going with filtrex unless it trips on an existing condition shape.

### What transfers from `dev` to `react-app`

- **Indicator pipeline** (`server/src/indicators.ts`) — moves to
  `client/src/lib/indicators.ts` essentially unchanged.
- **Config typing and parsing** (`server/src/config.ts` +
  `server/src/types.ts`) — moves to `client/src/lib/config.ts`,
  `client/src/types.ts`.
- **IBKR REST wrapper** (`server/src/ibkr.ts`) — moves to
  `client/src/lib/ibkr.ts`, rewritten against `fetch`, keeping the
  endpoint set we know works (`auth/status`, `iserver/accounts`,
  `tickle`, `secdef/search`, `marketdata/history`, `scanner/run`).
- **Scanner engine** (`server/src/scanner.ts`) — moves to
  `client/src/lib/scanner.ts`, swapping mathjs for filtrex.
- **Keepalive behavior** — the `keepalive()` helper that calls
  `/iserver/accounts` + `/tickle` every 60s moves into a top-level React
  effect in `App.tsx`.

### What gets deleted

- `server/` in its entirety
- `client/vite.config.ts` proxy block (no longer needed)
- `client/src/App.tsx` SSE plumbing (replaced with per-scanner interval
  hooks)

### UI shell reuse

`client/src/App.tsx`, `components/ScannerPane.tsx`, and `index.css` on
the `dev` branch already render a passable tabs-and-table UI. The
intent is to **reuse that shell** and replace only the data-loading
layer — not scrap the frontend entirely.

---

## Roadmap

### Step 1 — Plan approved + branch set up ✅
You're reading it.

### Step 2 — Migrate server logic to `client/src/lib/`
- Move `indicators.ts`, `config.ts`, `scanner.ts`, `types.ts` under
  `client/src/lib/`.
- Rewrite `ibkr.ts` as a fetch-based client. Keep the same method names
  so `scanner.ts` doesn't need changes beyond the eval swap.
- Swap mathjs → filtrex in `scanner.ts`.

### Step 3 — Wire scanners into React
- Parse YAML from `localStorage` (or a bundled default on first load).
- One `useScanner(scanner)` hook per scanner → returns
  `{ result, error, lastRunAt }`, handles its own `setInterval`.
- `useAuthStatus()` hook → calls `/iserver/auth/status` + keepalive on a
  60s interval, surfaces `authenticated` / `connected` booleans to the
  UI banner.
- Gate scanner loops on `authenticated === true`.

### Step 4 — Config UX
- In-app YAML editor (textarea, monospace, validate on blur).
- Persist to `localStorage`. File-upload as a nice-to-have.
- Ship a default example config embedded in the bundle.

### Step 5 — Delete `server/`
Once every scanner cycles without the Koa server running, remove
`server/` entirely. Update `README.md` to match the new architecture.

### Step 6 — Containerise (optional)
For hosted deployment: static bundle served by nginx or similar; point
at a [voyz/ibeam](https://github.com/voyz/ibeam) container for the
gateway. `ibkr.base_url` is the only knob that changes.

### Step 7 — Deploy
Client-only deployment options:
- **S3 + CloudFront** — cheapest, static files only. Gateway runs on a
  sidecar (ibeam container on EC2 or Fargate).
- **Netlify / Vercel / GitHub Pages** — zero-infra for the frontend;
  same gateway caveat.

---

## Open decisions

These need a call before Step 2 starts:

1. **filtrex vs expr-eval-fork vs hand-rolled.** Recommendation: filtrex
   for minimal bundle + explicit safety story.
2. **Disposition of uncommitted `dev` work on this branch.** Current
   react-app branch carries the mathjs swap, auth/keepalive additions,
   and README rewrite from the `dev` session as uncommitted changes.
   Options:
   - Commit them on `dev` (via a quick branch-switch) before doing
     react-app work, so `dev` is a preserved "finished server" state.
   - Discard them — most of the code is being deleted anyway, though
     the auth/keepalive learnings and the endpoint verification are
     genuinely useful reference material.
   - Leave them uncommitted in the working tree and let them get
     reshaped as Step 2 moves code around.
3. **Stray `.js` files in `client/src/`** (`App.js`, `main.js`,
   `types.js`, `components/ScannerPane.js`) — untracked, look like
   accidental artifacts alongside the real `.tsx` sources. Probably
   safe to delete but worth confirming.

---

## Current state

| Branch | Status |
|---|---|
| `main` | Original Python TUI (POC) |
| `dev` | Full TypeScript rewrite — Koa server + React client over SSE + Client Portal API. Has uncommitted polish from the most recent session (mathjs, auth/keepalive, README). |
| `react-app` | **Active.** This plan. Client-only rewrite in progress. |
