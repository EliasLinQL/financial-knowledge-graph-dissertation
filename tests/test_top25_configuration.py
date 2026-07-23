from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from src.collect_guardian_news import collection_windows
from src.validate_market_data import validate_candidate_universe


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Top25ConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config_path = PROJECT_ROOT / "config" / "config.yaml"
        cls.config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    def test_primary_scope_is_top25_for_twelve_months(self) -> None:
        study = self.config["study"]
        selection = self.config["company_selection"]
        start = date.fromisoformat(study["news_start_date"])
        end = date.fromisoformat(study["news_end_date"])

        self.assertEqual(selection["target_company_count"], 25)
        self.assertGreaterEqual(selection["market_eligible_pool_count"], 25)
        self.assertEqual(
            selection["news_coverage"]["minimum_accepted_articles"],
            12,
        )
        self.assertEqual(
            selection["news_coverage"]["minimum_active_months"],
            6,
        )
        self.assertEqual(study["sample_label"], "top25_12m")
        self.assertEqual(
            collection_windows(start.isoformat(), end.isoformat(), "monthly"),
            collection_windows("2025-07-01", "2026-06-30", "monthly"),
        )
        self.assertEqual(len(collection_windows(
            start.isoformat(), end.isoformat(), "monthly"
        )), 12)

    def test_curated_candidates_reconcile_to_frozen_ranking_snapshot(self) -> None:
        selection = self.config["company_selection"]
        candidates = pd.read_csv(
            PROJECT_ROOT / selection["candidate_file"],
            dtype={
                "source_ticker": str,
                "twelve_data_symbol": str,
                "twelve_data_exchange": str,
            },
        )
        ranking = pd.read_csv(
            PROJECT_ROOT / selection["ranking_source_file"],
            dtype={"source_ticker": str},
        )

        validated = validate_candidate_universe(
            candidates,
            selection["target_company_count"],
            ranking,
        )

        self.assertEqual(len(validated), 40)
        self.assertEqual(validated.iloc[0]["source_ticker"], "NASDAQ-NVDA")
        self.assertEqual(validated.iloc[-1]["source_ticker"], "NYSE-KO")

    def test_guardian_capacity_does_not_truncate_theoretical_result_set(self) -> None:
        mode = self.config["news_data"]["collection_modes"]["full"]
        pool_count = self.config["company_selection"][
            "market_eligible_pool_count"
        ]
        windows = collection_windows(
            self.config["study"]["news_start_date"],
            self.config["study"]["news_end_date"],
            mode["window_strategy"],
        )
        theoretical_maximum = (
            pool_count
            * len(windows)
            * mode["per_company_per_window"]
        )

        self.assertGreaterEqual(
            mode["global_unique_article_limit"],
            theoretical_maximum,
        )
        self.assertLessEqual(
            len(windows) * pool_count,
            self.config["news_data"]["maximum_logical_requests_per_run"],
        )
        self.assertLess(
            self.config["news_data"]["maximum_api_attempts_per_run"],
            500,
        )


if __name__ == "__main__":
    unittest.main()
