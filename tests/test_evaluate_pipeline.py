from __future__ import annotations

import unittest

import pandas as pd

from src.evaluate_pipeline import (
    company_stage_summary,
    deduplication_summary,
    simulate_relationship_gate,
    stage_summary,
    threshold_sensitivity,
)


def event_frame(
    event_ids: list[str],
    recommended_ids: set[str],
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_id": event_id,
                "event_type": "corporate_event",
                "recommended_for_graph": str(
                    event_id in recommended_ids
                ).lower(),
                "nlp_event_score": "0.8",
            }
            for event_id in event_ids
        ]
    )


def link_frame(
    rows: list[tuple[str, str, bool]],
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_id": event_id,
                "company_id": company_id,
                "event_type": "corporate_event",
                "recommended_for_graph": str(recommended).lower(),
            }
            for event_id, company_id, recommended in rows
        ]
    )


class PipelineEvaluationTests(unittest.TestCase):
    def test_stage_summary_reports_retention_without_accuracy_claim(self) -> None:
        rule_events = event_frame(["E1", "E2"], {"E1", "E2"})
        hybrid_events = event_frame(["E1", "E2"], {"E1"})
        rule_links = link_frame(
            [("E1", "C1", True), ("E2", "C2", True)]
        )
        hybrid_links = link_frame(
            [("E1", "C1", True), ("E2", "C2", False)]
        )

        summary = stage_summary(
            [
                ("rule", rule_events, rule_links, "rule"),
                ("hybrid", hybrid_events, hybrid_links, "hybrid"),
            ]
        )

        self.assertEqual(int(summary.iloc[0]["qualified_events"]), 2)
        self.assertEqual(int(summary.iloc[1]["qualified_events"]), 1)
        self.assertEqual(
            float(summary.iloc[1]["relationship_retention_from_previous_pct"]),
            50.0,
        )

    def test_gate_accepts_confirmed_or_strong_positive_labels_only(self) -> None:
        links = pd.DataFrame(
            [
                {
                    "event_id": "E1",
                    "company_id": "C1",
                    "rule_recommended_for_graph": "true",
                    "relationship_focus_score": "5",
                    "nlp_relationship_label": "direct_target",
                    "nlp_relationship_score": "0.40",
                    "nlp_positive_probability": "0.8",
                },
                {
                    "event_id": "E2",
                    "company_id": "C1",
                    "rule_recommended_for_graph": "true",
                    "relationship_focus_score": "7",
                    "nlp_relationship_label": "direct_subject",
                    "nlp_relationship_score": "0.20",
                    "nlp_positive_probability": "0.7",
                },
                {
                    "event_id": "E3",
                    "company_id": "C1",
                    "rule_recommended_for_graph": "true",
                    "relationship_focus_score": "9",
                    "nlp_relationship_label": "incidental_mention",
                    "nlp_relationship_score": "0.95",
                    "nlp_positive_probability": "0.1",
                },
                {
                    "event_id": "E4",
                    "company_id": "C1",
                    "rule_recommended_for_graph": "false",
                    "relationship_focus_score": "9",
                    "nlp_relationship_label": "direct_target",
                    "nlp_relationship_score": "0.95",
                    "nlp_positive_probability": "0.95",
                },
            ]
        )

        result = simulate_relationship_gate(
            links,
            {"direct_subject", "direct_target", "materially_affected"},
            confirmation_threshold=0.35,
            strong_rule_focus_score=7,
        )

        self.assertEqual(result.tolist(), [True, True, False, False])

    def test_company_summary_keeps_zero_counts_explicit(self) -> None:
        companies = pd.DataFrame(
            [
                {"company_id": "C1", "company_name": "One"},
                {"company_id": "C2", "company_name": "Two"},
            ]
        )
        rule = link_frame([("E1", "C1", True), ("E2", "C2", True)])
        hybrid = link_frame([("E1", "C1", True), ("E2", "C2", False)])
        canonical = link_frame([("CE1", "C1", True)])

        summary = company_stage_summary(
            companies,
            {"rule": rule, "hybrid": hybrid, "canonical": canonical},
        ).set_index("company_id")

        self.assertEqual(int(summary.loc["C2", "rule_events"]), 1)
        self.assertEqual(int(summary.loc["C2", "hybrid_events"]), 0)
        self.assertEqual(int(summary.loc["C2", "canonical_events"]), 0)

    def test_threshold_grid_reconstructs_saved_decisions(self) -> None:
        companies = pd.DataFrame(
            [{"company_id": "C1", "company_name": "One"}]
        )
        events = event_frame(["E1", "E2"], {"E1", "E2"})
        links = pd.DataFrame(
            [
                {
                    "event_id": "E1",
                    "company_id": "C1",
                    "event_type": "corporate_event",
                    "recommended_for_graph": "true",
                    "rule_recommended_for_graph": "true",
                    "relationship_focus_score": "5",
                    "nlp_relationship_label": "direct_target",
                    "nlp_relationship_score": "0.40",
                    "nlp_positive_probability": "0.8",
                    "hybrid_decision_reason": "rule_and_nlp_agree",
                },
                {
                    "event_id": "E2",
                    "company_id": "C1",
                    "event_type": "corporate_event",
                    "recommended_for_graph": "false",
                    "rule_recommended_for_graph": "true",
                    "relationship_focus_score": "5",
                    "nlp_relationship_label": "direct_target",
                    "nlp_relationship_score": "0.20",
                    "nlp_positive_probability": "0.6",
                    "hybrid_decision_reason": "nlp_rejected_rule_candidate",
                },
            ]
        )
        nlp_config = {
            "accepted_relationship_labels": [
                "direct_subject",
                "direct_target",
                "materially_affected",
            ],
            "confirmation_threshold": 0.35,
            "strong_rule_focus_score": 7,
            "enable_nlp_rescue": False,
        }
        sensitivity, _ = threshold_sensitivity(
            events,
            links,
            companies,
            nlp_config,
            {
                "confirmation_thresholds": [0.35],
                "strong_rule_focus_scores": [7],
            },
        )

        self.assertEqual(len(sensitivity), 1)
        self.assertTrue(bool(sensitivity.iloc[0]["matches_current_output"]))
        self.assertEqual(
            int(sensitivity.iloc[0]["qualified_event_company_links"]), 1
        )

    def test_deduplication_summary_reports_source_mentions(self) -> None:
        events = pd.DataFrame(
            [
                {
                    "event_id": "CE1",
                    "event_type": "corporate_event",
                    "recommended_for_graph": "true",
                    "source_event_count": "2",
                    "deduplication_min_similarity": "0.8",
                },
                {
                    "event_id": "CE2",
                    "event_type": "corporate_event",
                    "recommended_for_graph": "true",
                    "source_event_count": "1",
                    "deduplication_min_similarity": "1.0",
                },
            ]
        )
        links = link_frame([("CE1", "C1", True), ("CE2", "C1", True)])

        summary = deduplication_summary(events, links).set_index("metric")

        self.assertEqual(int(summary.loc["source_event_mentions", "value"]), 3)
        self.assertEqual(int(summary.loc["duplicates_removed", "value"]), 1)
        self.assertEqual(
            int(summary.loc["multi_source_canonical_events", "value"]), 1
        )


if __name__ == "__main__":
    unittest.main()
