# Financial Event Knowledge Graph

This repository contains the implementation used for a dissertation study of
company-related news events, market reactions and knowledge-graph
representation. The pipeline combines market data from Twelve Data, news from
the Guardian Open Platform, rule-based event extraction, local Natural
Language Inference (NLI), and Neo4j.

The current configuration covers 1 July 2025 to 30 June 2026. It selects 25
companies from a market-cap-ranked candidate pool, subject to market-data
quality and minimum news-coverage requirements.

This is a research prototype. Event-window returns are descriptive and are not
interpreted as evidence that a news event caused a price movement.

## Research workflow

The pipeline applies the following stages:

1. Validate daily OHLCV coverage and form a ranked market-eligible pool.
2. Collect a monthly, company-stratified Guardian news sample.
3. Remove query-only matches and retain explicit company or product evidence.
4. Select 25 companies that meet the configured news-coverage thresholds,
   using ranked backfill where necessary.
5. Select up to two non-overlapping company-bearing evidence spans per
   Article-Company pair. A span is either one sentence or an anaphorically
   linked two-sentence window from the same source paragraph.
6. Validate the same evidence span with a local NLI model, then apply narrow
   grammatical role checks for actor, target and contextual-list mentions.
7. Cluster qualified, cross-article Event mentions into canonical Events using
   temporal, company, event-type and complete-link text-similarity gates.
8. Align canonical Event-Company pairs with 1-, 3- and 7-trading-day market
   windows.
9. Build and validate a Neo4j import package.
10. Export automatic rule/NLP/deduplication ablation and threshold-sensitivity
    reports without changing the graph.
11. Export a read-only analyst package with canonical events, selected
    source-evidence spans, descriptive market windows and graph checks.

The event taxonomy contains corporate, regulatory, geopolitical,
macroeconomic, commodity and market-wide events. Article headlines remain on
the Article node as source metadata; Event titles, types and company
relationships are all grounded in the same selected evidence span. Intermediate
scores, raw and calibrated relationship labels, evidence spans and decision
reasons are retained in local outputs for auditability. When multiple articles
describe the same Event, every source mention remains connected through a
property-bearing `REPORTS` relationship; deduplication does not delete source
evidence.

## Repository contents

```text
config/
  config.yaml                     Study scope, thresholds and output paths
data/config/
  marketcapwatch_top40_*.csv      Frozen ranking snapshot
  company_candidates_top25_*.csv  Company, ticker and alias mappings
src/
  validate_market_data.py         Market-data eligibility checks
  collect_guardian_news.py        Guardian collection and caching
  prepare_guardian_news.py        Article-company evidence cleaning
  select_news_coverage.py         Coverage thresholds and ranked backfill
  extract_event_candidates.py     Evidence-span event extraction
  enrich_events_nlp.py            Same-span NLI and role validation
  deduplicate_events.py            Cross-article canonical Event clustering
  align_event_market_data.py      Event-window alignment
  build_kg_import.py              Neo4j package construction
  reload_neo4j.py                 Controlled database replacement
  query_kg.py                     Graph validation and analysis exports
  evaluate_pipeline.py            Ablation and threshold-sensitivity reports
  export_analyst_report.py        Read-only analyst-report package
tests/                             Unit and configuration tests
run_full_pipeline.ps1             PowerShell pipeline entry point
run_analyst_report.ps1            Neo4j analyst-report entry point
```

Raw API responses, market-price files, model caches, database exports,
dissertation drafts and local credentials are intentionally excluded from the
public repository.

## Environment

The project was developed for Python 3.12, Neo4j 5.x and Windows PowerShell.
Create a local virtual environment and install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For the CUDA configuration used in the main run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

The default configuration uses `cuda:0`. Set `nlp_enrichment.device` to `-1`
in `config/config.yaml` for a CPU-only run.

## Credentials

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Then provide the required values locally:

```dotenv
TWELVE_DATA_API_KEY=...
GUARDIAN_API_KEY=...
NEO4J_PASSWORD=...
```

The `.env` file is ignored by Git. The published configuration contains
environment-variable names, not credential values.

## Configuration

`config/config.yaml` is the main record of the study design. It defines:

