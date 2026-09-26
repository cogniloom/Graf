"""Public-only official authority retrieval; retrieval alone is not verification."""
from __future__ import annotations
import io
import json
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import corpus

STR = {'type': 'string'}
ARRAY = {'type': 'array', 'items': STR}
PROPOSAL = corpus.object_schema({'url': STR, 'citation': STR, 'section': STR,
                                'proposition': STR, 'quote': STR, 'date': STR})
SCHEMAS = {
    'research': corpus.object_schema({'authorities': {'type': 'array', 'items': PROPOSAL},
                                     'limitations': ARRAY}),
    'authority_verify': corpus.object_schema({'supported': {'type': 'boolean'},
        'section_matches': {'type': 'boolean'}, 'reason': STR, 'qualifications': ARRAY}),
}
ALLOWED_HOSTS = {'fedlex.admin.ch', 'www.fedlex.admin.ch', 'fedlex.data.admin.ch',
                 'bger.ch', 'www.bger.ch', 'search.bger.ch', 'bvger.ch', 'www.bvger.ch',
                 'bstger.ch', 'www.bstger.ch'}
MAX_BYTES = 30_000_000

def official_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in ALLOWED_HOSTS or p.username or p.password or p.port not in (None, 443):
        raise corpus.CorpusError('Authority URL must use HTTPS on an allowed official host.')
    return url


def fetch(url: str) -> tuple[str, bytes, str]:
    import httpx
    current = official_url(url)
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        for _ in range(6):
            with client.stream('GET', current) as response:
                if response.is_redirect:
                    current = official_url(urljoin(current, response.headers['location']))
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise corpus.CorpusError('Official authority exceeds download limit; not truncated.')
                    chunks.append(chunk)
                return current, b''.join(chunks), response.headers.get('content-type', '')
    raise corpus.CorpusError('Too many official-source redirects.')


def extract(data: bytes, content_type: str) -> str:
    if data.startswith(b'%PDF'):
        import pypdfium2
        with pypdfium2.PdfDocument(data) as pdf:
            pages = []
            for page in pdf:
                try:
                    text = page.get_textpage()
                    try:
                        pages.append(text.get_text_range())
                    finally:
                        text.close()
                finally:
                    page.close()
            return '\n'.join(pages)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data, 'html.parser')
    for node in soup(['script', 'style', 'noscript']):
        node.decompose()
    return soup.get_text('\n')


def research(issue: str, state: Path, worker) -> dict:
    """Caller supplies a public abstract issue; never consume a private run here."""
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    proposals = worker.call('research', {'public_legal_issue': issue, 'as_of': corpus.now()})
    corpus.schema_validate(proposals, SCHEMAS['research'])
    corpus.write_json(state / 'proposals.json', proposals)
    return verify_proposals(issue, proposals, state, worker)


