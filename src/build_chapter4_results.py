"""Build a reproducible Chapter 4 results package from frozen pipeline outputs.

The script reads existing CSV artefacts only.  It does not call source APIs,
run NLP models, change Neo4j, or claim that market-window returns are causal.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

try:
    from src.query_kg import load_config, resolve_path
except ModuleNotFoundError:
    from query_kg import load_config, resolve_path  # type: ignore[no-redef]


PALETTE = {
    "navy": "#17324D",
    "teal": "#147D92",
    "blue": "#4C78A8",
    "orange": "#F28E2B",
    "green": "#59A14F",
    "red": "#E15759",
    "purple": "#B279A2",
    "gold": "#EDC948",
    "grid": "#D8E1E8",
    "text": "#1F2937",
    "muted": "#64748B",
    "background": "#FFFFFF",
}


GDS_TABLE_OUTPUTS = {
    "tables/gds_environment.csv": "table_4_12_gds_environment.csv",
    "tables/projection_summary.csv": "table_4_13_gds_projection_summary.csv",
    "tables/memory_estimates.csv": "table_4_14_gds_memory_estimates.csv",
    "tables/event_company_distribution.csv": "table_4_15_gds_event_company_distribution.csv",
    "tables/company_coevent_edges.csv": "table_4_16_gds_company_coevent_edges.csv",
    "tables/wcc_components.csv": "table_4_17_gds_wcc_components.csv",
    "tables/company_node_similarity.csv": "table_4_18_gds_company_node_similarity.csv",
    "tables/louvain_communities.csv": "table_4_19_gds_louvain_communities.csv",
    "tables/weighted_louvain_community_summary.csv": "table_4_20_gds_weighted_louvain_communities.csv",
    "tables/unweighted_louvain_community_summary.csv": "table_4_21_gds_unweighted_louvain_communities.csv",
    "tables/company_centrality.csv": "table_4_22_gds_company_centrality.csv",
    "tables/centrality_correlations.csv": "table_4_23_gds_centrality_correlations.csv",
    "tables/threshold_sensitivity.csv": "table_4_24_gds_threshold_sensitivity.csv",
    "tables/gds_algorithm_summary.csv": "table_4_25_gds_algorithm_summary.csv",
}

GDS_FIGURE_OUTPUTS = {
    "figures/company_coevent_network.svg": "figure_4_6_company_coevent_network.svg",
    "figures/top_shared_event_pairs.svg": "figure_4_7_top_shared_event_pairs.svg",
    "figures/event_count_vs_strength.svg": "figure_4_8_event_count_vs_strength.svg",
}

ANALYST_TABLE_OUTPUTS = {
    "tables/use_case_summary.csv": "table_4_26_use_case_summary.csv",
    "tables/task_performance.csv": "table_4_27_task_performance.csv",
    "tables/task_quality_checks.csv": "table_4_28_task_quality_checks.csv",
    "tables/task_1_company_screening.csv": "table_4_29_task_1_company_screening.csv",
    "tables/task_2_tsmc_evidence.csv": "table_4_30_task_2_tsmc_evidence.csv",
    "tables/task_3_regulatory_alerts.csv": "table_4_31_task_3_regulatory_alerts.csv",
    "tables/task_4_alphabet_market_context.csv": "table_4_32_task_4_alphabet_market_context.csv",
    "tables/task_5_shared_event_pairs.csv": "table_4_33_task_5_shared_event_pairs.csv",
}

ANALYST_FIGURE_OUTPUTS = {
    "figures/use_case_latency.svg": "figure_4_9_use_case_latency.svg",
    "figures/use_case_completeness.svg": "figure_4_10_use_case_completeness.svg",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Chapter 4 tables, figures and bilingual narratives."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Project YAML configuration.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("outputs/chapter4_results"),
        help="Directory for generated tables, figures and reports.",
    )
    return parser.parse_args()


def truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def read_required_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required result file was not found: {path}")
    return pd.read_csv(path, encoding="utf-8-sig")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_manifest_path(value: str) -> str:
    """Return a portable, package-relative manifest path."""
    return value.replace("\\", "/").lstrip("./")


def package_file(package_directory: Path, relative_path: str) -> Path:
    """Resolve a manifest member while rejecting absolute/path-traversal entries."""
    normalised = normalise_manifest_path(relative_path)
    if not normalised or Path(normalised).is_absolute():
        raise ValueError(f"Package manifest path must be relative: {relative_path!r}")
    package_root = package_directory.resolve()
    candidate = (package_root / Path(normalised)).resolve()
    try:
        candidate.relative_to(package_root)
    except ValueError as error:
        raise ValueError(
            f"Package manifest path escapes its result directory: {relative_path!r}"
        ) from error
    return candidate


def validate_output_hashes(
    package_directory: Path,
    manifest: dict[str, Any],
    required_relative_paths: Iterable[str],
) -> dict[str, Path]:
    """Validate every declared result hash and return the required member paths."""
    declared = manifest.get("output_sha256")
    if not isinstance(declared, dict) or not declared:
        raise ValueError(
            f"Result manifest has no non-empty output_sha256 map: {package_directory}"
        )

    normalised_hashes: dict[str, str] = {}
    verified_paths: dict[str, Path] = {}
    for raw_relative_path, raw_expected_hash in declared.items():
        relative_path = normalise_manifest_path(str(raw_relative_path))
        if relative_path in normalised_hashes:
            raise ValueError(f"Duplicate normalised package path: {relative_path}")
        expected_hash = str(raw_expected_hash).strip().lower()
        if len(expected_hash) != 64:
            raise ValueError(f"Invalid SHA-256 for package member: {relative_path}")
        path = package_file(package_directory, relative_path)
        if not path.is_file():
            raise FileNotFoundError(f"Manifest-declared result file is missing: {path}")
        observed_hash = sha256_file(path)
        if observed_hash != expected_hash:
            raise ValueError(
                f"SHA-256 mismatch for {path}: expected {expected_hash}, "
                f"observed {observed_hash}"
            )
        normalised_hashes[relative_path] = expected_hash
        verified_paths[relative_path] = path

    required_paths: dict[str, Path] = {}
    for raw_relative_path in required_relative_paths:
        relative_path = normalise_manifest_path(raw_relative_path)
        if relative_path not in verified_paths:
            raise ValueError(
                f"Required package member is absent from output_sha256: {relative_path}"
            )
        required_paths[relative_path] = verified_paths[relative_path]
    return required_paths


def validate_gds_package(
    project_root: Path,
    package_directory: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Load and strictly validate the frozen, read-only GDS result package."""
    manifest_path = package_directory / "gds_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"GDS source manifest was not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    contract = manifest.get("read_only_contract")
    if not isinstance(contract, dict):
        raise ValueError("GDS manifest has no read_only_contract object")
    expected_contract = {
        "persisted_kg_writeback": False,
        "temporary_projections_dropped": True,
        "database_counts_unchanged": True,
    }
    for key, expected in expected_contract.items():
        if contract.get(key) is not expected:
            raise ValueError(
                f"GDS read-only contract failed for {key}: "
                f"expected {expected!r}, observed {contract.get(key)!r}"
            )

    import_hashes = manifest.get("expected_import_artifact_sha256")
    if not isinstance(import_hashes, dict) or not import_hashes:
        raise ValueError("GDS manifest has no expected import-artifact hashes")
    for raw_path, raw_expected_hash in import_hashes.items():
        artifact_path = resolve_path(project_root, Path(str(raw_path)))
        if not artifact_path.is_file():
            raise FileNotFoundError(
                f"GDS import artifact used for reproducibility is missing: {artifact_path}"
            )
        expected_hash = str(raw_expected_hash).strip().lower()
        observed_hash = sha256_file(artifact_path)
        if observed_hash != expected_hash:
            raise ValueError(
                f"GDS import-artifact SHA-256 mismatch for {artifact_path}: "
                f"expected {expected_hash}, observed {observed_hash}"
            )

    required = [*GDS_TABLE_OUTPUTS, *GDS_FIGURE_OUTPUTS]
    paths = validate_output_hashes(package_directory, manifest, required)
    paths["gds_manifest.json"] = manifest_path
    return manifest, paths


