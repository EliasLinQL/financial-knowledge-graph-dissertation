"""Create conservative evidence-span-level financial event candidates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ARTICLE_COLUMNS = {
    "article_id",
    "headline",
    "publication_date",
    "section_name",
    "web_url",
    "trail_text",
    "body_text",
    "accepted_company_ids",
    "analysis_ready",
}
LINK_COLUMNS = {
    "article_id",
    "company_id",
    "company_name",
    "query_returned",
    "matched_core_aliases",
    "matched_product_aliases",
    "link_status",
    "accepted_for_analysis",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify paragraph-aware evidence-span event candidates from "
            "analysis-ready Guardian articles and create company relationships."
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
    parser.add_argument("--articles-csv", type=Path, help="Optional clean article CSV.")
    parser.add_argument("--links-csv", type=Path, help="Optional clean link CSV.")
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="Optional output directory.",
    )
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


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)", flags=re.IGNORECASE)


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword_pattern(keyword).search(text)]


def parse_json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def alias_hits(text: str, aliases: list[str]) -> list[str]:
    return [
        alias
        for alias in aliases
        if re.search(
            rf"(?<!\w){re.escape(alias)}(?!\w)",
            text,
            flags=re.IGNORECASE,
        )
    ]


def normalise_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def evidence_sentence_records(article: pd.Series) -> list[dict[str, Any]]:
    """Return de-duplicated, paragraph-aware sentences in source order."""
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    position = 0
    for source in ("headline", "trail_text", "body_text"):
        paragraphs = [
            value
            for value in re.split(r"(?:\r?\n\s*)+|§+", str(article[source]))
            if normalise_text(value)
        ]
        for paragraph_position, paragraph in enumerate(paragraphs):
            parts = re.split(
                r"(?<=[.!?])\s+|(?<=[.!?][\"'”’])\s+",
                paragraph,
            )
            for sentence_position, part in enumerate(parts):
                sentence = normalise_text(part)
                key = sentence.casefold()
                if not sentence or key in seen:
                    continue
                output.append(
                    {
                        "evidence_source": source,
                        "paragraph_position": paragraph_position,
                        "sentence_position": sentence_position,
                        "evidence_position": position,
                        "evidence_sentence": sentence,
                    }
                )
                seen.add(key)
                position += 1
    return output


def evidence_sentences(article: pd.Series) -> list[tuple[str, int, str]]:
    """Return the legacy tuple view of paragraph-aware sentence records."""
    return [
        (
            str(record["evidence_source"]),
            int(record["evidence_position"]),
            str(record["evidence_sentence"]),
        )
        for record in evidence_sentence_records(article)
    ]


def evidence_units(
    article: pd.Series,
    maximum_sentences: int,
    maximum_characters: int,
) -> list[dict[str, Any]]:
    """Create single-sentence and adjacent-sentence units within one paragraph."""
    records = evidence_sentence_records(article)
    units: list[dict[str, Any]] = []
    maximum_sentences = max(1, maximum_sentences)
    for start, first in enumerate(records):
        group: list[dict[str, Any]] = []
        for offset in range(maximum_sentences):
            index = start + offset
            if index >= len(records):
                break
            current = records[index]
            if (
                current["evidence_source"] != first["evidence_source"]
                or current["paragraph_position"] != first["paragraph_position"]
            ):
                break
            group.append(current)
            text = normalise_text(
                " ".join(str(item["evidence_sentence"]) for item in group)
            )
            if len(group) > 1 and len(text) > maximum_characters:
                break
            units.append(
                {
                    "evidence_sentence": text,
                    "evidence_source": first["evidence_source"],
                    "evidence_position": first["evidence_position"],
                    "paragraph_position": first["paragraph_position"],
                    "sentence_positions": tuple(
                        int(item["evidence_position"]) for item in group
                    ),
                    "sentences": tuple(
                        str(item["evidence_sentence"]) for item in group
                    ),
                    "event_granularity": (
                        "evidence_sentence"
                        if len(group) == 1
                        else "evidence_sentence_window"
                    ),
                }
            )
    return units


def all_event_keywords(event_config: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for event_type in event_config["event_type_priority"]:
        values.extend(
            str(keyword)
            for keyword in event_config["event_keyword_rules"].get(event_type, [])
        )
    return list(dict.fromkeys(values))


def has_anaphoric_subject(text: str) -> bool:
    """Return whether a sentence explicitly continues the preceding subject."""
    return bool(
        re.match(
            r"^\W*(?:it|its|they|their|the company|the firm|the group|"
            r"the business|the chipmaker|the manufacturer|the developer|"
            r"this company|this firm)\b",
            normalise_text(text),
            flags=re.IGNORECASE,
        )
    )


def event_span_candidates(
    article: pd.Series,
    link: pd.Series,
    event_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Select the strongest company-bearing evidence spans for one article link."""
    core_aliases = parse_json_list(link["matched_core_aliases"])
    product_aliases = parse_json_list(link["matched_product_aliases"])
    aliases = list(dict.fromkeys(core_aliases + product_aliases))
    keywords = all_event_keywords(event_config)
    source_weights = {
        "headline": int(event_config.get("headline_keyword_weight", 3)),
        "trail_text": int(event_config.get("trail_keyword_weight", 2)),
        "body_text": int(event_config.get("body_keyword_weight", 1)),
    }
    maximum_sentences = int(
        event_config.get("maximum_sentences_per_event_span", 2)
    )
    maximum_characters = int(
        event_config.get("event_span_maximum_characters", 700)
    )
    same_sentence_bonus = int(
        event_config.get("same_sentence_selection_bonus", 4)
    )
    adjacent_sentence_bonus = int(
        event_config.get("adjacent_sentence_selection_bonus", 2)
    )
    candidates: list[dict[str, Any]] = []
    for unit in evidence_units(article, maximum_sentences, maximum_characters):
        evidence = str(unit["evidence_sentence"])
        matched_aliases = alias_hits(evidence, aliases)
        if not matched_aliases:
            continue
        matched_keywords = keyword_hits(evidence, keywords)
        same_sentence_keywords = list(
            dict.fromkeys(
                keyword
                for sentence in unit["sentences"]
                if alias_hits(sentence, aliases)
                for keyword in keyword_hits(sentence, keywords)
            )
        )
        if len(unit["sentences"]) > 1 and same_sentence_keywords:
            # A self-contained company/event sentence is a better atomic Event than
            # an overlapping window that merely appends neighbouring context.
            continue
        if len(unit["sentences"]) > 1 and matched_keywords:
            alias_positions = [
                index
                for index, sentence in enumerate(unit["sentences"])
                if alias_hits(sentence, aliases)
            ]
            keyword_positions = [
                index
                for index, sentence in enumerate(unit["sentences"])
                if keyword_hits(sentence, keywords)
            ]
            linked_by_anaphora = any(
                alias_position < keyword_position
                and has_anaphoric_subject(unit["sentences"][keyword_position])
                for alias_position in alias_positions
                for keyword_position in keyword_positions
            )
            if not linked_by_anaphora:
                continue
        proximity_bonus = (
            same_sentence_bonus
            if same_sentence_keywords
            else adjacent_sentence_bonus
            if matched_keywords and len(unit["sentences"]) > 1
            else 0
        )
        candidate = dict(unit)
        candidate.update(
            {
                "matched_aliases": matched_aliases,
                "all_event_keyword_hits": matched_keywords,
                "same_sentence_event_keyword_hits": same_sentence_keywords,
                "selection_score": (
                    10 * bool(matched_keywords)
                    + 2 * min(len(matched_keywords), 3)
                    + proximity_bonus
                    + source_weights.get(str(unit["evidence_source"]), 1)
                    + bool(core_aliases)
                    + parse_bool(link["query_returned"])
                    - (len(unit["sentences"]) - 1)
                ),
            }
        )
        candidates.append(candidate)

    if not candidates:
        return []
    candidates.sort(
        key=lambda row: (
            int(row["selection_score"]),
            bool(row["same_sentence_event_keyword_hits"]),
            -len(row["sentences"]),
            -int(row["evidence_position"]),
        ),
        reverse=True,
    )
    keyword_candidates = [
        row for row in candidates if row["all_event_keyword_hits"]
    ]
    ranked = keyword_candidates or [
        row for row in candidates if len(row["sentences"]) == 1
    ][:1]
    maximum = max(
        1, int(event_config.get("maximum_event_spans_per_article_company", 2))
    )
    selected: list[dict[str, Any]] = []
    occupied_positions: set[int] = set()
    for candidate in ranked:
        positions = set(int(value) for value in candidate["sentence_positions"])
        if positions & occupied_positions:
            continue
        selected.append(candidate)
        occupied_positions.update(positions)
        if len(selected) >= maximum:
            break
    return selected


