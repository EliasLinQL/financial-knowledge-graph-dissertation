from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.build_chapter4_results import (
    ANALYST_FIGURE_OUTPUTS,
    ANALYST_TABLE_OUTPUTS,
    GDS_FIGURE_OUTPUTS,
    GDS_TABLE_OUTPUTS,
    analyst_source_directory,
    blend_hex,
    build_results,
    clean_generated_result_files,
    gds_source_directory,
    nice_maximum,
    sha256_file,
    source_paths,
    truthy,
    validate_analyst_use_case_package,
    validate_gds_package,
    write_threshold_heatmap,
)
from src.query_kg import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


class Chapter4ResultsTests(unittest.TestCase):
    @staticmethod
    def _write_hashed_members(
        package_directory: Path,
        relative_paths: list[str],
    ) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for index, relative_path in enumerate(relative_paths):
            path = package_directory / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".csv":
                path.write_text(f"id,value\n{index},fixture\n", encoding="utf-8")
            else:
                path.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\"/>", encoding="utf-8")
            hashes[relative_path] = sha256_file(path)
        return hashes

    def test_truthy_accepts_serialised_boolean_variants(self) -> None:
        result = truthy(pd.Series(["true", "1", "YES", "false", "", None]))
        self.assertEqual(result.tolist(), [True, True, True, False, False, False])

    def test_chart_helpers_are_deterministic(self) -> None:
        self.assertEqual(nice_maximum(1_056), 2_000)
        self.assertEqual(blend_hex("#000000", "#FFFFFF", 0), "#000000")
        self.assertEqual(blend_hex("#000000", "#FFFFFF", 1), "#FFFFFF")
        self.assertEqual(blend_hex("#000000", "#FFFFFF", 0.5), "#808080")

    def test_threshold_heatmap_marks_the_current_setting(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "confirmation_threshold": threshold,
                    "strong_rule_focus_score": score,
                    "qualified_event_company_links": 1_100
                    - int(threshold * 100)
                    - score,
                    "current_setting": threshold == 0.35 and score == 7,
                }
                for score in [6, 7, 8]
                for threshold in [0.25, 0.35, 0.45, 0.55]
            ]
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "sensitivity.svg"
            write_threshold_heatmap(
                output,
                title="Sensitivity",
                subtitle="Test",
                frame=frame,
                footnote="All companies retained.",
            )
            svg = output.read_text(encoding="utf-8")
        self.assertIn("<svg", svg)
        self.assertIn("Current setting", svg)
        self.assertIn("#E15759", svg)
        self.assertEqual(svg.count("relationships</text>"), 12)
        current_outline = 'fill="none" stroke="#E15759" stroke-width="5"'
        self.assertIn(current_outline, svg)
        self.assertGreater(
            svg.index(current_outline),
            svg.rfind("relationships</text>"),
        )

    def test_gds_package_validation_uses_hashes_contract_and_import_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            package = project_root / "outputs" / "gds"
            output_hashes = self._write_hashed_members(
                package,
                [*GDS_TABLE_OUTPUTS, *GDS_FIGURE_OUTPUTS],
            )
            import_hashes: dict[str, str] = {}
            for name in (
                "companies.csv",
                "events.csv",
                "event_potentially_affects_company.csv",
            ):
                relative_path = Path("data") / "neo4j" / "import" / name
                path = project_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"fixture-{name}\n", encoding="utf-8")
                import_hashes[str(relative_path)] = sha256_file(path)
            manifest = {
                "output_sha256": output_hashes,
                "read_only_contract": {
                    "persisted_kg_writeback": False,
                    "temporary_projections_dropped": True,
                    "database_counts_unchanged": True,
                },
                "expected_import_artifact_sha256": import_hashes,
            }
            (package / "gds_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            loaded, paths = validate_gds_package(project_root, package)
            self.assertEqual(loaded["output_sha256"], output_hashes)
            self.assertEqual(
                len([key for key in paths if key != "gds_manifest.json"]),
                17,
            )

            tampered = package / next(iter(GDS_TABLE_OUTPUTS))
            tampered.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_gds_package(project_root, package)

    def test_analyst_package_validation_rejects_changed_database_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            package = Path(temporary_directory) / "analyst"
            output_hashes = self._write_hashed_members(
                package,
                [*ANALYST_TABLE_OUTPUTS, *ANALYST_FIGURE_OUTPUTS],
            )
            manifest_path = package / "analyst_use_case_manifest.json"
            manifest = {
                "output_sha256": output_hashes,
                "read_only_contract": {"database_counts_unchanged": True},
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded, paths = validate_analyst_use_case_package(package)
            self.assertEqual(loaded["output_sha256"], output_hashes)
            self.assertEqual(
                len(
                    [
                        key
                        for key in paths
                        if key != "analyst_use_case_manifest.json"
                    ]
                ),
                10,
            )

            manifest["read_only_contract"]["database_counts_unchanged"] = False
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unchanged database counts"):
                validate_analyst_use_case_package(package)

    def test_output_cleanup_removes_only_generated_table_and_figure_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tables = root / "tables"
            figures = root / "figures"
            tables.mkdir()
            figures.mkdir()
            (tables / "stale.csv").write_text("old", encoding="utf-8")
            (figures / "stale.svg").write_text("old", encoding="utf-8")
            (figures / "stale.png").write_bytes(b"old")
            keep = tables / "notes.txt"
            keep.write_text("keep", encoding="utf-8")

            clean_generated_result_files(tables, figures)

            self.assertFalse((tables / "stale.csv").exists())
            self.assertFalse((figures / "stale.svg").exists())
            self.assertFalse((figures / "stale.png").exists())
            self.assertTrue(keep.exists())

    @unittest.skipUnless(CONFIG_PATH.exists(), "Project configuration is unavailable")
    def test_frozen_outputs_generate_a_self_consistent_package(self) -> None:
        config = load_config(CONFIG_PATH)
        required = source_paths(PROJECT_ROOT, config)
        gds_directory = gds_source_directory(PROJECT_ROOT, config)
        analyst_directory = analyst_source_directory(PROJECT_ROOT, config)
        if not all(path.exists() for path in required.values()) or not (
            (gds_directory / "gds_manifest.json").exists()
            and (analyst_directory / "analyst_use_case_manifest.json").exists()
        ):
            self.skipTest("Frozen local result files are unavailable")

        with tempfile.TemporaryDirectory() as temporary_directory:
            result = build_results(
                CONFIG_PATH,
                Path(temporary_directory) / "chapter4",
            )
            output = Path(result["output_directory"])
            manifest = json.loads(
                (output / "results_manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(result["tables"], 33)
            self.assertEqual(result["figures"], 10)
            self.assertEqual(result["metrics"]["canonical_events"], 885)
            self.assertEqual(
                result["metrics"]["canonical_relationships"],
                1_005,
            )
            self.assertEqual(result["metrics"]["covered_companies"], 25)
            self.assertEqual(result["metrics"]["gds_logical_edge_count"], 39)
            self.assertEqual(result["metrics"]["gds_wcc_count"], 10)
            self.assertEqual(result["metrics"]["gds_isolate_count"], 8)
            self.assertEqual(result["metrics"]["analyst_use_case_count"], 5)
            self.assertFalse(
                manifest["interpretation"]["human_labelled_benchmark_available"]
            )
            self.assertFalse(
                manifest["interpretation"][
                    "ground_truth_precision_or_recall_claimed"
                ]
            )
            self.assertFalse(
                manifest["interpretation"]["market_returns_interpreted_as_causal"]
            )
            self.assertFalse(
                manifest["interpretation"]["manual_time_savings_claimed"]
            )
            self.assertIn("gds_source_manifest", manifest)
            self.assertIn("analyst_use_case_source_manifest", manifest)
            self.assertTrue(
                (
                    output
                    / "figures"
                    / "figure_4_4_threshold_sensitivity.svg"
                ).exists()
            )
            self.assertTrue(
                (output / "figures" / "figure_4_10_use_case_completeness.svg").exists()
            )
            self.assertTrue(
                (output / "tables" / "table_4_33_task_5_shared_event_pairs.csv").exists()
            )
            english_report = (output / "chapter4_results_en.md").read_text(
                encoding="utf-8-sig"
            )
            self.assertIn("GDS structural graph analysis", english_report)
            self.assertIn("Analyst use-case evaluation", english_report)


if __name__ == "__main__":
    unittest.main()
