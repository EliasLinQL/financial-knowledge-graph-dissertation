# Financial Event Intelligence Dashboard

This directory contains the bilingual dashboard and its research assistant.
The browser reads a static, credential-free snapshot; the server-side worker
handles assistant requests.

## Features

- company, event, evidence and source exploration;
- signed market context before and after publication;
- shared-event network and company comparison views;
- English and Chinese interface text; and
- a research assistant constrained to checked snapshot data.

The application is not a trading system and does not provide investment
advice. Market returns are descriptive, not causal.

## Requirements

- Node.js `>=22.13.0`
- npm
- Python 3.12 only when rebuilding the complete research snapshot

## Run the public demonstration

The repository includes a compact synthetic
`public/data/dashboard.json`. It contains fictional companies, events and
`example.test` links.

```powershell
npm ci
npm test
npm run dev
```

Open the local address printed by the development server. The assistant works
without an API key in checked-data preview mode.

## Enable AI explanations

Copy the local credential template:

```powershell
Copy-Item .dev.vars.example .dev.vars
```

Edit `.dev.vars`:

```dotenv
OPENAI_API_KEY=your_server_side_key
OPENAI_MODEL=gpt-4.1-mini
```

Restart `npm run dev` after changing the file.

Security requirements:

- never commit `.dev.vars`;
- never place the key in `NEXT_PUBLIC_*`, `VITE_*` or browser code;
- configure the key as a server-side runtime secret in production; and
- rotate a key immediately if it is printed, shared or committed accidentally.

The assistant endpoint is `/api/research-assistant`. It can explain the
snapshot, a company, an event or a company connection. It cannot query live
Neo4j, execute arbitrary Cypher or access files outside the checked snapshot.

## Use the complete research snapshot

From the repository root, first generate the analyst report, GDS analysis and
analyst-use-case evaluation. Then run:

```powershell
.\run_frontend_dashboard.ps1 -Stage snapshot
```

The snapshot builder verifies source SHA-256 hashes, graph counts, read-only
contracts and non-causal market semantics. It replaces the tracked synthetic
demonstration locally. The complete file may contain licensed article text, so
review `git status` and never stage that replacement for public release.

## Build for production

Use a clean dependency installation and run all checks:

```powershell
npm ci
npm test
npm run lint
npm run build
```

The build produces Cloudflare Worker-compatible ESM output under `dist/` and
copies the hosting metadata from `.openai/hosting.json`. `dist/` is generated
and must not be committed.

## Deploy

1. Build the exact commit that will be deployed.
2. Use a hosting service that supports the generated Cloudflare
   Worker-compatible output, or the configured Sites deployment workflow.
3. Upload the generated `dist/` package together with its hosting metadata.
4. Set `OPENAI_API_KEY` and optional `OPENAI_MODEL` as server-side runtime
   secrets in the hosting control plane.
5. Do not expose either value to client-side environment variables.
6. Verify `/`, `/data/dashboard.json` and `/api/research-assistant` after
   deployment.

No D1 database or R2 bucket is required by the current application. Deployment
credentials and runtime secrets belong in the hosting platform, not in this
repository.

## Optional local proxy

If server-side OpenAI requests require an HTTP(S) proxy, launch the application
from the repository root:

```powershell
.\run_frontend_dashboard.ps1 `
  -SkipSnapshot `
  -ProxyUrl "http://127.0.0.1:7890"
```

The proxy URL remains in the local host process and is not sent to the browser.

## Data contract

The snapshot contains:

- `scope` and `summary` metadata;
- `companies`, `events`, `impacts`, `sources` and signed `market` observations;
- `network` and `visualizations` for graph views; and
- `evaluation` results and interpretation `disclaimers`.

Correct upstream data and rebuild the snapshot instead of editing it manually.

## Release checklist

- `npm ci` completes from `package-lock.json`;
- `npm test` and `npm run lint` pass;
- `npm run build` produces `dist/server/index.js`;
- `.dev.vars`, credentials, caches and complete licensed snapshots are absent
  from Git; and
- the deployed assistant returns checked evidence without exposing internal
  identifiers, prompts or secrets.