def relationship_evidence(
    article: pd.Series,
    link: pd.Series,
    primary_keywords: list[str],
    event_config: dict[str, Any],
    base_event_recommended: bool,
    evidence_span: dict[str, Any] | None = None,
) -> dict[str, Any]:
    core_aliases = parse_json_list(link["matched_core_aliases"])
    product_aliases = parse_json_list(link["matched_product_aliases"])
    aliases = list(dict.fromkeys(core_aliases + product_aliases))
    headline_aliases = alias_hits(article["headline"], aliases)
    trail_aliases = alias_hits(article["trail_text"], aliases)

    if evidence_span is None:
        spans = event_span_candidates(article, link, event_config)
        evidence_span = spans[0] if spans else {}
    evidence_sentence = normalise_text(evidence_span.get("evidence_sentence", ""))
    evidence_source = str(evidence_span.get("evidence_source", ""))
    evidence_position = int(evidence_span.get("evidence_position", -1))
    context_keyword_hits = keyword_hits(evidence_sentence, primary_keywords)
    same_sentence_keyword_hits = list(
        dict.fromkeys(
            keyword
            for sentence in evidence_span.get("sentences", (evidence_sentence,))
            if alias_hits(str(sentence), aliases)
            for keyword in keyword_hits(str(sentence), primary_keywords)
        )
    )
    adjacent_keyword_hits = [
        keyword
        for keyword in context_keyword_hits
        if keyword not in same_sentence_keyword_hits
    ]

    weights = event_config.get("relationship_focus_weights", {})
    focus_score = (
        int(weights.get("headline_alias", 4)) * bool(headline_aliases)
        + int(weights.get("trail_alias", 3)) * bool(trail_aliases)
        + int(weights.get("keyword_same_sentence", 3))
        * bool(same_sentence_keyword_hits)
        + int(weights.get("keyword_adjacent_sentence", 2))
        * bool(adjacent_keyword_hits)
        + int(weights.get("query_returned", 1))
        * parse_bool(link["query_returned"])
        + int(weights.get("core_alias_evidence", 1))
        * bool(core_aliases)
    )
    product_only = link["link_status"] == "verified_product"
    threshold = int(
        event_config.get(
            "minimum_product_relationship_score"
            if product_only
            else "minimum_core_relationship_score",
            6 if product_only else 4,
        )
    )
    relationship_recommended = base_event_recommended and focus_score >= threshold
    return {
        "headline_aliases": headline_aliases,
        "trail_aliases": trail_aliases,
        "event_keyword_same_sentence": same_sentence_keyword_hits,
        "evidence_sentence": evidence_sentence,
        "evidence_source": evidence_source,
        "evidence_position": evidence_position,
        "relationship_focus_score": focus_score,
        "relationship_recommended": relationship_recommended,
    }


