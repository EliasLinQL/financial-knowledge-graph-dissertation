"""Enrich rule-generated event candidates with local zero-shot NLI inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml


ARTICLE_COLUMNS = {
    "article_id",
    "headline",
    "trail_text",
    "body_text",
}
EVENT_COLUMNS = {
    "event_id",
    "article_id",
    "event_type",
    "event_score",
    "recommended_for_graph",
}
LINK_COLUMNS = {
    "event_id",
    "article_id",
    "company_id",
    "company_name",
    "matched_core_aliases",
    "matched_product_aliases",
    "evidence_sentence",
    "relationship_focus_score",
    "recommended_for_graph",
}

EVENT_LABELS = {
    "corporate_event": (
        "a company-specific corporate event such as earnings, an acquisition, "
        "investment, a product launch, a partnership, layoffs, an executive "
        "change, an outage or a data breach"
    ),
    "regulatory_event": (
        "a regulatory or legal event such as an investigation, lawsuit, court "
        "decision, antitrust action, tax, tariff, fine, ban or compliance change"
    ),
    "geopolitical_event": (
        "a geopolitical event such as war, sanctions, military conflict, an "
        "election, diplomacy, an invasion or a ceasefire"
    ),
    "macroeconomic_event": (
        "a macroeconomic event such as inflation, interest rates, central-bank "
        "policy, GDP, unemployment, recession or fiscal policy"
    ),
    "commodity_event": (
        "a commodity event involving oil, natural gas, energy, gold, copper or "
        "a material supply disruption"
    ),
    "market_wide_event": (
        "a broad financial-market event such as a market rally, sell-off or "
        "large movement across a major stock index"
    ),
}

RELATIONSHIP_LABELS = {
    "direct_subject": "the named company is a direct participant or actor in the event",
    "direct_target": "the named company is a direct target of the event",
    "materially_affected": "the event is likely to materially affect the named company",
    "market_context": "the company is mentioned only as stock-market context",
    "incidental_mention": "the company is mentioned incidentally or only in a list",
    "unrelated": "the text does not establish a meaningful relationship to the company",
}


@dataclass(frozen=True)
class Prediction:
    label: str
    score: float
    scores: dict[str, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use a local zero-shot NLI model to semantically validate event types, "
            "event-company relationships and evidence sentences."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the project YAML configuration.",
    )
    parser.add_argument(
        "--mode", choices=("test", "full"), default="full"
    )
    parser.add_argument("--articles-csv", type=Path)
    parser.add_argument("--events-csv", type=Path)
    parser.add_argument("--event-links-csv", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore saved model predictions and recompute every NLP result.",
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


def parse_json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def normalise_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def sentence_split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", str(text))
    return [normalise_text(part) for part in parts if normalise_text(part)]


def alias_in_text(text: str, aliases: Iterable[str]) -> bool:
    for alias in aliases:
        if re.search(
            rf"(?<!\w){re.escape(alias)}(?!\w)",
            text,
            flags=re.IGNORECASE,
        ):
            return True
    return False


def unique_texts(values: Iterable[str], maximum: int, maximum_chars: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalise_text(value)[:maximum_chars]
        key = text.casefold()
        if not text or key in seen:
            continue
        output.append(text)
        seen.add(key)
        if len(output) >= maximum:
            break
    return output


def event_text(article: pd.Series, maximum_chars: int) -> str:
    parts = [
        f"Headline: {normalise_text(article['headline'])}",
        f"Summary: {normalise_text(article['trail_text'])}",
        f"Article context: {normalise_text(article['body_text'])}",
    ]
    return "\n".join(parts)[:maximum_chars]


def evidence_candidates(
    article: pd.Series,
    link: pd.Series,
    maximum: int,
    maximum_chars: int,
) -> list[str]:
    aliases = list(
        dict.fromkeys(
            parse_json_list(link["matched_core_aliases"])
            + parse_json_list(link["matched_product_aliases"])
            + [normalise_text(link["company_name"])]
        )
    )
    values: list[str] = [normalise_text(link["evidence_sentence"])]
    for field in ("headline", "trail_text"):
        text = normalise_text(article[field])
        if text and alias_in_text(text, aliases):
            values.append(text)
    for sentence in sentence_split(article["body_text"]):
        if alias_in_text(sentence, aliases):
            values.append(sentence)
        if len(values) >= maximum * 2:
            break
    candidates = unique_texts(values, maximum, maximum_chars)
    if not candidates:
        candidates = [normalise_text(link["evidence_sentence"]) or normalise_text(article["headline"])]
    return candidates


def prediction_cache_key(
    model_name: str,
    task: str,
    text: str,
    labels: dict[str, str],
    hypothesis_template: str,
) -> str:
    payload = json.dumps(
        {
            "model": model_name,
            "task": task,
            "text": text,
            "labels": labels,
            "hypothesis_template": hypothesis_template,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_compute_device(device: int) -> tuple[str, str]:
    """Validate the configured Transformers device and describe the runtime."""
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch is not installed. Run pip install -r requirements.txt."
        ) from exc

    torch_version = str(torch.__version__)
    if device == -1:
        return "cpu", torch_version
    if device < -1:
        raise ValueError(
            f"Invalid NLP device {device}. Use -1 for CPU or 0+ for a CUDA GPU."
        )
    if not torch.cuda.is_available():
        raise RuntimeError(
            f"NLP device {device} requires CUDA, but PyTorch {torch_version} "
            "cannot access a CUDA GPU. Install requirements-gpu.txt or set "
            "nlp_enrichment.device to -1 for an explicit CPU run."
        )
    device_count = int(torch.cuda.device_count())
    if device >= device_count:
        raise RuntimeError(
            f"NLP device cuda:{device} was requested, but only {device_count} "
            "CUDA device(s) are available."
        )
    return f"cuda:{device} ({torch.cuda.get_device_name(device)})", torch_version


class CachedZeroShotClassifier:
    def __init__(
        self,
        model_name: str,
        model_revision: str,
        cache_directory: Path,
        prediction_cache_path: Path,
        device: int,
        batch_size: int,
        refresh_cache: bool,
    ) -> None:
        try:
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
                pipeline,
            )
        except ImportError as exc:
            raise RuntimeError(
                "transformers is not installed. Run pip install -r requirements.txt."
            ) from exc

        self.compute_device, self.torch_version = resolve_compute_device(device)
        self.model_name = model_name
        self.batch_size = batch_size
        self.prediction_cache_path = prediction_cache_path
        self.cache: dict[str, dict[str, Any]] = {}
        if prediction_cache_path.exists() and not refresh_cache:
            try:
                loaded = json.loads(prediction_cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.cache = loaded
            except json.JSONDecodeError:
                self.cache = {}

        cache_directory.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        print(
            f"NLP compute device: {self.compute_device}; "
            f"PyTorch {self.torch_version}; batch_size={self.batch_size}"
        )
        print(f"Loading local NLP model: {model_name} ({model_revision})")
        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            revision=model_revision,
            cache_dir=str(cache_directory),
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            model_name,
            revision=model_revision,
            cache_dir=str(cache_directory),
        )
        self.pipeline = pipeline(
            "zero-shot-classification",
            model=model,
            tokenizer=tokenizer,
            device=device,
        )
        config = getattr(getattr(self.pipeline, "model", None), "config", None)
        self.resolved_revision = str(
            getattr(config, "_commit_hash", "") or model_revision
        )

    def _save_cache(self) -> None:
        self.prediction_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.prediction_cache_path.write_text(
            json.dumps(self.cache, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def predict_many(
        self,
        task: str,
        texts: list[str],
        labels: dict[str, str],
        hypothesis_template: str,
    ) -> list[Prediction]:
        descriptions = list(labels.values())
        description_to_key = {description: key for key, description in labels.items()}
        keys = [
            prediction_cache_key(
                self.model_name,
                task,
                text,
                labels,
                hypothesis_template,
            )
            for text in texts
        ]
        uncached: dict[str, str] = {}
        for key, text in zip(keys, texts, strict=True):
            if key not in self.cache:
                uncached[key] = text

        if uncached:
            print(
                f"  {task}: inferring {len(uncached)} uncached texts "
                f"({len(texts) - len(uncached)} cached)"
            )
            uncached_keys = list(uncached)
            results = self.pipeline(
                [uncached[key] for key in uncached_keys],
                candidate_labels=descriptions,
                hypothesis_template=hypothesis_template,
                multi_label=False,
                batch_size=self.batch_size,
                truncation=True,
            )
            if isinstance(results, dict):
                results = [results]
            for key, result in zip(uncached_keys, results, strict=True):
                mapped_scores = {
                    description_to_key[str(label)]: float(score)
                    for label, score in zip(
                        result.get("labels", []),
                        result.get("scores", []),
                        strict=True,
                    )
                }
                ordered = sorted(mapped_scores.items(), key=lambda item: item[1], reverse=True)
                if not ordered:
                    raise RuntimeError(f"NLP model returned no labels for task {task}.")
                self.cache[key] = {
                    "label": ordered[0][0],
                    "score": ordered[0][1],
                    "scores": mapped_scores,
                }
            self._save_cache()

        return [
            Prediction(
                label=str(self.cache[key]["label"]),
                score=float(self.cache[key]["score"]),
                scores={
                    str(label): float(score)
                    for label, score in self.cache[key]["scores"].items()
                },
            )
            for key in keys
        ]


def positive_probability(prediction: Prediction, accepted_labels: set[str]) -> float:
    return sum(prediction.scores.get(label, 0.0) for label in accepted_labels)


def choose_relation_prediction(
    candidates: list[str],
    predictions: list[Prediction],
    accepted_labels: set[str],
) -> tuple[str, Prediction, float]:
    if len(candidates) != len(predictions):
        raise ValueError("Evidence candidates and predictions must have equal length.")
    ranked = sorted(
        zip(candidates, predictions, strict=True),
        key=lambda pair: (
            positive_probability(pair[1], accepted_labels),
            pair[1].score,
        ),
        reverse=True,
    )
    sentence, prediction = ranked[0]
    return sentence, prediction, positive_probability(prediction, accepted_labels)


def decide_hybrid_relationship(
    rule_recommended: bool,
    focus_score: int,
    event_prediction: Prediction,
    relation_prediction: Prediction,
    positive_probability_value: float,
    config: dict[str, Any],
) -> tuple[bool, str]:
    accepted_labels = {
        str(value) for value in config.get("accepted_relationship_labels", [])
    }
    positive_top_label = relation_prediction.label in accepted_labels
    confirmation_threshold = float(config.get("confirmation_threshold", 0.35))
    rescue_threshold = float(config.get("rescue_threshold", 0.65))
    event_threshold = float(config.get("event_confidence_threshold", 0.35))
    strong_rule_score = int(config.get("strong_rule_focus_score", 7))
    rescue_focus_score = int(config.get("minimum_rescue_focus_score", 2))

    if rule_recommended:
        if positive_top_label and relation_prediction.score >= confirmation_threshold:
            return True, "rule_and_nlp_agree"
        if focus_score >= strong_rule_score and relation_prediction.label != "unrelated":
            return True, "strong_rule_fallback"
        return False, "nlp_rejected_rule_candidate"

    if not bool(config.get("enable_nlp_rescue", False)):
        return False, "rule_and_nlp_do_not_support"
    if (
        positive_top_label
        and relation_prediction.score >= rescue_threshold
        and positive_probability_value >= rescue_threshold
        and event_prediction.score >= event_threshold
        and focus_score >= rescue_focus_score
    ):
        return True, "nlp_rescued_rule_candidate"
    return False, "rule_and_nlp_do_not_support"


def json_scores(prediction: Prediction) -> str:
    return json.dumps(prediction.scores, ensure_ascii=False, sort_keys=True)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {path}. Close the file in Excel or another editor."
        ) from exc


def run() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    project = load_yaml(config_path)
    project_root = config_path.parent.parent
    news_config = project["news_data"]
    mode_config = news_config["collection_modes"][args.mode]
    nlp_config = project.get("nlp_enrichment", {})
    if not isinstance(nlp_config, dict) or not nlp_config.get("enabled", True):
        raise ValueError("nlp_enrichment must be enabled in config.yaml.")

    stem = str(mode_config["output_stem"])
    processed_directory = resolve_path(
        project_root, news_config["processed_output_directory"]
    )
    output_directory = args.output_directory.resolve() if args.output_directory else processed_directory
    article_path = args.articles_csv.resolve() if args.articles_csv else processed_directory / f"{stem}_articles_clean.csv"
    event_path = args.events_csv.resolve() if args.events_csv else processed_directory / f"{stem}_event_candidates.csv"
    link_path = args.event_links_csv.resolve() if args.event_links_csv else processed_directory / f"{stem}_event_company_links.csv"

    articles = pd.read_csv(article_path, dtype=str, keep_default_na=False)
    events = pd.read_csv(event_path, dtype=str, keep_default_na=False)
    links = pd.read_csv(link_path, dtype=str, keep_default_na=False)
    require_columns(articles, ARTICLE_COLUMNS, str(article_path))
    require_columns(events, EVENT_COLUMNS, str(event_path))
    require_columns(links, LINK_COLUMNS, str(link_path))
    if articles["article_id"].duplicated().any():
        raise ValueError("Duplicate article_id values in clean article data.")
    if events["event_id"].duplicated().any():
        raise ValueError("Duplicate event_id values in event candidate data.")
    if links[["event_id", "company_id"]].duplicated().any():
        raise ValueError("Duplicate event-company pairs in event link data.")

    article_map = articles.set_index("article_id").to_dict(orient="index")
    missing_articles = sorted(set(events["article_id"]) - set(article_map))
    if missing_articles:
        raise ValueError("NLP events reference missing articles: " + ", ".join(missing_articles[:10]))

    model_name = str(nlp_config.get("model_name", "cross-encoder/nli-deberta-v3-xsmall"))
    model_revision = str(nlp_config.get("model_revision", "main"))
    cache_directory = resolve_path(
        project_root, nlp_config.get("model_cache_directory", "data/models/huggingface")
    )
    prediction_cache_path = resolve_path(
        project_root,
        nlp_config.get(
            "prediction_cache_file", "data/news/processed/nlp_prediction_cache.json"
        ),
    )
    classifier = CachedZeroShotClassifier(
        model_name=model_name,
        model_revision=model_revision,
        cache_directory=cache_directory,
        prediction_cache_path=prediction_cache_path,
        device=int(nlp_config.get("device", -1)),
        batch_size=int(nlp_config.get("batch_size", 8)),
        refresh_cache=args.refresh_cache,
    )

    event_text_limit = int(nlp_config.get("event_text_maximum_characters", 1800))
    event_texts = [
        event_text(pd.Series(article_map[row["article_id"]]), event_text_limit)
        for _, row in events.iterrows()
    ]
    event_predictions = classifier.predict_many(
        "event_type",
        event_texts,
        EVENT_LABELS,
        "This news describes {}.",
    )
    event_prediction_by_id = {
        row["event_id"]: prediction
        for (_, row), prediction in zip(
            events.iterrows(), event_predictions, strict=True
        )
    }

    maximum_evidence_sentences = int(
        nlp_config.get("maximum_evidence_sentences_per_relationship", 3)
    )
    evidence_maximum_characters = int(
        nlp_config.get("evidence_maximum_characters", 700)
    )
    relationship_texts: list[str] = []
    relationship_ranges: list[tuple[int, int, list[str]]] = []
    for _, link in links.iterrows():
        article = pd.Series(article_map[link["article_id"]])
        candidates = evidence_candidates(
            article,
            link,
            maximum_evidence_sentences,
            evidence_maximum_characters,
        )
        start = len(relationship_texts)
        relationship_texts.extend(
            f"Company under assessment: {normalise_text(link['company_name'])}.\nSentence: {sentence}"
            for sentence in candidates
        )
        relationship_ranges.append((start, len(relationship_texts), candidates))

    relationship_predictions = classifier.predict_many(
        "event_company_relationship",
        relationship_texts,
        RELATIONSHIP_LABELS,
        "This text shows that {}.",
    )
    accepted_relationship_labels = {
        str(value)
        for value in nlp_config.get(
            "accepted_relationship_labels",
            ["direct_subject", "direct_target", "materially_affected"],
        )
    }

    event_override_threshold = float(
        nlp_config.get("event_type_override_threshold", 0.45)
    )
    final_event_type_by_id: dict[str, str] = {}
    for _, event in events.iterrows():
        prediction = event_prediction_by_id[event["event_id"]]
        final_event_type_by_id[event["event_id"]] = (
            prediction.label
            if bool(nlp_config.get("enable_event_type_override", False))
            and prediction.score >= event_override_threshold
            else event["event_type"]
        )

    enriched_links: list[dict[str, Any]] = []
    for position, (_, link) in enumerate(links.iterrows()):
        start, end, candidates = relationship_ranges[position]
        evidence, relationship_prediction, positive_score = choose_relation_prediction(
            candidates,
            relationship_predictions[start:end],
            accepted_relationship_labels,
        )
        event_prediction = event_prediction_by_id[link["event_id"]]
        rule_recommended = parse_bool(link["recommended_for_graph"])
        focus_score = int(float(link["relationship_focus_score"] or 0))
        final_recommended, reason = decide_hybrid_relationship(
            rule_recommended,
            focus_score,
            event_prediction,
            relationship_prediction,
            positive_score,
            nlp_config,
        )
        row = link.to_dict()
        row.update(
            {
                "rule_event_type": link.get("event_type", ""),
                "rule_recommended_for_graph": rule_recommended,
                "event_type": final_event_type_by_id[link["event_id"]],
                "nlp_relationship_label": relationship_prediction.label,
                "nlp_relationship_score": relationship_prediction.score,
                "nlp_positive_probability": positive_score,
                "nlp_relationship_scores": json_scores(relationship_prediction),
                "nlp_evidence_sentence": evidence,
                "nlp_model_name": model_name,
                "nlp_model_revision": classifier.resolved_revision,
                "hybrid_decision_reason": reason,
                "recommended_for_graph": final_recommended,
            }
        )
        enriched_links.append(row)

    recommended_company_ids_by_event: dict[str, list[str]] = {}
    for row in enriched_links:
        if parse_bool(row["recommended_for_graph"]):
            recommended_company_ids_by_event.setdefault(row["event_id"], []).append(
                row["company_id"]
            )

    enriched_events: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        prediction = event_prediction_by_id[event["event_id"]]
        recommended_company_ids = sorted(
            recommended_company_ids_by_event.get(event["event_id"], [])
        )
        row = event.to_dict()
        row.update(
            {
                "rule_event_type": event["event_type"],
                "rule_recommended_for_graph": parse_bool(
                    event["recommended_for_graph"]
                ),
                "nlp_event_type": prediction.label,
                "nlp_event_score": prediction.score,
                "nlp_event_scores": json_scores(prediction),
                "event_type": final_event_type_by_id[event["event_id"]],
                "recommended_company_ids": json.dumps(
                    recommended_company_ids, ensure_ascii=False
                ),
                "recommended_company_count": len(recommended_company_ids),
                "nlp_model_name": model_name,
                "nlp_model_revision": classifier.resolved_revision,
                "hybrid_decision_reason": (
                    "has_hybrid_supported_relationship"
                    if recommended_company_ids
                    else "no_hybrid_supported_relationship"
                ),
                "recommended_for_graph": bool(recommended_company_ids),
            }
        )
        enriched_events.append(row)

    event_output = output_directory / f"{stem}_event_candidates_nlp.csv"
    link_output = output_directory / f"{stem}_event_company_links_nlp.csv"
    report_output = output_directory / f"{stem}_nlp_enrichment_report.csv"
    enriched_event_frame = pd.DataFrame(enriched_events)
    enriched_link_frame = pd.DataFrame(enriched_links)

    reason_counts = enriched_link_frame["hybrid_decision_reason"].value_counts()
    label_counts = enriched_link_frame["nlp_relationship_label"].value_counts()
    report_rows: list[dict[str, Any]] = [
        {
            "section": "runtime",
            "metric": "compute_device",
            "value": classifier.compute_device,
            "details": (
                f"PyTorch {classifier.torch_version}; "
                f"batch_size={classifier.batch_size}"
            ),
        },
        {
            "section": "summary",
            "metric": "source_events",
            "value": len(events),
            "details": str(event_path),
        },
        {
            "section": "summary",
            "metric": "rule_recommended_events",
            "value": int(events["recommended_for_graph"].map(parse_bool).sum()),
            "details": "Rule-only baseline",
        },
        {
            "section": "summary",
            "metric": "hybrid_recommended_events",
            "value": int(enriched_event_frame["recommended_for_graph"].map(parse_bool).sum()),
            "details": "Events with at least one hybrid-supported relationship",
        },
        {
            "section": "summary",
            "metric": "event_type_changes",
            "value": int((enriched_event_frame["event_type"] != enriched_event_frame["rule_event_type"]).sum()),
            "details": "NLP confidence exceeded the configured override threshold",
        },
        {
            "section": "summary",
            "metric": "source_event_company_links",
            "value": len(links),
            "details": str(link_path),
        },
        {
            "section": "summary",
            "metric": "rule_recommended_event_company_links",
            "value": int(links["recommended_for_graph"].map(parse_bool).sum()),
            "details": "Rule-only baseline",
        },
        {
            "section": "summary",
            "metric": "hybrid_recommended_event_company_links",
            "value": int(enriched_link_frame["recommended_for_graph"].map(parse_bool).sum()),
            "details": "Final automatic graph relationships",
        },
    ]
    report_rows.extend(
        {
            "section": "decision_reason",
            "metric": reason,
            "value": int(count),
            "details": "Hybrid relationship decision",
        }
        for reason, count in reason_counts.items()
    )
    report_rows.extend(
        {
            "section": "relationship_label",
            "metric": label,
            "value": int(count),
            "details": "Top zero-shot NLI relationship label",
        }
        for label, count in label_counts.items()
    )

    write_csv(enriched_event_frame, event_output)
    write_csv(enriched_link_frame, link_output)
    write_csv(pd.DataFrame(report_rows), report_output)
    print("NLP event enrichment completed.")
    print(
        "Rule -> hybrid recommended events: "
        f"{int(events['recommended_for_graph'].map(parse_bool).sum())} -> "
        f"{int(enriched_event_frame['recommended_for_graph'].map(parse_bool).sum())}"
    )
    print(
        "Rule -> hybrid recommended relationships: "
        f"{int(links['recommended_for_graph'].map(parse_bool).sum())} -> "
        f"{int(enriched_link_frame['recommended_for_graph'].map(parse_bool).sum())}"
    )
    print(f"NLP events: {event_output}")
    print(f"NLP relationships: {link_output}")
    print(f"NLP report: {report_output}")
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
        print(f"NLP_ENRICHMENT_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
