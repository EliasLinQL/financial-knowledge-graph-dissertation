from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.collect_guardian_news import (
    ApiRequestBudget,
    GuardianRateLimitError,
    collection_windows,
    required_network_requests,
    validate_collection_capacity,
)


class CollectGuardianNewsTests(unittest.TestCase):
    def test_top25_study_period_has_twelve_monthly_windows(self) -> None:
        windows = collection_windows(
            "2025-07-01",
            "2026-06-30",
            "monthly",
        )

        self.assertEqual(len(windows), 12)
        self.assertEqual(windows[0], ("2025-07-01", "2025-07-31"))
        self.assertEqual(windows[-1], ("2026-06-01", "2026-06-30"))

    def test_collection_capacity_covers_every_company_window(self) -> None:
        validate_collection_capacity(
            company_count=35,
            window_count=12,
            per_window_limit=8,
            global_unique_article_limit=3400,
        )

    def test_collection_capacity_rejects_rank_order_truncation(self) -> None:
        with self.assertRaisesRegex(ValueError, "too small"):
            validate_collection_capacity(
                company_count=35,
                window_count=12,
                per_window_limit=8,
                global_unique_article_limit=3359,
            )

    def test_request_preflight_counts_only_missing_or_refreshed_windows(self) -> None:
        windows = [
            ("2025-07-01", "2025-07-31"),
            ("2025-08-01", "2025-08-31"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            raw_directory = Path(directory)
            (raw_directory / "C001_2025-07-01_2025-07-31.json").write_text(
                "{}",
                encoding="utf-8",
            )

            requests = required_network_requests(
                company_ids=["C001", "C002"],
                windows=windows,
                raw_directory=raw_directory,
                refresh_all=False,
                refresh_company_ids={"C002"},
                use_cached_responses=True,
            )

        self.assertEqual(requests, 3)

    def test_api_attempt_budget_counts_retries_and_stops_before_overrun(self) -> None:
        budget = ApiRequestBudget(maximum_attempts=2)

        budget.consume()
        budget.consume()

        with self.assertRaisesRegex(GuardianRateLimitError, "budget exhausted"):
            budget.consume()
        self.assertEqual(budget.attempts_used, 2)


if __name__ == "__main__":
    unittest.main()
