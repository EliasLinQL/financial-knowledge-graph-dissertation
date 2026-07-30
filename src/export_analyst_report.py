"""Export a read-only, analyst-facing data package from the Neo4j graph.

The package separates canonical events, source evidence and descriptive market
windows to preserve traceability and distinguish market context from causal
inference.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

try:
    from src.query_kg import (
        COMPANY_EVENT_SUMMARY_QUERY,
        build_validation_report,
        connection_settings,
        execute_read,
        load_config,
        resolve_path,
        validate_iso_date,
    )
except ModuleNotFoundError:
    from query_kg import (  # type: ignore[no-redef]
        COMPANY_EVENT_SUMMARY_QUERY,
        build_validation_report,
        connection_settings,
        execute_read,
        load_config,
        resolve_path,
        validate_iso_date,
    )


COMPANIES_QUERY = """
MATCH (c:Company)
WHERE $company_id IS NULL OR c.company_id = $company_id
OPTIONAL MATCH (c)-[:ISSUES]->(asset:Asset)
RETURN c.company_id AS company_id,
       c.name AS company,
       c.country AS country,
       c.source_rank AS source_rank,
       c.market_cap_usd AS market_cap_usd,
       c.ranking_snapshot_date AS ranking_snapshot_date,
       head(collect(DISTINCT asset.symbol)) AS symbol
ORDER BY c.source_rank, c.company_id
"""


CANONICAL_EVENTS_QUERY = """
MATCH (e:Event)-[impact:POTENTIALLY_AFFECTS]->(c:Company)
WHERE ($company_id IS NULL OR c.company_id = $company_id)
  AND ($event_type IS NULL OR e.event_type = $event_type)
  AND ($start_date IS NULL OR e.event_date >= date($start_date))
  AND ($end_date IS NULL OR e.event_date <= date($end_date))
  AND (
      $minimum_nlp_probability IS NULL
      OR impact.nlp_positive_probability >= $minimum_nlp_probability
  )
OPTIONAL MATCH (source_article:Article {article_id: impact.source_article_id})
RETURN c.company_id AS company_id,
       c.name AS company,
       e.event_id AS event_id,
       e.event_date AS event_date,
       e.event_type AS event_type,
       e.title AS event_title,
       e.summary AS event_summary,
       impact.evidence_sentence AS relationship_evidence,
       impact.nlp_relationship_label AS nlp_relationship_label,
       impact.nlp_positive_probability AS nlp_positive_probability,
       impact.relationship_focus_score AS relationship_focus_score,
       impact.hybrid_decision_reason AS hybrid_decision_reason,
       e.classification_confidence AS classification_confidence,
       e.source_event_count AS source_event_count,
       e.source_article_count AS source_article_count,
       e.deduplication_method AS deduplication_method,
       impact.source_event_id AS relationship_source_event_id,
       impact.source_article_id AS relationship_source_article_id,
       source_article.web_url AS relationship_source_url,
       impact.nlp_model_name AS nlp_model_name,
       impact.nlp_model_revision AS nlp_model_revision
ORDER BY event_date DESC, company, event_id
"""


SOURCE_EVIDENCE_QUERY = """
MATCH (e:Event)-[impact:POTENTIALLY_AFFECTS]->(c:Company)
MATCH (a:Article)-[report:REPORTS]->(e)
WHERE ($company_id IS NULL OR c.company_id = $company_id)
  AND ($event_type IS NULL OR e.event_type = $event_type)
  AND ($start_date IS NULL OR e.event_date >= date($start_date))
  AND ($end_date IS NULL OR e.event_date <= date($end_date))
  AND (
      $minimum_nlp_probability IS NULL
      OR impact.nlp_positive_probability >= $minimum_nlp_probability
  )
RETURN c.company_id AS company_id,
       c.name AS company,
       e.event_id AS event_id,
       e.event_date AS canonical_event_date,
       e.event_type AS event_type,
       e.title AS canonical_event_title,
       report.source_event_id AS source_event_id,
       report.source_event_date AS source_event_date,
       report.source_event_title AS source_event_title,
       report.source_evidence_span AS source_evidence_span,
       report.source_evidence_source AS source_evidence_source,
       report.similarity_to_representative AS similarity_to_representative,
       report.is_representative AS is_representative,
       report.deduplication_method AS deduplication_method,
       a.article_id AS article_id,
       a.title AS article_title,
       a.publication_timestamp AS publication_timestamp,
       a.section_name AS section_name,
       a.web_url AS source_url,
       impact.source_event_id = report.source_event_id
           AS is_relationship_source
