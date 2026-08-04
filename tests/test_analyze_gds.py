from __future__ import annotations

import json
import os
import unittest
from dataclasses import is_dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import src.analyze_gds as analyze_gds
from src.analyze_gds import (
    GdsSettings,
    average_ranks,
    build_structural_metrics,
    canonical_group_labels,
    canonical_pair,
    deduplicate_similarity_rows,
    gds_settings,
    spearman,
    validate_query_contract,
)


def valid_config() -> dict:
    return {
        "gds_analysis": {
            "output_directory": "outputs/gds_analysis",
            "graph_name_prefix": "financial_kg_gds",
            "concurrency": 1,
            "support_threshold": 2,
            "sensitivity_thresholds": [1, 2, 3, 5],
            "node_similarity": {
                "top_k": 24,
                "similarity_cutoff": 0.01,
                "similarity_metric": "JACCARD",
                "degree_cutoff": 1,
            },
            "louvain": {
                "max_levels": 10,
                "max_iterations": 10,
                "tolerance": 0.0001,
            },
            "pagerank": {
                "max_iterations": 20,
                "damping_factor": 0.85,
                "tolerance": 1.0e-7,
            },
        }
    }


class GdsSettingsTests(unittest.TestCase):
    def test_valid_nested_configuration_builds_a_dataclass(self) -> None:
        settings = gds_settings(valid_config())

        self.assertIsInstance(settings, GdsSettings)
        self.assertTrue(is_dataclass(settings))
        self.assertEqual(settings.concurrency, 1)
        self.assertEqual(settings.top_k, 24)
        self.assertAlmostEqual(settings.similarity_cutoff, 0.01)
        self.assertEqual(settings.similarity_metric, "JACCARD")
        self.assertEqual(settings.degree_cutoff, 1)
        self.assertEqual(settings.support_threshold, 2)
        self.assertEqual(settings.sensitivity_thresholds, (1, 2, 3, 5))
        self.assertEqual(settings.louvain_max_levels, 10)
        self.assertEqual(settings.louvain_max_iterations, 10)
        self.assertEqual(settings.pagerank_max_iterations, 20)
        self.assertAlmostEqual(settings.pagerank_damping_factor, 0.85)

    def test_configuration_rejects_values_outside_algorithm_boundaries(self) -> None:
        invalid_cases = [
            ("concurrency", 0),
            ("top_k", 0),
            ("similarity_cutoff", -0.01),
            ("similarity_cutoff", 1.01),
            ("similarity_metric", "COSINE"),
            ("degree_cutoff", 0),
            ("support_threshold", 0),
            ("sensitivity_thresholds", []),
            ("max_levels", 0),
            ("louvain_max_iterations", 0),
            ("louvain_tolerance", 0),
            ("max_iterations", 0),
            ("damping_factor", 0.0),
            ("damping_factor", 1.01),
            ("pagerank_tolerance", 0),
        ]

        for field, value in invalid_cases:
            with self.subTest(field=field, value=value):
                config = valid_config()
                if field == "concurrency":
                    config["gds_analysis"][field] = value
                elif field in {
                    "top_k",
                    "similarity_cutoff",
                    "similarity_metric",
                    "degree_cutoff",
                }:
                    config["gds_analysis"]["node_similarity"][field] = value
                elif field in {"support_threshold", "sensitivity_thresholds"}:
                    config["gds_analysis"][field] = value
                elif field == "max_levels":
                    config["gds_analysis"]["louvain"][field] = value
                elif field == "louvain_max_iterations":
                    config["gds_analysis"]["louvain"]["max_iterations"] = value
                elif field == "louvain_tolerance":
                    config["gds_analysis"]["louvain"]["tolerance"] = value
                elif field == "pagerank_tolerance":
                    config["gds_analysis"]["pagerank"]["tolerance"] = value
                else:
                    config["gds_analysis"]["pagerank"][field] = value
                with self.assertRaises(ValueError):
                    gds_settings(config)


