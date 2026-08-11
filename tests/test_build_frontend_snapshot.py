from __future__ import annotations

import csv
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from src.build_frontend_snapshot import (
    SnapshotValidationError,
    assert_no_credentials,
    build_snapshot,
    parse_scalar,
    write_snapshot,
)


FIXTURE_COUNTS = {
    "companyCount": 2,
    "eventCount": 2,
    "impactCount": 3,
    "sourceArticleCount": 2,
    "marketWindowCount": 6,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def hash_outputs(root: Path, relative_names: list[str]) -> dict[str, str]:
    return {name: sha256(root / name) for name in relative_names}


def fixture_report() -> dict[str, Any]:
    impacts = [
        {
            "company_id": "C001",
            "company": "One",
            "event_id": "E1",
            "event_date": "2026-01-01",
            "event_type": "corporate_event",
            "event_title": "Event one",
            "event_summary": "Summary one",
            "relationship_evidence": "Evidence one",
            "nlp_relationship_label": "direct_target",
            "nlp_positive_probability": 0.8,
            "relationship_focus_score": 9,
            "hybrid_decision_reason": "rule_and_nlp_agree",
            "classification_confidence": "high",
            "source_event_count": 2,
            "source_article_count": 2,
            "deduplication_method": "semantic_cluster",
            "relationship_source_event_id": "SE1",
            "relationship_source_article_id": "A1",
            "relationship_source_url": "https://example.test/a1",
        },
        {
            "company_id": "C002",
            "company": "Two",
            "event_id": "E1",
            "event_date": "2026-01-01",
            "event_type": "corporate_event",
            "event_title": "Event one",
            "event_summary": "Summary one",
            "relationship_evidence": "Evidence one for two",
            "nlp_relationship_label": "direct_target",
            "nlp_positive_probability": 0.75,
            "relationship_focus_score": 7,
            "hybrid_decision_reason": "rule_and_nlp_agree",
            "classification_confidence": "high",
            "source_event_count": 2,
            "source_article_count": 2,
            "deduplication_method": "semantic_cluster",
            "relationship_source_event_id": "SE1",
            "relationship_source_article_id": "A1",
            "relationship_source_url": "https://example.test/a1",
        },
        {
            "company_id": "C001",
            "company": "One",
            "event_id": "E2",
            "event_date": "2026-02-01",
            "event_type": "regulatory_event",
            "event_title": "Event two",
            "event_summary": "Summary two",
            "relationship_evidence": "Evidence two",
            "nlp_relationship_label": "direct_subject",
            "nlp_positive_probability": 0.91,
            "relationship_focus_score": 8,
            "hybrid_decision_reason": "rule_and_nlp_agree",
            "classification_confidence": "medium",
            "source_event_count": 1,
            "source_article_count": 1,
            "deduplication_method": "singleton",
            "relationship_source_event_id": "SE2",
            "relationship_source_article_id": "A2",
            "relationship_source_url": "https://example.test/a2",
        },
    ]
    sources = [
        {
            "company_id": "C001",
            "event_id": "E1",
            "source_event_id": "SE1",
            "source_event_date": "2026-01-01",
            "source_event_title": "Source one",
            "source_evidence_span": "Evidence one",
            "source_evidence_source": "body",
            "similarity_to_representative": 1.0,
            "is_representative": True,
            "article_id": "A1",
            "article_title": "Article one",
            "publication_timestamp": "2026-01-01T00:00:00Z",
            "section_name": "Business",
            "source_url": "https://example.test/a1",
            "is_relationship_source": True,
        },
        {
            "company_id": "C002",
            "event_id": "E1",
            "source_event_id": "SE1B",
            "source_event_date": "2026-01-01",
            "source_event_title": "Source one b",
            "source_evidence_span": "Evidence one b",
            "source_evidence_source": "body",
            "similarity_to_representative": 0.9,
            "is_representative": False,
            "article_id": "A2",
            "article_title": "Article two",
            "publication_timestamp": "2026-01-01T01:00:00Z",
            "section_name": "Business",
            "source_url": "https://example.test/a2",
            "is_relationship_source": False,
        },
        {
            "company_id": "C001",
            "event_id": "E2",
            "source_event_id": "SE2",
            "source_event_date": "2026-02-01",
            "source_event_title": "Source two",
            "source_evidence_span": "Evidence two",
            "source_evidence_source": "body",
            "similarity_to_representative": 1.0,
            "is_representative": True,
            "article_id": "A2",
            "article_title": "Article two",
            "publication_timestamp": "2026-02-01T00:00:00Z",
            "section_name": "Business",
            "source_url": "https://example.test/a2",
            "is_relationship_source": True,
        },
    ]
    market = [
        {
            "company_id": "C001",
            "event_id": "E1",
            "symbol": "ONE",
            "market_observation_id": f"M{window}",
            "window_days": window,
            "baseline_date": "2026-01-01",
            "window_end_date": "2026-01-02",
            "baseline_close": 100.0,
            "window_end_close": 101.0,
            "cumulative_return": 0.01,
            "anchor_rule": "on_or_after",
            "data_source": "fixture",
            "causal_claim": False,
        }
        for window in (-7, -3, -1, 1, 3, 7)
    ]
    return {
        "metadata": {
            "generated_at_utc": "2026-08-04T00:00:00+00:00",
            "database_uri": "neo4j://127.0.0.1:7687",
            "database": "neo4j",
            "filters": {
                "company_id": None,
                "event_type": None,
                "start_date": None,
                "end_date": None,
                "minimum_nlp_probability": None,
            },
            "counts": {
                "companies": 2,
                "companies_with_events": 2,
                "canonical_events": 2,
                "event_company_links": 3,
                "source_articles": 2,
                "source_evidence_rows": 3,
                "multi_source_events": 1,
                "market_windows": 6,
                "validation_failures": 0,
            },
            "interpretation": {
                "market_returns_are_causal": False,
                "market_context_note": "Descriptive only.",
            },
        },
        "companies": [
            {
                "company_id": "C001",
                "company": "One",
                "country": "Testland",
                "source_rank": 1,
                "market_cap_usd": 1000,
                "ranking_snapshot_date": "2026-07-23",
                "symbol": "ONE",
                "event_count": 2,
            },
            {
                "company_id": "C002",
                "company": "Two",
                "country": "Testland",
                "source_rank": 2,
                "market_cap_usd": 900,
                "ranking_snapshot_date": "2026-07-23",
                "symbol": "TWO",
                "event_count": 1,
            },
        ],
        "events": impacts,
        "sources": sources,
        "market": market,
        "validations": [],
    }


class SnapshotFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.report_path = root / "analyst_report_data.json"
        self.gds_root = root / "gds"
        self.gds_manifest_path = self.gds_root / "gds_manifest.json"
        self.evaluation_root = root / "evaluation"
        self.evaluation_manifest_path = self.evaluation_root / "manifest.json"
        self.report = fixture_report()
        self._write_all()

    def _write_all(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        gds_files = {
            "tables/company_centrality.csv": [
                {
                    "company_id": "C001",
                    "company": "One",
                    "source_rank": "1",
                    "event_count": "2",
                    "coevent_degree": "1",
                    "coevent_strength": "1",
                    "is_isolate": "False",
                    "wcc_component": "WCC01",
                    "louvain_community": "LC01",
                    "page_rank": "0.75",
                },
                {
                    "company_id": "C002",
                    "company": "Two",
                    "source_rank": "2",
                    "event_count": "1",
                    "coevent_degree": "1",
                    "coevent_strength": "1",
                    "is_isolate": "False",
                    "wcc_component": "WCC01",
                    "louvain_community": "LC01",
                    "page_rank": "0.25",
                },
            ],
            "tables/company_coevent_edges.csv": [
                {
                    "company1_id": "C001",
                    "company1": "One",
                    "company2_id": "C002",
                    "company2": "Two",
                    "shared_event_count": "1",
                    "meets_support_threshold": "True",
                }
            ],
            "tables/company_node_similarity.csv": [
                {
                    "company1_id": "C001",
                    "company2_id": "C002",
                    "similarity": "0.5",
                    "shared_event_count": "1",
                    "event_union_count": "2",
                    "meets_support_threshold": "True",
                }
            ],
            "tables/wcc_components.csv": [
                {
                    "wcc_component": "WCC01",
                    "company_count": "2",
                    "company_ids": "C001 | C002",
                    "companies": "One | Two",
                }
            ],
            "tables/weighted_louvain_community_summary.csv": [
                {
                    "louvain_community": "LC01",
                    "company_count": "2",
                    "company_ids": "C001 | C002",
                    "companies": "One | Two",
                }
            ],
            "tables/unweighted_louvain_community_summary.csv": [
                {
                    "unweighted_louvain_community": "ULC01",
                    "company_count": "2",
                    "company_ids": "C001 | C002",
                    "companies": "One | Two",
                }
            ],
            "tables/threshold_sensitivity.csv": [
                {
                    "minimum_shared_events": "1",
                    "logical_edge_count": "1",
                    "density": "1.0",
                    "component_count": "1",
                    "largest_component_size": "2",
                    "isolate_count": "0",
                }
            ],
            "tables/gds_algorithm_summary.csv": [
                {
                    "algorithm": "WCC",
                    "mode": "stream",
                    "result_count": "1",
                    "summary": "one component",
                }
            ],
            "notes.md": [{"text": "fixture"}],
        }
        for name, rows in gds_files.items():
            if name.endswith(".csv"):
                write_csv(self.gds_root / name, rows)
            else:
                path = self.gds_root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
        gds_manifest = {
            "generated_at_utc": "2026-08-04T00:00:00+00:00",
            "read_only_contract": {
                "persisted_kg_writeback": False,
                "algorithm_modes": ["stream", "stats", "estimate"],
                "temporary_projections_dropped": True,
                "database_counts_unchanged": True,
                "catalog_before": [],
                "catalog_after": [],
            },
            "summary": {
                "database_node_count": 9,
                "database_relationship_count": 12,
                "company_count": 2,
                "bipartite_node_count": 4,
                "bipartite_relationship_count": 3,
                "logical_edge_count": 1,
                "projected_relationship_count": 2,
                "wcc_count": 1,
                "largest_wcc_size": 2,
                "isolate_count": 0,
                "weighted_community_count": 1,
                "support_threshold": 1,
            },
            "output_sha256": hash_outputs(self.gds_root, list(gds_files)),
        }
        self.gds_manifest_path.write_text(
            json.dumps(gds_manifest, indent=2), encoding="utf-8"
        )

        evaluation_files = {
            "tables/use_case_summary.csv": [
                {
                    "task_id": "T1",
                    "title_en": "Screen",
                    "parameters_json": "{}",
                    "result_rows": "2",
                    "source_join_automated": "True",
                    "coverage_pct": "100.0",
                }
            ],
            "tables/task_performance.csv": [
                {
                    "task_id": "T1",
                    "result_rows": "2",
                    "median_client_ms": "4.5",
                    "result_hash_stable": "True",
                }
            ],
            "tables/task_quality_checks.csv": [
                {"task_id": "T1", "value_pct": "100.0", "status": "PASS"}
            ],
            "tables/task_run_timings.csv": [
                {"task_id": "T1", "run_index": "1", "is_warmup": "False"}
            ],
            "tables/task_1.csv": [
                {"company_id": "C001", "event_count": "2"},
                {"company_id": "C002", "event_count": "1"},
            ],
            "tables/task_2.csv": [{"company_id": "C001", "source_urls": "[]"}],
            "tables/task_3.csv": [{"event_id": "E2", "source_report_matched": "True"}],
            "tables/task_4.csv": [
                {"event_id": "E1", "window_days": "1", "causal_claim": "False"}
            ],
            "tables/task_5.csv": [
                {
                    "company1_id": "C001",
                    "company2_id": "C002",
                    "shared_event_count": "1",
                    "shared_event_ids": '["E1"]',
                }
            ],
            "report.md": [{"text": "fixture"}],
        }
        for name, rows in evaluation_files.items():
            if name.endswith(".csv"):
                write_csv(self.evaluation_root / name, rows)
            else:
                path = self.evaluation_root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
        fingerprint = "a" * 64
        graph_snapshot = {
            "nodes_by_label": {
                "Article": 2,
                "Company": 2,
                "Event": 2,
                "MarketObservation": 6,
            },
            "relationships_by_type": {"POTENTIALLY_AFFECTS": 3},
            "total_nodes": 9,
            "total_relationships": 12,
            "sha256": fingerprint,
        }
        tasks = [
            {
                "task_id": f"T{index}",
                "title_en": f"Task {index}",
                "title_cn": f"任务 {index}",
                "scope": "fixture",
                "parameters": {"limit": index},
                "query_sha256": str(index) * 64,
                "output": f"tables/task_{index}.csv",
            }
            for index in range(1, 6)
        ]
        evaluation_manifest = {
            "generated_at_utc": "2026-08-04T00:00:00+00:00",
            "timing_protocol": {"warmup_runs": 1, "measured_runs": 1},
            "tasks": tasks,
            "graph_state": {
                "before": graph_snapshot,
                "after": graph_snapshot,
                "unchanged": True,
            },
            "read_only_contract": {
                "persisted_kg_writeback": False,
                "routing_control": "READ",
                "cypher_write_operations_used": False,
                "database_counts_unchanged": True,
                "database_fingerprint_unchanged": True,
            },
            "summary": {
                "use_case_count": 5,
                "all_tasks_succeeded": True,
                "all_result_hashes_stable": True,
                "all_row_counts_stable": True,
                "graph_state_unchanged": True,
                "quality_checks_passed": 1,
                "quality_checks_total": 1,
                "company_scope_count": 2,
            },
            "output_sha256": hash_outputs(self.evaluation_root, list(evaluation_files)),
        }
        self.evaluation_manifest_path.write_text(
            json.dumps(evaluation_manifest, indent=2), encoding="utf-8"
        )

    def refresh_gds_manifest_hashes(self) -> None:
        manifest = json.loads(self.gds_manifest_path.read_text(encoding="utf-8"))
        manifest["output_sha256"] = {
            name: sha256(self.gds_root / name) for name in manifest["output_sha256"]
        }
        self.gds_manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


class BuildFrontendSnapshotTests(unittest.TestCase):
    def test_type_parsing_is_strict_and_preserves_identifiers(self) -> None:
        self.assertEqual(parse_scalar("25"), 25)
        self.assertEqual(parse_scalar("0.538"), 0.538)
        self.assertIs(parse_scalar("False"), False)
        self.assertEqual(parse_scalar('["E1", "E2"]'), ["E1", "E2"])
        self.assertEqual(parse_scalar("C001"), "C001")
        self.assertEqual(parse_scalar("2026-01-01"), "2026-01-01")

    def test_builds_normalised_credential_free_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = SnapshotFixture(Path(temporary))
            snapshot = build_snapshot(
                fixture.report_path,
                fixture.gds_manifest_path,
                fixture.evaluation_manifest_path,
                expected_counts=FIXTURE_COUNTS,
            )
            self.assertEqual(snapshot["scope"]["snapshotId"], "a" * 64)
            self.assertEqual(snapshot["summary"]["companyCount"], 2)
            self.assertEqual(len(snapshot["events"]), 2)
            self.assertEqual(len(snapshot["impacts"]), 3)
            self.assertEqual(len(snapshot["sources"]), 3)
            self.assertEqual(len(snapshot["market"]), 6)
            self.assertEqual(
                snapshot["visualizations"]["timeSeries"]["months"],
                ["2026-01", "2026-02"],
            )
            company_series = {
                row["companyId"]: row["values"]
                for row in snapshot["visualizations"]["timeSeries"]["companies"]
            }
            self.assertEqual(company_series["C001"], [1, 1])
            self.assertEqual(company_series["C002"], [1, 0])
            self.assertEqual(
                snapshot["visualizations"]["sharedEventMatrix"]["maximumSharedEventCount"],
                1,
            )
            self.assertEqual(
                snapshot["visualizations"]["sharedEventMatrix"]["cells"][0]["similarity"],
                0.5,
            )
            self.assertIs(snapshot["network"]["nodes"][0]["isIsolate"], False)
            self.assertIsInstance(snapshot["network"]["nodes"][0]["pageRank"], float)
            self.assertEqual(
                snapshot["evaluation"]["taskResults"]["T5"][0]["sharedEventIds"],
                ["E1"],
            )
            self.assertTrue(all(row["causalClaim"] is False for row in snapshot["market"]))
            assert_no_credentials(snapshot)
            serialised = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn("neo4j://", serialised)
            self.assertNotIn('"databaseUri"', serialised)
            self.assertNotIn('"password"', serialised.casefold())

            output = fixture.root / "dashboard.json"
            write_snapshot(snapshot, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), snapshot)

    def test_rejects_any_manifest_output_hash_tampering(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = SnapshotFixture(Path(temporary))
            target = fixture.gds_root / "notes.md"
            target.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(SnapshotValidationError, "hash mismatch"):
                build_snapshot(
                    fixture.report_path,
                    fixture.gds_manifest_path,
                    fixture.evaluation_manifest_path,
                    expected_counts=FIXTURE_COUNTS,
                )

    def test_rejects_cross_source_count_mismatch(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = SnapshotFixture(Path(temporary))
            fixture.report["metadata"]["counts"]["canonical_events"] = 3
            fixture.report_path.write_text(
                json.dumps(fixture.report, indent=2), encoding="utf-8"
            )
            with self.assertRaisesRegex(SnapshotValidationError, "canonical events"):
                build_snapshot(
                    fixture.report_path,
                    fixture.gds_manifest_path,
                    fixture.evaluation_manifest_path,
                    expected_counts=FIXTURE_COUNTS,
                )

    def test_rejects_write_contract_and_causal_market_claim(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = SnapshotFixture(Path(temporary))
            manifest = json.loads(
                fixture.evaluation_manifest_path.read_text(encoding="utf-8")
            )
            manifest["read_only_contract"]["cypher_write_operations_used"] = True
            fixture.evaluation_manifest_path.write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            with self.assertRaisesRegex(SnapshotValidationError, "cypher_write_operations"):
                build_snapshot(
                    fixture.report_path,
                    fixture.gds_manifest_path,
                    fixture.evaluation_manifest_path,
                    expected_counts=FIXTURE_COUNTS,
                )

        with TemporaryDirectory() as temporary:
            fixture = SnapshotFixture(Path(temporary))
            fixture.report["market"][0]["causal_claim"] = True
            fixture.report_path.write_text(
                json.dumps(fixture.report, indent=2), encoding="utf-8"
            )
            with self.assertRaisesRegex(SnapshotValidationError, "causal_claim=false"):
                build_snapshot(
                    fixture.report_path,
                    fixture.gds_manifest_path,
                    fixture.evaluation_manifest_path,
                    expected_counts=FIXTURE_COUNTS,
                )


if __name__ == "__main__":
    unittest.main()