def normalised_span_key(text: str) -> str:
    return re.sub(r"[^\w]+", " ", normalise_text(text).casefold()).strip()


def stable_event_id(article_id: str, evidence_span: str = "") -> str:
    identity = f"{article_id}\n{normalised_span_key(evidence_span)}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:14].upper()
    return f"EVT_{digest}"


def span_hash(evidence_span: str) -> str:
    return hashlib.sha1(
        normalised_span_key(evidence_span).encode("utf-8")
    ).hexdigest()


def extractive_event_title(evidence_span: str, maximum_chars: int = 180) -> str:
    text = normalise_text(evidence_span)
    if len(text) <= maximum_chars:
        return text
    clipped = text[: max(1, maximum_chars - 1)].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{clipped or text[: maximum_chars - 1]}…"


def json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def json_dict(values: dict[str, int]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {path}. Close this file in Excel or another editor."
        ) from exc


def classify_event(
    article: pd.Series,
    event_config: dict[str, Any],
) -> dict[str, Any]:
    rules = event_config["event_keyword_rules"]
    priorities = list(event_config["event_type_priority"])
    headline_weight = int(event_config.get("headline_keyword_weight", 3))
    trail_weight = int(event_config.get("trail_keyword_weight", 2))
    body_weight = int(event_config.get("body_keyword_weight", 1))
    maximum_body_hits = int(event_config.get("maximum_body_keyword_hits", 3))
    section_hint_weight = int(event_config.get("section_hint_weight", 1))
    section_hints = event_config.get("section_event_type_hints", {})

    scores: dict[str, int] = {}
    matched_by_type: dict[str, list[str]] = {}
    headline_hits_by_type: dict[str, list[str]] = {}
    trail_hits_by_type: dict[str, list[str]] = {}
    for event_type in priorities:
        keywords = [str(value) for value in rules.get(event_type, [])]
        headline_hits = keyword_hits(article["headline"], keywords)
        trail_hits = keyword_hits(article["trail_text"], keywords)
        body_hits = keyword_hits(article["body_text"], keywords)
        score = (
            headline_weight * len(headline_hits)
            + trail_weight * len(trail_hits)
            + body_weight * min(len(body_hits), maximum_body_hits)
        )
        if section_hints.get(article["section_name"]) == event_type:
            score += section_hint_weight
        scores[event_type] = score
        matched_by_type[event_type] = list(
            dict.fromkeys(headline_hits + trail_hits + body_hits)
        )
        headline_hits_by_type[event_type] = headline_hits
        trail_hits_by_type[event_type] = trail_hits

    maximum_score = max(scores.values()) if scores else 0
    tied_types = [
        event_type for event_type in priorities if scores.get(event_type) == maximum_score
    ]
    if maximum_score == 0:
        primary_type = str(event_config.get("default_event_type", "corporate_event"))
        tied_types = [primary_type]
    else:
        primary_type = tied_types[0]

    secondary_types = [
        event_type
        for event_type in priorities
        if event_type != primary_type and scores.get(event_type, 0) > 0
    ]
    high_score = int(event_config.get("high_confidence_score", 7))
    medium_score = int(event_config.get("medium_confidence_score", 4))
    if maximum_score >= high_score:
        confidence = "high"
    elif maximum_score >= medium_score:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "event_type": primary_type,
        "event_score": maximum_score,
        "classification_confidence": confidence,
        "matched_event_keywords": matched_by_type.get(primary_type, []),
        "headline_event_keywords": headline_hits_by_type.get(primary_type, []),
        "trail_event_keywords": trail_hits_by_type.get(primary_type, []),
        "secondary_event_types": secondary_types,
        "event_type_scores": scores,
        "classification_tie": len(tied_types) > 1,
    }