class GdsPureFunctionTests(unittest.TestCase):
    def test_canonical_pair_is_order_independent(self) -> None:
        self.assertEqual(canonical_pair("C009", "C003"), ("C003", "C009"))
        self.assertEqual(canonical_pair("C003", "C009"), ("C003", "C009"))
        self.assertEqual(canonical_pair("C003", "C003"), ("C003", "C003"))

    def test_similarity_rows_are_canonicalised_deduplicated_and_sorted(self) -> None:
        rows = [
            {
                "company1_id": "C009",
                "company1": "Meta",
                "company2_id": "C003",
                "company2": "Alphabet",
                "similarity": 0.40,
            },
            {
                "company1_id": "C003",
                "company1": "Alphabet",
                "company2_id": "C009",
                "company2": "Meta",
                "similarity": 0.42,
            },
            {
                "company1_id": "C004",
                "company1": "Microsoft",
                "company2_id": "C003",
                "company2": "Alphabet",
                "similarity": 0.30,
            },
            {
                "company1_id": "C003",
                "company1": "Alphabet",
                "company2_id": "C003",
                "company2": "Alphabet",
                "similarity": 1.0,
            },
        ]

        result = deduplicate_similarity_rows(rows)

        self.assertEqual(len(result), 2)
        self.assertEqual(
            [(row["company1_id"], row["company2_id"]) for row in result],
            [("C003", "C009"), ("C003", "C004")],
        )
        self.assertAlmostEqual(result[0]["similarity"], 0.42)
        self.assertEqual(result[0]["company1"], "Alphabet")
        self.assertEqual(result[0]["company2"], "Meta")

    def test_average_ranks_and_spearman_handle_ties(self) -> None:
        self.assertEqual(average_ranks([30, 10, 20, 20]), [4.0, 1.0, 2.5, 2.5])
        self.assertAlmostEqual(
            spearman([1, 2, 2, 4], [10, 20, 20, 40]),
            1.0,
        )
        self.assertAlmostEqual(
            spearman([1, 2, 2, 4], [40, 20, 20, 10]),
            -1.0,
        )
        self.assertIsNone(spearman([1, 1, 1], [2, 3, 4]))
        with self.assertRaises(ValueError):
            spearman([1, 2], [1])

    def test_structural_metrics_include_all_companies_and_keep_isolates(self) -> None:
        companies = [
            {"company_id": "C001", "company": "Alpha", "event_count": 4},
            {"company_id": "C002", "company": "Beta", "event_count": 3},
            {"company_id": "C003", "company": "Gamma", "event_count": 2},
            {"company_id": "C004", "company": "Delta", "event_count": 1},
        ]
        edges = [
            {
                "company1_id": "C001",
                "company2_id": "C002",
                "shared_event_count": 2,
            },
            {
                "company1_id": "C003",
                "company2_id": "C001",
                "shared_event_count": 1,
            },
        ]

        result = build_structural_metrics(companies, edges)
        by_company = {row["company_id"]: row for row in result}

        self.assertEqual(set(by_company), {"C001", "C002", "C003", "C004"})
        self.assertEqual(by_company["C001"]["coevent_degree"], 2)
        self.assertEqual(by_company["C001"]["coevent_strength"], 3)
        self.assertEqual(by_company["C002"]["coevent_degree"], 1)
        self.assertEqual(by_company["C002"]["coevent_strength"], 2)
        self.assertEqual(by_company["C003"]["coevent_degree"], 1)
        self.assertEqual(by_company["C003"]["coevent_strength"], 1)
        self.assertEqual(by_company["C004"]["coevent_degree"], 0)
        self.assertEqual(by_company["C004"]["coevent_strength"], 0)
        self.assertTrue(by_company["C004"]["is_isolate"])
        self.assertFalse(by_company["C001"]["is_isolate"])

    def test_group_labels_are_stable_when_raw_algorithm_ids_change(self) -> None:
        first = canonical_group_labels(
            {"C003": 90, "C001": 12, "C004": 7, "C002": 12},
            prefix="community",
        )
        second = canonical_group_labels(
            {"C004": 44, "C002": 3, "C001": 3, "C003": 81},
            prefix="community",
        )

        self.assertEqual(first, second)
        self.assertEqual(first["C001"], first["C002"])
        self.assertNotEqual(first["C001"], first["C003"])
        self.assertNotEqual(first["C003"], first["C004"])
        self.assertTrue(all(label.startswith("community") for label in first.values()))

    def test_cypher_contract_contains_no_database_writes(self) -> None:
        self.assertIsNone(validate_query_contract())
        with patch.object(
            analyze_gds,
            "DATABASE_READ_QUERIES",
            ["MATCH (c:Company) SET c.test_flag = true RETURN c"],
        ):
            with self.assertRaises(AssertionError):
                validate_query_contract()
        with patch.object(
            analyze_gds,
            "GDS_CATALOG_QUERIES",
            ["MATCH (c:Company) SET c.test_flag = true RETURN c"],
        ):
            with self.assertRaises(AssertionError):
                validate_query_contract()

    def test_manifest_finalization_hashes_rendered_png(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            (output / "tables").mkdir()
            (output / "figures").mkdir()
            (output / "tables" / "result.csv").write_text(
                "value\n1\n", encoding="utf-8"
            )
            (output / "figures" / "figure.svg").write_text(
                "<svg/>", encoding="utf-8"
            )
            (output / "figures" / "figure.png").write_bytes(b"png")
            (output / "gds_manifest.json").write_text(
                '{"output_sha256": {}}', encoding="utf-8"
            )

            analyze_gds.finalize_manifest(output)

            manifest = json.loads(
                (output / "gds_manifest.json").read_text(encoding="utf-8")
            )
            self.assertIn("figures\\figure.png", manifest["output_sha256"])
            self.assertIn("tables\\result.csv", manifest["output_sha256"])
            self.assertIn("manifest_finalized_at_utc", manifest)


@unittest.skipUnless(
    os.getenv("RUN_NEO4J_GDS_INTEGRATION_TESTS") == "1",
    "Set RUN_NEO4J_GDS_INTEGRATION_TESTS=1 to use the live Neo4j/GDS instance.",
)
class GdsIntegrationTests(unittest.TestCase):
    def test_live_analysis_preserves_database_and_cleans_catalog(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output = analyze_gds.run_analysis(
                Path("config/config.yaml"), Path(temporary_directory)
            )
            manifest = json.loads(
                (output / "gds_manifest.json").read_text(encoding="utf-8")
            )

            self.assertTrue(
                manifest["read_only_contract"]["database_counts_unchanged"]
            )
            self.assertTrue(
                manifest["read_only_contract"]["temporary_projections_dropped"]
            )
            self.assertEqual(
                set(manifest["read_only_contract"]["temporary_projection_names"])
                & set(manifest["read_only_contract"]["catalog_after"]),
                set(),
            )
            self.assertEqual(manifest["summary"]["company_count"], 25)
            self.assertEqual(manifest["summary"]["logical_edge_count"], 39)
            self.assertEqual(
                manifest["summary"]["projected_relationship_count"], 78
            )
            self.assertEqual(manifest["summary"]["node_similarity_pair_count"], 39)


if __name__ == "__main__":
    unittest.main()