ORDER BY canonical_event_date DESC, company, event_id,
         is_representative DESC, publication_timestamp
"""


MARKET_CONTEXT_QUERY = """
MATCH (e:Event)-[impact:POTENTIALLY_AFFECTS]->(c:Company)-[:ISSUES]->(asset:Asset)
MATCH (e)-[:HAS_MARKET_OBSERVATION]->(observation:MarketObservation)
MATCH (asset)-[:HAS_MARKET_OBSERVATION]->(observation)
WHERE ($company_id IS NULL OR c.company_id = $company_id)
  AND ($event_type IS NULL OR e.event_type = $event_type)
  AND ($start_date IS NULL OR e.event_date >= date($start_date))
  AND ($end_date IS NULL OR e.event_date <= date($end_date))
  AND (
      $minimum_nlp_probability IS NULL
      OR impact.nlp_positive_probability >= $minimum_nlp_probability
  )
RETURN c.company_id AS company_id,
       c.name AS company,
       e.event_id AS event_id,
       e.event_date AS event_date,
       e.event_type AS event_type,
       asset.symbol AS symbol,
       observation.market_observation_id AS market_observation_id,
       observation.window_trading_days AS window_days,
       observation.baseline_date AS baseline_date,
       observation.window_end_date AS window_end_date,
       observation.baseline_close AS baseline_close,
       observation.window_end_close AS window_end_close,
       observation.cumulative_return AS cumulative_return,
       observation.anchor_rule AS anchor_rule,
       observation.data_source AS data_source,
       coalesce(observation.causal_claim, false) AS causal_claim
ORDER BY event_date DESC, company, event_id, window_days
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export a read-only analyst report package from the canonical Neo4j "
            "financial knowledge graph."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the project YAML configuration.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("outputs/analyst_report"),
        help="Directory for JSON and bilingual Markdown outputs.",
    )
    parser.add_argument("--company-id", help="Optional company ID, for example C007.")
    parser.add_argument(
        "--event-type",
        help="Optional event type, for example regulatory_event.",
    )
    parser.add_argument("--start-date", help="Optional inclusive ISO date.")
    parser.add_argument("--end-date", help="Optional inclusive ISO date.")
    parser.add_argument(
        "--minimum-nlp-probability",
        type=float,
        help="Optional 0-1 relationship-probability filter.",
    )
    return parser.parse_args()


def validate_probability(value: float | None) -> float | None:
    if value is None:
        return None
    if not 0.0 <= value <= 1.0:
        raise ValueError("--minimum-nlp-probability must be between 0 and 1.")
    return value


def json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        return iso_format()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {str(key): json_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def filtered_query_parameters(args: argparse.Namespace) -> dict[str, Any]:
    start_date = validate_iso_date(args.start_date, "--start-date")
    end_date = validate_iso_date(args.end_date, "--end-date")
    if start_date and end_date and start_date > end_date:
        raise ValueError("--start-date cannot be later than --end-date.")
    return {
        "company_id": args.company_id or None,
        "event_type": args.event_type or None,
        "start_date": start_date,
        "end_date": end_date,
        "minimum_nlp_probability": validate_probability(
            args.minimum_nlp_probability
        ),
    }


def event_counts_by_company(
    companies: pd.DataFrame,
    events: pd.DataFrame,
) -> pd.DataFrame:
    if events.empty:
        counts = pd.DataFrame(columns=["company_id", "event_count"])
    else:
        counts = (
            events.groupby("company_id", as_index=False)
            .size()
            .rename(columns={"size": "event_count"})
        )
    summary = companies.merge(counts, on="company_id", how="left")
    summary["event_count"] = summary["event_count"].fillna(0).astype(int)
    return summary.sort_values(
        ["event_count", "source_rank", "company_id"],
        ascending=[False, True, True],
        na_position="last",
    )


def build_metadata(
    *,
    settings: Any,
    parameters: dict[str, Any],
    companies: pd.DataFrame,
    events: pd.DataFrame,
    sources: pd.DataFrame,
    market: pd.DataFrame,
    validations: pd.DataFrame,
) -> dict[str, Any]:
    unique_event_count = (
        int(events["event_id"].nunique()) if "event_id" in events else 0
    )
    multi_source_count = 0
    if not events.empty and "source_event_count" in events:
        multi_source_count = int(
            events.loc[
                pd.to_numeric(events["source_event_count"], errors="coerce") > 1,
                "event_id",
            ].nunique()
        )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_uri": settings.uri,
        "database": settings.database,
        "filters": parameters,
        "counts": {
            "companies": int(len(companies)),
            "companies_with_events": int(events["company_id"].nunique())
            if not events.empty
            else 0,
            "canonical_events": unique_event_count,
            "event_company_links": int(len(events)),
            "source_articles": int(sources["article_id"].nunique())
            if not sources.empty
            else 0,
            "source_evidence_rows": int(len(sources)),
            "multi_source_events": multi_source_count,
            "market_windows": int(len(market)),
            "validation_failures": int(
                (validations["status"] == "FAIL").sum()
            )
            if not validations.empty
            else 0,
        },
        "model": {
            "name": (
                str(events["nlp_model_name"].dropna().iloc[0])
                if not events.empty and events["nlp_model_name"].notna().any()
                else ""
            ),
            "revision": (
                str(events["nlp_model_revision"].dropna().iloc[0])
                if not events.empty and events["nlp_model_revision"].notna().any()
                else ""
            ),
        },
        "interpretation": {
            "market_returns_are_causal": False,
            "market_context_note": (
                "1/3/7-trading-day cumulative returns are descriptive context "
                "after publication, not estimates of causal impact."
            ),
        },
    }


