from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.query_kg import (
    comparison_rows,
    expected_graph_counts,
    validate_iso_date,
)


class QueryKgTests(unittest.TestCase):
    def test_expected_counts_are_derived_from_import_package(self) -> None:
        row_counts = {
            "articles.csv": 2,
            "assets.csv": 2,
            "companies.csv": 10,
            "events.csv": 2,
            "industries.csv": 1,
            "market_observations.csv": 6,
            "sectors.csv": 1,
            "company_belongs_to_industry.csv": 10,
            "event_has_market_observation.csv": 6,
            "asset_has_market_observation.csv": 6,
            "company_issues_asset.csv": 2,
            "article_mentions_company.csv": 2,
            "industry_part_of_sector.csv": 1,
            "event_potentially_affects_company.csv": 2,
            "article_reports_event.csv": 2,
        }
        with tempfile.TemporaryDirectory() as directory:
            import_directory = Path(directory)
            for filename, row_count in row_counts.items():
                rows = ["id"] + [str(index) for index in range(row_count)]
                (import_directory / filename).write_text(
                    "\n".join(rows) + "\n",
                    encoding="utf-8",
                )
            nodes, relationships = expected_graph_counts(import_directory)

        self.assertEqual(nodes["Company"], 10)
        self.assertGreater(nodes["Event"], 0)
        self.assertEqual(nodes["Article"], nodes["Event"])
        self.assertEqual(
            nodes["MarketObservation"],
            relationships["POTENTIALLY_AFFECTS"] * 3,
        )
        self.assertEqual(
            relationships["HAS_MARKET_OBSERVATION"],
            nodes["MarketObservation"] * 2,
        )

    def test_comparison_rows_detect_mismatch_and_unexpected_metric(self) -> None:
        rows = comparison_rows(
            "node_count",
            {"Company": 10},
            {"Company": 9, "Unexpected": 1},
        )
        by_metric = {row["metric"]: row for row in rows}

        self.assertEqual(by_metric["Company"]["status"], "FAIL")
        self.assertEqual(by_metric["Unexpected"]["expected"], 0)
        self.assertEqual(by_metric["Unexpected"]["status"], "FAIL")

    def test_iso_date_validation(self) -> None:
        self.assertEqual(validate_iso_date("2026-06-30", "date"), "2026-06-30")
        self.assertIsNone(validate_iso_date(None, "date"))
        with self.assertRaises(ValueError):
            validate_iso_date("30/06/2026", "date")


if __name__ == "__main__":
    unittest.main()
