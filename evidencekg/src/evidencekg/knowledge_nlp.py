"""Local conventional EN/DE NLP: language hints, mention and syntax candidates.

Pinned small spaCy CNN pipelines are not transformers or generative models.
Their output is never treated as a resolved identity, calibrated probability,
or a proven assertion. Deterministic rule extraction remains a separate path.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from functools import lru_cache
from pathlib import Path

from .db import sha

MODELS = {"en": "en_core_web_sm", "de": "de_core_news_sm"}
MAX_DOCUMENT_CHARS = 200_000
MAX_CHUNK_CHARS = 8000
MAX_MENTIONS = 4000
MAX_SENTENCES = 1000
LABELS = {"PERSON": "person", "PER": "person", "ORG": "organisation", "GPE": "place", "LOC": "place"}


@lru_cache(maxsize=1)
def identity():
    packages = {}
    for package in ("spacy", "lingua-language-detector", *MODELS.values()):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "unavailable"
    models = {}
    for language, package in MODELS.items():
        spec = importlib.util.find_spec(package)
        if spec is None or spec.origin is None:
            models[language] = {"status": "unavailable"}
            continue
        root = Path(spec.origin).parent
        # Verify the complete model payload, not only a mutable package version.
        files = {
            str(path.relative_to(root)): sha(path.read_bytes())
            for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
        models[language] = {
            "status": "available",
            "package": package,
            "payload_sha": sha(json.dumps(files, sort_keys=True)),
        }
    return {
        "method": "spacy-small-cnn-and-lingua-ngrams",
        "packages": packages,
        "models": models,
        "calibrated_probability": None,
        "generative_model_calls": 0,
    }


@lru_cache(maxsize=2)
def pipeline(language):
    import spacy

    spacy.require_cpu()
    nlp = spacy.load(MODELS[language])
    configuration = nlp.config.to_str().casefold()
    if nlp.lang != language or any(marker in configuration for marker in ("transformer", "llm")):
        raise ValueError("Only the bundled non-transformer NLP pipelines are permitted")
    return nlp


@lru_cache(maxsize=1)
def detector():
    from lingua import Language, LanguageDetectorBuilder

    return LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.GERMAN).build()


def language_hints(text):
    try:
        values = detector().compute_language_confidence_values(text)
    except ImportError:
        return {"status": "unavailable", "alternatives": []}
    # Parallel floating-point reduction in Lingua varies below machine epsilon.
    # Declare score quantization so immutable outputs reproduce on repeat runs;
    # these remain rounded engine scores, never calibrated probabilities.
    alternatives = sorted(
        [
            {"language": row.language.iso_code_639_1.name.lower(), "raw_score": round(row.value, 8)}
            for row in values
        ],
        key=lambda row: (-row["raw_score"], row["language"]),
    )
    return {
        "status": "model_candidate",
        "alternatives": alternatives,
        "calibrated_probability": None,
        "raw_score_precision_decimals": 8,
        "scope": "relative EN/DE language hints; both language parsers remain eligible",
    }


def analyse(text):
    """Return span candidates with parser disagreement retained, never a winner."""
    models = identity()["models"]
    mentions, sentences, gaps = {}, {}, {}
    if not any(row["status"] == "available" for row in models.values()):
        return {"mentions": [], "sentences": [], "gaps": {"optional_nlp_models_unavailable": 1}}
    truncated = len(text) > MAX_DOCUMENT_CHARS
    if truncated:
        # Explicitly bounded, with the original source retained by the vault.
        text = text[:MAX_DOCUMENT_CHARS]
        gaps["nlp_character_budget"] = 1
    # Chop only at whitespace; boundary-truncated clauses cannot become syntax
    # claims. Their text remains covered by mechanical span observations.
    position = 0
    while position < len(text):
        end = min(len(text), position + MAX_CHUNK_CHARS)
        clipped = end < len(text) or truncated
        if end < len(text):
            cut = text.rfind(" ", position + MAX_CHUNK_CHARS // 2, end)
            if cut > position:
                end = cut
        chunk = text[position:end]
        hints = language_hints(chunk)
        for language in ("en", "de"):
            if models[language]["status"] != "available":
                gaps["nlp_model_unavailable_" + language] = 1
                continue
            parsed = pipeline(language)(chunk)
            for entity in parsed.ents:
                kind = LABELS.get(entity.label_)
                if kind is None:
                    continue
                start, stop = position + entity.start_char, position + entity.end_char
                key = (start, stop, kind)
                if key not in mentions and len(mentions) >= MAX_MENTIONS:
                    gaps["nlp_mention_limit"] = gaps.get("nlp_mention_limit", 0) + 1
                    continue
                entry = mentions.setdefault(
                    key,
                    dict(
                        start=start,
                        end=stop,
                        category="name_candidate",
                        quote=text[start:stop],
                        alternatives=[entity.text],
                        concepts=[],
                        normalization_status="unresolved",
                        entity_type_candidate=kind,
                        identity_status="unresolved",
                        predictions=[],
                    ),
                )
                entry["predictions"].append(
                    {
                        "language": language,
                        "package": MODELS[language],
                        "label": entity.label_,
                        "calibrated_probability": None,
                    }
                )
            for sentence in parsed.sents:
                a, b = position + sentence.start_char, position + sentence.end_char
                if (a, b) not in sentences and len(sentences) >= MAX_SENTENCES:
                    gaps["nlp_sentence_limit"] = gaps.get("nlp_sentence_limit", 0) + 1
                    continue
                # Never reconstruct an assertion from a sentence cut by budget/chunk.
                if (position and sentence.start_char == 0) or (clipped and sentence.end_char == len(chunk)):
                    gaps["nlp_chunk_boundary_sentences"] = gaps.get("nlp_chunk_boundary_sentences", 0) + 1
                    continue
                tokens = [
                    {
                        "text": token.text,
                        "start": position + token.idx,
                        "end": position + token.idx + len(token),
                        "lemma": token.lemma_,
                        "pos": token.pos_,
                        "dependency": token.dep_,
                        "head_start": position + token.head.idx,
                    }
                    for token in sentence
                ]
                entry = sentences.setdefault(
                    (a, b), dict(start=a, end=b, quote=text[a:b], predictions=[], language_hints=hints)
                )
                entry["predictions"].append(
                    {"language": language, "package": MODELS[language], "tokens": tokens}
                )
        position = end
    return {
        "mentions": sorted(
            mentions.values(), key=lambda row: (row["start"], row["end"], row["entity_type_candidate"])
        ),
        "sentences": sorted(sentences.values(), key=lambda row: (row["start"], row["end"])),
        "gaps": gaps,
    }


def observations(text):
    """Name/syntax predictions; never resolve identity or assertion polarity."""
    from .knowledge_language import fold, query_concepts

    result = analyse(text)
    items = result["mentions"]
    for item in items:
        item["concepts"] = ["name_surface:" + fold(item["quote"])]
        item["interpretation_status"] = "model_prediction"
    for sentence in result["sentences"]:
        concepts = query_concepts(sentence["quote"])
        if len(sentence["quote"]) > 2000:
            continue
        syntax = []
        for prediction in sentence["predictions"]:
            tokens = prediction["tokens"]
            for token in tokens:
                if token["pos"] not in {"VERB", "AUX"}:
                    continue
                syntax.append(
                    {
                        "language": prediction["language"],
                        "package": prediction["package"],
                        "predicate_surface": token,
                        "dependents": [t for t in tokens if t["head_start"] == token["start"] and t != token],
                    }
                )
        if not syntax:
            continue
        items.append(
            {
                "start": sentence["start"],
                "end": sentence["end"],
                "quote": sentence["quote"],
                "category": "syntax_candidate",
                "alternatives": [],
                "concepts": concepts,
                "normalization_status": "unresolved",
                "interpretation_status": "model_prediction",
                "polarity": "unresolved",
                "modality": "unresolved",
                "attribution": "unresolved",
                "syntax_candidates": syntax,
                "language_hints": sentence["language_hints"],
                "calibrated_probability": None,
                "usage": "candidate syntax only; inspect full sentence and surrounding source; no assertion inferred",
            }
        )
    return items, result["gaps"]
