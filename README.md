# Financial Event Knowledge Graph

This repository implements a financial-event knowledge graph for analysing
company-related news, market context and graph structure. It combines Guardian
news, Twelve Data market prices, rule-based extraction, local Natural Language
Inference (NLI), Neo4j and Neo4j Graph Data Science (GDS).

The configured study covers 25 companies from 1 July 2025 to 30 June 2026.
Market returns are descriptive context only; they are not evidence that a news
event caused a price movement.

## What is included

- a reproducible data-to-Neo4j pipeline;
- evidence-grounded event extraction and cross-article deduplication;
- signed 1-, 3- and 7-trading-day windows before and after publication;
- read-only GDS and five fixed analyst-use-case evaluations;
- a bilingual analyst dashboard;
- a constrained research assistant that explains checked dashboard data; and
- unit, integration-contract and frontend tests.

Raw API responses, licensed article text, market-price files, model caches,
database exports, credentials and local working documents are not published.

## Quick start: dashboard and research assistant

The public repository includes a small synthetic snapshot. It is sufficient to
review the interface and assistant without private infrastructure or licensed
news text.

Requirements: Node.js `>=22.13.0` and npm.

```powershell
Set-Location frontend
npm ci
npm test
npm run dev
```

Open the local address printed by the development server. Without an API key,
the assistant uses a deterministic checked-data preview.

To enable model-generated explanations:

```powershell
Copy-Item .dev.vars.example .dev.vars
```

Set the following values in the ignored `frontend/.dev.vars` file:

```dotenv
OPENAI_API_KEY=your_server_side_key
OPENAI_MODEL=gpt-4.1-mini
```

Restart the development server after changing these values. The key is read by
the server-side worker only and must never use a browser-exposed variable name
such as `NEXT_PUBLIC_*` or `VITE_*`.

Production build and deployment guidance is in
[`frontend/README.md`](frontend/README.md).

## Research workflow

1. Validate ranked-company market coverage.
2. Collect and filter company-stratified Guardian articles.
3. Select company-bearing evidence spans.
4. Apply event rules, local NLI and grammatical role checks.
5. Cluster cross-article mentions into canonical events.
6. Align event-company pairs with signed market windows.
7. Build and validate a Neo4j import package.
8. Export evaluation and analyst-report packages.
9. Run read-only GDS and analyst-use-case evaluations against the frozen graph.

Every canonical event retains source provenance through
`Article-[:REPORTS]->Event-[:POTENTIALLY_AFFECTS]->Company`. GDS uses temporary
in-memory projections and does not write algorithm results to Neo4j.

## Repository map

```text
config/config.yaml                 Study scope and thresholds
data/config/                       Dated company-ranking inputs
src/                               Pipeline, evaluation and export modules
frontend/                          Dashboard and research assistant
tests/                             Python tests
run_full_pipeline.ps1              Main pipeline entry point
run_gds_analysis.ps1               Read-only GDS entry point
run_analyst_use_case_evaluation.ps1
run_analyst_report.ps1             Analyst-report export
run_frontend_dashboard.ps1         Dashboard snapshot/build/dev entry point
```

## Python and Neo4j setup

The project was developed with Python 3.12 and Windows PowerShell. Neo4j and
GDS must come from compatible release lines; the reviewed environment used
Neo4j Enterprise 2026.06.0 and GDS 2026.06.0.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For the CUDA configuration used in the main run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

Set `nlp_enrichment.device: -1` in `config/config.yaml` for CPU-only NLI.

Copy the credential template and fill it locally:

```powershell
Copy-Item .env.example .env
```

```dotenv
TWELVE_DATA_API_KEY=...
GUARDIAN_API_KEY=...
NEO4J_PASSWORD=...
```

The `.env` file is ignored by Git.

## Running the pipeline

Allow scripts for the current PowerShell process:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Build the core data-to-Neo4j import package:

```powershell
.\run_full_pipeline.ps1 -Stage all
```

When retained source data already exists, rebuild downstream outputs without
repeating API collection:

```powershell
.\run_full_pipeline.ps1 -Stage downstream
```

Auxiliary stages can be run independently:

```powershell
.\run_full_pipeline.ps1 -Stage gds
.\run_full_pipeline.ps1 -Stage usecases
.\run_full_pipeline.ps1 -Stage report
```

`gds` and `usecases` require a running Neo4j instance containing the reviewed
graph. They are intentionally excluded from `-Stage all`, which builds the
import package without silently replacing a live database.

After reviewing the generated import files, replace the project-managed graph
explicitly:

```powershell
.\.venv\Scripts\python.exe src\reload_neo4j.py `
  --config config\config.yaml `
  --confirm-replace
```

## Dashboard with the complete research snapshot

Authorised users who have generated the analyst, GDS and use-case packages can
replace the synthetic dashboard data with the validated research snapshot:

```powershell
.\run_frontend_dashboard.ps1 -Stage snapshot
```

The builder checks source hashes, graph counts, read-only contracts and market
semantics before writing `frontend/public/data/dashboard.json`. The browser
never connects directly to Neo4j.

## Tests

Run Python tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run frontend build and research-assistant tests:

```powershell
Set-Location frontend
npm ci
npm test
npm run lint
```

Live Neo4j/GDS integration tests are opt-in; the default suite does not mutate
the graph.

## Interpretation boundaries

- Corpus coverage is limited to the selected Guardian sample.
- NLI probability is model confidence in a relationship label, not the
  probability that an event is true or price-moving.
- Signed market windows describe surrounding prices and do not identify causal
  effects.
- Shared-event similarity, communities and centrality depend on the constructed
  graph; they are not confirmed commercial relationships or systemic risk.
- Automated analyst tasks test completeness and traceability, not precision,
  recall or human productivity.
- Local warm-cache timings are not deployed-system performance benchmarks.

Authorised users can reconstruct derived datasets subject to provider terms,
availability and API changes. Repeated retrieval may not produce byte-identical
source data.
