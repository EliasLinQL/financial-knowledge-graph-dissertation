"""Conservatively cluster cross-article event mentions into canonical Events."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import yaml


EVENT_COLUMNS = {
    "event_id",
    "article_id",
    "event_date",
    "publication_timestamp",
    "event_title",
    "evidence_span",
    "event_type",
    "recommended_for_graph",
}
LINK_COLUMNS = {
    "event_id",
    "article_id",
    "company_id",
    "evidence_sentence",
    "relationship_focus_score",
    "recommended_for_graph",
}

STOPWORDS = {
    "a",
    "about",
    "after",
    "again",
    "against",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "between",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "during",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "here",
    "hers",
    "him",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "may",
    "more",
    "most",
    "new",
    "no",
    "not",
    "now",
    "of",
    "on",
    "one",
    "or",
    "other",
    "our",
    "out",
    "over",
    "said",
    "says",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "they",
    "this",
    "those",
    "to",
    "under",
    "up",
    "was",
    "we",
    "were",
    "what",
    "when",
    "which",
    "while",
    "who",
    "will",
    "with",
    "would",
    "you",
}


@dataclass(frozen=True)
class Similarity:
    combined: float
    word: float
    character: float
    token_containment: float
    exact_text: bool


@dataclass
class Cluster:
    members: list[str]
    article_ids: set[str]
    company_ids: set[str]
    event_type: str
    first_date: date
    last_date: date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cluster semantically similar event mentions from different Guardian "
            "articles into provenance-preserving canonical Events."
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
        help="Guardian collection mode to process.",
    )
    parser.add_argument("--events-csv", type=Path)
    parser.add_argument("--event-links-csv", type=Path)
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


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def normalise_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
        .casefold()
    )
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9$%']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def event_similarity_text(row: Mapping[str, Any]) -> str:
    title = normalise_text(row.get("event_title", ""))
    evidence = normalise_text(row.get("evidence_span", ""))
    if title and evidence.startswith(title):
        return evidence
    return f"{title} {evidence}".strip()


def word_features(text: str) -> Counter[str]:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)
        if token not in STOPWORDS and len(token) > 1
    ]
    features: Counter[str] = Counter(tokens)
    features.update(
        f"{left}_{right}" for left, right in zip(tokens, tokens[1:], strict=False)
    )
    return features


def character_features(text: str, minimum: int = 3, maximum: int = 5) -> Counter[str]:
    compact = re.sub(r"\s+", " ", text)
    features: Counter[str] = Counter()
    for size in range(minimum, maximum + 1):
        features.update(
            compact[position : position + size]
            for position in range(max(0, len(compact) - size + 1))
        )
    return features


def inverse_document_frequency(
    feature_sets: Iterable[Counter[str]],
) -> dict[str, float]:
    sets = [set(counter) for counter in feature_sets]
    total = len(sets)
    document_frequency: Counter[str] = Counter()
    for values in sets:
        document_frequency.update(values)
    return {
        feature: math.log((total + 1) / (count + 1)) + 1.0
        for feature, count in document_frequency.items()
    }


def weighted_vector(
    features: Counter[str],
    idf: Mapping[str, float] | None = None,
) -> dict[str, float]:
    vector: dict[str, float] = {}
    for feature, count in features.items():
        weight = 1.0 + math.log(count)
        if idf is not None:
            weight *= idf.get(feature, 1.0)
        vector[feature] = weight
    return vector


def cosine_similarity(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    numerator = sum(left[key] * right[key] for key in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def token_containment(left: Counter[str], right: Counter[str]) -> float:
    left_tokens = {value for value in left if "_" not in value}
    right_tokens = {value for value in right if "_" not in value}
    denominator = min(len(left_tokens), len(right_tokens))
    if denominator == 0:
        return 0.0
    return len(left_tokens & right_tokens) / denominator


def calculate_similarity(
    left_text: str,
    right_text: str,
    left_words: Counter[str],
    right_words: Counter[str],
    left_word_vector: Mapping[str, float],
    right_word_vector: Mapping[str, float],
    left_character_vector: Mapping[str, float],
    right_character_vector: Mapping[str, float],
    word_weight: float,
    character_weight: float,
) -> Similarity:
    exact = bool(left_text) and left_text == right_text
    word_score = cosine_similarity(left_word_vector, right_word_vector)
    character_score = cosine_similarity(
        left_character_vector, right_character_vector
    )
    containment = token_containment(left_words, right_words)
    combined = 1.0 if exact else (
        word_weight * word_score + character_weight * character_score
    )
    return Similarity(
        combined=combined,
        word=word_score,
        character=character_score,
        token_containment=containment,
        exact_text=exact,
    )


def stable_canonical_event_id(source_event_ids: Iterable[str]) -> str:
    member_ids = sorted(set(str(value).strip() for value in source_event_ids))
    digest = hashlib.sha1("|".join(member_ids).encode("utf-8")).hexdigest()
    return f"CEVT_{digest[:14].upper()}"


def parse_event_date(value: Any) -> date:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        raise ValueError(f"Invalid event_date: {value!r}")
    return timestamp.date()


def numeric(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {path}. Close it in Excel or another editor."
        ) from exc


def best_relationship_row(group: pd.DataFrame) -> pd.Series:
    candidates = group.copy()
    candidates["_recommended"] = candidates["recommended_for_graph"].map(parse_bool)
    candidates["_positive"] = candidates.get(
        "nlp_positive_probability", pd.Series("0", index=candidates.index)
    ).map(numeric)
    candidates["_nlp_score"] = candidates.get(
        "nlp_relationship_score", pd.Series("0", index=candidates.index)
    ).map(numeric)
    candidates["_focus"] = candidates["relationship_focus_score"].map(numeric)
    candidates["_confidence"] = candidates.get(
        "link_confidence", pd.Series("", index=candidates.index)
    ).map({"high": 3, "medium": 2, "low": 1}).fillna(0)
    candidates = candidates.sort_values(
        [
            "_recommended",
            "_positive",
            "_nlp_score",
            "_focus",
            "_confidence",
            "event_id",
        ],
        ascending=[False, False, False, False, False, True],
        kind="stable",
    )
    return group.loc[candidates.index[0]].copy()


def representative_event_id(
    member_ids: Iterable[str],
    events_by_id: Mapping[str, Mapping[str, Any]],
    links_by_event: Mapping[str, pd.DataFrame],
) -> str:
    def quality(event_id: str) -> tuple[Any, ...]:
        group = links_by_event.get(event_id)
        if group is None or group.empty:
            recommended_links = 0
            positive = 0.0
            nlp_score = 0.0
            focus = 0.0
        else:
            recommended_links = int(
                group["recommended_for_graph"].map(parse_bool).sum()
            )
            positive = max(
                (
                    numeric(value)
                    for value in group.get(
                        "nlp_positive_probability",
                        pd.Series("0", index=group.index),
                    )
                ),
                default=0.0,
            )
            nlp_score = max(
                (
                    numeric(value)
                    for value in group.get(
                        "nlp_relationship_score",
                        pd.Series("0", index=group.index),
                    )
                ),
                default=0.0,
            )
            focus = max(
                (numeric(value) for value in group["relationship_focus_score"]),
                default=0.0,
            )
        event = events_by_id[event_id]
        return (
            recommended_links,
            positive,
            nlp_score,
            focus,
            numeric(event.get("event_score", 0)),
            min(len(str(event.get("evidence_span", ""))), 700),
            -len(str(event.get("evidence_span", ""))),
        )

    return sorted(member_ids, key=lambda event_id: (quality(event_id), event_id))[-1]


def cluster_events(
    events: pd.DataFrame,
    links: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return canonical events, canonical links, source mentions and a report."""
    require_columns(events, EVENT_COLUMNS, "Event table")
    require_columns(links, LINK_COLUMNS, "Event-company table")
    if events["event_id"].duplicated().any():
        raise ValueError("Event table contains duplicate event_id values.")
    if links[["event_id", "company_id"]].duplicated().any():
        raise ValueError("Event-company table contains duplicate pairs.")

    event_ids = set(events["event_id"])
    unknown_link_ids = sorted(set(links["event_id"]) - event_ids)
    if unknown_link_ids:
        raise ValueError(
            "Event-company links reference unknown events: "
            + ", ".join(unknown_link_ids[:10])
        )

    maximum_days = int(config.get("maximum_date_difference_days", 7))
    minimum_similarity = float(config.get("minimum_text_similarity", 0.72))
    minimum_containment = float(config.get("minimum_token_containment", 0.45))
    word_weight = float(config.get("word_similarity_weight", 0.75))
    character_weight = float(config.get("character_similarity_weight", 0.25))
    if maximum_days < 0:
        raise ValueError("maximum_date_difference_days cannot be negative.")
    if not 0 <= minimum_similarity <= 1:
        raise ValueError("minimum_text_similarity must be between 0 and 1.")
    if not 0 <= minimum_containment <= 1:
        raise ValueError("minimum_token_containment must be between 0 and 1.")
    if word_weight < 0 or character_weight < 0:
        raise ValueError("Similarity weights cannot be negative.")
    weight_total = word_weight + character_weight
    if weight_total <= 0:
        raise ValueError("At least one similarity weight must be positive.")
    word_weight /= weight_total
    character_weight /= weight_total
    require_same_type = bool(config.get("require_same_event_type", True))
    require_shared_company = bool(config.get("require_shared_company", True))
    merge_qualified_events = bool(config.get("merge_qualified_events", True))

    event_rows = {
        str(row["event_id"]): row.to_dict() for _, row in events.iterrows()
    }
    event_dates = {
        event_id: parse_event_date(row["event_date"])
        for event_id, row in event_rows.items()
    }
    event_texts = {
        event_id: event_similarity_text(row)
        for event_id, row in event_rows.items()
    }
    similarity_event_ids = {
        event_id
        for event_id, row in event_rows.items()
        if parse_bool(row["recommended_for_graph"])
    }
    word_counters = {
        event_id: word_features(event_texts[event_id])
        for event_id in similarity_event_ids
    }
    idf = inverse_document_frequency(word_counters.values())
    word_vectors = {
        event_id: weighted_vector(counter, idf)
        for event_id, counter in word_counters.items()
    }
    character_vectors = {
        event_id: weighted_vector(character_features(event_texts[event_id]))
        for event_id in similarity_event_ids
    }
    similarity_cache: dict[tuple[str, str], Similarity] = {}

    def similarity(left_id: str, right_id: str) -> Similarity:
        key = tuple(sorted((left_id, right_id)))
        if key not in similarity_cache:
            similarity_cache[key] = calculate_similarity(
                event_texts[left_id],
                event_texts[right_id],
                word_counters[left_id],
                word_counters[right_id],
                word_vectors[left_id],
                word_vectors[right_id],
                character_vectors[left_id],
                character_vectors[right_id],
                word_weight,
                character_weight,
            )
        return similarity_cache[key]

    links_by_event = {
        str(event_id): group.copy()
        for event_id, group in links.groupby("event_id", sort=False)
    }
    company_ids_by_event = {
        event_id: set(
            group.loc[
                group["recommended_for_graph"].map(parse_bool), "company_id"
            ].astype(str)
        )
        for event_id, group in links_by_event.items()
    }
    for event_id, group in links_by_event.items():
        if not company_ids_by_event[event_id]:
            company_ids_by_event[event_id] = set(group["company_id"].astype(str))
    for event_id in event_rows:
        company_ids_by_event.setdefault(event_id, set())

    timestamps = pd.to_datetime(
        events["publication_timestamp"], utc=True, errors="coerce"
    )
    timestamp_by_event = dict(zip(events["event_id"], timestamps, strict=False))
    invalid_timestamps = [
        event_id
        for event_id, value in timestamp_by_event.items()
        if pd.isna(value)
    ]
    if invalid_timestamps:
        raise ValueError(
            "Events contain invalid publication timestamps: "
            + ", ".join(sorted(invalid_timestamps)[:10])
        )

    ordered_ids = sorted(
        event_rows,
        key=lambda event_id: (timestamp_by_event[event_id], event_id),
    )
    clusters: list[Cluster] = []
    cluster_by_event: dict[str, int] = {}

    for event_id in ordered_ids:
        row = event_rows[event_id]
        event_date = event_dates[event_id]
        article_id = str(row["article_id"])
        recommended = parse_bool(row["recommended_for_graph"])
        best_cluster: int | None = None
        best_score = -1.0

        if recommended and merge_qualified_events:
            for cluster_index, cluster in enumerate(clusters):
                if not parse_bool(
                    event_rows[cluster.members[0]]["recommended_for_graph"]
                ):
                    continue
                if article_id in cluster.article_ids:
                    continue
                if require_same_type and str(row["event_type"]) != cluster.event_type:
                    continue
                if (
                    event_date - cluster.first_date
                ).days > maximum_days or (
                    cluster.last_date - event_date
                ).days > maximum_days:
                    continue
                company_ids = company_ids_by_event[event_id]
                if (
                    require_shared_company
                    and not company_ids.intersection(cluster.company_ids)
                ):
                    continue

                pair_scores = [
                    similarity(event_id, member_id)
                    for member_id in cluster.members
                ]
                if not pair_scores:
                    continue
                if any(
                    score.combined < minimum_similarity
                    or (
                        not score.exact_text
                        and score.token_containment < minimum_containment
                    )
                    for score in pair_scores
                ):
                    continue
                complete_link_score = min(
                    score.combined for score in pair_scores
                )
                if complete_link_score > best_score:
                    best_score = complete_link_score
                    best_cluster = cluster_index

        if best_cluster is None:
            cluster_by_event[event_id] = len(clusters)
            clusters.append(
                Cluster(
                    members=[event_id],
                    article_ids={article_id},
                    company_ids=set(company_ids_by_event[event_id]),
                    event_type=str(row["event_type"]),
                    first_date=event_date,
                    last_date=event_date,
                )
            )
            continue

        cluster = clusters[best_cluster]
        cluster.members.append(event_id)
        cluster.article_ids.add(article_id)
        cluster.company_ids.update(company_ids_by_event[event_id])
        cluster.first_date = min(cluster.first_date, event_date)
        cluster.last_date = max(cluster.last_date, event_date)
        cluster_by_event[event_id] = best_cluster

    canonical_rows: list[dict[str, Any]] = []
    mention_rows: list[dict[str, Any]] = []
    canonical_id_by_source: dict[str, str] = {}
    representative_by_canonical: dict[str, str] = {}
    canonical_event_type: dict[str, str] = {}

    for cluster in clusters:
        source_ids = sorted(cluster.members)
        canonical_id = stable_canonical_event_id(source_ids)
        representative_id = representative_event_id(
            source_ids, event_rows, links_by_event
        )
        representative = dict(event_rows[representative_id])
        representative_by_canonical[canonical_id] = representative_id
        canonical_event_type[canonical_id] = str(representative["event_type"])
        source_articles = sorted(
            {str(event_rows[event_id]["article_id"]) for event_id in source_ids}
        )
        publications = sorted(timestamp_by_event[event_id] for event_id in source_ids)
        dates = sorted(event_dates[event_id] for event_id in source_ids)
        recommended = any(
            parse_bool(event_rows[event_id]["recommended_for_graph"])
            for event_id in source_ids
        )
        member_similarities = [
            similarity(representative_id, event_id).combined
            if event_id != representative_id
            else 1.0
            for event_id in source_ids
        ]
        exact_cluster = len(source_ids) > 1 and all(
            similarity(representative_id, event_id).exact_text
            for event_id in source_ids
            if event_id != representative_id
        )
        method = (
            "singleton"
            if len(source_ids) == 1
            else "exact_text"
            if exact_cluster
            else "complete_link_text_similarity"
        )

        representative["event_id"] = canonical_id
        representative["event_date"] = dates[0].isoformat()
        representative["publication_timestamp"] = publications[0].isoformat()
        representative["recommended_for_graph"] = bool_text(recommended)
        representative["representative_event_id"] = representative_id
        representative["source_event_count"] = len(source_ids)
        representative["source_article_count"] = len(source_articles)
        representative["source_event_ids"] = json.dumps(
            source_ids, ensure_ascii=False
        )
        representative["source_article_ids"] = json.dumps(
            source_articles, ensure_ascii=False
        )
        representative["first_publication_timestamp"] = publications[0].isoformat()
        representative["last_publication_timestamp"] = publications[-1].isoformat()
        representative["deduplication_method"] = method
        representative["deduplication_min_similarity"] = min(member_similarities)
        representative["deduplication_max_date_span_days"] = (
            dates[-1] - dates[0]
        ).days
        canonical_rows.append(representative)

        for event_id in source_ids:
            canonical_id_by_source[event_id] = canonical_id
            source = event_rows[event_id]
            score = (
                similarity(representative_id, event_id)
                if event_id != representative_id
                else Similarity(1.0, 1.0, 1.0, 1.0, True)
            )
            mention_rows.append(
                {
                    "canonical_event_id": canonical_id,
                    "source_event_id": event_id,
                    "article_id": source["article_id"],
                    "event_date": source["event_date"],
                    "publication_timestamp": source["publication_timestamp"],
                    "event_title": source["event_title"],
                    "evidence_span": source["evidence_span"],
                    "evidence_source": source.get("evidence_source", ""),
                    "event_granularity": source.get("event_granularity", ""),
                    "event_type": source["event_type"],
                    "recommended_for_graph": source["recommended_for_graph"],
                    "similarity_to_representative": score.combined,
                    "word_similarity": score.word,
                    "character_similarity": score.character,
                    "token_containment": score.token_containment,
                    "is_representative": bool_text(event_id == representative_id),
                    "deduplication_method": method,
                }
            )

    link_work = links.copy()
    link_work["canonical_event_id"] = link_work["event_id"].map(
        canonical_id_by_source
    )
    if link_work["canonical_event_id"].isna().any():
        raise ValueError("Some event-company links could not be mapped to a cluster.")

    canonical_link_rows: list[dict[str, Any]] = []
    for (canonical_id, company_id), group in link_work.groupby(
        ["canonical_event_id", "company_id"], sort=True
    ):
        selected = best_relationship_row(group)
        selected_source_id = str(selected["event_id"])
        source_event_ids = sorted(set(group["event_id"].astype(str)))
        source_article_ids = sorted(set(group["article_id"].astype(str)))
        selected["event_id"] = canonical_id
        selected["source_event_id"] = selected_source_id
        selected["source_article_id"] = selected["article_id"]
        selected["relationship_publication_timestamp"] = event_rows[
            selected_source_id
        ]["publication_timestamp"]
        selected["source_relationship_count"] = len(group)
        selected["source_event_ids"] = json.dumps(
            source_event_ids, ensure_ascii=False
        )
        selected["source_article_ids"] = json.dumps(
            source_article_ids, ensure_ascii=False
        )
        selected["recommended_for_graph"] = bool_text(
            any(group["recommended_for_graph"].map(parse_bool))
        )
        selected["deduplicated_relationship"] = bool_text(len(group) > 1)
        selected["event_type"] = canonical_event_type[canonical_id]
        canonical_link_rows.append(selected.to_dict())

    canonical_events = pd.DataFrame(canonical_rows)
    canonical_links = pd.DataFrame(canonical_link_rows)
    mentions = pd.DataFrame(mention_rows)
    if canonical_events["event_id"].duplicated().any():
        raise ValueError("Canonical event IDs are not unique.")
    if canonical_links[["event_id", "company_id"]].duplicated().any():
        raise ValueError("Canonical event-company pairs are not unique.")

    recommended_source_events = int(
        events["recommended_for_graph"].map(parse_bool).sum()
    )
    recommended_canonical_events = int(
        canonical_events["recommended_for_graph"].map(parse_bool).sum()
    )
    recommended_source_links = int(
        links["recommended_for_graph"].map(parse_bool).sum()
    )
    recommended_canonical_links = int(
        canonical_links["recommended_for_graph"].map(parse_bool).sum()
    )
    cluster_sizes = [len(cluster.members) for cluster in clusters]
    multi_source_clusters = sum(size > 1 for size in cluster_sizes)
    method_counts = Counter(
        str(row["deduplication_method"]) for row in canonical_rows
    )
    report_rows: list[dict[str, Any]] = [
        {
            "section": "configuration",
            "metric": "maximum_date_difference_days",
            "value": maximum_days,
            "details": "Complete-link temporal gate",
        },
        {
            "section": "configuration",
            "metric": "minimum_text_similarity",
            "value": minimum_similarity,
            "details": "Weighted word and character similarity",
        },
        {
            "section": "configuration",
            "metric": "minimum_token_containment",
            "value": minimum_containment,
            "details": "Ignored for exact normalized text matches",
        },
        {
            "section": "summary",
            "metric": "source_events",
            "value": len(events),
            "details": "NLP event mentions before cross-article deduplication",
        },
        {
            "section": "summary",
            "metric": "canonical_events",
            "value": len(canonical_events),
            "details": "All canonical events including rejected singletons",
        },
        {
            "section": "summary",
            "metric": "recommended_source_events",
            "value": recommended_source_events,
            "details": "Qualified Event mentions before deduplication",
        },
        {
            "section": "summary",
            "metric": "recommended_canonical_events",
            "value": recommended_canonical_events,
            "details": "Qualified canonical Events after deduplication",
        },
        {
            "section": "summary",
            "metric": "recommended_events_removed_as_duplicates",
            "value": recommended_source_events - recommended_canonical_events,
            "details": "Duplicate mentions retained in the provenance table",
        },
        {
            "section": "summary",
            "metric": "recommended_source_relationships",
            "value": recommended_source_links,
            "details": "Qualified source Event-Company links",
        },
        {
            "section": "summary",
            "metric": "recommended_canonical_relationships",
            "value": recommended_canonical_links,
            "details": "Qualified canonical Event-Company links",
        },
        {
            "section": "summary",
            "metric": "multi_source_clusters",
            "value": multi_source_clusters,
            "details": "Canonical Events supported by more than one source Event",
        },
        {
            "section": "summary",
            "metric": "largest_cluster_size",
            "value": max(cluster_sizes, default=0),
            "details": "Maximum source Event mentions in one canonical Event",
        },
    ]
    report_rows.extend(
        {
            "section": "cluster_method",
            "metric": method,
            "value": count,
            "details": "Canonical Event count",
        }
        for method, count in sorted(method_counts.items())
    )
    return (
        canonical_events,
        canonical_links,
        mentions,
        pd.DataFrame(report_rows),
    )


