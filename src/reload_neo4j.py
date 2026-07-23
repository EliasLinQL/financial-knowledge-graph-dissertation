"""Safely replace the managed financial graph with the latest CSV package."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

try:
    from query_kg import (
        NODE_COUNT_QUERY,
        RELATIONSHIP_COUNT_QUERY,
        connection_settings,
        count_mapping,
        execute_read,
        expected_graph_counts,
        load_config,
        resolve_path,
    )
except ModuleNotFoundError:
    from src.query_kg import (
        NODE_COUNT_QUERY,
        RELATIONSHIP_COUNT_QUERY,
        connection_settings,
        count_mapping,
        execute_read,
        expected_graph_counts,
        load_config,
        resolve_path,
    )


MANAGED_LABELS = [
    "Article",
    "Asset",
    "Company",
    "Event",
    "Industry",
    "MarketObservation",
    "Sector",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Back up the current Neo4j import files, copy the latest generated "
            "package, and replace only nodes managed by this project."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
    )
    parser.add_argument(
        "--confirm-replace",
        action="store_true",
        help="Required safety flag. Without it, no files or database data are changed.",
    )
    return parser.parse_args()


def split_cypher_statements(text: str) -> list[str]:
    statements: list[str] = []
    for part in text.split(";"):
        lines = [
            line for line in part.splitlines() if not line.lstrip().startswith("//")
        ]
        statement = "\n".join(lines).strip()
        if statement:
            statements.append(statement)
    return statements


def server_import_directory(driver: Any, database: str) -> Path:
    rows, _, _ = driver.execute_query(
        """
        SHOW SETTINGS YIELD name, value
        WHERE name = 'server.directories.import'
        RETURN value
        """,
        database_=database,
    )
    if not rows or not str(rows[0]["value"]).strip():
        raise RuntimeError("Neo4j did not report server.directories.import.")
    path = Path(str(rows[0]["value"]))
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Neo4j import directory not found: {path}")
    return path


def backup_and_copy_package(
    generated_directory: Path,
    instance_directory: Path,
    backup_root: Path,
) -> Path:
    generated_files = sorted(
        path
        for path in generated_directory.iterdir()
        if path.is_file() and path.suffix.casefold() in {".csv", ".cypher"}
    )
    if not generated_files:
        raise FileNotFoundError(
            f"No generated CSV/Cypher package files found: {generated_directory}"
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_directory = backup_root / f"pre_reload_{timestamp}"
    backup_directory.mkdir(parents=True, exist_ok=False)
    for source in generated_files:
        existing = instance_directory / source.name
        if existing.exists() and existing.is_file():
            shutil.copy2(existing, backup_directory / existing.name)
    for source in generated_files:
        shutil.copy2(source, instance_directory / source.name)
    return backup_directory


def validate_loaded_counts(
    driver: Any,
    database: str,
    generated_directory: Path,
) -> tuple[int, int]:
    expected_nodes, expected_relationships = expected_graph_counts(
        generated_directory
    )
    actual_nodes = count_mapping(
        execute_read(driver, database, NODE_COUNT_QUERY), "label"
    )
    actual_relationships = count_mapping(
        execute_read(driver, database, RELATIONSHIP_COUNT_QUERY),
        "relationship_type",
    )
    if actual_nodes != expected_nodes:
        raise RuntimeError(
            f"Node-count mismatch after reload: expected={expected_nodes}, "
            f"actual={actual_nodes}"
        )
    if actual_relationships != expected_relationships:
        raise RuntimeError(
            "Relationship-count mismatch after reload: "
            f"expected={expected_relationships}, actual={actual_relationships}"
        )
    return sum(actual_nodes.values()), sum(actual_relationships.values())


def run() -> int:
    args = parse_args()
    if not args.confirm_replace:
        raise RuntimeError(
            "Refusing to modify Neo4j without the explicit --confirm-replace flag."
        )

    config_path = args.config.resolve()
    config = load_config(config_path)
    project_root = config_path.parent.parent
    load_dotenv(project_root / ".env")
    settings = connection_settings(config)
    password = os.getenv(settings.password_environment_variable, "").strip()
    if not password:
        raise RuntimeError(
            f"{settings.password_environment_variable} is missing from .env."
        )

    import_config = config.get("neo4j_import", {})
    if not isinstance(import_config, dict):
        raise ValueError("neo4j_import must be a YAML mapping.")
    generated_directory = resolve_path(
        project_root,
        import_config.get("output_directory", "data/neo4j/import"),
    )
    loader_path = generated_directory / "neo4j_load.cypher"
    if not loader_path.exists():
        raise FileNotFoundError(f"Neo4j loader not found: {loader_path}")
    statements = split_cypher_statements(loader_path.read_text(encoding="utf-8"))
    if not statements:
        raise ValueError(f"Neo4j loader contains no statements: {loader_path}")

    with GraphDatabase.driver(
        settings.uri,
        auth=(settings.user, password),
    ) as driver:
        driver.verify_connectivity()
        instance_directory = server_import_directory(driver, settings.database)
        backup_directory = backup_and_copy_package(
            generated_directory,
            instance_directory,
            project_root / "data" / "neo4j" / "backups",
        )
        print(f"Previous import files backed up to: {backup_directory}")
        print(f"Latest import package copied to: {instance_directory}")

        driver.execute_query(
            """
            MATCH (n)
            WHERE any(label IN labels(n) WHERE label IN $managed_labels)
            DETACH DELETE n
            """,
            managed_labels=MANAGED_LABELS,
            database_=settings.database,
        )
        for position, statement in enumerate(statements, start=1):
            driver.execute_query(statement, database_=settings.database)
            print(f"Executed loader statement {position}/{len(statements)}")

        node_count, relationship_count = validate_loaded_counts(
            driver,
            settings.database,
            generated_directory,
        )
    print(f"Neo4j reload verified: {node_count} nodes, {relationship_count} relationships")
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except AuthError as exc:
        print("Neo4j authentication failed.", file=sys.stderr)
        raise SystemExit(1) from exc
    except ServiceUnavailable as exc:
        print("Neo4j is unavailable. Start the configured instance.", file=sys.stderr)
        raise SystemExit(1) from exc
    except (
        FileNotFoundError,
        Neo4jError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"NEO4J_RELOAD_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
