# Financial Event Knowledge Graph

This repository contains the implementation used for a dissertation study of
company-related news events, market reactions and knowledge-graph
representation. The pipeline combines market data from Twelve Data, news from
the Guardian Open Platform, rule-based event extraction, local Natural
Language Inference (NLI), and Neo4j.

An independent Neo4j Graph Data Science (GDS) stage performs exploratory
structural analysis over the frozen knowledge graph. It uses temporary
in-memory projections and does not write algorithm results back to Neo4j.

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
   windows before and after publication.
9. Build and validate a Neo4j import package.
10. Export automatic rule/NLP/deduplication ablation and threshold-sensitivity
    reports without changing the graph.
11. Export a read-only analyst package with canonical events, selected
    source-evidence spans, descriptive market windows and graph checks.
12. Run an optional read-only GDS stage for shared-event connectivity,
    canonical-event-neighbour similarity, community structure and
    supplementary centrality.
13. Evaluate five fixed analyst information-integration tasks against the
    frozen graph, with provenance/completeness checks and local query-time
    diagnostics.

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
  analyze_gds.py                  Read-only GDS structural analysis
  evaluate_analyst_use_cases.py   Five-task read-only analyst evaluation
  evaluate_pipeline.py            Ablation and threshold-sensitivity reports
  export_analyst_report.py        Read-only analyst-report package
  build_chapter4_results.py       Reproducible dissertation result package
tests/                             Unit and configuration tests
run_full_pipeline.ps1             PowerShell pipeline entry point
run_gds_analysis.ps1              Read-only GDS analysis entry point
run_analyst_use_case_evaluation.ps1
                                  Read-only analyst use-case evaluation
run_analyst_report.ps1            Neo4j analyst-report entry point
run_chapter4_results.ps1          Chapter 4 result-package entry point
```

Raw API responses, market-price files, model caches, database exports,
dissertation drafts and local credentials are intentionally excluded from the
public repository.

## Environment

The project was developed for Python 3.12, Neo4j and Windows PowerShell. The
GDS implementation has been verified with Neo4j Enterprise 2026.06.0 and GDS
2026.06.0. Neo4j and GDS must be installed from a compatible release line.
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
- market-window definitions;
- Neo4j input and analysis paths;
- GDS output paths, concurrency, shared-event support thresholds,
  NodeSimilarity, Louvain and PageRank parameters; and
- analyst use-case output paths, fixed task parameters, warm-up count,
  measured-run count and expected market windows.

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
.\run_full_pipeline.ps1 -Stage gds
.\run_full_pipeline.ps1 -Stage evaluation
.\run_full_pipeline.ps1 -Stage usecases
.\run_full_pipeline.ps1 -Stage report
.\run_full_pipeline.ps1 -Stage results
```

When the market and Guardian source files already exist locally, rebuild the
downstream outputs without repeating data collection:

```powershell
.\run_full_pipeline.ps1 -Stage downstream
```

An alternative configuration can be supplied with `-ConfigPath`.

The `gds` and `usecases` stages are intentionally not included in `-Stage all`:
both depend on a running Neo4j instance containing the reviewed, loaded graph,
whereas `all` builds the import package but does not silently replace the live
database. For the final frozen result package, use the order `gds` →
`usecases` → `results`.

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

## Graph Data Science analysis

With the reviewed knowledge graph loaded and the `financial-kg` Neo4j instance
running, execute either entry point:

```powershell
.\run_full_pipeline.ps1 -Stage gds
# or
.\run_gds_analysis.ps1
```

The stage creates two UUID-named, in-memory projections. A Company-Event
bipartite projection supports Jaccard NodeSimilarity over canonical Event
neighbours. A weighted, undirected Company co-event projection supports WCC,
weighted and unweighted Louvain sensitivity analysis, and weighted PageRank.
Every algorithm uses `stream` or `stats`; no GDS property or relationship is
written to the persisted graph. Projection names are checked and dropped in a
`finally` block, while before/after database counts provide an additional
non-mutation check.

For the current frozen graph, `company_coevent_edges.csv` contains 39 unordered
logical company pairs. The GDS projection reports 78 relationships because an
undirected relationship is stored in both directions in memory. This is a
storage representation, not 78 independent company relationships.

The default `outputs/gds_analysis/` package contains bilingual result
narratives, CSV tables for environment checks, projections, memory estimates,
algorithms, co-event edges, WCC, NodeSimilarity, Louvain, PageRank-derived
centrality, correlations and threshold sensitivity, plus three SVG figures.
`gds_manifest.json` records versions, parameters, expected import-artifact and
output SHA-256 hashes, a deterministic
fingerprint of the live Company/Event/POTENTIALLY_AFFECTS structure, cleanup
status and interpretation boundaries.

