"""Evaluate five reproducible, read-only analyst use cases against Neo4j.

The evaluation measures local query latency, internal retrieval completeness and
evidence/provenance availability.  It deliberately does not estimate semantic
precision/recall, human productivity gains, or causal market effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.sax.saxutils import escape

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase, RoutingControl
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

try:
    from src.query_kg import (
        NODE_COUNT_QUERY,
        RELATIONSHIP_COUNT_QUERY,
        connection_settings,
        load_config,
        records_to_frame,
        resolve_path,
        validate_iso_date,
    )
except ModuleNotFoundError:
    from query_kg import (  # type: ignore[no-redef]
        NODE_COUNT_QUERY,
        RELATIONSHIP_COUNT_QUERY,
        connection_settings,
        load_config,
        records_to_frame,
        resolve_path,
        validate_iso_date,
    )


MANIFEST_FILENAME = "analyst_use_case_manifest.json"
REPORT_FILENAMES = (
    "analyst_use_case_evaluation_cn.md",
    "analyst_use_case_evaluation_en.md",
)
TABLE_FILENAMES = (
    "use_case_summary.csv",
    "task_performance.csv",
    "task_quality_checks.csv",
    "task_run_timings.csv",
    "task_1_company_screening.csv",
    "task_2_tsmc_evidence.csv",
    "task_3_regulatory_alerts.csv",
    "task_4_alphabet_market_context.csv",
    "task_5_shared_event_pairs.csv",
)
FIGURE_FILENAMES = (
    "use_case_latency.svg",
    "use_case_completeness.svg",
)


TASK_1_QUERY = """
MATCH (c:Company)
OPTIONAL MATCH (e:Event)-[impact:POTENTIALLY_AFFECTS]->(c)
WITH c,
     count(DISTINCT e) AS event_count,
     count(DISTINCT CASE
         WHEN trim(coalesce(impact.evidence_sentence, '')) <> '' THEN e
     END) AS evidenced_event_count
OPTIONAL MATCH (c)-[:ISSUES]->(asset:Asset)
RETURN c.company_id AS company_id,
       c.name AS company,
       c.source_rank AS source_rank,
       head(collect(DISTINCT asset.symbol)) AS symbol,
       event_count,
       evidenced_event_count,
       CASE WHEN event_count = 0 THEN 'no_qualified_events' ELSE 'covered' END
           AS coverage_status
ORDER BY source_rank, company_id
"""


TASK_2_QUERY = """
MATCH (e:Event)-[impact:POTENTIALLY_AFFECTS]->
      (c:Company {company_id: $company_id})
OPTIONAL MATCH (a:Article)-[report:REPORTS]->(e)
WITH c, e, impact,
     count(DISTINCT a) AS source_article_count,
     count(DISTINCT CASE
         WHEN trim(coalesce(a.web_url, '')) <> '' THEN a
     END) AS source_url_count,
     count(DISTINCT CASE
         WHEN report.source_event_id = impact.source_event_id
           OR (
               impact.source_event_id IS NULL
               AND coalesce(report.is_representative, true) = true
           )
         THEN a
     END) AS matching_source_count,
     collect(DISTINCT CASE
         WHEN trim(coalesce(a.web_url, '')) <> '' THEN a.web_url
     END) AS source_urls
RETURN c.company_id AS company_id,
       c.name AS company,
       e.event_id AS event_id,
       e.event_date AS event_date,
       e.event_type AS event_type,
       e.title AS event_title,
       impact.evidence_sentence AS evidence_sentence,
       impact.nlp_relationship_label AS nlp_relationship_label,
       impact.nlp_positive_probability AS nlp_positive_probability,
       impact.relationship_focus_score AS relationship_focus_score,
       source_article_count,
       source_url_count,
       matching_source_count,
       source_urls
ORDER BY event_date DESC, event_id
"""


TASK_3_QUERY = """
MATCH (e:Event {event_type: $event_type})-
      [impact:POTENTIALLY_AFFECTS]->(c:Company)
WHERE e.event_date >= date($start_date)
  AND e.event_date <= date($end_date)
  AND impact.nlp_positive_probability >= $minimum_nlp_probability
OPTIONAL MATCH (source_article:Article {article_id: impact.source_article_id})-
               [report:REPORTS]->(e)
WHERE report.source_event_id = impact.source_event_id
   OR (
       impact.source_event_id IS NULL
       AND coalesce(report.is_representative, true) = true
   )
RETURN c.company_id AS company_id,
       c.name AS company,
       e.event_id AS event_id,
       e.event_date AS event_date,
       e.event_type AS event_type,
       e.title AS event_title,
       impact.evidence_sentence AS evidence_sentence,
       impact.nlp_positive_probability AS nlp_positive_probability,
       impact.relationship_focus_score AS relationship_focus_score,
       source_article.article_id AS source_article_id,
       source_article.web_url AS source_url,
       report IS NOT NULL AS source_report_matched
ORDER BY event_date DESC, company, event_id
"""


TASK_4_QUERY = """
MATCH (e:Event)-[impact:POTENTIALLY_AFFECTS]->
      (c:Company {company_id: $company_id})-[:ISSUES]->(asset:Asset)
MATCH (e)-[:HAS_MARKET_OBSERVATION]->(observation:MarketObservation)<-
      [:HAS_MARKET_OBSERVATION]-(asset)
OPTIONAL MATCH (source_article:Article {article_id: impact.source_article_id})-
               [report:REPORTS]->(e)
WHERE report.source_event_id = impact.source_event_id
   OR (
       impact.source_event_id IS NULL
       AND coalesce(report.is_representative, true) = true
   )
RETURN c.company_id AS company_id,
       c.name AS company,
       e.event_id AS event_id,
       e.event_date AS event_date,
       e.event_type AS event_type,
       e.title AS event_title,
       impact.evidence_sentence AS evidence_sentence,
       source_article.article_id AS source_article_id,
       source_article.web_url AS source_url,
       report IS NOT NULL AS source_report_matched,
       asset.symbol AS symbol,
       observation.market_observation_id AS market_observation_id,
       observation.window_trading_days AS window_days,
       observation.baseline_date AS baseline_date,
       observation.window_end_date AS window_end_date,
       observation.cumulative_return AS cumulative_return,
       coalesce(observation.causal_claim, false) AS causal_claim
ORDER BY event_date DESC, event_id, window_days
"""


TASK_5_QUERY = """
MATCH (c1:Company)<-[:POTENTIALLY_AFFECTS]-(shared:Event)-
      [:POTENTIALLY_AFFECTS]->(c2:Company)