- study dates and sample label;
- ranked candidate and final sample sizes;
- market-data completeness rules;
- Guardian collection limits and company aliases;
- news-coverage thresholds;
- event and relationship scoring rules;
- NLI model settings, including the exact model revision used for the study;
- conservative cross-article Event deduplication thresholds;
- automatic ablation and threshold-sensitivity grids;
- market-window definitions; and
- Neo4j input and analysis paths.

The dated CSV files under `data/config/` preserve the ranking snapshot and the
company-to-ticker mapping used by the configured run.

## Running the pipeline

Allow the script for the current PowerShell process:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Run the core data-to-Neo4j-import pipeline (stages 1–9):

```powershell
.\run_full_pipeline.ps1 -Stage all
```

Resume from a named processing point or run an auxiliary export:

```powershell
.\run_full_pipeline.ps1 -Stage market
.\run_full_pipeline.ps1 -Stage news
.\run_full_pipeline.ps1 -Stage coverage
.\run_full_pipeline.ps1 -Stage event
.\run_full_pipeline.ps1 -Stage nlp
.\run_full_pipeline.ps1 -Stage dedup
.\run_full_pipeline.ps1 -Stage kg
.\run_full_pipeline.ps1 -Stage analysis
.\run_full_pipeline.ps1 -Stage evaluation
.\run_full_pipeline.ps1 -Stage report
```

When the market and Guardian source files already exist locally, rebuild the
downstream outputs without repeating data collection:

```powershell
.\run_full_pipeline.ps1 -Stage downstream
```

An alternative configuration can be supplied with `-ConfigPath`.

## Neo4j

The graph contains `Article`, `Event`, `Company`, `Sector`, `Industry`, `Asset`
and `MarketObservation` nodes. Its principal relationships are `REPORTS`,
`MENTIONS`, `POTENTIALLY_AFFECTS`, `ISSUES`, `BELONGS_TO`, `PART_OF` and
`HAS_MARKET_OBSERVATION`.

After reviewing the generated import package, replace the project-managed
subgraph explicitly:

```powershell
.\.venv\Scripts\python.exe src\reload_neo4j.py `
  --config config\config.yaml `
  --confirm-replace
```

The loader backs up the current project-managed import files before replacing
the relevant labels and relationships.

## Analyst report

With the Neo4j instance running, generate the final read-only analyst package:

```powershell
.\run_analyst_report.ps1
```

The output contains `analyst_report_data.json` together with English and
Chinese Markdown briefings. The JSON package separates canonical events,
selected source evidence, descriptive market windows and graph-validation
results. Source article URLs and
`Article-[:REPORTS]->Event-[:POTENTIALLY_AFFECTS]->Company` provenance are
retained. Market returns are labelled as descriptive 1-, 3- and 7-trading-day
context and are never interpreted as causal effects.

Optional filters can be supplied without changing the graph:

```powershell
.\run_analyst_report.ps1 `
  -CompanyId C007 `
  -EventType regulatory_event `
  -StartDate 2025-07-01 `
  -EndDate 2026-06-30 `
  -MinimumNlpProbability 0.50
```

## Tests

Run the test suite from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The tests cover configuration integrity, market-data validation, Guardian
collection limits, coverage-based sample selection, NLI decision rules,
cross-article Event deduplication, Neo4j package construction and controlled
graph replacement.

## Data availability

This repository publishes code and small configuration inputs only.

- Twelve Data OHLCV files are retained locally and are not redistributed.
- Guardian article text and API responses are retained locally and are not
  included in Git.
- Neo4j import files, analysis exports and backups may contain article text or
  licensed derived data and are therefore excluded.
- API credentials, local paths, dissertation drafts and development records
  are excluded.

Authorised users with valid source credentials can reconstruct the derived
datasets, subject to provider availability and terms. Repeated API retrieval
may not return byte-identical source data.

## Methodological limitations

- Market-cap ranking is a dated snapshot rather than a time-varying measure.
- Data availability affects which ranked companies enter the final sample.
- Guardian coverage varies by company and month.
- Rule and NLI thresholds prioritise transparent, reproducible decisions but
  do not replace a fully labelled financial-event benchmark.
- Cross-article deduplication is intentionally conservative and may retain
  heavily paraphrased duplicate Events.
- Market-window returns provide context only and do not identify causal
  effects.
