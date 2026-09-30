"""Optional, local-only semantic adapters for the isolated accuracy PoC.

No downloads are performed. ``from_local`` accepts an already acquired HF
snapshot and an explicit immutable revision; file hashes identify actual bytes
(the revision is caller-supplied provenance, not remotely attested). Tests with
injected models establish mechanics only, never multilingual retrieval quality.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

DENSE_MODEL_ID = "BAAI/bge-m3"
RERANKER_MODEL_ID = "BAAI/bge-reranker-v2-m3"
MAX_BATCH_SIZE = 64


@dataclass(frozen=True)
class ModelIdentity:
    model_id: str
    revision: str
    files_sha256: dict[str, str]


def snapshot_identity(path: str | Path, model_id: str, revision: str) -> ModelIdentity:
    """Hash every snapshot file, including tokenizer/config files and symlink targets."""
    root = Path(path)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be an explicit 40-character commit SHA")
    if not root.is_dir():
        raise ValueError("local snapshot directory is required")
    hashes = {}
    for file in sorted(root.rglob("*")):
        if file.is_file():
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[file.relative_to(root).as_posix()] = digest.hexdigest()
    if not hashes:
        raise ValueError("snapshot is empty")
    return ModelIdentity(model_id, revision, hashes)


@dataclass(frozen=True)
class Window:
    start: int
    end: int
    text: str


def _ids(tokenizer: Any, text: str, query: str | None = None) -> list[int]:
    args = (text,) if query is None else (query, text)
    return tokenizer(*args, add_special_tokens=True, truncation=False)["input_ids"]


def window_text(text: str, tokenizer: Any, max_tokens: int, query: str | None = None) -> list[Window]:
    """Gap-free Python-character offsets, with every final window re-tokenized.

    Pair windows reserve the complete query and pair special tokens. Tokenizer
    normalization and boundary changes cannot silently truncate source text.
    Whitespace and zero-token spans remain covered in the original string.
    """
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    if query is not None and len(_ids(tokenizer, "", query)) >= max_tokens and text:
        raise ValueError("full query leaves no passage budget")
    if len(_ids(tokenizer, "", query)) > max_tokens:
        raise ValueError("special tokens or full query exceed token budget")
    if not text:
        return [Window(0, 0, "")]
    encoded = tokenizer(text, add_special_tokens=False, truncation=False, return_offsets_mapping=True)
    offsets = encoded.get("offset_mapping")
    if offsets is None:
        raise ValueError("tokenizer must provide character offsets")
    # Token starts partition the entire original string, including whitespace.
    boundaries = sorted({int(start) for start, end in offsets if 0 < start < len(text)} | {len(text)})
    windows = []
    start = 0
    cursor = 0
    available = max_tokens - len(_ids(tokenizer, "", query))
    while start < len(text):
        # At most max_tokens token boundaries per trial; shrink by actual count
        # because subword re-tokenization can add tokens at either boundary.
        stop_index = min(cursor + max(1, available) - 1, len(boundaries) - 1)
        end = boundaries[stop_index]
        while len(_ids(tokenizer, text[start:end], query)) > max_tokens:
            stop_index -= 1
            if stop_index < cursor:
                raise ValueError("one tokenizer span cannot fit without truncation")
            end = boundaries[stop_index]
        windows.append(Window(start, end, text[start:end]))
        start = end
        cursor = stop_index + 1
    return windows


@dataclass(frozen=True)
class PassageEmbeddings:
    vectors: np.ndarray
    windows: tuple[Window, ...]
    passage_indices: tuple[int, ...]


@dataclass(frozen=True)
class RerankScores:
    scores: np.ndarray
    window_scores: tuple[tuple[float, ...], ...]
    windows: tuple[tuple[Window, ...], ...]


class _Adapter:
    def __init__(
        self,
        tokenizer: Any,
        model: Any,
        identity: ModelIdentity,
        *,
        max_tokens: int = 8192,
        batch_size: int = 8,
        device: str = "cpu",
    ):
        import torch

        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= MAX_BATCH_SIZE
        ):
            raise ValueError(f"batch_size must be in 1..{MAX_BATCH_SIZE}")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if device != "cpu" and not re.fullmatch(r"cuda(?::[0-9]+)?", device):
            raise ValueError("device must be cpu or cuda[:index]")
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise ValueError("CUDA requested but unavailable; no silent fallback")
        limits = [
            getattr(tokenizer, "model_max_length", None),
            getattr(getattr(model, "config", None), "max_position_embeddings", None),
        ]
        # XLM-R reserves positions for the padding index (514 positions -> 512
        # tokens). Tokenizer limits are the authoritative input length bound.
        if any(isinstance(limit, int) and max_tokens > limit for limit in limits):
            raise ValueError("max_tokens exceeds tokenizer/model capacity")
        self.tokenizer, self.model, self.identity = tokenizer, model, identity
        self.max_tokens, self.batch_size, self.device = max_tokens, batch_size, device
        self.model.to(device)
        self.model.eval()

    @classmethod
    def from_local(cls, path: str | Path, revision: str, *, model_id: str | None = None, **kwargs: Any):
        from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

        expected = DENSE_MODEL_ID if cls is DenseAdapter else RERANKER_MODEL_ID
        if model_id is not None and model_id != expected:
            raise ValueError(f"this adapter requires {expected}; model substitution is unsupported")
        identity = snapshot_identity(path, expected, revision)
        tokenizer = AutoTokenizer.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False, use_fast=True
        )
        factory = AutoModel if cls is DenseAdapter else AutoModelForSequenceClassification
        model = factory.from_pretrained(
            str(path),
            local_files_only=True,
            trust_remote_code=False,
            dtype="float16" if kwargs.get("device", "cpu").startswith("cuda") else "float32",
        )
        return cls(tokenizer, model, identity, **kwargs)

    def _batches(self, texts: Sequence[str], query: str | None = None):
        import torch

        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            for text in batch:
                if len(_ids(self.tokenizer, text, query)) > self.max_tokens:
                    raise ValueError("input exceeds capacity; truncation is prohibited")
            args = (batch,) if query is None else ([query] * len(batch), batch)
            inputs = self.tokenizer(
                *args, padding=True, truncation=False, return_tensors="pt", add_special_tokens=True
            )
            if inputs["input_ids"].shape[1] > self.max_tokens:
                raise ValueError("batched tokenizer exceeds token budget")
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with torch.inference_mode():
                yield self.model(**inputs)


class DenseAdapter(_Adapter):
    """BGE-M3 dense CLS pooling; sparse and multi-vector modes are not used."""

    def encode_queries(self, queries: Sequence[str], progress=None) -> np.ndarray:
        rows = []
        completed = 0
        if progress is not None:
            progress(indexing_stage="embedding", indexing_completed=0, indexing_total=len(queries))
        for output in self._batches(queries):
            rows.append(output.last_hidden_state[:, 0].float().cpu().numpy())
            completed += len(rows[-1])
            if progress is not None:
                progress(indexing_stage="embedding", indexing_completed=completed, indexing_total=len(queries))
        if not rows:
            return np.empty((0, 0), dtype=np.float32)
        return _normalize(np.concatenate(rows))

    def encode_passages(self, passages: Sequence[str], progress=None) -> PassageEmbeddings:
        windows, indices = [], []
        if progress is not None:
            progress(indexing_stage="windowing", indexed_passages=0, total_passages=len(passages))
        for index, text in enumerate(passages):
            found = window_text(text, self.tokenizer, self.max_tokens)
            windows.extend(found)
            indices.extend([index] * len(found))
            if progress is not None:
                progress(indexing_stage="windowing", indexed_passages=index + 1, total_passages=len(passages))
        vectors = self.encode_queries([window.text for window in windows], progress=progress)
        return PassageEmbeddings(vectors, tuple(windows), tuple(indices))


class CrossEncoderAdapter(_Adapter):
    """Raw BGE pair logits; maximum over all windows per passage (not probability)."""

    def score(self, query: str, passages: Sequence[str]) -> RerankScores:
        if len(_ids(self.tokenizer, "", query)) > self.max_tokens:
            raise ValueError("full query exceeds token budget")
        windows = tuple(tuple(window_text(text, self.tokenizer, self.max_tokens, query)) for text in passages)
        flattened = [window.text for group in windows for window in group]
        values = []
        for output in self._batches(flattened, query):
            logits = output.logits.float().cpu().numpy()
            if logits.ndim != 2 or logits.shape[1] != 1 or not np.isfinite(logits).all():
                raise ValueError("reranker requires one finite logit per pair")
            values.extend(float(value) for value in logits[:, 0])
        grouped, position = [], 0
        for group in windows:
            grouped.append(tuple(values[position : position + len(group)]))
            position += len(group)
        return RerankScores(np.array([max(group) for group in grouped]), tuple(grouped), windows)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    values = np.asarray(vectors, dtype=np.float32)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise ValueError("embeddings must be a finite matrix")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms == 0) or not np.isfinite(norms).all():
        raise ValueError("zero or overflowing embedding norm")
    return values / norms


def exact_cosine_search(query_vector: np.ndarray, matrix: np.ndarray, k: int = 10) -> list[tuple[int, float]]:
    """Exact NumPy cosine ranking, stable input-index tie break, no ANN pruning."""
    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
        raise ValueError("k must be a nonnegative integer")
    query = np.asarray(query_vector)
    corpus = np.asarray(matrix)
    if query.ndim != 1 or corpus.ndim != 2 or corpus.shape[1] != query.shape[0]:
        raise ValueError("query and corpus embedding dimensions must match")
    normalized_query = _normalize(query[None, :])[0]
    scores = _normalize(corpus) @ normalized_query
    order = np.argsort(-scores, kind="stable")[:k]
    return [(int(index), float(scores[index])) for index in order]
