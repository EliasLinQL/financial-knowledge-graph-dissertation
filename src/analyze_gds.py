"""Run a read-only, reproducible Neo4j Graph Data Science analysis.

The module creates two uniquely named in-memory GDS projections, streams every
algorithm result back to Python, writes a bilingual evidence package, and drops
the projections in a ``finally`` block.  It deliberately never writes
algorithm properties or relationships to the persisted Neo4j database.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import sys
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase, RoutingControl

try:
    from query_kg import connection_settings, load_config, resolve_path
except ImportError:  # pragma: no cover - package import in tests
    from src.query_kg import connection_settings, load_config, resolve_path


REQUIRED_GDS_PROCEDURES = {
    "gds.graph.project",
    "gds.wcc.stream",
    "gds.nodeSimilarity.stream",
    "gds.louvain.stream",
    "gds.louvain.stats",
    "gds.pageRank.stream",
}

PALETTE = {
    "background": "#FAFBFC",
    "panel": "#FFFFFF",
    "text": "#17212B",
    "muted": "#5D6975",
    "grid": "#DCE3E8",
    "edge": "#8293A3",
    "accent": "#176B87",
    "accent2": "#D96C4A",
}

COMMUNITY_COLORS = [
    "#176B87",
    "#D96C4A",
    "#5B8E55",
    "#8C67A8",
    "#C29A32",
    "#3178B5",
    "#B65378",
    "#4C9B8A",
    "#A66B42",
    "#64748B",
    "#A855F7",
    "#E76F51",
    "#2A9D8F",
    "#E9C46A",
    "#6B7280",
]

FIGURE_STEMS = (
    "company_coevent_network",
    "top_shared_event_pairs",
    "event_count_vs_strength",
)


ENVIRONMENT_QUERY = """
CALL dbms.components()
YIELD name, versions, edition
WHERE name = 'Neo4j Kernel'
RETURN versions[0] AS neo4j_version, edition
"""

GDS_VERSION_QUERY = "RETURN gds.version() AS gds_version"

GDS_CAPABILITY_QUERY = """
CALL gds.list()
YIELD name
WHERE name IN $required
RETURN DISTINCT name
ORDER BY name
"""

DATABASE_COUNTS_QUERY = """
MATCH (n)
WITH count(n) AS node_count
MATCH ()-[r]->()
RETURN node_count, count(r) AS relationship_count
"""

CATALOG_QUERY = """
CALL gds.graph.list()
YIELD graphName
RETURN graphName
ORDER BY graphName
"""

GDS_INPUT_FINGERPRINT_QUERY = """
CALL () {
  MATCH (company:Company)
  RETURN 'Company' AS record_type, company.company_id AS key1, company.name AS key2
  UNION ALL
  MATCH (event:Event)
  RETURN 'Event' AS record_type, event.event_id AS key1, '' AS key2
  UNION ALL
  MATCH (event:Event)-[:POTENTIALLY_AFFECTS]->(company:Company)
  RETURN
    'POTENTIALLY_AFFECTS' AS record_type,
    event.event_id AS key1,
    company.company_id AS key2
}
RETURN record_type, key1, key2
ORDER BY record_type, key1, key2
"""

COMPANIES_QUERY = """
MATCH (c:Company)
OPTIONAL MATCH (e:Event)-[:POTENTIALLY_AFFECTS]->(c)
RETURN
  c.company_id AS company_id,
  c.name AS company,
  coalesce(c.source_rank, 0) AS source_rank,
  count(DISTINCT e) AS event_count
ORDER BY company_id
"""

COEVENT_EDGES_QUERY = """
MATCH (source:Company)
MATCH
  (source)<-[:POTENTIALLY_AFFECTS]-(event:Event)
          -[:POTENTIALLY_AFFECTS]->(target:Company)
WHERE source.company_id < target.company_id
RETURN
  source.company_id AS company1_id,
  source.name AS company1,
  target.company_id AS company2_id,
  target.name AS company2,
  count(DISTINCT event) AS shared_event_count
ORDER BY shared_event_count DESC, company1_id, company2_id
"""

EVENT_COMPANY_DISTRIBUTION_QUERY = """
MATCH (event:Event)
OPTIONAL MATCH (event)-[:POTENTIALLY_AFFECTS]->(company:Company)
WITH event, count(DISTINCT company) AS company_count
RETURN company_count, count(*) AS event_count
ORDER BY company_count
"""

BIPARTITE_PROJECT_QUERY = """
CALL gds.graph.project(
  $graph_name,
  ['Company', 'Event'],
  {
    POTENTIALLY_AFFECTS: {
      type: 'POTENTIALLY_AFFECTS',
      orientation: 'REVERSE'
    }
  }
)
YIELD graphName, nodeCount, relationshipCount, projectMillis
RETURN graphName, nodeCount, relationshipCount, projectMillis
"""

COEVENT_PROJECT_QUERY = """
MATCH (source:Company)
OPTIONAL MATCH
  (source)<-[:POTENTIALLY_AFFECTS]-(event:Event)
          -[:POTENTIALLY_AFFECTS]->(target:Company)
WHERE source.company_id < target.company_id
WITH source, target, count(DISTINCT event) AS sharedEvents
WITH gds.graph.project(
  $graph_name,
  source,
  target,
  {
    sourceNodeLabels: ['Company'],
    targetNodeLabels: ['Company'],
    relationshipType: 'CO_EVENT',
    relationshipProperties: {weight: toFloat(sharedEvents)}
  },
  {undirectedRelationshipTypes: ['CO_EVENT']}
) AS graph
RETURN
  graph.graphName AS graphName,
  graph.nodeCount AS nodeCount,
  graph.relationshipCount AS relationshipCount,
  graph.projectMillis AS projectMillis
"""

DROP_GRAPH_QUERY = """
CALL gds.graph.drop($graph_name, false)
YIELD graphName, nodeCount, relationshipCount
RETURN graphName, nodeCount, relationshipCount
"""

WCC_ESTIMATE_QUERY = """
CALL gds.wcc.stream.estimate($graph_name, {concurrency: $concurrency})
YIELD requiredMemory, nodeCount, relationshipCount, bytesMin, bytesMax
RETURN requiredMemory, nodeCount, relationshipCount, bytesMin, bytesMax
"""

WCC_STREAM_QUERY = """
CALL gds.wcc.stream($graph_name, {concurrency: $concurrency})
YIELD nodeId, componentId
RETURN
  gds.util.asNode(nodeId).company_id AS company_id,
  gds.util.asNode(nodeId).name AS company,
  componentId
ORDER BY company_id
"""

NODE_SIMILARITY_ESTIMATE_QUERY = """
CALL gds.nodeSimilarity.stream.estimate(
  $graph_name,
  {
    similarityCutoff: $similarity_cutoff,
    similarityMetric: $similarity_metric,
    degreeCutoff: $degree_cutoff,
    topK: $top_k,
    concurrency: $concurrency
  }
)
YIELD requiredMemory, nodeCount, relationshipCount, bytesMin, bytesMax
RETURN requiredMemory, nodeCount, relationshipCount, bytesMin, bytesMax
"""

NODE_SIMILARITY_STREAM_QUERY = """
CALL gds.nodeSimilarity.stream(
  $graph_name,
  {
    similarityCutoff: $similarity_cutoff,
    similarityMetric: $similarity_metric,
    degreeCutoff: $degree_cutoff,
    topK: $top_k,
    concurrency: $concurrency
  }
)
YIELD node1, node2, similarity
WITH gds.util.asNode(node1) AS c1, gds.util.asNode(node2) AS c2, similarity
WHERE c1.company_id IS NOT NULL AND c2.company_id IS NOT NULL
RETURN
  c1.company_id AS company1_id,
  c1.name AS company1,
  c2.company_id AS company2_id,
  c2.name AS company2,
  similarity
ORDER BY similarity DESC, company1_id, company2_id
"""

LOUVAIN_ESTIMATE_QUERY = """
CALL gds.louvain.stream.estimate(
  $graph_name,
  {
    relationshipWeightProperty: 'weight',
    maxLevels: $max_levels,
    maxIterations: $max_iterations,
    tolerance: $tolerance,
    concurrency: $concurrency
  }
)
YIELD requiredMemory, nodeCount, relationshipCount, bytesMin, bytesMax
RETURN requiredMemory, nodeCount, relationshipCount, bytesMin, bytesMax
"""

LOUVAIN_WEIGHTED_STREAM_QUERY = """
CALL gds.louvain.stream(
  $graph_name,
  {
    relationshipWeightProperty: 'weight',
    maxLevels: $max_levels,
    maxIterations: $max_iterations,
    tolerance: $tolerance,
    concurrency: $concurrency,
    includeIntermediateCommunities: true
  }
)
YIELD nodeId, communityId, intermediateCommunityIds
RETURN
  gds.util.asNode(nodeId).company_id AS company_id,
  gds.util.asNode(nodeId).name AS company,
  communityId,
  intermediateCommunityIds
