from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from src.enrich_events_nlp import (
    Prediction,
    choose_relation_prediction,
    decide_hybrid_relationship,
    evidence_candidates,
    prediction_cache_key,
    resolve_compute_device,
)


class EnrichEventsNlpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "accepted_relationship_labels": [
                "direct_subject",
                "direct_target",
                "materially_affected",
            ],
            "confirmation_threshold": 0.35,
            "enable_nlp_rescue": True,
            "rescue_threshold": 0.65,
            "event_confidence_threshold": 0.35,
            "strong_rule_focus_score": 7,
            "minimum_rescue_focus_score": 2,
        }
        self.event_prediction = Prediction(
            "regulatory_event",
            0.7,
            {"regulatory_event": 0.7, "corporate_event": 0.3},
        )

    def test_evidence_candidates_prioritise_existing_evidence(self) -> None:
        article = pd.Series(
            {
                "headline": "Regulator opens inquiry into Apple",
                "trail_text": "Apple says it will cooperate.",
                "body_text": "Other firms were mentioned. Apple faces an inquiry.",
            }
        )
        link = pd.Series(
            {
                "company_name": "Apple Inc.",
                "matched_core_aliases": '["Apple"]',
                "matched_product_aliases": "[]",
                "evidence_sentence": "Apple faces an inquiry.",
            }
        )

        candidates = evidence_candidates(article, link, maximum=3, maximum_chars=200)

        self.assertEqual(candidates[0], "Apple faces an inquiry.")
        self.assertEqual(len(candidates), 3)

    def test_rule_and_nlp_agreement_is_retained(self) -> None:
        relation = Prediction(
            "direct_target",
            0.6,
            {"direct_target": 0.6, "incidental_mention": 0.4},
        )

        admitted, reason = decide_hybrid_relationship(
            True,
            5,
            self.event_prediction,
            relation,
            0.6,
            self.config,
        )

        self.assertTrue(admitted)
        self.assertEqual(reason, "rule_and_nlp_agree")

    def test_nlp_can_reject_weak_rule_candidate(self) -> None:
        relation = Prediction(
            "incidental_mention",
            0.7,
            {"incidental_mention": 0.7, "direct_subject": 0.3},
        )

        admitted, reason = decide_hybrid_relationship(
            True,
            5,
            self.event_prediction,
            relation,
            0.3,
            self.config,
        )

        self.assertFalse(admitted)
        self.assertEqual(reason, "nlp_rejected_rule_candidate")

    def test_nlp_can_rescue_strong_semantic_candidate(self) -> None:
        relation = Prediction(
            "materially_affected",
            0.72,
            {"materially_affected": 0.72, "market_context": 0.28},
        )

        admitted, reason = decide_hybrid_relationship(
            False,
            2,
            self.event_prediction,
            relation,
            0.72,
            self.config,
        )

        self.assertTrue(admitted)
        self.assertEqual(reason, "nlp_rescued_rule_candidate")

    def test_rescue_is_disabled_by_default_for_precision(self) -> None:
        relation = Prediction(
            "materially_affected",
            0.9,
            {"materially_affected": 0.9, "market_context": 0.1},
        )
        config = dict(self.config)
        config["enable_nlp_rescue"] = False

        admitted, reason = decide_hybrid_relationship(
            False,
            7,
            self.event_prediction,
            relation,
            0.9,
            config,
        )

        self.assertFalse(admitted)
        self.assertEqual(reason, "rule_and_nlp_do_not_support")

    def test_evidence_ranking_uses_positive_semantic_probability(self) -> None:
        predictions = [
            Prediction(
                "incidental_mention",
                0.8,
                {"incidental_mention": 0.8, "direct_subject": 0.2},
            ),
            Prediction(
                "direct_subject",
                0.6,
                {"direct_subject": 0.6, "incidental_mention": 0.4},
            ),
        ]

        sentence, prediction, positive = choose_relation_prediction(
            ["incidental", "direct"], predictions, {"direct_subject"}
        )

        self.assertEqual(sentence, "direct")
        self.assertEqual(prediction.label, "direct_subject")
        self.assertAlmostEqual(positive, 0.6)

    def test_prediction_cache_key_is_deterministic(self) -> None:
        first = prediction_cache_key(
            "model", "task", "text", {"a": "label"}, "This is {}."
        )
        second = prediction_cache_key(
            "model", "task", "text", {"a": "label"}, "This is {}."
        )
        changed = prediction_cache_key(
            "model", "task", "other", {"a": "label"}, "This is {}."
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_cpu_device_is_explicit(self) -> None:
        fake_torch = SimpleNamespace(__version__="test")
        with patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertEqual(resolve_compute_device(-1), ("cpu", "test"))

    def test_cuda_device_is_described(self) -> None:
        fake_cuda = SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_name=lambda device: "Test GPU",
        )
        fake_torch = SimpleNamespace(__version__="test+cuda", cuda=fake_cuda)
        with patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertEqual(
                resolve_compute_device(0),
                ("cuda:0 (Test GPU)", "test+cuda"),
            )

    def test_unavailable_cuda_does_not_silently_fall_back_to_cpu(self) -> None:
        fake_cuda = SimpleNamespace(is_available=lambda: False)
        fake_torch = SimpleNamespace(__version__="test+cpu", cuda=fake_cuda)
        with patch.dict(sys.modules, {"torch": fake_torch}):
            with self.assertRaisesRegex(RuntimeError, "requires CUDA"):
                resolve_compute_device(0)


if __name__ == "__main__":
    unittest.main()
