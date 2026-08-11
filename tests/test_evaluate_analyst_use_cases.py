from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.evaluate_analyst_use_cases import (
    EvaluationConfig,
    build_manifest,
    collect_output_hashes,
    completeness_svg,
    ensure_read_only_query,
    evaluation_config,
    evaluation_report,
    finalize_manifest,
    frame_sha256,
    latency_svg,
    percentile,
    performance_table,
    safe_pct,
    summary_table,
    task_definitions,
    task_quality_checks,
    validate_evaluation_config,
)


def sample_config(output_directory: Path, *, target_count: int = 1) -> EvaluationConfig:
    return EvaluationConfig(
        output_directory=output_directory,
        warmup_runs=1,
        measured_runs=2,
        target_company_count=target_count,
        study_start_date="2025-07-01",
        study_end_date="2026-06-30",
        tsmc_company_id="C007",
        alphabet_company_id="C003",
        regulatory_event_type="regulatory_event",
        minimum_nlp_probability=0.8,
        shared_event_support_threshold=2,
        expected_market_windows=(-7, -3, -1, 1, 3, 7),
    )


def complete_results() -> dict[str, pd.DataFrame]:
    return {
        "T1": pd.DataFrame(
            [
                {
                    "company_id": "C001",
                    "company": "One",
                    "source_rank": 1,
                    "symbol": "ONE",
                    "event_count": 1,
                    "evidenced_event_count": 1,
                    "coverage_status": "covered",
                }
            ]
        ),
        "T2": pd.DataFrame(
            [
                {
                    "company_id": "C007",
                    "event_id": "E1",
                    "evidence_sentence": "Evidence",
                    "matching_source_count": 1,
                    "source_url_count": 1,
                }
            ]
        ),
        "T3": pd.DataFrame(
            [
                {
                    "company_id": "C001",
                    "event_id": "E1",
                    "evidence_sentence": "Evidence",
                    "source_report_matched": True,
                    "source_url": "https://example.test/e1",
                }
            ]
        ),
        "T4": pd.DataFrame(
            [
                {
                    "event_id": "E1",
                    "window_days": window,
                    "evidence_sentence": "Evidence",
                    "source_report_matched": True,
                    "source_url": "https://example.test/e1",
                    "causal_claim": False,
                }
                for window in (-7, -3, -1, 1, 3, 7)
            ]
        ),
        "T5": pd.DataFrame(
            [
                {
                    "company1_id": "C001",
                    "company2_id": "C002",
                    "shared_event_count": 2,
                    "evidenced_shared_event_count": 2,
                    "sourced_shared_event_count": 2,
                    "shared_event_ids": ["E2", "E1"],
                }
            ]
        ),
    }


