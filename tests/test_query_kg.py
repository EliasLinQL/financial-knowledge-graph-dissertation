from __future__ import annotations

import unittest
from pathlib import Path

from src.query_kg import (
    comparison_rows,
    expected_graph_counts,
    validate_iso_date,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class QueryKgTests(unittest.TestCase):
    def test_expected_counts_are_derived_from_import_package(self) -> None:
        nodes, relationships = expected_graph_counts(
            PROJECT_ROOT / "data" / "neo4j" / "import"
        )

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
