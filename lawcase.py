#!/usr/bin/env python3
"""Auditable law investigation CLI; see LAWCASE.md."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import corpus

DEFAULT_ROOT = '/home/wenga/Documents/Dossier Scheidung'

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, default=Path('.lawcase'))
    sub = p.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Register source folder and local MCP connection')
    init.add_argument('source_root', nargs='?', default=DEFAULT_ROOT)
    init.add_argument('--mcp-url')
    init.add_argument('--mcp-command', type=json.loads, help='JSON argv array, including --read-only mcp --stdio')
    sub.add_parser('probe', help='Read-only MCP connectivity and inventory check')
    sub.add_parser('snapshot', help='Reconcile originals and freeze extracted source units')
    sub.add_parser('login', help='Sign into dedicated subscription-only Codex home')
    sub.add_parser('doctor', help='Check Codex version and dedicated ChatGPT login')
    for name in ('investigate', 'resume'):
        q = sub.add_parser(name)
        if name == 'investigate':
            q.add_argument('question')
            q.add_argument('--snapshot', type=Path)
            q.add_argument('--authorities', type=Path)
            q.add_argument('--evidence-only', action='store_true', help='Explicitly omit legal assessment; no authorities required')
            q.add_argument('--model', default='gpt-6-astra')
            q.add_argument('--effort', choices=['low','medium','high','xhigh'], default='medium')
            q.add_argument('--dry-run', action='store_true')
            q.add_argument('--max-rounds', type=int, default=3, help='Investigation follow-up rounds; remaining issues stay explicit')
        else:
            q.add_argument('run', nargs='?', default='latest')
        q.add_argument('--max-calls', type=int, default=0)
        q.add_argument('--timeout', type=int, default=900)
    for name in ('status', 'report', 'verify'):
        sub.add_parser(name).add_argument('run', nargs='?', default='latest')
    research = sub.add_parser('research', help='Research an explicitly supplied PUBLIC abstract legal issue')
    research.add_argument('public_issue', nargs='?', help='Public legal question only; no private parties or facts')
    research.add_argument('--resume', type=Path, help='Resume an existing authority research directory')
    research.add_argument('--model', default='gpt-6-astra')
    research.add_argument('--effort', choices=['low','medium','high','xhigh'], default='medium')
    research.add_argument('--max-calls', type=int, default=0)
    research.add_argument('--timeout', type=int, default=900)
    return p


def read_json(path):
    return json.loads(path.read_text())


def open_engine(state, ident, readonly=False):
    from lawcase_engine import Engine
    if ident == 'latest':
        ident = (state / 'latest-run').read_text().strip()
    if Path(ident).name != ident:
        raise corpus.CorpusError('Run must be a run ID, not a path.')
    folder = state / 'runs' / ident
    meta = read_json(folder / 'request.json')
    return (Engine.open_readonly(folder) if readonly else Engine(folder, Path(meta['snapshot']), meta['question'], meta['config'])), meta


def execute(args):
    from lawcase_worker import CodexWorker
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfg_path = state / 'config.json'
    if args.command == 'init':
        root = Path(args.source_root).expanduser().resolve(strict=True)
        if not root.is_dir() or state == root or root in state.parents:
            raise corpus.CorpusError('State must be outside the original source folder.')
        if cfg_path.exists():
            raise corpus.CorpusError('Already initialized; configuration was preserved.')
        cfg = {'source_root': str(root)}
        if args.mcp_url:
            cfg['mcp_url'] = args.mcp_url
        if args.mcp_command:
            cfg['mcp_command'] = args.mcp_command
        corpus.write_json(cfg_path, cfg)
        print('Initialized:', cfg_path)
        return 0
    if args.command == 'login':
        from lawcase_worker import login
        return login(state)
    if args.command == 'doctor':
        try:
            print(corpus.codex_preflight(state, corpus.codex_executable('codex')))
        except corpus.CorpusError as exc:
            raise corpus.CorpusError(str(exc).replace('corpus.py login', 'lawcase.py login')) from exc
        print('Dedicated ChatGPT login confirmed; no inference used.')
        return 0
    if args.command == 'research':
        from lawcase_authorities import research, verify_proposals, SCHEMAS
        worker = CodexWorker(state, SCHEMAS, args.model, args.effort, args.max_calls, args.timeout)
        folder = args.resume.resolve() if args.resume else state / 'authorities' / ('A' + uuid.uuid4().hex[:16])
        print('Research directory:', folder, flush=True)
        if args.resume:
            if not folder.is_relative_to(state / 'authorities'):
                raise corpus.CorpusError('Research resume directory must be inside this case authority store.')
            previous = read_json(folder / 'research.json')
            issue = args.public_issue or previous['public_issue']
            result = verify_proposals(issue, read_json(folder / 'proposals.json'), folder, worker)
        else:
            if not args.public_issue or not args.public_issue.strip():
                raise corpus.CorpusError('Public legal issue is required.')
            result = research(args.public_issue, folder, worker)
        print(corpus.dump({'file': str(folder / 'research.json'),
                           'verified': sum(a['verification_status'] == 'verified' for a in result['authorities']),
                           'considered': len(result['authorities']), 'limitations': result['limitations']}))
        return 0 if any(a['verification_status'] == 'verified' for a in result['authorities']) else 2
    if args.command in ('probe','snapshot'):
        import lawcase_sources
        cfg = read_json(cfg_path)
        if args.command == 'probe':
            print(json.dumps(asyncio.run(lawcase_sources.probe(cfg)), indent=2))
        else:
            folder = state / 'snapshots' / ('S' + uuid.uuid4().hex[:16])
            result = asyncio.run(lawcase_sources.snapshot(cfg, folder))
            corpus.write_text(state / 'latest-snapshot', str(folder))
            print('Snapshot:', folder)
            print('Documents:', len(result['documents']), 'Units:', len(result['units']))
        return 0
    from lawcase_engine import Engine, STAGE_SCHEMAS
    if args.command == 'investigate':
        if not args.question.strip():
            raise corpus.CorpusError('Question must not be empty.')
        snap = args.snapshot or Path((state / 'latest-snapshot').read_text().strip())
        config = {'model': args.model, 'effort': args.effort, 'verified_authorities': [], 'evidence_only': args.evidence_only, 'max_rounds': args.max_rounds}
        if not args.authorities and not args.evidence_only:
            raise corpus.CorpusError('Supply --authorities research.json or explicitly choose --evidence-only.')
        if args.authorities:
            from lawcase_authorities import load_verified
            config['verified_authorities'] = load_verified(args.authorities)
        ident = 'R' + uuid.uuid4().hex[:16]
        folder = state / 'runs' / ident
        folder.mkdir(parents=True, mode=0o700)
        meta = {'snapshot': str(snap.resolve()), 'question': args.question, 'config': config}
        corpus.write_json(folder / 'request.json', meta)
        engine = Engine(folder, snap.resolve(), args.question, config)
        corpus.write_text(state / 'latest-run', ident)
        print('Run:', ident, flush=True)
        if args.dry_run:
            print(json.dumps(engine.status(), indent=2))
            return 0
    else:
        engine, meta = open_engine(state, args.run, readonly=args.command in ('status', 'verify'))
    if args.command == 'status':
        print(json.dumps(engine.status(), indent=2))
    elif args.command == 'report':
        engine.report()
        from lawcase_report import render
        print(render(engine))
    elif args.command == 'verify':
        errors = engine.verify()
        print(json.dumps({'ok': not errors, 'errors': errors}, indent=2))
        return int(bool(errors))
    else:
        config = meta['config']
        worker = CodexWorker(state, STAGE_SCHEMAS, config['model'], config['effort'], args.max_calls, args.timeout)
        engine.run(worker)
        print(json.dumps(engine.status(), indent=2))
        engine.report()
        from lawcase_report import render
        print('Report:', render(engine))
    return 0


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    if hasattr(args, 'max_calls') and args.max_calls < 0:
        parser().error('--max-calls must be nonnegative')
    if hasattr(args, 'timeout') and args.timeout <= 0:
        parser().error('--timeout must be positive')
    try:
        return execute(args)
    except KeyboardInterrupt:
        print('Interrupted; saved work can be resumed.', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
