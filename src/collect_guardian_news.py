"""Collect Guardian news for the configured selected-company sample."""

from __future__ import annotations

import argparse
import calendar
import csv
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml
from dotenv import load_dotenv


class GuardianError(RuntimeError):
    """Base error for Guardian data-source failures."""


class GuardianAuthenticationError(GuardianError):
    """The API credential is absent or rejected."""


class GuardianRateLimitError(GuardianError):
    """The Guardian API rate limit has been reached."""


@dataclass
class ApiRequestBudget:
    """Cap actual HTTP attempts, including retry attempts."""

    maximum_attempts: int
    attempts_used: int = 0

    def consume(self) -> None:
        if self.maximum_attempts > 0 and self.attempts_used >= self.maximum_attempts:
            raise GuardianRateLimitError(
                "Local Guardian API-attempt budget exhausted before the next "
                f"request ({self.attempts_used}/{self.maximum_attempts}). "
                "Resume later with the saved cache files."
            )
        self.attempts_used += 1


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def strip_html(value: Any) -> str:
    if value is None:
        return ""
    parser = TextExtractor()
    parser.feed(html.unescape(str(value)))
    return parser.text()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Guardian articles for the configured company sample."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the project YAML configuration file.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore saved per-company Guardian responses and request them again.",
    )
    parser.add_argument(
        "--refresh-company",
        action="append",
        default=[],
        metavar="COMPANY_ID",
        help=(
            "Refresh only this company's windows while reusing every other cache. "
            "May be supplied more than once."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("test", "full"),
        default="test",
        help=(
            "test: one full-period window; "
            "full: monthly windows across the configured study period."
        ),
    )
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("The configuration file must contain a YAML mapping.")
    return config


def resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def collection_windows(
    start_value: str, end_value: str, strategy: str
) -> list[tuple[str, str]]:
    start = date.fromisoformat(start_value)
    end = date.fromisoformat(end_value)
    if end < start:
        raise ValueError("news_end_date must not be earlier than news_start_date")
    if strategy == "full_period":
        return [(start.isoformat(), end.isoformat())]
    if strategy != "monthly":
        raise ValueError(f"Unsupported Guardian window strategy: {strategy}")

    windows: list[tuple[str, str]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        window_start = max(start, month_start)
        window_end = min(end, month_end)
        windows.append((window_start.isoformat(), window_end.isoformat()))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return windows


def required_network_requests(
    company_ids: list[str],
    windows: list[tuple[str, str]],
    raw_directory: Path,
    refresh_all: bool,
    refresh_company_ids: set[str],
    use_cached_responses: bool,
) -> int:
    """Count logical Guardian requests after accounting for reusable cache files."""
    requests_required = 0
    for company_id in company_ids:
        refresh_company = refresh_all or company_id in refresh_company_ids
        for window_start, window_end in windows:
            cache_path = raw_directory / (
                f"{company_id}_{window_start}_{window_end}.json"
            )
            cache_reusable = (
                use_cached_responses
                and cache_path.exists()
                and not refresh_company
            )
            if not cache_reusable:
                requests_required += 1
    return requests_required


def validate_collection_capacity(
    company_count: int,
    window_count: int,
    per_window_limit: int,
    global_unique_article_limit: int,
) -> None:
    """Prevent an undersized global limit from biasing later-ranked companies."""
    maximum_returned_articles = company_count * window_count * per_window_limit
    if global_unique_article_limit < maximum_returned_articles:
        raise ValueError(
            "global_unique_article_limit is too small for the configured sample: "
            f"{global_unique_article_limit} < {maximum_returned_articles} "
            f"({company_count} companies x {window_count} windows x "
            f"{per_window_limit} articles)."
        )


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


def guardian_query(aliases: list[str]) -> str:
    terms: list[str] = []
    for alias in aliases:
        escaped = alias.replace('"', "")
        if re.fullmatch(r"[A-Za-z0-9_-]+", escaped):
            terms.append(escaped)
        else:
            terms.append(f'"{escaped}"')
    return " OR ".join(terms)


def company_query(
    company_id: str, aliases: list[str], config: dict[str, Any]
) -> str:
    overrides = config.get("company_query_overrides", {})
    override = str(overrides.get(company_id, "")).strip()
    return override or guardian_query(aliases)


def company_matching_aliases(
    company_id: str, aliases: list[str], config: dict[str, Any]
) -> list[str]:
    matching = config.get("alias_matching", {})
    excluded_map = matching.get("excluded_aliases_by_company", {})
    excluded = {
        str(alias).strip().casefold()
        for alias in excluded_map.get(company_id, [])
        if str(alias).strip()
    }
    additional_map = matching.get("additional_aliases_by_company", {})
    candidates = list(aliases) + [
        str(alias).strip()
        for alias in additional_map.get(company_id, [])
        if str(alias).strip()
    ]
    accepted: list[str] = []
    seen: set[str] = set()
    for alias in candidates:
        key = alias.casefold()
        if key in excluded or key in seen:
            continue
        accepted.append(alias)
        seen.add(key)
    return accepted


def company_case_sensitive_aliases(
    company_id: str, config: dict[str, Any]
) -> set[str]:
    matching = config.get("alias_matching", {})
    strict_map = matching.get("case_sensitive_aliases_by_company", {})
    return {
        str(alias).strip().casefold()
        for alias in strict_map.get(company_id, [])
        if str(alias).strip()
    }


def api_error_message(response: requests.Response, payload: Any) -> str:
    if isinstance(payload, dict):
        guardian_response = payload.get("response")
        if isinstance(guardian_response, dict):
            return str(
                guardian_response.get("message")
                or guardian_response.get("status")
                or f"HTTP {response.status_code}"
            )
        return str(payload.get("message") or payload.get("error") or response.reason)
    return f"HTTP {response.status_code}: {response.reason}"


def request_guardian(
    base_url: str,
    params: dict[str, Any],
    timeout_seconds: float,
    max_retries: int,
    backoff_seconds: float,
    request_budget: ApiRequestBudget | None = None,
) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        if request_budget is not None:
            request_budget.consume()
        try:
            response = requests.get(base_url, params=params, timeout=timeout_seconds)
        except requests.RequestException as exc:
            if attempt >= max_retries:
                raise GuardianError(f"Network error: {exc}") from exc
            wait = backoff_seconds * (2**attempt)
            print(f"  Network error; retrying in {wait:.0f} seconds...")
            time.sleep(wait)
            continue

        try:
            payload = response.json()
        except ValueError as exc:
            raise GuardianError(
                f"Guardian returned non-JSON content (HTTP {response.status_code})"
            ) from exc

        if response.status_code in {401, 403}:
            raise GuardianAuthenticationError(api_error_message(response, payload))
        if response.status_code == 429:
            if attempt >= max_retries:
                raise GuardianRateLimitError(api_error_message(response, payload))
            wait = backoff_seconds * (2**attempt)
            print(f"  Rate limited; retrying in {wait:.0f} seconds...")
            time.sleep(wait)
            continue
        if response.status_code >= 500:
            if attempt >= max_retries:
                raise GuardianError(api_error_message(response, payload))
            wait = backoff_seconds * (2**attempt)
            print(f"  Guardian server error; retrying in {wait:.0f} seconds...")
            time.sleep(wait)
            continue
        if response.status_code >= 400:
            raise GuardianError(api_error_message(response, payload))

        guardian_response = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(guardian_response, dict):
            raise GuardianError("Unexpected Guardian response structure")
        if guardian_response.get("status") != "ok":
            raise GuardianError(str(guardian_response.get("message", "Unknown error")))
        return payload

    raise AssertionError("Unreachable Guardian retry state")


def read_or_request_company(
    company: pd.Series,
    config: dict[str, Any],
    study: dict[str, Any],
    api_key: str,
    cache_path: Path,
    refresh: bool,
    window_start_date: str,
    window_end_date: str,
    per_window_limit: int,
    order_by: str,
    request_budget: ApiRequestBudget | None = None,
) -> tuple[dict[str, Any], str, list[str], str]:
    aliases = english_aliases(company)
    query = company_query(str(company["company_id"]), aliases, config)
    if config.get("use_cached_responses", True) and cache_path.exists() and not refresh:
        with cache_path.open("r", encoding="utf-8") as handle:
            return json.load(handle), "cache", aliases, query

    params = {
        "api-key": api_key,
        "q": query,
        "from-date": window_start_date,
        "to-date": window_end_date,
        "page-size": per_window_limit,
        "page": 1,
        "order-by": order_by,
        "show-fields": ",".join(config.get("show_fields", [])),
        "show-tags": config.get("show_tags", "all"),
    }
    payload = request_guardian(
        base_url=config["base_url"],
        params=params,
        timeout_seconds=float(config.get("request_timeout_seconds", 30)),
        max_retries=int(config.get("max_retries", 2)),
        backoff_seconds=float(config.get("retry_backoff_seconds", 5)),
        request_budget=request_budget,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return payload, "guardian", aliases, query


def alias_matches(
    text: str, aliases: list[str], case_sensitive_aliases: set[str] | None = None
) -> list[str]:
    strict = case_sensitive_aliases or set()
    matches: list[str] = []
    for alias in aliases:
        pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"
        flags = 0 if alias.casefold() in strict else re.IGNORECASE
        if re.search(pattern, text, flags=flags):
            matches.append(alias)
    return matches


def article_search_text(result: dict[str, Any]) -> str:
    fields = result.get("fields") or {}
    parts = [
        result.get("webTitle", ""),
        fields.get("headline", ""),
        strip_html(fields.get("trailText", "")),
        fields.get("bodyText", ""),
    ]
    return "\n".join(str(part) for part in parts if part)


def flatten_article(
    article: dict[str, Any],
    queried_company_ids: list[str],
    matched_company_ids: list[str],
) -> dict[str, Any]:
    fields = article.get("fields") or {}
    tags = article.get("tags") or []
    return {
        "article_id": article.get("id", ""),
        "headline": fields.get("headline") or article.get("webTitle", ""),
        "publication_date": article.get("webPublicationDate", ""),
        "section_id": article.get("sectionId", ""),
        "section_name": article.get("sectionName", ""),
        "web_url": article.get("webUrl", ""),
        "short_url": fields.get("shortUrl", ""),
        "byline": fields.get("byline", ""),
        "wordcount": fields.get("wordcount", ""),
        "trail_text": strip_html(fields.get("trailText", "")),
        "body_text": fields.get("bodyText", ""),
        "tag_ids": json.dumps(
            [tag.get("id", "") for tag in tags if isinstance(tag, dict)],
            ensure_ascii=False,
        ),
        "tag_titles": json.dumps(
            [tag.get("webTitle", "") for tag in tags if isinstance(tag, dict)],
            ensure_ascii=False,
        ),
        "queried_company_ids": json.dumps(queried_company_ids),
        "matched_company_ids": json.dumps(matched_company_ids),
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project_root = config_path.parent.parent
    load_dotenv(project_root / ".env")
    project = load_config(config_path)
    study = project["study"]
    config = project["news_data"]
    try:
        mode_config = config["collection_modes"][args.mode]
    except KeyError as exc:
        print(f"Missing Guardian collection-mode configuration: {exc}", file=sys.stderr)
        return 1
    try:
        windows = collection_windows(
            study["news_start_date"],
            study["news_end_date"],
            mode_config["window_strategy"],
        )
    except ValueError as exc:
        print(f"Invalid Guardian date-window configuration: {exc}", file=sys.stderr)
        return 1
    collection_path = resolve_path(
        project_root,
        project["outputs"].get(
            "market_eligible_companies",
            project["outputs"]["selected_companies"],
        ),
    )

    key_name = config["api_key_environment_variable"]
    api_key = os.getenv(key_name, "").strip()
    if not api_key:
        print(f"Missing {key_name} in the project .env file.", file=sys.stderr)
        return 3
    if not collection_path.exists():
        print(
            f"Market-eligible company pool not found: {collection_path}\n"
            "Run validate_market_data.py successfully first.",
            file=sys.stderr,
        )
        return 1

    companies = pd.read_csv(collection_path, dtype=str)
    sample_order = pd.to_numeric(companies["sample_order"], errors="coerce")
    if sample_order.isna().any():
        print(
            "Market-eligible company pool contains an invalid sample_order.",
            file=sys.stderr,
        )
        return 1
    companies = companies.assign(_sample_order_numeric=sample_order)
    companies = companies.sort_values("_sample_order_numeric").drop(
        columns="_sample_order_numeric"
    )
    target_company_count = int(
        project["company_selection"]["target_company_count"]
    )
    if len(companies) < target_company_count:
        print(
            f"Expected at least {target_company_count} market-eligible companies, "
            f"found {len(companies)} in {collection_path}.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Guardian coverage pool: {len(companies)} market-eligible companies; "
        f"final target={target_company_count}."
    )

    selected_company_ids = set(companies["company_id"])
    refresh_company_ids = {
        str(company_id).strip() for company_id in args.refresh_company
        if str(company_id).strip()
    }
    unknown_refresh_ids = sorted(refresh_company_ids - selected_company_ids)
    if unknown_refresh_ids:
        print(
            "Unknown --refresh-company value(s): "
            + ", ".join(unknown_refresh_ids),
            file=sys.stderr,
        )
        return 1

    output_stem = str(mode_config["output_stem"])
    raw_root = resolve_path(project_root, config["raw_response_root"])
    raw_dir = raw_root / args.mode
    raw_output_dir = resolve_path(project_root, config["raw_output_directory"])
    processed_output_dir = resolve_path(
        project_root, config["processed_output_directory"]
    )
    combined_json_path = raw_output_dir / f"{output_stem}_batch.json"
    article_csv_path = processed_output_dir / f"{output_stem}_articles.csv"
    links_csv_path = (
        processed_output_dir / f"{output_stem}_article_company_links.csv"
    )
    report_csv_path = (
        processed_output_dir / f"{output_stem}_collection_report.csv"
    )
    global_limit = int(mode_config["global_unique_article_limit"])
    per_window_limit = int(mode_config["per_company_per_window"])
    order_by = str(mode_config.get("order_by", "relevance"))
    try:
        validate_collection_capacity(
            company_count=len(companies),
            window_count=len(windows),
            per_window_limit=per_window_limit,
            global_unique_article_limit=global_limit,
        )
    except ValueError as exc:
        print(f"Invalid Guardian collection capacity: {exc}", file=sys.stderr)
        return 1

    network_requests = required_network_requests(
        company_ids=companies["company_id"].astype(str).tolist(),
        windows=windows,
        raw_directory=raw_dir,
        refresh_all=args.refresh,
        refresh_company_ids=refresh_company_ids,
        use_cached_responses=bool(config.get("use_cached_responses", True)),
    )
    maximum_requests = int(
        config.get("maximum_logical_requests_per_run", 0)
    )
    print(
        "Guardian request preflight: "
        f"logical_windows={len(companies) * len(windows)}, "
        f"network_requests_required={network_requests}, "
        f"reused_cache_windows={len(companies) * len(windows) - network_requests}"
    )
    if maximum_requests > 0 and network_requests > maximum_requests:
        print(
            "REQUEST_BUDGET_ERROR: this run would require "
            f"{network_requests} Guardian requests, above the configured "
            f"maximum_logical_requests_per_run={maximum_requests}. "
            "Use cached responses, refresh selected companies only, or split "
            "the collection across runs.",
            file=sys.stderr,
        )
        return 1
    api_request_budget = ApiRequestBudget(
        maximum_attempts=int(
            config.get("maximum_api_attempts_per_run", 0)
        )
    )

    aliases_by_company: dict[str, list[str]] = {}
    strict_aliases_by_company: dict[str, set[str]] = {}
    for _, row in companies.iterrows():
        company_id = row["company_id"]
        aliases = english_aliases(row)
        aliases_by_company[company_id] = company_matching_aliases(
            company_id, aliases, config
        )
        strict_aliases_by_company[company_id] = company_case_sensitive_aliases(
            company_id, config
        )
    company_by_id = {
        row["company_id"]: row for _, row in companies.iterrows()
    }
    articles: dict[str, dict[str, Any]] = {}
    report_rows: list[dict[str, Any]] = []

    for _, company in companies.iterrows():
        company_id = company["company_id"]
        print(f"Collecting {company['company_name']} ({company_id})...")
        for window_start, window_end in windows:
            cache_path = raw_dir / (
                f"{company_id}_{window_start}_{window_end}.json"
            )
            try:
                payload, source, aliases, query = read_or_request_company(
                    company=company,
                    config=config,
                    study=study,
                    api_key=api_key,
                    cache_path=cache_path,
                    refresh=args.refresh or company_id in refresh_company_ids,
                    window_start_date=window_start,
                    window_end_date=window_end,
                    per_window_limit=per_window_limit,
                    order_by=order_by,
                    request_budget=api_request_budget,
                )
            except GuardianAuthenticationError as exc:
                print(f"AUTHENTICATION_ERROR: {exc}", file=sys.stderr)
                return 3
            except GuardianRateLimitError as exc:
                print(f"RATE_LIMITED: {exc}", file=sys.stderr)
                return 2
            except GuardianError as exc:
                print(f"SOURCE_ERROR: {exc}", file=sys.stderr)
                return 2

            response = payload["response"]
            results = response.get("results", [])
            new_unique = 0
            for result in results:
                article_id = str(result.get("id", "")).strip()
                if not article_id:
                    continue
                if article_id not in articles:
                    if len(articles) >= global_limit:
                        continue
                    articles[article_id] = {
                        "raw_result": result,
                        "queried_company_ids": set(),
                    }
                    new_unique += 1
                articles[article_id]["queried_company_ids"].add(company_id)

            report_rows.append(
                {
                    "mode": args.mode,
                    "window_start_date": window_start,
                    "window_end_date": window_end,
                    "company_id": company_id,
                    "company_name": company["company_name"],
                    "aliases": json.dumps(aliases, ensure_ascii=False),
                    "matching_aliases": json.dumps(
                        aliases_by_company[company_id], ensure_ascii=False
                    ),
                    "query": query,
                    "source": source,
                    "api_total_results": response.get("total", 0),
                    "returned_results": len(results),
                    "new_unique_articles": new_unique,
                }
            )
            print(
                f"  {window_start} to {window_end}: returned={len(results)}, "
                f"new_unique={new_unique}, combined_unique={len(articles)}"
            )
            delay = float(config.get("request_delay_seconds", 0))
            if source == "guardian" and delay > 0:
                time.sleep(delay)

    article_rows: list[dict[str, Any]] = []
    link_rows: list[dict[str, Any]] = []
    combined_articles: list[dict[str, Any]] = []
    for article_id, record in articles.items():
        result = record["raw_result"]
        queried_ids = sorted(record["queried_company_ids"])
        text = article_search_text(result)
        matched_by_company: dict[str, list[str]] = {}
        for company_id, aliases in aliases_by_company.items():
            matches = alias_matches(
                text,
                aliases,
                strict_aliases_by_company.get(company_id, set()),
            )
            if matches:
                matched_by_company[company_id] = matches
        matched_ids = sorted(matched_by_company)
        article_rows.append(flatten_article(result, queried_ids, matched_ids))

        linked_ids = sorted(set(queried_ids) | set(matched_ids))
        for company_id in linked_ids:
            company = company_by_id[company_id]
            link_rows.append(
                {
                    "article_id": article_id,
                    "company_id": company_id,
                    "company_name": company["company_name"],
                    "query_returned": company_id in queried_ids,
                    "text_alias_matched": company_id in matched_ids,
                    "matched_aliases": json.dumps(
                        matched_by_company.get(company_id, []), ensure_ascii=False
                    ),
                }
            )
        combined_articles.append(
            {
                "article_id": article_id,
                "queried_company_ids": queried_ids,
                "matched_company_ids": matched_ids,
                "matched_aliases_by_company": matched_by_company,
                "raw_result": result,
            }
        )

    article_rows.sort(key=lambda row: (row["publication_date"], row["article_id"]))
    link_rows.sort(key=lambda row: (row["article_id"], row["company_id"]))
    combined_articles.sort(key=lambda row: row["article_id"])
    combined_json_path.parent.mkdir(parents=True, exist_ok=True)
    combined_payload = {
        "collection_metadata": {
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "collection_mode": args.mode,
            "window_strategy": mode_config["window_strategy"],
            "news_start_date": study["news_start_date"],
            "news_end_date": study["news_end_date"],
            "selected_company_count": len(companies),
            "window_count": len(windows),
            "per_company_per_window": per_window_limit,
            "global_unique_article_limit": global_limit,
            "unique_articles_collected": len(combined_articles),
            "api_attempts_used": api_request_budget.attempts_used,
            "api_attempt_limit": api_request_budget.maximum_attempts,
            "source": "The Guardian Open Platform Content API",
        },
        "articles": combined_articles,
    }
    with combined_json_path.open("w", encoding="utf-8") as handle:
        json.dump(combined_payload, handle, ensure_ascii=False, indent=2)

    article_fields = [
        "article_id",
        "headline",
        "publication_date",
        "section_id",
        "section_name",
        "web_url",
        "short_url",
        "byline",
        "wordcount",
        "trail_text",
        "body_text",
        "tag_ids",
        "tag_titles",
        "queried_company_ids",
        "matched_company_ids",
    ]
    link_fields = [
        "article_id",
        "company_id",
        "company_name",
        "query_returned",
        "text_alias_matched",
        "matched_aliases",
    ]
    report_fields = [
        "mode",
        "window_start_date",
        "window_end_date",
        "company_id",
        "company_name",
        "aliases",
        "matching_aliases",
        "query",
        "source",
        "api_total_results",
        "returned_results",
        "new_unique_articles",
    ]
    write_csv(article_csv_path, article_rows, article_fields)
    write_csv(links_csv_path, link_rows, link_fields)
    write_csv(report_csv_path, report_rows, report_fields)

    print(f"\nGuardian {args.mode} collection completed.")
    print(f"Unique articles: {len(article_rows)}")
    print(f"Combined raw JSON: {combined_json_path}")
    print(f"Article CSV: {article_csv_path}")
    print(f"Article-company links: {links_csv_path}")
    print(f"Collection report: {report_csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