ORDER BY company_id
"""

LOUVAIN_UNWEIGHTED_STREAM_QUERY = """
CALL gds.louvain.stream(
  $graph_name,
  {
    maxLevels: $max_levels,
    maxIterations: $max_iterations,
    tolerance: $tolerance,
    concurrency: $concurrency
  }
)
YIELD nodeId, communityId
RETURN
  gds.util.asNode(nodeId).company_id AS company_id,
  gds.util.asNode(nodeId).name AS company,
  communityId
ORDER BY company_id
"""

LOUVAIN_WEIGHTED_STATS_QUERY = """
CALL gds.louvain.stats(
  $graph_name,
  {
    relationshipWeightProperty: 'weight',
    maxLevels: $max_levels,
    maxIterations: $max_iterations,
    tolerance: $tolerance,
    concurrency: $concurrency
  }
)
YIELD communityCount, modularity, modularities, ranLevels, computeMillis
RETURN communityCount, modularity, modularities, ranLevels, computeMillis
"""

LOUVAIN_UNWEIGHTED_STATS_QUERY = """
CALL gds.louvain.stats(
  $graph_name,
  {
    maxLevels: $max_levels,
    maxIterations: $max_iterations,
    tolerance: $tolerance,
    concurrency: $concurrency
  }
)
YIELD communityCount, modularity, modularities, ranLevels, computeMillis
RETURN communityCount, modularity, modularities, ranLevels, computeMillis
"""

PAGERANK_ESTIMATE_QUERY = """
CALL gds.pageRank.stream.estimate(
  $graph_name,
  {
    relationshipWeightProperty: 'weight',
    maxIterations: $max_iterations,
    dampingFactor: $damping_factor,
    tolerance: $tolerance,
    concurrency: $concurrency
  }
)
YIELD requiredMemory, nodeCount, relationshipCount, bytesMin, bytesMax
RETURN requiredMemory, nodeCount, relationshipCount, bytesMin, bytesMax
"""

PAGERANK_STREAM_QUERY = """
CALL gds.pageRank.stream(
  $graph_name,
  {
    relationshipWeightProperty: 'weight',
    maxIterations: $max_iterations,
    dampingFactor: $damping_factor,
    tolerance: $tolerance,
    concurrency: $concurrency
  }
)
YIELD nodeId, score
RETURN
  gds.util.asNode(nodeId).company_id AS company_id,
  gds.util.asNode(nodeId).name AS company,
  score AS page_rank
ORDER BY page_rank DESC, company_id
"""

DATABASE_READ_QUERIES = [
    ENVIRONMENT_QUERY,
    GDS_VERSION_QUERY,
    GDS_CAPABILITY_QUERY,
    DATABASE_COUNTS_QUERY,
    CATALOG_QUERY,
    GDS_INPUT_FINGERPRINT_QUERY,
    COMPANIES_QUERY,
    COEVENT_EDGES_QUERY,
    EVENT_COMPANY_DISTRIBUTION_QUERY,
]

GDS_STREAM_QUERIES = [
    WCC_ESTIMATE_QUERY,
    WCC_STREAM_QUERY,
    NODE_SIMILARITY_ESTIMATE_QUERY,
    NODE_SIMILARITY_STREAM_QUERY,
    LOUVAIN_ESTIMATE_QUERY,
    LOUVAIN_WEIGHTED_STREAM_QUERY,
    LOUVAIN_UNWEIGHTED_STREAM_QUERY,
    LOUVAIN_WEIGHTED_STATS_QUERY,
    LOUVAIN_UNWEIGHTED_STATS_QUERY,
    PAGERANK_ESTIMATE_QUERY,
    PAGERANK_STREAM_QUERY,
]

GDS_CATALOG_QUERIES = [
    BIPARTITE_PROJECT_QUERY,
    COEVENT_PROJECT_QUERY,
    DROP_GRAPH_QUERY,
]


@dataclass(frozen=True)
class GdsSettings:
    output_directory: Path
    graph_name_prefix: str
    concurrency: int
    similarity_cutoff: float
    similarity_metric: str
    degree_cutoff: int
    top_k: int
    support_threshold: int
    sensitivity_thresholds: tuple[int, ...]
    louvain_max_levels: int
    louvain_max_iterations: int
    louvain_tolerance: float
    pagerank_max_iterations: int
    pagerank_damping_factor: float
    pagerank_tolerance: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run reproducible, non-writing GDS analysis over the live financial KG."
        )
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
        help="Override gds_analysis.output_directory.",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--print-output-directory",
        action="store_true",
        help="Print the resolved output directory without connecting to Neo4j.",
    )
    action.add_argument(
        "--finalize-manifest",
        action="store_true",
        help="Refresh output hashes for an existing result package.",
    )
    return parser.parse_args()


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer.") from exc
    if parsed <= 0 or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{label} must be a positive integer.")
    return parsed


def _unit_interval(value: Any, label: str, *, include_zero: bool = True) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    lower_ok = parsed >= 0 if include_zero else parsed > 0
    if not math.isfinite(parsed) or not lower_ok or parsed > 1:
        boundary = "[0, 1]" if include_zero else "(0, 1]"
        raise ValueError(f"{label} must be in {boundary}.")
    return parsed


def gds_settings(config: Mapping[str, Any]) -> GdsSettings:
    section = config.get("gds_analysis", {})
    if not isinstance(section, Mapping):
        raise ValueError("gds_analysis must be a YAML mapping.")
    node_similarity = section.get("node_similarity", {})
    louvain = section.get("louvain", {})
    pagerank = section.get("pagerank", {})
    for label, value in (
        ("gds_analysis.node_similarity", node_similarity),
        ("gds_analysis.louvain", louvain),
        ("gds_analysis.pagerank", pagerank),
    ):
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} must be a YAML mapping.")

    prefix = str(section.get("graph_name_prefix", "financial_kg_gds")).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", prefix):
        raise ValueError(
            "gds_analysis.graph_name_prefix must start with a letter and contain "
            "only letters, digits or underscores (maximum 48 characters)."
        )

    thresholds_raw = section.get("sensitivity_thresholds", [1, 2, 3, 5])
    if not isinstance(thresholds_raw, Sequence) or isinstance(
        thresholds_raw, (str, bytes)
    ):
        raise ValueError("gds_analysis.sensitivity_thresholds must be a list.")
    thresholds = tuple(
        sorted(
            {
                _positive_int(value, "gds_analysis.sensitivity_thresholds[]")
                for value in thresholds_raw
            }
        )
    )
    if not thresholds:
        raise ValueError("gds_analysis.sensitivity_thresholds cannot be empty.")

    louvain_tolerance = float(louvain.get("tolerance", 0.0001))
    pagerank_tolerance = float(pagerank.get("tolerance", 1.0e-7))
    if not math.isfinite(louvain_tolerance) or louvain_tolerance <= 0:
        raise ValueError("gds_analysis.louvain.tolerance must be positive.")
    if not math.isfinite(pagerank_tolerance) or pagerank_tolerance <= 0:
        raise ValueError("gds_analysis.pagerank.tolerance must be positive.")
    similarity_metric = str(
        node_similarity.get("similarity_metric", "JACCARD")
    ).strip().upper()
    if similarity_metric != "JACCARD":
        raise ValueError(
            "gds_analysis.node_similarity.similarity_metric must be JACCARD "
            "because the exported cross-check uses Jaccard."
        )

    return GdsSettings(
        output_directory=Path(section.get("output_directory", "outputs/gds_analysis")),
        graph_name_prefix=prefix,
        concurrency=_positive_int(section.get("concurrency", 1), "concurrency"),
        similarity_cutoff=_unit_interval(
            node_similarity.get("similarity_cutoff", 1.0e-12),
            "gds_analysis.node_similarity.similarity_cutoff",
        ),
        similarity_metric=similarity_metric,
        degree_cutoff=_positive_int(
            node_similarity.get("degree_cutoff", 1),
            "gds_analysis.node_similarity.degree_cutoff",
        ),
        top_k=_positive_int(
            node_similarity.get("top_k", 24),
            "gds_analysis.node_similarity.top_k",
        ),
        support_threshold=_positive_int(
            section.get("support_threshold", 2),
            "gds_analysis.support_threshold",
        ),
        sensitivity_thresholds=thresholds,
        louvain_max_levels=_positive_int(
            louvain.get("max_levels", 10), "gds_analysis.louvain.max_levels"
        ),
        louvain_max_iterations=_positive_int(
            louvain.get("max_iterations", 10),
            "gds_analysis.louvain.max_iterations",
        ),
        louvain_tolerance=louvain_tolerance,
        pagerank_max_iterations=_positive_int(
            pagerank.get("max_iterations", 20),
            "gds_analysis.pagerank.max_iterations",
        ),
        pagerank_damping_factor=_unit_interval(
            pagerank.get("damping_factor", 0.85),
            "gds_analysis.pagerank.damping_factor",
            include_zero=False,
        ),
        pagerank_tolerance=pagerank_tolerance,
    )


def configured_output_directory(
    config_path: Path, output_override: Path | None = None
) -> Path:
    resolved_config = config_path.resolve()
    config = load_config(resolved_config)
    project_root = resolved_config.parent.parent
    settings = gds_settings(config)
    return resolve_path(
        project_root, output_override or settings.output_directory
    ).resolve()


def canonical_pair(first: str, second: str) -> tuple[str, str]:
    first_text = str(first)
    second_text = str(second)
    return (first_text, second_text) if first_text <= second_text else (second_text, first_text)


def deduplicate_similarity_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse symmetric NodeSimilarity rows to stable unordered pairs."""

    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for source in rows:
        first = str(source.get("company1_id", ""))
        second = str(source.get("company2_id", ""))
        if not first or not second or first == second:
            continue
        similarity = float(source.get("similarity", 0.0))
        if not math.isfinite(similarity) or similarity < 0 or similarity > 1:
            raise ValueError(f"Invalid NodeSimilarity score: {similarity!r}")
        left, right = canonical_pair(first, second)
        if left == first:
            company1 = source.get("company1", "")
            company2 = source.get("company2", "")
        else:
            company1 = source.get("company2", "")
            company2 = source.get("company1", "")
        candidate = {
            "company1_id": left,
            "company1": company1,
            "company2_id": right,
            "company2": company2,
            "similarity": similarity,
        }
        current = deduplicated.get((left, right))
        if current is None or similarity > float(current["similarity"]):
            deduplicated[(left, right)] = candidate
    return sorted(
        deduplicated.values(),
        key=lambda row: (-float(row["similarity"]), row["company1_id"], row["company2_id"]),
    )


