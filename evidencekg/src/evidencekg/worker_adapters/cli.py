import json
import math
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from ..db import dump, sha
from ..validation import RESULT_SCHEMA, STAGE_SCHEMAS, schema_for_payload


class WorkerOutputError(ValueError):
    """Rejected worker output remains available for the queue's attempt artifact."""

    def __init__(self, message, raw_output):
        super().__init__(message)
        self.raw_output = raw_output


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-JSON constant: " + value)


class CLIWorker:
    """Administrator-configured JSON stdin/stdout executable, never supplied by MCP/source text."""

    def __init__(self, argv, model, timeout=300):
        if (
            not isinstance(argv, (list, tuple))
            or not argv
            or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)
            or not isinstance(model, str)
            or not model.strip()
        ):
            raise ValueError("Explicit executable argv and model identity required")
        executable = shutil.which(argv[0])
        if not executable:
            raise ValueError("Administrator executable unavailable")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Finite positive worker timeout required")
        self.argv = [str(Path(executable).resolve()), *argv[1:]]
        self.model, self.timeout = model, timeout
        self.effort = "administrator-configured"

    @property
    def identity(self):
        argument_files = {}
        for arg in self.argv[1:]:
            try:
                path = Path(arg)
                if path.is_file():
                    argument_files[str(path.resolve())] = sha(path.read_bytes())
            except (OSError, ValueError):
                # Inline program/configuration arguments need not be filesystem paths.
                continue
        return {
            "adapter": "administrator-cli-v2",
            "argv": self.argv,
            "executable_sha": sha(Path(self.argv[0]).read_bytes()),
            "argument_files": argument_files,
        }

    def call(self, task):
        envelope = {**task, "result_schema": schema_for_payload(task.get("payload", {}))}
        # No automatic credential/model/billing fallback. Administrator argv is the only command source.
        env = {
            k: v
            for k, v in os.environ.items()
            if not (
                "API_KEY" in k.upper()
                or k.upper()
                in {
                    "OPENAI_BASE_URL",
                    "OPENAI_API_BASE",
                    "CODEX_ACCESS_TOKEN",
                    "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_BASE_URL",
                    "AZURE_OPENAI_ENDPOINT",
                }
            )
        }
        with tempfile.TemporaryDirectory(prefix="evidencekg-worker-") as cwd:
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                proc = subprocess.Popen(
                    self.argv,
                    stdin=subprocess.PIPE,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=cwd,
                    env=env,
                    start_new_session=True,
                )
                try:
                    proc.communicate(dump(envelope).encode(), timeout=self.timeout)
                except BaseException as exc:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    stdout.seek(0)
                    raw = stdout.read(1_000_000)
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        exc.raw_output = raw
                        raise
                    raise WorkerOutputError("Worker interrupted: " + str(exc), raw) from exc
                stdout.seek(0)
                output = stdout.read(1_000_001)
                if proc.returncode or len(output) > 1_000_000:
                    size = os.fstat(stdout.fileno()).st_size
                    raise WorkerOutputError(
                        f"Worker failed (exit {proc.returncode}) or exceeded output budget; stdout bytes={size}, retained={len(output)}",
                        output,
                    )
                try:
                    return json.loads(
                        output, object_pairs_hook=_strict_object, parse_constant=_invalid_constant
                    )
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise WorkerOutputError("Malformed worker JSON: " + str(exc), output) from exc


