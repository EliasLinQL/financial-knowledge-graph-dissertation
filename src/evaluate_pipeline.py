"""Generate automatic ablation, coverage and threshold-sensitivity reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import yaml


EVENT_COLUMNS = {
    "event_id",
    "event_type",
    "recommended_for_graph",
}
LINK_COLUMNS = {
    "event_id",
    "company_id",
    "event_type",
    "recommended_for_graph",
}
HYBRID_LINK_COLUMNS = LINK_COLUMNS | {
    "rule_recommended_for_graph",
    "relationship_focus_score",
    "nlp_relationship_label",
    "nlp_relationship_score",
    "nlp_positive_probability",
    "hybrid_decision_reason",
}
COMPANY_COLUMNS = {
    "company_id",
    "company_name",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare rule, hybrid NLP and canonical Event stages, and simulate "
            "configured NLP decision thresholds without changing the graph."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the project YAML configuration.",
    )
    parser.add_argument(
        "--mode",
        choices=("test", "full"),
        default="full",
        help="Guardian collection mode to evaluate.",
    )
    parser.add_argument("--output-directory", type=Path)
    return parser.parse_args()


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError("The configuration file must contain a YAML mapping.")
    return value


def resolve_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def parse_bool(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def numeric(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def read_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    frame = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )
    return frame


def recommended(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["recommended_for_graph"].map(parse_bool)].copy()


def safe_percentage(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator) * 100.0, 3)


def stage_summary(
    stages: Iterable[
        tuple[str, pd.DataFrame, pd.DataFrame, str]
    ],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    previous_events: int | None = None
    previous_links: int | None = None
    for stage, events, links, description in stages:
        selected_events = recommended(events)
        selected_links = recommended(links)
        event_count = int(selected_events["event_id"].nunique())
        link_count = int(
            selected_links[["event_id", "company_id"]].drop_duplicates().shape[0]
        )
        rows.append(
            {
                "stage": stage,
                "description": description,
                "candidate_events": len(events),
                "qualified_events": event_count,
                "candidate_event_company_links": len(links),
                "qualified_event_company_links": link_count,
                "covered_companies": int(selected_links["company_id"].nunique()),
                "event_retention_from_previous_pct": (
                    ""
                    if previous_events is None
                    else safe_percentage(event_count, previous_events)
                ),
                "relationship_retention_from_previous_pct": (
                    ""
                    if previous_links is None
                    else safe_percentage(link_count, previous_links)
                ),
            }
        )
        previous_events = event_count
        previous_links = link_count
    return pd.DataFrame(rows)


def company_stage_summary(
    companies: pd.DataFrame,
    stage_links: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    selected_by_stage = {
        stage: recommended(frame) for stage, frame in stage_links.items()
    }
    for _, company in companies.sort_values("company_id").iterrows():
        company_id = str(company["company_id"])
        row: dict[str, Any] = {
            "company_id": company_id,
            "company_name": company["company_name"],
        }
        for stage, links in selected_by_stage.items():
            company_links = links[links["company_id"] == company_id]
            row[f"{stage}_events"] = int(company_links["event_id"].nunique())
            row[f"{stage}_relationships"] = len(company_links)
        row["hybrid_removed_from_rule"] = (
            int(row["rule_relationships"]) - int(row["hybrid_relationships"])
        )
        row["duplicates_removed"] = (
            int(row["hybrid_relationships"])
            - int(row["canonical_relationships"])
        )
        rows.append(row)
    return pd.DataFrame(rows)


def event_type_stage_summary(
    stage_events: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    event_types = sorted(
        {
            str(value)
            for frame in stage_events.values()
            for value in frame["event_type"]
            if str(value).strip()
        }
    )
    rows: list[dict[str, Any]] = []
    for event_type in event_types:
        row: dict[str, Any] = {"event_type": event_type}
        for stage, frame in stage_events.items():
            selected = recommended(frame)
            row[f"{stage}_events"] = int(
                selected.loc[
                    selected["event_type"] == event_type, "event_id"
                ].nunique()
            )
        row["nlp_removed"] = int(row["rule_events"]) - int(row["hybrid_events"])
        row["duplicates_removed"] = (
            int(row["hybrid_events"]) - int(row["canonical_events"])
        )
        rows.append(row)
    return pd.DataFrame(rows)


def simulate_relationship_gate(
    hybrid_links: pd.DataFrame,
    accepted_labels: set[str],
    confirmation_threshold: float,
    strong_rule_focus_score: int,
    *,
    enable_nlp_rescue: bool = False,
    rescue_threshold: float = 0.65,
    minimum_rescue_focus_score: int = 2,
    event_scores: Mapping[str, float] | None = None,
    event_confidence_threshold: float = 0.35,
) -> pd.Series:
    rule_recommended = hybrid_links["rule_recommended_for_graph"].map(parse_bool)
    labels = hybrid_links["nlp_relationship_label"].astype(str)
    positive_label = labels.isin(accepted_labels)
    relationship_score = hybrid_links["nlp_relationship_score"].map(numeric)
    positive_probability = hybrid_links["nlp_positive_probability"].map(numeric)
    focus_score = hybrid_links["relationship_focus_score"].map(numeric)

    accepted = rule_recommended & positive_label & (
        (relationship_score >= confirmation_threshold)
        | (focus_score >= strong_rule_focus_score)
    )
    if not enable_nlp_rescue:
        return accepted

    score_map = event_scores or {}
    event_score = hybrid_links["event_id"].map(
        lambda event_id: numeric(score_map.get(str(event_id), 0.0))
    )
    rescued = (
        ~rule_recommended
        & positive_label
        & (relationship_score >= rescue_threshold)
        & (positive_probability >= rescue_threshold)
        & (focus_score >= minimum_rescue_focus_score)
        & (event_score >= event_confidence_threshold)
    )
    return accepted | rescued


def threshold_sensitivity(
    hybrid_events: pd.DataFrame,
    hybrid_links: pd.DataFrame,
    companies: pd.DataFrame,
    nlp_config: Mapping[str, Any],
    evaluation_config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    accepted_labels = {
        str(value) for value in nlp_config.get("accepted_relationship_labels", [])
    }
    confirmations = sorted(
        {
            float(value)
            for value in evaluation_config.get(
                "confirmation_thresholds",
                [0.25, 0.35, 0.45, 0.55],
            )
        }
    )
    strong_scores = sorted(
        {
            int(value)
            for value in evaluation_config.get(
                "strong_rule_focus_scores", [6, 7, 8]
            )
        }
    )
    current_confirmation = float(nlp_config.get("confirmation_threshold", 0.35))
    current_strong_score = int(nlp_config.get("strong_rule_focus_score", 7))
    event_scores = {
        str(row["event_id"]): numeric(row.get("nlp_event_score", 0.0))
        for _, row in hybrid_events.iterrows()
    }
    actual_pairs = set(
        map(
            tuple,
            recommended(hybrid_links)[["event_id", "company_id"]].to_records(
                index=False
            ),
        )
    )

    summary_rows: list[dict[str, Any]] = []
    company_rows: list[dict[str, Any]] = []
    for confirmation in confirmations:
        for strong_score in strong_scores:
            mask = simulate_relationship_gate(
                hybrid_links,
                accepted_labels,
                confirmation,
                strong_score,
                enable_nlp_rescue=bool(
                    nlp_config.get("enable_nlp_rescue", False)
                ),
                rescue_threshold=float(nlp_config.get("rescue_threshold", 0.65)),
                minimum_rescue_focus_score=int(
                    nlp_config.get("minimum_rescue_focus_score", 2)
                ),
                event_scores=event_scores,
                event_confidence_threshold=float(
                    nlp_config.get("event_confidence_threshold", 0.35)
                ),
            )
            selected = hybrid_links.loc[mask].copy()
            selected_pairs = set(
                map(
                    tuple,
                    selected[["event_id", "company_id"]].to_records(index=False),
                )
            )
            per_company_counts = {
                str(company_id): int(group["event_id"].nunique())
                for company_id, group in selected.groupby("company_id")
            }
            all_counts = [
                per_company_counts.get(str(company_id), 0)
                for company_id in companies["company_id"]
            ]
            current_setting = (
                confirmation == current_confirmation
                and strong_score == current_strong_score
            )
            summary_rows.append(
                {
                    "confirmation_threshold": confirmation,
                    "strong_rule_focus_score": strong_score,
                    "current_setting": current_setting,
                    "qualified_events": int(selected["event_id"].nunique()),
                    "qualified_event_company_links": len(selected_pairs),
                    "covered_companies": sum(count > 0 for count in all_counts),
                    "zero_event_companies": sum(count == 0 for count in all_counts),
                    "minimum_company_event_count": min(all_counts, default=0),
                    "median_company_event_count": (
                        float(pd.Series(all_counts).median()) if all_counts else 0.0
                    ),
                    "maximum_company_event_count": max(all_counts, default=0),
                    "pairs_added_vs_current": len(selected_pairs - actual_pairs),
                    "pairs_removed_vs_current": len(actual_pairs - selected_pairs),
                    "matches_current_output": selected_pairs == actual_pairs,
                }
            )
            for _, company in companies.iterrows():
                company_id = str(company["company_id"])
                company_rows.append(
                    {
                        "confirmation_threshold": confirmation,
                        "strong_rule_focus_score": strong_score,
                        "current_setting": current_setting,
                        "company_id": company_id,
                        "company_name": company["company_name"],
                        "qualified_events": per_company_counts.get(company_id, 0),
                    }
                )
    return pd.DataFrame(summary_rows), pd.DataFrame(company_rows)


def decision_reason_summary(hybrid_links: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for reason, group in hybrid_links.groupby("hybrid_decision_reason"):
        rows.append(
            {
                "decision_reason": reason,
                "candidate_relationships": len(group),
                "qualified_relationships": int(
                    group["recommended_for_graph"].map(parse_bool).sum()
                ),
                "distinct_events": int(group["event_id"].nunique()),
                "distinct_companies": int(group["company_id"].nunique()),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["qualified_relationships", "candidate_relationships"],
        ascending=False,
    )


def deduplication_summary(
    canonical_events: pd.DataFrame,
    canonical_links: pd.DataFrame,
) -> pd.DataFrame:
    selected_events = recommended(canonical_events)
    selected_links = recommended(canonical_links)
    sizes = selected_events["source_event_count"].map(numeric)
    similarities = selected_events["deduplication_min_similarity"].map(numeric)
    multi_source = selected_events[sizes > 1]
    rows = [
        {
            "metric": "qualified_canonical_events",
            "value": len(selected_events),
            "details": "Canonical Events after the final automatic gate",
        },
        {
            "metric": "qualified_canonical_relationships",
            "value": len(selected_links),
            "details": "Canonical Event-Company relationships",
        },
        {
            "metric": "source_event_mentions",
            "value": int(sizes.sum()),
            "details": "Original qualified Event mentions represented",
        },
        {
            "metric": "duplicates_removed",
            "value": int(sizes.sum()) - len(selected_events),
            "details": "Source mentions represented by an existing canonical Event",
        },
        {
            "metric": "multi_source_canonical_events",
            "value": len(multi_source),
            "details": "Canonical Events with more than one source mention",
        },
        {
            "metric": "largest_cluster_size",
            "value": int(sizes.max()) if not sizes.empty else 0,
            "details": "Maximum source mentions in one canonical Event",
        },
        {
            "metric": "minimum_multi_source_similarity",
            "value": (
                round(
                    float(
                        similarities.loc[multi_source.index].min()
                    ),
                    6,
                )
                if not multi_source.empty
                else ""
            ),
            "details": "Lowest complete-link similarity among merged clusters",
        },
    ]
    return pd.DataFrame(rows)


def automatic_checks(
    companies: pd.DataFrame,
    canonical_events: pd.DataFrame,
    canonical_links: pd.DataFrame,
    sensitivity: pd.DataFrame,
    graph_validation_path: Path,
) -> pd.DataFrame:
    checks: list[dict[str, Any]] = []

    def add(metric: str, passed: bool, actual: Any, expected: Any, detail: str) -> None:
        checks.append(
            {
                "metric": metric,
                "status": "PASS" if passed else "FAIL",
                "expected": expected,
                "actual": actual,
                "detail": detail,
            }
        )

    selected_links = recommended(canonical_links)
    covered = int(selected_links["company_id"].nunique())
    add(
        "canonical_company_coverage",
        covered == len(companies),
        covered,
        len(companies),
        "All selected companies should retain at least one canonical Event.",
    )
    duplicate_events = int(canonical_events["event_id"].duplicated().sum())
    add(
        "duplicate_canonical_event_ids",
        duplicate_events == 0,
        duplicate_events,
        0,
        "Canonical Event IDs must be unique.",
    )
    duplicate_pairs = int(
        canonical_links[["event_id", "company_id"]].duplicated().sum()
    )
    add(
        "duplicate_canonical_event_company_pairs",
        duplicate_pairs == 0,
        duplicate_pairs,
        0,
        "Canonical Event-Company pairs must be unique.",
    )
    missing_source = int(
        (
            canonical_links.get(
                "source_event_id", pd.Series("", index=canonical_links.index)
            ).astype(str).str.strip()
            == ""
        ).sum()
    )
    add(
        "canonical_relationships_without_source_event",
        missing_source == 0,
        missing_source,
        0,
        "Every canonical relationship must identify its supporting source Event.",
    )
    current_rows = sensitivity[sensitivity["current_setting"].map(parse_bool)]
    current_matches = bool(
        len(current_rows) == 1
        and parse_bool(current_rows.iloc[0]["matches_current_output"])
    )
    add(
        "current_threshold_reconstruction",
        current_matches,
        current_matches,
        True,
        "Saved scores should reproduce the currently imported hybrid decisions.",
    )

    graph_failures: Any = "not_available"
    graph_passed = True
    if graph_validation_path.exists():
        graph = read_csv(graph_validation_path, "Graph validation report")
        if "status" in graph.columns:
            graph_failures = int((graph["status"] == "FAIL").sum())
            graph_passed = graph_failures == 0
    add(
        "neo4j_graph_validation_failures",
        graph_passed,
        graph_failures,
        0 if graph_failures != "not_available" else "not_available",
        "Uses the latest query_kg.py validation export when available.",
    )
    return pd.DataFrame(checks)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def markdown_report(
    stage_frame: pd.DataFrame,
    sensitivity: pd.DataFrame,
    checks: pd.DataFrame,
    dedup: pd.DataFrame,
) -> str:
    stage_lines = [
        (
            f"| {row.stage} | {int(row.qualified_events)} | "
            f"{int(row.qualified_event_company_links)} | "
            f"{int(row.covered_companies)} |"
        )
        for row in stage_frame.itertuples()
    ]
    current = sensitivity[sensitivity["current_setting"].map(parse_bool)].iloc[0]
    most_permissive = sensitivity.sort_values(
        ["qualified_event_company_links", "confirmation_threshold"],
        ascending=[False, True],
    ).iloc[0]
    most_conservative = sensitivity.sort_values(
        ["qualified_event_company_links", "confirmation_threshold"],
        ascending=[True, False],
    ).iloc[0]
    failed_checks = int((checks["status"] == "FAIL").sum())
    dedup_values = {
        str(row["metric"]): row["value"] for _, row in dedup.iterrows()
    }
    return "\n".join(
        [
            "# Automated Pipeline Evaluation",
            "",
            "This report evaluates internal consistency, stage retention and ",
            "threshold sensitivity. It does not estimate ground-truth precision or ",
            "causal market effects.",
            "",
            "## Stage ablation",
            "",
            "| Stage | Qualified Events | Event-Company links | Covered companies |",
            "|---|---:|---:|---:|",
            *stage_lines,
            "",
            "## Current threshold reconstruction",
            "",
            (
                f"The configured confirmation threshold "
                f"({current.confirmation_threshold}) and strong-rule score "
                f"({int(current.strong_rule_focus_score)}) reconstruct "
                f"{int(current.qualified_event_company_links)} relationships. "
                f"Exact match with saved output: "
                f"{str(current.matches_current_output).lower()}."
            ),
            "",
            "Across the configured sensitivity grid:",
            "",
            (
                f"- Most permissive setting retains "
                f"{int(most_permissive.qualified_event_company_links)} relationships."
            ),
            (
                f"- Most conservative setting retains "
                f"{int(most_conservative.qualified_event_company_links)} relationships."
            ),
            (
                f"- Current canonical layer represents "
                f"{int(float(dedup_values['source_event_mentions']))} source mentions "
                f"as {int(float(dedup_values['qualified_canonical_events']))} "
                f"canonical Events."
            ),
            "",
            "## Automatic checks",
            "",
            f"Failed automatic checks: {failed_checks}.",
            "",
        ]
    )


def run() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project_root = config_path.parent.parent
    try:
        config = load_yaml(config_path)
        news = config["news_data"]
        stem = str(news["collection_modes"][args.mode]["output_stem"])
        processed = resolve_path(project_root, news["processed_output_directory"])
        nlp_config = config["nlp_enrichment"]
        evaluation_config = config.get("pipeline_evaluation", {})
        if not isinstance(evaluation_config, dict):
            raise ValueError("pipeline_evaluation must be a YAML mapping.")
        output_directory = (
            args.output_directory.resolve()
            if args.output_directory
            else resolve_path(
                project_root,
                evaluation_config.get(
                    "output_directory",
                    "data/evaluation/top25_12m",
                ),
            )
        )
        companies = read_csv(
            resolve_path(project_root, config["outputs"]["selected_companies"]),
            "Selected-company table",
        )
        rule_events = read_csv(
            processed / f"{stem}_event_candidates.csv", "Rule Event table"
        )
        rule_links = read_csv(
            processed / f"{stem}_event_company_links.csv",
            "Rule Event-Company table",
        )
        hybrid_events = read_csv(
            processed / f"{stem}_event_candidates_nlp.csv",
            "Hybrid Event table",
        )
        hybrid_links = read_csv(
            processed / f"{stem}_event_company_links_nlp.csv",
            "Hybrid Event-Company table",
        )
        canonical_events = read_csv(
            processed / f"{stem}_canonical_events.csv",
            "Canonical Event table",
        )
        canonical_links = read_csv(
            processed / f"{stem}_canonical_event_company_links.csv",
            "Canonical Event-Company table",
        )
        for label, frame in (
            ("Rule Event table", rule_events),
            ("Hybrid Event table", hybrid_events),
            ("Canonical Event table", canonical_events),
        ):
            require_columns(frame, EVENT_COLUMNS, label)
        for label, frame in (
            ("Rule Event-Company table", rule_links),
            ("Canonical Event-Company table", canonical_links),
        ):
            require_columns(frame, LINK_COLUMNS, label)
        require_columns(
            hybrid_links, HYBRID_LINK_COLUMNS, "Hybrid Event-Company table"
        )
        require_columns(companies, COMPANY_COLUMNS, "Selected-company table")
    except (FileNotFoundError, KeyError, ValueError, pd.errors.ParserError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    stages = stage_summary(
        [
            (
                "rule",
                rule_events,
                rule_links,
                "Transparent keyword and relationship-focus gate",
            ),
            (
                "hybrid",
                hybrid_events,
                hybrid_links,
                "Rule candidates validated by local NLI and grammar calibration",
            ),
            (
                "canonical",
                canonical_events,
                canonical_links,
                "Cross-article complete-link Event deduplication",
            ),
        ]
    )
    company_summary = company_stage_summary(
        companies,
        {
            "rule": rule_links,
            "hybrid": hybrid_links,
            "canonical": canonical_links,
        },
    )
    type_summary = event_type_stage_summary(
        {
            "rule": rule_events,
            "hybrid": hybrid_events,
            "canonical": canonical_events,
        }
    )
    sensitivity, sensitivity_companies = threshold_sensitivity(
        hybrid_events,
        hybrid_links,
        companies,
        nlp_config,
        evaluation_config,
    )
    decisions = decision_reason_summary(hybrid_links)
    dedup = deduplication_summary(canonical_events, canonical_links)
    graph_validation_path = resolve_path(
        project_root,
        config.get("neo4j_connection", {}).get(
            "query_output_directory", "data/neo4j/analysis"
        ),
    ) / "graph_validation.csv"
    checks = automatic_checks(
        companies,
        canonical_events,
        canonical_links,
        sensitivity,
        graph_validation_path,
    )

    outputs = {
        "stage_ablation_summary.csv": stages,
        "company_stage_summary.csv": company_summary,
        "event_type_stage_summary.csv": type_summary,
        "threshold_sensitivity.csv": sensitivity,
        "threshold_company_counts.csv": sensitivity_companies,
        "hybrid_decision_reason_summary.csv": decisions,
        "deduplication_summary.csv": dedup,
        "automatic_quality_checks.csv": checks,
    }
    try:
        for filename, frame in outputs.items():
            write_csv(frame, output_directory / filename)
        report_text = markdown_report(stages, sensitivity, checks, dedup)
        output_directory.mkdir(parents=True, exist_ok=True)
        (output_directory / "automated_evaluation.md").write_text(
            report_text,
            encoding="utf-8",
        )
        manifest = {
            "sample": config.get("study", {}).get("sample_label", ""),
            "mode": args.mode,
            "outputs": sorted([*outputs, "automated_evaluation.md"]),
            "accuracy_claim": False,
        }
        (output_directory / "evaluation_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except PermissionError as exc:
        print(
            f"OUTPUT_ERROR: Close evaluation files in Excel or another editor: {exc}",
            file=sys.stderr,
        )
        return 1

    failed_checks = int((checks["status"] == "FAIL").sum())
    current = sensitivity[sensitivity["current_setting"].map(parse_bool)].iloc[0]
    print("Automated pipeline evaluation completed.")
    print(f"Stage rows: {len(stages)}")
    print(f"Threshold scenarios: {len(sensitivity)}")
    print(
        "Current threshold reconstruction: "
        f"{int(current['qualified_event_company_links'])} relationships; "
        f"exact_match={parse_bool(current['matches_current_output'])}"
    )
    print(f"Failed automatic checks: {failed_checks}")
    print(f"Evaluation outputs: {output_directory}")
    return 2 if failed_checks else 0


if __name__ == "__main__":
    raise SystemExit(run())