def classify_event_span(
    evidence_span: str,
    section_name: str,
    event_config: dict[str, Any],
) -> dict[str, Any]:
    """Classify an Event from its own evidence, not from the whole article."""
    rules = event_config["event_keyword_rules"]
    priorities = list(event_config["event_type_priority"])
    span_weight = int(event_config.get("event_span_keyword_weight", 3))
    section_hint_weight = int(event_config.get("section_hint_weight", 1))
    section_hints = event_config.get("section_event_type_hints", {})

    scores: dict[str, int] = {}
    matched_by_type: dict[str, list[str]] = {}
    for event_type in priorities:
        keywords = [str(value) for value in rules.get(event_type, [])]
        hits = keyword_hits(evidence_span, keywords)
        score = span_weight * len(hits)
        if section_hints.get(section_name) == event_type:
            score += section_hint_weight
        scores[event_type] = score
        matched_by_type[event_type] = hits

    maximum_score = max(scores.values()) if scores else 0
    tied_types = [
        event_type for event_type in priorities if scores.get(event_type) == maximum_score
    ]
    if maximum_score == 0:
        primary_type = str(event_config.get("default_event_type", "corporate_event"))
        tied_types = [primary_type]
    else:
        primary_type = tied_types[0]

    secondary_types = [
        event_type
        for event_type in priorities
        if event_type != primary_type and scores.get(event_type, 0) > 0
    ]
    high_score = int(event_config.get("high_confidence_score", 7))
    medium_score = int(event_config.get("medium_confidence_score", 4))
    confidence = (
        "high"
        if maximum_score >= high_score
        else "medium"
        if maximum_score >= medium_score
        else "low"
    )
    return {
        "event_type": primary_type,
        "event_score": maximum_score,
        "classification_confidence": confidence,
        "matched_event_keywords": matched_by_type.get(primary_type, []),
        "headline_event_keywords": [],
        "trail_event_keywords": [],
        "secondary_event_types": secondary_types,
        "event_type_scores": scores,
        "classification_tie": len(tied_types) > 1,
    }


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project_root = config_path.parent.parent
    try:
        project = load_yaml(config_path)
        news_config = project["news_data"]
        event_config = project["event_analysis"]
        mode_config = news_config["collection_modes"][args.mode]
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"CONFIGURATION_ERROR: {exc}", file=sys.stderr)
        return 1

    stem = str(mode_config["output_stem"])
    processed_dir = resolve_path(project_root, news_config["processed_output_directory"])
    articles_path = (
        args.articles_csv.resolve()
        if args.articles_csv
        else processed_dir / f"{stem}_articles_clean.csv"
    )
    links_path = (
        args.links_csv.resolve()
        if args.links_csv
        else processed_dir / f"{stem}_article_company_links_clean.csv"
    )
    output_dir = args.output_directory.resolve() if args.output_directory else processed_dir

    try:
        articles = pd.read_csv(articles_path, dtype=str, keep_default_na=False)
        links = pd.read_csv(links_path, dtype=str, keep_default_na=False)
        require_columns(articles, ARTICLE_COLUMNS, str(articles_path))
        require_columns(links, LINK_COLUMNS, str(links_path))
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    if articles["article_id"].duplicated().any():
        print("INPUT_ERROR: duplicate article_id values.", file=sys.stderr)
        return 1
    if links[["article_id", "company_id"]].duplicated().any():
        print("INPUT_ERROR: duplicate article-company links.", file=sys.stderr)
        return 1

    accepted_links = links[links["accepted_for_analysis"].map(parse_bool)].copy()
    accepted_links_by_article = {
        article_id: group.copy()
        for article_id, group in accepted_links.groupby("article_id", sort=False)
    }
    low_relevance_sections = {
        str(value) for value in event_config.get("low_relevance_sections", [])
    }
    minimum_score = int(event_config.get("minimum_recommended_score", 3))
    product_minimum_score = int(
        event_config.get("minimum_product_only_score", minimum_score + 2)
    )
    high_score = int(event_config.get("high_confidence_score", 7))

    event_by_id: dict[str, dict[str, Any]] = {}
    event_link_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    analysis_ready_articles = articles[articles["analysis_ready"].map(parse_bool)].copy()
    for _, article in analysis_ready_articles.iterrows():
        article_id = article["article_id"]
        article_links = accepted_links_by_article.get(article_id)
        if article_links is None or article_links.empty:
            continue

        low_relevance_section = article["section_name"] in low_relevance_sections
        for _, link in article_links.iterrows():
            spans = event_span_candidates(article, link, event_config)
            if not spans:
                continue
            link_confidence = (
                "high" if link["link_status"] == "verified_core" else "medium"
            )
            for span in spans:
                evidence_sentence = span["evidence_sentence"]
                classification = classify_event_span(
                    evidence_sentence,
                    article["section_name"],
                    event_config,
                )
                product_only = link["link_status"] == "verified_product"
                score_threshold = (
                    product_minimum_score if product_only else minimum_score
                )
                base_event_recommended = (
                    classification["event_score"] >= score_threshold
                )
                if (
                    low_relevance_section
                    and classification["event_score"] < high_score
                ):
                    base_event_recommended = False

                event_id = stable_event_id(article_id, evidence_sentence)
                focus = relationship_evidence(
                    article,
                    link,
                    classification["matched_event_keywords"],
                    event_config,
                    base_event_recommended,
                    span,
                )
                link_row = {
                    "event_id": event_id,
                    "article_id": article_id,
                    "company_id": link["company_id"],
                    "company_name": link["company_name"],
                    "relationship_type": "POTENTIALLY_AFFECTS",
                    "link_status": link["link_status"],
                    "link_confidence": link_confidence,
                    "query_returned": parse_bool(link["query_returned"]),
                    "matched_core_aliases": link["matched_core_aliases"],
                    "matched_product_aliases": link["matched_product_aliases"],
                    "event_type": classification["event_type"],
                    "event_score": classification["event_score"],
                    "headline_aliases": json_list(focus["headline_aliases"]),
                    "trail_aliases": json_list(focus["trail_aliases"]),
                    "event_keyword_same_sentence": json_list(
                        focus["event_keyword_same_sentence"]
                    ),
                    "evidence_sentence": evidence_sentence,
                    "evidence_source": focus["evidence_source"],
                    "evidence_position": focus["evidence_position"],
                    "relationship_focus_score": focus[
                        "relationship_focus_score"
                    ],
                    "recommended_for_graph": focus[
                        "relationship_recommended"
                    ],
                }
                pair = (event_id, link["company_id"])
                existing_link = event_link_by_pair.get(pair)
                if (
                    existing_link is None
                    or int(link_row["relationship_focus_score"])
                    > int(existing_link["relationship_focus_score"])
                ):
                    event_link_by_pair[pair] = link_row

                if event_id not in event_by_id:
                    maximum_title_chars = int(
                        event_config.get("event_title_maximum_characters", 180)
                    )
                    event_by_id[event_id] = {
                        "event_id": event_id,
                        "article_id": article_id,
                        "event_date": article["publication_date"][:10],
                        "publication_timestamp": article["publication_date"],
                        "event_title": extractive_event_title(
                            evidence_sentence, maximum_title_chars
                        ),
                        "event_summary": evidence_sentence,
                        "evidence_span": evidence_sentence,
                        "evidence_source": span["evidence_source"],
                        "evidence_position": span["evidence_position"],
                        "event_granularity": span.get(
                            "event_granularity", "evidence_sentence"
                        ),
                        "event_span_hash": span_hash(evidence_sentence),
                        "source_headline": article["headline"],
                        "event_type": classification["event_type"],
                        "event_score": classification["event_score"],
                        "classification_confidence": classification[
                            "classification_confidence"
                        ],
                        "matched_event_keywords": json_list(
                            classification["matched_event_keywords"]
                        ),
                        "headline_event_keywords": json_list([]),
                        "trail_event_keywords": json_list([]),
                        "secondary_event_types": json_list(
                            classification["secondary_event_types"]
                        ),
                        "event_type_scores": json_dict(
                            classification["event_type_scores"]
                        ),
                        "classification_tie": classification[
                            "classification_tie"
                        ],
                        "section_name": article["section_name"],
                        "low_relevance_section": low_relevance_section,
                        "base_event_recommended": base_event_recommended,
                        "web_url": article["web_url"],
                    }

    event_link_rows = list(event_link_by_pair.values())
    link_rows_by_event: dict[str, list[dict[str, Any]]] = {}
    for row in event_link_rows:
        link_rows_by_event.setdefault(row["event_id"], []).append(row)

    event_rows: list[dict[str, Any]] = []
    for event_id, event in event_by_id.items():
        event_links = link_rows_by_event.get(event_id, [])
        company_ids = sorted({row["company_id"] for row in event_links})
        recommended_company_ids = sorted(
            {
                row["company_id"]
                for row in event_links
                if row["recommended_for_graph"]
            }
        )
        all_product_only = bool(event_links) and all(
            row["link_status"] == "verified_product" for row in event_links
        )
        event.update(
            {
                "accepted_company_ids": json_list(company_ids),
                "accepted_company_count": len(company_ids),
                "recommended_company_ids": json_list(recommended_company_ids),
                "recommended_company_count": len(recommended_company_ids),
                "all_links_product_only": all_product_only,
                "recommended_for_graph": (
                    bool(event["base_event_recommended"])
                    and bool(recommended_company_ids)
                ),
            }
        )
        event_rows.append(event)

    event_rows.sort(key=lambda row: (row["publication_timestamp"], row["event_id"]))
    event_link_rows.sort(key=lambda row: (row["event_id"], row["company_id"]))

    type_counts = Counter(row["event_type"] for row in event_rows)
    recommended_counts = Counter(
        row["event_type"] for row in event_rows if row["recommended_for_graph"]
    )
    report_rows: list[dict[str, Any]] = []
    for event_type in event_config["event_type_priority"]:
        report_rows.append(
            {
                "scope": "event_type",
                "event_type": event_type,
                "candidate_events": type_counts.get(event_type, 0),
                "recommended_events": recommended_counts.get(event_type, 0),
                "not_recommended_events": type_counts.get(event_type, 0)
                - recommended_counts.get(event_type, 0),
            }
        )
    report_rows.append(
        {
            "scope": "all_event_types",
            "event_type": "ALL",
            "candidate_events": len(event_rows),
            "recommended_events": sum(
                bool(row["recommended_for_graph"]) for row in event_rows
            ),
            "not_recommended_events": sum(
                not bool(row["recommended_for_graph"]) for row in event_rows
            ),
        }
    )

    event_path = output_dir / f"{stem}_event_candidates.csv"
    event_links_path = output_dir / f"{stem}_event_company_links.csv"
    report_path = output_dir / f"{stem}_event_extraction_report.csv"
    event_fields = [
        "event_id",
        "article_id",
        "event_date",
        "publication_timestamp",
        "event_title",
        "event_summary",
        "evidence_span",
        "evidence_source",
        "evidence_position",
        "event_granularity",
        "event_span_hash",
        "source_headline",
        "event_type",
        "event_score",
        "classification_confidence",
        "matched_event_keywords",
        "headline_event_keywords",
        "trail_event_keywords",
        "secondary_event_types",
        "event_type_scores",
        "classification_tie",
        "section_name",
        "accepted_company_ids",
        "accepted_company_count",
        "recommended_company_ids",
        "recommended_company_count",
        "all_links_product_only",
        "low_relevance_section",
        "base_event_recommended",
        "recommended_for_graph",
        "web_url",
    ]
    link_fields = [
        "event_id",
        "article_id",
        "company_id",
        "company_name",
        "relationship_type",
        "link_status",
        "link_confidence",
        "query_returned",
        "matched_core_aliases",
        "matched_product_aliases",
        "event_type",
        "event_score",
        "headline_aliases",
        "trail_aliases",
        "event_keyword_same_sentence",
        "evidence_sentence",
        "evidence_source",
        "evidence_position",
        "relationship_focus_score",
        "recommended_for_graph",
    ]
    report_fields = [
        "scope",
        "event_type",
        "candidate_events",
        "recommended_events",
        "not_recommended_events",
    ]
    try:
        write_csv(event_path, event_rows, event_fields)
        write_csv(event_links_path, event_link_rows, link_fields)
        write_csv(report_path, report_rows, report_fields)
    except PermissionError as exc:
        print(f"OUTPUT_ERROR: {exc}", file=sys.stderr)
        return 1

    recommended_total = sum(
        bool(row["recommended_for_graph"]) for row in event_rows
    )
    print("Event candidate extraction completed.")
    print(f"Analysis-ready input articles: {len(analysis_ready_articles)}")
    print(f"Event candidates: {len(event_rows)}")
    print(f"Automatically admitted to primary graph: {recommended_total}")
    print(f"Event-company links: {len(event_link_rows)}")
    print(f"Event candidates: {event_path}")
    print(f"Event-company links: {event_links_path}")
    print(f"Extraction report: {report_path}")
    print(
        "Import policy: recommended_for_graph is the automatic quality gate; "
        "evidence and confidence fields remain available for traceability."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
