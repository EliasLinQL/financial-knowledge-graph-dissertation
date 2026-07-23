"""Query and validate the Neo4j financial knowledge graph."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml
from dotenv import load_dotenv
from neo4j import GraphDatabase, RoutingControl
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable


NODE_FILES = {
    "Article": "articles.csv",
    "Asset": "assets.csv",
    "Company": "companies.csv",
    "Event": "events.csv",
    "Industry": "industries.csv",
    "MarketObservation": "market_observations.csv",
    "Sector": "sectors.csv",
}
RELATIONSHIP_FILES = {
    "BELONGS_TO": ("company_belongs_to_industry.csv",),
    "HAS_MARKET_OBSERVATION": (
        "event_has_market_observation.csv",
        "asset_has_market_observation.csv",
    ),
    "ISSUES": ("company_issues_asset.csv",),
    "MENTIONS": ("article_mentions_company.csv",),
    "PART_OF": ("industry_part_of_sector.csv",),
    "POTENTIALLY_AFFECTS": ("event_potentially_affects_company.csv",),
    "REPORTS": ("article_reports_event.csv",),
}


NODE_COUNT_QUERY = """
MATCH (n)
UNWIND labels(n) AS label
RETURN label, count(*) AS count
ORDER BY label
"""

RELATIONSHIP_COUNT_QUERY = """
MATCH ()-[r]->()
RETURN type(r) AS relationship_type, count(*) AS count
ORDER BY relationship_type
"""

COMPANY_EVENT_SUMMARY_QUERY = """
MATCH (c:Company)
WHERE $company_id IS NULL OR c.company_id = $company_id
OPTIONAL MATCH (e:Event)-[r:POTENTIALLY_AFFECTS]->(c)
WHERE ($event_type IS NULL OR e.event_type = $event_type)
  AND ($start_date IS NULL OR e.event_date >= date($start_date))
  AND ($end_date IS NULL OR e.event_date <= date($end_date))
WITH c,
     count(DISTINCT e) AS event_count,
     count(r) AS relationship_count,
     avg(r.relationship_focus_score) AS avg_focus_score
RETURN c.company_id AS company_id,
       c.name AS company,
       event_count,
       relationship_count,
       CASE
           WHEN avg_focus_score IS NULL THEN NULL
           ELSE round(avg_focus_score, 2)
       END AS avg_focus_score,
       CASE WHEN event_count = 0 THEN 'no_qualified_events' ELSE 'covered' END
           AS coverage_status
ORDER BY event_count DESC, company
"""

EVENT_EVIDENCE_QUERY = """
MATCH (a:Article)-[:REPORTS]->(e:Event)
      -[r:POTENTIALLY_AFFECTS]->(c:Company)
WHERE ($company_id IS NULL OR c.company_id = $company_id)
  AND ($event_type IS NULL OR e.event_type = $event_type)
  AND ($start_date IS NULL OR e.event_date >= date($start_date))
  AND ($end_date IS NULL OR e.event_date <= date($end_date))
RETURN c.company_id AS company_id,
       c.name AS company,
       e.event_id AS event_id,
       e.event_date AS event_date,
       e.event_type AS event_type,
       e.title AS event_title,
       e.classification_confidence AS classification_confidence,
       r.link_confidence AS relationship_confidence,
       r.relationship_focus_score AS relationship_focus_score,
       r.evidence_sentence AS evidence_sentence,
       r.rule_evidence_sentence AS rule_evidence_sentence,
       r.nlp_relationship_label AS nlp_relationship_label,
       r.nlp_relationship_score AS nlp_relationship_score,
       r.nlp_positive_probability AS nlp_positive_probability,
       r.hybrid_decision_reason AS hybrid_decision_reason,
       r.nlp_model_name AS nlp_model_name,
       r.nlp_model_revision AS nlp_model_revision,
       a.article_id AS article_id,
       a.web_url AS source_url