def verify_proposals(issue: str, proposals: dict, state: Path, worker) -> dict:
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    corpus.schema_validate(proposals, SCHEMAS['research'])
    previous = {}
    if (state / 'research.json').exists():
        saved = json.loads((state / 'research.json').read_text())
        if saved['public_issue'] != issue:
            raise corpus.CorpusError('Research issue differs from saved investigation.')
        previous = {r['id']: r for r in load_verified(state / 'research.json')}
    corpus.write_json(state / 'proposals.json', proposals)
    result = {'public_issue': issue, 'retrieved_at': corpus.now(), 'authorities': [],
              'limitations': proposals['limitations']}
    for proposal in proposals['authorities']:
        record = dict(proposal, verification_status='unverified')
        try:
            prior = next((r for r in previous.values() if all(
                (r.get('proposed_quote', r['quote']) if k == 'quote' else r[k]) == v
                for k, v in proposal.items())), None)
            if prior is not None:
                result['authorities'].append(prior)
                continue
            url, data, mime = fetch(proposal['url'])
            text = extract(data, mime)
            if not text.strip():
                raise corpus.CorpusError('No usable official source text; cannot verify.')
            quote = proposal['quote']
            if not quote.strip():
                raise corpus.CorpusError('Empty authority quotation.')
            start, end = corpus.locate_quote(text, quote, 0, len(text))
            record['proposed_quote'] = quote
            record['quote'] = text[start:end]
            quote = record['quote']
            # Full local source is retained; verification scope is explicitly bounded.
            section = text[max(0, start-12000):min(len(text), start+len(quote)+12000)]
            verification = worker.call('authority_verify', {
                'instruction': 'Independently test whether the official passage supports the proposition and cited section. Reject mismatched sections, misleading quotation, unsupported dates or hidden qualifications. Retrieval does not establish that law is current.',
                'proposal': proposal, 'official_passage': section})
            corpus.schema_validate(verification, SCHEMAS['authority_verify'])
            ident = 'L' + corpus.digest(corpus.dump([url, corpus.digest(data), proposal]))[:24]
            folder = state / ident
            folder.mkdir(exist_ok=True, mode=0o700)
            (folder / 'source.bin').write_bytes(data)
            corpus.write_text(folder / 'source.txt', text)
            record.update(id=ident, source_uri=url, content_sha256=corpus.digest(data),
                          text_sha256=corpus.digest(text), retrieved_at=corpus.now(),
                          verification=verification, snapshot=str(folder.resolve()),
                          verification_scope='quoted passage plus 12000 surrounding characters; currency not independently certified',
                          research_limitations=proposals['limitations'])
            if verification['supported'] and verification['section_matches']:
                record['verification_status'] = 'verified'
            corpus.write_json(folder / 'authority.json', record)
        except corpus.Pause as exc:
            record['error'] = str(exc)
            record['verification_status'] = 'paused'
            result['authorities'].append(record)
            result['status'] = 'paused'
            corpus.write_json(state / 'research.json', result)
            raise
        except Exception as exc:
            record['error'] = type(exc).__name__ + ': ' + str(exc)
        result['authorities'].append(record)
        corpus.write_json(state / 'research.json', result)
    result['status'] = 'finished_with_unverified' if any(r['verification_status'] != 'verified' for r in result['authorities']) else 'finished'
    corpus.write_json(state / 'research.json', result)
    return result


def load_verified(path: Path) -> list[dict]:
    bundle = json.loads(path.read_text())
    verified = []
    for record in bundle['authorities']:
        if record['verification_status'] != 'verified':
            continue
        official_url(record['source_uri'])
        folder = Path(record['snapshot'])
        if not folder.resolve().is_relative_to(path.resolve().parent):
            raise corpus.CorpusError('Authority snapshot escapes research directory.')
        saved = json.loads((folder / 'authority.json').read_text())
        if saved != record:
            raise corpus.CorpusError('Authority metadata differs from verified snapshot.')
        verification = record['verification']
        corpus.schema_validate(verification, SCHEMAS['authority_verify'])
        if not verification['supported'] or not verification['section_matches']:
            raise corpus.CorpusError('Authority verifier did not accept this proposition.')
        proposal = {k: record[k] for k in PROPOSAL['properties']}
        proposal['quote'] = record.get('proposed_quote', record['quote'])
        expected_id = 'L' + corpus.digest(corpus.dump([record['source_uri'], record['content_sha256'], proposal]))[:24]
        if expected_id != record['id']:
            raise corpus.CorpusError('Authority proposition identity mismatch.')
        text = corpus.read_text_exact(folder / 'source.txt')
        if corpus.digest((folder / 'source.bin').read_bytes()) != record['content_sha256'] or corpus.digest(text) != record['text_sha256']:
            raise corpus.CorpusError('Authority snapshot checksum failed.')
        if not record['quote'] or record['quote'] not in text:
            raise corpus.CorpusError('Authority quote no longer resolves.')
        verified.append(record)
    return verified
