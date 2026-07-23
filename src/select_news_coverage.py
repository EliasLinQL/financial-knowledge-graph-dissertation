"""Select the first market-ranked companies with sufficient Guardian coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


COMPANY_COLUMNS = {
    "sample_order",
    "rank",
    "company_id",
    "company_name",
}
ARTICLE_COLUMNS = {
    "article_id",
    "publication_date",
    "content_complete",
    "date_in_study_window",
    "analysis_ready",
}
LINK_COLUMNS = {
    "article_id",
    "company_id",
    "query_returned",
    "link_status",
    "accepted_for_analysis",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exclude companies with insufficient clean-news coverage and "
            "backfill the final sample by market-cap rank."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the project YAML configuration.",
    )
    parser.add_argument("--mode", choices=("test", "full"), default="full")
    parser.add_argument("--companies-csv", type=Path)
    parser.add_argument("--articles-csv", type=Path)
    parser.add_argument("--links-csv", type=Path)
    parser.add_argument("--output-directory", type=Path)
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


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def select_companies_by_coverage(
    companies: pd.DataFrame,
    articles: pd.DataFrame,
    links: pd.DataFrame,
    *,
    target_count: int,
    minimum_accepted_articles: int,
    minimum_active_months: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return final companies and an auditable coverage report."""
    require_columns(companies, COMPANY_COLUMNS, "company pool")
    require_columns(articles, ARTICLE_COLUMNS, "clean articles")
    require_columns(links, LINK_COLUMNS, "clean article-company links")
    if target_count <= 0:
        raise ValueError("target_count must be positive.")
    if minimum_accepted_articles <= 0 or minimum_active_months <= 0:
        raise ValueError("News-coverage thresholds must be positive.")

    ordered = companies.copy()
    ordered["_market_order"] = pd.to_numeric(
        ordered["sample_order"], errors="coerce"
    )
    if ordered["_market_order"].isna().any():
        raise ValueError("Company pool contains invalid sample_order values.")
    ordered = ordered.sort_values(["_market_order", "company_id"])

    ready_ids = set(
        articles.loc[
            articles["analysis_ready"].map(parse_bool),
            "article_id",
        ].astype(str)
    )
    accepted = links[
        links["accepted_for_analysis"].map(parse_bool)
        & links["article_id"].astype(str).isin(ready_ids)
    ].copy()
    publication_by_id = articles.set_index("article_id")[
        "publication_date"
    ].to_dict()

    report_rows: list[dict[str, Any]] = []
    for _, company in ordered.iterrows():
        company_id = str(company["company_id"])
        company_links = accepted[accepted["company_id"].astype(str) == company_id]
        article_ids = sorted(set(company_links["article_id"].astype(str)))
        parsed_dates = pd.to_datetime(
            [publication_by_id.get(article_id, "") for article_id in article_ids],
            errors="coerce",
            utc=True,
        )
        valid_dates = parsed_dates[~parsed_dates.isna()]
        active_months = sorted(
            set(valid_dates.strftime("%Y-%m").tolist())
        )
        accepted_count = len(article_ids)
        month_count = len(active_months)
        qualifies = (
            accepted_count >= minimum_accepted_articles
            and month_count >= minimum_active_months
        )
        shortfalls: list[str] = []
        if accepted_count < minimum_accepted_articles:
            shortfalls.append(
                f"accepted_articles={accepted_count}"
                f"<{minimum_accepted_articles}"
            )
        if month_count < minimum_active_months:
            shortfalls.append(
                f"active_months={month_count}<{minimum_active_months}"
            )
        report_rows.append(
            {
                "market_pool_order": int(company["_market_order"]),
                "rank": int(float(company["rank"])),
                "company_id": company_id,
                "company_name": company["company_name"],
                "accepted_articles": accepted_count,
                "active_months": month_count,
                "first_article_month": active_months[0] if active_months else "",
                "last_article_month": active_months[-1] if active_months else "",
                "minimum_accepted_articles": minimum_accepted_articles,
                "minimum_active_months": minimum_active_months,
                "coverage_qualified": qualifies,
                "coverage_reason": (
                    "thresholds_met" if qualifies else "; ".join(shortfalls)
                ),
            }
        )

    report = pd.DataFrame(report_rows)
    qualified_ids = report.loc[
        report["coverage_qualified"].map(parse_bool),
        "company_id",
    ].astype(str).tolist()
    selected_ids = qualified_ids[:target_count]
    selection_order = {
        company_id: index
        for index, company_id in enumerate(selected_ids, start=1)
    }
    report["selection_order"] = report["company_id"].map(selection_order).fillna("")
    report["selection_status"] = report.apply(
        lambda row: (
            "SELECTED"
            if row["company_id"] in selection_order
            else (
                "QUALIFIED_RESERVE"
                if parse_bool(row["coverage_qualified"])
                else "EXCLUDED_LOW_NEWS_COVERAGE"
            )
        ),
        axis=1,
    )

    selected = ordered[
        ordered["company_id"].astype(str).isin(selected_ids)
    ].copy()
    selected["_selection_order"] = selected["company_id"].map(selection_order)
    selected = selected.sort_values("_selection_order")
    selected = selected.rename(columns={"sample_order": "market_pool_order"})
    selected = selected.drop(columns=["_market_order", "_selection_order"])
    selected.insert(0, "sample_order", range(1, len(selected) + 1))
    return selected, report


