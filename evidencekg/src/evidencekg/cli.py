import argparse
import json
import os
from pathlib import Path

from . import reports
from .config import configure, initialize
from .db import Store
from .ingest import ingest
from .retrieval import API
from .review_queue import Queue


def parser():
    p = argparse.ArgumentParser(description="Local immutable evidence graph and exhaustive review")
    p.add_argument("--state", type=Path, default=Path(".evidencekg"))
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("root", type=Path)
    init.add_argument("--config", type=Path, help="Trusted administrator JSON parser/rule settings")
    config = commands.add_parser("configure")
    config.add_argument("settings", type=Path)
    commands.add_parser("ingest").add_argument("--reextract", action="store_true")
    commands.add_parser("verify")
    listing = commands.add_parser("inventory")
    listing.add_argument("--snapshot")
    listing.add_argument("--cursor")
    listing.add_argument("--limit", type=int, default=100)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--mode", choices=["lexical", "literal"], default="lexical")
    search.add_argument("--snapshot")
    search.add_argument("--cursor")
    search.add_argument("--limit", type=int, default=100)
    discover = commands.add_parser(
        "discover", help="Bounded ranked evidence preview with typed graph reasons"
    )
    discover.add_argument("question")
    discover.add_argument("--backend", choices=["hybrid", "mechanical"], default="hybrid")
    discover.add_argument("--hybrid-config", type=Path)
    discover.add_argument("--snapshot")
    discover.add_argument("--limit", type=int, default=12)
    discover.add_argument("--discovery-index", type=Path, help="Optional previously built attributed index")
    prepare = commands.add_parser("prepare-hybrid", help="Import verified snapshot into PostgreSQL and build local dense index")
    prepare.add_argument("--models", type=Path, required=True)
    prepare.add_argument("--database-config", type=Path, required=True)
    prepare.add_argument("--snapshot")
    prepare.add_argument("--device", default="cuda")
    prepare.add_argument("--output", type=Path)
    workset = commands.add_parser("discovery-workset", help="Continue a retained hybrid candidate queue")
    workset.add_argument("workset_id")
    workset.add_argument("--cursor")
    workset.add_argument("--limit", type=int, default=100)
    workset.add_argument("--hybrid-config", type=Path)
    accuracy = commands.add_parser(
        "discover-accuracy", help="Explicit subscription model planning and original-passage relevance review"
    )
    accuracy.add_argument("--question-file", type=Path, required=True)
    accuracy.add_argument("--snapshot")
    accuracy.add_argument("--limit", type=int, default=12)
    accuracy.add_argument("--max-candidates", type=int, default=80)
    accuracy.add_argument("--document-limit", type=int, default=16)
    accuracy.add_argument("--scope-documents", type=Path, help="JSON list of frozen document IDs")
    accuracy.add_argument("--output", type=Path, required=True, help="Durable local model-call receipts")
    accuracy.add_argument("--lawcase-project", type=Path, default=Path(__file__).resolve().parents[3])
    accuracy.add_argument("--lawcase-worker-state", type=Path, required=True)
    aliases = commands.add_parser("duplicate-aliases")
    aliases.add_argument("segment_id")
    aliases.add_argument("--snapshot")
    aliases.add_argument("--cursor")
    aliases.add_argument("--limit", type=int, default=100)
    locators = commands.add_parser("segment-locators")
    locators.add_argument("segment_id")
    locators.add_argument("--snapshot")
    locators.add_argument("--cursor")
    locators.add_argument("--limit", type=int, default=100)
    index = commands.add_parser(
        "index-discovery", help="Explicit opt-in to attributed bilingual model indexing"
    )
    index.add_argument("--snapshot")
    index.add_argument("--output", type=Path, required=True)
    index.add_argument("--lawcase-project", type=Path, default=Path(__file__).resolve().parents[3])
    index.add_argument("--lawcase-worker-state", type=Path, required=True)
    index.add_argument("--partition", type=int, default=0)
    index.add_argument("--partitions", type=int, choices=[1, 2], default=1)
    index.add_argument("--prepare-only", action="store_true")
    serve = commands.add_parser("serve")
    serve.add_argument("--backend", choices=["hybrid", "mechanical"], default="hybrid")
    serve.add_argument("--hybrid-config", type=Path)
    serve.add_argument("--transport", choices=["stdio"], default="stdio")
    serve.add_argument(
        "--discovery-index", type=Path, help="Administrator-selected completed index; no model calls"
    )
    for name in ("review", "resume"):
        review = commands.add_parser(name)
        if name == "review":
            review.add_argument("--question-file", type=Path, required=True)
            review.add_argument("--exhaustive", action="store_true")
            review.add_argument("--snapshot")
            review.add_argument("--max-rounds", type=int, default=2)
            review.add_argument(
                "--enhanced",
                action=argparse.BooleanOptionalAction,
                default=True,
                help="Structured observations, blind wording review and premise challenges (default on)",
            )
        else:
            review.add_argument("run_id")
        review.add_argument("--schedule-only", action="store_true")
        review.add_argument(
            "--worker-command", help="JSON argv array for a trusted JSON stdin/stdout executable"
        )
        review.add_argument(
            "--lawcase-worker-state",
            type=Path,
            help="Existing dedicated subscription-authenticated Codex worker state",
        )
        review.add_argument("--lawcase-project", type=Path, default=Path(__file__).resolve().parents[3])
        review.add_argument("--model", default="gpt-6-astra")
        review.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
        review.add_argument("--max-calls", type=int, default=0)
    status = commands.add_parser("status")
    status.add_argument("run_id", nargs="?")
    export = commands.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("--output", type=Path, required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("root", type=Path)
    sample.add_argument("--output", type=Path, required=True)
    sample.add_argument("--seed", default="2026-09-25")
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--cases", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--snapshot")
    benchmark.add_argument("--enhanced-run-id")
    benchmark.add_argument("--limit", type=int, default=6)
    benchmark.add_argument("--seed", default="2026-09-25")
    benchmark.add_argument("--lawcase-project", type=Path, default=Path(__file__).resolve().parents[3])
    benchmark.add_argument("--lawcase-worker-state", type=Path, required=True)
    benchmark.add_argument("--model", default="gpt-6-astra")
    benchmark.add_argument("--effort", choices=["low", "medium", "high"], default="medium")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    return p


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == "sample":
            from .evaluation import sample_corpus

            sampled = sample_corpus(args.root, args.output, args.seed)
            result = {
                "output": str(args.output.resolve()),
                "selected": len(sampled["selected"]),
                "population_known": sampled["population_known"],
                "inventory_complete": sampled["inventory_complete"],
            }
        elif args.command == "init":
            store = initialize(
                args.state, args.root, json.loads(args.config.read_text()) if args.config else None
            )
            result = {"state": str(store.state), "root": str(args.root.resolve())}
        elif args.command == "restore":
            result = reports.restore(args.source, args.destination)
        elif args.command == "serve":
            from .mcp_server import create_server

            create_server(args.state, discovery_index=args.discovery_index, backend=args.backend, hybrid_config=args.hybrid_config).run(transport=args.transport)
            return 0
        elif args.command == "prepare-hybrid":
            from .hybrid.runtime import prepare
            result = prepare(args.state, args.models, args.database_config, snapshot=args.snapshot,
                             device=args.device, output=args.output)
        elif args.command == "discovery-workset" or (args.command == "discover" and args.backend == "hybrid"):
            from .hybrid.runtime import Runtime
            if getattr(args, "discovery_index", None):
                raise ValueError("Attributed LLM indexes require explicit --backend mechanical")
            runtime = Runtime(args.state, args.hybrid_config)
            try:
                result = (runtime.page(args.workset_id, args.cursor, args.limit)
                          if args.command == "discovery-workset" else
                          runtime.retrieve(args.question, limit=args.limit, snapshot=args.snapshot))
            finally:
                runtime.close()
        else:
            store = Store(args.state)
            api, queue = API(store), Queue(store)
            if args.command == "configure":
                result = configure(store, json.loads(args.settings.read_text()))
            elif args.command == "benchmark":
                from .benchmark import run_benchmark
                from .worker_adapters.benchmark import SubscriptionEvaluator

                worker = SubscriptionEvaluator(
                    args.lawcase_project, args.lawcase_worker_state, args.model, args.effort
                )
                judge = SubscriptionEvaluator(
                    args.lawcase_project, args.lawcase_worker_state, args.model, args.effort
                )
                result = run_benchmark(
                    store,
                    args.cases,
                    worker,
                    args.output,
                    enhanced_run_id=args.enhanced_run_id,
                    limit=args.limit,
                    seed=args.seed,
                    snapshot_id=args.snapshot,
                    judge_worker=judge,
                )
            elif args.command == "ingest":
                result = {"snapshot_id": ingest(store, reextract=args.reextract)}
            elif args.command == "verify":
                result = reports.verify(store)
            elif args.command == "inventory":
                result = api.inventory(args.snapshot, args.cursor, args.limit)
            elif args.command == "search":
                result = api.search(
                    store.snapshot(args.snapshot)["id"], args.query, args.mode, args.cursor, args.limit
                )
            elif args.command == "discover":
                from .ranking import RankedDiscovery

                observations = None
                if args.discovery_index:
                    from .discovery_index import load_observations

                    observations, identity = load_observations(args.discovery_index)
                    if identity["snapshot_id"] != store.snapshot(args.snapshot)["id"]:
                        raise ValueError("Discovery index belongs to a different snapshot")
                result = RankedDiscovery(
                    store, store.snapshot(args.snapshot)["id"], observations=observations
                ).retrieve(args.question, limit=args.limit)
            elif args.command == "discover-accuracy":
                from .accuracy import AccuracyDiscovery, DurableCalls
                from .accuracy_candidates import CandidateCollector
                from .worker_adapters.accuracy import SubscriptionAccuracyWorker

                corpus_root = Path(store.one("SELECT root FROM corpora")["root"]).resolve()
                if args.output.resolve().is_relative_to(corpus_root):
                    raise ValueError("Model receipts must stay outside the source corpus")
                worker = SubscriptionAccuracyWorker(args.lawcase_project, args.lawcase_worker_state)
                discovery = AccuracyDiscovery(
                    CandidateCollector(store, store.snapshot(args.snapshot)["id"]),
                    DurableCalls(args.output, worker),
                    max_candidates=args.max_candidates,
                    document_limit=args.document_limit,
                )
                scope = json.loads(args.scope_documents.read_text()) if args.scope_documents else None
                result = discovery.retrieve(
                    args.question_file.read_text(), limit=args.limit, scope_document_ids=scope
                )
            elif args.command == "duplicate-aliases":
                from .ranking import RankedDiscovery

                snapshot = store.snapshot(args.snapshot)["id"]
                aliases = RankedDiscovery(store, snapshot).duplicate_aliases(args.segment_id)
                result = api.page(
                    snapshot,
                    {"kind": "duplicate_aliases", "segment_id": args.segment_id},
                    aliases,
                    args.cursor,
                    args.limit,
                    key=lambda row: row["segment_id"],
                )
            elif args.command == "segment-locators":
                result = api.segment_locators(
                    store.snapshot(args.snapshot)["id"],
                    args.segment_id,
                    args.cursor,
                    args.limit,
                )
            elif args.command == "index-discovery":
                from .discovery_index import (
                    SubscriptionDiscoveryWorker,
                    build_index,
                    freeze_index,
                    index_status,
                )

                worker = SubscriptionDiscoveryWorker(args.lawcase_project, args.lawcase_worker_state)
                index = freeze_index(
                    store,
                    store.snapshot(args.snapshot)["id"],
                    args.output,
                    worker,
                    partition_count=args.partitions,
                )
                if not args.prepare_only:
                    build_index(index, partition_index=args.partition)
                result = index_status(index)
            elif args.command == "status":
                result = queue.review_status(args.run_id) if args.run_id else api.inventory(limit=100)
            elif args.command == "export":
                result = {"output": reports.export(store, args.run_id, args.output)}
            elif args.command == "backup":
                result = reports.backup(store, args.destination)
            elif args.command in ("review", "resume"):
                if args.max_calls < 0:
                    raise ValueError("max-calls must be nonnegative")
                if args.command == "review":
                    snapshot = store.snapshot(args.snapshot)["id"]
                    result = queue.start_review(
                        snapshot,
                        args.question_file.read_text(),
                        exhaustive=args.exhaustive,
                        max_rounds=args.max_rounds,
                        model=args.model,
                        enhanced=args.enhanced,
                    )
                    run_id = result["run_id"]
                else:
                    run_id = args.run_id
                    result = queue.review_status(run_id)
                if not args.schedule_only:
                    from .runner import run
                    from .worker_adapters.cli import CLIWorker, ExistingLawcaseCodex

                    if args.worker_command and args.lawcase_worker_state:
                        raise ValueError("Choose one worker adapter")
                    if args.worker_command:
                        worker = CLIWorker(json.loads(args.worker_command), args.model)
                    elif args.lawcase_worker_state:
                        worker = ExistingLawcaseCodex(
                            args.lawcase_project, args.lawcase_worker_state, args.model, args.effort
                        )
                    else:
                        raise ValueError(
                            "Queue saved. Select --worker-command or --lawcase-worker-state; no automatic model or billing configuration"
                        )
                    result = run(store, run_id, worker, args.max_calls)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("ok") is False:
            return 1
        if result.get("state") in ("paused", "scheduled_work_complete_unresolved", "failed_unresolved"):
            return 2
        return 0
    except (ValueError, OSError) as exc:
        import sys

        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
