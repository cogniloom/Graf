"""Fetch pinned public weights only; never reads private corpus data."""

import json
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS = {
    "dense": ("BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181"),
    "reranker": ("BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"),
}

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("directory", type=Path)
    args = p.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for name, (model, revision) in MODELS.items():
        target = args.directory / name
        snapshot_download(
            model,
            revision=revision,
            local_dir=target,
            token=False,
            allow_patterns=[
                "config.json",
                "model.safetensors",
                "pytorch_model.bin",
                "tokenizer*",
                "sentencepiece.bpe.model",
                "special_tokens_map.json",
            ],
        )
        (target / "pinned-revision.json").write_text(json.dumps({"model": model, "revision": revision}))
        print(name, revision, "downloaded", flush=True)
