"""Validate Guardian article-company evidence and prepare analysis-ready CSVs."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ARTICLE_REQUIRED_COLUMNS = {
    "article_id",
    "headline",
    "publication_date",
    "web_url",
    "trail_text",
    "body_text",
}
LINK_REQUIRED_COLUMNS = {
    "article_id",
    "company_id",
    "query_returned",
    "text_alias_matched",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Separate company-name evidence, product/platform evidence and "
            "unverified query-only Guardian links."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the project YAML configuration file.",
    )
    parser.add_argument(
        "--mode",
        choices=("test", "full"),
        default="full",
        help="Guardian collection mode whose processed files will be prepared.",
    )
    parser.add_argument(
        "--articles-csv",
        type=Path,
        help="Optional source article CSV; defaults to the configured mode output.",
    )
    parser.add_argument(
        "--links-csv",
        type=Path,
        help="Optional source article-company link CSV.",
    )
    parser.add_argument(
        "--selected-companies",
        type=Path,
        help="Optional selected_companies.csv path.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="Optional output directory; defaults to the configured processed directory.",
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


def parse_bool(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def english_aliases(row: pd.Series) -> list[str]:
    raw_aliases = str(row.get("aliases", "")).split("|")
    candidates = raw_aliases or [str(row["company_name"])]
    aliases: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        alias = " ".join(raw.strip().split())
        if not alias or not alias.isascii() or not any(c.isalpha() for c in alias):
            continue
        key = alias.casefold()
        if key not in seen:
            aliases.append(alias)
            seen.add(key)
    if not aliases:
        aliases.append(str(row["company_name"]).strip())
    return aliases


def configured_aliases(
    company_id: str,
    base_aliases: list[str],
    matching_config: dict[str, Any],
) -> tuple[list[str], list[str], set[str]]:
    excluded = {
        str(value).strip().casefold()
        for value in matching_config.get("excluded_aliases_by_company", {}).get(
            company_id, []
        )
        if str(value).strip()
    }
    product_aliases = [
        str(value).strip()
        for value in matching_config.get("product_aliases_by_company", {}).get(
            company_id, []
        )
        if str(value).strip()
    ]
    product_keys = {value.casefold() for value in product_aliases}
    additional_aliases = [
        str(value).strip()
        for value in matching_config.get("additional_aliases_by_company", {}).get(
            company_id, []
        )
        if str(value).strip()
    ]

    core: list[str] = []
    products: list[str] = []
    seen_core: set[str] = set()
    seen_products: set[str] = set()
    for alias in list(base_aliases) + additional_aliases:
        key = alias.casefold()
        if key in excluded:
            continue
        if key in product_keys:
            if key not in seen_products:
                products.append(alias)
                seen_products.add(key)
        elif key not in seen_core:
            core.append(alias)
            seen_core.add(key)

    strict = {
        str(value).strip().casefold()
        for value in matching_config.get("case_sensitive_aliases_by_company", {}).get(
            company_id, []
        )
        if str(value).strip()
    }
    return core, products, strict


def alias_matches(text: str, aliases: list[str], strict: set[str]) -> list[str]:
    matches: list[str] = []
    for alias in aliases:
        pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"
        flags = 0 if alias.casefold() in strict else re.IGNORECASE
        if re.search(pattern, text, flags=flags):
            matches.append(alias)
    return matches


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {path}. Close this CSV in Excel or another editor and retry."
        ) from exc


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project_root = config_path.parent.parent
    try:
        project = load_yaml(config_path)
        news_config = project["news_data"]
        mode_config = news_config["collection_modes"][args.mode]
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"CONFIGURATION_ERROR: {exc}", file=sys.stderr)
        return 1

    stem = str(mode_config["output_stem"])
    processed_dir = resolve_path(project_root, news_config["processed_output_directory"])
    coverage_config = project.get("company_selection", {}).get(
        "news_coverage", {}
    )
    articles_path = (
        args.articles_csv.resolve()
        if args.articles_csv
        else processed_dir / f"{stem}_articles.csv"
    )
    links_path = (
        args.links_csv.resolve()
        if args.links_csv
        else processed_dir / f"{stem}_article_company_links.csv"
    )
    selected_path = (
        args.selected_companies.resolve()
        if args.selected_companies
        else resolve_path(
            project_root,
            project["outputs"].get(
                "market_eligible_companies",
                project["outputs"]["selected_companies"],
            ),
        )
    )
    output_dir = (
        args.output_directory.resolve()
        if args.output_directory
        else resolve_path(
            project_root,
            coverage_config.get(
                "pool_processed_output_directory",
                news_config["processed_output_directory"],
            ),
        )
    )

    try:
        articles = pd.read_csv(articles_path, dtype=str, keep_default_na=False)
        links = pd.read_csv(links_path, dtype=str, keep_default_na=False)
        companies = pd.read_csv(selected_path, dtype=str, keep_default_na=False)
        require_columns(articles, ARTICLE_REQUIRED_COLUMNS, str(articles_path))
        require_columns(links, LINK_REQUIRED_COLUMNS, str(links_path))
        require_columns(
            companies,
            {"company_id", "company_name", "aliases"},
            str(selected_path),
        )
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    if articles["article_id"].duplicated().any():
        print("INPUT_ERROR: article CSV contains duplicate article_id values.", file=sys.stderr)
        return 1
    if links[["article_id", "company_id"]].duplicated().any():
        print("INPUT_ERROR: link CSV contains duplicate article-company pairs.", file=sys.stderr)
        return 1
    unknown_link_article_ids = sorted(set(links["article_id"]) - set(articles["article_id"]))
    if unknown_link_article_ids:
        print(
            "INPUT_ERROR: link CSV contains article IDs absent from the article CSV: "
            + ", ".join(unknown_link_article_ids[:10]),
            file=sys.stderr,
        )
        return 1

    matching_config = news_config.get("alias_matching", {})
    company_records: dict[str, dict[str, Any]] = {}
    for _, company in companies.iterrows():
        company_id = company["company_id"]
        core, products, strict = configured_aliases(
            company_id,
            english_aliases(company),
            matching_config,
        )
        company_records[company_id] = {
            "company_name": company["company_name"],
            "core_aliases": core,
            "product_aliases": products,
            "strict_aliases": strict,
        }

    unknown_link_company_ids = sorted(set(links["company_id"]) - set(company_records))
    if unknown_link_company_ids:
        print(
            "INPUT_ERROR: link CSV contains companies absent from the coverage pool: "
            + ", ".join(unknown_link_company_ids),
            file=sys.stderr,
        )
        return 1

    source_links: dict[tuple[str, str], dict[str, bool]] = {}
    for _, link in links.iterrows():
        source_links[(link["article_id"], link["company_id"])] = {
            "query_returned": parse_bool(link["query_returned"]),
            "collector_text_alias_matched": parse_bool(link["text_alias_matched"]),
        }

    link_rows: list[dict[str, Any]] = []
    article_acceptance: dict[str, dict[str, list[str]]] = {}
    for _, article in articles.iterrows():
        article_id = article["article_id"]
        text = "\n".join(
            [article["headline"], article["trail_text"], article["body_text"]]
        )
        accepted_ids: list[str] = []
        rejected_query_ids: list[str] = []
        for company_id, record in company_records.items():
            source = source_links.get((article_id, company_id), {})
            query_returned = bool(source.get("query_returned", False))
            collector_match = bool(
                source.get("collector_text_alias_matched", False)
            )
            core_matches = alias_matches(
                text, record["core_aliases"], record["strict_aliases"]
            )
            product_matches = alias_matches(
                text, record["product_aliases"], record["strict_aliases"]
            )
            if core_matches and product_matches:
                evidence_type = "core_and_product"
                link_status = "verified_core"
            elif core_matches:
                evidence_type = "core_alias"
                link_status = "verified_core"
            elif product_matches:
                evidence_type = "product_alias"
                link_status = "verified_product"
            elif query_returned:
                evidence_type = "query_only"
                link_status = "rejected_query_only"
            else:
                continue

            accepted = bool(core_matches or product_matches)
            if accepted:
                accepted_ids.append(company_id)
            else:
                rejected_query_ids.append(company_id)
            link_rows.append(
                {
                    "article_id": article_id,
                    "company_id": company_id,
                    "company_name": record["company_name"],
                    "query_returned": query_returned,
                    "collector_text_alias_matched": collector_match,
                    "core_alias_matched": bool(core_matches),
                    "product_alias_matched": bool(product_matches),
                    "matched_core_aliases": json_list(core_matches),
                    "matched_product_aliases": json_list(product_matches),
                    "evidence_type": evidence_type,
                    "link_status": link_status,
                    "accepted_for_analysis": accepted,
                }
            )
        article_acceptance[article_id] = {
            "accepted": sorted(accepted_ids),
            "rejected_query_only": sorted(rejected_query_ids),
        }

    article_rows: list[dict[str, Any]] = []
    study_start = str(project["study"]["news_start_date"])
    study_end = str(project["study"]["news_end_date"])
    for _, article in articles.iterrows():
        row = article.to_dict()
        status = article_acceptance[article["article_id"]]
        accepted = status["accepted"]
        rejected = status["rejected_query_only"]
        publication_day = article["publication_date"][:10]
        content_complete = all(
            str(article[column]).strip()
            for column in ("article_id", "headline", "publication_date", "web_url", "body_text")
        )
        date_in_study_window = (
            len(publication_day) == 10 and study_start <= publication_day <= study_end
        )
        row.update(
            {
                "accepted_company_ids": json_list(accepted),
                "accepted_company_count": len(accepted),
                "rejected_query_only_company_ids": json_list(rejected),
                "content_complete": content_complete,
                "date_in_study_window": date_in_study_window,
                "analysis_ready": bool(
                    accepted and content_complete and date_in_study_window
                ),
            }
        )
        article_rows.append(row)

    link_rows.sort(key=lambda row: (row["article_id"], row["company_id"]))
    report_rows: list[dict[str, Any]] = []
    for company_id, record in company_records.items():
        company_links = [row for row in link_rows if row["company_id"] == company_id]
        accepted = [row for row in company_links if row["accepted_for_analysis"]]
        report_rows.append(
            {
                "scope": "company",
                "company_id": company_id,
                "company_name": record["company_name"],
                "candidate_links": len(company_links),
                "query_returned_links": sum(
                    bool(row["query_returned"]) for row in company_links
                ),
                "accepted_links": len(accepted),
                "verified_core_links": sum(
                    row["link_status"] == "verified_core" for row in company_links
                ),
                "verified_product_only_links": sum(
                    row["link_status"] == "verified_product" for row in company_links
                ),
                "rejected_query_only_links": sum(
                    row["link_status"] == "rejected_query_only"
                    for row in company_links
                ),
            }
        )

    unique_accepted_articles = sum(
        bool(row["analysis_ready"]) for row in article_rows
    )
    report_rows.append(
        {
            "scope": "all_companies",
            "company_id": "ALL",
            "company_name": "All selected companies",
            "candidate_links": len(link_rows),
            "query_returned_links": sum(
                bool(row["query_returned"]) for row in link_rows
            ),
            "accepted_links": sum(
                bool(row["accepted_for_analysis"]) for row in link_rows
            ),
            "verified_core_links": sum(
                row["link_status"] == "verified_core" for row in link_rows
            ),
            "verified_product_only_links": sum(
                row["link_status"] == "verified_product" for row in link_rows
            ),
            "rejected_query_only_links": sum(
                row["link_status"] == "rejected_query_only" for row in link_rows
            ),
        }
    )

    clean_articles_path = output_dir / f"{stem}_articles_clean.csv"
    clean_links_path = output_dir / f"{stem}_article_company_links_clean.csv"
    cleaning_report_path = output_dir / f"{stem}_cleaning_report.csv"
    article_fields = list(articles.columns) + [
        "accepted_company_ids",
        "accepted_company_count",
        "rejected_query_only_company_ids",
        "content_complete",
        "date_in_study_window",
        "analysis_ready",
    ]
    link_fields = [
        "article_id",
        "company_id",
        "company_name",
        "query_returned",
        "collector_text_alias_matched",
        "core_alias_matched",
        "product_alias_matched",
        "matched_core_aliases",
        "matched_product_aliases",
        "evidence_type",
        "link_status",
        "accepted_for_analysis",
    ]
    report_fields = [
        "scope",
        "company_id",
        "company_name",
        "candidate_links",
        "query_returned_links",
        "accepted_links",
        "verified_core_links",
        "verified_product_only_links",
        "rejected_query_only_links",
    ]
    try:
        write_csv(clean_articles_path, article_rows, article_fields)
        write_csv(clean_links_path, link_rows, link_fields)
        write_csv(cleaning_report_path, report_rows, report_fields)
    except PermissionError as exc:
        print(f"OUTPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    print("Guardian news preparation completed.")
    print(f"Input articles: {len(articles)}")
    print(f"Analysis-ready articles: {unique_accepted_articles}")
    print(f"Candidate article-company links: {len(link_rows)}")
    print(
        "Accepted links: "
        f"{sum(bool(row['accepted_for_analysis']) for row in link_rows)}"
    )
    print(
        "Rejected query-only links: "
        f"{sum(row['link_status'] == 'rejected_query_only' for row in link_rows)}"
    )
    print(f"Clean article CSV: {clean_articles_path}")
    print(f"Clean link CSV: {clean_links_path}")
    print(f"Cleaning report: {cleaning_report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
