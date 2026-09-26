"""Explicit subscription-only transport for accuracy retrieval stages."""

import json
from pathlib import Path

from ..db import dump, sha
from .cli import ExistingLawcaseCodex


class SubscriptionAccuracyWorker:
    def __init__(self, project, worker_state, *, model="gpt-6-astra", effort="medium", timeout=600):
        self.adapter = ExistingLawcaseCodex(project, worker_state, model, effort, timeout)
        self.model, self.effort = model, effort

    @property
    def identity(self):
        return {
            "adapter": "subscription-accuracy-v1",
            "implementation_sha": sha(Path(__file__).read_bytes()),
            "subscription": self.adapter.identity,
        }

    def call(self, stage, payload, schema, instruction):
        if stage not in {"accuracy_plan", "accuracy_assess"}:
            raise ValueError("Unsupported accuracy stage")
        provider = json.loads(dump(schema))

        def adapt(node):
            if isinstance(node, dict):
                node.pop("uniqueItems", None)
                for value in node.values():
                    adapt(value)
            elif isinstance(node, list):
                for value in node:
                    adapt(value)

        adapt(provider)
        worker = self.adapter.worker
        worker.schemas[stage] = provider
        worker.trusted_instructions[stage] = instruction
        try:
            return worker.call(stage, payload)
        except Exception as exc:
            directory = getattr(exc, "call_directory", None)
            if directory:
                response = Path(directory) / "response.json"
                exc.raw_output = {
                    "directory": str(directory),
                    "response": response.read_text(errors="replace") if response.exists() else None,
                }
            raise
