"""Deterministic mechanics only: no actual model downloads or quality claims."""

import hashlib
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import torch

from poc_accuracy.semantic import (
    CrossEncoderAdapter,
    DenseAdapter,
    ModelIdentity,
    exact_cosine_search,
    snapshot_identity,
    window_text,
)


class ToyTokenizer:
    model_max_length = 100

    def __init__(self):
        self.pairs = []

    def __call__(
        self,
        text,
        text_pair=None,
        *,
        add_special_tokens=True,
        truncation=False,
        return_offsets_mapping=False,
        padding=False,
        return_tensors=None,
    ):
        assert truncation is False
        if isinstance(text, list):
            pairs = text_pair if text_pair is not None else [None] * len(text)
            rows = [
                self(item, pair, add_special_tokens=add_special_tokens)["input_ids"]
                for item, pair in zip(text, pairs)
            ]
            width = max(map(len, rows))
            return {
                "input_ids": torch.tensor([row + [0] * (width - len(row)) for row in rows]),
                "attention_mask": torch.tensor([[1] * len(row) + [0] * (width - len(row)) for row in rows]),
            }
        matches = list(re.finditer(r"\S+", text))
        ids = [sum(map(ord, match.group())) % 41 + 4 for match in matches]
        if text_pair is not None:
            self.pairs.append((text, text_pair))
            second = self(text_pair, add_special_tokens=False)["input_ids"]
            ids = [1] + ids + [2, 2] + second + [2] if add_special_tokens else ids + second
        elif add_special_tokens:
            ids = [1] + ids + [2]
        result = {"input_ids": ids}
        if return_offsets_mapping:
            result["offset_mapping"] = [(match.start(), match.end()) for match in matches]
        return result


class ToyModel:
    config = SimpleNamespace(max_position_embeddings=100)

    def __init__(self):
        self.batches = []
        self.device = None
        self.evaluated = False

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        self.evaluated = True

    def __call__(self, input_ids, attention_mask):
        assert not torch.is_grad_enabled()
        self.batches.append(input_ids.shape)
        sums = (input_ids * attention_mask).sum(dim=1).float()
        cls = torch.stack([sums, torch.ones_like(sums)], dim=1)
        hidden = cls[:, None, :].expand(-1, input_ids.shape[1], -1)
        return SimpleNamespace(last_hidden_state=hidden, logits=sums[:, None])


IDENTITY = ModelIdentity("injected-toy-only", "0" * 40, {"toy": "fake"})


