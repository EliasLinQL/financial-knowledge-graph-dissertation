"""Build a Neo4j LOAD CSV package from automatically recommended graph records."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml


COMPANY_COLUMNS = {
    "company_id",
    "company_name",
    "aliases",
    "twelve_data_symbol",
    "twelve_data_exchange",
    "sector",
    "industry",
    "country",
}
ARTICLE_COLUMNS = {
    "article_id",
    "headline",
    "publication_date",
    "section_id",
    "section_name",
    "web_url",
    "analysis_ready",
}
ARTICLE_LINK_COLUMNS = {
    "article_id",
    "company_id",
    "link_status",
    "accepted_for_analysis",
}
EVENT_COLUMNS = {
    "event_id",
    "article_id",
    "event_date",
    "publication_timestamp",
    "event_title",
    "event_summary",
    "evidence_span",
    "evidence_source",
    "event_granularity",
    "event_span_hash",
    "source_headline",
    "event_type",
    "event_score",
    "classification_confidence",
    "recommended_for_graph",
}
EVENT_LINK_COLUMNS = {
    "event_id",
    "article_id",
    "company_id",
    "relationship_type",
    "evidence_sentence",
    "relationship_focus_score",
    "recommended_for_graph",
}
EVENT_MENTION_COLUMNS = {
    "canonical_event_id",
    "source_event_id",
    "article_id",
    "event_date",
    "publication_timestamp",
    "event_title",
    "evidence_span",
    "similarity_to_representative",
    "is_representative",
    "deduplication_method",
}
MARKET_WINDOW_COLUMNS = {
    "market_link_id",
    "event_id",
    "article_id",
    "company_id",
    "symbol",
    "exchange",
    "window_trading_days",
    "cumulative_return",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create graph-ready Neo4j CSV files from filtered Guardian event "
            "records."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the project YAML configuration.",
    )
    parser.add_argument(
        "--mode",
        choices=("test", "full"),
        default="full",
        help="Guardian collection mode to package.",
    )
    parser.add_argument("--selected-companies", type=Path)
    parser.add_argument("--articles-csv", type=Path)
    parser.add_argument("--article-links-csv", type=Path)
    parser.add_argument("--events-csv", type=Path)
    parser.add_argument("--event-links-csv", type=Path)
    parser.add_argument(
        "--event-mentions-csv",
        type=Path,
        help="Optional canonical Event-to-source mention provenance CSV.",
    )
    parser.add_argument("--market-windows-csv", type=Path)
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="Output directory. Defaults to data/neo4j/import.",
    )
    parser.add_argument(
        "--include-not-recommended",
        action="store_true",
        help=(
            "Include all analysis-ready event and relationship candidates. "
            "Use only for sensitivity analysis; the default graph uses the "
            "automatic recommended_for_graph policy."
        ),
    )
    return parser.parse_args()


def load_yaml(path: Path) -> dict[str, Any]:
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


def read_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def parse_bool(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def bool_text(value: Any) -> str:
    return "true" if parse_bool(value) else "false"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def integer_text(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Expected an integer-compatible value, received {text!r}") from exc
    if number != number.to_integral_value():
        raise ValueError(f"Expected an integer-compatible value, received {text!r}")
    return str(int(number))


def rounded_integer_text(value: Any) -> str:
    """Convert a finite decimal value to the nearest integer using half-up rounding."""
    text = clean_text(value)
    if not text:
        return ""
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Expected a numeric value, received {text!r}") from exc
    if not number.is_finite():
        raise ValueError(f"Expected a finite numeric value, received {text!r}")
    return str(int(number.to_integral_value(rounding=ROUND_HALF_UP)))


def dimension_id(prefix: str, name: str) -> str:
    normalised = clean_text(name).casefold()
    slug = re.sub(r"[^A-Z0-9]+", "_", normalised.upper()).strip("_")
    slug = slug[:48] or "UNNAMED"
    digest = hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:8].upper()
    return f"{prefix}_{slug}_{digest}"


def asset_id(symbol: str, exchange: str) -> str:
    value = f"{clean_text(symbol)}_{clean_text(exchange)}".upper()
    return "AST_" + re.sub(r"[^A-Z0-9]+", "_", value).strip("_")


def market_observation_id(market_link_id: str, window_trading_days: str) -> str:
    return f"{clean_text(market_link_id)}-{clean_text(window_trading_days)}"


def ensure_unique(
    rows: list[dict[str, str]], fields: tuple[str, ...], label: str
) -> None:
    seen: set[tuple[str, ...]] = set()
    duplicates: list[tuple[str, ...]] = []
    for row in rows:
        key = tuple(row[field] for field in fields)
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    if duplicates:
        preview = ", ".join("|".join(key) for key in duplicates[:5])
        raise ValueError(f"{label} contains duplicate keys: {preview}")


def ensure_nonblank(
    rows: list[dict[str, str]], fields: Iterable[str], label: str
) -> None:
    failures: list[str] = []
    for index, row in enumerate(rows, start=1):
        missing = [field for field in fields if not clean_text(row.get(field, ""))]
        if missing:
            failures.append(f"row {index}: {', '.join(missing)}")
    if failures:
        raise ValueError(f"{label} contains blank required fields: {failures[:5]}")


def ensure_references(
    rows: list[dict[str, str]],
    field: str,
    valid_ids: set[str],
    label: str,
) -> None:
    missing = sorted({row[field] for row in rows if row[field] not in valid_ids})
    if missing:
        raise ValueError(
            f"{label} references missing {field} values: {', '.join(missing[:10])}"
        )


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {path}. Close this file in Excel, Neo4j, or another editor."
        ) from exc


def record(frame_row: pd.Series, field: str) -> str:
    return clean_text(frame_row.get(field, ""))


def build_cypher() -> str:
    return """// Neo4j 5.x LOAD CSV package generated by src/build_kg_import.py
