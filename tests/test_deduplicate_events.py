from __future__ import annotations

import unittest

import pandas as pd

from src.deduplicate_events import cluster_events, stable_canonical_event_id


CONFIG = {
    "maximum_date_difference_days": 7,
    "minimum_text_similarity": 0.72,
    "minimum_token_containment": 0.45,
    "word_similarity_weight": 0.75,
    "character_similarity_weight": 0.25,
    "require_same_event_type": True,
    "require_shared_company": True,
}


def event(
    event_id: str,
    article_id: str,
    day: str,
    evidence: str,
    *,
    event_type: str = "corporate_event",
    recommended: str = "true",
) -> dict[str, str]:
    return {
        "event_id": event_id,
        "article_id": article_id,
        "event_date": day,
        "publication_timestamp": f"{day}T09:00:00Z",
        "event_title": evidence,
        "event_summary": evidence,
        "evidence_span": evidence,
        "evidence_source": "body_text",
        "event_granularity": "evidence_sentence",
        "event_type": event_type,
        "event_score": "8",
        "recommended_for_graph": recommended,
    }


def link(
    event_id: str,
    article_id: str,
    company_id: str,
    evidence: str,
    *,
    recommended: str = "true",
    positive_probability: str = "0.80",
) -> dict[str, str]:
    return {
        "event_id": event_id,
        "article_id": article_id,
        "company_id": company_id,
        "company_name": company_id,
        "relationship_type": "POTENTIALLY_AFFECTS",
        "evidence_sentence": evidence,
        "relationship_focus_score": "8",
        "recommended_for_graph": recommended,
        "nlp_positive_probability": positive_probability,
        "nlp_relationship_score": positive_probability,
        "link_confidence": "high",
    }


class DeduplicateEventsTests(unittest.TestCase):
    def run_cluster(
        self,
        event_rows: list[dict[str, str]],
        link_rows: list[dict[str, str]],
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        return cluster_events(
            pd.DataFrame(event_rows),
            pd.DataFrame(link_rows),
            CONFIG,
        )

    def test_exact_cross_article_mentions_merge_and_keep_provenance(self) -> None:
        evidence = "Nvidia agreed to invest $5bn in Intel after signing a deal."
        events, links, mentions, _ = self.run_cluster(
            [
                event("EVT_A", "article-a", "2026-01-10", evidence),
                event("EVT_B", "article-b", "2026-01-11", evidence),
            ],
            [
                link("EVT_A", "article-a", "C001", evidence),
                link("EVT_B", "article-b", "C001", evidence),
            ],
        )

        recommended = events[
            events["recommended_for_graph"].astype(str).str.casefold() == "true"
        ]
        self.assertEqual(len(recommended), 1)
        self.assertEqual(int(recommended.iloc[0]["source_event_count"]), 2)
        self.assertEqual(int(recommended.iloc[0]["source_article_count"]), 2)
        self.assertEqual(len(mentions), 2)
        self.assertEqual(mentions["canonical_event_id"].nunique(), 1)
        self.assertEqual(len(links), 1)
        self.assertEqual(int(links.iloc[0]["source_relationship_count"]), 2)

    def test_different_facts_for_same_company_do_not_merge(self) -> None:
        first = "Tesla approved a new compensation package for its chief executive."
        second = "Tesla recalled vehicles after a battery software fault."
        events, _, _, _ = self.run_cluster(
            [
                event("EVT_A", "article-a", "2026-01-10", first),
                event("EVT_B", "article-b", "2026-01-10", second),
            ],
            [
                link("EVT_A", "article-a", "C011", first),
                link("EVT_B", "article-b", "C011", second),
            ],
        )
        self.assertEqual(len(events), 2)

    def test_shared_company_gate_prevents_text_only_merge(self) -> None:
        evidence = "The company raised its annual revenue forecast after strong sales."
        events, _, _, _ = self.run_cluster(
            [
                event("EVT_A", "article-a", "2026-01-10", evidence),
                event("EVT_B", "article-b", "2026-01-10", evidence),
            ],
            [
                link("EVT_A", "article-a", "C001", evidence),
                link("EVT_B", "article-b", "C002", evidence),
            ],
        )
        self.assertEqual(len(events), 2)

    def test_mentions_from_same_article_remain_distinct(self) -> None:
        evidence = "Microsoft announced a $10bn investment in cloud infrastructure."
        events, _, _, _ = self.run_cluster(
            [
                event("EVT_A", "article-a", "2026-01-10", evidence),
                event("EVT_B", "article-a", "2026-01-10", evidence),
            ],
            [
                link("EVT_A", "article-a", "C004", evidence),
                link("EVT_B", "article-a", "C004", evidence),
            ],
        )
        self.assertEqual(len(events), 2)

    def test_temporal_gate_prevents_distant_merge(self) -> None:
        evidence = "Apple reported record quarterly revenue from iPhone sales."
        events, _, _, _ = self.run_cluster(
            [
                event("EVT_A", "article-a", "2026-01-01", evidence),
                event("EVT_B", "article-b", "2026-02-01", evidence),
            ],
            [
                link("EVT_A", "article-a", "C002", evidence),
                link("EVT_B", "article-b", "C002", evidence),
            ],
        )
        self.assertEqual(len(events), 2)

    def test_rejected_mentions_are_not_merged_into_qualified_cluster(self) -> None:
        evidence = "Amazon announced a new cloud-computing investment."
        events, _, mentions, _ = self.run_cluster(
            [
                event("EVT_A", "article-a", "2026-01-10", evidence),
                event(
                    "EVT_B",
                    "article-b",
                    "2026-01-10",
                    evidence,
                    recommended="false",
                ),
            ],
            [
                link("EVT_A", "article-a", "C005", evidence),
                link(
                    "EVT_B",
                    "article-b",
                    "C005",
                    evidence,
                    recommended="false",
                ),
            ],
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(mentions["canonical_event_id"].nunique(), 2)

    def test_best_relationship_source_and_timestamp_are_retained(self) -> None:
        first = "Meta agreed to buy millions of Nvidia AI chips."
        second = first
        _, links, _, _ = self.run_cluster(
            [
                event("EVT_A", "article-a", "2026-01-10", first),
                event("EVT_B", "article-b", "2026-01-11", second),
            ],
            [
                link(
                    "EVT_A",
                    "article-a",
                    "C001",
                    first,
                    positive_probability="0.72",
                ),
                link(
                    "EVT_B",
                    "article-b",
                    "C001",
                    second,
                    positive_probability="0.94",
                ),
            ],
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links.iloc[0]["source_event_id"], "EVT_B")
        self.assertEqual(links.iloc[0]["article_id"], "article-b")
        self.assertEqual(
            links.iloc[0]["relationship_publication_timestamp"],
            "2026-01-11T09:00:00Z",
        )

    def test_canonical_id_is_input_order_independent(self) -> None:
        self.assertEqual(
            stable_canonical_event_id(["EVT_B", "EVT_A"]),
            stable_canonical_event_id(["EVT_A", "EVT_B"]),
        )


if __name__ == "__main__":
    unittest.main()