ORDER BY event_date DESC, company, event_id
LIMIT $evidence_limit
"""

EVENT_MARKET_SUMMARY_QUERY = """
MATCH (e:Event)-[:POTENTIALLY_AFFECTS]->(c:Company)-[:ISSUES]->(asset:Asset)
MATCH (e)-[:HAS_MARKET_OBSERVATION]->(m:MarketObservation)
MATCH (asset)-[:HAS_MARKET_OBSERVATION]->(m)
WHERE ($company_id IS NULL OR c.company_id = $company_id)
  AND ($event_type IS NULL OR e.event_type = $event_type)
  AND ($start_date IS NULL OR e.event_date >= date($start_date))
  AND ($end_date IS NULL OR e.event_date <= date($end_date))
RETURN c.company_id AS company_id,
       c.name AS company,
       asset.symbol AS symbol,
       e.event_type AS event_type,
       m.window_trading_days AS window_days,
       count(*) AS observations,
       round(avg(m.cumulative_return) * 100, 3) AS avg_return_pct,
       round(percentileCont(m.cumulative_return, 0.5) * 100, 3)
           AS median_return_pct,
       round(
           avg(CASE WHEN m.cumulative_return > 0 THEN 1.0 ELSE 0.0 END) * 100,
           1
       ) AS positive_return_rate_pct,
       false AS causal_claim