// Copy the CSV files in this directory into the Neo4j import directory,
// then run these statements in Neo4j Browser or cypher-shell.

CREATE CONSTRAINT company_id_unique IF NOT EXISTS FOR (n:Company) REQUIRE n.company_id IS UNIQUE;
CREATE CONSTRAINT sector_id_unique IF NOT EXISTS FOR (n:Sector) REQUIRE n.sector_id IS UNIQUE;
CREATE CONSTRAINT industry_id_unique IF NOT EXISTS FOR (n:Industry) REQUIRE n.industry_id IS UNIQUE;
CREATE CONSTRAINT asset_id_unique IF NOT EXISTS FOR (n:Asset) REQUIRE n.asset_id IS UNIQUE;
CREATE CONSTRAINT article_id_unique IF NOT EXISTS FOR (n:Article) REQUIRE n.article_id IS UNIQUE;
CREATE CONSTRAINT event_id_unique IF NOT EXISTS FOR (n:Event) REQUIRE n.event_id IS UNIQUE;
CREATE CONSTRAINT market_observation_id_unique IF NOT EXISTS FOR (n:MarketObservation) REQUIRE n.market_observation_id IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///companies.csv' AS row
MERGE (n:Company {company_id: row.company_id})
SET n.name = row.company_name,
    n.aliases = row.aliases,
    n.country = row.country,
    n.sample_order = toInteger(row.sample_order),
    n.source_rank = toInteger(row.source_rank),
    n.market_cap_usd = toInteger(row.market_cap_usd),
    n.ranking_snapshot_date = date(row.ranking_snapshot_date);

LOAD CSV WITH HEADERS FROM 'file:///sectors.csv' AS row
MERGE (n:Sector {sector_id: row.sector_id})
SET n.name = row.sector_name;

LOAD CSV WITH HEADERS FROM 'file:///industries.csv' AS row
MERGE (n:Industry {industry_id: row.industry_id})
SET n.name = row.industry_name;

LOAD CSV WITH HEADERS FROM 'file:///assets.csv' AS row
MERGE (n:Asset {asset_id: row.asset_id})
SET n.symbol = row.symbol,
    n.exchange = row.exchange,
    n.asset_type = row.asset_type,
    n.data_source = row.data_source;

LOAD CSV WITH HEADERS FROM 'file:///articles.csv' AS row
MERGE (n:Article {article_id: row.article_id})
SET n.title = row.title,
    n.publication_timestamp = datetime(row.publication_timestamp),
    n.section_id = row.section_id,
    n.section_name = row.section_name,
    n.web_url = row.web_url,
    n.short_url = row.short_url,
    n.byline = row.byline,
    n.wordcount = toInteger(row.wordcount),
    n.trail_text = row.trail_text,
    n.body_text = row.body_text,
    n.tag_ids = row.tag_ids,
    n.tag_titles = row.tag_titles,
    n.source = row.source;

LOAD CSV WITH HEADERS FROM 'file:///events.csv' AS row
MERGE (n:Event {event_id: row.event_id})
SET n.event_date = date(row.event_date),
    n.publication_timestamp = datetime(row.publication_timestamp),
    n.title = row.event_title,
    n.summary = row.event_summary,
    n.evidence_span = row.evidence_span,
    n.evidence_source = row.evidence_source,
    n.evidence_position = toInteger(row.evidence_position),
    n.event_granularity = row.event_granularity,
    n.event_span_hash = row.event_span_hash,
    n.source_headline = row.source_headline,
    n.event_type = row.event_type,
    n.rule_event_type = row.rule_event_type,
    n.event_score = toInteger(row.event_score),
    n.classification_confidence = row.classification_confidence,
    n.matched_event_keywords = row.matched_event_keywords,
    n.secondary_event_types = row.secondary_event_types,
    n.event_type_scores = row.event_type_scores,
    n.classification_tie = toBoolean(row.classification_tie),
    n.nlp_event_type = row.nlp_event_type,
    n.nlp_event_score = toFloat(row.nlp_event_score),
    n.nlp_event_scores = row.nlp_event_scores,
    n.nlp_model_name = row.nlp_model_name,
    n.nlp_model_revision = row.nlp_model_revision,
    n.hybrid_decision_reason = row.hybrid_decision_reason,
    n.section_name = row.section_name,
    n.source_method = row.source_method,
    n.web_url = row.web_url,
    n.representative_event_id = row.representative_event_id,
    n.source_event_count = toInteger(row.source_event_count),
    n.source_article_count = toInteger(row.source_article_count),
    n.source_event_ids = row.source_event_ids,
    n.source_article_ids = row.source_article_ids,
    n.first_publication_timestamp = datetime(row.first_publication_timestamp),
    n.last_publication_timestamp = datetime(row.last_publication_timestamp),
    n.deduplication_method = row.deduplication_method,
    n.deduplication_min_similarity = toFloat(row.deduplication_min_similarity),
    n.deduplication_max_date_span_days =
        toInteger(row.deduplication_max_date_span_days);