def filter_news_for_selected(
    articles: pd.DataFrame,
    links: pd.DataFrame,
    selected_company_ids: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filter pool-cleaned data and recompute article readiness."""
    final_links = links[
        links["company_id"].astype(str).isin(selected_company_ids)
    ].copy()
    linked_article_ids = set(final_links["article_id"].astype(str))
    final_articles = articles[
        articles["article_id"].astype(str).isin(linked_article_ids)
    ].copy()

    accepted_by_article: dict[str, list[str]] = {}
    rejected_by_article: dict[str, list[str]] = {}
    for _, link in final_links.iterrows():
        article_id = str(link["article_id"])
        company_id = str(link["company_id"])
        if parse_bool(link["accepted_for_analysis"]):
            accepted_by_article.setdefault(article_id, []).append(company_id)
        elif str(link["link_status"]) == "rejected_query_only":
            rejected_by_article.setdefault(article_id, []).append(company_id)

    for index, article in final_articles.iterrows():
        article_id = str(article["article_id"])
        accepted = sorted(set(accepted_by_article.get(article_id, [])))
        rejected = sorted(set(rejected_by_article.get(article_id, [])))
        final_articles.at[index, "accepted_company_ids"] = json.dumps(
            accepted, ensure_ascii=False
        )
        final_articles.at[index, "accepted_company_count"] = len(accepted)
        final_articles.at[index, "rejected_query_only_company_ids"] = (
            json.dumps(rejected, ensure_ascii=False)
        )
        final_articles.at[index, "analysis_ready"] = bool(
            accepted
            and parse_bool(article["content_complete"])
            and parse_bool(article["date_in_study_window"])
        )

    return final_articles, final_links


def cleaning_report(
    selected: pd.DataFrame,
    links: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, company in selected.iterrows():
        company_links = links[
            links["company_id"].astype(str) == str(company["company_id"])
        ]
        rows.append(
            {
                "scope": "company",
                "company_id": company["company_id"],
                "company_name": company["company_name"],
                "candidate_links": len(company_links),
                "query_returned_links": int(
                    company_links["query_returned"].map(parse_bool).sum()
                ),
                "accepted_links": int(
                    company_links["accepted_for_analysis"].map(parse_bool).sum()
                ),
                "verified_core_links": int(
                    (company_links["link_status"] == "verified_core").sum()
                ),
                "verified_product_only_links": int(
                    (company_links["link_status"] == "verified_product").sum()
                ),
                "rejected_query_only_links": int(
                    (company_links["link_status"] == "rejected_query_only").sum()
                ),
            }
        )
    rows.append(
        {
            "scope": "all_companies",
            "company_id": "ALL",
            "company_name": "All coverage-selected companies",
            "candidate_links": len(links),
            "query_returned_links": int(
                links["query_returned"].map(parse_bool).sum()
            ),
            "accepted_links": int(
                links["accepted_for_analysis"].map(parse_bool).sum()
            ),
            "verified_core_links": int(
                (links["link_status"] == "verified_core").sum()
            ),
            "verified_product_only_links": int(
                (links["link_status"] == "verified_product").sum()
            ),
            "rejected_query_only_links": int(
                (links["link_status"] == "rejected_query_only").sum()
            ),
        }
    )
    return pd.DataFrame(rows)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {path}. Close the file in Excel or another editor."
        ) from exc


def run() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project = load_yaml(config_path)
    project_root = config_path.parent.parent
    selection_config = project["company_selection"]
    coverage_config = selection_config["news_coverage"]
    news_config = project["news_data"]
    mode_config = news_config["collection_modes"][args.mode]
    outputs = project["outputs"]
    stem = str(mode_config["output_stem"])

    pool_directory = resolve_path(
        project_root,
        coverage_config["pool_processed_output_directory"],
    )
    output_directory = (
        args.output_directory.resolve()
        if args.output_directory
        else resolve_path(project_root, news_config["processed_output_directory"])
    )
    companies_path = (
        args.companies_csv.resolve()
        if args.companies_csv
        else resolve_path(project_root, outputs["market_eligible_companies"])
    )
    articles_path = (
        args.articles_csv.resolve()
        if args.articles_csv
        else pool_directory / f"{stem}_articles_clean.csv"
    )
    links_path = (
        args.links_csv.resolve()
        if args.links_csv
        else pool_directory / f"{stem}_article_company_links_clean.csv"
    )

    companies = pd.read_csv(companies_path, dtype=str, keep_default_na=False)
    articles = pd.read_csv(articles_path, dtype=str, keep_default_na=False)
    links = pd.read_csv(links_path, dtype=str, keep_default_na=False)
    if companies["company_id"].duplicated().any():
        raise ValueError("Market-eligible company pool contains duplicate IDs.")
    if articles["article_id"].duplicated().any():
        raise ValueError("Pool-cleaned articles contain duplicate article IDs.")
    if links[["article_id", "company_id"]].duplicated().any():
        raise ValueError("Pool-cleaned links contain duplicate pairs.")

    target_count = int(selection_config["target_company_count"])
    selected, report = select_companies_by_coverage(
        companies,
        articles,
        links,
        target_count=target_count,
        minimum_accepted_articles=int(
            coverage_config["minimum_accepted_articles"]
        ),
        minimum_active_months=int(coverage_config["minimum_active_months"]),
    )
    report_path = resolve_path(project_root, outputs["news_coverage_report"])
    write_csv(report, report_path)

    if len(selected) < target_count:
        partial_path = resolve_path(
            project_root,
            outputs["news_coverage_partial_selection"],
        )
        write_csv(selected, partial_path)
        print(f"News-coverage report: {report_path}")
        print(f"Partial coverage selection: {partial_path}")
        raise ValueError(
            f"Only {len(selected)}/{target_count} companies met the news "
            "coverage thresholds. Extend the ranked candidate mapping and "
            "rerun the market and news stages."
        )

    selected_ids = set(selected["company_id"].astype(str))
    final_articles, final_links = filter_news_for_selected(
        articles,
        links,
        selected_ids,
    )
    final_report = cleaning_report(selected, final_links)

    selected_path = resolve_path(project_root, outputs["selected_companies"])
    article_output = output_directory / f"{stem}_articles_clean.csv"
    link_output = output_directory / f"{stem}_article_company_links_clean.csv"
    cleaning_output = output_directory / f"{stem}_cleaning_report.csv"
    write_csv(selected, selected_path)
    write_csv(final_articles, article_output)
    write_csv(final_links, link_output)
    write_csv(final_report, cleaning_output)

    excluded = int(
        (report["selection_status"] == "EXCLUDED_LOW_NEWS_COVERAGE").sum()
    )
    reserves = int(
        (report["selection_status"] == "QUALIFIED_RESERVE").sum()
    )
    print("News-coverage selection completed.")
    print(
        f"Final companies: {len(selected)}/{target_count}; "
        f"low-coverage exclusions={excluded}; qualified reserves={reserves}"
    )
    print(f"Selected companies: {selected_path}")
    print(f"Coverage report: {report_path}")
    print(f"Final clean articles: {article_output}")
    print(f"Final clean links: {link_output}")
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (
        FileNotFoundError,
        KeyError,
        PermissionError,
        ValueError,
        pd.errors.ParserError,
    ) as exc:
        print(f"NEWS_COVERAGE_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