class EvaluateAnalystUseCasesTests(unittest.TestCase):
    def test_config_defaults_and_validation(self) -> None:
        root = Path("C:/project")
        value = evaluation_config(
            {
                "study": {
                    "news_start_date": "2025-07-01",
                    "news_end_date": "2026-06-30",
                },
                "company_selection": {"target_company_count": 25},
            },
            root,
        )
        self.assertEqual(value.warmup_runs, 2)
        self.assertEqual(value.measured_runs, 10)
        self.assertEqual(value.tsmc_company_id, "C007")
        self.assertEqual(value.alphabet_company_id, "C003")
        self.assertEqual(value.expected_market_windows, (-7, -3, -1, 1, 3, 7))
        self.assertEqual(
            value.output_directory,
            root / "outputs/analyst_use_case_evaluation",
        )

        invalid = sample_config(Path("out"))
        invalid = EvaluationConfig(**{**invalid.__dict__, "measured_runs": 0})
        with self.assertRaises(ValueError):
            validate_evaluation_config(invalid)

    def test_registry_contains_five_parameterised_read_only_tasks(self) -> None:
        tasks = task_definitions(sample_config(Path("out")))
        self.assertEqual([task.task_id for task in tasks], ["T1", "T2", "T3", "T4", "T5"])
        self.assertEqual(tasks[1].parameters["company_id"], "C007")
        self.assertEqual(tasks[3].parameters["company_id"], "C003")
        self.assertEqual(tasks[4].parameters["support_threshold"], 2)
        for task in tasks:
            ensure_read_only_query(task.query)
        with self.assertRaises(ValueError):
            ensure_read_only_query("MATCH (n) SET n.changed = true RETURN n")
        with self.assertRaises(ValueError):
            ensure_read_only_query("CALL gds.pageRank.write('g')")

    def test_frame_hash_is_stable_across_row_and_list_order(self) -> None:
        first = pd.DataFrame(
            [
                {"id": "B", "values": [3, 1]},
                {"id": "A", "values": [2, 1]},
            ]
        )
        second = pd.DataFrame(
            [
                {"id": "A", "values": [1, 2]},
                {"id": "B", "values": [1, 3]},
            ]
        )
        self.assertEqual(frame_sha256(first), frame_sha256(second))

    def test_percentile_and_safe_percentage(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0], 50), 2.0)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0], 95), 2.9)
        self.assertEqual(safe_pct(1, 4), 25.0)
        self.assertIsNone(safe_pct(0, 0))

    def test_complete_synthetic_tasks_pass_quality_checks(self) -> None:
        config = sample_config(Path("out"))
        checks = task_quality_checks(config, complete_results(), True)
        self.assertFalse((checks["status"] == "FAIL").any())
        provenance = checks.loc[
            checks["metric_class"] == "provenance_completeness"
        ]
        self.assertTrue((provenance["value_pct"] == 100.0).all())
        graph_check = checks.loc[
            checks["check_id"] == "graph_state_unchanged"
        ].iloc[0]
        self.assertEqual(graph_check["status"], "PASS")

    def test_missing_provenance_is_reported_without_claiming_precision(self) -> None:
        results = complete_results()
        results["T3"].loc[0, "source_report_matched"] = False
        checks = task_quality_checks(sample_config(Path("out")), results, True)
        row = checks.loc[
            (checks["task_id"] == "T3")
            & (checks["metric_class"] == "provenance_completeness")
        ].iloc[0]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("not external recall", row["limitation"])

    def test_performance_summary_and_use_case_summary_contract(self) -> None:
        config = sample_config(Path("out"))
        tasks = task_definitions(config)
        timing_rows = []
        for task_index, task in enumerate(tasks, start=1):
            for run_index, elapsed in enumerate((task_index, task_index + 1), start=1):
                timing_rows.append(
                    {
                        "task_id": task.task_id,
                        "run_index": run_index,
                        "is_warmup": False,
                        "client_elapsed_ms": float(elapsed),
                        "server_available_ms": float(elapsed) / 2,
                        "server_consumed_ms": 0.0,
                        "result_rows": len(complete_results()[task.task_id]),
                        "result_sha256": f"hash-{task.task_id}",
                    }
                )
        performance = performance_table(tasks, pd.DataFrame(timing_rows), 1, 2)
        checks = task_quality_checks(config, complete_results(), True)
        summary = summary_table(tasks, complete_results(), performance, checks)
        self.assertEqual(len(summary), 5)
        self.assertEqual(
            list(summary.columns),
            [
                "task_id",
                "title_en",
                "title_cn",
                "scope",
                "parameters_json",
                "workflow_query_steps",
                "manual_join_steps",
                "manual_calculation_steps",
                "source_join_automated",
                "aggregation_automated",
                "export_automated",
                "result_rows",
                "primary_units",
                "coverage_numerator",
                "coverage_denominator",
                "coverage_pct",
                "evidence_numerator",
                "evidence_denominator",
                "evidence_completeness_pct",
                "provenance_numerator",
                "provenance_denominator",
                "provenance_completeness_pct",
                "median_client_ms",
                "p95_client_ms",
                "status",
            ],
        )
        self.assertTrue((summary["workflow_query_steps"] == 1).all())
        self.assertTrue((summary["manual_join_steps"] == 0).all())

    def test_bilingual_reports_and_svg_retain_boundaries(self) -> None:
        config = sample_config(Path("out"))
        tasks = task_definitions(config)
        timings = pd.DataFrame(
            [
                {
                    "task_id": task.task_id,
                    "run_index": 1,
                    "is_warmup": False,
                    "client_elapsed_ms": 2.0,
                    "server_available_ms": 1.0,
                    "server_consumed_ms": 0.0,
                    "result_rows": len(complete_results()[task.task_id]),
                    "result_sha256": task.task_id,
                }
                for task in tasks
            ]
        )
        performance = performance_table(tasks, timings, 0, 1)
        checks = task_quality_checks(config, complete_results(), True)
        summary = summary_table(tasks, complete_results(), performance, checks)
        chinese = evaluation_report("zh", config, summary, checks, True)
        english = evaluation_report("en", config, summary, checks, True)
        self.assertIn("不等于针对外部新闻总体的 precision、recall", chinese)
        self.assertIn("没有设置人工工作流基线", chinese)
        self.assertNotIn("�", chinese)
        self.assertIn("not external precision, recall", english)
        self.assertIn("No human-workflow baseline", english)
        self.assertIn("localhost", english)
        self.assertIn("<svg", latency_svg(summary))
        self.assertIn("Internal completeness", completeness_svg(summary))

    def test_finalize_manifest_hashes_existing_artifacts_without_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "tables").mkdir()
            (output / "figures").mkdir()
            (output / "tables/example.csv").write_text("a\n1\n", encoding="utf-8")
            (output / "report.md").write_text("report", encoding="utf-8")
            (output / "figures/chart.svg").write_text("<svg/>", encoding="utf-8")
            (output / "figures/chart.png").write_bytes(b"png")
            manifest_path = output / "analyst_use_case_manifest.json"
            manifest_path.write_text(
                json.dumps({"output_sha256": {}}), encoding="utf-8"
            )

            finalize_manifest(output)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(manifest["output_sha256"]),
                {
                    "tables/example.csv",
                    "report.md",
                    "figures/chart.svg",
                    "figures/chart.png",
                },
            )
            self.assertEqual(
                manifest["output_sha256"], collect_output_hashes(output)
            )

    def test_manifest_exposes_the_read_only_contract_used_by_chapter4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            config_path = output / "config.yaml"
            script_path = output / "evaluator.py"
            config_path.write_text("study: {}\n", encoding="utf-8")
            script_path.write_text("# evaluator\n", encoding="utf-8")
            config = sample_config(output)
            tasks = task_definitions(config)
            results = complete_results()
            timings = pd.DataFrame(
                [
                    {
                        "task_id": task.task_id,
                        "run_index": 1,
                        "is_warmup": False,
                        "client_elapsed_ms": 2.0,
                        "server_available_ms": 1.0,
                        "server_consumed_ms": 0.0,
                        "result_rows": len(results[task.task_id]),
                        "result_sha256": task.task_id,
                    }
                    for task in tasks
                ]
            )
            performance = performance_table(tasks, timings, 0, 1)
            checks = task_quality_checks(config, results, True)
            summary = summary_table(tasks, results, performance, checks)
            state = {"total_nodes": 3, "total_relationships": 2, "sha256": "x"}
            manifest = build_manifest(
                config_path=config_path,
                script_path=script_path,
                evaluation=config,
                settings=SimpleNamespace(uri="neo4j://localhost", database="neo4j"),
                tasks=tasks,
                performance=performance,
                summary=summary,
                checks=checks,
                before=state,
                after=state,
                environment={},
                output_directory=output,
            )

        self.assertFalse(manifest["read_only_contract"]["persisted_kg_writeback"])
        self.assertEqual(manifest["read_only_contract"]["routing_control"], "READ")
        self.assertTrue(
            manifest["read_only_contract"]["database_counts_unchanged"]
        )


if __name__ == "__main__":
    unittest.main()