LOAD CSV WITH HEADERS FROM 'file:///market_observations.csv' AS row
MERGE (n:MarketObservation {market_observation_id: row.market_observation_id})
SET n.market_link_id = row.market_link_id,
    n.publication_timestamp_utc = datetime(row.publication_timestamp_utc),
    n.publication_timestamp_market_tz = datetime(row.publication_timestamp_market_tz),
    n.anchor_rule = row.anchor_rule,
    n.baseline_date = date(row.baseline_date),
    n.baseline_close = toFloat(row.baseline_close),
    n.window_trading_days = toInteger(row.window_trading_days),
    n.window_end_date = date(row.window_end_date),
    n.window_end_close = toFloat(row.window_end_close),
    n.cumulative_return = toFloat(row.cumulative_return),
    n.data_source = row.data_source,
    n.causal_claim = toBoolean(row.causal_claim);

LOAD CSV WITH HEADERS FROM 'file:///company_issues_asset.csv' AS row
MATCH (c:Company {company_id: row.company_id})
MATCH (a:Asset {asset_id: row.asset_id})
MERGE (c)-[:ISSUES]->(a);

LOAD CSV WITH HEADERS FROM 'file:///company_belongs_to_industry.csv' AS row
MATCH (c:Company {company_id: row.company_id})
MATCH (i:Industry {industry_id: row.industry_id})
MERGE (c)-[:BELONGS_TO]->(i);

LOAD CSV WITH HEADERS FROM 'file:///industry_part_of_sector.csv' AS row
MATCH (i:Industry {industry_id: row.industry_id})
MATCH (s:Sector {sector_id: row.sector_id})
MERGE (i)-[:PART_OF]->(s);

LOAD CSV WITH HEADERS FROM 'file:///article_reports_event.csv' AS row
MATCH (a:Article {article_id: row.article_id})
MATCH (e:Event {event_id: row.event_id})
MERGE (a)-[r:REPORTS]->(e)
SET r.source_event_id = row.source_event_id,
    r.source_event_date = date(row.source_event_date),
    r.source_publication_timestamp =
        datetime(row.source_publication_timestamp),
    r.source_event_title = row.source_event_title,
    r.source_evidence_span = row.source_evidence_span,
    r.source_evidence_source = row.source_evidence_source,
    r.source_event_granularity = row.source_event_granularity,
    r.similarity_to_representative =
        toFloat(row.similarity_to_representative),
    r.is_representative = toBoolean(row.is_representative),
    r.deduplication_method = row.deduplication_method;

LOAD CSV WITH HEADERS FROM 'file:///article_mentions_company.csv' AS row
MATCH (a:Article {article_id: row.article_id})
MATCH (c:Company {company_id: row.company_id})
MERGE (a)-[r:MENTIONS]->(c)
SET r.link_status = row.link_status,
    r.evidence_type = row.evidence_type,
    r.query_returned = toBoolean(row.query_returned),
    r.matched_core_aliases = row.matched_core_aliases,
    r.matched_product_aliases = row.matched_product_aliases;

LOAD CSV WITH HEADERS FROM 'file:///event_potentially_affects_company.csv' AS row
MATCH (e:Event {event_id: row.event_id})
MATCH (c:Company {company_id: row.company_id})
MERGE (e)-[r:POTENTIALLY_AFFECTS]->(c)
SET r.link_status = row.link_status,
    r.link_confidence = row.link_confidence,
    r.relationship_focus_score = toInteger(row.relationship_focus_score),
    r.evidence_sentence = row.evidence_sentence,
    r.rule_evidence_sentence = row.rule_evidence_sentence,
    r.headline_aliases = row.headline_aliases,
    r.trail_aliases = row.trail_aliases,
    r.event_keyword_same_sentence = row.event_keyword_same_sentence,
    r.nlp_raw_relationship_label = row.nlp_raw_relationship_label,
    r.nlp_relationship_label = row.nlp_relationship_label,
    r.nlp_relationship_score = toFloat(row.nlp_relationship_score),
    r.nlp_positive_probability = toFloat(row.nlp_positive_probability),
    r.nlp_relationship_scores = row.nlp_relationship_scores,
    r.nlp_model_name = row.nlp_model_name,
    r.nlp_model_revision = row.nlp_model_revision,
    r.nlp_role_calibration_reason = row.nlp_role_calibration_reason,
    r.hybrid_decision_reason = row.hybrid_decision_reason,
    r.source_method = row.source_method,
    r.source_event_id = row.source_event_id,
    r.source_article_id = row.source_article_id,
    r.relationship_publication_timestamp = CASE
        WHEN trim(row.relationship_publication_timestamp) = '' THEN null
        ELSE datetime(row.relationship_publication_timestamp)
    END,
    r.source_relationship_count = toInteger(row.source_relationship_count),
    r.source_event_ids = row.source_event_ids,
    r.source_article_ids = row.source_article_ids,
    r.deduplicated_relationship =
        toBoolean(row.deduplicated_relationship);

LOAD CSV WITH HEADERS FROM 'file:///event_has_market_observation.csv' AS row
MATCH (e:Event {event_id: row.event_id})
MATCH (m:MarketObservation {market_observation_id: row.market_observation_id})
MERGE (e)-[:HAS_MARKET_OBSERVATION]->(m);

