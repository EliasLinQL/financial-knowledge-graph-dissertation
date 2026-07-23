from __future__ import annotations

import unittest

import pandas as pd

from src.select_news_coverage import (
    filter_news_for_selected,
    select_companies_by_coverage,
)


class NewsCoverageSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.companies = pd.DataFrame(
            [
                {
                    "sample_order": "1",
                    "rank": "1",
                    "company_id": "C001",
                    "company_name": "First",
                    "aliases": "First",
                },
                {
                    "sample_order": "2",
                    "rank": "2",
                    "company_id": "C002",
                    "company_name": "Low coverage",
                    "aliases": "Low",
                },
                {
                    "sample_order": "3",
                    "rank": "3",
                    "company_id": "C003",
                    "company_name": "Backfill",
                    "aliases": "Backfill",
                },
            ]
        )
        self.articles = pd.DataFrame(
            [
                {
                    "article_id": "A1",
                    "publication_date": "2025-07-01T10:00:00Z",
                    "content_complete": "True",
                    "date_in_study_window": "True",
                    "analysis_ready": "True",
                    "accepted_company_ids": '["C001"]',
                    "accepted_company_count": "1",
                    "rejected_query_only_company_ids": "[]",
                },
                {
                    "article_id": "A2",
                    "publication_date": "2025-08-01T10:00:00Z",
                    "content_complete": "True",
                    "date_in_study_window": "True",
                    "analysis_ready": "True",
                    "accepted_company_ids": '["C001"]',
                    "accepted_company_count": "1",
                    "rejected_query_only_company_ids": "[]",
                },
                {
                    "article_id": "A3",
                    "publication_date": "2025-07-10T10:00:00Z",
                    "content_complete": "True",
                    "date_in_study_window": "True",
                    "analysis_ready": "True",
                    "accepted_company_ids": '["C002"]',
                    "accepted_company_count": "1",
                    "rejected_query_only_company_ids": "[]",
                },
                {
                    "article_id": "A4",
                    "publication_date": "2025-09-01T10:00:00Z",
                    "content_complete": "True",
                    "date_in_study_window": "True",
                    "analysis_ready": "True",
                    "accepted_company_ids": '["C003"]',
                    "accepted_company_count": "1",
                    "rejected_query_only_company_ids": "[]",
                },
                {
                    "article_id": "A5",
                    "publication_date": "2025-10-01T10:00:00Z",
                    "content_complete": "True",
                    "date_in_study_window": "True",
                    "analysis_ready": "True",
                    "accepted_company_ids": '["C003"]',
                    "accepted_company_count": "1",
                    "rejected_query_only_company_ids": "[]",
                },
            ]
        )
        self.links = pd.DataFrame(
            [
                {
                    "article_id": article_id,
                    "company_id": company_id,
                    "company_name": company_id,
                    "query_returned": "True",
                    "link_status": "verified_core",
                    "accepted_for_analysis": "True",
                }
                for article_id, company_id in (
                    ("A1", "C001"),
                    ("A2", "C001"),
                    ("A3", "C002"),
                    ("A4", "C003"),
                    ("A5", "C003"),
                )
            ]
        )

    def test_low_coverage_company_is_replaced_by_next_ranked_company(self) -> None:
        selected, report = select_companies_by_coverage(
            self.companies,
            self.articles,
            self.links,
            target_count=2,
            minimum_accepted_articles=2,
            minimum_active_months=2,
        )

        self.assertEqual(selected["company_id"].tolist(), ["C001", "C003"])
        self.assertEqual(selected["sample_order"].tolist(), [1, 2])
        status = report.set_index("company_id")["selection_status"].to_dict()
        self.assertEqual(status["C002"], "EXCLUDED_LOW_NEWS_COVERAGE")
        self.assertEqual(status["C003"], "SELECTED")

    def test_final_articles_are_recomputed_after_exclusion(self) -> None:
        final_articles, final_links = filter_news_for_selected(
            self.articles,
            self.links,
            {"C001", "C003"},
        )

        self.assertNotIn("C002", set(final_links["company_id"]))
        self.assertNotIn("A3", set(final_articles["article_id"]))
        self.assertTrue(final_articles["analysis_ready"].map(bool).all())


if __name__ == "__main__":
    unittest.main()