## Analyst report

With the Neo4j instance running, generate the final read-only analyst package:

```powershell
.\run_analyst_report.ps1
```

The runner currently generates `analyst_report_data.json` plus English and
Chinese Markdown briefings. The package separates canonical events, selected
source evidence, descriptive market windows and graph-validation results.
Source article URLs and
`Article-[:REPORTS]->Event-[:POTENTIALLY_AFFECTS]->Company` provenance are
retained. Market returns are labelled as descriptive 1-, 3- and 7-trading-day
windows before and after publication and are never interpreted as causal
effects.

Optional filters can be supplied without changing the graph:

```powershell
.\run_analyst_report.ps1 `
  -CompanyId C007 `
  -EventType regulatory_event `
  -StartDate 2025-07-01 `
  -EndDate 2026-06-30 `
  -MinimumNlpProbability 0.50
```

## Analyst use-case evaluation

Start the frozen `financial-kg` instance, make sure the GDS package belongs to
that graph, and run:

```powershell
.\run_full_pipeline.ps1 -Stage usecases
# or
.\run_analyst_use_case_evaluation.ps1
```

The stage executes five fixed tasks: whole-sample company screening; TSMC
event/evidence traceability; high-confidence regulatory-event alerting;
Alphabet event-to-market-context retrieval; and shared-event company-pair
discovery. Each query receives two warm-up executions followed by ten measured
executions. Node and relationship counts are captured before and after the
evaluation, and every query is routed as read-only.

The package contains nine CSV tables (five task results, summary, performance,
quality checks and per-run timings), bilingual narratives, two SVG figures
and a manifest containing parameters, hashes, timings and the non-mutation
contract. These results test
whether the prototype can consolidate and trace the required information. They
do not estimate precision, recall or analyst productivity. Localhost timings
use a warm cache and must not be generalised to a deployed multi-user system.
Market windows remain non-causal, and shared-event Jaccard is not business or
fundamental similarity.

## Dissertation results package

Once the pipeline evaluation, Neo4j import report, graph-validation export, GDS
package and analyst use-case evaluation exist, generate citation-ready Chapter
4 materials without calling APIs, rerunning NLP or changing Neo4j:

```powershell
.\run_full_pipeline.ps1 -Stage results
```

The Chapter 4 builder preserves the original 11 pipeline tables and five
figures, incorporates all 14 GDS tables and three GDS figures, and adds the
eight citation-level analyst-use-case tables and two figures. The evaluator's
low-level ninth table, `task_run_timings.csv`, remains in its audit package.
The default Chapter 4 package therefore
contains 33 citation-ready CSV tables in one numbered sequence and ten
publication figures in SVG, English and Chinese result narratives, and
a manifest with source-file SHA-256 values and the configuration hash. The
result narrative reports retention, coverage, threshold sensitivity, graph
structure, analyst-task completeness and graph integrity. It does not claim
precision or recall without a human-labelled benchmark, treat localhost
timings as a productivity benchmark, or interpret market windows as causal.

## Tests

Run the test suite from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The tests cover configuration integrity, market-data validation, Guardian
collection limits, coverage-based sample selection, NLI decision rules,
cross-article Event deduplication, Neo4j package construction and controlled
graph replacement. `tests/test_analyze_gds.py` additionally covers GDS
configuration validation, pair and community canonicalisation, structural
metric calculations, rank correlation and the read-only query contract.
The analyst-use-case tests validate the five fixed task definitions, required
field completeness, timing summaries, read-only query contract and manifest;
the Chapter 4 tests validate package manifests, source hashes, table sequencing
and SVG generation. Any live Neo4j/GDS integration test remains explicitly
opt-in.

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
- GDS structure is conditional on Guardian coverage, event extraction,
  relationship validation and canonical-event deduplication.
- Shared-event similarity is Jaccard similarity over canonical Event
  neighbours, not business-model or fundamental similarity.
- WCC, Louvain and PageRank are exploratory structural descriptions. They do
  not measure causal influence, systemic importance, investment quality or
  risk, and disconnected components limit strong cross-company ranking claims.
- Analyst use-case results are internal completeness and traceability checks,
  not precision/recall estimates or a human-productivity study.
- Measured query times come from ten warm-cache localhost executions after two
  warm-ups and are not deployable-system performance benchmarks.