WHERE c1.company_id < c2.company_id
WITH c1, c2, collect(DISTINCT shared) AS shared_events
WHERE size(shared_events) >= $support_threshold
UNWIND shared_events AS shared_event
MATCH (shared_event)-[impact1:POTENTIALLY_AFFECTS]->(c1)
MATCH (shared_event)-[impact2:POTENTIALLY_AFFECTS]->(c2)
WITH c1, c2, shared_events,
     collect(DISTINCT shared_event.event_id) AS shared_event_ids,
     sum(CASE
         WHEN trim(coalesce(impact1.evidence_sentence, '')) <> ''
          AND trim(coalesce(impact2.evidence_sentence, '')) <> ''
         THEN 1 ELSE 0
     END) AS evidenced_shared_event_count,
     sum(CASE
         WHEN EXISTS {
             MATCH (a1:Article)-[r1:REPORTS]->(shared_event)
             WHERE a1.article_id = impact1.source_article_id
               AND (
                   r1.source_event_id = impact1.source_event_id
                   OR (
                       impact1.source_event_id IS NULL
                       AND coalesce(r1.is_representative, true) = true
                   )
               )
         }
          AND EXISTS {
             MATCH (a2:Article)-[r2:REPORTS]->(shared_event)
             WHERE a2.article_id = impact2.source_article_id
               AND (
                   r2.source_event_id = impact2.source_event_id
                   OR (
                       impact2.source_event_id IS NULL
                       AND coalesce(r2.is_representative, true) = true
                   )
               )
         }
         THEN 1 ELSE 0
     END) AS sourced_shared_event_count
OPTIONAL MATCH (event1:Event)-[:POTENTIALLY_AFFECTS]->(c1)
WITH c1, c2, shared_events, shared_event_ids,
     evidenced_shared_event_count, sourced_shared_event_count,
     count(DISTINCT event1) AS company1_event_count
OPTIONAL MATCH (event2:Event)-[:POTENTIALLY_AFFECTS]->(c2)
WITH c1, c2, shared_events, shared_event_ids,
     evidenced_shared_event_count, sourced_shared_event_count,
     company1_event_count, count(DISTINCT event2) AS company2_event_count
RETURN c1.company_id AS company1_id,
       c1.name AS company1,
       c2.company_id AS company2_id,
       c2.name AS company2,
       size(shared_events) AS shared_event_count,
       company1_event_count,
       company2_event_count,
       toFloat(size(shared_events)) /
           (company1_event_count + company2_event_count - size(shared_events))
           AS jaccard_similarity,
       evidenced_shared_event_count,
       sourced_shared_event_count,
       shared_event_ids
