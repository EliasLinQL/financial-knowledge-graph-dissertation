from __future__ import annotations

import unittest

import pandas as pd

from src.extract_event_candidates import (
    classify_event_span,
    event_span_candidates,
    extractive_event_title,
    stable_event_id,
)


class ExtractEventCandidatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "event_type_priority": ["regulatory_event", "corporate_event"],
            "event_keyword_rules": {
                "regulatory_event": ["tariff", "fine"],
                "corporate_event": ["revenue", "forecast", "investment"],
            },
            "default_event_type": "corporate_event",
            "headline_keyword_weight": 3,
            "trail_keyword_weight": 2,
            "body_keyword_weight": 1,
            "event_span_keyword_weight": 3,
            "maximum_event_spans_per_article_company": 2,
            "section_hint_weight": 0,
            "section_event_type_hints": {},
            "high_confidence_score": 7,
            "medium_confidence_score": 4,
        }

    def test_live_blog_event_is_grounded_in_company_sentence(self) -> None:
        article = pd.Series(
            {
                "headline": (
                    "Global markets digest: credit conditions and policy "
                    "updates"
                ),
                "trail_text": "A rolling summary of markets and policy news.",
                "body_text": (
                    "The IMF discussed credit risks. "
                    "TSMC said it expects robust AI demand and raised its "
                    "full-year revenue forecast."
                ),
            }
        )
        link = pd.Series(
            {
                "matched_core_aliases": '["TSMC"]',
                "matched_product_aliases": "[]",
                "query_returned": "true",
            }
        )

        spans = event_span_candidates(article, link, self.config)
        classification = classify_event_span(
            spans[0]["evidence_sentence"], "Business", self.config
        )

        self.assertTrue(spans[0]["evidence_sentence"].startswith("TSMC said"))
        self.assertNotIn(
            "Global markets",
            extractive_event_title(spans[0]["evidence_sentence"]),
        )
        self.assertEqual(classification["event_type"], "corporate_event")
        self.assertIn("revenue", classification["matched_event_keywords"])

    def test_distinct_evidence_spans_create_distinct_event_ids(self) -> None:
        first = stable_event_id("article-1", "TSMC raised its revenue forecast.")
        second = stable_event_id("article-1", "TSMC announced an investment.")
        repeated = stable_event_id(
            "article-1", "  TSMC raised its revenue forecast! "
        )

        self.assertNotEqual(first, second)
        self.assertEqual(first, repeated)

    def test_same_evidence_span_deduplicates_across_company_links(self) -> None:
        sentence = "Apple and Microsoft announced an investment partnership."

        self.assertEqual(
            stable_event_id("article-2", sentence),
            stable_event_id("article-2", sentence),
        )

    def test_two_distinct_company_events_in_one_article_are_retained(self) -> None:
        article = pd.Series(
            {
                "headline": "TSMC updates investors",
                "trail_text": "",
                "body_text": (
                    "TSMC announced an investment in a new fabrication plant. "
                    "TSMC raised its full-year revenue forecast."
                ),
            }
        )
        link = pd.Series(
            {
                "matched_core_aliases": '["TSMC"]',
                "matched_product_aliases": "[]",
                "query_returned": "true",
            }
        )

        spans = event_span_candidates(article, link, self.config)

        self.assertEqual(len(spans), 2)
        self.assertEqual(
            {span["event_granularity"] for span in spans},
            {"evidence_sentence"},
        )
        self.assertNotEqual(
            stable_event_id("article-3", spans[0]["evidence_sentence"]),
            stable_event_id("article-3", spans[1]["evidence_sentence"]),
        )

    def test_adjacent_sentence_window_supplies_local_event_context(self) -> None:
        article = pd.Series(
            {
                "headline": "Broadcom updates investors",
                "trail_text": "",
                "body_text": (
                    "Broadcom issued an update. "
                    "Its latest outlook missed the sales forecast."
                ),
            }
        )
        link = pd.Series(
            {
                "matched_core_aliases": '["Broadcom"]',
                "matched_product_aliases": "[]",
                "query_returned": "true",
            }
        )

        spans = event_span_candidates(article, link, self.config)

        self.assertEqual(len(spans), 1)
        self.assertEqual(
            spans[0]["event_granularity"], "evidence_sentence_window"
        )
        self.assertIn("Broadcom issued an update.", spans[0]["evidence_sentence"])
        self.assertIn("sales forecast", spans[0]["evidence_sentence"])

    def test_adjacent_unrelated_company_event_is_not_joined(self) -> None:
        article = pd.Series(
            {
                "headline": "Technology companies in focus",
                "trail_text": "",
                "body_text": (
                    "Cisco and Broadcom gained access to the new system. "
                    "Meta was fined by a regulator for a separate breach."
                ),
            }
        )
        link = pd.Series(
            {
                "matched_core_aliases": '["Broadcom"]',
                "matched_product_aliases": "[]",
                "query_returned": "true",
            }
        )

        spans = event_span_candidates(article, link, self.config)

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["event_granularity"], "evidence_sentence")
        self.assertNotIn("Meta was fined", spans[0]["evidence_sentence"])

    def test_sentence_split_handles_punctuation_before_closing_quote(self) -> None:
        article = pd.Series(
            {
                "headline": "AI shares in focus",
                "trail_text": "",
                "body_text": (
                    "Broadcom reports later, providing another look at AI demand.” "
                    "An analyst said an unrelated investment was risky."
                ),
            }
        )
        link = pd.Series(
            {
                "matched_core_aliases": '["Broadcom"]',
                "matched_product_aliases": "[]",
                "query_returned": "true",
            }
        )

        spans = event_span_candidates(article, link, self.config)

        self.assertEqual(len(spans), 1)
        self.assertNotIn("unrelated investment", spans[0]["evidence_sentence"])


if __name__ == "__main__":
    unittest.main()
