from __future__ import annotations

import unittest
from types import SimpleNamespace

import pandas as pd

from src.export_analyst_report import (
    build_metadata,
    event_counts_by_company,
    filtered_query_parameters,
    markdown_briefing,
    validate_probability,
)
from src.query_kg import ConnectionSettings


class ExportAnalystReportTests(unittest.TestCase):
    def test_probability_and_date_filters_are_validated(self) -> None:
        self.assertEqual(validate_probability(0.35), 0.35)
        self.assertIsNone(validate_probability(None))
        with self.assertRaises(ValueError):
            validate_probability(1.01)

        parameters = filtered_query_parameters(
            SimpleNamespace(
                company_id="C007",
                event_type="regulatory_event",
                start_date="2025-07-01",
                end_date="2026-06-30",
                minimum_nlp_probability=0.5,
            )
        )
        self.assertEqual(parameters["company_id"], "C007")
        self.assertEqual(parameters["minimum_nlp_probability"], 0.5)

    def test_company_summary_retains_zero_event_companies(self) -> None:
        companies = pd.DataFrame(
            [
                {"company_id": "C001", "company": "One", "source_rank": 1},
                {"company_id": "C002", "company": "Two", "source_rank": 2},
            ]
        )
        events = pd.DataFrame(
            [
                {"company_id": "C001", "event_id": "E1"},
                {"company_id": "C001", "event_id": "E2"},
            ]
        )
        summary = event_counts_by_company(companies, events)
        by_company = summary.set_index("company_id")

        self.assertEqual(int(by_company.loc["C001", "event_count"]), 2)
        self.assertEqual(int(by_company.loc["C002", "event_count"]), 0)

    def test_metadata_counts_unique_and_multi_source_events(self) -> None:
        settings = ConnectionSettings(
            uri="neo4j://127.0.0.1:7687",
            database="neo4j",
            user="neo4j",
            password_environment_variable="NEO4J_PASSWORD",
        )
        companies = pd.DataFrame(
            [{"company_id": "C001"}, {"company_id": "C002"}]
        )
        events = pd.DataFrame(
            [
                {
                    "company_id": "C001",
                    "event_id": "E1",
                    "source_event_count": 2,
                    "nlp_model_name": "model",
                    "nlp_model_revision": "rev",
                },
                {
                    "company_id": "C002",
                    "event_id": "E1",
                    "source_event_count": 2,
                    "nlp_model_name": "model",
                    "nlp_model_revision": "rev",
                },
                {
                    "company_id": "C001",
                    "event_id": "E2",
                    "source_event_count": 1,
                    "nlp_model_name": "model",
                    "nlp_model_revision": "rev",
                },
            ]
        )
        sources = pd.DataFrame(
            [{"article_id": "A1"}, {"article_id": "A1"}, {"article_id": "A2"}]
        )
        market = pd.DataFrame([{"window_days": 1}, {"window_days": 3}])
        validations = pd.DataFrame([{"status": "PASS"}, {"status": "FAIL"}])

        metadata = build_metadata(
            settings=settings,
            parameters={
                "company_id": None,
                "event_type": None,
                "start_date": None,
                "end_date": None,
                "minimum_nlp_probability": None,
            },
            companies=companies,
            events=events,
            sources=sources,
            market=market,
            validations=validations,
        )

        self.assertEqual(metadata["counts"]["canonical_events"], 2)
        self.assertEqual(metadata["counts"]["event_company_links"], 3)
        self.assertEqual(metadata["counts"]["multi_source_events"], 1)
        self.assertEqual(metadata["counts"]["source_articles"], 2)
        self.assertEqual(metadata["counts"]["validation_failures"], 1)
        self.assertFalse(metadata["interpretation"]["market_returns_are_causal"])

    def test_bilingual_briefings_retain_non_causal_warning(self) -> None:
        metadata = {
            "generated_at_utc": "2026-07-29T00:00:00+00:00",
            "filters": {
                "company_id": None,
                "event_type": None,
                "start_date": None,
                "end_date": None,
                "minimum_nlp_probability": None,
            },
            "counts": {
                "companies": 1,
                "companies_with_events": 1,
                "canonical_events": 1,
                "event_company_links": 1,
                "source_articles": 1,
                "multi_source_events": 0,
                "validation_failures": 0,
            },
        }
        company_summary = pd.DataFrame(
            [{"company": "Example", "event_count": 1}]
        )
        events = pd.DataFrame([{"event_type": "corporate_event"}])
        market = pd.DataFrame(
            [{"window_days": 1, "cumulative_return": 0.01}]
        )

        chinese = markdown_briefing(
            language="zh",
            metadata=metadata,
            company_summary=company_summary,
            events=events,
            market=market,
        )
        english = markdown_briefing(
            language="en",
            metadata=metadata,
            company_summary=company_summary,
            events=events,
            market=market,
        )

        self.assertIn("不代表事件导致了该收益", chinese)
        self.assertIn("do not establish that an event caused a return", english)


if __name__ == "__main__":
    unittest.main()