def average_ranks(values: Sequence[float], *, descending: bool = False) -> list[float]:
    """Return one-based average ranks with deterministic tie handling."""

    indexed = sorted(
        enumerate(float(value) for value in values),
        key=lambda item: ((-item[1]) if descending else item[1], item[0]),
    )
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        rank = ((cursor + 1) + end) / 2.0
        for index, _ in indexed[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def pearson(first: Sequence[float], second: Sequence[float]) -> float | None:
    if len(first) != len(second):
        raise ValueError("Correlation inputs must have equal length.")
    if len(first) < 2:
        return None
    left = [float(value) for value in first]
    right = [float(value) for value in second]
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    left_sum = sum((value - left_mean) ** 2 for value in left)
    right_sum = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_sum * right_sum)
    if denominator == 0:
        return None
    return numerator / denominator


def spearman(first: Sequence[float], second: Sequence[float]) -> float | None:
    return pearson(average_ranks(first), average_ranks(second))


def canonical_group_labels(
    mapping: Mapping[str, Any], prefix: str
) -> dict[str, str]:
    """Replace unstable raw algorithm IDs with membership-stable labels."""

    groups: dict[str, list[str]] = defaultdict(list)
    for company_id, raw_label in mapping.items():
        groups[str(raw_label)].append(str(company_id))
    ordered = sorted(
        (sorted(members) for members in groups.values()),
        key=lambda members: (-len(members), members),
    )
    result: dict[str, str] = {}
    width = max(2, len(str(len(ordered))))
    for number, members in enumerate(ordered, start=1):
        stable = f"{prefix}{number:0{width}d}"
        for company_id in members:
            result[company_id] = stable
    return result


def _components_for_threshold(
    company_ids: Sequence[str], edges: Iterable[Mapping[str, Any]], threshold: int
) -> list[list[str]]:
    adjacency = {company_id: set() for company_id in company_ids}
    for edge in edges:
        if int(edge["shared_event_count"]) < threshold:
            continue
        left = str(edge["company1_id"])
        right = str(edge["company2_id"])
        adjacency[left].add(right)
        adjacency[right].add(left)
    components: list[list[str]] = []
    seen: set[str] = set()
    for start in sorted(company_ids):
        if start in seen:
            continue
        queue: deque[str] = deque([start])
        seen.add(start)
        members: list[str] = []
        while queue:
            current = queue.popleft()
            members.append(current)
            for neighbour in sorted(adjacency[current]):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        components.append(sorted(members))
    return sorted(components, key=lambda members: (-len(members), members))


