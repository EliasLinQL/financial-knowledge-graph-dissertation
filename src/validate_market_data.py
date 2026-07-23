"""Select a configured market-cap-ranked company sample using daily OHLCV."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml
from dotenv import load_dotenv


@dataclass
class ValidationResult:
    eligible: bool
    first_date: str = ""
    last_date: str = ""
    row_count: int = 0
    maximum_gap_days: int = 0
    missing_ratio: float = 1.0
    reason: str = ""


class MarketDataError(RuntimeError):
    """Base error for failures that must not be treated as missing history."""


class MarketDataRateLimited(MarketDataError):
    """Twelve Data has temporarily rejected requests due to API limits."""


class MarketDataTransientError(MarketDataError):
    """A temporary network, TLS or upstream-server failure occurred."""


class MarketDataAuthenticationError(MarketDataError):
    """The Twelve Data credential is absent or invalid."""


class MarketDataUnavailable(RuntimeError):
    """A symbol or exchange is not available under the current data plan."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Twelve Data daily prices in ranking order and select "
            "the first eligible companies."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the YAML configuration file.",
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


def validate_candidate_universe(
    candidates: pd.DataFrame,
    target_count: int,
    ranking_snapshot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Validate candidate uniqueness, ordering and optional ranking provenance."""
    required_candidate_columns = {
        "rank",
        "company_id",
        "company_name",
        "source_ticker",
        "twelve_data_symbol",
        "twelve_data_exchange",
        "market_cap_usd",
        "ranking_snapshot_date",
    }
    missing_candidate_columns = required_candidate_columns.difference(
        candidates.columns
    )
    if missing_candidate_columns:
        missing = ", ".join(sorted(missing_candidate_columns))
        raise ValueError(f"Candidate file is missing required columns: {missing}")

    validated = candidates.copy()
    validated["rank"] = pd.to_numeric(validated["rank"], errors="coerce")
    if validated["rank"].isna().any():
        raise ValueError("Candidate file contains a non-numeric rank.")
    validated["rank"] = validated["rank"].astype(int)
    if (validated["rank"] < 1).any():
        raise ValueError("Candidate ranks must be positive integers.")
    for column in ("rank", "company_id", "source_ticker"):
        duplicates = validated.loc[
            validated[column].duplicated(keep=False), column
        ].astype(str)
        if not duplicates.empty:
            raise ValueError(
                f"Candidate file contains duplicate {column} values: "
                + ", ".join(sorted(duplicates.unique())[:10])
            )
    if len(validated) < target_count:
        raise ValueError(
            f"Candidate universe has only {len(validated)} rows, fewer than "
            f"target_company_count={target_count}. Add lower-ranked fallback "
            "companies before running market validation."
        )

    if ranking_snapshot is not None:
        required_ranking_columns = {
            "rank",
            "source_ticker",
            "market_cap_usd",
        }
        missing_ranking_columns = required_ranking_columns.difference(
            ranking_snapshot.columns
        )
        if missing_ranking_columns:
            missing = ", ".join(sorted(missing_ranking_columns))
            raise ValueError(f"Ranking snapshot is missing columns: {missing}")
        ranking = ranking_snapshot.copy()
        ranking["rank"] = pd.to_numeric(ranking["rank"], errors="coerce")
        ranking["market_cap_usd"] = pd.to_numeric(
            ranking["market_cap_usd"], errors="coerce"
        )
        if ranking[["rank", "market_cap_usd"]].isna().any().any():
            raise ValueError("Ranking snapshot contains invalid numeric values.")
        ranking["rank"] = ranking["rank"].astype(int)
        ranking = ranking.set_index("source_ticker")

        missing_tickers = sorted(
            set(validated["source_ticker"]) - set(ranking.index)
        )
        if missing_tickers:
            raise ValueError(
                "Candidate tickers absent from ranking snapshot: "
                + ", ".join(missing_tickers[:10])
            )
        for _, candidate in validated.iterrows():
            source = ranking.loc[candidate["source_ticker"]]
            if isinstance(source, pd.DataFrame):
                raise ValueError(
                    "Ranking snapshot contains duplicate source_ticker values: "
                    f"{candidate['source_ticker']}"
                )
            if int(candidate["rank"]) != int(source["rank"]):
                raise ValueError(
                    f"Rank mismatch for {candidate['source_ticker']}: "
                    f"candidate={candidate['rank']}, snapshot={source['rank']}"
                )
            candidate_cap = float(candidate["market_cap_usd"])
            source_cap = float(source["market_cap_usd"])
            tolerance = max(1.0, abs(source_cap) * 1e-10)
            if abs(candidate_cap - source_cap) > tolerance:
                raise ValueError(
                    f"Market-cap mismatch for {candidate['source_ticker']}: "
                    f"candidate={candidate_cap}, snapshot={source_cap}"
                )

    return validated.sort_values("rank").reset_index(drop=True)


def read_cached_market_data(path: Path) -> pd.DataFrame:
    data = pd.read_csv(path, index_col="Date", parse_dates=["Date"])
    data.index = pd.to_datetime(data.index, errors="coerce").normalize()
    data = data.loc[~data.index.isna()]
    data = data.loc[~data.index.duplicated(keep="last")].sort_index()
    data.index.name = "Date"
    return data


def response_is_rate_limited(code: Any, message: str) -> bool:
    text = f"{code} {message}".lower()
    indicators = ("429", "rate limit", "too many requests", "api credits")
    return any(indicator in text for indicator in indicators)


def redact_sensitive_text(value: Any, api_key: str = "") -> str:
    """Remove credentials from exceptions before they reach logs or reports."""
    text = str(value)
    if api_key:
        text = text.replace(api_key, "<redacted>")
    text = re.sub(
        r"(?i)([?&](?:api[_-]?key|apikey|token)=)[^&\s'\"]+",
        r"\1<redacted>",
        text,
    )
    return text


def download_twelve_data(
    symbol: str,
    exchange: str,
    market: dict[str, Any],
    api_key: str,
) -> pd.DataFrame:
    provider = market["twelvedata"]
    params = {
        "symbol": symbol,
        "interval": market["interval"],
        "start_date": market["download_start_date"],
        "end_date": market["end_date_inclusive"],
        "outputsize": int(provider.get("outputsize", 5000)),
        "format": "JSON",
        "apikey": api_key,
    }
    if exchange:
        params["exchange"] = exchange

    try:
        response = requests.get(
            provider["base_url"],
            params=params,
            timeout=float(provider.get("timeout_seconds", 30)),
        )
    except requests.RequestException as exc:
        safe_message = redact_sensitive_text(exc, api_key)
        raise MarketDataTransientError(f"Network error: {safe_message}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise MarketDataError(
            f"Twelve Data returned non-JSON content (HTTP {response.status_code})"
        ) from exc

    if response.status_code == 401:
        raise MarketDataAuthenticationError("Twelve Data rejected the API key")
    if response.status_code == 429:
        raise MarketDataRateLimited("Twelve Data HTTP 429 rate limit")
    if response.status_code >= 500:
        raise MarketDataTransientError(
            f"Twelve Data server error HTTP {response.status_code}"
        )

    if isinstance(payload, dict) and payload.get("status") == "error":
        code = payload.get("code", "")
        message = str(payload.get("message", "Unknown Twelve Data error"))
        if response_is_rate_limited(code, message):
            raise MarketDataRateLimited(message)
        if str(code) in {"401", "403"} or "api key" in message.lower():
            raise MarketDataAuthenticationError(message)
        if str(code) in {"400", "404"}:
            raise MarketDataUnavailable(message)
        raise MarketDataError(f"Twelve Data error {code}: {message}")

    values = payload.get("values") if isinstance(payload, dict) else None
    if not values:
        raise MarketDataUnavailable("No daily price values returned")

    data = pd.DataFrame(values)
    required_source_columns = {"datetime", "open", "high", "low", "close", "volume"}
    missing = required_source_columns.difference(data.columns)
    if missing:
        raise MarketDataError(
            "Unexpected Twelve Data response; missing fields: "
            + ", ".join(sorted(missing))
        )

    data = data.rename(
        columns={
            "datetime": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["Date"]).set_index("Date").sort_index()
    data = data.loc[~data.index.duplicated(keep="last")]
    data.index = data.index.normalize()
    data.index.name = "Date"
    return data[["Open", "High", "Low", "Close", "Volume"]]


def get_market_data(
    symbol: str,
    exchange: str,
    cache_path: Path,
    market: dict[str, Any],
    api_key: str,
) -> tuple[pd.DataFrame, str]:
    if bool(market.get("use_cached_data", True)) and cache_path.exists():
        cached = read_cached_market_data(cache_path)
        if not cached.empty:
            return cached, "cache"

    rate_limit_retry_count = int(market.get("max_rate_limit_retries", 0))
    rate_limit_backoff = float(market.get("rate_limit_backoff_seconds", 65))
    network_retry_count = int(market.get("max_network_retries", 2))
    network_backoff = float(market.get("network_backoff_seconds", 10))
    rate_limit_attempt = 0
    network_attempt = 0

    while True:
        try:
            return download_twelve_data(symbol, exchange, market, api_key), "twelvedata"
        except MarketDataRateLimited:
            if rate_limit_attempt >= rate_limit_retry_count:
                raise
            wait_seconds = rate_limit_backoff * (2**rate_limit_attempt)
            rate_limit_attempt += 1
            print(
                f"  RATE LIMITED: waiting {wait_seconds:.0f} seconds before "
                f"retry {rate_limit_attempt}/{rate_limit_retry_count}..."
            )
            time.sleep(wait_seconds)
        except MarketDataTransientError as exc:
            if network_attempt >= network_retry_count:
                raise
            wait_seconds = network_backoff * (2**network_attempt)
            network_attempt += 1
            safe_message = redact_sensitive_text(exc, api_key)
            print(
                f"  NETWORK ERROR: {safe_message}; waiting {wait_seconds:.0f} "
                f"seconds before retry {network_attempt}/{network_retry_count}..."
            )
            time.sleep(wait_seconds)


def validate_market_data(
    data: pd.DataFrame,
    requested_start: str,
    requested_end_inclusive: str,
    rules: dict[str, Any],
) -> ValidationResult:
    if data.empty:
        return ValidationResult(eligible=False, reason="No market data returned")

    required_columns = list(rules["required_columns"])
    missing_columns = [column for column in required_columns if column not in data]
    if missing_columns:
        return ValidationResult(
            eligible=False,
            row_count=len(data),
            reason=f"Missing columns: {', '.join(missing_columns)}",
        )

    first_timestamp = pd.Timestamp(data.index.min()).normalize()
    last_timestamp = pd.Timestamp(data.index.max()).normalize()
    start_timestamp = pd.Timestamp(requested_start)
    end_timestamp = pd.Timestamp(requested_end_inclusive)
    start_delay = max(0, (first_timestamp - start_timestamp).days)
    end_delay = max(0, (end_timestamp - last_timestamp).days)
    differences = data.index.to_series().diff().dt.days.dropna()
    maximum_gap = int(differences.max()) if not differences.empty else 0
    required_values = data[required_columns]
    missing_ratio = float(required_values.isna().sum().sum()) / float(
        required_values.size
    )

    reasons: list[str] = []
    if len(data) < int(rules["minimum_rows"]):
        reasons.append(f"Only {len(data)} rows; minimum is {rules['minimum_rows']}")
    if start_delay > int(rules["max_start_delay_calendar_days"]):
        reasons.append(f"Market data begins {start_delay} days after requested start")
    if end_delay > int(rules["max_end_delay_calendar_days"]):
        reasons.append(f"Market data ends {end_delay} days before requested end")
    if maximum_gap > int(rules["max_internal_gap_calendar_days"]):
        reasons.append(f"Maximum internal gap is {maximum_gap} calendar days")
    if missing_ratio > float(rules["max_missing_ratio"]):
        reasons.append(
            f"Missing-value ratio is {missing_ratio:.2%}; "
            f"maximum is {float(rules['max_missing_ratio']):.2%}"
        )

    return ValidationResult(
        eligible=not reasons,
        first_date=first_timestamp.date().isoformat(),
        last_date=last_timestamp.date().isoformat(),
        row_count=len(data),
        maximum_gap_days=maximum_gap,
        missing_ratio=missing_ratio,
        reason="; ".join(reasons) if reasons else "Eligible",
    )


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project_root = config_path.parent.parent
    load_dotenv(project_root / ".env")
    config = load_config(config_path)

    selection = config["company_selection"]
    market = config["market_data"]
    rules = market["validation"]
    outputs = config["outputs"]
    provider = market["twelvedata"]
    key_name = provider["api_key_environment_variable"]
    api_key = os.getenv(key_name, "").strip()
    if not api_key:
        print(
            f"Missing {key_name}. Copy .env.example to .env and add your new API key.",
            file=sys.stderr,
        )
        return 3

    candidate_path = resolve_path(project_root, selection["candidate_file"])
    report_path = resolve_path(project_root, outputs["selection_report"])
    selected_path = resolve_path(project_root, outputs["selected_companies"])
    eligible_pool_path = resolve_path(
        project_root,
        outputs.get("market_eligible_companies", outputs["selected_companies"]),
    )
    partial_path = resolve_path(project_root, outputs["partial_selection"])
    market_directory = resolve_path(project_root, market["output_directory"])

    target_count = int(selection["target_company_count"])
    pool_count = max(
        target_count,
        int(selection.get("market_eligible_pool_count", target_count)),
    )
    candidates = pd.read_csv(
        candidate_path,
        dtype={
            "source_ticker": str,
            "twelve_data_symbol": str,
            "twelve_data_exchange": str,
        },
    )
    ranking_snapshot: pd.DataFrame | None = None
    ranking_source_value = str(selection.get("ranking_source_file", "")).strip()
    if ranking_source_value:
        ranking_source_path = resolve_path(project_root, ranking_source_value)
        if not ranking_source_path.exists():
            raise FileNotFoundError(
                f"Ranking snapshot file not found: {ranking_source_path}"
            )
        ranking_snapshot = pd.read_csv(
            ranking_source_path,
            dtype={"source_ticker": str},
        )
    candidates = validate_candidate_universe(
        candidates,
        pool_count,
        ranking_snapshot,
    )
    print(
        f"Candidate universe validated: {len(candidates)} ranked companies; "
        f"final target={target_count}; market-eligible pool target={pool_count}."
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    eligible_pool_path.parent.mkdir(parents=True, exist_ok=True)
    market_directory.mkdir(parents=True, exist_ok=True)
    report_rows: list[dict[str, Any]] = []
    selected_indices: list[int] = []
    interrupted = False
    interruption_status = ""

    for index, company in candidates.iterrows():
        symbol = str(company["twelve_data_symbol"]).strip()
        exchange_value = company.get("twelve_data_exchange", "")
        exchange = "" if pd.isna(exchange_value) else str(exchange_value).strip()
        print(
            f"Checking rank {company['rank']}: {company['company_name']} "
            f"({symbol}, {exchange or 'default exchange'})"
        )

        safe_name = f"{symbol}_{exchange}".replace("/", "-").replace(".", "_")
        cache_path = market_directory / f"{safe_name}.csv"
        data_source = ""
        status = "SOURCE_ERROR"
        try:
            data, data_source = get_market_data(
                symbol=symbol,
                exchange=exchange,
                cache_path=cache_path,
                market=market,
                api_key=api_key,
            )
            result = validate_market_data(
                data=data,
                requested_start=market["download_start_date"],
                requested_end_inclusive=market["end_date_inclusive"],
                rules=rules,
            )
            status = "ELIGIBLE" if result.eligible else "INELIGIBLE"
            if market.get("save_downloaded_data", True) and not data.empty:
                data.to_csv(cache_path)
        except MarketDataUnavailable as exc:
            result = ValidationResult(
                eligible=False,
                reason=(
                    "Symbol/market unavailable from Twelve Data: "
                    + redact_sensitive_text(exc, api_key)
                ),
            )
            status = "INELIGIBLE"
        except MarketDataRateLimited as exc:
            result = ValidationResult(
                eligible=False,
                reason=(
                    "Twelve Data rate limit interrupted validation: "
                    + redact_sensitive_text(exc, api_key)
                ),
            )
            status = "RATE_LIMITED"
            interrupted = True
            interruption_status = status
        except MarketDataAuthenticationError as exc:
            result = ValidationResult(
                eligible=False,
                reason=redact_sensitive_text(exc, api_key),
            )
            status = "AUTHENTICATION_ERROR"
            interrupted = True
            interruption_status = status
        except MarketDataError as exc:
            result = ValidationResult(
                eligible=False,
                reason=redact_sensitive_text(exc, api_key),
            )
            status = "SOURCE_ERROR"
            interrupted = True
            interruption_status = status

        report_rows.append(
            {
                "rank": int(company["rank"]),
                "company_id": company["company_id"],
                "company_name": company["company_name"],
                "symbol": symbol,
                "exchange": exchange,
                "first_date": result.first_date,
                "last_date": result.last_date,
                "row_count": result.row_count,
                "maximum_gap_days": result.maximum_gap_days,
                "missing_ratio": result.missing_ratio,
                "status": status,
                "data_source": data_source,
                "eligible": result.eligible,
                "reason": result.reason,
            }
        )
        print(f"  {status}: {result.reason}")

        if interrupted:
            print(
                "Stopping because the data source could not complete validation. "
                "This company has not been excluded.",
                file=sys.stderr,
            )
            break
        if result.eligible:
            selected_indices.append(index)
        if len(selected_indices) == pool_count:
            break
        delay_seconds = float(market.get("request_delay_seconds", 0))
        if data_source == "twelvedata" and delay_seconds > 0:
            time.sleep(delay_seconds)

    report = pd.DataFrame(report_rows)
    eligible_pool = candidates.loc[selected_indices].copy()
    eligible_pool.insert(0, "sample_order", range(1, len(eligible_pool) + 1))
    selected_companies = eligible_pool.head(target_count).copy()
    report.to_csv(report_path, index=False, encoding="utf-8-sig")
    eligible_pool.to_csv(eligible_pool_path, index=False, encoding="utf-8-sig")
    print(f"Market-eligible company pool: {eligible_pool_path}")
    print(f"Market-eligible companies: {len(eligible_pool)}/{pool_count} requested")

    if len(selected_companies) == target_count:
        selected_companies.to_csv(selected_path, index=False, encoding="utf-8-sig")
        print(
            "Provisional market-only selection (news coverage is checked later): "
            f"{selected_path}"
        )
    else:
        selected_companies.to_csv(partial_path, index=False, encoding="utf-8-sig")
        print(f"Partial selection: {partial_path}")
    print(f"Selection report: {report_path}")
    print(f"Eligible companies selected: {len(selected_companies)}/{target_count}")

    if interrupted:
        return 2 if interruption_status == "RATE_LIMITED" else 3
    if len(eligible_pool) < target_count:
        print(
            "Not enough eligible companies were found. Add more ranked candidates "
            "or review unavailable market mappings.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