def render_filters(filters: dict[str, Any], language: str) -> str:
    labels = {
        "zh": {
            "company_id": "公司",
            "event_type": "事件类型",
            "start_date": "开始日期",
            "end_date": "结束日期",
            "minimum_nlp_probability": "最低 NLP 概率",
            "all": "全部",
        },
        "en": {
            "company_id": "Company",
            "event_type": "Event type",
            "start_date": "Start date",
            "end_date": "End date",
            "minimum_nlp_probability": "Minimum NLP probability",
            "all": "All",
        },
    }[language]
    return "; ".join(
        f"{labels[key]}: {value if value is not None else labels['all']}"
        for key, value in filters.items()
    )


def markdown_briefing(
    *,
    language: str,
    metadata: dict[str, Any],
    company_summary: pd.DataFrame,
    events: pd.DataFrame,
    market: pd.DataFrame,
) -> str:
    counts = metadata["counts"]
    top_companies = company_summary.head(10)
    event_type_counts = (
        events.groupby("event_type").size().sort_values(ascending=False)
        if not events.empty
        else pd.Series(dtype=int)
    )
    market_windows = (
        market.groupby("window_days")["cumulative_return"]
        .agg(["count", "mean", "median"])
        .reset_index()
        if not market.empty
        else pd.DataFrame()
    )

    if language == "zh":
        lines = [
            "# 金融知识图谱分析简报",
            "",
            f"- 生成时间（UTC）：{metadata['generated_at_utc']}",
            f"- 查询范围：{render_filters(metadata['filters'], 'zh')}",
            f"- 公司覆盖：{counts['companies_with_events']}/{counts['companies']}",
            f"- 规范化事件：{counts['canonical_events']}",
            f"- 事件—公司关系：{counts['event_company_links']}",
            f"- 去重后来源文章：{counts['source_articles']}",
            f"- 多来源事件：{counts['multi_source_events']}",
            f"- 图谱验证失败项：{counts['validation_failures']}",
            "",
            "## 公司事件覆盖（前 10）",
            "",
            "| 公司 | 事件—公司关系数 |",
            "|---|---:|",
        ]
        lines.extend(
            f"| {row.company} | {int(row.event_count)} |"
            for row in top_companies.itertuples()
        )
        lines.extend(["", "## 事件类型", "", "| 类型 | 数量 |", "|---|---:|"])
        lines.extend(f"| {name} | {int(value)} |" for name, value in event_type_counts.items())
        lines.extend(
            [
                "",
                "## 市场窗口背景",
                "",
                "| 交易日窗口 | 观察数 | 平均累计收益 | 中位累计收益 |",
                "|---:|---:|---:|---:|",
            ]
        )
        lines.extend(
            (
                f"| {int(row.window_days)} | {int(row['count'])} | "
                f"{row['mean']:.2%} | {row['median']:.2%} |"
            )
            for _, row in market_windows.iterrows()
        )
        lines.extend(
            [
                "",
                "> 重要说明：市场收益仅用于描述新闻发布时间之后的市场背景，"
                "不代表事件导致了该收益，也不构成投资建议。",
                "",
                "证据句、来源文章、NLP 概率和去重映射均被保留，"
                "用于审计与追溯。",
            ]
        )
        return "\n".join(lines) + "\n"

    lines = [
        "# Financial Knowledge Graph Analyst Briefing",
        "",
        f"- Generated at (UTC): {metadata['generated_at_utc']}",
        f"- Scope: {render_filters(metadata['filters'], 'en')}",
        f"- Company coverage: {counts['companies_with_events']}/{counts['companies']}",
        f"- Canonical events: {counts['canonical_events']}",
        f"- Event-company relationships: {counts['event_company_links']}",
        f"- Deduplicated source articles: {counts['source_articles']}",
        f"- Multi-source events: {counts['multi_source_events']}",
        f"- Failed graph checks: {counts['validation_failures']}",
        "",
        "## Company event coverage (top 10)",
        "",
        "| Company | Event-company relationships |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {row.company} | {int(row.event_count)} |"
        for row in top_companies.itertuples()
    )
    lines.extend(["", "## Event types", "", "| Type | Count |", "|---|---:|"])
    lines.extend(f"| {name} | {int(value)} |" for name, value in event_type_counts.items())
    lines.extend(
        [
            "",
            "## Descriptive market windows",
            "",
            "| Trading-day window | Observations | Average return | Median return |",
            "|---:|---:|---:|---:|",
        ]
    )
    lines.extend(
        (
            f"| {int(row.window_days)} | {int(row['count'])} | "
            f"{row['mean']:.2%} | {row['median']:.2%} |"
        )
        for _, row in market_windows.iterrows()
    )
    lines.extend(
        [
            "",
            "> Important: returns are descriptive post-publication market context. "
            "They do not establish that an event caused a return and are not "
            "investment advice.",
            "",
            "Evidence sentences, source articles, NLP probabilities and "
            "deduplication mappings remain available for audit and traceability.",
        ]
    )
    return "\n".join(lines) + "\n"


