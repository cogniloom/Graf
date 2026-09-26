"""Streaming human-readable report and complete evidence ledger from engine state."""
from __future__ import annotations
import html
import json
from pathlib import Path
import shutil
from urllib.parse import quote
import corpus

def anchor(ident):
    return 'ref-' + corpus.digest(ident)[:32]

def link(ident):
    return f'[{corpus.markdown_text(ident)}](evidence.md#{anchor(ident)})'

def render(engine) -> Path:
    status = engine.status()
    errors = engine.verify()
    folder = engine.state / 'report'
    folder.mkdir(exist_ok=True, mode=0o700)
    sources = folder / 'sources'
    sources.mkdir(exist_ok=True, mode=0o700)
    for doc in engine.documents.values():
        if doc['id'] in engine.texts:
            corpus.write_text(sources / (corpus.digest(doc['id']) + '.txt'), engine.texts[doc['id']])
    # Write temp files atomically; never hold every finding or pair in memory.
    evidence_temp = folder / '.evidence.tmp'
    with evidence_temp.open('w', encoding='utf-8') as ledger:
        ledger.write('# Evidence ledger\n\nQuotes establish source spans, not the truth of assertions.\n\n')
        for row in engine.db.execute("SELECT id,stage,result FROM tasks WHERE status='succeeded' ORDER BY stage,id"):
            result = engine._identified(row['id'], json.loads(row['result']))
            for category in ('findings','edges','claims','issues'):
                for item in result.get(category, []):
                    ledger.write(f'<a id="{anchor(item["id"])}"></a>\n\n## {corpus.markdown_text(item["id"])}\n\n')
                    text = item.get('statement', item.get('explanation', item.get('question', '')))
                    ledger.write(corpus.markdown_text(text) + '\n\n')
                    ledger.write('Type: ' + corpus.markdown_text(item.get('epistemic', item.get('relation', category))) + '\n\n')
                    for name in ('event_date','document_date','date_uncertainty'):
                        if item.get(name):
                            ledger.write(name + ': ' + corpus.markdown_text(item[name]) + '\n\n')
                    for q in item.get('quotes', []):
                        unit = engine.units[q['unit_id']]
                        doc = engine.documents[unit['document_id']]
                        ledger.write(f'[Source: {corpus.markdown_text(doc["path"])}](sources/{corpus.digest(doc["id"])}.txt), characters {q["start"]}–{q["end"]} (end exclusive).\n\n')
                        ledger.write(corpus.fenced(q['text']) + '\n\n')
                    refs = item.get('basis_ids', [])
                    if refs:
                        ledger.write('Depends on: ' + ', '.join(link(i) if i not in engine.units else corpus.markdown_text(i) for i in refs) + '\n\n')
                    if item.get('authority_ids'):
                        ledger.write('Authorities: ' + ', '.join(f'[{corpus.markdown_text(i)}](report.md#{anchor(i)})' for i in item['authority_ids']) + '\n\n')
    evidence_temp.replace(folder / 'evidence.md')
    lines = ['# Investigation report', '', corpus.markdown_text(engine.question), '',
             '**Run status: ' + corpus.markdown_text(status['status']) + '**', '',
             'Evidence analysis only.' if status['evidence_only'] else 'Assessment uses the supplied authority snapshots; current law and exhaustive precedent coverage are not certified.', '',
             'Mechanical coverage is separate from semantic confidence. No guarantee of identifying every relevant fact or relationship.', '',
             f'Units: {status["unit_count"]}; baseline map expected: {status["baseline_map_expected"]}; baseline pairs expected: {status["baseline_pairs_expected"]}.', '',
             '| Stage | Saved task states |', '|---|---|']
    if status.get('synthetic_snapshot'):
        lines[2:2] = ['**Synthetic validation run. This is not an assessment of the private dossier.**', '']
    for stage, counts in status['stages'].items():
        lines.append('| ' + stage + ' | ' + corpus.markdown_text(corpus.dump(counts)) + ' |')
    if status.get('stage_coverage'):
        lines += ['', '| Stage | Expected | Accounted | Successful |', '|---|---:|---:|---:|']
        for stage, counts in status['stage_coverage'].items():
            lines.append(f'| {stage} | {counts["expected"]} | {counts["accounted"]} | {counts["succeeded"]} |')
    if status.get('synthesis_multipart'):
        lines += ['', 'The assessment remains in multiple bounded parts. Each part is audited; no single global synthesis is claimed.', '']
    lines += ['', '## Limitations and unresolved work', '']
    limitations = errors + status['blockers'] + ['Source gap: ' + s for s in status['source_gaps']]
    if status['unresolved_issues']:
        limitations.append('Unresolved investigation issues: ' + str(status['unresolved_issues']))
    if status.get('error'):
        limitations.append(status['error'])
    lines += ['- ' + corpus.markdown_text(x) for x in limitations] or ['No recorded blockers; semantic completeness remains unproven.']
    lines += ['', '## Provisional assessment', '']
    claims = engine._artifacts('claims', group=engine._get('final_group', 'no-final-group'))
    claim_count = 0
    for claim in claims:
        claim_count += 1
        lines += [corpus.markdown_text(claim['statement']), '', 'Evidence: ' + link(claim['id']), '']
    if not claim_count:
        lines += ['No completed synthesis is available. Consult the evidence ledger and task failures.', '']
    lines += ['## Legal authorities', '']
    for a in engine.authorities.values():
        url = quote(a.get('source_uri', ''), safe=':/?=&%#')
        lines += [f'<a id="{anchor(a["id"])}"></a>', '', corpus.markdown_text(a['id']), '',
                  f'- [{corpus.markdown_text(a.get("citation", a["id"]))}]({url}) — ' +
                  corpus.markdown_text(a.get('proposition','')), '',
                  '  Section: ' + corpus.markdown_text(a.get('section','')), '',
                  corpus.fenced(a.get('quote','')), '',
                  '  ' + corpus.markdown_text(a.get('verification_scope','')), '',
                  '  Retrieved: ' + corpus.markdown_text(a.get('retrieved_at','unknown')), '']
        lines += ['- ' + corpus.markdown_text(q) for q in a.get('verification', {}).get('qualifications', [])]
        lines += ['- ' + corpus.markdown_text(q) for q in a.get('research_limitations', [])]
        lines.append('')
    lines += ['[Complete evidence ledger](evidence.md). Full task inputs, outputs and attempts remain in the SQLite audit database.', '']
    corpus.write_text(folder / 'report.md', '\n'.join(lines))
    corpus.write_json(folder / 'coverage.json', status)
    return folder / 'report.md'
