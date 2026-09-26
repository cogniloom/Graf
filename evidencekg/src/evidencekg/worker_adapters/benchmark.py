"""Fresh-context, subscription-only structured calls for private evaluations."""

import json
from pathlib import Path

import jsonschema

from ..db import dump, sha
from .cli import ExistingLawcaseCodex


class SubscriptionEvaluator:
    def __init__(self, project, worker_state, model="gpt-6-astra", effort="medium", timeout=600):
        self.adapter = ExistingLawcaseCodex(project, worker_state, model, effort, timeout)
        self.model, self.effort = model, effort

    @property
    def identity(self):
        return {
            "adapter": "subscription-evaluator-v1",
            "implementation_sha": sha(Path(__file__).read_bytes()),
            "subscription": self.adapter.identity,
        }

    def call(self, stage, payload, schema):
        from ..benchmark import ANSWER_INSTRUCTION, JUDGE_INSTRUCTION

        instructions = {"answer": ANSWER_INSTRUCTION, "judge": JUDGE_INSTRUCTION}
        if stage not in instructions:
            raise ValueError("Unknown benchmark application stage")
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
        name = "benchmark_" + stage
        self.adapter.worker.schemas[name] = provider
        self.adapter.worker.trusted_instructions[name] = instructions[stage]
        result = self.adapter.worker.call(name, payload)
        jsonschema.Draft202012Validator(schema).validate(result)
        return result