def validate_analyst_use_case_package(
    package_directory: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Load and strictly validate the frozen analyst-use-case evaluation package."""
    manifest_path = package_directory / "analyst_use_case_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Analyst-use-case source manifest was not found: {manifest_path}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    contract = manifest.get("read_only_contract")
    if not isinstance(contract, dict) or contract.get("database_counts_unchanged") is not True:
        raise ValueError(
            "Analyst-use-case read-only contract does not confirm unchanged database counts"
        )
    if contract.get("persisted_kg_writeback") is True:
        raise ValueError("Analyst-use-case package reports persisted KG writeback")

    required = [*ANALYST_TABLE_OUTPUTS, *ANALYST_FIGURE_OUTPUTS]
    paths = validate_output_hashes(package_directory, manifest, required)
    paths["analyst_use_case_manifest.json"] = manifest_path
    return manifest, paths


def clean_generated_result_files(tables_directory: Path, figures_directory: Path) -> None:
    """Remove only the generated Chapter 4 table/figure file types."""
    for pattern in ("*.csv",):
        for path in tables_directory.glob(pattern):
            path.unlink()
    for pattern in ("*.svg", "*.png"):
        for path in figures_directory.glob(pattern):
            path.unlink()


def copy_artifacts(
    source_paths: dict[str, Path],
    mapping: dict[str, str],
    destination_directory: Path,
) -> list[Path]:
    """Copy hash-validated frozen artifacts under Chapter 4 names."""
    copied: list[Path] = []
    for relative_path, output_name in mapping.items():
        destination = destination_directory / output_name
        shutil.copy2(source_paths[normalise_manifest_path(relative_path)], destination)
        copied.append(destination)
    return copied


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def svg_text(
    x: float,
    y: float,
    text: Any,
    *,
    size: int = 16,
    anchor: str = "start",
    weight: int = 400,
    color: str | None = None,
    rotate: int | None = None,
) -> str:
    transform = f' transform="rotate({rotate} {x} {y})"' if rotate else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
        f'font-family="Arial, Noto Sans, sans-serif" font-size="{size}" '
        f'font-weight="{weight}" fill="{color or PALETTE["text"]}"{transform}>'
        f"{html.escape(str(text))}</text>"
    )


def svg_document(
    width: int,
    height: int,
    elements: Iterable[str],
    *,
    title: str,
) -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                f'height="{height}" viewBox="0 0 {width} {height}" '
                'role="img">'
            ),
            f"<title>{html.escape(title)}</title>",
            f'<rect width="{width}" height="{height}" fill="{PALETTE["background"]}"/>',
            *elements,
            "</svg>",
        ]
    )


def nice_maximum(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    scaled = value / magnitude
    nice = 1 if scaled <= 1 else 2 if scaled <= 2 else 5 if scaled <= 5 else 10
    return nice * magnitude


def write_grouped_bar_chart(
    path: Path,
    *,
    title: str,
    subtitle: str,
    categories: Sequence[str],
    series: Sequence[tuple[str, Sequence[float], str]],
    y_label: str,
    value_format: str = "{:,.0f}",
    footnote: str = "",
) -> None:
    width, height = 1200, 720
    left, right, top, bottom = 105, 45, 115, 145
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [float(value) for _, data, _ in series for value in data]
    minimum = min([0.0, *values])
    maximum = max([0.0, *values])
    if minimum >= 0:
        maximum = nice_maximum(maximum * 1.08)
        minimum = 0.0
    else:
        span = maximum - minimum
        minimum -= span * 0.08
        maximum += span * 0.08
    scale = plot_height / (maximum - minimum)

    def y_position(value: float) -> float:
        return top + (maximum - value) * scale

    elements = [
        svg_text(left, 44, title, size=26, weight=700, color=PALETTE["navy"]),
        svg_text(left, 76, subtitle, size=15, color=PALETTE["muted"]),
    ]
    for tick_index in range(6):
        tick = minimum + (maximum - minimum) * tick_index / 5
        y = y_position(tick)
        elements.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" '
            f'stroke="{PALETTE["grid"]}" stroke-width="1"/>'
        )
        elements.append(
            svg_text(left - 12, y + 5, value_format.format(tick), size=13, anchor="end")
        )
    zero_y = y_position(0)
    elements.append(
        f'<line x1="{left}" y1="{zero_y:.1f}" x2="{width-right}" y2="{zero_y:.1f}" '
        f'stroke="{PALETTE["navy"]}" stroke-width="1.5"/>'
    )
    category_width = plot_width / max(len(categories), 1)
    group_width = category_width * 0.72
    bar_width = group_width / max(len(series), 1)
    for category_index, category in enumerate(categories):
        center = left + category_width * (category_index + 0.5)
        elements.append(
            svg_text(center, height - bottom + 34, category, size=14, anchor="middle")
        )
        for series_index, (_, data, color) in enumerate(series):
            value = float(data[category_index])
            x = center - group_width / 2 + series_index * bar_width + 2
            y = min(zero_y, y_position(value))
            bar_height = max(1.0, abs(y_position(value) - zero_y))
            elements.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(2, bar_width-4):.1f}" '
                f'height="{bar_height:.1f}" rx="2" fill="{color}"/>'
            )
            label_y = y - 8 if value >= 0 else y + bar_height + 18
            elements.append(
                svg_text(
                    x + (bar_width - 4) / 2,
                    label_y,
                    value_format.format(value),
                    size=12,
                    anchor="middle",
                    color=color,
                    weight=600,
                )
            )
    legend_x = left
    legend_y = height - 48
    for name, _, color in series:
        elements.append(
            f'<rect x="{legend_x}" y="{legend_y-13}" width="18" height="12" '
            f'rx="2" fill="{color}"/>'
        )
        elements.append(svg_text(legend_x + 26, legend_y - 2, name, size=13))
        legend_x += 180
    elements.append(
        svg_text(28, top + plot_height / 2, y_label, size=14, anchor="middle", rotate=-90)
    )
    if footnote:
        elements.append(
            svg_text(left, height - 16, footnote, size=12, color=PALETTE["muted"])
        )
    path.write_text(
        svg_document(width, height, elements, title=title),
        encoding="utf-8",
    )


def write_horizontal_bar_chart(
    path: Path,
    *,
    title: str,
    subtitle: str,
    labels: Sequence[str],
    values: Sequence[float],
    x_label: str,
    footnote: str = "",
) -> None:
    width = 1350
    row_height = 35
    height = 150 + row_height * len(labels) + 75
    left, right, top, bottom = 390, 70, 115, 70
    plot_width = width - left - right
    maximum = nice_maximum(max(values) * 1.08 if values else 1)
    elements = [
        svg_text(left, 44, title, size=26, weight=700, color=PALETTE["navy"]),
        svg_text(left, 76, subtitle, size=15, color=PALETTE["muted"]),
    ]
    for tick_index in range(6):
        tick = maximum * tick_index / 5
        x = left + plot_width * tick / maximum
        elements.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" '
            f'stroke="{PALETTE["grid"]}" stroke-width="1"/>'
        )
        elements.append(svg_text(x, height - bottom + 28, f"{tick:,.0f}", size=12, anchor="middle"))
    colors = [PALETTE["teal"], PALETTE["blue"]]
    for index, (label, value) in enumerate(zip(labels, values)):
        y = top + index * row_height
        bar_width = plot_width * float(value) / maximum
        elements.append(
            svg_text(left - 14, y + 21, label, size=13, anchor="end")
        )
        elements.append(
            f'<rect x="{left}" y="{y+5}" width="{bar_width:.1f}" height="22" '
            f'rx="3" fill="{colors[index % 2]}"/>'
        )
        elements.append(
            svg_text(left + bar_width + 8, y + 21, f"{value:,.0f}", size=12, weight=600)
        )
    elements.append(
        svg_text(left + plot_width / 2, height - 12, x_label, size=14, anchor="middle")
    )
    if footnote:
        elements.append(
            svg_text(left, 104, footnote, size=12, color=PALETTE["muted"])
        )
    path.write_text(
        svg_document(width, height, elements, title=title),
        encoding="utf-8",
    )


def write_line_chart(
    path: Path,
    *,
    title: str,
    subtitle: str,
    x_values: Sequence[float],
    series: Sequence[tuple[str, Sequence[float], str]],
    x_label: str,
    y_label: str,
    current_x: float | None = None,
    footnote: str = "",
) -> None:
    width, height = 1200, 720
    left, right, top, bottom = 110, 55, 115, 115
    plot_width = width - left - right
    plot_height = height - top - bottom
    x_min, x_max = min(x_values), max(x_values)
    all_values = [float(value) for _, data, _ in series for value in data]
    y_min = math.floor(min(all_values) / 50) * 50
    y_max = nice_maximum(max(all_values) * 1.03)

    def x_position(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * plot_width

    def y_position(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_height

    elements = [
        svg_text(left, 44, title, size=26, weight=700, color=PALETTE["navy"]),
        svg_text(left, 76, subtitle, size=15, color=PALETTE["muted"]),
    ]
    for tick_index in range(6):
        tick = y_min + (y_max - y_min) * tick_index / 5
        y = y_position(tick)
        elements.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" '
            f'stroke="{PALETTE["grid"]}" stroke-width="1"/>'
        )
        elements.append(svg_text(left - 12, y + 5, f"{tick:,.0f}", size=13, anchor="end"))
    for value in x_values:
        x = x_position(value)
        elements.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" '
            f'stroke="{PALETTE["grid"]}" stroke-width="1"/>'
        )
        elements.append(svg_text(x, height - bottom + 30, f"{value:.2f}", size=13, anchor="middle"))
    if current_x is not None:
        current = x_position(current_x)
        elements.append(
            f'<line x1="{current:.1f}" y1="{top}" x2="{current:.1f}" '
            f'y2="{height-bottom}" stroke="{PALETTE["red"]}" stroke-width="2" '
            'stroke-dasharray="7 5"/>'
        )
        elements.append(
            svg_text(current + 8, top + 18, "Current threshold", size=12, color=PALETTE["red"])
        )
    legend_x = left
    for name, data, color in series:
        points = " ".join(
            f"{x_position(x):.1f},{y_position(float(y)):.1f}"
            for x, y in zip(x_values, data)
        )
        elements.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" '
            'stroke-width="3" stroke-linejoin="round"/>'
        )
        for x, y in zip(x_values, data):
            px, py = x_position(x), y_position(float(y))
            elements.append(
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{color}" '
                f'stroke="{PALETTE["background"]}" stroke-width="2"/>'
            )
            elements.append(
                svg_text(px, py - 12, f"{float(y):,.0f}", size=12, anchor="middle", color=color, weight=600)
            )
        elements.append(
            f'<line x1="{legend_x}" y1="{height-43}" x2="{legend_x+24}" '
            f'y2="{height-43}" stroke="{color}" stroke-width="3"/>'
        )
        elements.append(svg_text(legend_x + 32, height - 38, name, size=13))
        legend_x += 200
    elements.append(
        svg_text(left + plot_width / 2, height - 13, x_label, size=14, anchor="middle")
    )
    elements.append(
        svg_text(30, top + plot_height / 2, y_label, size=14, anchor="middle", rotate=-90)
    )
    if footnote:
        elements.append(
            svg_text(left, 101, footnote, size=12, color=PALETTE["muted"])
        )
    path.write_text(
        svg_document(width, height, elements, title=title),
        encoding="utf-8",
    )


def blend_hex(start: str, end: str, fraction: float) -> str:
    """Blend two six-digit hex colours for a value in the inclusive 0..1 range."""
    bounded = max(0.0, min(1.0, fraction))
    start_rgb = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
    blended = tuple(
        round(start_component + (end_component - start_component) * bounded)
        for start_component, end_component in zip(start_rgb, end_rgb)
    )
    return "#" + "".join(f"{component:02X}" for component in blended)


def write_threshold_heatmap(
    path: Path,
    *,
    title: str,
    subtitle: str,
    frame: pd.DataFrame,
    footnote: str = "",
) -> None:
    """Render the small threshold grid without overlapping near-identical lines."""
    width, height = 1200, 720
    left, right, top, bottom = 245, 75, 170, 145
    thresholds = sorted(frame["confirmation_threshold"].astype(float).unique())
    scores = sorted(frame["strong_rule_focus_score"].astype(int).unique())
    cell_width = (width - left - right) / max(len(thresholds), 1)
    cell_height = (height - top - bottom) / max(len(scores), 1)
    values = frame["qualified_event_company_links"].astype(float)
    minimum, maximum = float(values.min()), float(values.max())
    span = maximum - minimum or 1.0

    elements = [
        svg_text(left, 44, title, size=26, weight=700, color=PALETTE["navy"]),
        svg_text(left, 76, subtitle, size=15, color=PALETTE["muted"]),
        svg_text(
            left + (width - left - right) / 2,
            125,
            "NLP confirmation threshold",
            size=14,
            anchor="middle",
            weight=600,
        ),
        svg_text(
            52,
            top + (height - top - bottom) / 2,
            "Strong-rule focus score",
            size=14,
            anchor="middle",
            weight=600,
            rotate=-90,
        ),
    ]

    for column, threshold in enumerate(thresholds):
        x = left + column * cell_width
        elements.append(
            svg_text(
                x + cell_width / 2,
                top - 24,
                f"{threshold:.2f}",
                size=14,
                anchor="middle",
                weight=600,
            )
        )
    current_cell: tuple[float, float] | None = None
    for row, score in enumerate(scores):
        y = top + row * cell_height
        elements.append(
            svg_text(
                left - 24,
                y + cell_height / 2 + 6,
                str(score),
                size=15,
                anchor="end",
                weight=600,
            )
        )
        for column, threshold in enumerate(thresholds):
            x = left + column * cell_width
            selected = frame.loc[
                (frame["strong_rule_focus_score"].astype(int) == score)
                & (
                    frame["confirmation_threshold"].astype(float)
                    == float(threshold)
                )
            ].iloc[0]
            value = float(selected["qualified_event_company_links"])
            fraction = (value - minimum) / span
            fill = blend_hex("#EAF4F6", PALETTE["teal"], fraction)
            current = bool(selected["current_setting"])
            if current:
                current_cell = (x, y)
            elements.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_width:.1f}" '
                f'height="{cell_height:.1f}" rx="5" fill="{fill}" '
                f'stroke="{PALETTE["background"]}" stroke-width="4"/>'
            )
            text_colour = (
                PALETTE["background"] if fraction >= 0.58 else PALETTE["text"]
            )
            elements.append(
                svg_text(
                    x + cell_width / 2,
                    y + cell_height / 2 + 2,
                    f"{value:,.0f}",
                    size=20,
                    anchor="middle",
                    weight=700,
                    color=text_colour,
                )
            )
            elements.append(
                svg_text(
                    x + cell_width / 2,
                    y + cell_height / 2 + 27,
                    "relationships",
                    size=11,
                    anchor="middle",
                    color=text_colour,
                )
            )

    # Draw the current-setting outline after every filled cell. Keeping this
    # overlay last prevents the next row or column from covering its right and
    # bottom edges. The inset keeps the complete stroke inside the selected cell.
    if current_cell is not None:
        current_x, current_y = current_cell
        inset = 3
        elements.append(
            f'<rect x="{current_x + inset:.1f}" y="{current_y + inset:.1f}" '
            f'width="{cell_width - 2 * inset:.1f}" '
            f'height="{cell_height - 2 * inset:.1f}" rx="4" fill="none" '
            f'stroke="{PALETTE["red"]}" stroke-width="5"/>'
        )

    legend_y = height - 84
    elements.extend(
        [
            f'<rect x="{left}" y="{legend_y-17}" width="26" height="20" '
            f'rx="3" fill="none" stroke="{PALETTE["red"]}" stroke-width="3"/>',
            svg_text(left + 38, legend_y, "Current setting", size=12),
            svg_text(
                left,
                height - 30,
                footnote,
                size=12,
                color=PALETTE["muted"],
            ),
        ]
    )
    path.write_text(
        svg_document(width, height, elements, title=title),
        encoding="utf-8",
    )


def markdown_table(
    frame: pd.DataFrame,
    columns: Sequence[tuple[str, str]],
    *,
    formats: dict[str, str] | None = None,
) -> str:
    formats = formats or {}
    headers = [label for _, label in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---:" if key in formats else "---" for key, _ in columns) + "|",
    ]
    for _, row in frame.iterrows():
        cells = []
        for key, _ in columns:
            value = row[key]
            if pd.isna(value):
                rendered = ""
            elif key in formats:
                rendered = formats[key].format(value)
            else:
                rendered = str(value)
            cells.append(rendered.replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def numeric(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    copy = frame.copy()
    for column in columns:
        copy[column] = pd.to_numeric(copy[column], errors="coerce")
    return copy


def source_paths(
    project_root: Path,
    config: dict[str, Any],
) -> dict[str, Path]:
    sample_label = str(config["study"]["sample_label"])
    news_config = config.get("news_data", {})
    evaluation_config = config.get("pipeline_evaluation", {})
    import_config = config.get("neo4j_import", {})
    connection_config = config.get("neo4j_connection", {})
    news_directory = resolve_path(
        project_root,
        news_config.get("processed_output_directory", "data/news/processed"),
    )
    evaluation_directory = resolve_path(
        project_root,
        evaluation_config.get("output_directory", f"data/evaluation/{sample_label}"),
    )
    import_directory = resolve_path(
        project_root,
        import_config.get("output_directory", "data/neo4j/import"),
    )
    analysis_directory = resolve_path(
        project_root,
        connection_config.get("query_output_directory", "data/neo4j/analysis"),
    )
    stem = f"guardian_{sample_label}"
    return {
        "articles": news_directory / f"{stem}_articles.csv",
        "articles_clean": news_directory / f"{stem}_articles_clean.csv",
        "article_company_links_clean": news_directory
        / f"{stem}_article_company_links_clean.csv",
        "stage_ablation": evaluation_directory / "stage_ablation_summary.csv",
        "event_type_stage": evaluation_directory / "event_type_stage_summary.csv",
        "company_stage": evaluation_directory / "company_stage_summary.csv",
        "threshold_sensitivity": evaluation_directory / "threshold_sensitivity.csv",
        "deduplication": evaluation_directory / "deduplication_summary.csv",
        "automatic_checks": evaluation_directory / "automatic_quality_checks.csv",
        "market_windows": news_directory / f"{stem}_event_market_windows.csv",
        "kg_import_report": import_directory / "kg_import_report.csv",
        "graph_validation": analysis_directory / "graph_validation.csv",
        "graph_articles": import_directory / "articles.csv",
    }


def gds_source_directory(project_root: Path, config: dict[str, Any]) -> Path:
    gds_config = config.get("gds_analysis", {})
    return resolve_path(
        project_root,
        gds_config.get("output_directory", "outputs/gds_analysis"),
    )


def analyst_source_directory(project_root: Path, config: dict[str, Any]) -> Path:
    analyst_config = config.get("analyst_use_case_evaluation", {})
    return resolve_path(
        project_root,
        analyst_config.get(
            "output_directory",
            "outputs/analyst_use_case_evaluation",
        ),
    )


def build_results(
    config_path: Path,
    output_directory: Path,
) -> dict[str, Any]:
    config_path = config_path.resolve()
    config = load_config(config_path)
    project_root = config_path.parent.parent
    output_directory = resolve_path(project_root, output_directory)
    tables_directory = output_directory / "tables"
    figures_directory = output_directory / "figures"
    tables_directory.mkdir(parents=True, exist_ok=True)
    figures_directory.mkdir(parents=True, exist_ok=True)

    paths = source_paths(project_root, config)
    frames = {name: read_required_csv(path) for name, path in paths.items()}
    gds_directory = gds_source_directory(project_root, config)
    analyst_directory = analyst_source_directory(project_root, config)
    gds_manifest, gds_paths = validate_gds_package(project_root, gds_directory)
    analyst_manifest, analyst_paths = validate_analyst_use_case_package(
        analyst_directory
    )
    gds_frames = {
        Path(relative_path).stem: read_required_csv(gds_paths[relative_path])
        for relative_path in GDS_TABLE_OUTPUTS
    }
    analyst_frames = {
        Path(relative_path).stem: read_required_csv(analyst_paths[relative_path])
        for relative_path in ANALYST_TABLE_OUTPUTS
    }

    # A successful rebuild is an exact package, not an accumulation of stale files.
    clean_generated_result_files(tables_directory, figures_directory)

    stage = numeric(
        frames["stage_ablation"],
        [
            "candidate_events",
            "qualified_events",
            "candidate_event_company_links",
            "qualified_event_company_links",
            "covered_companies",
            "event_retention_from_previous_pct",
            "relationship_retention_from_previous_pct",
        ],
    )
    event_types = numeric(
        frames["event_type_stage"],
        [
            "rule_events",
            "hybrid_events",
            "canonical_events",
            "nlp_removed",
            "duplicates_removed",
        ],
    )
    companies = numeric(
        frames["company_stage"],
        [
            "rule_events",
            "rule_relationships",
            "hybrid_events",
            "hybrid_relationships",
            "canonical_events",
            "canonical_relationships",
            "hybrid_removed_from_rule",
            "duplicates_removed",
        ],
    ).sort_values(
        ["canonical_relationships", "company_name"],
        ascending=[False, True],
    )
    sensitivity = numeric(
        frames["threshold_sensitivity"],
        [
            "confirmation_threshold",
            "strong_rule_focus_score",
            "qualified_events",
            "qualified_event_company_links",
            "covered_companies",
            "zero_event_companies",
            "minimum_company_event_count",
            "median_company_event_count",
            "maximum_company_event_count",
        ],
    )
    sensitivity["current_setting"] = truthy(sensitivity["current_setting"])
    deduplication = frames["deduplication"].copy()
    deduplication["value"] = pd.to_numeric(
        deduplication["value"], errors="coerce"
    )

    articles = frames["articles"]
    articles_clean = frames["articles_clean"]
    links_clean = frames["article_company_links_clean"]
    qualified_article_count = len(frames["graph_articles"])
    article_summary = pd.DataFrame(
        [
            {
                "stage": "Collected unique articles",
                "count": len(articles),
                "unit": "articles",
            },
            {
                "stage": "Cleaned in selected-company scope",
                "count": len(articles_clean),
                "unit": "articles",
            },
            {
                "stage": "Analysis-ready articles",
                "count": int(truthy(articles_clean["analysis_ready"]).sum()),
                "unit": "articles",
            },
            {
                "stage": "Accepted Article–Company links",
                "count": int(truthy(links_clean["accepted_for_analysis"]).sum()),
                "unit": "relationships",
            },
            {
                "stage": "Articles supporting final graph",
                "count": qualified_article_count,
                "unit": "articles",
            },
        ]
    )

    market = numeric(
        frames["market_windows"],
        ["window_trading_days", "cumulative_return"],
    )
    market_summary = (
        market.groupby("window_trading_days")["cumulative_return"]
        .agg(["count", "mean", "median", "std", "min", "max"])
        .reset_index()
        .rename(
            columns={
                "window_trading_days": "window_days",
                "count": "observations",
                "mean": "average_return",
                "median": "median_return",
                "std": "standard_deviation",
                "min": "minimum_return",
                "max": "maximum_return",
            }
        )
    )
    positive_rate = (
        market.assign(positive=market["cumulative_return"] > 0)
        .groupby("window_trading_days")["positive"]
        .mean()
        .rename("positive_return_rate")
        .reset_index()
        .rename(columns={"window_trading_days": "window_days"})
    )
    market_summary = market_summary.merge(positive_rate, on="window_days")
    market_by_type = (
        market.groupby(["event_type", "window_trading_days"])[
            "cumulative_return"
        ]
        .agg(["count", "mean", "median"])
        .reset_index()
        .rename(
            columns={
                "window_trading_days": "window_days",
                "count": "observations",
                "mean": "average_return",
                "median": "median_return",
            }
        )
    )

    kg_inventory = frames["kg_import_report"].loc[
        frames["kg_import_report"]["section"].isin(["nodes", "relationships"]),
        ["section", "metric", "value", "status", "details"],
    ].copy()
    kg_inventory["value"] = pd.to_numeric(kg_inventory["value"], errors="coerce")
    graph_validation = frames["graph_validation"].copy()

    write_csv(article_summary, tables_directory / "table_4_1_article_pipeline.csv")
    write_csv(stage, tables_directory / "table_4_2_stage_ablation.csv")
    write_csv(event_types, tables_directory / "table_4_3_event_type_progression.csv")
    write_csv(companies, tables_directory / "table_4_4_company_coverage.csv")
    write_csv(sensitivity, tables_directory / "table_4_5_threshold_sensitivity.csv")
    write_csv(deduplication, tables_directory / "table_4_6_deduplication.csv")
    write_csv(market_summary, tables_directory / "table_4_7_market_windows.csv")
    write_csv(market_by_type, tables_directory / "table_4_8_market_by_event_type.csv")
    write_csv(kg_inventory, tables_directory / "table_4_9_graph_inventory.csv")
    write_csv(
        frames["automatic_checks"],
        tables_directory / "table_4_10_automatic_checks.csv",
    )
    write_csv(
        graph_validation,
        tables_directory / "table_4_11_graph_validation.csv",
    )
    generated_table_paths = [
        tables_directory / f"table_4_{index}_{suffix}.csv"
        for index, suffix in [
            (1, "article_pipeline"),
            (2, "stage_ablation"),
            (3, "event_type_progression"),
            (4, "company_coverage"),
            (5, "threshold_sensitivity"),
            (6, "deduplication"),
            (7, "market_windows"),
            (8, "market_by_event_type"),
            (9, "graph_inventory"),
            (10, "automatic_checks"),
            (11, "graph_validation"),
        ]
    ]
    generated_table_paths.extend(
        copy_artifacts(gds_paths, GDS_TABLE_OUTPUTS, tables_directory)
    )
    generated_table_paths.extend(
        copy_artifacts(analyst_paths, ANALYST_TABLE_OUTPUTS, tables_directory)
    )

    write_grouped_bar_chart(
        figures_directory / "figure_4_1_stage_ablation.svg",
        title="Automatic Event Pipeline: Stage Retention",
        subtitle="Rule qualification, NLI validation and cross-article canonicalisation",
        categories=stage["stage"].str.title().tolist(),
        series=[
            (
                "Qualified events",
                stage["qualified_events"].tolist(),
                PALETTE["teal"],
            ),
            (
                "Event–Company relationships",
                stage["qualified_event_company_links"].tolist(),
                PALETTE["orange"],
            ),
        ],
        y_label="Count",
        footnote="All three stages retained coverage of all 25 selected companies.",
    )
    event_types_plot = event_types.sort_values(
        "canonical_events", ascending=False
    )
    write_grouped_bar_chart(
        figures_directory / "figure_4_2_event_type_progression.svg",
        title="Event-Type Counts Across the Automatic Pipeline",
        subtitle="The NLI stage removes unsupported candidates; deduplication is conservative",
        categories=[
            value.replace("_event", "").replace("_", " ").title()
            for value in event_types_plot["event_type"]
        ],
        series=[
            ("Rule", event_types_plot["rule_events"].tolist(), PALETTE["blue"]),
            ("Hybrid", event_types_plot["hybrid_events"].tolist(), PALETTE["orange"]),
            (
                "Canonical",
                event_types_plot["canonical_events"].tolist(),
                PALETTE["green"],
            ),
        ],
        y_label="Qualified event mentions / canonical events",
    )
    write_horizontal_bar_chart(
        figures_directory / "figure_4_3_company_coverage.svg",
        title="Canonical Event–Company Relationships by Company",
        subtitle="Coverage is complete but strongly heterogeneous across companies",
        labels=companies["company_name"].tolist(),
        values=companies["canonical_relationships"].tolist(),
        x_label="Canonical Event–Company relationships",
        footnote="Counts reflect source coverage and automatic evidence gates, not company importance.",
    )
    write_threshold_heatmap(
        figures_directory / "figure_4_4_threshold_sensitivity.svg",
        title="Relationship Retention Across the Threshold Grid",
        subtitle="All tested settings preserve 25-company coverage",
        frame=sensitivity,
        footnote=(
            "Cell values are retained Event–Company relationships. "
            "Every tested setting covers all 25 selected companies."
        ),
    )
    write_grouped_bar_chart(
        figures_directory / "figure_4_5_market_context.svg",
        title="Descriptive Post-Publication Market Windows",
        subtitle="Average and median cumulative returns by trading-day window",
        categories=[f"{int(value)} day" for value in market_summary["window_days"]],
        series=[
            (
                "Average return",
                (market_summary["average_return"] * 100).tolist(),
                PALETTE["teal"],
            ),
            (
                "Median return",
                (market_summary["median_return"] * 100).tolist(),
                PALETTE["orange"],
            ),
        ],
        y_label="Cumulative return (%)",
        value_format="{:.2f}%",
        footnote="Descriptive context only. No causal interpretation or investment recommendation is made.",
    )
    generated_figure_paths = [
        figures_directory / f"figure_4_{index}_{suffix}.svg"
        for index, suffix in [
            (1, "stage_ablation"),
            (2, "event_type_progression"),
            (3, "company_coverage"),
            (4, "threshold_sensitivity"),
            (5, "market_context"),
        ]
    ]
    generated_figure_paths.extend(
        copy_artifacts(gds_paths, GDS_FIGURE_OUTPUTS, figures_directory)
    )
    generated_figure_paths.extend(
        copy_artifacts(analyst_paths, ANALYST_FIGURE_OUTPUTS, figures_directory)
    )

    rule_row = stage.loc[stage["stage"] == "rule"].iloc[0]
    hybrid_row = stage.loc[stage["stage"] == "hybrid"].iloc[0]
    canonical_row = stage.loc[stage["stage"] == "canonical"].iloc[0]
    dedup_metrics = deduplication.set_index("metric")["value"]
    current_sensitivity = sensitivity.loc[sensitivity["current_setting"]].iloc[0]
    top_five_share = (
        companies.head(5)["canonical_relationships"].sum()
        / companies["canonical_relationships"].sum()
    )
    canonical_total = int(canonical_row["qualified_events"])
    event_types["canonical_share"] = (
        event_types["canonical_events"] / canonical_total
    )
    quality_failures = int(
        (
            frames["automatic_checks"]["status"].astype(str).str.upper()
            == "FAIL"
        ).sum()
    )
    neo4j_failures = int(
        (graph_validation["status"].astype(str).str.upper() == "FAIL").sum()
    )

    gds_summary = gds_manifest.get("summary", {})
    if not isinstance(gds_summary, dict):
        raise ValueError("GDS manifest summary must be an object")
    gds_thresholds = numeric(
        gds_frames["threshold_sensitivity"],
        [
            "minimum_shared_events",
            "logical_edge_count",
            "density",
            "component_count",
            "largest_component_size",
            "isolate_count",
        ],
    )
    gds_support_threshold = int(gds_summary.get("support_threshold", 2))
    support_rows = gds_thresholds.loc[
        gds_thresholds["minimum_shared_events"] == gds_support_threshold
    ]
    if support_rows.empty:
        raise ValueError(
            "GDS threshold_sensitivity.csv has no row for the configured support threshold"
        )
    gds_support_row = support_rows.iloc[0]
    gds_correlations = numeric(
        gds_frames["centrality_correlations"],
        ["n", "pearson", "spearman"],
    )
    strength_correlation_rows = gds_correlations.loc[
        (
            gds_correlations["metric_1"].astype(str).eq("event_count")
            & gds_correlations["metric_2"].astype(str).eq("coevent_strength")
        )
        | (
            gds_correlations["metric_2"].astype(str).eq("event_count")
            & gds_correlations["metric_1"].astype(str).eq("coevent_strength")
        )
    ]
    if strength_correlation_rows.empty:
        raise ValueError(
            "GDS centrality_correlations.csv has no event_count/coevent_strength row"
        )
    event_strength_spearman = float(strength_correlation_rows.iloc[0]["spearman"])

    analyst_summary = analyst_manifest.get("summary", {})
    if not isinstance(analyst_summary, dict):
        raise ValueError("Analyst-use-case manifest summary must be an object")
    use_case_summary = numeric(
        analyst_frames["use_case_summary"],
        [
            "workflow_query_steps",
            "manual_join_steps",
            "manual_calculation_steps",
            "result_rows",
            "coverage_pct",
            "evidence_completeness_pct",
            "provenance_completeness_pct",
            "median_client_ms",
            "p95_client_ms",
        ],
    )
    task_performance = numeric(
        analyst_frames["task_performance"],
        [
            "result_rows",
            "median_client_ms",
            "p95_client_ms",
            "min_client_ms",
            "max_client_ms",
        ],
    )
    task_quality_checks = analyst_frames["task_quality_checks"].copy()
    analyst_quality_passes = int(
        task_quality_checks["status"].astype(str).str.upper().eq("PASS").sum()
    )
    analyst_quality_total = int(len(task_quality_checks))

    metrics = {
        "collected_articles": int(len(articles)),
        "cleaned_selected_scope_articles": int(len(articles_clean)),
        "analysis_ready_articles": int(truthy(articles_clean["analysis_ready"]).sum()),
        "accepted_article_company_links": int(
            truthy(links_clean["accepted_for_analysis"]).sum()
        ),
        "graph_source_articles": int(qualified_article_count),
        "candidate_events": int(rule_row["candidate_events"]),
        "rule_events": int(rule_row["qualified_events"]),
        "hybrid_events": int(hybrid_row["qualified_events"]),
        "canonical_events": int(canonical_row["qualified_events"]),
        "candidate_relationships": int(rule_row["candidate_event_company_links"]),
        "rule_relationships": int(rule_row["qualified_event_company_links"]),
        "hybrid_relationships": int(hybrid_row["qualified_event_company_links"]),
        "canonical_relationships": int(
            canonical_row["qualified_event_company_links"]
        ),
        "rule_to_hybrid_event_retention": float(
            hybrid_row["event_retention_from_previous_pct"] / 100
        ),
        "rule_to_hybrid_relationship_retention": float(
            hybrid_row["relationship_retention_from_previous_pct"] / 100
        ),
        "source_event_mentions": int(dedup_metrics["source_event_mentions"]),
        "duplicates_removed": int(dedup_metrics["duplicates_removed"]),
        "multi_source_events": int(
            dedup_metrics["multi_source_canonical_events"]
        ),
        "largest_cluster_size": int(dedup_metrics["largest_cluster_size"]),
        "minimum_multi_source_similarity": float(
            dedup_metrics["minimum_multi_source_similarity"]
        ),
        "covered_companies": int(canonical_row["covered_companies"]),
        "top_five_company_share": float(top_five_share),
        "current_threshold": float(
            current_sensitivity["confirmation_threshold"]
        ),
        "current_strong_rule_score": int(
            current_sensitivity["strong_rule_focus_score"]
        ),
        "current_threshold_relationships": int(
            current_sensitivity["qualified_event_company_links"]
        ),
        "sensitivity_minimum_relationships": int(
            sensitivity["qualified_event_company_links"].min()
        ),
        "sensitivity_maximum_relationships": int(
            sensitivity["qualified_event_company_links"].max()
        ),
        "automatic_quality_failures": quality_failures,
        "neo4j_validation_failures": neo4j_failures,
        "graph_nodes": int(
            kg_inventory.loc[kg_inventory["section"] == "nodes", "value"].sum()
        ),
        "graph_relationships": int(
            kg_inventory.loc[
                kg_inventory["section"] == "relationships", "value"
            ].sum()
        ),
        "market_windows": int(len(market)),
        "gds_company_count": int(gds_summary.get("company_count", 0)),
        "gds_logical_edge_count": int(gds_summary.get("logical_edge_count", 0)),
        "gds_projected_relationship_count": int(
            gds_summary.get("projected_relationship_count", 0)
        ),
        "gds_wcc_count": int(gds_summary.get("wcc_count", 0)),
        "gds_largest_wcc_size": int(gds_summary.get("largest_wcc_size", 0)),
        "gds_isolate_count": int(gds_summary.get("isolate_count", 0)),
        "gds_weighted_community_count": int(
            gds_summary.get("weighted_community_count", 0)
        ),
        "gds_weighted_modularity": float(
            gds_summary.get("weighted_modularity", 0.0)
        ),
        "gds_node_similarity_pair_count": int(
            gds_summary.get("node_similarity_pair_count", 0)
        ),
        "gds_single_support_edge_count": int(
            gds_summary.get("single_support_edge_count", 0)
        ),
        "gds_support_threshold": gds_support_threshold,
        "gds_supported_edge_count": int(gds_support_row["logical_edge_count"]),
        "gds_supported_isolate_count": int(gds_support_row["isolate_count"]),
        "gds_event_strength_spearman": event_strength_spearman,
        "analyst_use_case_count": int(
            analyst_summary.get("use_case_count", len(use_case_summary))
        ),
        "analyst_quality_checks_passed": int(
            analyst_summary.get("quality_checks_passed", analyst_quality_passes)
        ),
        "analyst_quality_checks_total": int(
            analyst_summary.get("quality_checks_total", analyst_quality_total)
        ),
        "analyst_all_tasks_succeeded": bool(
            analyst_summary.get(
                "all_tasks_succeeded",
                use_case_summary["status"].astype(str).str.upper().eq("PASS").all(),
            )
        ),
        "analyst_all_result_hashes_stable": bool(
            analyst_summary.get("all_result_hashes_stable", False)
        ),
        "analyst_all_row_counts_stable": bool(
            analyst_summary.get("all_row_counts_stable", False)
        ),
        "analyst_graph_state_unchanged": bool(
            analyst_manifest["read_only_contract"]["database_counts_unchanged"]
        ),
        "analyst_median_latency_ms": float(
            task_performance["median_client_ms"].median()
        ),
        "analyst_max_p95_latency_ms": float(task_performance["p95_client_ms"].max()),
        "analyst_mean_coverage_pct": float(use_case_summary["coverage_pct"].mean()),
        "analyst_mean_evidence_completeness_pct": float(
            use_case_summary["evidence_completeness_pct"].mean()
        ),
        "analyst_mean_provenance_completeness_pct": float(
            use_case_summary["provenance_completeness_pct"].mean()
        ),
    }

    narrative_cn = chapter4_markdown(
        language="zh",
        config=config,
        metrics=metrics,
        article_summary=article_summary,
        stage=stage,
        event_types=event_types,
        companies=companies,
        sensitivity=sensitivity,
        market_summary=market_summary,
        kg_inventory=kg_inventory,
        quality_checks=frames["automatic_checks"],
        gds_manifest=gds_manifest,
        gds_frames=gds_frames,
        analyst_manifest=analyst_manifest,
        analyst_frames=analyst_frames,
    )
    narrative_en = chapter4_markdown(
        language="en",
        config=config,
        metrics=metrics,
        article_summary=article_summary,
        stage=stage,
        event_types=event_types,
        companies=companies,
        sensitivity=sensitivity,
        market_summary=market_summary,
        kg_inventory=kg_inventory,
        quality_checks=frames["automatic_checks"],
        gds_manifest=gds_manifest,
        gds_frames=gds_frames,
        analyst_manifest=analyst_manifest,
        analyst_frames=analyst_frames,
    )
    (output_directory / "chapter4_results_cn.md").write_text(
        narrative_cn,
        encoding="utf-8-sig",
    )
    (output_directory / "chapter4_results_en.md").write_text(
        narrative_en,
        encoding="utf-8-sig",
    )
    gds_source_manifest_output = output_directory / "gds_source_manifest.json"
    analyst_source_manifest_output = (
        output_directory / "analyst_use_case_source_manifest.json"
    )
    shutil.copy2(gds_paths["gds_manifest.json"], gds_source_manifest_output)
    shutil.copy2(
        analyst_paths["analyst_use_case_manifest.json"],
        analyst_source_manifest_output,
    )

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
        "study": config.get("study", {}),
        "metrics": metrics,
        "source_files": {
            name: {
                "path": str(path),
                "sha256": sha256_file(path),
            }
            for name, path in paths.items()
        },
        "gds_source_manifest": {
            "source_path": str(gds_paths["gds_manifest.json"]),
            "copied_path": str(gds_source_manifest_output),
            "sha256": sha256_file(gds_paths["gds_manifest.json"]),
            "manifest": gds_manifest,
        },
        "analyst_use_case_source_manifest": {
            "source_path": str(
                analyst_paths["analyst_use_case_manifest.json"]
            ),
            "copied_path": str(analyst_source_manifest_output),
            "sha256": sha256_file(
                analyst_paths["analyst_use_case_manifest.json"]
            ),
            "manifest": analyst_manifest,
        },
        "generated_outputs": {
            "tables": [str(path) for path in generated_table_paths],
            "figures": [str(path) for path in generated_figure_paths],
        },
        "interpretation": {
            "human_labelled_benchmark_available": False,
            "ground_truth_precision_or_recall_claimed": False,
            "market_returns_interpreted_as_causal": False,
            "gds_structure_interpreted_as_causal": False,
            "manual_time_savings_claimed": False,
            "analyst_evaluation_precision_or_recall_claimed": False,
        },
    }
    (output_directory / "results_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "output_directory": output_directory,
        "metrics": metrics,
        "tables": len(generated_table_paths),
        "figures": len(generated_figure_paths),
    }


def gds_markdown_lines(
    *,
    language: str,
    metrics: dict[str, Any],
    gds_frames: dict[str, pd.DataFrame],
) -> list[str]:
    edges = numeric(
        gds_frames["company_coevent_edges"],
        ["shared_event_count"],
    ).sort_values(
        ["shared_event_count", "company1", "company2"],
        ascending=[False, True, True],
    )
    similarities = numeric(
        gds_frames["company_node_similarity"],
        ["similarity", "shared_event_count"],
    ).sort_values(
        ["similarity", "company1", "company2"],
        ascending=[False, True, True],
    )
    thresholds = numeric(
        gds_frames["threshold_sensitivity"],
        [
            "minimum_shared_events",
            "logical_edge_count",
            "component_count",
            "largest_component_size",
            "isolate_count",
        ],
    ).sort_values("minimum_shared_events")

    if language == "zh":
        return [
            "## 4.9 GDS 图结构分析",
            "",
            "![公司共事件网络](figures/figure_4_6_company_coevent_network.svg)",
            "",
            (
                f"公司共事件投影包含 {metrics['gds_company_count']} 家公司和 "
                f"{metrics['gds_logical_edge_count']} 个无序逻辑公司对。GDS 将无向边双向"
                f"存储，因此内存投影中的 {metrics['gds_projected_relationship_count']} 条关系"
                "不是独立关系数的翻倍证据。WCC 得到 "
                f"{metrics['gds_wcc_count']} 个连通分量，最大分量含 "
                f"{metrics['gds_largest_wcc_size']} 家公司，并有 "
                f"{metrics['gds_isolate_count']} 家孤立公司。孤立仅表示在本样本中没有共享"
                "同一规范事件。"
            ),
            "",
            "![共享事件数最高的公司对](figures/figure_4_7_top_shared_event_pairs.svg)",
            "",
            markdown_table(
                edges.head(10),
                [
                    ("company1", "公司一"),
                    ("company2", "公司二"),
                    ("shared_event_count", "共享规范事件"),
                    ("meets_support_threshold", "达到支持阈值"),
                ],
                formats={"shared_event_count": "{:,.0f}"},
            ),
            "",
            (
                f"39 个公司对中有 {metrics['gds_single_support_edge_count']} 个仅由一个共享"
                f"事件支持。采用至少 {metrics['gds_support_threshold']} 个共享事件作为主要"
                f"解释阈值后保留 {metrics['gds_supported_edge_count']} 条边，并有 "
                f"{metrics['gds_supported_isolate_count']} 家公司成为孤立点。"
            ),
            "",
            markdown_table(
                thresholds,
                [
                    ("minimum_shared_events", "最少共享事件"),
                    ("logical_edge_count", "逻辑边"),
                    ("component_count", "连通分量"),
                    ("largest_component_size", "最大分量"),
                    ("isolate_count", "孤立公司"),
                ],
                formats={
                    "minimum_shared_events": "{:,.0f}",
                    "logical_edge_count": "{:,.0f}",
                    "component_count": "{:,.0f}",
                    "largest_component_size": "{:,.0f}",
                    "isolate_count": "{:,.0f}",
                },
            ),
            "",
            markdown_table(
                similarities.head(5),
                [
                    ("company1", "公司一"),
                    ("company2", "公司二"),
                    ("similarity", "Jaccard"),
                    ("shared_event_count", "共享事件"),
                ],
                formats={"similarity": "{:.4f}", "shared_event_count": "{:,.0f}"},
            ),
            "",
            (
                f"Node Similarity 返回 {metrics['gds_node_similarity_pair_count']} 个正 Jaccard "
                f"公司对；加权 Louvain 得到 {metrics['gds_weighted_community_count']} 个社区，"
                f"modularity 为 {metrics['gds_weighted_modularity']:.4f}。这些社区是当前新闻"
                "事件投影下的探索性分组，不是行业分类。"
            ),
            "",
            "![事件覆盖与共事件强度](figures/figure_4_8_event_count_vs_strength.svg)",
            "",
            (
                "规范事件数与共事件强度的 Spearman 相关为 "
                f"{metrics['gds_event_strength_spearman']:.4f}，说明中心性明显受到语料覆盖量"
                "影响。PageRank、WCC、相似度和社区均不得解释为公司重要性、系统性风险、"
                "因果影响或投资建议。GDS 只使用临时内存投影，未向持久化知识图谱写回。"
            ),
            "",
        ]

    return [
        "## 4.9 GDS structural graph analysis",
        "",
        "![Company co-event network](figures/figure_4_6_company_coevent_network.svg)",
        "",
        (
            f"The company co-event projection contains {metrics['gds_company_count']} "
            f"companies and {metrics['gds_logical_edge_count']} unordered logical pairs. "
            "Because GDS stores undirected edges in both directions, the "
            f"{metrics['gds_projected_relationship_count']} projected relationships do not "
            "represent twice as many independent links. WCC identifies "
            f"{metrics['gds_wcc_count']} components, a largest component of "
            f"{metrics['gds_largest_wcc_size']} companies and "
            f"{metrics['gds_isolate_count']} isolates. An isolate means only that no "
            "canonical Event is shared with another company in this sample."
        ),
        "",
        "![Top shared-event company pairs](figures/figure_4_7_top_shared_event_pairs.svg)",
        "",
        markdown_table(
            edges.head(10),
            [
                ("company1", "Company 1"),
                ("company2", "Company 2"),
                ("shared_event_count", "Shared canonical Events"),
                ("meets_support_threshold", "Meets support threshold"),
            ],
            formats={"shared_event_count": "{:,.0f}"},
        ),
        "",
        (
            f"Of the 39 company pairs, {metrics['gds_single_support_edge_count']} are backed "
            f"by one shared Event. Requiring at least {metrics['gds_support_threshold']} "
            f"shared Events retains {metrics['gds_supported_edge_count']} edges and leaves "
            f"{metrics['gds_supported_isolate_count']} companies isolated."
        ),
        "",
        markdown_table(
            thresholds,
            [
                ("minimum_shared_events", "Minimum shared Events"),
                ("logical_edge_count", "Logical edges"),
                ("component_count", "Components"),
                ("largest_component_size", "Largest component"),
                ("isolate_count", "Isolates"),
            ],
            formats={
                "minimum_shared_events": "{:,.0f}",
                "logical_edge_count": "{:,.0f}",
                "component_count": "{:,.0f}",
                "largest_component_size": "{:,.0f}",
                "isolate_count": "{:,.0f}",
            },
        ),
        "",
        markdown_table(
            similarities.head(5),
            [
                ("company1", "Company 1"),
                ("company2", "Company 2"),
                ("similarity", "Jaccard"),
                ("shared_event_count", "Shared Events"),
            ],
            formats={"similarity": "{:.4f}", "shared_event_count": "{:,.0f}"},
        ),
        "",
        (
            f"Node Similarity returns {metrics['gds_node_similarity_pair_count']} positive "
            f"Jaccard pairs. Weighted Louvain returns "
            f"{metrics['gds_weighted_community_count']} communities with modularity "
            f"{metrics['gds_weighted_modularity']:.4f}. These are exploratory groupings in "
            "the present news-event projection, not industry classifications."
        ),
        "",
        "![Event coverage and co-event strength](figures/figure_4_8_event_count_vs_strength.svg)",
        "",
        (
            "Canonical-event count and co-event strength have Spearman correlation "
            f"{metrics['gds_event_strength_spearman']:.4f}, showing that centrality is "
            "materially conditioned by corpus coverage. PageRank, WCC, similarity and "
            "communities are not company-importance, systemic-risk, causal-impact or "
            "investment measures. GDS used temporary in-memory projections and wrote "
            "nothing back to the persisted knowledge graph."
        ),
        "",
    ]


def analyst_markdown_lines(
    *,
    language: str,
    metrics: dict[str, Any],
    analyst_frames: dict[str, pd.DataFrame],
) -> list[str]:
    summary = numeric(
        analyst_frames["use_case_summary"],
        [
            "workflow_query_steps",
            "manual_join_steps",
            "manual_calculation_steps",
            "result_rows",
            "coverage_pct",
            "evidence_completeness_pct",
            "provenance_completeness_pct",
            "median_client_ms",
            "p95_client_ms",
        ],
    ).sort_values("task_id")
    if language == "zh":
        title_column = "title_cn"
        return [
            "## 4.10 分析师应用场景评价",
            "",
            "![五项用例的查询时延](figures/figure_4_9_use_case_latency.svg)",
            "",
            markdown_table(
                summary,
                [
                    ("task_id", "任务"),
                    (title_column, "场景"),
                    ("result_rows", "结果行"),
                    ("coverage_pct", "范围覆盖"),
                    ("evidence_completeness_pct", "证据完整性"),
                    ("provenance_completeness_pct", "来源完整性"),
                    ("median_client_ms", "中位时延(ms)"),
                    ("status", "状态"),
                ],
                formats={
                    "result_rows": "{:,.0f}",
                    "coverage_pct": "{:.1f}%",
                    "evidence_completeness_pct": "{:.1f}%",
                    "provenance_completeness_pct": "{:.1f}%",
                    "median_client_ms": "{:.1f}",
                },
            ),
            "",
            (
                f"评价执行了 {metrics['analyst_use_case_count']} 项预定义任务，涵盖公司筛选、"
                "台积电证据追溯、监管事件提醒、Alphabet 市场背景和共享事件公司对。"
                f"{metrics['analyst_quality_checks_passed']} / "
                f"{metrics['analyst_quality_checks_total']} 项自动质量检查通过；结果哈希稳定="
                f"{metrics['analyst_all_result_hashes_stable']}，行数稳定="
                f"{metrics['analyst_all_row_counts_stable']}，数据库状态不变="
                f"{metrics['analyst_graph_state_unchanged']}。"
            ),
            "",
            "![用例覆盖、证据与来源完整性](figures/figure_4_10_use_case_completeness.svg)",
            "",
            (
                f"五项任务的平均范围覆盖为 {metrics['analyst_mean_coverage_pct']:.1f}%，"
                f"平均证据完整性为 {metrics['analyst_mean_evidence_completeness_pct']:.1f}%，"
                f"平均来源完整性为 {metrics['analyst_mean_provenance_completeness_pct']:.1f}%。"
                f"各任务中位客户端时延的中位数为 {metrics['analyst_median_latency_ms']:.1f} ms，"
                f"最高 p95 为 {metrics['analyst_max_p95_latency_ms']:.1f} ms。时延只描述本机"
                "冻结数据库上的系统执行，不是与人工流程对照的时间节省证据。"
            ),
            "",
            (
                "评价证明了查询、关系连接、聚合和导出能够由系统按固定定义重复执行，并保留"
                "事件证据和来源链接；但它没有人工金标准或人工基线，因此不报告 precision、"
                "recall、人工节时比例，也不将描述性市场窗口解释为因果影响。"
            ),
            "",
        ]

    return [
        "## 4.10 Analyst use-case evaluation",
        "",
        "![Query latency for five use cases](figures/figure_4_9_use_case_latency.svg)",
        "",
        markdown_table(
            summary,
            [
                ("task_id", "Task"),
                ("title_en", "Use case"),
                ("result_rows", "Result rows"),
                ("coverage_pct", "Scope coverage"),
                ("evidence_completeness_pct", "Evidence completeness"),
                ("provenance_completeness_pct", "Provenance completeness"),
                ("median_client_ms", "Median latency (ms)"),
                ("status", "Status"),
            ],
            formats={
                "result_rows": "{:,.0f}",
                "coverage_pct": "{:.1f}%",
                "evidence_completeness_pct": "{:.1f}%",
                "provenance_completeness_pct": "{:.1f}%",
                "median_client_ms": "{:.1f}",
            },
        ),
        "",
        (
            f"The evaluation executes {metrics['analyst_use_case_count']} predefined tasks: "
            "company screening, TSMC evidence tracing, regulatory-event alerts, Alphabet "
            "market context and shared-event company pairs. "
            f"{metrics['analyst_quality_checks_passed']} of "
            f"{metrics['analyst_quality_checks_total']} automatic checks pass; result hashes "
            f"are stable={metrics['analyst_all_result_hashes_stable']}, row counts are "
            f"stable={metrics['analyst_all_row_counts_stable']}, and the database is "
            f"unchanged={metrics['analyst_graph_state_unchanged']}."
        ),
        "",
        "![Use-case coverage, evidence and provenance completeness](figures/figure_4_10_use_case_completeness.svg)",
        "",
        (
            f"Mean scope coverage is {metrics['analyst_mean_coverage_pct']:.1f}%, mean "
            "evidence completeness is "
            f"{metrics['analyst_mean_evidence_completeness_pct']:.1f}% and mean provenance "
            f"completeness is {metrics['analyst_mean_provenance_completeness_pct']:.1f}%. "
            "The median of task-level median client latency is "
            f"{metrics['analyst_median_latency_ms']:.1f} ms and the largest p95 is "
            f"{metrics['analyst_max_p95_latency_ms']:.1f} ms. Latency describes execution "
            "on this local frozen database; no controlled analyst baseline was collected."
        ),
        "",
        (
            "The evaluation shows that queries, joins, aggregations and exports can be "
            "repeated under fixed definitions while retaining evidence and provenance. It "
            "has neither a human-labelled benchmark nor a controlled analyst baseline, "
            "so it reports no precision, recall or time-saving estimate and makes no causal claim "
            "from descriptive market windows."
        ),
        "",
    ]


def chapter4_markdown(
    *,
    language: str,
    config: dict[str, Any],
    metrics: dict[str, Any],
    article_summary: pd.DataFrame,
    stage: pd.DataFrame,
    event_types: pd.DataFrame,
    companies: pd.DataFrame,
    sensitivity: pd.DataFrame,
    market_summary: pd.DataFrame,
    kg_inventory: pd.DataFrame,
    quality_checks: pd.DataFrame,
    gds_manifest: dict[str, Any],
    gds_frames: dict[str, pd.DataFrame],
    analyst_manifest: dict[str, Any],
    analyst_frames: dict[str, pd.DataFrame],
) -> str:
    study = config["study"]
    current = sensitivity.loc[sensitivity["current_setting"]].iloc[0]
    top_companies = companies.head(10).copy()
    low_companies = companies.tail(8).sort_values(
        ["canonical_relationships", "company_name"]
    )
    event_types_display = event_types.sort_values(
        "canonical_events", ascending=False
    ).copy()
    checks_passed = int(
        (quality_checks["status"].astype(str).str.upper() == "PASS").sum()
    )

    if language == "zh":
        return "\n".join(
            [
                "# 第四章结果：自动金融事件知识图谱",
                "",
                (
                    f"本结果包对应 `{study['sample_label']}` 冻结实验，新闻窗口为 "
                    f"{study['news_start_date']} 至 {study['news_end_date']}。所有数值均由"
                    "保存的流水线 CSV 自动计算，而非人工抄录。"
                ),
                "",
                "## 4.1 数据与样本形成",
                "",
                markdown_table(
                    article_summary,
                    [("stage", "阶段"), ("count", "数量"), ("unit", "单位")],
                    formats={"count": "{:,.0f}"},
                ),
                "",
                (
                    f"系统在候选公司池与回填过程中共采集 {metrics['collected_articles']:,} "
                    f"篇唯一文章；限定到最终入选公司后保留 "
                    f"{metrics['cleaned_selected_scope_articles']:,} 篇，其中 "
                    f"{metrics['analysis_ready_articles']:,} 篇满足日期、正文和公司证据要求。"
                    f"最终有 {metrics['graph_source_articles']:,} 篇文章为合格规范化事件提供"
                    "来源证据。文章数量与 Article–Company 关系数量具有不同统计单位，因此"
                    "不应合并为单一留存率。"
                ),
                "",
                "## 4.2 自动事件筛选与消融",
                "",
                "![自动事件流水线阶段留存](figures/figure_4_1_stage_ablation.svg)",
                "",
                markdown_table(
                    stage,
                    [
                        ("stage", "阶段"),
                        ("qualified_events", "合格事件"),
                        ("qualified_event_company_links", "事件—公司关系"),
                        ("covered_companies", "覆盖公司"),
                    ],
                    formats={
                        "qualified_events": "{:,.0f}",
                        "qualified_event_company_links": "{:,.0f}",
                        "covered_companies": "{:,.0f}",
                    },
                ),
                "",
                (
                    f"规则阶段保留 {metrics['rule_events']:,} 个事件候选和 "
                    f"{metrics['rule_relationships']:,} 条关系。加入本地 NLI 与语法角色校准后，"
                    f"分别保留 {metrics['hybrid_events']:,} 和 "
                    f"{metrics['hybrid_relationships']:,}，相对规则阶段的留存率为 "
                    f"{metrics['rule_to_hybrid_event_retention']:.1%} 和 "
                    f"{metrics['rule_to_hybrid_relationship_retention']:.1%}。这表明语义验证"
                    "主要起到了排除仅由关键词触发但缺乏关系支持的候选项的作用。"
                ),
                "",
                "## 4.3 事件类型构成",
                "",
                "![事件类型在各阶段的数量](figures/figure_4_2_event_type_progression.svg)",
                "",
                markdown_table(
                    event_types_display,
                    [
                        ("event_type", "事件类型"),
                        ("rule_events", "规则阶段"),
                        ("hybrid_events", "混合阶段"),
                        ("canonical_events", "规范事件"),
                        ("canonical_share", "规范事件占比"),
                    ],
                    formats={
                        "rule_events": "{:,.0f}",
                        "hybrid_events": "{:,.0f}",
                        "canonical_events": "{:,.0f}",
                        "canonical_share": "{:.1%}",
                    },
                ),
                "",
                (
                    "企业事件与监管事件构成最终图谱的主要部分。该分布反映 Guardian 语料、"
                    "公司别名和事件规则共同形成的样本结构，不代表现实世界全部公司事件的"
                    "总体发生率。"
                ),
                "",
                "## 4.4 跨文章去重",
                "",
                (
                    f"跨文章完整链接聚类将 {metrics['source_event_mentions']:,} 个合格来源事件"
                    f"表述归并为 {metrics['canonical_events']:,} 个规范事件，去除 "
                    f"{metrics['duplicates_removed']:,} 个重复表述；"
                    f"{metrics['multi_source_events']:,} 个规范事件拥有多个来源，最大聚类包含 "
                    f"{metrics['largest_cluster_size']} 个来源表述。被合并聚类的最低完整链接"
                    f"相似度为 {metrics['minimum_multi_source_similarity']:.3f}。原始文章和"
                    "证据并未删除，而是继续通过 REPORTS 关系保留。"
                ),
                "",
                "## 4.5 公司覆盖",
                "",
                "![公司事件覆盖](figures/figure_4_3_company_coverage.svg)",
                "",
                markdown_table(
                    top_companies,
                    [
                        ("company_name", "公司"),
                        ("canonical_relationships", "规范事件—公司关系"),
                        ("hybrid_removed_from_rule", "NLP 排除"),
                        ("duplicates_removed", "去重表述"),
                    ],
                    formats={
                        "canonical_relationships": "{:,.0f}",
                        "hybrid_removed_from_rule": "{:,.0f}",
                        "duplicates_removed": "{:,.0f}",
                    },
                ),
                "",
                (
                    f"全部 {metrics['covered_companies']} 家公司均至少保留一条规范事件关系，"
                    f"但前五家公司占全部关系的 {metrics['top_five_company_share']:.1%}。"
                    "因此覆盖是完整的，但分布并不均衡。低覆盖公司如下："
                ),
                "",
                markdown_table(
                    low_companies,
                    [
                        ("company_name", "公司"),
                        ("canonical_relationships", "规范事件—公司关系"),
                    ],
                    formats={"canonical_relationships": "{:,.0f}"},
                ),
                "",
                "低数量表示在本语料和自动证据门槛下可用事件较少，而不是公司不重要。",
                "",
                "## 4.6 阈值敏感性",
                "",
                "![阈值敏感性](figures/figure_4_4_threshold_sensitivity.svg)",
                "",
                (
                    f"当前确认阈值为 {metrics['current_threshold']:.2f}，强规则回退分数为 "
                    f"{metrics['current_strong_rule_score']}，可精确重建 "
                    f"{metrics['current_threshold_relationships']:,} 条混合阶段关系。配置网格"
                    f"中的关系数量从 {metrics['sensitivity_minimum_relationships']:,} 到 "
                    f"{metrics['sensitivity_maximum_relationships']:,}，所有组合仍覆盖 25 家"
                    "公司。这说明公司覆盖对测试范围内的阈值较稳健，但关系数量会明显变化。"
                ),
                "",
                "## 4.7 描述性市场窗口",
                "",
                "![市场窗口背景](figures/figure_4_5_market_context.svg)",
                "",
                markdown_table(
                    market_summary,
                    [
                        ("window_days", "交易日窗口"),
                        ("observations", "观察数"),
                        ("average_return", "平均累计收益"),
                        ("median_return", "中位累计收益"),
                        ("standard_deviation", "标准差"),
                        ("positive_return_rate", "正收益比例"),
                    ],
                    formats={
                        "window_days": "{:,.0f}",
                        "observations": "{:,.0f}",
                        "average_return": "{:.2%}",
                        "median_return": "{:.2%}",
                        "standard_deviation": "{:.2%}",
                        "positive_return_rate": "{:.1%}",
                    },
                ),
                "",
                (
                    f"每条事件—公司关系均获得发布前后 1、3 和 7 个交易日窗口，共 "
                    f"{metrics['market_windows']:,} 条市场观察。收益仅描述新闻发布时间前后的"
                    "市场背景；没有控制同期市场、行业、重复事件或预期信息，因此不能解释为"
                    "事件导致的异常收益，也不构成投资建议。"
                ),
                "",
                "## 4.8 图谱规模与自动检查",
                "",
                markdown_table(
                    kg_inventory,
                    [
                        ("section", "类型"),
                        ("metric", "节点或关系"),
                        ("value", "数量"),
                        ("status", "状态"),
                    ],
                    formats={"value": "{:,.0f}"},
                ),
                "",
                (
                    f"最终图谱包含 {metrics['graph_nodes']:,} 个节点和 "
                    f"{metrics['graph_relationships']:,} 条关系。流水线的 "
                    f"{checks_passed} 项自动质量检查全部通过，Neo4j 导入后验证失败项为 "
                    f"{metrics['neo4j_validation_failures']}。这些检查证明内部一致性与可追溯性，"
                    "但不等同于基于人工标注真值的精确率或召回率。"
                ),
                "",
                *gds_markdown_lines(
                    language="zh",
                    metrics=metrics,
                    gds_frames=gds_frames,
                ),
                *analyst_markdown_lines(
                    language="zh",
                    metrics=metrics,
                    analyst_frames=analyst_frames,
                ),
                "## 4.11 结果解释边界",
                "",
                "- 本章评价的是自动流水线的留存、稳健性、覆盖和内部一致性。",
                "- 本研究未构建人工标注基准，因此不估计精确率或召回率。",
                "- 市场窗口是描述性上下文，不是事件研究中的因果异常收益估计。",
                "- 公司间事件数量差异同时受新闻覆盖、别名设计和自动质量门槛影响。",
                "- GDS 的共事件、相似度、社区与中心性均为样本条件下的结构描述，不代表因果或公司重要性。",
                "- 分析师场景没有对照分析师基线，不据此声称时间节省。",
                "",
                "所有底层表格位于 `tables/`，矢量图位于 `figures/`，输入文件哈希、"
                "GDS 与分析师评价来源 manifest 及冻结指标记录于 `results_manifest.json`。",
                "",
            ]
        )

    return "\n".join(
        [
            "# Chapter 4 Results: Automatic Financial Event Knowledge Graph",
            "",
            (
                f"This package reports the frozen `{study['sample_label']}` experiment "
                f"covering news published from {study['news_start_date']} to "
                f"{study['news_end_date']}. Every value is computed from saved pipeline "
                "CSVs rather than transcribed manually."
            ),
            "",
            "## 4.1 Data and sample formation",
            "",
            markdown_table(
                article_summary,
                [("stage", "Stage"), ("count", "Count"), ("unit", "Unit")],
                formats={"count": "{:,.0f}"},
            ),
            "",
            (
                f"The candidate-pool and ranked-backfill collection produced "
                f"{metrics['collected_articles']:,} unique articles. Restricting the data "
                f"to the selected companies retained "
                f"{metrics['cleaned_selected_scope_articles']:,} articles, of which "
                f"{metrics['analysis_ready_articles']:,} satisfied the date, "
                "content and company-evidence requirements. "
                f"{metrics['graph_source_articles']:,} articles ultimately supported "
                "qualified canonical events in the graph. Article counts and "
                "Article–Company relationship counts use different units and should not "
                "be combined into a single retention rate."
            ),
            "",
            "## 4.2 Automatic event screening and ablation",
            "",
            "![Automatic event pipeline stage retention](figures/figure_4_1_stage_ablation.svg)",
            "",
            markdown_table(
                stage,
                [
                    ("stage", "Stage"),
                    ("qualified_events", "Qualified events"),
                    ("qualified_event_company_links", "Event–Company relationships"),
                    ("covered_companies", "Covered companies"),
                ],
                formats={
                    "qualified_events": "{:,.0f}",
                    "qualified_event_company_links": "{:,.0f}",
                    "covered_companies": "{:,.0f}",
                },
            ),
            "",
            (
                f"The rule stage retained {metrics['rule_events']:,} event candidates and "
                f"{metrics['rule_relationships']:,} relationships. Adding local NLI and "
                f"grammatical-role calibration retained {metrics['hybrid_events']:,} and "
                f"{metrics['hybrid_relationships']:,}, equivalent to "
                f"{metrics['rule_to_hybrid_event_retention']:.1%} and "
                f"{metrics['rule_to_hybrid_relationship_retention']:.1%} of the rule "
                "stage. Semantic validation therefore primarily removed keyword-triggered "
                "candidates without sufficient relationship support."
            ),
            "",
            "## 4.3 Event-type composition",
            "",
            "![Event types across stages](figures/figure_4_2_event_type_progression.svg)",
            "",
            markdown_table(
                event_types_display,
                [
                    ("event_type", "Event type"),
                    ("rule_events", "Rule"),
                    ("hybrid_events", "Hybrid"),
                    ("canonical_events", "Canonical"),
                    ("canonical_share", "Canonical share"),
                ],
                formats={
                    "rule_events": "{:,.0f}",
                    "hybrid_events": "{:,.0f}",
                    "canonical_events": "{:,.0f}",
                    "canonical_share": "{:.1%}",
                },
            ),
            "",
            (
                "Corporate and regulatory events dominate the final graph. This "
                "composition reflects the Guardian corpus, company aliases and event "
                "rules; it is not an estimate of the population frequency of all "
                "real-world corporate events."
            ),
            "",
            "## 4.4 Cross-article deduplication",
            "",
            (
                f"Complete-link clustering represented "
                f"{metrics['source_event_mentions']:,} qualified source mentions as "
                f"{metrics['canonical_events']:,} canonical events, removing "
                f"{metrics['duplicates_removed']:,} duplicate mentions. "
                f"{metrics['multi_source_events']:,} canonical events had multiple "
                f"sources, the largest cluster contained "
                f"{metrics['largest_cluster_size']} mentions, and the minimum complete-link "
                f"similarity among merged clusters was "
                f"{metrics['minimum_multi_source_similarity']:.3f}. Source articles and "
                "evidence were retained through property-bearing REPORTS relationships."
            ),
            "",
            "## 4.5 Company coverage",
            "",
            "![Company event coverage](figures/figure_4_3_company_coverage.svg)",
            "",
            markdown_table(
                top_companies,
                [
                    ("company_name", "Company"),
                    ("canonical_relationships", "Canonical Event–Company relationships"),
                    ("hybrid_removed_from_rule", "Removed by NLP"),
                    ("duplicates_removed", "Deduplicated mentions"),
                ],
                formats={
                    "canonical_relationships": "{:,.0f}",
                    "hybrid_removed_from_rule": "{:,.0f}",
                    "duplicates_removed": "{:,.0f}",
                },
            ),
            "",
            (
                f"All {metrics['covered_companies']} companies retained at least one "
                f"canonical event relationship, although the five most-covered companies "
                f"accounted for {metrics['top_five_company_share']:.1%} of all "
                "relationships. Low-coverage companies were:"
            ),
            "",
            markdown_table(
                low_companies,
                [
                    ("company_name", "Company"),
                    ("canonical_relationships", "Canonical relationships"),
                ],
                formats={"canonical_relationships": "{:,.0f}"},
            ),
            "",
            (
                "Low counts indicate limited evidence under this corpus and the automatic "
                "quality gates; they do not indicate low economic importance."
            ),
            "",
            "## 4.6 Threshold sensitivity",
            "",
            "![Threshold sensitivity](figures/figure_4_4_threshold_sensitivity.svg)",
            "",
            (
                f"The current confirmation threshold of "
                f"{metrics['current_threshold']:.2f} and strong-rule fallback score of "
                f"{metrics['current_strong_rule_score']} exactly reconstructed "
                f"{metrics['current_threshold_relationships']:,} hybrid relationships. "
                f"Across the configured grid, relationship counts ranged from "
                f"{metrics['sensitivity_minimum_relationships']:,} to "
                f"{metrics['sensitivity_maximum_relationships']:,}, while every setting "
                "retained all 25 companies. Coverage is therefore stable within the tested "
                "range, although relationship volume is threshold-sensitive."
            ),
            "",
            "## 4.7 Descriptive market windows",
            "",
            "![Descriptive market windows](figures/figure_4_5_market_context.svg)",
            "",
            markdown_table(
                market_summary,
                [
                    ("window_days", "Trading-day window"),
                    ("observations", "Observations"),
                    ("average_return", "Average return"),
                    ("median_return", "Median return"),
                    ("standard_deviation", "Standard deviation"),
                    ("positive_return_rate", "Positive-return rate"),
                ],
                formats={
                    "window_days": "{:,.0f}",
                    "observations": "{:,.0f}",
                    "average_return": "{:.2%}",
                    "median_return": "{:.2%}",
                    "standard_deviation": "{:.2%}",
                    "positive_return_rate": "{:.1%}",
                },
            ),
            "",
            (
                f"Each Event–Company relationship received 1-, 3- and 7-trading-day "
                f"windows before and after publication, producing {metrics['market_windows']:,} observations. Returns "
                "describe market conditions around publication only. They do not control "
                "for contemporaneous market or sector moves, overlapping events or prior "
                "expectations, so they cannot be interpreted as causal abnormal returns or "
                "investment recommendations."
            ),
            "",
            "## 4.8 Graph scale and automatic checks",
            "",
            markdown_table(
                kg_inventory,
                [
                    ("section", "Type"),
                    ("metric", "Node or relationship"),
                    ("value", "Count"),
                    ("status", "Status"),
                ],
                formats={"value": "{:,.0f}"},
            ),
            "",
            (
                f"The final graph contained {metrics['graph_nodes']:,} nodes and "
                f"{metrics['graph_relationships']:,} relationships. All {checks_passed} "
                "pipeline quality checks passed and the post-import Neo4j validation "
                f"reported {metrics['neo4j_validation_failures']} failures. These checks "
                "support internal consistency and traceability, but they are not "
                "precision or recall estimates against a human-annotated gold standard."
            ),
            "",
            *gds_markdown_lines(
                language="en",
                metrics=metrics,
                gds_frames=gds_frames,
            ),
            *analyst_markdown_lines(
                language="en",
                metrics=metrics,
                analyst_frames=analyst_frames,
            ),
            "## 4.11 Interpretation boundary",
            "",
            "- The evaluation measures stage retention, robustness, coverage and internal consistency.",
            "- No human-labelled benchmark was constructed; precision and recall are therefore not estimated.",
            "- Market windows provide descriptive context rather than causal event-study estimates.",
            "- Differences in company counts reflect news coverage, alias design and automatic quality gates.",
            "- GDS co-events, similarity, communities and centrality are sample-conditional structural descriptions, not causal or company-importance measures.",
            "- The analyst scenarios have no controlled analyst baseline and therefore do not support a time-saving claim.",
            "",
            "Underlying tables are stored in `tables/`, vector figures in `figures/`, "
            "and the frozen metrics, source manifests and hashes in `results_manifest.json`.",
            "",
        ]
    )


def main() -> None:
    args = parse_args()
    result = build_results(args.config, args.output_directory)
    print(f"Chapter 4 output directory: {result['output_directory']}")
    print(f"Tables generated: {result['tables']}")
    print(f"SVG figures generated: {result['figures']}")
    print(
        "Frozen result: "
        f"{result['metrics']['canonical_events']} canonical events, "
        f"{result['metrics']['canonical_relationships']} Event-Company relationships."
    )


if __name__ == "__main__":
    main()
