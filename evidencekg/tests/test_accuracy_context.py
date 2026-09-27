import copy

import pytest
from test_code_context import snapshot

from evidencekg.db import dump
from evidencekg.hybrid.accuracy_context import build_accuracy_context


def test_original_passages_and_overrides_are_preserved():
    source = 'def inventory():\n    complete = True\n' + '    padding = 0\n' * 700 + (
        '    if unknown_descendants:\n        complete = False\n    return complete\n')
    segments = snapshot({'pkg/ingest.py': source, 'other/ingest.py': 'def inventory():\n    return True\n'})
    packet = dict(snapshot_id='frozen', segments=list(segments.values())[:2])
    before = copy.deepcopy((segments, packet))
    result = build_accuracy_context(segments, 'inventory complete', packet)
    text = '\n'.join(r['text'] for e in result['evidence'] if e['path'] == 'pkg/ingest.py'
                     for r in e['regions'])
    assert source in text
    for part in packet['segments']:
        assert part['text'] in text
    assert (segments, packet) == before
    assert result['limitations']['omitted_function_expansions'] == 0


def test_unicode_decorators_and_exact_contiguous_provenance():
    source = 'banner = "hello\u2028world"\n@decorator\nasync def target():\n    return "café"\n'
    segments = snapshot({'a.py': source}, size=13)
    result = build_accuracy_context(segments, 'target', dict(segments=list(segments.values())[3:5]))
    for item in result['evidence']:
        for region in item['regions']:
            assert region['text'] == ''.join(segments[r['segment_id']]['text'][r['start']:r['end']]
                                             for r in region['source_refs'])
    assert any('@decorator\nasync def' in r['text'] for e in result['evidence'] for r in e['regions'])


def test_nonpython_and_unparseable_python_are_retained():
    segments = snapshot({'x.rs': 'pub fn hello() {}', 'bad.py': 'def broken(:\n'})
    result = build_accuracy_context(segments, 'hello', dict(segments=list(segments.values())))
    assert {e['path'] for e in result['evidence']} == {'x.rs', 'bad.py'}


def test_budget_fails_instead_of_silently_dropping_original_evidence():
    segments = snapshot({'large.txt': 'x' * 20000}, size=20000)
    with pytest.raises(ValueError, match='cannot retain'):
        build_accuracy_context(segments, 'hello', dict(segments=list(segments.values())), max_bytes=10000)


def test_expansion_budget_preserves_seeds_and_reports_omissions():
    source = 'def target():\n    x = 1\n' + '    padding = 0\n' * 18000 + '    return x\n'
    segments = snapshot({'a.py': source}, size=1000)
    result = build_accuracy_context(segments, 'target', dict(segments=list(segments.values())[:1]), max_bytes=60000)
    assert result['limitations']['omitted_function_expansions'] > 0
    assert len(dump(result).encode()) <= 60000
    assert any(source[:1000] in r['text'] for e in result['evidence'] for r in e['regions'])


@pytest.mark.parametrize('header', ['if TYPE_CHECKING:\n', 'class Target(Base):\n',
                                   'if active:\n    pass\nelse:\n',
                                   'try:\n    first()\nexcept ValueError:\n',
                                   'match value:\n    case 42:\n'])
def test_original_only_large_function_keeps_ancestor_scope(header):
    indent = '        ' if header.startswith('match') else '    '
    source = header + indent + 'def target():\n' + indent + '    return "' + 'x' * 50000 + '"\n'
    segments = snapshot({'a.py': source}, size=1000)
    result = build_accuracy_context(segments, 'target', dict(segments=list(segments.values())[2:3]))
    text = '\n'.join(r['text'] for e in result['evidence'] for r in e['regions'])
    assert header.splitlines()[0] in text
    if header.startswith('if active'):
        assert 'else:' in text
    if header.startswith('try'):
        assert 'except ValueError:' in text
    if header.startswith('match'):
        assert 'case 42:' in text


@pytest.mark.parametrize('header,precedence', [
    ('try:\n    first()\nexcept Exception:\n    pass\nexcept ValueError:\n', 'except Exception:'),
    ('match value:\n    case _ if blocked:\n        pass\n    case 42:\n', 'case _ if blocked:'),
])
def test_expansion_preserves_earlier_branch_precedence(header, precedence):
    indent = '        ' if header.startswith('match') else '    '
    source = header + indent + 'def target():\n' + indent + '    return "' + 'x' * 50000 + '"\n'
    segments = snapshot({'a.py': source}, size=1000)
    result = build_accuracy_context(segments, 'target', dict(segments=list(segments.values())[2:3]))
    assert any(precedence in r['text'] for e in result['evidence'] for r in e['regions'])
