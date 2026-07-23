"""Align recommended event-company links to market trading-day return windows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml


EVENT_COLUMNS = {
    "event_id",
    "article_id",
    "publication_timestamp",
    "event_type",
    "recommended_for_graph",
}
LINK_COLUMNS = {
    "event_id",
    "article_id",
    "company_id",
    "recommended_for_graph",
}
COMPANY_COLUMNS = {
    "company_id",
    "company_name",
    "twelve_data_symbol",
    "twelve_data_exchange",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Map event-company candidates to Twelve Data trading sessions and "
            "calculate configured cumulative return windows."
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
        help="Guardian collection mode to align.",
    )
    parser.add_argument("--events-csv", type=Path, help="Optional event candidate CSV.")
    parser.add_argument(
        "--event-links-csv",
        type=Path,
        help="Optional event-company link CSV.",
    )
    parser.add_argument(
        "--selected-companies",
        type=Path,
        help="Optional selected_companies.csv path.",
    )
    parser.add_argument(
        "--market-directory",
        type=Path,
        help="Optional Twelve Data cache directory.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="Optional output directory.",
    )
    parser.add_argument(
        "--include-not-recommended",
        action="store_true",
        help="Also align events not recommended by the rule-based event filter.",
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


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def parse_close_time(value: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":", maxsplit=1))
        return time(hour=hour, minute=minute)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "market_close_local_time must use HH:MM 24-hour format"
        ) from exc


def market_cache_name(symbol: str, exchange: str) -> str:
    return f"{symbol}_{exchange}".replace("/", "-").replace(".", "_") + ".csv"


def read_market_file(path: Path) -> pd.DataFrame:
    data = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"Date", "Open", "High", "Low", "Close", "Volume"}
    require_columns(data, required, str(path))
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce").dt.normalize()
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["Date", "Close"])
    data = data.drop_duplicates("Date", keep="last").sort_values("Date")
    if data.empty:
        raise ValueError(f"No valid market rows in {path}")
    return data.set_index("Date")


def stable_market_link_id(event_id: str, company_id: str) -> str:
    digest = hashlib.sha1(f"{event_id}|{company_id}".encode("utf-8")).hexdigest()
    return f"EML_{digest[:14].upper()}"


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {path}. Close this file in Excel or another editor."
        ) from exc


def align_publication_to_session(
    publication_timestamp: pd.Timestamp,
    trading_dates: pd.DatetimeIndex,
    timezone: ZoneInfo,
    market_close: time,
) -> tuple[int | None, str, str]:
    if publication_timestamp.tzinfo is None:
        publication_timestamp = publication_timestamp.tz_localize("UTC")
    local_timestamp = publication_timestamp.tz_convert(timezone)
    local_date = pd.Timestamp(local_timestamp.date())
    local_time = local_timestamp.time().replace(tzinfo=None)

    exact_positions = trading_dates.get_indexer([local_date])
    exact_position = int(exact_positions[0])
    if exact_position >= 0 and local_time <= market_close:
        return exact_position, "same_session_before_close", local_timestamp.isoformat()

    later_positions = trading_dates.searchsorted(local_date, side="right")
    if exact_position < 0:
        later_positions = trading_dates.searchsorted(local_date, side="left")
        rule = "next_session_non_trading_day"
    else:
        rule = "next_session_after_close"
    if later_positions >= len(trading_dates):
        return None, rule, local_timestamp.isoformat()
    return int(later_positions), rule, local_timestamp.isoformat()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project_root = config_path.parent.parent
    try:
        project = load_yaml(config_path)
        news_config = project["news_data"]
        event_config = project["event_analysis"]
        nlp_config = project.get("nlp_enrichment", {})
        market_config = project["market_data"]
        mode_config = news_config["collection_modes"][args.mode]
        windows = sorted(
            {int(value) for value in event_config["event_windows_trading_days"]}
        )
        if not windows or windows[0] < 1:
            raise ValueError("event_windows_trading_days must contain positive integers")
        timezone = ZoneInfo(str(event_config["market_timezone"]))
        market_close = parse_close_time(
            str(event_config.get("market_close_local_time", "16:00"))
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"CONFIGURATION_ERROR: {exc}", file=sys.stderr)
        return 1

    stem = str(mode_config["output_stem"])
    processed_dir = resolve_path(project_root, news_config["processed_output_directory"])
    use_nlp_inputs = bool(
        isinstance(nlp_config, dict)
        and nlp_config.get("enabled", False)
        and nlp_config.get("use_for_downstream", False)
    )
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
    events_path = (
        args.events_csv.resolve()
        if args.events_csv
        else processed_dir / event_filename
    )
    links_path = (
        args.event_links_csv.resolve()
        if args.event_links_csv
        else processed_dir / event_link_filename
    )
    selected_path = (
        args.selected_companies.resolve()
        if args.selected_companies
        else resolve_path(project_root, project["outputs"]["selected_companies"])
    )
    market_dir = (
        args.market_directory.resolve()
        if args.market_directory
        else resolve_path(project_root, market_config["output_directory"])
    )
    output_dir = args.output_directory.resolve() if args.output_directory else processed_dir

    try:
        events = pd.read_csv(events_path, dtype=str, keep_default_na=False)
        links = pd.read_csv(links_path, dtype=str, keep_default_na=False)
        companies = pd.read_csv(selected_path, dtype=str, keep_default_na=False)
        require_columns(events, EVENT_COLUMNS, str(events_path))
        require_columns(links, LINK_COLUMNS, str(links_path))
        require_columns(companies, COMPANY_COLUMNS, str(selected_path))
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    if events["event_id"].duplicated().any():
        print("INPUT_ERROR: duplicate event_id values.", file=sys.stderr)
        return 1
    if links[["event_id", "company_id"]].duplicated().any():
        print("INPUT_ERROR: duplicate event-company pairs.", file=sys.stderr)
        return 1

    company_map = companies.set_index("company_id").to_dict(orient="index")
    unknown_ids = sorted(set(links["company_id"]) - set(company_map))
    if unknown_ids:
        print(
            "INPUT_ERROR: event links contain unknown company IDs: "
            + ", ".join(unknown_ids),
            file=sys.stderr,
        )
        return 1

    market_by_company: dict[str, pd.DataFrame] = {}
    missing_market_files: list[str] = []
    for company_id in sorted(set(links["company_id"])):
        company = company_map[company_id]
        path = market_dir / market_cache_name(
            company["twelve_data_symbol"],
            company["twelve_data_exchange"],
        )
        if not path.exists():
            missing_market_files.append(str(path))
            continue
        try:
            market_by_company[company_id] = read_market_file(path)
        except ValueError as exc:
            print(f"INPUT_ERROR: {exc}", file=sys.stderr)
            return 1
    if missing_market_files:
        print(
            "INPUT_ERROR: missing Twelve Data cache file(s):\n- "
            + "\n- ".join(missing_market_files),
            file=sys.stderr,
        )
        return 1

    event_map = events.set_index("event_id").to_dict(orient="index")
    include_only_recommended = bool(
        event_config.get(
            "include_only_recommended_events_for_market_alignment",
            True,
        )
    ) and not args.include_not_recommended

    observation_rows: list[dict[str, Any]] = []
    alignment_records: list[dict[str, Any]] = []
    for _, link in links.iterrows():
        event_id = link["event_id"]
        event = event_map.get(event_id)
        if event is None:
            print(
                f"INPUT_ERROR: link references unknown event_id {event_id}",
                file=sys.stderr,
            )
            return 1
        recommended = parse_bool(event["recommended_for_graph"])
        link_recommended = parse_bool(link["recommended_for_graph"])
        if include_only_recommended and not (recommended and link_recommended):
            continue

        company_id = link["company_id"]
        company = company_map[company_id]
        market = market_by_company[company_id]
        trading_dates = pd.DatetimeIndex(market.index)
        publication = pd.to_datetime(
            event["publication_timestamp"], utc=True, errors="coerce"
        )
        market_link_id = stable_market_link_id(event_id, company_id)
        if pd.isna(publication):
            alignment_records.append(
                {
                    "market_link_id": market_link_id,
                    "company_id": company_id,
                    "status": "INVALID_PUBLICATION_TIMESTAMP",
                }
            )
            continue

        anchor_position, anchor_rule, local_timestamp = align_publication_to_session(
            publication,
            trading_dates,
            timezone,
            market_close,
        )
        if anchor_position is None:
            alignment_records.append(
                {
                    "market_link_id": market_link_id,
                    "company_id": company_id,
                    "status": "NO_TRADING_SESSION_AFTER_EVENT",
                }
            )
            continue
        if anchor_position == 0:
            alignment_records.append(
                {
                    "market_link_id": market_link_id,
                    "company_id": company_id,
                    "status": "NO_BASELINE_SESSION",
                }
            )
            continue

        baseline_date = trading_dates[anchor_position - 1]
        baseline_close = float(market.loc[baseline_date, "Close"])
        complete_windows = 0
        for window in windows:
            end_position = anchor_position + window - 1
            if end_position >= len(trading_dates):
                continue
            end_date = trading_dates[end_position]
            end_close = float(market.loc[end_date, "Close"])
            cumulative_return = end_close / baseline_close - 1.0
            observation_rows.append(
                {
                    "market_link_id": market_link_id,
                    "event_id": event_id,
                    "article_id": event["article_id"],
                    "company_id": company_id,
                    "company_name": company["company_name"],
                    "symbol": company["twelve_data_symbol"],
                    "exchange": company["twelve_data_exchange"],
                    "event_type": event["event_type"],
                    "publication_timestamp_utc": publication.isoformat(),
                    "publication_timestamp_market_tz": local_timestamp,
                    "anchor_rule": anchor_rule,
                    "baseline_date": baseline_date.date().isoformat(),
                    "baseline_close": baseline_close,
                    "window_trading_days": window,
                    "window_end_date": end_date.date().isoformat(),
                    "window_end_close": end_close,
                    "cumulative_return": cumulative_return,
                    "data_source": "Twelve Data daily OHLCV cache",
                    "causal_claim": False,
                }
            )
            complete_windows += 1

        status = "ALIGNED" if complete_windows == len(windows) else "PARTIAL_WINDOWS"
        alignment_records.append(
            {
                "market_link_id": market_link_id,
                "company_id": company_id,
                "status": status,
            }
        )

    observation_rows.sort(
        key=lambda row: (
            row["event_id"],
            row["company_id"],
            row["window_trading_days"],
        )
    )
    status_counts = pd.DataFrame(alignment_records)
    report_rows: list[dict[str, Any]] = []
    for company_id, company in company_map.items():
        company_records = (
            status_counts[status_counts["company_id"] == company_id]
            if not status_counts.empty
            else pd.DataFrame()
        )
        report_rows.append(
            {
                "scope": "company",
                "company_id": company_id,
                "company_name": company["company_name"],
                "event_company_pairs": len(company_records),
                "aligned_pairs": int(
                    (company_records.get("status", pd.Series(dtype=str)) == "ALIGNED").sum()
                ),
                "partial_or_failed_pairs": int(
                    (company_records.get("status", pd.Series(dtype=str)) != "ALIGNED").sum()
                ),
                "market_observation_rows": sum(
                    row["company_id"] == company_id for row in observation_rows
                ),
            }
        )
    aligned_total = sum(
        record.get("status") == "ALIGNED" for record in alignment_records
    )
    report_rows.append(
        {
            "scope": "all_companies",
            "company_id": "ALL",
            "company_name": "All selected companies",
            "event_company_pairs": len(alignment_records),
            "aligned_pairs": aligned_total,
            "partial_or_failed_pairs": len(alignment_records) - aligned_total,
            "market_observation_rows": len(observation_rows),
        }
    )

    observations_path = output_dir / f"{stem}_event_market_windows.csv"
    report_path = output_dir / f"{stem}_event_market_alignment_report.csv"
    observation_fields = [
        "market_link_id",
        "event_id",
        "article_id",
        "company_id",
        "company_name",
        "symbol",
        "exchange",
        "event_type",
        "publication_timestamp_utc",
        "publication_timestamp_market_tz",
        "anchor_rule",
        "baseline_date",
        "baseline_close",
        "window_trading_days",
        "window_end_date",
        "window_end_close",
        "cumulative_return",
        "data_source",
        "causal_claim",
    ]
    report_fields = [
        "scope",
        "company_id",
        "company_name",
        "event_company_pairs",
        "aligned_pairs",
        "partial_or_failed_pairs",
        "market_observation_rows",
    ]
    try:
        write_csv(observations_path, observation_rows, observation_fields)
        write_csv(report_path, report_rows, report_fields)
    except PermissionError as exc:
        print(f"OUTPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    print("Event-market alignment completed.")
    print(f"Event-company pairs considered: {len(alignment_records)}")
    print(f"Fully aligned pairs: {aligned_total}")
    print(f"Market-window rows: {len(observation_rows)}")
    print(f"Market windows: {observations_path}")
    print(f"Alignment report: {report_path}")
    print(
        "Cumulative returns are descriptive market context and must not be "
        "interpreted as causal effects."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
