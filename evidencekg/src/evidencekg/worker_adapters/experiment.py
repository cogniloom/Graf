"""Explicit opt-in subscription transport for every frozen experiment arm.

Construction performs the existing subscription-login preflight. Calls use that
adapter's ephemeral fresh context, clean environment, tool-activity rejection,
and durable raw receipts; there is no provider API, billing or model fallback.
"""

from pathlib import Path

from ..db import sha
from ..experiments import ANSWER_INSTRUCTION, JUDGE_INSTRUCTION
from .cli import ExistingLawcaseCodex


class SubscriptionExperimentWorker:
    def __init__(self, project, worker_state, *, role, model="gpt-6-astra", effort="medium", timeout=600):
        if role not in {"answer", "judge"}:
            raise ValueError("Explicit answer or judge role required")
        self.role = role
        self.adapter = ExistingLawcaseCodex(project, worker_state, model, effort, timeout)
        self.model, self.effort = model, effort

    @property
    def identity(self):
        return {
            "adapter": "subscription-experiment-citation-ids-v1",
            "role": self.role,
            "implementation_sha": sha(Path(__file__).read_bytes()),
            "subscription": self.adapter.identity,
        }

    def call(self, stage, payload, schema):
        if stage != self.role:
            raise ValueError("Separate answer/judge adapter instances required")
        # Provider subset adaptation affects uniqueItems only; harness retains the
        # exact local schema and raw choices, and validates before resolving refs.
        import json

        provider = json.loads(json.dumps(schema))

        def adapt(node):
            if isinstance(node, dict):
                node.pop("uniqueItems", None)
                for value in node.values():
                    adapt(value)
            elif isinstance(node, list):
                for value in node:
                    adapt(value)

        adapt(provider)
        name = "frozen_experiment_" + stage
        worker = self.adapter.worker
        worker.schemas[name] = provider
        worker.trusted_instructions[name] = {"answer": ANSWER_INSTRUCTION, "judge": JUDGE_INSTRUCTION}[stage]
        try:
            return worker.call(name, payload)
        except Exception as exc:
            # Only the transport knows which receipt belongs to this invocation.
            # Never discover receipts by scanning shared subscription state.
            directory = getattr(exc, "call_directory", None)
            receipts = []
            if directory is not None:
                folder = Path(directory)
                response = folder / "response.json"
                receipts.append(
                    {
                        "directory": str(folder),
                        "response": response.read_text(errors="replace") if response.exists() else None,
                    }
                )
            exc.raw_output = {"subscription_receipts": receipts}
            raise
