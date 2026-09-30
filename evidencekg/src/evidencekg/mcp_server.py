"""Official MCP Python SDK v2. stdio only; no unauthenticated network listener."""

import atexit
import threading
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .db import Store
from .retrieval import API
from .review_queue import Queue


def create_server(state, discovery_index=None, *, backend="hybrid", hybrid_config=None):
    if backend not in {"hybrid", "mechanical"}:
        raise ValueError("Unknown discovery backend")
    if backend == "hybrid" and discovery_index is not None:
        raise ValueError("Attributed LLM indexes require explicit mechanical backend")
    server = MCPServer("Evidence Graph")
    runtime = None
    runtime_lock = threading.RLock()

    def hybrid():
        nonlocal runtime
        with runtime_lock:
            if runtime is None:
                from .hybrid.runtime import Runtime
                runtime = Runtime(state, hybrid_config)
                atexit.register(runtime.close)
            return runtime

    @server.tool(structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False))
    def discovery_workset(workset_id: str, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Continue a persisted candidate queue. Ranking never marks candidates reviewed."""
        return hybrid().page(workset_id, cursor, limit)


    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def segment_locators(
        snapshot_id: str,
        segment_id: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Enumerate complete source locator metadata independently of passage text."""
        return access(
            "segment_locators", snapshot_id=snapshot_id, segment_id=segment_id, cursor=cursor, limit=limit
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def duplicate_aliases(
        snapshot_id: str,
        segment_id: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Enumerate complete exact-text/context aliases from a discovery preview."""
        from .ranking import RankedDiscovery

        store = Store(state)
        try:
            aliases = RankedDiscovery(store, snapshot_id).duplicate_aliases(segment_id)
            return API(store).page(
                snapshot_id,
                {"kind": "duplicate_aliases", "segment_id": segment_id},
                aliases,
                cursor,
                limit,
                key=lambda row: row["segment_id"],
            )
        finally:
            store.close()

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=(backend == "mechanical"), destructive_hint=False, open_world_hint=False),
    )
    def discover(snapshot_id: str, question: str, limit: int = 12) -> dict[str, Any]:
        """Discover original evidence using local embeddings, reranking and typed links.

        No generative or remote model calls. Persists ranked candidate queues/cache;
        canonical source evidence is unchanged. Returns explicit bounded coverage.
        """
        if backend == "hybrid":
            return hybrid().retrieve(question, limit=limit, snapshot=snapshot_id)
        from .ranking import RankedDiscovery

        store = Store(state)
        try:
            observations = None
            if discovery_index is not None:
                from .discovery_index import load_observations

                observations, identity = load_observations(discovery_index)
                if identity["snapshot_id"] != snapshot_id:
                    raise ValueError("Discovery index belongs to a different snapshot")
            return RankedDiscovery(store, snapshot_id, observations=observations).retrieve(
                question, limit=limit
            )
        finally:
            store.close()

    # Each call owns its connection. SQLite and the process lock serialize writes.
    def access(method, **kwargs):
        store = Store(state)
        try:
            service = API(store) if hasattr(API, method) else Queue(store)
            return getattr(service, method)(**kwargs)
        finally:
            store.close()

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def knowledge_query(
        snapshot_id: str,
        kind: str = "claim",
        entity: str | None = None,
        predicate: str | None = None,
        applicable_on: str | None = None,
        valid_at: str | None = None,
        group_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
        concept: str | None = None,
        text: str | None = None,
        segment_id: str | None = None,
    ) -> dict[str, Any]:
        """Read automatic non-LLM knowledge with original quotations and uncertainty.

        Kinds: claim, evidence_set, observation, concept, entity, identity, uncertainty,
        conflict, dependence, source, edge. Enumerate entity
        first or use discover's knowledge_context.entities (e.g. order:1847).
        Then query claim by entity WITHOUT polarity filtering to retain conditions,
        planned/negative claims and possible contrary evidence. Predicates:
        approval, cancellation, payment, delivery, rejection, validity, supersession,
        dependency. applicable_on matches explicit event days and excludes unknown
        dates. valid_at matches stated validity intervals, retaining uncertain end
        boundaries. Neither filter establishes current real-world authority.
        group_id retrieves members of a conflict or shared-source signal. Every
        next_cursor must be consumed for all stored matches. Literal entity matches
        do not prove identity; no score is a probability of truth. Inspect sources.
        Old snapshots require a new ingest to gain the automatic knowledge layer.
        English/German and mixed-language terms share canonical concepts.
        For complete entity context, use kind="evidence_set" and entity: this retains
        negative, qualified, planned, other-day and unknown-date claims with role labels,
        even when applicable_on or valid_at is supplied. Explicit incoming supersession
        statements are included. Only entity, predicate and requested dates
        are allowed in this mode. Page through all results before summarizing.
        Use observation kind with concept="month:3" or text="März"/"March". Text matches
        any recognized vocabulary concept, not arbitrary translated meaning.
        Observations preserve ambiguous date/amount alternatives. segment_id
        enumerates all claim/observation/uncertainty annotations for a passage.
        With edge kind, segment_id enumerates its incident relationships and
        incoming edges sharing their target nodes, with related source locators.
        Identity comparisons are reversible, non-transitive hypotheses. Uncertainty
        nodes locate unclear audio without releasing withheld transcript guesses.
        """
        return access(
            "knowledge_query",
            snapshot_id=snapshot_id,
            kind=kind,
            entity=entity,
            predicate=predicate,
            applicable_on=applicable_on,
            valid_at=valid_at,
            group_id=group_id,
            cursor=cursor,
            limit=limit,
            concept=concept,
            text=text,
            segment_id=segment_id,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def inventory(
        snapshot_id: str | None = None, cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Enumerate all captured source entries, including gaps, with snapshot-bound cursors."""
        return access("inventory", snapshot_id=snapshot_id, cursor=cursor, limit=limit)

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def document_details(
        snapshot_id: str,
        document_version_id: str,
        kind: str = "warnings",
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Enumerate complete extraction warnings or preserved artifacts for a source."""
        return access(
            "document_details",
            snapshot_id=snapshot_id,
            document_version_id=document_version_id,
            kind=kind,
            cursor=cursor,
            limit=limit,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def sections(
        snapshot_id: str, document_version_id: str, cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Enumerate immutable primary segments of a source version."""
        return access(
            "sections",
            snapshot_id=snapshot_id,
            document_version_id=document_version_id,
            cursor=cursor,
            limit=limit,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def read_segments(
        snapshot_id: str, segment_ids: list[str], max_output_bytes: int = 100000
    ) -> dict[str, Any]:
        """Read exact preserved segment text; oversized requests return remaining IDs."""
        return access(
            "read_segments",
            snapshot_id=snapshot_id,
            segment_ids=segment_ids,
            max_output_bytes=max_output_bytes,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def read_original_region(snapshot_id: str, document_version_id: str, locator: dict) -> dict[str, Any]:
        """Read bounded original bytes or named preserved rendered artifacts; never arbitrary paths."""
        return access(
            "read_original_region",
            snapshot_id=snapshot_id,
            document_version_id=document_version_id,
            locator=locator,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def search(
        snapshot_id: str, query: str, mode: str = "lexical", cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Search Unicode FTS phrase/BM25 or verified literal text, with complete pagination."""
        return access("search", snapshot_id=snapshot_id, query=query, mode=mode, cursor=cursor, limit=limit)

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def neighbours(
        snapshot_id: str,
        node_id: str,
        relation_types: list[str] | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Enumerate typed membership and explicit-link observations, not semantic truth."""
        return access(
            "neighbours",
            snapshot_id=snapshot_id,
            node_id=node_id,
            relation_types=relation_types,
            cursor=cursor,
            limit=limit,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def explain_connection(
        snapshot_id: str,
        left_document_id: str,
        right_document_id: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return reproducible shared-key paths and explicit links, never invented relationships."""
        return access(
            "explain_connection",
            snapshot_id=snapshot_id,
            left_document_id=left_document_id,
            right_document_id=right_document_id,
            cursor=cursor,
            limit=limit,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def related(
        snapshot_id: str, document_version_id: str, cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Rank typed observable reasons: explicit links first, distinctive keys next, weak surfaces last."""
        return access(
            "related",
            snapshot_id=snapshot_id,
            document_version_id=document_version_id,
            cursor=cursor,
            limit=limit,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def start_review(
        snapshot_id: str, question: str, scope: str = "all", enhanced: bool = False
    ) -> dict[str, Any]:
        """Schedule complete source/graph scope; enhanced adds blind wording and semantic checks."""
        return access(
            "start_review", snapshot_id=snapshot_id, question=question, scope=scope, enhanced=enhanced
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def next_review_task(run_id: str, worker_id: str) -> dict[str, Any]:
        """Lease the next durable scheduled task. The model cannot choose its scope."""
        return access("next_review_task", run_id=run_id, worker_id=worker_id) or {"task": None}

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def submit_review_result(
        run_id: str, task_id: str, lease_id: str, input_sha: str, result: dict
    ) -> dict[str, Any]:
        """Validate identity, lease, exact quotations and schema before accepting once."""
        return access(
            "submit_review_result",
            run_id=run_id,
            task_id=task_id,
            lease_id=lease_id,
            input_sha=input_sha,
            result=result,
        )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def review_status(run_id: str) -> dict[str, Any]:
        """Software coverage counts, failures and unresolved matters; never comprehension."""
        return access("review_status", run_id=run_id)

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    def review_evidence(run_id: str, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Read attributed observations, candidate paths and human flags; cursor rejects changed run views."""
        from .review_views import review_evidence as read

        store = Store(state)
        try:
            return read(store, run_id, cursor, limit)
        finally:
            store.close()

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def store_interpretation(
        run_id: str, evidence_refs: list[dict], relationship: str, explanation: str, attribution: str
    ) -> dict[str, Any]:
        """Append an attributed unreviewed interpretation; cannot modify canonical evidence."""
        return access(
            "store_interpretation",
            run_id=run_id,
            evidence_refs=evidence_refs,
            relationship=relationship,
            explanation=explanation,
            attribution=attribution,
        )

    return server
