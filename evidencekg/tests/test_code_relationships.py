import copy

import pytest
from test_code_context import snapshot

from evidencekg.db import dump
from evidencekg.hybrid.code_context import _documents
from evidencekg.hybrid.code_relationships import _edges, _nodes, build_relationship_context


def test_imported_caller_and_callee_are_linked_with_exact_sources():
    files = {
        'pkg/src/pkg/parser.py': 'def parse():\n    return {"status": "failed"}\n',
        'pkg/src/pkg/ingest.py': 'from .parser import parse\n\ndef ingest():\n    result = parse()\n    status = result["status"]\n    return status\n',
    }
    segments = snapshot(files)
    packet = dict(snapshot_id='frozen', segments=list(segments.values())[:1])
    before = copy.deepcopy((segments, packet))
    result = build_relationship_context(segments, 'parse failed status', packet)
    entries = [n for g in result['implementations'] for n in g['nodes']]
    parse = next(n for n in entries if n['symbol'] == 'parse')
    ingest = next(n for n in entries if n['symbol'] == 'ingest')
    assert any(r['symbol'] == 'ingest' and r['included'] for r in parse['called_by'])
    assert any(r['symbol'] == 'parse' and r['included'] for r in ingest['calls'])
    assert ingest['dimensions']['mapping_keys']['reads'] == ['status']
    for n in entries:
        for q in [n['source']] + n['enclosing_source'] + n['dimensions']['returns'] + n['dimensions']['raises']:
            assert q['quote'] == ''.join(segments[r['segment_id']]['text'][r['start']:r['end']]
                                         for r in q['source_refs'])
    assert (segments, packet) == before


def test_unrelated_implementations_stay_separate():
    segments = snapshot({'pkg/src/pkg/page.py': 'def page():\n    return "cursor"\n',
                         'legacy.py': 'def page():\n    return "offset"\n'})
    result = build_relationship_context(segments, 'page cursor', dict(segments=list(segments.values())))
    assert len(result['implementations']) == 2
    assert {g['implementation'] for g in result['implementations']} == {'pkg', 'legacy'}


def test_shadowed_import_does_not_create_relationship():
    segments = snapshot({'pkg/a.py': 'from .b import parse\n\ndef ingest(parse):\n    return parse()\n',
                         'pkg/b.py': 'def parse():\n    return 1\n'})
    nodes, _ = _nodes(_documents(segments))
    assert not _edges(nodes)


def test_guard_and_complete_function_survive_with_unicode():
    source = 'banner = "é\u2028text"\nif TYPE_CHECKING:\n    def target():\n        first = True\n        first = False\n        return first\n'
    segments = snapshot({'pkg/a.py': source})
    result = build_relationship_context(segments, 'target first', dict(segments=list(segments.values())))
    target = next(n for g in result['implementations'] for n in g['nodes'] if n['symbol'] == 'target')
    assert any('if TYPE_CHECKING:' in q['quote'] for q in target['enclosing_source'])
    assert 'first = True' in target['source']['quote'] and 'first = False' in target['source']['quote']


def test_source_mismatch_fails_closed():
    segments = snapshot({'x.py': 'def x():\n    return 1\n'})
    packet = dict(segments=copy.deepcopy(list(segments.values())))
    packet['segments'][0]['text'] += 'changed'
    with pytest.raises(ValueError, match='source mismatch'):
        build_relationship_context(segments, 'x function', packet)


def test_bounded_output_and_nonpython_fallback():
    segments = snapshot({'note.md': 'source information\n'})
    result = build_relationship_context(segments, 'source information', dict(segments=list(segments.values())), max_bytes=10000)
    assert result['original_passages'][0]['quote'] == 'source information\n'
    assert len(dump(result).encode()) <= 10000


def test_ambiguous_module_symbols_do_not_create_relationships():
    segments = snapshot({'one/src/pkg/b.py': 'def parse():\n    return 1\n',
                         'two/src/pkg/b.py': 'def parse():\n    return 2\n',
                         'one/src/pkg/a.py': 'from .b import parse\n\ndef run():\n    return parse()\n'})
    nodes, _ = _nodes(_documents(segments))
    assert not _edges(nodes)


def test_same_module_name_in_other_implementation_is_not_a_dependency():
    segments = snapshot({'one/src/pkg/a.py': 'from .b import parse\n\ndef run():\n    return parse()\n',
                         'two/src/pkg/b.py': 'def parse():\n    return 2\n'})
    nodes, _ = _nodes(_documents(segments))
    assert not _edges(nodes)


def test_decorator_and_static_self_are_not_body_calls():
    segments = snapshot({'pkg/a.py': 'def decorate():\n    return identity\n\n@decorate()\ndef run():\n    return 1\n\nclass C:\n    def parse(self):\n        return 2\n    @staticmethod\n    def static(self):\n        return self.parse()\n'})
    nodes, _ = _nodes(_documents(segments))
    assert not _edges(nodes)


def test_guarded_nested_function_calls_have_their_own_owner():
    segments = snapshot({'pkg/a.py': 'def helper():\n    return 1\n\nif active:\n    def guarded():\n        return helper()\n\ndef outer():\n    def inner():\n        return helper()\n    return inner\n'})
    nodes, _ = _nodes(_documents(segments))
    links = {(nodes[i]['symbol'], nodes[j]['symbol']) for i, j in _edges(nodes)}
    assert links == {('guarded', 'helper'), ('outer.inner', 'helper')}