LOAD CSV WITH HEADERS FROM 'file:///asset_has_market_observation.csv' AS row
MATCH (a:Asset {asset_id: row.asset_id})
MATCH (m:MarketObservation {market_observation_id: row.market_observation_id})
MERGE (a)-[:HAS_MARKET_OBSERVATION]->(m);
"""


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_yaml(config_path)
    project_root = config_path.parent.parent
    processed_news = resolve_path(
        project_root, config["news_data"]["processed_output_directory"]
    )
    stem = config["news_data"]["collection_modes"][args.mode]["output_stem"]
    nlp_config = config.get("nlp_enrichment", {})
    dedup_config = config.get("event_deduplication", {})
    use_nlp_inputs = bool(
        isinstance(nlp_config, dict)
        and nlp_config.get("enabled", False)
        and nlp_config.get("use_for_downstream", False)
    )
    use_deduplicated_inputs = bool(
        isinstance(dedup_config, dict)
        and dedup_config.get("enabled", False)
        and dedup_config.get("use_for_downstream", False)
    )
    if use_deduplicated_inputs:
        event_filename = f"{stem}_canonical_events.csv"
        event_link_filename = f"{stem}_canonical_event_company_links.csv"
    else:
        event_filename = (
            f"{stem}_event_candidates_nlp.csv"
            if use_nlp_inputs
            else f"{stem}_event_candidates.csv"
        )
        event_link_filename = (
            f"{stem}_event_company_links_nlp.csv"
            if use_nlp_inputs
            else f"{stem}_event_company_links.csv"
        )

    company_path = resolve_path(
        project_root,
        args.selected_companies or config["outputs"]["selected_companies"],
    )
    article_path = resolve_path(
        project_root,
        args.articles_csv or processed_news / f"{stem}_articles_clean.csv",
    )
    article_link_path = resolve_path(
        project_root,
        args.article_links_csv
        or processed_news / f"{stem}_article_company_links_clean.csv",
    )
    event_path = resolve_path(
        project_root,
        args.events_csv or processed_news / event_filename,
    )
    event_link_path = resolve_path(
        project_root,
        args.event_links_csv or processed_news / event_link_filename,
    )
    event_mentions_path = resolve_path(
        project_root,
        args.event_mentions_csv
        or processed_news / f"{stem}_event_mentions.csv",
    )
    market_path = resolve_path(
        project_root,
        args.market_windows_csv or processed_news / f"{stem}_event_market_windows.csv",
    )
    configured_output_directory = config.get("neo4j_import", {}).get(
        "output_directory", "data/neo4j/import"
    )
    output_directory = resolve_path(
        project_root, args.output_directory or Path(configured_output_directory)
    )

    companies_frame = read_csv(company_path, "Selected-company table")
    articles_frame = read_csv(article_path, "Clean article table")
    article_links_frame = read_csv(article_link_path, "Clean article-company table")
    events_frame = read_csv(event_path, "Event candidate table")
    event_links_frame = read_csv(event_link_path, "Event-company table")
    event_mentions_frame = (
        read_csv(event_mentions_path, "Event mention provenance table")
        if use_deduplicated_inputs
        else pd.DataFrame()
    )
    market_frame = read_csv(market_path, "Event-market-window table")

    require_columns(companies_frame, COMPANY_COLUMNS, "Selected-company table")
    require_columns(articles_frame, ARTICLE_COLUMNS, "Clean article table")
    require_columns(
        article_links_frame, ARTICLE_LINK_COLUMNS, "Clean article-company table"
    )
    require_columns(events_frame, EVENT_COLUMNS, "Event candidate table")
    require_columns(event_links_frame, EVENT_LINK_COLUMNS, "Event-company table")
    if use_deduplicated_inputs:
        require_columns(
            event_mentions_frame,
            EVENT_MENTION_COLUMNS,
            "Event mention provenance table",
        )
    require_columns(market_frame, MARKET_WINDOW_COLUMNS, "Event-market-window table")

    analysis_ready_mask = articles_frame["analysis_ready"].map(parse_bool)
    accepted_article_link_mask = article_links_frame["accepted_for_analysis"].map(
        parse_bool
    )
    if args.include_not_recommended:
        selected_events_frame = events_frame[
            events_frame["article_id"].isin(
                set(articles_frame.loc[analysis_ready_mask, "article_id"])
            )
        ].copy()
        selected_event_links_frame = event_links_frame[
            event_links_frame["event_id"].isin(set(selected_events_frame["event_id"]))
        ].copy()
        selection_policy = "all_analysis_ready_candidates"
    else:
        selected_events_frame = events_frame[
            events_frame["recommended_for_graph"].map(parse_bool)
        ].copy()
        selected_event_links_frame = event_links_frame[
            event_links_frame["recommended_for_graph"].map(parse_bool)
            & event_links_frame["event_id"].isin(
                set(selected_events_frame["event_id"])
            )
        ].copy()
        selection_policy = (
            "automatic_recommended_canonical_events"
            if use_deduplicated_inputs
            else "automatic_recommended_for_graph"
        )

    selected_event_ids = set(selected_events_frame["event_id"])
    if use_deduplicated_inputs:
        selected_event_mentions_frame = event_mentions_frame[
            event_mentions_frame["canonical_event_id"].isin(selected_event_ids)
        ].copy()
        selected_article_ids = set(selected_event_mentions_frame["article_id"])
    else:
        selected_event_mentions_frame = pd.DataFrame()
        selected_article_ids = set(selected_events_frame["article_id"])
    selected_company_ids = set(companies_frame["company_id"])
    selected_event_company_pairs = set(
        zip(
            selected_event_links_frame["event_id"],
            selected_event_links_frame["company_id"],
            strict=False,
        )
    )

    selected_articles_frame = articles_frame[
        analysis_ready_mask & articles_frame["article_id"].isin(selected_article_ids)
    ].copy()
    selected_article_links_frame = article_links_frame[
        accepted_article_link_mask
        & article_links_frame["article_id"].isin(selected_article_ids)
        & article_links_frame["company_id"].isin(selected_company_ids)
    ].copy()
    selected_market_frame = market_frame[
        market_frame.apply(
            lambda row: (row["event_id"], row["company_id"])
            in selected_event_company_pairs,
            axis=1,
        )
    ].copy()

    missing_articles = selected_article_ids - set(selected_articles_frame["article_id"])
    if missing_articles:
        raise ValueError(
            "Selected events reference missing analysis-ready articles: "
            + ", ".join(sorted(missing_articles)[:10])
        )
    if use_deduplicated_inputs:
        missing_canonical_events = selected_event_ids - set(
            selected_event_mentions_frame["canonical_event_id"]
        )
        if missing_canonical_events:
            raise ValueError(
                "Canonical Events have no source mention provenance: "
                + ", ".join(sorted(missing_canonical_events)[:10])
            )
    missing_event_companies = set(selected_event_links_frame["company_id"]) - selected_company_ids
    if missing_event_companies:
        raise ValueError(
            "Selected event links reference companies outside selected_companies.csv: "
            + ", ".join(sorted(missing_event_companies))
        )

    company_rows: list[dict[str, str]] = []
    sector_by_name: dict[str, str] = {}
    industry_by_name: dict[str, str] = {}
    asset_by_company: dict[str, str] = {}
    company_industry_rows: list[dict[str, str]] = []
    industry_sector_pairs: set[tuple[str, str]] = set()
    company_asset_rows: list[dict[str, str]] = []
    asset_rows: list[dict[str, str]] = []

    ordered_companies = companies_frame.sort_values(
        "sample_order", key=lambda values: pd.to_numeric(values, errors="coerce")
    )
    for _, row in ordered_companies.iterrows():
        company_id = record(row, "company_id")
        sector_name = record(row, "sector")
        industry_name = record(row, "industry")
        symbol = record(row, "twelve_data_symbol")
        exchange = record(row, "twelve_data_exchange")
        sector_key = sector_by_name.setdefault(
            sector_name, dimension_id("SEC", sector_name)
        )
        industry_key = industry_by_name.setdefault(
            industry_name, dimension_id("IND", industry_name)
        )
        asset_key = asset_id(symbol, exchange)
        asset_by_company[company_id] = asset_key
        company_rows.append(
            {
                "company_id": company_id,
                "company_name": record(row, "company_name"),
                "aliases": record(row, "aliases"),
                "country": record(row, "country"),
                "sample_order": integer_text(row.get("sample_order", "")),
                "source_rank": integer_text(row.get("rank", "")),
                "market_cap_usd": rounded_integer_text(
                    row.get("market_cap_usd", "")
                ),
                "ranking_snapshot_date": record(row, "ranking_snapshot_date"),
            }
        )
        asset_rows.append(
            {
                "asset_id": asset_key,
                "symbol": symbol,
                "exchange": exchange,
                "asset_type": "equity",
                "data_source": "Twelve Data",
            }
        )
        company_asset_rows.append(
            {"company_id": company_id, "asset_id": asset_key}
        )
        company_industry_rows.append(
            {"company_id": company_id, "industry_id": industry_key}
        )
        industry_sector_pairs.add((industry_key, sector_key))

    sector_rows = [
        {"sector_id": sector_id, "sector_name": sector_name}
        for sector_name, sector_id in sorted(sector_by_name.items())
    ]
    industry_rows = [
        {"industry_id": industry_id, "industry_name": industry_name}
        for industry_name, industry_id in sorted(industry_by_name.items())
    ]
    industry_sector_rows = [
        {"industry_id": industry_id, "sector_id": sector_id}
        for industry_id, sector_id in sorted(industry_sector_pairs)
    ]

    article_rows: list[dict[str, str]] = []
    for _, row in selected_articles_frame.sort_values("article_id").iterrows():
        article_rows.append(
            {
                "article_id": record(row, "article_id"),
                "title": record(row, "headline"),
                "publication_timestamp": record(row, "publication_date"),
                "section_id": record(row, "section_id"),
                "section_name": record(row, "section_name"),
                "web_url": record(row, "web_url"),
                "short_url": record(row, "short_url"),
                "byline": record(row, "byline"),
                "wordcount": integer_text(row.get("wordcount", "")),
                "trail_text": record(row, "trail_text"),
                "body_text": record(row, "body_text"),
                "tag_ids": record(row, "tag_ids"),
                "tag_titles": record(row, "tag_titles"),
                "source": "The Guardian",
            }
        )

    event_rows: list[dict[str, str]] = []
    article_event_rows: list[dict[str, str]] = []
    for _, row in selected_events_frame.sort_values("event_id").iterrows():
        event_id = record(row, "event_id")
        article_id = record(row, "article_id")
        event_rows.append(
            {
                "event_id": event_id,
                "event_date": record(row, "event_date"),
                "publication_timestamp": record(row, "publication_timestamp"),
                "event_title": record(row, "event_title"),
                "event_summary": record(row, "event_summary"),
                "evidence_span": record(row, "evidence_span"),
                "evidence_source": record(row, "evidence_source"),
                "evidence_position": integer_text(
                    row.get("evidence_position", "")
                ),
                "event_granularity": record(row, "event_granularity"),
                "event_span_hash": record(row, "event_span_hash"),
                "source_headline": record(row, "source_headline"),
                "event_type": record(row, "event_type"),
                "rule_event_type": record(row, "rule_event_type")
                or record(row, "event_type"),
                "event_score": integer_text(row.get("event_score", "")),
                "classification_confidence": record(
                    row, "classification_confidence"
                ),
                "matched_event_keywords": record(row, "matched_event_keywords"),
                "secondary_event_types": record(row, "secondary_event_types"),
                "event_type_scores": record(row, "event_type_scores"),
                "classification_tie": bool_text(row.get("classification_tie", "")),
                "nlp_event_type": record(row, "nlp_event_type"),
                "nlp_event_score": record(row, "nlp_event_score"),
                "nlp_event_scores": record(row, "nlp_event_scores"),
                "nlp_model_name": record(row, "nlp_model_name"),
                "nlp_model_revision": record(row, "nlp_model_revision"),
                "hybrid_decision_reason": record(row, "hybrid_decision_reason"),
                "section_name": record(row, "section_name"),
                "source_method": (
                    "canonical_cross_article_event_pipeline"
                    if use_deduplicated_inputs
                    else "hybrid_rule_nli_event_pipeline"
                    if use_nlp_inputs
                    else "rule_based_guardian_event_pipeline"
                ),
                "web_url": record(row, "web_url"),
                "representative_event_id": record(
                    row, "representative_event_id"
                )
                or event_id,
                "source_event_count": integer_text(
                    row.get("source_event_count", "1")
                ),
                "source_article_count": integer_text(
                    row.get("source_article_count", "1")
                ),
                "source_event_ids": record(row, "source_event_ids"),
                "source_article_ids": record(row, "source_article_ids"),
                "first_publication_timestamp": record(
                    row, "first_publication_timestamp"
                )
                or record(row, "publication_timestamp"),
                "last_publication_timestamp": record(
                    row, "last_publication_timestamp"
                )
                or record(row, "publication_timestamp"),
                "deduplication_method": record(row, "deduplication_method")
                or "singleton",
                "deduplication_min_similarity": record(
                    row, "deduplication_min_similarity"
                )
                or "1",
                "deduplication_max_date_span_days": integer_text(
                    row.get("deduplication_max_date_span_days", "0")
                ),
            }
        )
        if not use_deduplicated_inputs:
            article_event_rows.append(
                {
                    "article_id": article_id,
                    "event_id": event_id,
                    "source_event_id": event_id,
                    "source_event_date": record(row, "event_date"),
                    "source_publication_timestamp": record(
                        row, "publication_timestamp"
                    ),
                    "source_event_title": record(row, "event_title"),
                    "source_evidence_span": record(row, "evidence_span"),
                    "source_evidence_source": record(row, "evidence_source"),
                    "source_event_granularity": record(
                        row, "event_granularity"
                    ),
                    "similarity_to_representative": "1",
                    "is_representative": "true",
                    "deduplication_method": "singleton",
                }
            )

    if use_deduplicated_inputs:
        for _, row in selected_event_mentions_frame.sort_values(
            ["canonical_event_id", "article_id", "source_event_id"]
        ).iterrows():
            article_event_rows.append(
                {
                    "article_id": record(row, "article_id"),
                    "event_id": record(row, "canonical_event_id"),
                    "source_event_id": record(row, "source_event_id"),
                    "source_event_date": record(row, "event_date"),
                    "source_publication_timestamp": record(
                        row, "publication_timestamp"
                    ),
                    "source_event_title": record(row, "event_title"),
                    "source_evidence_span": record(row, "evidence_span"),
                    "source_evidence_source": record(row, "evidence_source"),
                    "source_event_granularity": record(
                        row, "event_granularity"
                    ),
                    "similarity_to_representative": record(
                        row, "similarity_to_representative"
                    ),
                    "is_representative": bool_text(
                        row.get("is_representative", "")
                    ),
                    "deduplication_method": record(
                        row, "deduplication_method"
                    ),
                }
            )

    article_company_rows: list[dict[str, str]] = []
    for _, row in selected_article_links_frame.sort_values(
        ["article_id", "company_id"]
    ).iterrows():
        article_company_rows.append(
            {
                "article_id": record(row, "article_id"),
                "company_id": record(row, "company_id"),
                "link_status": record(row, "link_status"),
                "evidence_type": record(row, "evidence_type"),
                "query_returned": bool_text(row.get("query_returned", "")),
                "matched_core_aliases": record(row, "matched_core_aliases"),
                "matched_product_aliases": record(row, "matched_product_aliases"),
            }
        )

    event_company_rows: list[dict[str, str]] = []
    for _, row in selected_event_links_frame.sort_values(
        ["event_id", "company_id"]
    ).iterrows():
        event_company_rows.append(
            {
                "event_id": record(row, "event_id"),
                "company_id": record(row, "company_id"),
                "link_status": record(row, "link_status"),
                "link_confidence": record(row, "link_confidence"),
                "relationship_focus_score": integer_text(
                    row.get("relationship_focus_score", "")
                ),
                "evidence_sentence": record(row, "nlp_evidence_sentence")
                or record(row, "evidence_sentence"),
                "rule_evidence_sentence": record(row, "evidence_sentence"),
                "headline_aliases": record(row, "headline_aliases"),
                "trail_aliases": record(row, "trail_aliases"),
                "event_keyword_same_sentence": record(
                    row, "event_keyword_same_sentence"
                ),
                "nlp_raw_relationship_label": record(
                    row, "nlp_raw_relationship_label"
                ),
                "nlp_relationship_label": record(row, "nlp_relationship_label"),
                "nlp_relationship_score": record(row, "nlp_relationship_score"),
                "nlp_positive_probability": record(
                    row, "nlp_positive_probability"
                ),
                "nlp_relationship_scores": record(
                    row, "nlp_relationship_scores"
                ),
                "nlp_model_name": record(row, "nlp_model_name"),
                "nlp_model_revision": record(row, "nlp_model_revision"),
                "nlp_role_calibration_reason": record(
                    row, "nlp_role_calibration_reason"
                ),
                "hybrid_decision_reason": record(
                    row, "hybrid_decision_reason"
                ),
                "source_method": (
                    "canonical_cross_article_relationship_aggregation"
                    if use_deduplicated_inputs
                    else "hybrid_rule_nli_evidence_scoring"
                    if use_nlp_inputs
                    else "rule_based_evidence_scoring"
                ),
                "source_event_id": record(row, "source_event_id")
                or record(row, "event_id"),
                "source_article_id": record(row, "source_article_id")
                or record(row, "article_id"),
                "relationship_publication_timestamp": record(
                    row, "relationship_publication_timestamp"
                ),
                "source_relationship_count": integer_text(
                    row.get("source_relationship_count", "1")
                ),
                "source_event_ids": record(row, "source_event_ids"),
                "source_article_ids": record(row, "source_article_ids"),
                "deduplicated_relationship": bool_text(
                    row.get("deduplicated_relationship", "")
                ),
            }
        )

    market_rows: list[dict[str, str]] = []
    event_market_rows: list[dict[str, str]] = []
    asset_market_rows: list[dict[str, str]] = []
    for _, row in selected_market_frame.sort_values(
        ["event_id", "company_id", "window_trading_days"]
    ).iterrows():
        company_id = record(row, "company_id")
        observation_id = market_observation_id(
            record(row, "market_link_id"), record(row, "window_trading_days")
        )
        expected_asset_id = asset_by_company[company_id]
        source_asset_id = asset_id(record(row, "symbol"), record(row, "exchange"))
        if source_asset_id != expected_asset_id:
            raise ValueError(
                f"Market window {observation_id} maps to {source_asset_id}, "
                f"but company {company_id} maps to {expected_asset_id}."
            )
        market_rows.append(
            {
                "market_observation_id": observation_id,
                "market_link_id": record(row, "market_link_id"),
                "publication_timestamp_utc": record(
                    row, "publication_timestamp_utc"
                ),
                "publication_timestamp_market_tz": record(
                    row, "publication_timestamp_market_tz"
                ),
                "anchor_rule": record(row, "anchor_rule"),
                "baseline_date": record(row, "baseline_date"),
                "baseline_close": record(row, "baseline_close"),
                "window_trading_days": integer_text(
                    row.get("window_trading_days", "")
                ),
                "window_end_date": record(row, "window_end_date"),
                "window_end_close": record(row, "window_end_close"),
                "cumulative_return": record(row, "cumulative_return"),
                "data_source": record(row, "data_source"),
                "causal_claim": bool_text(row.get("causal_claim", "")),
            }
        )
        event_market_rows.append(
            {
                "event_id": record(row, "event_id"),
                "market_observation_id": observation_id,
            }
        )
        asset_market_rows.append(
            {
                "asset_id": expected_asset_id,
                "market_observation_id": observation_id,
            }
        )

    node_groups = {
        "companies": (company_rows, ("company_id",)),
        "sectors": (sector_rows, ("sector_id",)),
        "industries": (industry_rows, ("industry_id",)),
        "assets": (asset_rows, ("asset_id",)),
        "articles": (article_rows, ("article_id",)),
        "events": (event_rows, ("event_id",)),
        "market_observations": (market_rows, ("market_observation_id",)),
    }
    relationship_groups = {
        "company_issues_asset": (
            company_asset_rows,
            ("company_id", "asset_id"),
        ),
        "company_belongs_to_industry": (
            company_industry_rows,
            ("company_id", "industry_id"),
        ),
        "industry_part_of_sector": (
            industry_sector_rows,
            ("industry_id", "sector_id"),
        ),
        "article_reports_event": (
            article_event_rows,
            ("article_id", "event_id"),
        ),
        "article_mentions_company": (
            article_company_rows,
            ("article_id", "company_id"),
        ),
        "event_potentially_affects_company": (
            event_company_rows,
            ("event_id", "company_id"),
        ),
        "event_has_market_observation": (
            event_market_rows,
            ("event_id", "market_observation_id"),
        ),
        "asset_has_market_observation": (
            asset_market_rows,
            ("asset_id", "market_observation_id"),
        ),
    }
    for label, (rows, key_fields) in {**node_groups, **relationship_groups}.items():
        ensure_unique(rows, key_fields, label)
        ensure_nonblank(rows, key_fields, label)

    company_ids = {row["company_id"] for row in company_rows}
    sector_ids = {row["sector_id"] for row in sector_rows}
    industry_ids = {row["industry_id"] for row in industry_rows}
    asset_ids = {row["asset_id"] for row in asset_rows}
    article_ids = {row["article_id"] for row in article_rows}
    event_ids = {row["event_id"] for row in event_rows}
    observation_ids = {row["market_observation_id"] for row in market_rows}

    for rows, field, valid, label in [
        (company_asset_rows, "company_id", company_ids, "company_issues_asset"),
        (company_asset_rows, "asset_id", asset_ids, "company_issues_asset"),
        (
            company_industry_rows,
            "company_id",
            company_ids,
            "company_belongs_to_industry",
        ),
        (
            company_industry_rows,
            "industry_id",
            industry_ids,
            "company_belongs_to_industry",
        ),
        (
            industry_sector_rows,
            "industry_id",
            industry_ids,
            "industry_part_of_sector",
        ),
        (
            industry_sector_rows,
            "sector_id",
            sector_ids,
            "industry_part_of_sector",
        ),
        (article_event_rows, "article_id", article_ids, "article_reports_event"),
        (article_event_rows, "event_id", event_ids, "article_reports_event"),
        (
            article_company_rows,
            "article_id",
            article_ids,
            "article_mentions_company",
        ),
        (
            article_company_rows,
            "company_id",
            company_ids,
            "article_mentions_company",
        ),
        (
            event_company_rows,
            "event_id",
            event_ids,
            "event_potentially_affects_company",
        ),
        (
            event_company_rows,
            "company_id",
            company_ids,
            "event_potentially_affects_company",
        ),
        (
            event_market_rows,
            "event_id",
            event_ids,
            "event_has_market_observation",
        ),
        (
            event_market_rows,
            "market_observation_id",
            observation_ids,
            "event_has_market_observation",
        ),
        (
            asset_market_rows,
            "asset_id",
            asset_ids,
            "asset_has_market_observation",
        ),
        (
            asset_market_rows,
            "market_observation_id",
            observation_ids,
            "asset_has_market_observation",
        ),
    ]:
        ensure_references(rows, field, valid, label)

    output_specs = {
        "companies.csv": company_rows,
        "sectors.csv": sector_rows,
        "industries.csv": industry_rows,
        "assets.csv": asset_rows,
        "articles.csv": article_rows,
        "events.csv": event_rows,
        "market_observations.csv": market_rows,
        "company_issues_asset.csv": company_asset_rows,
        "company_belongs_to_industry.csv": company_industry_rows,
        "industry_part_of_sector.csv": industry_sector_rows,
        "article_reports_event.csv": article_event_rows,
        "article_mentions_company.csv": article_company_rows,
        "event_potentially_affects_company.csv": event_company_rows,
        "event_has_market_observation.csv": event_market_rows,
        "asset_has_market_observation.csv": asset_market_rows,
    }
    for filename, rows in output_specs.items():
        if not rows:
            raise ValueError(f"Refusing to write an empty graph component: {filename}")
        write_csv(output_directory / filename, rows, list(rows[0]))

    output_directory.mkdir(parents=True, exist_ok=True)
    cypher_path = output_directory / "neo4j_load.cypher"
    cypher_path.write_text(build_cypher(), encoding="utf-8")

    report_rows = [
        {
            "section": "policy",
            "metric": "selection_policy",
            "value": selection_policy,
            "status": "pass",
            "details": "recommended_for_graph is the automatic import gate",
        },
        {
            "section": "source",
            "metric": "candidate_events",
            "value": str(len(events_frame)),
            "status": "info",
            "details": str(event_path),
        },
        {
            "section": "source",
            "metric": "candidate_event_company_links",
            "value": str(len(event_links_frame)),
            "status": "info",
            "details": str(event_link_path),
        },
    ]
    if use_deduplicated_inputs:
        report_rows.append(
            {
                "section": "source",
                "metric": "event_mention_provenance",
                "value": str(len(event_mentions_frame)),
                "status": "info",
                "details": str(event_mentions_path),
            }
        )
    report_rows.extend(
        {
            "section": "nodes",
            "metric": label,
            "value": str(len(rows)),
            "status": "pass",
            "details": "unique IDs and required fields validated",
        }
        for label, (rows, _) in node_groups.items()
    )
    report_rows.extend(
        {
            "section": "relationships",
            "metric": label,
            "value": str(len(rows)),
            "status": "pass",
            "details": "unique endpoints and referential integrity validated",
        }
        for label, (rows, _) in relationship_groups.items()
    )
    report_rows.append(
        {
            "section": "validation",
            "metric": "graph_package",
            "value": "ready",
            "status": "pass",
            "details": "all node and relationship checks completed",
        }
    )
    write_csv(
        output_directory / "kg_import_report.csv",
        report_rows,
        ["section", "metric", "value", "status", "details"],
    )

    print(f"Selection policy: {selection_policy}")
    print(f"Neo4j import package: {output_directory}")
    for label, (rows, _) in node_groups.items():
        print(f"  node {label}: {len(rows)}")
    for label, (rows, _) in relationship_groups.items():
        print(f"  relationship {label}: {len(rows)}")
    print(f"Cypher loader: {cypher_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