ORDER BY shared_event_count DESC, company1_id, company2_id
"""


@dataclass(frozen=True)
class EvaluationConfig:
    output_directory: Path
    warmup_runs: int
    measured_runs: int
    target_company_count: int
    study_start_date: str
    study_end_date: str
    tsmc_company_id: str
    alphabet_company_id: str
    regulatory_event_type: str
    minimum_nlp_probability: float
    shared_event_support_threshold: int
    expected_market_windows: tuple[int, ...]


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    title_en: str
    title_cn: str
    scope: str
    query: str
    parameters: Mapping[str, Any]
    output_filename: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate five read-only financial-KG analyst use cases."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config/config.yaml")
    )
    parser.add_argument("--output-directory", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--print-output-directory",
        action="store_true",
        help="Print the resolved output directory without connecting to Neo4j.",
    )
    mode.add_argument(
        "--finalize-manifest",
        action="store_true",
        help=(
            "Recompute output_sha256 in an existing manifest without connecting "
            "to Neo4j."
        ),
    )
    return parser.parse_args(argv)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a YAML mapping.")
    return value


def evaluation_config(
    config: Mapping[str, Any],
    project_root: Path,
    output_override: Path | None = None,
) -> EvaluationConfig:
    section = _mapping(
        config.get("analyst_use_case_evaluation"),
        "analyst_use_case_evaluation",
    )
    study = _mapping(config.get("study"), "study")
    selection = _mapping(config.get("company_selection"), "company_selection")
    tasks = _mapping(section.get("tasks"), "analyst_use_case_evaluation.tasks")
    tsmc = _mapping(tasks.get("tsmc_evidence"), "tasks.tsmc_evidence")
    regulatory = _mapping(tasks.get("regulatory_alerts"), "tasks.regulatory_alerts")
    alphabet = _mapping(
        tasks.get("alphabet_market_context"), "tasks.alphabet_market_context"
    )
    shared = _mapping(tasks.get("shared_event_pairs"), "tasks.shared_event_pairs")

    output_value: Path | str = output_override or section.get(
        "output_directory", "outputs/analyst_use_case_evaluation"
    )
    start_date = str(study.get("news_start_date", "2025-07-01"))
    end_date = str(study.get("news_end_date", "2026-06-30"))
    result = EvaluationConfig(
        output_directory=resolve_path(project_root, output_value),
        warmup_runs=int(section.get("warmup_runs", 2)),
        measured_runs=int(section.get("measured_runs", 10)),
        target_company_count=int(selection.get("target_company_count", 25)),
        study_start_date=start_date,
        study_end_date=end_date,
        tsmc_company_id=str(tsmc.get("company_id", "C007")),
        alphabet_company_id=str(alphabet.get("company_id", "C003")),
        regulatory_event_type=str(
            regulatory.get("event_type", "regulatory_event")
        ),
        minimum_nlp_probability=float(
            regulatory.get("minimum_nlp_probability", 0.8)
        ),
        shared_event_support_threshold=int(
            shared.get("support_threshold", 2)
        ),
        expected_market_windows=tuple(
            int(value)
            for value in section.get("expected_market_windows", [1, 3, 7])
        ),
    )
    validate_evaluation_config(result)
    return result


def validate_evaluation_config(config: EvaluationConfig) -> None:
    if config.warmup_runs < 0:
        raise ValueError("warmup_runs cannot be negative.")
    if config.measured_runs <= 0:
        raise ValueError("measured_runs must be positive.")
    if config.target_company_count <= 0:
        raise ValueError("target_company_count must be positive.")
    start = validate_iso_date(config.study_start_date, "study.news_start_date")
    end = validate_iso_date(config.study_end_date, "study.news_end_date")
    if start and end and start > end:
        raise ValueError("study.news_start_date cannot follow study.news_end_date.")
    if not 0.0 <= config.minimum_nlp_probability <= 1.0:
        raise ValueError("minimum_nlp_probability must be between 0 and 1.")
    if config.shared_event_support_threshold <= 0:
        raise ValueError("support_threshold must be positive.")
    if not config.expected_market_windows:
        raise ValueError("expected_market_windows cannot be empty.")
    if any(value <= 0 for value in config.expected_market_windows):
        raise ValueError("expected_market_windows must contain positive integers.")
    if len(set(config.expected_market_windows)) != len(
        config.expected_market_windows
    ):
        raise ValueError("expected_market_windows cannot contain duplicates.")


def task_definitions(config: EvaluationConfig) -> list[TaskDefinition]:
    tasks = [
        TaskDefinition(
            "T1",
            "Portfolio company screening",
            "投资组合公司筛查",
            "All selected companies and their canonical-event coverage",
            TASK_1_QUERY,
            {},
            "task_1_company_screening.csv",
        ),
        TaskDefinition(
            "T2",
            "TSMC evidence dossier",
            "台积电证据档案",
            f"All canonical event links for {config.tsmc_company_id}",
            TASK_2_QUERY,
            {"company_id": config.tsmc_company_id},
            "task_2_tsmc_evidence.csv",
        ),
        TaskDefinition(
            "T3",
            "High-confidence regulatory alerts",
            "高置信度监管事件预警",
            (
                f"{config.regulatory_event_type}, {config.study_start_date} to "
                f"{config.study_end_date}, NLP probability >= "
                f"{config.minimum_nlp_probability:g}"
            ),
            TASK_3_QUERY,
            {
                "event_type": config.regulatory_event_type,
                "start_date": config.study_start_date,
                "end_date": config.study_end_date,
                "minimum_nlp_probability": config.minimum_nlp_probability,
            },
            "task_3_regulatory_alerts.csv",
        ),
        TaskDefinition(
            "T4",
            "Alphabet event-to-market trace",
            "Alphabet 事件至市场背景追溯",
            (
                f"All {config.alphabet_company_id} canonical-event links and "
                f"{list(config.expected_market_windows)}-trading-day windows"
            ),
            TASK_4_QUERY,
            {"company_id": config.alphabet_company_id},
            "task_4_alphabet_market_context.csv",
        ),
        TaskDefinition(
            "T5",
            "Shared-event peer discovery",
            "共享事件同业发现",
            (
                "Company pairs sharing at least "
                f"{config.shared_event_support_threshold} canonical events"
            ),
            TASK_5_QUERY,
            {"support_threshold": config.shared_event_support_threshold},
            "task_5_shared_event_pairs.csv",
        ),
    ]
    for task in tasks:
        ensure_read_only_query(task.query)
    return tasks


_WRITE_TOKEN = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b",
    flags=re.IGNORECASE,
)
_WRITE_PROCEDURE = re.compile(
    r"\b(?:write|mutate)\b|gds\.graph\.drop", flags=re.IGNORECASE
)


def ensure_read_only_query(query: str) -> None:
    if _WRITE_TOKEN.search(query) or _WRITE_PROCEDURE.search(query):
        raise ValueError("Analyst use-case queries must be strictly read-only.")


def json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        return iso_format()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set)):
        converted = [json_value(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    if hasattr(value, "item"):
        try:
            return json_value(value.item())
        except (TypeError, ValueError):
            pass
    return value


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in normalized.columns:
        normalized[column] = normalized[column].map(json_value)
    if not normalized.empty:
        normalized = normalized.sort_values(
            list(normalized.columns),
            kind="mergesort",
            key=lambda series: series.map(
                lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
            ),
        ).reset_index(drop=True)
    return normalized


def frame_sha256(frame: pd.DataFrame) -> str:
    normalized = normalize_frame(frame)
    records = [
        {str(key): json_value(value) for key, value in row.items()}
        for row in normalized.to_dict(orient="records")
    ]
    payload = json.dumps(
        {"columns": list(normalized.columns), "records": records},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def percentile(values: Sequence[float], percentage: float) -> float:
    if not values:
        return math.nan
    if not 0.0 <= percentage <= 100.0:
        raise ValueError("percentage must be between 0 and 100.")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentage / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def safe_pct(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return round(float(numerator) / float(denominator) * 100.0, 6)


def _duration_ms(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    total_seconds = getattr(value, "total_seconds", None)
    if callable(total_seconds):
        return float(total_seconds()) * 1000.0
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    export = frame.copy()
    for column in export.columns:
        export[column] = export[column].map(
            lambda value: (
                json.dumps(json_value(value), ensure_ascii=False, sort_keys=True)
                if isinstance(value, (list, tuple, set, Mapping))
                else json_value(value)
            )
        )
    export.to_csv(path, index=False, encoding="utf-8-sig")


def collect_output_hashes(output_directory: Path) -> dict[str, str]:
    allowed = {".csv", ".md", ".svg", ".png"}
    files = [
        path
        for path in output_directory.rglob("*")
        if path.is_file()
        and path.name != MANIFEST_FILENAME
        and path.suffix.lower() in allowed
    ]
    return {
        path.relative_to(output_directory).as_posix(): file_sha256(path)
        for path in sorted(files)
    }


def finalize_manifest(output_directory: Path) -> Path:
    manifest_path = output_directory / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("The existing analyst use-case manifest must be an object.")
    manifest["output_sha256"] = collect_output_hashes(output_directory)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest_path


def execute_timed_read(
    driver: Any,
    database: str,
    task: TaskDefinition,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    ensure_read_only_query(task.query)
    started = time.perf_counter_ns()
    records, summary, keys = driver.execute_query(
        task.query,
        parameters_=dict(task.parameters),
        database_=database,
        routing_=RoutingControl.READ,
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    frame = normalize_frame(records_to_frame(records, list(keys)))
    return frame, {
        "client_elapsed_ms": elapsed_ms,
        "server_available_ms": _duration_ms(
            getattr(summary, "result_available_after", None)
        ),
        "server_consumed_ms": _duration_ms(
            getattr(summary, "result_consumed_after", None)
        ),
        "result_rows": int(len(frame)),
        "result_sha256": frame_sha256(frame),
    }


def graph_state(driver: Any, database: str) -> dict[str, Any]:
    node_records, _, _ = driver.execute_query(
        NODE_COUNT_QUERY,
        database_=database,
        routing_=RoutingControl.READ,
    )
    relationship_records, _, _ = driver.execute_query(
        RELATIONSHIP_COUNT_QUERY,
        database_=database,
        routing_=RoutingControl.READ,
    )
    nodes = {
        str(record["label"]): int(record["count"])
        for record in node_records
    }
    relationships = {
        str(record["relationship_type"]): int(record["count"])
        for record in relationship_records
    }
    state = {
        "nodes_by_label": dict(sorted(nodes.items())),
        "relationships_by_type": dict(sorted(relationships.items())),
        "total_nodes": int(sum(nodes.values())),
        "total_relationships": int(sum(relationships.values())),
    }
    state["sha256"] = text_sha256(
        json.dumps(state, ensure_ascii=False, sort_keys=True)
    )
    return state


def database_environment(driver: Any, database: str) -> dict[str, Any]:
    records, _, _ = driver.execute_query(
        """
        CALL dbms.components()
        YIELD name, versions, edition
        RETURN name, versions[0] AS version, edition
        ORDER BY name
        """,
        database_=database,
        routing_=RoutingControl.READ,
    )
    return {
        "components": [
            {
                "name": str(record["name"]),
                "version": str(record["version"]),
                "edition": str(record["edition"]),
            }
            for record in records
        ]
    }


def performance_table(
    tasks: Sequence[TaskDefinition],
    timings: pd.DataFrame,
    warmup_runs: int,
    measured_runs: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        selected = timings.loc[
            (timings["task_id"] == task.task_id) & (~timings["is_warmup"])
        ]
        client = selected["client_elapsed_ms"].astype(float).tolist()
        available = pd.to_numeric(
            selected["server_available_ms"], errors="coerce"
        ).dropna()
        consumed = pd.to_numeric(
            selected["server_consumed_ms"], errors="coerce"
        ).dropna()
        hashes = selected["result_sha256"].astype(str)
        row_counts = selected["result_rows"].astype(int)
        rows.append(
            {
                "task_id": task.task_id,
                "warmup_runs": warmup_runs,
                "measured_runs": measured_runs,
                "result_rows": int(row_counts.iloc[0]) if len(row_counts) else 0,
                "median_client_ms": statistics.median(client),
                "p95_client_ms": percentile(client, 95),
                "min_client_ms": min(client),
                "max_client_ms": max(client),
                "median_server_available_ms": (
                    statistics.median(available.tolist())
                    if len(available)
                    else None
                ),
                "median_server_consumed_ms": (
                    statistics.median(consumed.tolist())
                    if len(consumed)
                    else None
                ),
                "result_sha256": hashes.iloc[0] if len(hashes) else "",
                "result_hash_stable": bool(hashes.nunique() == 1),
                "row_count_stable": bool(row_counts.nunique() == 1),
                "interpretation": (
                    "Descriptive localhost warm-cache timing; it excludes Neo4j "
                    "startup and user-interface rendering."
                ),
            }
        )
    return pd.DataFrame(rows)


def _nonblank(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().ne("")


def _boolean(series: pd.Series) -> pd.Series:
    return series.map(
        lambda value: value is True or str(value).strip().lower() == "true"
    )


def _check(
    task_id: str,
    check_id: str,
    metric_class: str,
    numerator: int,
    denominator: int,
    definition: str,
    limitation: str,
    *,
    informational: bool = False,
) -> dict[str, Any]:
    value = safe_pct(numerator, denominator)
    if informational:
        status = "INFO"
    elif denominator <= 0:
        status = "FAIL"
    else:
        status = "PASS" if numerator == denominator else "FAIL"
    return {
        "task_id": task_id,
        "check_id": check_id,
        "metric_class": metric_class,
        "numerator": int(numerator),
        "denominator": int(denominator),
        "value_pct": value,
        "status": status,
        "definition": definition,
        "limitation": limitation,
    }


def task_quality_checks(
    config: EvaluationConfig,
    results: Mapping[str, pd.DataFrame],
    graph_unchanged: bool,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    task1 = results["T1"]
    company_count = int(task1["company_id"].nunique()) if not task1.empty else 0
    event_links = int(pd.to_numeric(task1["event_count"]).sum()) if not task1.empty else 0
    evidenced_links = (
        int(pd.to_numeric(task1["evidenced_event_count"]).sum())
        if not task1.empty
        else 0
    )
    covered_companies = (
        int((pd.to_numeric(task1["event_count"]) > 0).sum())
        if not task1.empty
        else 0
    )
    rows.extend(
        [
            _check(
                "T1",
                "selected_company_retrieval",
                "internal_retrieval_completeness",
                company_count,
                config.target_company_count,
                "Unique returned companies divided by the configured portfolio size.",
                "This is not recall against the global company universe.",
            ),
            _check(
                "T1",
                "company_event_reach",
                "scope_coverage",
                covered_companies,
                company_count,
                "Returned companies with at least one qualified canonical event.",
                "A zero-event company would describe corpus coverage, not query error.",
                informational=True,
            ),
            _check(
                "T1",
                "relationship_evidence_presence",
                "evidence_completeness",
                evidenced_links,
                event_links,
                "Event-company links with non-empty relationship evidence.",
                "Evidence presence does not prove the underlying claim is true.",
            ),
        ]
    )

    task2 = results["T2"]
    task2_links = len(task2)
    task2_evidence = int(_nonblank(task2["evidence_sentence"]).sum())
    task2_provenance = int(
        (
            (pd.to_numeric(task2["matching_source_count"]) > 0)
            & (pd.to_numeric(task2["source_url_count"]) > 0)
        ).sum()
    )
    rows.extend(
        [
            _check(
                "T2",
                "scoped_event_links_returned",
                "internal_retrieval_completeness",
                task2_links,
                task2_links,
                "All untruncated event-company rows in the fixed company scope.",
                "The denominator is internal to the frozen graph, not external news recall.",
            ),
            _check(
                "T2",
                "relationship_evidence_presence",
                "evidence_completeness",
                task2_evidence,
                task2_links,
                "Rows with a non-empty event-company evidence sentence.",
                "Presence does not measure semantic correctness.",
            ),
            _check(
                "T2",
                "matching_source_provenance",
                "provenance_completeness",
                task2_provenance,
                task2_links,
                "Rows with a matching REPORTS source mapping and non-empty URL.",
                "A source path supports auditability, not truth verification.",
            ),
        ]
    )

    task3 = results["T3"]
    task3_links = len(task3)
    task3_evidence = int(_nonblank(task3["evidence_sentence"]).sum())
    task3_provenance = int(
        (
            _boolean(task3["source_report_matched"])
            & _nonblank(task3["source_url"])
        ).sum()
    )
    reached_companies = int(task3["company_id"].nunique()) if not task3.empty else 0
    rows.extend(
        [
            _check(
                "T3",
                "scoped_regulatory_links_returned",
                "internal_retrieval_completeness",
                task3_links,
                task3_links,
                "All untruncated links satisfying the fixed date/type/NLP filter.",
                "The NLP threshold is an operational filter, not a precision estimate.",
            ),
            _check(
                "T3",
                "portfolio_reach",
                "scope_coverage",
                reached_companies,
                config.target_company_count,
                "Portfolio companies represented in the filtered regulatory result.",
                "Companies outside this result are not necessarily missed events.",
                informational=True,
            ),
            _check(
                "T3",
                "relationship_evidence_presence",
                "evidence_completeness",
                task3_evidence,
                task3_links,
                "Filtered links with non-empty relationship evidence.",
                "Presence is not human-labelled relevance or correctness.",
            ),
            _check(
                "T3",
                "matching_source_provenance",
                "provenance_completeness",
                task3_provenance,
                task3_links,
                "Filtered links with matching REPORTS source and URL.",
                "Internal provenance completeness is not external recall.",
            ),
        ]
    )

    task4 = results["T4"]
    event_count = int(task4["event_id"].nunique()) if not task4.empty else 0
    expected_windows = set(config.expected_market_windows)
    if task4.empty:
        complete_events: set[str] = set()
        evidence_events: set[str] = set()
        provenance_events: set[str] = set()
    else:
        windows_by_event = task4.groupby("event_id")["window_days"].agg(
            lambda values: {int(value) for value in values}
        )
        complete_events = {
            str(event_id)
            for event_id, windows in windows_by_event.items()
            if windows == expected_windows
        }
        evidence_events = set(
            task4.loc[_nonblank(task4["evidence_sentence"]), "event_id"].astype(str)
        )
        provenance_events = set(
            task4.loc[
                _boolean(task4["source_report_matched"])
                & _nonblank(task4["source_url"]),
                "event_id",
            ].astype(str)
        )
    noncausal_rows = (
        int((~_boolean(task4["causal_claim"])).sum()) if not task4.empty else 0
    )
    rows.extend(
        [
            _check(
                "T4",
                "scoped_market_rows_returned",
                "internal_retrieval_completeness",
                (
                    int(
                        task4[["event_id", "window_days"]]
                        .drop_duplicates()
                        .shape[0]
                    )
                    if not task4.empty
                    else 0
                ),
                event_count * len(expected_windows),
                "Distinct event-window rows divided by the configured expected rows.",
                "This is internal coverage and does not measure market-data accuracy.",
            ),
            _check(
                "T4",
                "expected_market_windows",
                "market_window_completeness",
                len(complete_events),
                event_count,
                "Event links with exactly the configured 1/3/7-day windows.",
                "Window availability does not identify causal price effects.",
            ),
            _check(
                "T4",
                "relationship_evidence_presence",
                "evidence_completeness",
                len(evidence_events),
                event_count,
                "Unique market-linked events with non-empty relationship evidence.",
                "Presence does not measure factual correctness.",
            ),
            _check(
                "T4",
                "matching_source_provenance",
                "provenance_completeness",
                len(provenance_events),
                event_count,
                "Unique market-linked events with matching REPORTS source and URL.",
                "A source path supports audit but not causal inference.",
            ),
            _check(
                "T4",
                "noncausal_market_flags",
                "interpretation_integrity",
                noncausal_rows,
                len(task4),
                "Market rows explicitly marked as non-causal.",
                "Returns remain descriptive post-publication context.",
            ),
        ]
    )

    task5 = results["T5"]
    shared_instances = (
        int(pd.to_numeric(task5["shared_event_count"]).sum())
        if not task5.empty
        else 0
    )
    evidenced_instances = (
        int(pd.to_numeric(task5["evidenced_shared_event_count"]).sum())
        if not task5.empty
        else 0
    )
    sourced_instances = (
        int(pd.to_numeric(task5["sourced_shared_event_count"]).sum())
        if not task5.empty
        else 0
    )
    supported_pairs = (
        int(
            (
                pd.to_numeric(task5["shared_event_count"])
                >= config.shared_event_support_threshold
            ).sum()
        )
        if not task5.empty
        else 0
    )
    rows.extend(
        [
            _check(
                "T5",
                "support_threshold_applied",
                "internal_retrieval_completeness",
                supported_pairs,
                len(task5),
                "Returned pairs meeting the configured shared-event support threshold.",
                "The threshold is interpretive support, not statistical significance.",
            ),
            _check(
                "T5",
                "two_sided_relationship_evidence",
                "evidence_completeness",
                evidenced_instances,
                shared_instances,
                "Shared pair-event instances with evidence for both company links.",
                "Co-event evidence is not proof of a business or causal relationship.",
            ),
            _check(
                "T5",
                "two_sided_source_provenance",
                "provenance_completeness",
                sourced_instances,
                shared_instances,
                "Shared pair-event instances with matching REPORTS paths on both sides.",
                "Jaccard and shared events are structural, not systemic-risk scores.",
            ),
        ]
    )

    rows.append(
        _check(
            "GLOBAL",
            "graph_state_unchanged",
            "read_only_integrity",
            int(graph_unchanged),
            1,
            "Node/relationship counts and their graph-state hash match before and after.",
            "This detects persisted graph-count changes during the evaluation run.",
        )
    )
    return pd.DataFrame(rows)


def summary_table(
    tasks: Sequence[TaskDefinition],
    results: Mapping[str, pd.DataFrame],
    performance: pd.DataFrame,
    checks: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        frame = results[task.task_id]
        task_checks = checks.loc[checks["task_id"] == task.task_id]
        lookup = task_checks.set_index("metric_class")

        def metric(metric_class: str, field: str) -> Any:
            if metric_class not in lookup.index:
                return None
            selected = lookup.loc[metric_class]
            if isinstance(selected, pd.DataFrame):
                selected = selected.iloc[-1]
            return selected[field]

        perf = performance.loc[
            performance["task_id"] == task.task_id
        ].iloc[0]
        coverage_num = metric("internal_retrieval_completeness", "numerator")
        coverage_den = metric("internal_retrieval_completeness", "denominator")
        evidence_num = metric("evidence_completeness", "numerator")
        evidence_den = metric("evidence_completeness", "denominator")
        provenance_num = metric("provenance_completeness", "numerator")
        provenance_den = metric("provenance_completeness", "denominator")
        failed = int((task_checks["status"] == "FAIL").sum())
        rows.append(
            {
                "task_id": task.task_id,
                "title_en": task.title_en,
                "title_cn": task.title_cn,
                "scope": task.scope,
                "parameters_json": json.dumps(
                    dict(task.parameters), ensure_ascii=False, sort_keys=True
                ),
                "workflow_query_steps": 1,
                "manual_join_steps": 0,
                "manual_calculation_steps": 0,
                "source_join_automated": True,
                "aggregation_automated": True,
                "export_automated": True,
                "result_rows": int(len(frame)),
                "primary_units": int(coverage_den or len(frame)),
                "coverage_numerator": coverage_num,
                "coverage_denominator": coverage_den,
                "coverage_pct": safe_pct(coverage_num or 0, coverage_den or 0),
                "evidence_numerator": evidence_num,
                "evidence_denominator": evidence_den,
                "evidence_completeness_pct": safe_pct(
                    evidence_num or 0, evidence_den or 0
                ),
                "provenance_numerator": provenance_num,
                "provenance_denominator": provenance_den,
                "provenance_completeness_pct": safe_pct(
                    provenance_num or 0, provenance_den or 0
                ),
                "median_client_ms": perf["median_client_ms"],
                "p95_client_ms": perf["p95_client_ms"],
                "status": "PASS" if failed == 0 else "FAIL",
            }
        )
    return pd.DataFrame(rows)


def _svg_document(width: int, height: int, content: Iterable[str]) -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                f'height="{height}" viewBox="0 0 {width} {height}">'
            ),
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            *content,
            "</svg>",
            "",
        ]
    )


def latency_svg(summary: pd.DataFrame) -> str:
    width, height = 960, 500
    left, top, chart_width, chart_height = 105, 80, 800, 330
    maximum = max(float(summary["p95_client_ms"].max()), 1.0)
    lines = [
        '<text x="40" y="38" font-family="Arial" font-size="24" '
        'font-weight="700" fill="#172554">Analyst use-case query latency</text>',
        '<text x="40" y="61" font-family="Arial" font-size="13" '
        'fill="#475569">Localhost, warm cache; milliseconds, lower is faster</text>',
    ]
    group_width = chart_width / max(len(summary), 1)
    for index, row in summary.reset_index(drop=True).iterrows():
        centre = left + group_width * (index + 0.5)
        for offset, field, colour in (
            (-18, "median_client_ms", "#0f766e"),
            (18, "p95_client_ms", "#f59e0b"),
        ):
            value = float(row[field])
            bar_height = chart_height * value / maximum
            x = centre + offset - 14
            y = top + chart_height - bar_height
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="28" '
                f'height="{bar_height:.1f}" rx="3" fill="{colour}"/>'
            )
            lines.append(
                f'<text x="{centre + offset:.1f}" y="{max(y - 5, 74):.1f}" '
                'text-anchor="middle" font-family="Arial" font-size="11" '
                f'fill="#334155">{value:.1f}</text>'
            )
        lines.append(
            f'<text x="{centre:.1f}" y="{top + chart_height + 25}" '
            'text-anchor="middle" font-family="Arial" font-size="13" '
            f'font-weight="700" fill="#172554">{escape(str(row.task_id))}</text>'
        )
    lines.extend(
        [
            '<rect x="680" y="22" width="14" height="14" fill="#0f766e"/>',
            '<text x="701" y="34" font-family="Arial" font-size="12" '
            'fill="#334155">Median</text>',
            '<rect x="770" y="22" width="14" height="14" fill="#f59e0b"/>',
            '<text x="791" y="34" font-family="Arial" font-size="12" '
            'fill="#334155">P95</text>',
            f'<line x1="{left}" y1="{top + chart_height}" '
            f'x2="{left + chart_width}" y2="{top + chart_height}" '
            'stroke="#94a3b8"/>',
        ]
    )
    return _svg_document(width, height, lines)


def completeness_svg(summary: pd.DataFrame) -> str:
    width, height = 960, 520
    left, top, chart_width, chart_height = 105, 90, 800, 330
    metrics = (
        ("coverage_pct", "Coverage", "#2563eb"),
        ("evidence_completeness_pct", "Evidence", "#0f766e"),
        ("provenance_completeness_pct", "Provenance", "#f59e0b"),
    )
    lines = [
        '<text x="40" y="38" font-family="Arial" font-size="24" '
        'font-weight="700" fill="#172554">Internal completeness by use case</text>',
        '<text x="40" y="62" font-family="Arial" font-size="13" '
        'fill="#475569">Internal graph checks; not external precision or recall</text>',
    ]
    group_width = chart_width / max(len(summary), 1)
    for index, row in summary.reset_index(drop=True).iterrows():
        centre = left + group_width * (index + 0.5)
        for metric_index, (field, _, colour) in enumerate(metrics):
            raw = row[field]
            if pd.isna(raw):
                continue
            value = float(raw)
            bar_height = chart_height * value / 100.0
            offset = (metric_index - 1) * 31
            x = centre + offset - 12
            y = top + chart_height - bar_height
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="24" '
                f'height="{bar_height:.1f}" rx="3" fill="{colour}"/>'
            )
        lines.append(
            f'<text x="{centre:.1f}" y="{top + chart_height + 25}" '
            'text-anchor="middle" font-family="Arial" font-size="13" '
            f'font-weight="700" fill="#172554">{escape(str(row.task_id))}</text>'
        )
    for index, (_, label, colour) in enumerate(metrics):
        x = 585 + index * 115
        lines.append(f'<rect x="{x}" y="24" width="13" height="13" fill="{colour}"/>')
        lines.append(
            f'<text x="{x + 19}" y="35" font-family="Arial" font-size="12" '
            f'fill="#334155">{escape(label)}</text>'
        )
    lines.extend(
        [
            f'<line x1="{left}" y1="{top + chart_height}" '
            f'x2="{left + chart_width}" y2="{top + chart_height}" '
            'stroke="#94a3b8"/>',
            f'<line x1="{left}" y1="{top}" x2="{left + chart_width}" '
            f'y2="{top}" stroke="#cbd5e1" stroke-dasharray="4 4"/>',
            f'<text x="{left - 12}" y="{top + 4}" text-anchor="end" '
            'font-family="Arial" font-size="11" fill="#475569">100%</text>',
        ]
    )
    return _svg_document(width, height, lines)


def _pct_text(value: Any) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{float(value):.1f}%"


def evaluation_report(
    language: str,
    config: EvaluationConfig,
    summary: pd.DataFrame,
    checks: pd.DataFrame,
    graph_unchanged: bool,
) -> str:
    if language == "zh":
        lines = [
            "# 分析师应用场景自动评价",
            "",
            "本评价对五个固定、参数化且严格只读的知识图谱任务进行复现实验。",
            f"每项任务先预热 {config.warmup_runs} 次，再测量 {config.measured_runs} 次；"
            "客户端时间覆盖查询提交至结果完全消费。",
            "",
            "| 任务 | 场景 | 返回行 | 中位耗时（ms） | P95（ms） | 内部覆盖 | 证据完整性 | 来源完整性 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in summary.itertuples():
            lines.append(
                f"| {row.task_id} | {row.title_cn} | {int(row.result_rows)} | "
                f"{float(row.median_client_ms):.2f} | {float(row.p95_client_ms):.2f} | "
                f"{_pct_text(row.coverage_pct)} | "
                f"{_pct_text(row.evidence_completeness_pct)} | "
                f"{_pct_text(row.provenance_completeness_pct)} |"
            )
        passed = int((checks["status"] == "PASS").sum())
        failed = int((checks["status"] == "FAIL").sum())
        lines.extend(
            [
                "",
                "## 自动化与可复现性",
                "",
                "- 每项任务只执行一条参数化数据库查询。",
                "- 来源连接、聚合、指标计算和文件导出均由脚本自动完成；人工连接步骤和人工计算步骤均为 0。",
                "- 所有测量轮次均检查结果行数和内容哈希是否稳定。",
                f"- 自动质量检查：{passed} 项通过，{failed} 项失败。",
                f"- 运行前后图状态一致：{'是' if graph_unchanged else '否'}。",
                "",
                "## 解释边界",
                "",
                "- 内部检索完整性和字段完整性不等于针对外部新闻总体的 precision、recall 或 F1。",
                "- 证据句和来源路径存在只说明结果可审计，不证明新闻陈述或事件关系必然正确。",
                "- NLP 概率阈值是操作筛选条件，不是经人工金标准验证的准确率。",
                "- 响应时间来自 localhost 和预热缓存环境，不应外推为生产系统性能。",
                "- 本研究没有设置人工工作流基线，因此不声称节省了具体工时或提高了人工准确率。",
                "- 市场窗口收益仅是新闻发布后的描述性背景，不建立事件与收益之间的因果关系，也不构成投资建议。",
                "- 共享事件和 Jaccard 相似度表示样本内结构邻近性，不等于业务联系、系统重要性或风险传导。",
            ]
        )
        return "\n".join(lines) + "\n"

    lines = [
        "# Automated Analyst Use-Case Evaluation",
        "",
        "This evaluation runs five fixed, parameterised and strictly read-only "
        "knowledge-graph tasks.",
        f"Each task uses {config.warmup_runs} warm-up runs followed by "
        f"{config.measured_runs} measured runs; client time covers query submission "
        "through full result consumption.",
        "",
        "| Task | Use case | Rows | Median (ms) | P95 (ms) | Internal coverage | Evidence | Provenance |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples():
        lines.append(
            f"| {row.task_id} | {row.title_en} | {int(row.result_rows)} | "
            f"{float(row.median_client_ms):.2f} | {float(row.p95_client_ms):.2f} | "
            f"{_pct_text(row.coverage_pct)} | "
            f"{_pct_text(row.evidence_completeness_pct)} | "
            f"{_pct_text(row.provenance_completeness_pct)} |"
        )
    passed = int((checks["status"] == "PASS").sum())
    failed = int((checks["status"] == "FAIL").sum())
    lines.extend(
        [
            "",
            "## Automation and reproducibility",
            "",
            "- Each use case executes one parameterised database query.",
            "- Source joins, aggregation, metric calculation and export are automated; manual join and calculation steps are zero.",
            "- Row counts and result-content hashes are checked across every measured run.",
            f"- Automated quality checks: {passed} passed and {failed} failed.",
            f"- Graph state unchanged before and after execution: {'yes' if graph_unchanged else 'no'}.",
            "",
            "## Interpretation boundaries",
            "",
            "- Internal retrieval and field completeness are not external precision, recall or F1 against the wider news universe.",
            "- Evidence and provenance paths support auditability; they do not prove that a news statement or relationship is true.",
            "- The NLP probability threshold is an operational filter, not human-gold-standard accuracy.",
            "- Latencies are localhost, warm-cache measurements and cannot be extrapolated to production performance.",
            "- No human-workflow baseline was collected, so the study claims no quantified labour saving or improvement in human accuracy.",
            "- Market-window returns are descriptive post-publication context, not causal effects or investment advice.",
            "- Shared events and Jaccard similarity indicate within-sample structural proximity, not business ties, systemic importance or risk transmission.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_manifest(
    *,
    config_path: Path,
    script_path: Path,
    evaluation: EvaluationConfig,
    settings: Any,
    tasks: Sequence[TaskDefinition],
    performance: pd.DataFrame,
    summary: pd.DataFrame,
    checks: pd.DataFrame,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    environment: Mapping[str, Any],
    output_directory: Path,
) -> dict[str, Any]:
    graph_unchanged = before == after
    non_info = checks.loc[checks["status"] != "INFO"]
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": {
            "uri": settings.uri,
            "database": settings.database,
            **dict(environment),
        },
        "timing_protocol": {
            "warmup_runs": evaluation.warmup_runs,
            "measured_runs": evaluation.measured_runs,
            "clock": "time.perf_counter_ns",
            "scope": "localhost query submission through eager result consumption",
            "cache_state": "warm after configured warm-up runs",
        },
        "tasks": [
            {
                "task_id": task.task_id,
                "title_en": task.title_en,
                "title_cn": task.title_cn,
                "scope": task.scope,
                "parameters": dict(task.parameters),
                "query_sha256": text_sha256(task.query.strip()),
                "output": f"tables/{task.output_filename}",
            }
            for task in tasks
        ],
        "graph_state": {
            "before": dict(before),
            "after": dict(after),
            "unchanged": graph_unchanged,
        },
        "read_only_contract": {
            "persisted_kg_writeback": False,
            "routing_control": "READ",
            "cypher_write_operations_used": False,
            "database_counts_unchanged": graph_unchanged,
            "database_fingerprint_unchanged": graph_unchanged,
        },
        "summary": {
            "use_case_count": int(len(tasks)),
            "all_tasks_succeeded": bool((summary["status"] == "PASS").all()),
            "all_result_hashes_stable": bool(
                performance["result_hash_stable"].all()
            ),
            "all_row_counts_stable": bool(performance["row_count_stable"].all()),
            "graph_state_unchanged": graph_unchanged,
            "quality_checks_passed": int((non_info["status"] == "PASS").sum()),
            "quality_checks_total": int(len(non_info)),
            "company_scope_count": evaluation.target_company_count,
        },
        "source_sha256": {
            config_path.name: file_sha256(config_path),
            script_path.name: file_sha256(script_path),
        },
        "interpretation_boundaries": {
            "internal_completeness_is_external_precision_or_recall": False,
            "human_efficiency_baseline_available": False,
            "market_returns_are_causal": False,
            "latency_environment": "localhost_warm_cache",
            "shared_event_similarity_is_business_or_risk_causality": False,
        },
        "output_sha256": collect_output_hashes(output_directory),
    }


def run_evaluation(
    config_path: Path,
    output_override: Path | None = None,
) -> tuple[int, Path]:
    config_path = config_path.resolve()
    config = load_config(config_path)
    project_root = config_path.parent.parent
    evaluation = evaluation_config(config, project_root, output_override)
    output_directory = evaluation.output_directory.resolve()
    tables_directory = output_directory / "tables"
    figures_directory = output_directory / "figures"
    tables_directory.mkdir(parents=True, exist_ok=True)
    figures_directory.mkdir(parents=True, exist_ok=True)

    load_dotenv(project_root / ".env")
    settings = connection_settings(config)
    password = os.getenv(settings.password_environment_variable, "").strip()
    if not password:
        raise RuntimeError(
            f"{settings.password_environment_variable} is missing. Add it to "
            f"{project_root / '.env'}."
        )

    tasks = task_definitions(evaluation)
    result_frames: dict[str, pd.DataFrame] = {}
    timing_rows: list[dict[str, Any]] = []

    with GraphDatabase.driver(
        settings.uri, auth=(settings.user, password)
    ) as driver:
        driver.verify_connectivity()
        before = graph_state(driver, settings.database)
        environment = database_environment(driver, settings.database)

        for task in tasks:
            final_frame = pd.DataFrame()
            total_runs = evaluation.warmup_runs + evaluation.measured_runs
            for run_offset in range(total_runs):
                is_warmup = run_offset < evaluation.warmup_runs
                run_index = (
                    run_offset + 1
                    if is_warmup
                    else run_offset - evaluation.warmup_runs + 1
                )
                frame, timing = execute_timed_read(
                    driver, settings.database, task
                )
                timing_rows.append(
                    {
                        "task_id": task.task_id,
                        "run_index": run_index,
                        "is_warmup": is_warmup,
                        **timing,
                    }
                )
                if not is_warmup:
                    final_frame = frame
            result_frames[task.task_id] = final_frame

        after = graph_state(driver, settings.database)

    graph_unchanged = before == after
    timings = pd.DataFrame(timing_rows)
    performance = performance_table(
        tasks,
        timings,
        evaluation.warmup_runs,
        evaluation.measured_runs,
    )
    checks = task_quality_checks(evaluation, result_frames, graph_unchanged)
    reproducibility_rows: list[dict[str, Any]] = []
    for row in performance.itertuples():
        reproducibility_rows.extend(
            [
                _check(
                    row.task_id,
                    "stable_result_hash",
                    "reproducibility",
                    int(row.result_hash_stable),
                    1,
                    "All measured runs produced the same normalised result hash.",
                    "Stable output does not establish semantic correctness.",
                ),
                _check(
                    row.task_id,
                    "stable_row_count",
                    "reproducibility",
                    int(row.row_count_stable),
                    1,
                    "All measured runs produced the same result row count.",
                    "Row-count stability is an internal reproducibility check.",
                ),
            ]
        )
    checks = pd.concat(
        [checks, pd.DataFrame(reproducibility_rows)], ignore_index=True
    )
    summary = summary_table(tasks, result_frames, performance, checks)

    for task in tasks:
        write_csv(
            result_frames[task.task_id],
            tables_directory / task.output_filename,
        )
    write_csv(summary, tables_directory / "use_case_summary.csv")
    write_csv(performance, tables_directory / "task_performance.csv")
    write_csv(checks, tables_directory / "task_quality_checks.csv")
    write_csv(timings, tables_directory / "task_run_timings.csv")

    (figures_directory / "use_case_latency.svg").write_text(
        latency_svg(summary), encoding="utf-8"
    )
    (figures_directory / "use_case_completeness.svg").write_text(
        completeness_svg(summary), encoding="utf-8"
    )
    (output_directory / REPORT_FILENAMES[0]).write_text(
        evaluation_report(
            "zh", evaluation, summary, checks, graph_unchanged
        ),
        encoding="utf-8",
    )
    (output_directory / REPORT_FILENAMES[1]).write_text(
        evaluation_report(
            "en", evaluation, summary, checks, graph_unchanged
        ),
        encoding="utf-8",
    )

    script_path = Path(__file__).resolve()
    manifest = build_manifest(
        config_path=config_path,
        script_path=script_path,
        evaluation=evaluation,
        settings=settings,
        tasks=tasks,
        performance=performance,
        summary=summary,
        checks=checks,
        before=before,
        after=after,
        environment=environment,
        output_directory=output_directory,
    )
    (output_directory / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    failures = int((summary["status"] == "FAIL").sum())
    print(f"Analyst use cases completed: {len(tasks)}")
    print(f"Graph state unchanged: {graph_unchanged}")
    print(f"Task failures: {failures}")
    print(f"Output directory: {output_directory}")
    return (0 if failures == 0 and graph_unchanged else 2), output_directory


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = args.config.resolve()
    config = load_config(config_path)
    project_root = config_path.parent.parent
    evaluation = evaluation_config(
        config, project_root, args.output_directory
    )
    if args.print_output_directory:
        print(evaluation.output_directory.resolve())
        return 0
    if args.finalize_manifest:
        path = finalize_manifest(evaluation.output_directory.resolve())
        print(path)
        return 0
    exit_code, _ = run_evaluation(config_path, args.output_directory)
    return exit_code


def main() -> None:
    try:
        raise SystemExit(run())
    except AuthError as exc:
        print(
            "Neo4j authentication failed. Check NEO4J_PASSWORD in .env.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except ServiceUnavailable as exc:
        print(
            "Neo4j is unavailable. Start the local instance and check the URI.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except (Neo4jError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Analyst use-case evaluation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