class ExistingLawcaseCodex:
    """Use the project's existing hardened subscription adapter, without copying credentials."""

    def __init__(self, project, worker_state, model="gpt-6-astra", effort="medium", timeout=300):
        import sys

        self.project = Path(project).resolve()
        if not (self.project / "lawcase_worker.py").is_file():
            raise ValueError("Existing lawyer adapter unavailable")
        if effort not in {"low", "medium", "high"}:
            raise ValueError("Explicit low, medium or high effort required")
        # Explicit administrator installation path, never source-controlled tool arguments.
        sys.path.insert(0, str(self.project))
        import lawcase_worker

        if Path(lawcase_worker.__file__).resolve() != self.project / "lawcase_worker.py":
            raise ValueError("Loaded lawcase adapter does not match administrator installation")

        # Provider structured-output schemas are a subset of JSON Schema. Keep the
        # complete local validator authoritative; only omit unsupported provider hints.
        provider_schemas = json.loads(dump({"evidence": RESULT_SCHEMA, **STAGE_SCHEMAS}))

        def adapt(node):
            if isinstance(node, dict):
                node.pop("uniqueItems", None)
                for value in node.values():
                    adapt(value)
            elif isinstance(node, list):
                for value in node:
                    adapt(value)

        adapt(provider_schemas)
        self.provider_schemas = json.loads(dump(provider_schemas))
        from ..assurance import INSTRUCTIONS as ASSURANCE_INSTRUCTIONS
        from ..review_queue import INSTRUCTIONS

        trusted = {"evidence": INSTRUCTIONS}
        trusted.update({kind: INSTRUCTIONS + ASSURANCE_INSTRUCTIONS for kind in STAGE_SCHEMAS})
        self.trusted_instructions = dict(trusted)
        self.worker = lawcase_worker.CodexWorker(
            Path(worker_state),
            provider_schemas,
            model=model,
            effort=effort,
            timeout=timeout,
            trusted_instructions=trusted,
        )
        self.model, self.effort = model, effort

    @property
    def identity(self):
        from .. import citation_choices

        return {
            "adapter": "existing-lawcase-codex-v5",
            "implementation_sha": sha(Path(__file__).read_bytes()),
            "citation_choices_sha": sha(Path(citation_choices.__file__).read_bytes()),
            "project": str(self.project),
            "adapter_sha": sha((self.project / "lawcase_worker.py").read_bytes()),
            "corpus_sha": sha((self.project / "corpus.py").read_bytes()),
            "executable": str(self.worker.executable),
            "cli_version": self.worker.version,
        }

    def call(self, task):
        payload = task.get("payload", {})
        stage = payload["kind"] if payload.get("assurance_version") else "evidence"
        schema = json.loads(dump(self.provider_schemas[stage]))
        vocabulary = {
            "task_id": [task["task_id"]] if task.get("task_id") else [],
            "input_sha": [task["input_sha"]],
            "segment_id": sorted({s["id"] for s in payload.get("segments", [])}),
            "extraction_id": sorted({s["extraction_id"] for s in payload.get("segments", [])}),
            "target_id": [t["id"] for t in payload.get("descriptor", {}).get("targets", [])],
            "marker_id": [m["id"] for m in payload.get("descriptor", {}).get("risk_markers", [])],
        }

        def bind(node):
            if isinstance(node, dict):
                for name, prop in node.get("properties", {}).items():
                    if vocabulary.get(name):
                        prop["enum"] = vocabulary[name]
                for child in node.values():
                    bind(child)
            elif isinstance(node, list):
                for child in node:
                    bind(child)

        bind(schema)
        from ..citation_choices import choices, selection_schema

        catalog = (
            choices(payload)
            if payload.get("citation_selection_version") == "immutable-citation-choice-v2"
            else {}
        )
        use_choices = bool(catalog)
        self.worker.trusted_instructions[stage] = self.trusted_instructions[stage]
        if use_choices:
            schema = selection_schema(schema, catalog)
            self.worker.trusted_instructions[stage] += (
                " Return citations only as citation_id selections from citation_spans or citation_extra_spans, "
                "as required by the output schema. The service resolves each selected ID to its immutable "
                "original quote and offsets. Choose all spans needed to support each claim and qualifier."
            )
        if (
            payload.get("max_input_bytes")
            and len(dump(task).encode()) + len(dump(schema).encode()) + 2048 > payload["max_input_bytes"]
        ):
            raise ValueError("Bound provider input exceeds frozen input budget")
        self.worker.schemas[stage] = schema
        raw = self.worker.call(stage, {**task, "input_sha": task["input_sha"]})
        if use_choices:
            from ..citation_choices import SelectedCitationResult, resolve

            try:
                return SelectedCitationResult(resolve(raw, payload), raw)
            except (ValueError, KeyError, TypeError) as exc:
                raise WorkerOutputError(str(exc), dump(raw)) from exc
        return raw