ORDER BY company, event_type, window_days
"""

INTEGRITY_QUERIES = {
    "articles_without_reported_event": """
        MATCH (a:Article)
        WHERE NOT EXISTS { MATCH (a)-[:REPORTS]->(:Event) }
        RETURN count(a) AS actual
    """,
    "events_without_source_article": """
        MATCH (e:Event)
        WHERE NOT EXISTS { MATCH (:Article)-[:REPORTS]->(e) }
        RETURN count(e) AS actual
    """,
    "event_company_links_without_evidence": """
        MATCH (:Event)-[r:POTENTIALLY_AFFECTS]->(:Company)
        WHERE trim(coalesce(r.evidence_sentence, '')) = ''
        RETURN count(r) AS actual
    """,
    "market_observations_without_event": """
        MATCH (m:MarketObservation)
        WHERE NOT EXISTS { MATCH (:Event)-[:HAS_MARKET_OBSERVATION]->(m) }
        RETURN count(m) AS actual
    """,
    "market_observations_without_asset": """
        MATCH (m:MarketObservation)
        WHERE NOT EXISTS { MATCH (:Asset)-[:HAS_MARKET_OBSERVATION]->(m) }
        RETURN count(m) AS actual
    """,
    "duplicate_event_company_relationships": """
        MATCH (e:Event)-[r:POTENTIALLY_AFFECTS]->(c:Company)
        WITH e, c, count(r) - 1 AS extra
        WHERE extra > 0
        RETURN coalesce(sum(extra), 0) AS actual
    """,
    "market_observations_with_causal_claim": """
        MATCH (m:MarketObservation)
        WHERE coalesce(m.causal_claim, false) = true
        RETURN count(m) AS actual
    """,
}


@dataclass(frozen=True)
class ConnectionSettings:
    uri: str
    database: str
    user: str
    password_environment_variable: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the imported Neo4j graph and export reusable company-event, "
            "evidence-trace and descriptive market-summary CSV files."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the project YAML configuration.",
    )
    parser.add_argument("--company-id", help="Optional company ID, for example C003.")
    parser.add_argument(
        "--event-type",
        help="Optional event type, for example regulatory_event.",
    )
    parser.add_argument("--start-date", help="Optional inclusive ISO date (YYYY-MM-DD).")
    parser.add_argument("--end-date", help="Optional inclusive ISO date (YYYY-MM-DD).")
    parser.add_argument(
        "--evidence-limit",
        type=int,
        help="Maximum evidence rows. Defaults to neo4j_connection.evidence_limit.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="Override the configured Neo4j analysis output directory.",
    )
    parser.add_argument(
        "--allow-validation-failures",
        action="store_true",
        help="Write reports but return success even when graph validation fails.",
    )
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError("The configuration file must contain a YAML mapping.")
    return value


def resolve_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def validate_iso_date(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format: {value!r}") from exc
    return value


def csv_data_row_count(path: Path) -> int:
    if not path.exists():
        raise FileNotFoundError(f"Neo4j import file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for row in reader if any(cell.strip() for cell in row))


def expected_graph_counts(import_directory: Path) -> tuple[dict[str, int], dict[str, int]]:
    node_counts = {
        label: csv_data_row_count(import_directory / filename)
        for label, filename in NODE_FILES.items()
    }
    relationship_counts = {
        relationship_type: sum(
            csv_data_row_count(import_directory / filename) for filename in filenames
        )
        for relationship_type, filenames in RELATIONSHIP_FILES.items()
    }
    return node_counts, relationship_counts


def records_to_frame(records: Iterable[Any], columns: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame([record.data() for record in records])
    if frame.empty and columns:
        return pd.DataFrame(columns=columns)
    return frame


def execute_read(
    driver: Any,
    database: str,
    query: str,
    parameters: dict[str, Any] | None = None,
) -> pd.DataFrame:
    records, _, keys = driver.execute_query(
        query,
        parameters_=parameters or {},
        database_=database,
        routing_=RoutingControl.READ,
    )
    return records_to_frame(records, list(keys))


def count_mapping(frame: pd.DataFrame, key: str) -> dict[str, int]:
    if frame.empty:
        return {}
    return {str(row[key]): int(row["count"]) for _, row in frame.iterrows()}


def comparison_rows(
    category: str,
    expected: dict[str, int],
    actual: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in sorted(set(expected) | set(actual)):
        expected_value = expected.get(metric, 0)
        actual_value = actual.get(metric, 0)
        rows.append(
            {
                "category": category,
                "metric": metric,
                "expected": expected_value,
                "actual": actual_value,
                "status": "PASS" if expected_value == actual_value else "FAIL",
                "detail": "Compared with the latest generated Neo4j import CSV package.",
            }
        )
    return rows


def build_validation_report(
    driver: Any,
    database: str,
    import_directory: Path,
    company_summary: pd.DataFrame,
) -> pd.DataFrame:
    expected_nodes, expected_relationships = expected_graph_counts(import_directory)
    actual_node_frame = execute_read(driver, database, NODE_COUNT_QUERY)
    actual_relationship_frame = execute_read(driver, database, RELATIONSHIP_COUNT_QUERY)

    rows = comparison_rows(
        "node_count",
        expected_nodes,
        count_mapping(actual_node_frame, "label"),
    )
    rows.extend(
        comparison_rows(
            "relationship_count",
            expected_relationships,
            count_mapping(actual_relationship_frame, "relationship_type"),
        )
    )
    rows.extend(
        [
            {
                "category": "total",
                "metric": "all_nodes",
                "expected": sum(expected_nodes.values()),
                "actual": int(actual_node_frame["count"].sum()),
                "status": (
                    "PASS"
                    if sum(expected_nodes.values()) == int(actual_node_frame["count"].sum())
                    else "FAIL"
                ),
                "detail": "Total node count.",
            },
            {
                "category": "total",
                "metric": "all_relationships",
                "expected": sum(expected_relationships.values()),
                "actual": int(actual_relationship_frame["count"].sum()),
                "status": (
                    "PASS"
                    if sum(expected_relationships.values())
                    == int(actual_relationship_frame["count"].sum())
                    else "FAIL"
                ),
                "detail": "Total relationship count.",
            },
        ]
    )

    for metric, query in INTEGRITY_QUERIES.items():
        frame = execute_read(driver, database, query)
        actual_value = int(frame.iloc[0]["actual"]) if not frame.empty else 0
        rows.append(
            {
                "category": "integrity",
                "metric": metric,
                "expected": 0,
                "actual": actual_value,
                "status": "PASS" if actual_value == 0 else "FAIL",
                "detail": "Automated graph-integrity rule; no manual review required.",
            }
        )

    uncovered = 0
    if not company_summary.empty:
        uncovered = int((company_summary["event_count"] == 0).sum())
    rows.append(
        {
            "category": "coverage",
            "metric": "companies_without_qualified_events",
            "expected": "",
            "actual": uncovered,
            "status": "INFO",
            "detail": (
                "Coverage observation only. Zero-event companies are retained rather than "
                "lowering the automatic evidence threshold."
            ),
        }
    )
    return pd.DataFrame(rows)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def connection_settings(config: dict[str, Any]) -> ConnectionSettings:
    section = config.get("neo4j_connection", {})
    if not isinstance(section, dict):
        raise ValueError("neo4j_connection must be a YAML mapping.")
    return ConnectionSettings(
        uri=str(section.get("uri", "neo4j://127.0.0.1:7687")),
        database=str(section.get("database", "neo4j")),
        user=str(section.get("user", "neo4j")),
        password_environment_variable=str(
            section.get("password_environment_variable", "NEO4J_PASSWORD")
        ),
    )


def run() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    project_root = config_path.parent.parent
    load_dotenv(project_root / ".env")

    settings = connection_settings(config)
    password = os.getenv(settings.password_environment_variable, "").strip()
    if not password:
        raise RuntimeError(
            f"{settings.password_environment_variable} is missing. Add it to "
            f"{project_root / '.env'}; do not put the password in config.yaml."
        )

    start_date = validate_iso_date(args.start_date, "--start-date")
    end_date = validate_iso_date(args.end_date, "--end-date")
    if start_date and end_date and start_date > end_date:
        raise ValueError("--start-date cannot be later than --end-date.")

    connection_config = config.get("neo4j_connection", {})
    import_config = config.get("neo4j_import", {})
    if not isinstance(connection_config, dict) or not isinstance(import_config, dict):
        raise ValueError("neo4j_connection and neo4j_import must be YAML mappings.")

    evidence_limit = (
        args.evidence_limit
        if args.evidence_limit is not None
        else int(connection_config.get("evidence_limit", 500))
    )
    if evidence_limit <= 0:
        raise ValueError("--evidence-limit must be a positive integer.")

    output_directory = resolve_path(
        project_root,
        args.output_directory
        or connection_config.get("query_output_directory", "data/neo4j/analysis"),
    )
    import_directory = resolve_path(
        project_root,
        import_config.get("output_directory", "data/neo4j/import"),
    )
    parameters = {
        "company_id": args.company_id or None,
        "event_type": args.event_type or None,
        "start_date": start_date,
        "end_date": end_date,
        "evidence_limit": evidence_limit,
    }

    with GraphDatabase.driver(
        settings.uri,
        auth=(settings.user, password),
    ) as driver:
        driver.verify_connectivity()
        company_summary = execute_read(
            driver, settings.database, COMPANY_EVENT_SUMMARY_QUERY, parameters
        )
        evidence_trace = execute_read(
            driver, settings.database, EVENT_EVIDENCE_QUERY, parameters
        )
        market_summary = execute_read(
            driver, settings.database, EVENT_MARKET_SUMMARY_QUERY, parameters
        )
        complete_coverage_summary = execute_read(
            driver,
            settings.database,
            COMPANY_EVENT_SUMMARY_QUERY,
            {
                "company_id": None,
                "event_type": None,
                "start_date": None,
                "end_date": None,
                "evidence_limit": evidence_limit,
            },
        )
        validation_report = build_validation_report(
            driver,
            settings.database,
            import_directory,
            complete_coverage_summary,
        )

    outputs = {
        "graph_validation.csv": validation_report,
        "company_event_summary.csv": company_summary,
        "event_evidence_trace.csv": evidence_trace,
        "event_market_summary.csv": market_summary,
    }
    for filename, frame in outputs.items():
        write_csv(frame, output_directory / filename)

    failures = int((validation_report["status"] == "FAIL").sum())
    print(f"Neo4j connection verified: {settings.uri} / {settings.database}")
    print(f"Validation failures: {failures}")
    print(f"Company summary rows: {len(company_summary)}")
    print(f"Evidence trace rows: {len(evidence_trace)}")
    print(f"Market summary rows: {len(market_summary)}")
    print(f"Reports written to: {output_directory}")
    if failures and not args.allow_validation_failures:
        return 2
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except AuthError as exc:
        print(
            "Neo4j authentication failed. Check the configured user and "
            "NEO4J_PASSWORD in .env.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except ServiceUnavailable as exc:
        print(
            "Neo4j is unavailable. Start the local instance and check the configured URI.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except (Neo4jError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Neo4j analysis failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