def run() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    project_root = config_path.parent.parent
    output_directory = resolve_path(project_root, args.output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    load_dotenv(project_root / ".env")

    settings = connection_settings(config)
    password = os.getenv(settings.password_environment_variable, "").strip()
    if not password:
        raise RuntimeError(
            f"{settings.password_environment_variable} is missing. Add it to "
            f"{project_root / '.env'}."
        )
    parameters = filtered_query_parameters(args)
    import_config = config.get("neo4j_import", {})
    if not isinstance(import_config, dict):
        raise ValueError("neo4j_import must be a YAML mapping.")
    import_directory = resolve_path(
        project_root,
        import_config.get("output_directory", "data/neo4j/import"),
    )

    with GraphDatabase.driver(
        settings.uri,
        auth=(settings.user, password),
    ) as driver:
        driver.verify_connectivity()
        companies = execute_read(
            driver,
            settings.database,
            COMPANIES_QUERY,
            {"company_id": parameters["company_id"]},
        )
        events = execute_read(
            driver, settings.database, CANONICAL_EVENTS_QUERY, parameters
        )
        sources = execute_read(
            driver, settings.database, SOURCE_EVIDENCE_QUERY, parameters
        )
        market = execute_read(
            driver, settings.database, MARKET_CONTEXT_QUERY, parameters
        )
        complete_coverage = execute_read(
            driver,
            settings.database,
            COMPANY_EVENT_SUMMARY_QUERY,
            {
                "company_id": None,
                "event_type": None,
                "start_date": None,
                "end_date": None,
                "evidence_limit": 1,
            },
        )
        validations = build_validation_report(
            driver,
            settings.database,
            import_directory,
            complete_coverage,
        )

    company_summary = event_counts_by_company(companies, events)
    metadata = build_metadata(
        settings=settings,
        parameters=parameters,
        companies=companies,
        events=events,
        sources=sources,
        market=market,
        validations=validations,
    )
    payload = {
        "metadata": metadata,
        "companies": frame_records(company_summary),
        "events": frame_records(events),
        "sources": frame_records(sources),
        "market": frame_records(market),
        "validations": frame_records(validations),
    }

    json_path = output_directory / "analyst_report_data.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_directory / "analyst_briefing_cn.md").write_text(
        markdown_briefing(
            language="zh",
            metadata=metadata,
            company_summary=company_summary,
            events=events,
            market=market,
        ),
        encoding="utf-8-sig",
    )
    (output_directory / "analyst_briefing_en.md").write_text(
        markdown_briefing(
            language="en",
            metadata=metadata,
            company_summary=company_summary,
            events=events,
            market=market,
        ),
        encoding="utf-8-sig",
    )

    print(f"Neo4j analyst export completed: {settings.uri} / {settings.database}")
    print(f"Companies: {len(companies)}")
    print(f"Canonical event-company rows: {len(events)}")
    print(f"Source evidence rows: {len(sources)}")
    print(f"Market-context rows: {len(market)}")
    print(f"Output directory: {output_directory}")
    return 0


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
        print(f"Analyst report export failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