class SemanticTests(unittest.TestCase):
    def adapter(self, cls=DenseAdapter, **kwargs):
        return cls(ToyTokenizer(), ToyModel(), IDENTITY, max_tokens=kwargs.pop("max_tokens", 8), **kwargs)

    def test_gap_free_multilingual_whitespace_and_offsets(self):
        for text in ["  Grüezi Zürich café 東京 שלום  \nnext\tline end ", "   \n", "", "token"]:
            tokenizer = ToyTokenizer()
            windows = window_text(text, tokenizer, 4)
            self.assertEqual("".join(window.text for window in windows), text)
            self.assertEqual(windows[0].start, 0)
            self.assertEqual(windows[-1].end, len(text))
            for index, window in enumerate(windows):
                self.assertEqual(text[window.start : window.end], window.text)
                self.assertLessEqual(len(tokenizer(window.text)["input_ids"]), 4)
                if index:
                    self.assertEqual(windows[index - 1].end, window.start)

    def test_full_query_and_pair_special_token_budget(self):
        tokenizer = ToyTokenizer()
        query = "where is Zürich"
        text = "one two three four five six seven"
        windows = window_text(text, tokenizer, 9, query)
        self.assertEqual("".join(window.text for window in windows), text)
        for window in windows:
            self.assertLessEqual(len(tokenizer(query, window.text)["input_ids"]), 9)
        self.assertTrue(all(pair[0] == query for pair in tokenizer.pairs))
        with self.assertRaises(ValueError):
            window_text(text, tokenizer, 7, query)
        with self.assertRaises(ValueError):
            window_text("", tokenizer, 6, query)

    def test_dense_batches_queries_and_passage_mapping(self):
        adapter = self.adapter(batch_size=2, max_tokens=4)
        encoded = adapter.encode_passages(["one two three four five", "sechs sieben"])
        self.assertEqual(encoded.passage_indices, (0, 0, 0, 1))
        np.testing.assert_allclose(np.linalg.norm(encoded.vectors, axis=1), 1, atol=1e-6)
        self.assertTrue(all(shape[0] <= 2 and shape[1] <= 4 for shape in adapter.model.batches))
        self.assertTrue(adapter.model.evaluated)
        self.assertEqual(adapter.model.device, "cpu")
        with self.assertRaises(ValueError):
            adapter.encode_queries(["one two three"])

    def test_cross_encoder_all_windows_raw_scores_and_max(self):
        adapter = self.adapter(CrossEncoderAdapter, batch_size=2)
        query = "find Zürich"
        passages = ["one two three four five six", "another", ""]
        result = adapter.score(query, passages)
        self.assertEqual(len(result.scores), 3)
        self.assertEqual(len(result.window_scores[0]), 3)
        for score, values, windows, original in zip(
            result.scores, result.window_scores, result.windows, passages
        ):
            self.assertEqual(score, max(values))
            self.assertEqual("".join(window.text for window in windows), original)
        self.assertTrue(all(pair[0] == query for pair in adapter.tokenizer.pairs))
        self.assertTrue(all(shape[0] <= 2 and shape[1] <= 8 for shape in adapter.model.batches))
        with self.assertRaises(ValueError):
            adapter.score("one two three four five", ["passage"])
        self.assertEqual(adapter.score("short", []).scores.size, 0)

    def test_exact_cosine_ranking_ties_and_validation(self):
        result = exact_cosine_search(np.array([1, 0]), np.array([[2, 0], [4, 0], [0, 3], [-1, 0]]), 9)
        self.assertEqual([index for index, score in result], [0, 1, 2, 3])
        np.testing.assert_allclose([score for index, score in result], [1, 1, 0, -1])
        for matrix in [np.array([[0, 0]]), np.array([[np.nan, 1]])]:
            with self.assertRaises(ValueError):
                exact_cosine_search(np.array([1, 0]), matrix)
        self.assertEqual(exact_cosine_search(np.array([1, 0]), np.empty((0, 2))), [])
        with self.assertRaises(ValueError):
            exact_cosine_search(np.array([1]), np.ones((2, 2)))

    def test_snapshot_hashes_cover_tokenizer_and_weights(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tokenizer.json").write_text("toy", encoding="utf-8")
            (root / "model.safetensors").write_bytes(b"weights")
            first = snapshot_identity(root, "explicit", "a" * 40)
            self.assertEqual(first.files_sha256["model.safetensors"], hashlib.sha256(b"weights").hexdigest())
            (root / "tokenizer.json").write_text("changed", encoding="utf-8")
            self.assertNotEqual(first, snapshot_identity(root, "explicit", "a" * 40))
            with self.assertRaises(ValueError):
                snapshot_identity(root, "explicit", "main")

    def test_local_loader_rejects_substitution_before_loading(self):
        with self.assertRaises(ValueError):
            DenseAdapter.from_local("/nonexistent", "a" * 40, model_id="substitute")
        with self.assertRaises(ValueError):
            DenseAdapter.from_local("/nonexistent", "main")

    def test_impossible_single_span_fails_without_dropping_characters(self):
        class ExpandingTokenizer(ToyTokenizer):
            def __call__(self, text, *args, **kwargs):
                result = super().__call__(text, *args, **kwargs)
                if text and kwargs.get("add_special_tokens", True):
                    result["input_ids"] = [1] * 20
                return result

        with self.assertRaises(ValueError):
            window_text("one", ExpandingTokenizer(), 4)

    def test_bad_capacity_batch_device_and_logits_fail(self):
        for batch in [0, 65, True]:
            with self.assertRaises(ValueError):
                self.adapter(batch_size=batch)
        with self.assertRaises(ValueError):
            self.adapter(max_tokens=101)
        with self.assertRaises(ValueError):
            self.adapter(device="mps")

        class BadModel(ToyModel):
            def __call__(self, **inputs):
                result = super().__call__(**inputs)
                result.logits = torch.ones((len(inputs["input_ids"]), 2))
                return result

        adapter = CrossEncoderAdapter(ToyTokenizer(), BadModel(), IDENTITY, max_tokens=8)
        with self.assertRaises(ValueError):
            adapter.score("query", ["text"])


if __name__ == "__main__":
    unittest.main()