def run() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project_root = config_path.parent.parent
    try:
        project = load_yaml(config_path)
        news_config = project["news_data"]
        mode_config = news_config["collection_modes"][args.mode]
        nlp_config = project.get("nlp_enrichment", {})
        dedup_config = project.get("event_deduplication", {})
        if not isinstance(dedup_config, dict):
            raise ValueError("event_deduplication must be a YAML mapping.")
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"CONFIGURATION_ERROR: {exc}", file=sys.stderr)
        return 1

    stem = str(mode_config["output_stem"])
    processed_directory = resolve_path(
        project_root, news_config["processed_output_directory"]
    )
    use_nlp_inputs = bool(
        isinstance(nlp_config, dict)
        and nlp_config.get("enabled", False)
        and nlp_config.get("use_for_downstream", False)
    )
    source_event_filename = (
        f"{stem}_event_candidates_nlp.csv"
        if use_nlp_inputs
        else f"{stem}_event_candidates.csv"
    )
    source_link_filename = (
        f"{stem}_event_company_links_nlp.csv"
        if use_nlp_inputs
        else f"{stem}_event_company_links.csv"
    )
    event_path = (
        args.events_csv.resolve()
        if args.events_csv
        else processed_directory / source_event_filename
    )
    link_path = (
        args.event_links_csv.resolve()
        if args.event_links_csv
        else processed_directory / source_link_filename
    )
    output_directory = (
        args.output_directory.resolve()
        if args.output_directory
        else processed_directory
    )

    try:
        events = pd.read_csv(event_path, dtype=str, keep_default_na=False)
        links = pd.read_csv(link_path, dtype=str, keep_default_na=False)
        effective_config = dict(dedup_config)
        if not bool(dedup_config.get("enabled", True)):
            effective_config["merge_qualified_events"] = False
        canonical_events, canonical_links, mentions, report = cluster_events(
            events, links, effective_config
        )
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    event_output = output_directory / f"{stem}_canonical_events.csv"
    link_output = (
        output_directory / f"{stem}_canonical_event_company_links.csv"
    )
    mention_output = output_directory / f"{stem}_event_mentions.csv"
    report_output = output_directory / f"{stem}_event_deduplication_report.csv"
    try:
        write_csv(canonical_events, event_output)
        write_csv(canonical_links, link_output)
        write_csv(mentions, mention_output)
        write_csv(report, report_output)
    except PermissionError as exc:
        print(f"OUTPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    report_values = {
        str(row["metric"]): row["value"] for _, row in report.iterrows()
    }
    count_value = lambda metric: int(float(report_values[metric]))
    print("Cross-article Event deduplication completed.")
    print(f"Source Events: {count_value('source_events')}")
    print(f"Canonical Events: {count_value('canonical_events')}")
    print(
        "Qualified Events: "
        f"{count_value('recommended_source_events')} source mentions -> "
        f"{count_value('recommended_canonical_events')} canonical Events"
    )
    print(
        "Qualified Event-Company relationships: "
        f"{count_value('recommended_source_relationships')} source links -> "
        f"{count_value('recommended_canonical_relationships')} canonical links"
    )
    print(f"Multi-source clusters: {count_value('multi_source_clusters')}")
    print(f"Canonical Events: {event_output}")
    print(f"Canonical Event-Company links: {link_output}")
    print(f"Source mention provenance: {mention_output}")
    print(f"Deduplication report: {report_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
