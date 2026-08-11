"""Build a credential-free, integrity-checked dashboard data snapshot.

The dashboard must not connect to Neo4j directly.  This module joins the
existing analyst report, GDS results and analyst-use-case evaluation into one
static JSON artifact after verifying their manifests and cross-checking their
shared graph counts.  The graph fingerprint captured by the read-only use-case
evaluation is used as the stable snapshot identifier.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


CANONICAL_SCOPE_COUNTS: dict[str, int] = {
    "companyCount": 25,
    "eventCount": 885,
    "impactCount": 1005,
    "sourceArticleCount": 564,
    "marketWindowCount": 6030,
}

DEFAULT_ANALYST_REPORT = Path("outputs/analyst_report/analyst_report_data.json")
DEFAULT_GDS_MANIFEST = Path("outputs/gds_analysis/gds_manifest.json")
DEFAULT_EVALUATION_MANIFEST = Path(
    "outputs/analyst_use_case_evaluation/analyst_use_case_manifest.json"
)
DEFAULT_OUTPUT = Path("frontend/public/data/dashboard.json")

INTEGER_PATTERN = re.compile(r"[-+]?(?:0|[1-9]\d*)\Z")
FLOAT_PATTERN = re.compile(
    r"[-+]?(?:(?:0|[1-9]\d*)\.\d+|(?:0|[1-9]\d*)[eE][-+]?\d+|"
    r"(?:0|[1-9]\d*)\.\d+[eE][-+]?\d+)\Z"
)
SENSITIVE_KEY_PATTERN = re.compile(r"(?:password|passwd|secret|credential|token)", re.I)
CONNECTION_SCHEME_PATTERN = re.compile(r"(?:neo4j|neo4j\+s|bolt|bolt\+s)://", re.I)


class SnapshotValidationError(ValueError):
    """Raised when source artifacts fail the dashboard snapshot contract."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a verified static JSON snapshot for the analyst dashboard."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--analyst-report", type=Path, default=DEFAULT_ANALYST_REPORT)
    parser.add_argument("--gds-manifest", type=Path, default=DEFAULT_GDS_MANIFEST)
    parser.add_argument(
        "--evaluation-manifest", type=Path, default=DEFAULT_EVALUATION_MANIFEST
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def resolve_path(project_root: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (project_root / value).resolve()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SnapshotValidationError(f"Required JSON artifact does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotValidationError(f"Cannot read valid UTF-8 JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SnapshotValidationError(f"Expected a JSON object in {path}")
    return value


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def manifest_artifact_path(root: Path, relative_name: str) -> Path:
    if not isinstance(relative_name, str) or not relative_name.strip():
        raise SnapshotValidationError("Manifest output paths must be non-empty strings")
    normalised = relative_name.replace("\\", "/")
    candidate = (root / Path(normalised)).resolve()
    if not _inside(root.resolve(), candidate):
        raise SnapshotValidationError(
            f"Manifest output path escapes its artifact directory: {relative_name!r}"
        )
    return candidate


def validate_manifest_output_hashes(manifest: Mapping[str, Any], root: Path) -> None:
    output_hashes = manifest.get("output_sha256")
    if not isinstance(output_hashes, Mapping) or not output_hashes:
        raise SnapshotValidationError(f"Manifest has no output_sha256 mapping: {root}")
    for relative_name, expected_hash in output_hashes.items():
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise SnapshotValidationError(
                f"Invalid SHA-256 value for manifest output {relative_name!r}"
            )
        artifact_path = manifest_artifact_path(root, str(relative_name))
        if not artifact_path.is_file():
            raise SnapshotValidationError(f"Manifest output is missing: {artifact_path}")
        actual_hash = sha256_file(artifact_path)
        if actual_hash != expected_hash:
            raise SnapshotValidationError(
                f"Manifest hash mismatch for {artifact_path}: "
                f"expected {expected_hash}, received {actual_hash}"
            )


def _require_bool(mapping: Mapping[str, Any], key: str, expected: bool, label: str) -> None:
    actual = mapping.get(key)
    if actual is not expected:
        raise SnapshotValidationError(
            f"{label}.{key} must be {expected!r}; received {actual!r}"
        )


def validate_read_only_contracts(
    gds_manifest: Mapping[str, Any], evaluation_manifest: Mapping[str, Any]
) -> None:
    gds_contract = gds_manifest.get("read_only_contract")
    if not isinstance(gds_contract, Mapping):
        raise SnapshotValidationError("GDS manifest has no read_only_contract")
    _require_bool(gds_contract, "persisted_kg_writeback", False, "GDS read_only_contract")
    _require_bool(gds_contract, "temporary_projections_dropped", True, "GDS read_only_contract")
    _require_bool(gds_contract, "database_counts_unchanged", True, "GDS read_only_contract")
    if gds_contract.get("catalog_before") != gds_contract.get("catalog_after"):
        raise SnapshotValidationError("GDS catalog differs before and after analysis")
    modes = gds_contract.get("algorithm_modes")
    if not isinstance(modes, list) or not modes or not set(modes).issubset(
        {"stream", "stats", "estimate"}
    ):
        raise SnapshotValidationError("GDS manifest includes a non-read-only algorithm mode")

    contract = evaluation_manifest.get("read_only_contract")
    if not isinstance(contract, Mapping):
        raise SnapshotValidationError("Use-case manifest has no read_only_contract")
    _require_bool(contract, "persisted_kg_writeback", False, "Use-case read_only_contract")
    _require_bool(contract, "cypher_write_operations_used", False, "Use-case read_only_contract")
    _require_bool(contract, "database_counts_unchanged", True, "Use-case read_only_contract")
    _require_bool(contract, "database_fingerprint_unchanged", True, "Use-case read_only_contract")
    if contract.get("routing_control") != "READ":
        raise SnapshotValidationError("Use-case evaluation must use READ routing control")

    graph_state = evaluation_manifest.get("graph_state")
    if not isinstance(graph_state, Mapping):
        raise SnapshotValidationError("Use-case manifest has no graph_state")
    _require_bool(graph_state, "unchanged", True, "Use-case graph_state")
    before = graph_state.get("before")
    after = graph_state.get("after")
    if not isinstance(before, Mapping) or before != after:
        raise SnapshotValidationError("Use-case graph state differs before and after evaluation")


def parse_scalar(value: Any) -> Any:
    """Parse CSV-like strings without corrupting IDs, dates, or ordinary text."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, dict)):
        return normalise_value(value)
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped == "":
        return None
    lowered = stripped.casefold()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none", "nan"}:
        return None
    if stripped[:1] in {"[", "{"} and stripped[-1:] in {"]",
        "}",
    }:
        try:
            return normalise_value(json.loads(stripped))
        except json.JSONDecodeError:
            pass
    if INTEGER_PATTERN.fullmatch(stripped):
        return int(stripped)
    if FLOAT_PATTERN.fullmatch(stripped):
        number = float(stripped)
        return number if math.isfinite(number) else None
    return value


def normalise_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): normalise_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalise_value(item) for item in value]
    return parse_scalar(value)


def snake_to_camel(name: str) -> str:
    pieces = name.split("_")
    return pieces[0] + "".join(piece[:1].upper() + piece[1:] for piece in pieces[1:])


def normalise_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {snake_to_camel(str(key)): parse_scalar(value) for key, value in row.items()}


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SnapshotValidationError(f"Required CSV artifact does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [normalise_row(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SnapshotValidationError(f"Cannot read CSV artifact {path}: {exc}") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotValidationError(f"Expected an object at {label}")
    return value


def _array(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise SnapshotValidationError(f"Expected an array of objects at {label}")
    return value


def _int(value: Any, label: str) -> int:
    parsed = parse_scalar(value)
    if isinstance(parsed, bool) or not isinstance(parsed, int):
        raise SnapshotValidationError(f"Expected an integer at {label}; received {value!r}")
    return parsed


def _equal_count(name: str, values: Mapping[str, int]) -> int:
    unique = set(values.values())
    if len(unique) != 1:
        details = ", ".join(f"{source}={count}" for source, count in values.items())
        raise SnapshotValidationError(f"Cross-source count mismatch for {name}: {details}")
    return next(iter(unique))


def validate_scope_counts(
    report: Mapping[str, Any],
    gds_manifest: Mapping[str, Any],
    evaluation_manifest: Mapping[str, Any],
    expected_counts: Mapping[str, int] | None,
) -> dict[str, int]:
    metadata = _mapping(report.get("metadata"), "analyst report metadata")
    filters = _mapping(metadata.get("filters"), "analyst report metadata.filters")
    if any(value not in (None, "") for value in filters.values()):
        raise SnapshotValidationError("Frontend snapshot requires an unfiltered analyst report")
    counts = _mapping(metadata.get("counts"), "analyst report metadata.counts")
    companies = _array(report.get("companies"), "analyst report companies")
    impacts = _array(report.get("events"), "analyst report events")
    sources = _array(report.get("sources"), "analyst report sources")
    market = _array(report.get("market"), "analyst report market")

    unique_events = {str(row.get("event_id")) for row in impacts if row.get("event_id")}
    unique_articles = {str(row.get("article_id")) for row in sources if row.get("article_id")}
    unique_market = {
        str(row.get("market_observation_id"))
        for row in market
        if row.get("market_observation_id")
    }

    graph = _mapping(evaluation_manifest.get("graph_state"), "use-case graph_state")
    graph_before = _mapping(graph.get("before"), "use-case graph_state.before")
    labels = _mapping(graph_before.get("nodes_by_label"), "graph nodes_by_label")
    relationships = _mapping(
        graph_before.get("relationships_by_type"), "graph relationships_by_type"
    )
    evaluation_summary = _mapping(
        evaluation_manifest.get("summary"), "use-case manifest summary"
    )
    gds_summary = _mapping(gds_manifest.get("summary"), "GDS manifest summary")

    resolved = {
        "companyCount": _equal_count(
            "companies",
            {
                "report metadata": _int(counts.get("companies"), "counts.companies"),
                "report rows": len(companies),
                "graph": _int(labels.get("Company"), "nodes_by_label.Company"),
                "GDS": _int(gds_summary.get("company_count"), "gds.summary.company_count"),
                "evaluation": _int(
                    evaluation_summary.get("company_scope_count"),
                    "evaluation.summary.company_scope_count",
                ),
            },
        ),
        "eventCount": _equal_count(
            "canonical events",
            {
                "report metadata": _int(
                    counts.get("canonical_events"), "counts.canonical_events"
                ),
                "report unique rows": len(unique_events),
                "graph": _int(labels.get("Event"), "nodes_by_label.Event"),
                "GDS bipartite minus companies": _int(
                    gds_summary.get("bipartite_node_count"),
                    "gds.summary.bipartite_node_count",
                )
                - len(companies),
            },
        ),
        "impactCount": _equal_count(
            "event-company impacts",
            {
                "report metadata": _int(
                    counts.get("event_company_links"), "counts.event_company_links"
                ),
                "report rows": len(impacts),
                "graph": _int(
                    relationships.get("POTENTIALLY_AFFECTS"),
                    "relationships_by_type.POTENTIALLY_AFFECTS",
                ),
                "GDS": _int(
                    gds_summary.get("bipartite_relationship_count"),
                    "gds.summary.bipartite_relationship_count",
                ),
            },
        ),
        "sourceArticleCount": _equal_count(
            "source articles",
            {
                "report metadata": _int(
                    counts.get("source_articles"), "counts.source_articles"
                ),
                "report unique rows": len(unique_articles),
                "graph": _int(labels.get("Article"), "nodes_by_label.Article"),
            },
        ),
        "marketWindowCount": _equal_count(
            "market windows",
            {
                "report metadata": _int(
                    counts.get("market_windows"), "counts.market_windows"
                ),
                "report rows": len(market),
                "report unique IDs": len(unique_market),
                "graph": _int(
                    labels.get("MarketObservation"), "nodes_by_label.MarketObservation"
                ),
            },
        ),
    }

    if len(sources) != _int(counts.get("source_evidence_rows"), "counts.source_evidence_rows"):
        raise SnapshotValidationError("Source evidence row count differs from analyst report metadata")
    if expected_counts is not None:
        for key, expected in expected_counts.items():
            if resolved.get(key) != expected:
                raise SnapshotValidationError(
                    f"Snapshot scope {key} must equal {expected}; received {resolved.get(key)!r}"
                )
    return resolved


def _false_causal_claim(value: Any, label: str) -> None:
    if parse_scalar(value) is not False:
        raise SnapshotValidationError(f"{label} must explicitly set causal_claim=false")


def validate_market_contract(
    report: Mapping[str, Any], evaluation_tables: Mapping[str, list[dict[str, Any]]]
) -> None:
    for index, row in enumerate(_array(report.get("market"), "analyst report market")):
        _false_causal_claim(row.get("causal_claim"), f"analyst report market[{index}]")
    for index, row in enumerate(evaluation_tables.get("T4", [])):
        _false_causal_claim(row.get("causalClaim"), f"evaluation T4[{index}]")


def _report_company(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "companyId": row.get("company_id"),
        "name": row.get("company"),
        "country": row.get("country"),
        "sourceRank": parse_scalar(row.get("source_rank")),
        "marketCapUsd": parse_scalar(row.get("market_cap_usd")),
        "rankingSnapshotDate": row.get("ranking_snapshot_date"),
        "symbol": row.get("symbol"),
        "eventCount": parse_scalar(row.get("event_count")),
    }


def _event_records(impact_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for row in impact_rows:
        event_id = str(row.get("event_id") or "")
        if not event_id:
            raise SnapshotValidationError("Every analyst event row must have an event_id")
        candidate = {
            "eventId": event_id,
            "date": row.get("event_date"),
            "type": row.get("event_type"),
            "title": row.get("event_title"),
            "summary": row.get("event_summary"),
            "classificationConfidence": row.get("classification_confidence"),
            "sourceEventCount": parse_scalar(row.get("source_event_count")),
            "sourceArticleCount": parse_scalar(row.get("source_article_count")),
            "deduplicationMethod": row.get("deduplication_method"),
        }
        previous = records.get(event_id)
        if previous is None:
            records[event_id] = candidate
        else:
            stable_fields = ("date", "type", "title", "summary")
            if any(previous[field] != candidate[field] for field in stable_fields):
                raise SnapshotValidationError(
                    f"Conflicting canonical-event attributes for {event_id}"
                )
            previous["sourceEventCount"] = max(
                int(previous.get("sourceEventCount") or 0),
                int(candidate.get("sourceEventCount") or 0),
            )
            previous["sourceArticleCount"] = max(
                int(previous.get("sourceArticleCount") or 0),
                int(candidate.get("sourceArticleCount") or 0),
            )
    return sorted(records.values(), key=lambda item: (str(item["date"]), item["eventId"]), reverse=True)


def _impact(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "companyId": row.get("company_id"),
        "eventId": row.get("event_id"),
        "evidenceSentence": row.get("relationship_evidence"),
        "nlpRelationshipLabel": row.get("nlp_relationship_label"),
        "nlpPositiveProbability": parse_scalar(row.get("nlp_positive_probability")),
        "relationshipFocusScore": parse_scalar(row.get("relationship_focus_score")),
        "hybridDecisionReason": row.get("hybrid_decision_reason"),
        "relationshipSourceEventId": row.get("relationship_source_event_id"),
        "relationshipSourceArticleId": row.get("relationship_source_article_id"),
        "sourceUrl": row.get("relationship_source_url"),
    }


def _source(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "companyId": row.get("company_id"),
        "eventId": row.get("event_id"),
        "sourceEventId": row.get("source_event_id"),
        "sourceEventDate": row.get("source_event_date"),
        "sourceEventTitle": row.get("source_event_title"),
        "evidenceSentence": row.get("source_evidence_span"),
        "evidenceSource": row.get("source_evidence_source"),
        "similarityToRepresentative": parse_scalar(row.get("similarity_to_representative")),
        "isRepresentative": parse_scalar(row.get("is_representative")),
        "articleId": row.get("article_id"),
        "articleTitle": row.get("article_title"),
        "publicationTimestamp": row.get("publication_timestamp"),
        "sectionName": row.get("section_name"),
        "url": row.get("source_url"),
        "isRelationshipSource": parse_scalar(row.get("is_relationship_source")),
    }


def _market(row: Mapping[str, Any]) -> dict[str, Any]:
    _false_causal_claim(row.get("causal_claim"), "analyst report market row")
    return {
        "companyId": row.get("company_id"),
        "eventId": row.get("event_id"),
        "symbol": row.get("symbol"),
        "marketObservationId": row.get("market_observation_id"),
        "windowDays": parse_scalar(row.get("window_days")),
        "baselineDate": row.get("baseline_date"),
        "windowEndDate": row.get("window_end_date"),
        "baselineClose": parse_scalar(row.get("baseline_close")),
        "windowEndClose": parse_scalar(row.get("window_end_close")),
        "cumulativeReturn": parse_scalar(row.get("cumulative_return")),
        "anchorRule": row.get("anchor_rule"),
        "dataSource": row.get("data_source"),
        "causalClaim": False,
    }


def _csv(root: Path, relative_name: str) -> list[dict[str, Any]]:
    return read_csv_rows(manifest_artifact_path(root, relative_name))


def load_gds_tables(root: Path) -> dict[str, list[dict[str, Any]]]:
    names = {
        "nodes": "tables/company_centrality.csv",
        "edges": "tables/company_coevent_edges.csv",
        "similarities": "tables/company_node_similarity.csv",
        "components": "tables/wcc_components.csv",
        "communities": "tables/weighted_louvain_community_summary.csv",
        "unweightedCommunities": "tables/unweighted_louvain_community_summary.csv",
        "thresholdSensitivity": "tables/threshold_sensitivity.csv",
        "algorithmSummary": "tables/gds_algorithm_summary.csv",
    }
    return {key: _csv(root, name) for key, name in names.items()}


def load_evaluation_tables(
    root: Path, evaluation_manifest: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    tables = {
        "useCases": _csv(root, "tables/use_case_summary.csv"),
        "performance": _csv(root, "tables/task_performance.csv"),
        "qualityChecks": _csv(root, "tables/task_quality_checks.csv"),
        "runTimings": _csv(root, "tables/task_run_timings.csv"),
    }
    tasks = evaluation_manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SnapshotValidationError("Use-case manifest has no task definitions")
    for task in tasks:
        if not isinstance(task, Mapping):
            raise SnapshotValidationError("Use-case task definitions must be objects")
        task_id = str(task.get("task_id") or "")
        output = task.get("output")
        if not task_id or not isinstance(output, str):
            raise SnapshotValidationError("Every use-case task needs task_id and output")
        tables[task_id] = _csv(root, output)
    return tables


def _safe_task(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "taskId": task.get("task_id"),
        "titleEn": task.get("title_en"),
        "titleCn": task.get("title_cn"),
        "scope": task.get("scope"),
        "parameters": normalise_value(task.get("parameters") or {}),
        "querySha256": task.get("query_sha256"),
    }


def _date_scope(impacts: list[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    dates = sorted(str(row["event_date"]) for row in impacts if row.get("event_date"))
    return (dates[0], dates[-1]) if dates else (None, None)


def _month_sequence(start_date: str | None, end_date: str | None) -> list[str]:
    """Return an inclusive YYYY-MM sequence without adding a date dependency."""
    if not start_date or not end_date:
        return []
    try:
        start_year, start_month = (int(value) for value in start_date[:7].split("-"))
        end_year, end_month = (int(value) for value in end_date[:7].split("-"))
    except (TypeError, ValueError) as exc:
        raise SnapshotValidationError("Event date range is not YYYY-MM compatible") from exc
    if (start_year, start_month) > (end_year, end_month):
        raise SnapshotValidationError("Event date range starts after it ends")
    months: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def _visualization_payload(
    companies: list[Mapping[str, Any]],
    events: list[Mapping[str, Any]],
    impacts: list[Mapping[str, Any]],
    similarities: list[Mapping[str, Any]],
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    """Build compact, deterministic chart inputs from the verified graph rows."""
    months = _month_sequence(start_date, end_date)
    month_set = set(months)
    event_month: dict[str, str] = {}
    portfolio_events: dict[str, set[str]] = {month: set() for month in months}
    for event in events:
        event_id = str(event.get("eventId") or "")
        month = str(event.get("date") or "")[:7]
        if not event_id or month not in month_set:
            raise SnapshotValidationError(
                f"Visualisation event {event_id or '<missing>'} falls outside the date range"
            )
        event_month[event_id] = month
        portfolio_events[month].add(event_id)

    company_links: dict[str, dict[str, set[str]]] = {
        str(company.get("companyId")): {month: set() for month in months}
        for company in companies
    }
    portfolio_links: dict[str, int] = {month: 0 for month in months}
    seen_links: set[tuple[str, str]] = set()
    for impact in impacts:
        company_id = str(impact.get("companyId") or "")
        event_id = str(impact.get("eventId") or "")
        if company_id not in company_links or event_id not in event_month:
            raise SnapshotValidationError(
                f"Visualisation impact references unknown company/event: {company_id}/{event_id}"
            )
        link = (company_id, event_id)
        if link in seen_links:
            raise SnapshotValidationError(
                f"Duplicate company-event link in visualisation input: {company_id}/{event_id}"
            )
        seen_links.add(link)
        month = event_month[event_id]
        company_links[company_id][month].add(event_id)
        portfolio_links[month] += 1

    company_series = [
        {
            "companyId": str(company.get("companyId")),
            "values": [len(company_links[str(company.get("companyId"))][month]) for month in months],
        }
        for company in companies
    ]
    matrix_cells = [
        {
            "company1Id": row.get("company1Id"),
            "company2Id": row.get("company2Id"),
            "sharedEventCount": int(row.get("sharedEventCount") or 0),
            "similarity": float(row.get("similarity") or 0.0),
        }
        for row in similarities
        if int(row.get("sharedEventCount") or 0) > 0
    ]
    matrix_cells.sort(
        key=lambda row: (str(row["company1Id"]), str(row["company2Id"]))
    )
    return {
        "schemaVersion": 1,
        "timeSeries": {
            "months": months,
            "portfolio": [
                {
                    "month": month,
                    "eventCount": len(portfolio_events[month]),
                    "impactCount": portfolio_links[month],
                }
                for month in months
            ],
            "companies": company_series,
        },
        "sharedEventMatrix": {
            "companyIds": [str(company.get("companyId")) for company in companies],
            "maximumSharedEventCount": max(
                (int(row["sharedEventCount"]) for row in matrix_cells), default=0
            ),
            "cells": matrix_cells,
        },
    }


def _validate_evaluation_crosschecks(
    evaluation_tables: Mapping[str, list[dict[str, Any]]],
    counts: Mapping[str, int],
    gds_tables: Mapping[str, list[dict[str, Any]]],
) -> None:
    if len(evaluation_tables.get("T1", [])) != counts["companyCount"]:
        raise SnapshotValidationError("T1 company screening does not cover the snapshot company scope")
    supported_gds_pairs = {
        (row.get("company1Id"), row.get("company2Id"))
        for row in gds_tables["edges"]
        if row.get("meetsSupportThreshold") is True
    }
    evaluated_pairs = {
        (row.get("company1Id"), row.get("company2Id"))
        for row in evaluation_tables.get("T5", [])
    }
    if supported_gds_pairs != evaluated_pairs:
        raise SnapshotValidationError(
            "T5 shared-event pairs do not match GDS support-threshold edges"
        )


def assert_no_credentials(value: Any, path: str = "dashboard") -> None:
    """Reject credential-like keys and direct database connection strings."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).casefold()
            if lowered in {"uri", "databaseuri", "username", "user"} or SENSITIVE_KEY_PATTERN.search(
                lowered
            ):
                raise SnapshotValidationError(f"Sensitive key is forbidden at {path}.{key}")
            assert_no_credentials(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_credentials(item, f"{path}[{index}]")
    elif isinstance(value, str) and CONNECTION_SCHEME_PATTERN.search(value):
        raise SnapshotValidationError(f"Database connection string is forbidden at {path}")


def build_snapshot(
    analyst_report_path: Path,
    gds_manifest_path: Path,
    evaluation_manifest_path: Path,
    *,
    expected_counts: Mapping[str, int] | None = CANONICAL_SCOPE_COUNTS,
) -> dict[str, Any]:
    report = load_json(analyst_report_path)
    gds_manifest = load_json(gds_manifest_path)
    evaluation_manifest = load_json(evaluation_manifest_path)
    gds_root = gds_manifest_path.parent.resolve()
    evaluation_root = evaluation_manifest_path.parent.resolve()

    validate_manifest_output_hashes(gds_manifest, gds_root)
    validate_manifest_output_hashes(evaluation_manifest, evaluation_root)
    validate_read_only_contracts(gds_manifest, evaluation_manifest)

    counts = validate_scope_counts(
        report, gds_manifest, evaluation_manifest, expected_counts
    )
    gds_tables = load_gds_tables(gds_root)
    evaluation_tables = load_evaluation_tables(evaluation_root, evaluation_manifest)
    validate_market_contract(report, evaluation_tables)
    _validate_evaluation_crosschecks(evaluation_tables, counts, gds_tables)

    metadata = _mapping(report.get("metadata"), "analyst report metadata")
    report_counts = _mapping(metadata.get("counts"), "analyst report metadata.counts")
    graph_state = _mapping(evaluation_manifest.get("graph_state"), "graph_state")
    graph_before = _mapping(graph_state.get("before"), "graph_state.before")
    snapshot_id = graph_before.get("sha256")
    if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise SnapshotValidationError("Use-case graph fingerprint is not a valid SHA-256")

    impact_rows = _array(report.get("events"), "analyst report events")
    start_date, end_date = _date_scope(impact_rows)
    companies = [_report_company(row) for row in _array(report.get("companies"), "companies")]
    companies.sort(key=lambda item: (int(item.get("sourceRank") or 10**9), str(item["companyId"])))
    events = _event_records(impact_rows)
    impacts = [_impact(row) for row in impact_rows]
    sources = [_source(row) for row in _array(report.get("sources"), "sources")]
    market = [_market(row) for row in _array(report.get("market"), "market")]
    gds_summary = normalise_row(_mapping(gds_manifest.get("summary"), "GDS summary"))
    evaluation_summary = normalise_row(
        _mapping(evaluation_manifest.get("summary"), "evaluation summary")
    )
    interpretation = _mapping(
        metadata.get("interpretation"), "analyst report metadata.interpretation"
    )

    dashboard: dict[str, Any] = {
        "scope": {
            "snapshotId": snapshot_id,
            "sourceGeneratedAtUtc": metadata.get("generated_at_utc"),
            "gdsGeneratedAtUtc": gds_manifest.get("generated_at_utc"),
            "evaluationGeneratedAtUtc": evaluation_manifest.get("generated_at_utc"),
            "eventDateRange": {"start": start_date, "end": end_date},
            "publisher": "The Guardian",
            "rankingSnapshotDate": next(
                (company.get("rankingSnapshotDate") for company in companies if company.get("rankingSnapshotDate")),
                None,
            ),
            "fullPortfolioSnapshot": True,
        },
        "summary": {
            **counts,
            "coveredCompanyCount": _int(
                report_counts.get("companies_with_events"), "counts.companies_with_events"
            ),
            "sourceEvidenceRowCount": len(sources),
            "multiSourceEventCount": _int(
                report_counts.get("multi_source_events"), "counts.multi_source_events"
            ),
            "validationFailureCount": _int(
                report_counts.get("validation_failures"), "counts.validation_failures"
            ),
            "networkEdgeCount": len(gds_tables["edges"]),
            "supportedNetworkEdgeCount": sum(
                row.get("meetsSupportThreshold") is True for row in gds_tables["edges"]
            ),
            "evaluationTaskCount": len(evaluation_manifest.get("tasks", [])),
            "evaluationChecksPassed": evaluation_summary.get("qualityChecksPassed"),
            "evaluationChecksTotal": evaluation_summary.get("qualityChecksTotal"),
        },
        "companies": companies,
        "events": events,
        "impacts": impacts,
        "sources": sources,
        "market": market,
        "visualizations": _visualization_payload(
            companies,
            events,
            impacts,
            gds_tables["similarities"],
            start_date,
            end_date,
        ),
        "network": {
            "summary": gds_summary,
            "nodes": gds_tables["nodes"],
            "edges": gds_tables["edges"],
            "similarities": gds_tables["similarities"],
            "components": gds_tables["components"],
            "communities": gds_tables["communities"],
            "unweightedCommunities": gds_tables["unweightedCommunities"],
            "thresholdSensitivity": gds_tables["thresholdSensitivity"],
            "algorithmSummary": gds_tables["algorithmSummary"],
        },
        "evaluation": {
            "summary": evaluation_summary,
            "timingProtocol": normalise_value(evaluation_manifest.get("timing_protocol") or {}),
            "tasks": [_safe_task(task) for task in evaluation_manifest.get("tasks", [])],
            "useCases": evaluation_tables["useCases"],
            "performance": evaluation_tables["performance"],
            "qualityChecks": evaluation_tables["qualityChecks"],
            "runTimings": evaluation_tables["runTimings"],
            "taskResults": {
                task_id: evaluation_tables[task_id]
                for task_id in sorted(
                    key for key in evaluation_tables if re.fullmatch(r"T\d+", key)
                )
            },
        },
        "disclaimers": [
            {
                "code": "coverage-boundary",
                "titleCn": "新闻范围",
                "titleEn": "News coverage",
                "bodyCn": "这里只使用所选时间内的 Guardian 报道和 25 家公司，不代表所有新闻。",
                "bodyEn": "This data covers selected Guardian articles and 25 companies. It does not cover all news.",
            },
            {
                "code": "internal-completeness",
                "titleCn": "数据完整不等于完全准确",
                "titleEn": "Complete records can still contain errors",
                "bodyCn": "每条结果都有证据和来源，但自动提取仍可能出错。请回到原始报道核实。",
                "bodyEn": "Each result has evidence and a source, but automated extraction can still be wrong. Check the original article.",
            },
            {
                "code": "descriptive-market-context",
                "titleCn": "价格变化不是因果结论",
                "titleEn": "Price changes do not prove cause",
                "bodyCn": "前后 1、3、7 个交易日的收益只显示报道发布前后的价格变化，不代表事件导致了变化。",
                "bodyEn": "Returns for 1, 3 and 7 trading days before and after publication show price context. They do not prove that the event caused a move.",
            },
            {
                "code": "exploratory-network",
                "titleCn": "网络连线不是商业关系",
                "titleEn": "Network lines are not business links",
                "bodyCn": "连线表示公司出现在相同新闻事件中，不代表它们存在商业关系或同样重要。",
                "bodyEn": "A line means that companies appear in the same news events. It does not confirm a business relationship or equal importance.",
            },
            {
                "code": "latency-boundary",
                "titleCn": "速度仅供参考",
                "titleEn": "Speed is a guide only",
                "bodyCn": "查询时间在本机数据已载入后测得，不包含数据库启动、网络传输和页面显示时间。",
                "bodyEn": "Query times were measured on this computer after the data had loaded. They exclude database startup, network delivery and page rendering.",
            },
        ],
    }
    assert_no_credentials(dashboard)
    # Fail before writing if non-finite values slipped through a source artifact.
    try:
        json.dumps(dashboard, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SnapshotValidationError(f"Dashboard snapshot is not strict JSON: {exc}") from exc
    return dashboard


def write_snapshot(snapshot: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output_path.write_text(payload, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = args.project_root.resolve()
    output_path = resolve_path(project_root, args.output)
    try:
        snapshot = build_snapshot(
            resolve_path(project_root, args.analyst_report),
            resolve_path(project_root, args.gds_manifest),
            resolve_path(project_root, args.evaluation_manifest),
        )
        write_snapshot(snapshot, output_path)
    except SnapshotValidationError as exc:
        print(f"Frontend snapshot validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Frontend dashboard snapshot: {output_path}")
    print(f"Snapshot ID: {snapshot['scope']['snapshotId']}")
    print(
        "Scope: "
        f"{snapshot['summary']['companyCount']} companies, "
        f"{snapshot['summary']['eventCount']} events, "
        f"{snapshot['summary']['impactCount']} impacts"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