def build_structural_metrics(
    companies: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    company_rows = [dict(row) for row in companies]
    edge_rows = [dict(row) for row in edges]
    neighbours: dict[str, set[str]] = defaultdict(set)
    strengths: dict[str, int] = defaultdict(int)
    for edge in edge_rows:
        left = str(edge["company1_id"])
        right = str(edge["company2_id"])
        weight = int(edge["shared_event_count"])
        if left == right or weight <= 0:
            continue
        neighbours[left].add(right)
        neighbours[right].add(left)
        strengths[left] += weight
        strengths[right] += weight
    result: list[dict[str, Any]] = []
    for company in company_rows:
        company_id = str(company["company_id"])
        result.append(
            {
                **company,
                "event_count": int(company.get("event_count", 0)),
                "coevent_degree": len(neighbours[company_id]),
                "coevent_strength": strengths[company_id],
                "is_isolate": len(neighbours[company_id]) == 0,
            }
        )
    return sorted(result, key=lambda row: str(row["company_id"]))


def validate_query_contract() -> None:
    """Guard the persisted-KG read-only contract against accidental edits."""

    forbidden = [r"\.write\b", r"\.mutate\b", r"\bCREATE\b", r"\bMERGE\b", r"\bSET\b", r"\bDELETE\b", r"\bREMOVE\b"]
    for query in DATABASE_READ_QUERIES + GDS_STREAM_QUERIES + GDS_CATALOG_QUERIES:
        for pattern in forbidden:
            if re.search(pattern, query, flags=re.IGNORECASE):
                raise AssertionError(
                    f"The GDS analysis query contract contains forbidden operation {pattern!r}."
                )


def _records(driver: Any, database: str, query: str, parameters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    records, _, _ = driver.execute_query(
        query,
        parameters_=dict(parameters or {}),
        database_=database,
    )
    return [record.data() for record in records]


def _read_records(driver: Any, database: str, query: str, parameters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    records, _, _ = driver.execute_query(
        query,
        parameters_=dict(parameters or {}),
        database_=database,
        routing_=RoutingControl.READ,
    )
    return [record.data() for record in records]


def _write_csv(rows: Iterable[Mapping[str, Any]], path: Path, columns: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row_list = [dict(row) for row in rows]
    frame = pd.DataFrame(row_list, columns=list(columns) if columns else None)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_config(config: Mapping[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_records(rows: Iterable[Mapping[str, Any]]) -> str:
    """Hash an already deterministically ordered live-graph record stream."""

    digest = hashlib.sha256()
    for row in rows:
        canonical = json.dumps(
            dict(row), sort_keys=True, ensure_ascii=False, default=str
        )
        digest.update(canonical.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _package_output_hashes(output_directory: Path) -> dict[str, str]:
    candidates: set[Path] = set()
    for pattern in (
        "tables/*.csv",
        "figures/*.svg",
        "figures/*.png",
        "gds_results_cn.md",
        "gds_results_en.md",
    ):
        candidates.update(output_directory.glob(pattern))
    return {
        str(path.relative_to(output_directory)): _sha256_file(path)
        for path in sorted(candidates)
        if path.is_file()
    }


def finalize_manifest(output_directory: Path) -> Path:
    manifest_path = output_directory / "gds_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"GDS manifest was not found for finalization: {manifest_path}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_sha256"] = _package_output_hashes(output_directory)
    manifest["output_hash_scope"] = (
        "All CSV, Markdown, SVG and any additional PNG artifacts present in "
        "the package; the manifest does not hash itself."
    )
    manifest["manifest_finalized_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_value),
        encoding="utf-8",
    )
    return manifest_path


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (pd.NA.__class__,)):
        return None
    return value


def _component_rows(metrics: Sequence[Mapping[str, Any]], label_key: str, output_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in metrics:
        grouped[str(row[label_key])].append(row)
    result: list[dict[str, Any]] = []
    for label, members in grouped.items():
        ordered = sorted(members, key=lambda row: str(row["company_id"]))
        result.append(
            {
                output_key: label,
                "company_count": len(ordered),
                "company_ids": " | ".join(str(row["company_id"]) for row in ordered),
                "companies": " | ".join(str(row["company"]) for row in ordered),
            }
        )
    return sorted(result, key=lambda row: (-int(row["company_count"]), row[output_key]))


def _threshold_sensitivity(
    companies: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    thresholds: Sequence[int],
) -> list[dict[str, Any]]:
    company_ids = [str(row["company_id"]) for row in companies]
    total_possible = len(company_ids) * (len(company_ids) - 1) / 2
    rows: list[dict[str, Any]] = []
    for threshold in thresholds:
        retained = [
            row for row in edges if int(row["shared_event_count"]) >= threshold
        ]
        components = _components_for_threshold(company_ids, retained, threshold)
        non_isolates = {
            str(row[key])
            for row in retained
            for key in ("company1_id", "company2_id")
        }
        rows.append(
            {
                "minimum_shared_events": threshold,
                "logical_edge_count": len(retained),
                "density": len(retained) / total_possible if total_possible else 0.0,
                "component_count": len(components),
                "largest_component_size": len(components[0]) if components else 0,
                "isolate_count": len(company_ids) - len(non_isolates),
            }
        )
    return rows


def _correlation_rows(metrics: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pairs = [
        ("event_count", "coevent_degree"),
        ("event_count", "coevent_strength"),
        ("coevent_degree", "coevent_strength"),
        ("coevent_strength", "page_rank"),
    ]
    rows: list[dict[str, Any]] = []
    for first, second in pairs:
        left = [float(row[first]) for row in metrics]
        right = [float(row[second]) for row in metrics]
        rows.append(
            {
                "metric_1": first,
                "metric_2": second,
                "n": len(metrics),
                "pearson": pearson(left, right),
                "spearman": spearman(left, right),
                "interpretation": "descriptive association; not a causal estimate",
            }
        )
    return rows


def _svg_document(width: int, height: int, title: str, elements: Iterable[str]) -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                f'height="{height}" viewBox="0 0 {width} {height}" role="img">'
            ),
            f"<title>{html.escape(title)}</title>",
            f'<rect width="{width}" height="{height}" fill="{PALETTE["background"]}"/>',
            *elements,
            "</svg>",
        ]
    )


def _svg_text(
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
    transform = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
        f'font-family="Arial, Noto Sans, sans-serif" font-size="{size}" '
        f'font-weight="{weight}" fill="{color or PALETTE["text"]}"{transform}>'
        f"{html.escape(str(text))}</text>"
    )


def _short_name(name: str) -> str:
    replacements = {
        "Taiwan Semiconductor Manufacturing Company": "TSMC",
        "JPMorgan Chase & Co.": "JPMorgan",
        "Berkshire Hathaway Inc.": "Berkshire",
        "Microsoft Corporation": "Microsoft",
        "NVIDIA Corporation": "NVIDIA",
        "Exxon Mobil Corporation": "Exxon Mobil",
    }
    if name in replacements:
        return replacements[name]
    return re.sub(r"\s+(Corporation|Company|Inc\.|Ltd\.)$", "", name).strip()


def _network_figure(metrics: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]], path: Path) -> None:
    width, height = 1600, 1060
    connected = [row for row in metrics if not bool(row["is_isolate"])]
    isolates = [row for row in metrics if bool(row["is_isolate"])]
    connected = sorted(connected, key=lambda row: str(row["company_id"]))
    positions: dict[str, tuple[float, float]] = {}
    centre_x, centre_y, radius = 800.0, 465.0, 345.0
    for index, row in enumerate(connected):
        angle = -math.pi / 2 + (2 * math.pi * index / max(1, len(connected)))
        positions[str(row["company_id"])] = (
            centre_x + radius * math.cos(angle),
            centre_y + radius * math.sin(angle),
        )
    if isolates:
        spacing = 1400 / max(1, len(isolates))
        for index, row in enumerate(sorted(isolates, key=lambda item: str(item["company_id"]))):
            positions[str(row["company_id"])] = (100 + spacing * (index + 0.5), 920)

    community_names = sorted({str(row["louvain_community"]) for row in metrics})
    color_map = {
        label: COMMUNITY_COLORS[index % len(COMMUNITY_COLORS)]
        for index, label in enumerate(community_names)
    }
    elements: list[str] = [
        _svg_text(60, 54, "Company shared-event network", size=30, weight=700),
        _svg_text(
            60,
            84,
            "Node size = shared-event strength; colour = weighted Louvain; edge width = canonical shared events",
            size=15,
            color=PALETTE["muted"],
        ),
    ]
    for edge in sorted(edges, key=lambda row: int(row["shared_event_count"])):
        left = str(edge["company1_id"])
        right = str(edge["company2_id"])
        x1, y1 = positions[left]
        x2, y2 = positions[right]
        weight = int(edge["shared_event_count"])
        opacity = min(0.85, 0.18 + math.log1p(weight) / 5)
        stroke_width = 0.8 + 1.4 * math.log1p(weight)
        elements.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{PALETTE["edge"]}" stroke-width="{stroke_width:.2f}" opacity="{opacity:.3f}"/>'
        )
    max_strength = max((int(row["coevent_strength"]) for row in metrics), default=1)
    for row in metrics:
        company_id = str(row["company_id"])
        x, y = positions[company_id]
        strength = int(row["coevent_strength"])
        node_radius = 13 + 18 * math.sqrt(strength / max_strength) if strength else 12
        color = color_map[str(row["louvain_community"])]
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{node_radius:.1f}" fill="{color}" '
            f'stroke="#FFFFFF" stroke-width="2.5"><title>{html.escape(str(row["company"]))}: '
            f'degree {int(row["coevent_degree"])}, strength {strength}</title></circle>'
        )
        label_y = y + node_radius + 19 if y < centre_y else y - node_radius - 8
        if bool(row["is_isolate"]):
            label_y = y + 32
        elements.append(
            _svg_text(x, label_y, _short_name(str(row["company"])), size=13, anchor="middle", weight=600)
        )
    elements.extend(
        [
            _svg_text(60, 1010, "Isolated nodes have no shared canonical event in this sample; this is not evidence of no real-world relationship.", size=14, color=PALETTE["muted"]),
            _svg_text(1540, 1010, "Source: frozen financial KG; GDS stream mode", size=13, anchor="end", color=PALETTE["muted"]),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_svg_document(width, height, "Company shared-event network", elements), encoding="utf-8")


def _edge_bar_figure(edges: Sequence[Mapping[str, Any]], support_threshold: int, path: Path) -> None:
    rows = list(edges[:15])
    width, height = 1500, 900
    left, top, chart_width, row_height = 500, 125, 900, 45
    maximum = max((int(row["shared_event_count"]) for row in rows), default=1)
    elements = [
        _svg_text(55, 55, "Top company pairs by shared canonical events", size=30, weight=700),
        _svg_text(55, 86, f"Support flag requires at least {support_threshold} shared events; all logical edges remain in the CSV", size=15, color=PALETTE["muted"]),
    ]
    for index, row in enumerate(rows):
        y = top + index * row_height
        value = int(row["shared_event_count"])
        label = f"{_short_name(str(row['company1']))} – {_short_name(str(row['company2']))}"
        bar_width = chart_width * value / maximum
        color = PALETTE["accent"] if value >= support_threshold else PALETTE["muted"]
        elements.append(_svg_text(left - 18, y + 22, label, size=14, anchor="end"))
        elements.append(
            f'<rect x="{left}" y="{y}" width="{bar_width:.1f}" height="29" rx="4" fill="{color}" opacity="0.88"/>'
        )
        elements.append(_svg_text(left + bar_width + 10, y + 21, value, size=14, weight=700))
    elements.append(_svg_text(1450, 855, "Logical edge weight = count of distinct shared canonical Events", size=13, anchor="end", color=PALETTE["muted"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_svg_document(width, height, "Top shared-event pairs", elements), encoding="utf-8")


def _scatter_figure(metrics: Sequence[Mapping[str, Any]], correlation: float | None, path: Path) -> None:
    width, height = 1400, 900
    left, right, top, bottom = 120, 70, 110, 110
    chart_width = width - left - right
    chart_height = height - top - bottom
    max_x = max(1, max((int(row["event_count"]) for row in metrics), default=0))
    max_y = max(
        1, max((int(row["coevent_strength"]) for row in metrics), default=0)
    )
    elements = [
        _svg_text(55, 55, "Event coverage versus shared-event strength", size=30, weight=700),
        _svg_text(55, 84, f"Spearman ρ = {correlation:.3f}" if correlation is not None else "Spearman ρ = undefined", size=16, color=PALETTE["muted"]),
    ]
    for tick in range(6):
        x = left + chart_width * tick / 5
        y = top + chart_height - chart_height * tick / 5
        elements.extend(
            [
                f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + chart_height}" stroke="{PALETTE["grid"]}"/>',
                f'<line x1="{left}" y1="{y:.1f}" x2="{left + chart_width}" y2="{y:.1f}" stroke="{PALETTE["grid"]}"/>',
                _svg_text(x, top + chart_height + 28, round(max_x * tick / 5), size=13, anchor="middle", color=PALETTE["muted"]),
                _svg_text(left - 15, y + 5, round(max_y * tick / 5), size=13, anchor="end", color=PALETTE["muted"]),
            ]
        )
    for row in metrics:
        x = left + chart_width * int(row["event_count"]) / max_x
        y = top + chart_height - chart_height * int(row["coevent_strength"]) / max_y
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{PALETTE["accent2"]}" opacity="0.78"><title>{html.escape(str(row["company"]))}</title></circle>'
        )
        if int(row["coevent_strength"]) >= 20:
            elements.append(_svg_text(x + 10, y - 8, _short_name(str(row["company"])), size=12, weight=600))
    elements.extend(
        [
            _svg_text(left + chart_width / 2, height - 45, "Qualified canonical event count", size=16, anchor="middle", weight=600),
            _svg_text(
                42,
                top + chart_height / 2,
                "Shared-event strength",
                size=16,
                anchor="middle",
                weight=600,
                rotate=-90,
            ),
            _svg_text(1330, 855, "Association is descriptive, not causal", size=13, anchor="end", color=PALETTE["muted"]),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_svg_document(width, height, "Coverage and shared-event strength", elements), encoding="utf-8")


def _report_text(
    *,
    language: str,
    summary: Mapping[str, Any],
    metrics: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    similarities: Sequence[Mapping[str, Any]],
    wcc_rows: Sequence[Mapping[str, Any]],
    community_rows: Sequence[Mapping[str, Any]],
    correlations: Sequence[Mapping[str, Any]],
) -> str:
    top_centrality = sorted(metrics, key=lambda row: (-float(row["page_rank"]), str(row["company_id"])))[:5]
    isolates = [str(row["company"]) for row in metrics if bool(row["is_isolate"])]
    top_edges = edges[:5]
    top_similarity = similarities[:5]
    event_strength = next(
        row for row in correlations if row["metric_1"] == "event_count" and row["metric_2"] == "coevent_strength"
    )
    correlation_value = event_strength.get("spearman")
    correlation_text = (
        f"{float(correlation_value):.4f}"
        if correlation_value is not None
        else ("未定义" if language == "cn" else "undefined")
    )
    threshold_text = "/".join(
        str(value) for value in summary["sensitivity_thresholds"]
    )
    if language == "cn":
        return f"""# GDS 图结构分析结果

## 1. 范围与可复现性

本阶段在已冻结的 Neo4j 金融知识图谱上运行，未重新采集新闻、未重跑 NLP，也未向业务图谱写入任何 GDS 属性或关系。脚本建立 UUID 命名的临时内存投影，使用 `stream` / `stats` 模式取得结果，并在 `finally` 中删除投影。运行前后数据库均为 {summary['database_node_count']} 个节点和 {summary['database_relationship_count']} 条关系。

二部投影含 {summary['bipartite_node_count']} 个 Company/Event 节点及 {summary['bipartite_relationship_count']} 条 Company→Event 关系。公司共事件投影含 {summary['company_count']} 家公司、{summary['logical_edge_count']} 个无序逻辑公司对；GDS 将无向关系双向存储，所以投影关系数是 {summary['projected_relationship_count']}，这不代表存在 {summary['projected_relationship_count']} 个独立公司关系。

## 2. 连通性（WCC）

共事件图共有 {summary['wcc_count']} 个连通分量，最大分量含 {summary['largest_wcc_size']} 家公司，孤立公司 {summary['isolate_count']} 家：{', '.join(isolates) if isolates else '无'}。孤立仅表示这些公司在本样本中没有与其他公司共享同一个规范事件，不表示现实世界不存在公司关系。

## 3. 共事件暴露与中心性

边权是两家公司共享的不同规范事件数。权重最高的公司对为：{'; '.join(f"{row['company1']}–{row['company2']} ({row['shared_event_count']})" for row in top_edges)}。加权 PageRank 前五为：{'; '.join(f"{row['company']} ({float(row['page_rank']):.4f})" for row in top_centrality)}。

全部 {summary['company_count']} 家公司中，事件数量与共事件强度的 Spearman 相关为 {correlation_text}。这说明中心性会受到新闻覆盖量影响；PageRank 在断开的分量之间也不适合做强排序解释，因此它只作为 degree/strength 的补充描述。

## 4. Node Similarity

Node Similarity 在反向的 Company→Event 二部图上计算 Jaccard，相似度最高的公司对为：{'; '.join(f"{row['company1']}–{row['company2']} ({float(row['similarity']):.4f}, shared={row['shared_event_count']})" for row in top_similarity)}。Jaccard 表示共享规范事件邻居的比例，不等于业务相似度、行业相似度或供应链关系。

## 5. Louvain 社区

加权 Louvain 得到 {summary['weighted_community_count']} 个社区（modularity={summary['weighted_modularity']:.4f}），不加权版本得到 {summary['unweighted_community_count']} 个社区。社区编号已按成员集合稳定重编号，原始 GDS ID 不被视为跨运行标识。权重设定会改变社区结果，因此本结果是探索性的新闻事件共现分组，而非真实行业分类。

## 6. 稳健性与解释边界

共有 {summary['single_support_edge_count']} / {summary['logical_edge_count']} 条边只由 1 个共享事件支持。主表保留全部边，同时用 `support_threshold={summary['support_threshold']}` 标记较有支持的关系；`threshold_sensitivity.csv` 报告阈值 {threshold_text} 对边数、孤立点和最大连通分量的影响。

- 图结构仅反映当前 Guardian 样本、事件抽取阈值和规范事件去重结果。
- WCC、相似度、社区和中心性不是因果效应、系统性风险或投资建议。
- 市场窗口收益仍只是描述性背景，本阶段没有训练预测模型。
- 结果没有写回 Neo4j；所有表格、图形和哈希记录均保存在本输出目录。

## 7. 输出

`tables/` 包含投影、边、WCC、相似度、社区、中心性、相关性、阈值敏感性及内存估计；`figures/` 包含 SVG 图；`gds_manifest.json` 保存版本、参数、输入/输出 SHA-256 和解释边界。
"""
    return f"""# GDS structural graph analysis

## 1. Scope and reproducibility

This stage runs over the frozen Neo4j financial knowledge graph. It neither recollects news nor reruns NLP, and it writes no GDS property or relationship to the persisted graph. UUID-named in-memory projections are queried in `stream` / `stats` mode and dropped in a `finally` block. The database contains {summary['database_node_count']} nodes and {summary['database_relationship_count']} relationships both before and after the run.

The bipartite projection contains {summary['bipartite_node_count']} Company/Event nodes and {summary['bipartite_relationship_count']} Company→Event relationships. The company co-event projection contains {summary['company_count']} companies and {summary['logical_edge_count']} unordered logical company pairs. GDS stores each undirected relationship in both directions, hence {summary['projected_relationship_count']} projected relationships; these are not {summary['projected_relationship_count']} independent company links.

## 2. Connectivity (WCC)

The co-event graph has {summary['wcc_count']} weakly connected components. The largest contains {summary['largest_wcc_size']} companies and {summary['isolate_count']} companies are isolates: {', '.join(isolates) if isolates else 'none'}. An isolate means only that the company shares no canonical Event with another company in this sample; it is not evidence of no real-world relationship.

## 3. Co-event exposure and centrality

An edge weight counts distinct canonical Events shared by two companies. The largest weights are: {'; '.join(f"{row['company1']}–{row['company2']} ({row['shared_event_count']})" for row in top_edges)}. Weighted PageRank ranks: {'; '.join(f"{row['company']} ({float(row['page_rank']):.4f})" for row in top_centrality)}.

Across all {summary['company_count']} companies, canonical event count and co-event strength have Spearman correlation {correlation_text}. Centrality therefore partly reflects news coverage. PageRank is also difficult to compare strongly across disconnected components, so it is supplementary to degree and strength.

## 4. Node Similarity

Node Similarity computes Jaccard over the reversed Company→Event bipartite graph. The highest similarities are: {'; '.join(f"{row['company1']}–{row['company2']} ({float(row['similarity']):.4f}, shared={row['shared_event_count']})" for row in top_similarity)}. Jaccard measures shared canonical-event neighbours; it is not business, sector or supply-chain similarity.

## 5. Louvain communities

Weighted Louvain returns {summary['weighted_community_count']} communities (modularity={summary['weighted_modularity']:.4f}), compared with {summary['unweighted_community_count']} without weights. Community labels are stabilised from membership sets rather than treating raw GDS IDs as persistent. Weight sensitivity means these are exploratory news-event groupings, not true industry classifications.

## 6. Sensitivity and interpretation boundaries

{summary['single_support_edge_count']} of {summary['logical_edge_count']} edges are supported by exactly one shared Event. The complete edge table is retained and a `support_threshold={summary['support_threshold']}` flag identifies better-supported pairs; `threshold_sensitivity.csv` reports how thresholds {threshold_text} change edges, isolates and the largest component.

- Structure is conditional on Guardian coverage, extraction thresholds and canonical-event deduplication.
- Connectivity, similarity, community and centrality are not causal effects, systemic-risk estimates or investment advice.
- Market-window returns remain descriptive context; this stage trains no predictive model.
- No result is written back to Neo4j. Tables, figures and hashes are saved only in this output package.

## 7. Outputs

`tables/` contains projections, edges, WCC, similarity, communities, centrality, correlations, threshold sensitivity and memory estimates. `figures/` contains SVG assets. `gds_manifest.json` records versions, parameters, input/output SHA-256 values and interpretation boundaries.
"""


def _add_support_and_jaccard(
    similarities: list[dict[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    companies: Sequence[Mapping[str, Any]],
    threshold: int,
) -> list[dict[str, Any]]:
    edge_map = {
        canonical_pair(str(row["company1_id"]), str(row["company2_id"])): int(row["shared_event_count"])
        for row in edges
    }
    counts = {str(row["company_id"]): int(row["event_count"]) for row in companies}
    result: list[dict[str, Any]] = []
    for row in similarities:
        key = canonical_pair(str(row["company1_id"]), str(row["company2_id"]))
        shared = edge_map.get(key, 0)
        union = counts[key[0]] + counts[key[1]] - shared
        expected = shared / union if union else 0.0
        difference = abs(float(row["similarity"]) - expected)
        if difference > 1.0e-9:
            raise RuntimeError(
                f"NodeSimilarity cross-check failed for {key}: GDS={row['similarity']}, expected={expected}."
            )
        result.append(
            {
                **row,
                "shared_event_count": shared,
                "event_union_count": union,
                "jaccard_crosscheck": expected,
                "crosscheck_absolute_difference": difference,
                "meets_support_threshold": shared >= threshold,
            }
        )
    return result


def _memory_row(algorithm: str, projection: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    row = dict(rows[0]) if rows else {}
    return {"algorithm": algorithm, "projection": projection, **row}


def run_analysis(config_path: Path, output_override: Path | None = None) -> Path:
    config_path = config_path.resolve()
    config = load_config(config_path)
    project_root = config_path.parent.parent
    settings = gds_settings(config)
    output_directory = resolve_path(
        project_root, output_override or settings.output_directory
    ).resolve()
    tables_directory = output_directory / "tables"
    figures_directory = output_directory / "figures"
    tables_directory.mkdir(parents=True, exist_ok=True)
    figures_directory.mkdir(parents=True, exist_ok=True)
    # Remove only known derived PNG copies so a new SVG-only run cannot leave
    # stale images from an earlier conversion step.
    for stem in FIGURE_STEMS:
        (figures_directory / f"{stem}.png").unlink(missing_ok=True)

    validate_query_contract()
    load_dotenv(project_root / ".env")
    neo4j = connection_settings(config)
    password = os.getenv(neo4j.password_environment_variable, "").strip()
    if not password:
        raise RuntimeError(
            f"{neo4j.password_environment_variable} is missing. Add it to {project_root / '.env'}."
        )

    suffix = uuid.uuid4().hex[:12]
    bipartite_graph = f"{settings.graph_name_prefix}_bipartite_{suffix}"
    coevent_graph = f"{settings.graph_name_prefix}_coevent_{suffix}"
    created_graphs: set[str] = set()
    cleanup_errors: list[str] = []
    counts_after: dict[str, Any] = {}
    catalog_after: list[str] = []

    with GraphDatabase.driver(neo4j.uri, auth=(neo4j.user, password)) as driver:
        driver.verify_connectivity()
        environment = _read_records(driver, neo4j.database, ENVIRONMENT_QUERY)[0]
        environment.update(_read_records(driver, neo4j.database, GDS_VERSION_QUERY)[0])
        capabilities = {
            str(row["name"])
            for row in _read_records(
                driver,
                neo4j.database,
                GDS_CAPABILITY_QUERY,
                {"required": sorted(REQUIRED_GDS_PROCEDURES)},
            )
        }
        missing = sorted(REQUIRED_GDS_PROCEDURES - capabilities)
        if missing:
            raise RuntimeError(f"Required GDS procedures are unavailable: {', '.join(missing)}")

        counts_before = _read_records(driver, neo4j.database, DATABASE_COUNTS_QUERY)[0]
        catalog_before = [
            str(row["graphName"])
            for row in _read_records(driver, neo4j.database, CATALOG_QUERY)
        ]
        collisions = sorted(
            {bipartite_graph, coevent_graph} & set(catalog_before)
        )
        if collisions:
            raise RuntimeError(
                "Refusing to reuse an existing GDS graph name: "
                + ", ".join(collisions)
            )
        live_gds_input_rows = _read_records(
            driver, neo4j.database, GDS_INPUT_FINGERPRINT_QUERY
        )
        companies = _read_records(driver, neo4j.database, COMPANIES_QUERY)
        edges = _read_records(driver, neo4j.database, COEVENT_EDGES_QUERY)
        distribution = _read_records(
            driver, neo4j.database, EVENT_COMPANY_DISTRIBUTION_QUERY
        )
        for row in edges:
            row["shared_event_count"] = int(row["shared_event_count"])
            row["meets_support_threshold"] = (
                int(row["shared_event_count"]) >= settings.support_threshold
            )
        if len(companies) < 2:
            raise RuntimeError(
                "GDS company analysis requires at least two Company nodes."
            )
        if not edges:
            raise RuntimeError(
                "The live graph contains no company pair sharing a canonical Event; "
                "NodeSimilarity, Louvain and weighted PageRank would be uninformative."
            )

        try:
            bipartite_projection = _records(
                driver,
                neo4j.database,
                BIPARTITE_PROJECT_QUERY,
                {"graph_name": bipartite_graph},
            )[0]
            created_graphs.add(bipartite_graph)
            coevent_projection = _records(
                driver,
                neo4j.database,
                COEVENT_PROJECT_QUERY,
                {"graph_name": coevent_graph},
            )[0]
            created_graphs.add(coevent_graph)

            if int(coevent_projection["nodeCount"]) != len(companies):
                raise RuntimeError(
                    "The co-event projection lost Company nodes, including possible isolates: "
                    f"expected {len(companies)}, received {coevent_projection['nodeCount']}."
                )
            if int(coevent_projection["relationshipCount"]) != 2 * len(edges):
                raise RuntimeError(
                    "The undirected co-event projection does not match the logical edge table: "
                    f"expected {2 * len(edges)} stored directions, received "
                    f"{coevent_projection['relationshipCount']}."
                )

            common = {"concurrency": settings.concurrency}
            similarity_parameters = {
                "graph_name": bipartite_graph,
                "similarity_cutoff": settings.similarity_cutoff,
                "similarity_metric": settings.similarity_metric,
                "degree_cutoff": settings.degree_cutoff,
                "top_k": settings.top_k,
                **common,
            }
            louvain_parameters = {
                "graph_name": coevent_graph,
                "max_levels": settings.louvain_max_levels,
                "max_iterations": settings.louvain_max_iterations,
                "tolerance": settings.louvain_tolerance,
                **common,
            }
            pagerank_parameters = {
                "graph_name": coevent_graph,
                "max_iterations": settings.pagerank_max_iterations,
                "damping_factor": settings.pagerank_damping_factor,
                "tolerance": settings.pagerank_tolerance,
                **common,
            }

            memory_rows = [
                _memory_row(
                    "WCC",
                    "company_coevent",
                    _records(
                        driver,
                        neo4j.database,
                        WCC_ESTIMATE_QUERY,
                        {"graph_name": coevent_graph, **common},
                    ),
                ),
                _memory_row(
                    "NodeSimilarity",
                    "company_event_bipartite",
                    _records(driver, neo4j.database, NODE_SIMILARITY_ESTIMATE_QUERY, similarity_parameters),
                ),
                _memory_row(
                    "Louvain_weighted",
                    "company_coevent",
                    _records(driver, neo4j.database, LOUVAIN_ESTIMATE_QUERY, louvain_parameters),
                ),
                _memory_row(
                    "PageRank_weighted",
                    "company_coevent",
                    _records(driver, neo4j.database, PAGERANK_ESTIMATE_QUERY, pagerank_parameters),
                ),
            ]

            wcc_raw = _records(
                driver,
                neo4j.database,
                WCC_STREAM_QUERY,
                {"graph_name": coevent_graph, **common},
            )
            similarity_raw = _records(
                driver, neo4j.database, NODE_SIMILARITY_STREAM_QUERY, similarity_parameters
            )
            weighted_louvain_raw = _records(
                driver, neo4j.database, LOUVAIN_WEIGHTED_STREAM_QUERY, louvain_parameters
            )
            unweighted_louvain_raw = _records(
                driver, neo4j.database, LOUVAIN_UNWEIGHTED_STREAM_QUERY, louvain_parameters
            )
            weighted_louvain_stats = _records(
                driver, neo4j.database, LOUVAIN_WEIGHTED_STATS_QUERY, louvain_parameters
            )[0]
            unweighted_louvain_stats = _records(
                driver, neo4j.database, LOUVAIN_UNWEIGHTED_STATS_QUERY, louvain_parameters
            )[0]
            page_rank_raw = _records(
                driver, neo4j.database, PAGERANK_STREAM_QUERY, pagerank_parameters
            )
        finally:
            for graph_name in (coevent_graph, bipartite_graph):
                if graph_name not in created_graphs:
                    continue
                try:
                    _records(
                        driver,
                        neo4j.database,
                        DROP_GRAPH_QUERY,
                        {"graph_name": graph_name},
                    )
                except Exception as exc:  # cleanup must attempt both graphs
                    cleanup_errors.append(f"{graph_name}: {exc}")

        counts_after = _read_records(driver, neo4j.database, DATABASE_COUNTS_QUERY)[0]
        catalog_after = [
            str(row["graphName"])
            for row in _read_records(driver, neo4j.database, CATALOG_QUERY)
        ]

    if cleanup_errors:
        raise RuntimeError("GDS projection cleanup failed: " + " | ".join(cleanup_errors))
    leaked = sorted({bipartite_graph, coevent_graph} & set(catalog_after))
    if leaked:
        raise RuntimeError(f"Temporary GDS projections were not removed: {leaked}")
    if counts_before != counts_after:
        raise RuntimeError(
            "The persisted Neo4j node/relationship counts changed during GDS analysis: "
            f"before={counts_before}, after={counts_after}."
        )

    wcc_labels = canonical_group_labels(
        {str(row["company_id"]): row["componentId"] for row in wcc_raw}, "WCC"
    )
    weighted_labels = canonical_group_labels(
        {str(row["company_id"]): row["communityId"] for row in weighted_louvain_raw},
        "LC",
    )
    unweighted_labels = canonical_group_labels(
        {str(row["company_id"]): row["communityId"] for row in unweighted_louvain_raw},
        "ULC",
    )
    page_rank = {
        str(row["company_id"]): float(row["page_rank"]) for row in page_rank_raw
    }
    metrics = build_structural_metrics(companies, edges)
    for row in metrics:
        company_id = str(row["company_id"])
        row["wcc_component"] = wcc_labels[company_id]
        row["louvain_community"] = weighted_labels[company_id]
        row["unweighted_louvain_community"] = unweighted_labels[company_id]
        row["page_rank"] = page_rank[company_id]
    page_ranks = average_ranks([float(row["page_rank"]) for row in metrics], descending=True)
    degree_ranks = average_ranks([float(row["coevent_degree"]) for row in metrics], descending=True)
    strength_ranks = average_ranks([float(row["coevent_strength"]) for row in metrics], descending=True)
    for row, page_rank_value, degree_rank, strength_rank in zip(metrics, page_ranks, degree_ranks, strength_ranks):
        row["page_rank_rank"] = page_rank_value
        row["degree_rank"] = degree_rank
        row["strength_rank"] = strength_rank

    similarities = _add_support_and_jaccard(
        deduplicate_similarity_rows(similarity_raw),
        edges,
        companies,
        settings.support_threshold,
    )
    wcc_components = _component_rows(metrics, "wcc_component", "wcc_component")
    weighted_communities = _component_rows(metrics, "louvain_community", "louvain_community")
    unweighted_communities = _component_rows(
        metrics, "unweighted_louvain_community", "unweighted_louvain_community"
    )
    community_rows: list[dict[str, Any]] = []
    weighted_membership = {
        str(member["company_id"]): frozenset(
            str(candidate["company_id"])
            for candidate in metrics
            if candidate["louvain_community"] == member["louvain_community"]
        )
        for member in metrics
    }
    unweighted_membership = {
        str(member["company_id"]): frozenset(
            str(candidate["company_id"])
            for candidate in metrics
            if candidate["unweighted_louvain_community"]
            == member["unweighted_louvain_community"]
        )
        for member in metrics
    }
    for row in metrics:
        company_id = str(row["company_id"])
        community_rows.append(
            {
                "company_id": row["company_id"],
                "company": row["company"],
                "weighted_community": row["louvain_community"],
                "unweighted_community": row["unweighted_louvain_community"],
                "community_assignment_changed": (
                    weighted_membership[company_id]
                    != unweighted_membership[company_id]
                ),
            }
        )
    threshold_rows = _threshold_sensitivity(
        companies, edges, settings.sensitivity_thresholds
    )
    correlation_rows = _correlation_rows(metrics)

    projection_rows = [
        {
            "projection": "company_event_bipartite",
            "node_count": int(bipartite_projection["nodeCount"]),
            "projected_relationship_count": int(bipartite_projection["relationshipCount"]),
            "logical_relationship_count": int(bipartite_projection["relationshipCount"]),
            "orientation": "Company to Event (REVERSE of POTENTIALLY_AFFECTS)",
            "purpose": "Jaccard NodeSimilarity over canonical Event neighbours",
        },
        {
            "projection": "company_coevent",
            "node_count": int(coevent_projection["nodeCount"]),
            "projected_relationship_count": int(coevent_projection["relationshipCount"]),
            "logical_relationship_count": len(edges),
            "orientation": "undirected; stored in both directions by GDS",
            "purpose": "WCC, Louvain and weighted PageRank",
        },
    ]
    algorithm_rows = [
        {
            "algorithm": "WCC",
            "mode": "stream",
            "result_count": len(wcc_components),
            "summary": f"{len(wcc_components)} components; largest={wcc_components[0]['company_count']}",
        },
        {
            "algorithm": "NodeSimilarity",
            "mode": "stream",
            "result_count": len(similarities),
            "summary": "Jaccard over Company-to-Event neighbours",
        },
        {
            "algorithm": "Louvain_weighted",
            "mode": "stream+stats",
            "result_count": len(weighted_communities),
            "summary": f"modularity={float(weighted_louvain_stats['modularity']):.6f}",
        },
        {
            "algorithm": "Louvain_unweighted_sensitivity",
            "mode": "stream+stats",
            "result_count": len(unweighted_communities),
            "summary": f"modularity={float(unweighted_louvain_stats['modularity']):.6f}",
        },
        {
            "algorithm": "PageRank_weighted",
            "mode": "stream",
            "result_count": len(metrics),
            "summary": "supplementary centrality on disconnected graph",
        },
    ]

    table_files = {
        "gds_environment": tables_directory / "gds_environment.csv",
        "projection_summary": tables_directory / "projection_summary.csv",
        "memory_estimates": tables_directory / "memory_estimates.csv",
        "algorithm_summary": tables_directory / "gds_algorithm_summary.csv",
        "event_company_distribution": tables_directory / "event_company_distribution.csv",
        "coevent_edges": tables_directory / "company_coevent_edges.csv",
        "centrality": tables_directory / "company_centrality.csv",
        "wcc": tables_directory / "wcc_components.csv",
        "similarity": tables_directory / "company_node_similarity.csv",
        "communities": tables_directory / "louvain_communities.csv",
        "weighted_community_summary": tables_directory / "weighted_louvain_community_summary.csv",
        "unweighted_community_summary": tables_directory / "unweighted_louvain_community_summary.csv",
        "correlations": tables_directory / "centrality_correlations.csv",
        "threshold_sensitivity": tables_directory / "threshold_sensitivity.csv",
    }
    environment_rows = [
        {"metric": "neo4j_version", "value": environment["neo4j_version"]},
        {"metric": "neo4j_edition", "value": environment["edition"]},
        {"metric": "gds_version", "value": environment["gds_version"]},
        {"metric": "database_node_count_before", "value": counts_before["node_count"]},
        {"metric": "database_relationship_count_before", "value": counts_before["relationship_count"]},
        {"metric": "database_node_count_after", "value": counts_after["node_count"]},
        {"metric": "database_relationship_count_after", "value": counts_after["relationship_count"]},
        {"metric": "temporary_graphs_remaining", "value": 0},
    ]
    _write_csv(environment_rows, table_files["gds_environment"])
    _write_csv(projection_rows, table_files["projection_summary"])
    _write_csv(memory_rows, table_files["memory_estimates"])
    _write_csv(algorithm_rows, table_files["algorithm_summary"])
    _write_csv(distribution, table_files["event_company_distribution"])
    _write_csv(edges, table_files["coevent_edges"])
    _write_csv(metrics, table_files["centrality"])
    _write_csv(wcc_components, table_files["wcc"])
    _write_csv(similarities, table_files["similarity"])
    _write_csv(community_rows, table_files["communities"])
    _write_csv(weighted_communities, table_files["weighted_community_summary"])
    _write_csv(unweighted_communities, table_files["unweighted_community_summary"])
    _write_csv(correlation_rows, table_files["correlations"])
    _write_csv(threshold_rows, table_files["threshold_sensitivity"])

    event_strength_spearman = next(
        row["spearman"]
        for row in correlation_rows
        if row["metric_1"] == "event_count" and row["metric_2"] == "coevent_strength"
    )
    _network_figure(metrics, edges, figures_directory / "company_coevent_network.svg")
    _edge_bar_figure(edges, settings.support_threshold, figures_directory / "top_shared_event_pairs.svg")
    _scatter_figure(metrics, event_strength_spearman, figures_directory / "event_count_vs_strength.svg")

    summary = {
        "database_node_count": int(counts_before["node_count"]),
        "database_relationship_count": int(counts_before["relationship_count"]),
        "company_count": len(companies),
        "bipartite_node_count": int(bipartite_projection["nodeCount"]),
        "bipartite_relationship_count": int(bipartite_projection["relationshipCount"]),
        "logical_edge_count": len(edges),
        "projected_relationship_count": int(coevent_projection["relationshipCount"]),
        "edge_weight_sum": sum(int(row["shared_event_count"]) for row in edges),
        "wcc_count": len(wcc_components),
        "largest_wcc_size": int(wcc_components[0]["company_count"]),
        "isolate_count": sum(bool(row["is_isolate"]) for row in metrics),
        "weighted_community_count": len(weighted_communities),
        "weighted_modularity": float(weighted_louvain_stats["modularity"]),
        "unweighted_community_count": len(unweighted_communities),
        "unweighted_modularity": float(unweighted_louvain_stats["modularity"]),
        "node_similarity_pair_count": len(similarities),
        "single_support_edge_count": sum(int(row["shared_event_count"]) == 1 for row in edges),
        "support_threshold": settings.support_threshold,
        "sensitivity_thresholds": list(settings.sensitivity_thresholds),
    }

    report_cn = _report_text(
        language="cn",
        summary=summary,
        metrics=metrics,
        edges=edges,
        similarities=similarities,
        wcc_rows=wcc_components,
        community_rows=weighted_communities,
        correlations=correlation_rows,
    )
    report_en = _report_text(
        language="en",
        summary=summary,
        metrics=metrics,
        edges=edges,
        similarities=similarities,
        wcc_rows=wcc_components,
        community_rows=weighted_communities,
        correlations=correlation_rows,
    )
    report_cn_path = output_directory / "gds_results_cn.md"
    report_en_path = output_directory / "gds_results_en.md"
    report_cn_path.write_text(report_cn, encoding="utf-8")
    report_en_path.write_text(report_en, encoding="utf-8")

    import_config = config.get("neo4j_import", {})
    import_directory = resolve_path(
        project_root,
        import_config.get("output_directory", "data/neo4j/import")
        if isinstance(import_config, Mapping)
        else "data/neo4j/import",
    )
    input_hashes: dict[str, str] = {}
    for filename in (
        "companies.csv",
        "events.csv",
        "event_potentially_affects_company.csv",
    ):
        path = import_directory / filename
        if path.exists():
            input_hashes[str(path.relative_to(project_root))] = _sha256_file(path)

    output_hashes = _package_output_hashes(output_directory)
    parameter_manifest = {
        key: _json_value(value) for key, value in asdict(settings).items()
    }
    parameter_manifest["resolved_output_directory"] = str(output_directory)
    parameter_manifest["output_override_applied"] = output_override is not None
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "exploratory_structural_gds",
        "read_only_contract": {
            "persisted_kg_writeback": False,
            "algorithm_modes": ["stream", "stats", "estimate"],
            "temporary_projections_dropped": True,
            "database_counts_unchanged": counts_before == counts_after,
            "temporary_projection_names": [bipartite_graph, coevent_graph],
            "catalog_before": catalog_before,
            "catalog_after": catalog_after,
        },
        "environment": environment,
        "required_gds_procedures": sorted(REQUIRED_GDS_PROCEDURES),
        "parameters": parameter_manifest,
        "summary": summary,
        "louvain_stats": {
            "weighted": weighted_louvain_stats,
            "unweighted": unweighted_louvain_stats,
        },
        "configuration_sha256": _hash_config(config),
        "expected_import_artifact_sha256": input_hashes,
        "live_gds_input_sha256": _hash_records(live_gds_input_rows),
        "live_gds_input_record_count": len(live_gds_input_rows),
        "output_sha256": output_hashes,
        "output_hash_scope": (
            "CSV, Markdown and SVG artifacts generated by the Python analysis."
        ),
        "interpretation_boundaries": [
            "Shared-event structure is conditional on source coverage, extraction and deduplication.",
            "Similarity is canonical-event-neighbour Jaccard, not business similarity.",
            "Communities are projection-specific and exploratory.",
            "Centrality is not systemic importance, causal impact or investment advice.",
            "Market-window returns remain descriptive rather than causal.",
        ],
    }
    (output_directory / "gds_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_value),
        encoding="utf-8",
    )

    print("GDS analysis completed without writing to the persisted Neo4j graph.")
    print(f"Companies: {len(companies)}")
    print(f"Logical company co-event edges: {len(edges)}")
    print(f"WCC components: {len(wcc_components)}")
    print(f"Weighted Louvain communities: {len(weighted_communities)}")
    print(f"NodeSimilarity pairs: {len(similarities)}")
    print(f"Output directory: {output_directory}")
    return output_directory


def main() -> int:
    args = parse_args()
    try:
        if args.print_output_directory:
            print(configured_output_directory(args.config, args.output_directory))
            return 0
        if args.finalize_manifest:
            output_directory = configured_output_directory(
                args.config, args.output_directory
            )
            manifest_path = finalize_manifest(output_directory)
            print(f"Finalized GDS manifest: {manifest_path}")
            return 0
        run_analysis(args.config, args.output_directory)
    except Exception as exc:
        print(f"GDS analysis failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