def test_enclosing_parameter_shadow_is_not_global_function_call():
    segments = snapshot({'pkg/a.py': 'def helper():\n    return 1\n\ndef outer(helper):\n    def inner():\n        return helper()\n    return inner\n'})
    nodes, _ = _nodes(_documents(segments))
    assert not _edges(nodes)


def test_mixed_unparsed_source_is_not_lost_when_python_nodes_exist():
    segments = snapshot({'broken.py': 'def broken(:\n', 'app.js': 'export function run() {}\n',
                         'valid.py': 'def run():\n    return 1\n'})
    result = build_relationship_context(segments, 'run', dict(segments=list(segments.values())))
    assert {p['path'] for p in result['original_passages']} == {'broken.py', 'app.js', 'valid.py'}


def test_mapping_update_reads_and_writes_but_delete_does_not_read():
    segments = snapshot({'pkg/a.py': 'def run(data):\n    data["total"] += 1\n    del data["removed"]\n'})
    result = build_relationship_context(segments, 'run total', dict(segments=list(segments.values())))
    node = next(n for g in result['implementations'] for n in g['nodes'] if n['symbol'] == 'run')
    assert node['dimensions']['mapping_keys'] == {'reads': ['total'], 'writes': ['total'], 'deletes': ['removed']}


@pytest.mark.parametrize('source', [
    'def parse():\n    return 1\n\nif active:\n    parse = replacement\n\ndef run():\n    return parse()\n',
    'def parse():\n    return 1\n\ndef run(value=parse()):\n    return value\n',
    'class C:\n    def parse(self):\n        return 1\n    def run(self, other):\n        self = other\n        return self.parse()\n',
    'from .other import *\n\ndef parse():\n    return 1\n\ndef run():\n    return parse()\n',
])
def test_ambiguous_or_definition_time_calls_do_not_become_body_edges(source):
    nodes, _ = _nodes(_documents(snapshot({'pkg/a.py': source})))
    assert not _edges(nodes)


def test_deep_valid_expression_preserves_original_without_recursion_failure():
    segments = snapshot({'deep.py': 'def run():\n    return ' + '+'.join(['1'] * 1100) + '\n'})
    packet = dict(segments=list(segments.values())[:1])
    result = build_relationship_context(segments, 'run return', packet)
    assert result['original_passages'][0]['quote'] == packet['segments'][0]['text']
    assert result['limitations']['skipped_python'][0]['reason'] == 'Python AST nesting limit'


def test_omissions_include_nodes_not_considered_for_selection():
    segments = snapshot({'a.py': '\n'.join(f'def task_{i}():\n    return {i}\n' for i in range(25))})
    result = build_relationship_context(segments, 'task', dict(segments=list(segments.values())[:1]))
    selected = sum(len(g['nodes']) for g in result['implementations'])
    assert result['limitations']['omitted_nodes'] == 25 - selected > 0


def test_original_python_passages_survive_and_unrelated_matches_cannot_replace_them():
    segments = snapshot({'pkg/a.py': 'def entry():\n    return helper()\n\ndef helper():\n    return 1\n',
                         'unrelated.py': 'def target_inventory_timeout():\n    return "target inventory timeout"\n'})
    originals = [p for p in segments.values() if p['source_path'] == 'pkg/a.py']
    result = build_relationship_context(segments, 'target inventory timeout', dict(segments=originals))
    assert [p['quote'] for p in result['original_passages']] == [p['text'] for p in originals]
    assert all(n['path'] == 'pkg/a.py' for g in result['implementations'] for n in g['nodes'])


def test_seed_is_exact_interval_not_any_function_in_the_same_document():
    first = 'def seed():\n    return 1\n'
    source = first + '\n' + 'def unrelated_target():\n    return "target"\n'
    segments = snapshot({'pkg/a.py': source}, size=len(first))
    original = next(iter(segments.values()))
    result = build_relationship_context(segments, 'unrelated target', dict(segments=[original]))
    assert [n['symbol'] for g in result['implementations'] for n in g['nodes']] == ['seed']


def test_required_originals_fail_closed_when_budget_is_insufficient():
    segments = snapshot({'large.py': '# ' + 'x' * 12000}, size=20000)
    with pytest.raises(ValueError, match='retain original source passages'):
        build_relationship_context(segments, 'large', dict(segments=list(segments.values())), max_bytes=10000)


def test_no_global_reranker_invocation_and_no_second_hop_expansion():
    class ForbiddenReranker:
        def score(self, *args):
            raise AssertionError('Original node anchors must not be globally reranked')
    segments = snapshot({'pkg/a.py': 'from .b import helper\ndef seed():\n    return helper()\n',
                         'pkg/b.py': 'from .c import distant\ndef helper():\n    return distant()\n',
                         'pkg/c.py': 'def distant():\n    return 1\n'})
    originals = [p for p in segments.values() if p['source_path'] == 'pkg/a.py']
    result = build_relationship_context(segments, 'seed helper distant', dict(segments=originals), reranker=ForbiddenReranker())
    assert {n['symbol'] for g in result['implementations'] for n in g['nodes']} == {'seed', 'helper'}
