import json
from pathlib import Path
import tempfile
import unittest
from lawcase_engine import Engine
from lawcase_report import render, anchor
from test_engine import snapshot, FakeWorker

class ReportTests(unittest.TestCase):
    def test_all_final_citations_resolve_and_source_markup_is_escaped(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            snapshot(root/'snapshot', [['<script>bad</script> Original agreement.\n'], ['Amendment replaces agreement.\n']])
            engine=Engine(root/'run',root/'snapshot','Compare agreements',{'evidence_only':True})
            try:
                engine.run(FakeWorker())
                path=render(engine)
                text=path.read_text()
                ledger=(path.parent/'evidence.md').read_text()
                self.assertIn('Evidence analysis only.',text)
                for claim in engine._artifacts('claims', group=engine._get('final_group')):
                    self.assertIn(anchor(claim['id']),ledger)
                self.assertEqual(len(list((path.parent/'sources').glob('*.txt'))),2)
                self.assertNotIn('\n<script>bad</script>',text)
            finally:
                engine.db.close()

    def test_authority_ids_resolve_and_urls_cannot_inject_links(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            snapshot(root/'snapshot',[['Original agreement.\n']])
            authority={'id':'L-test','verification_status':'verified','source_uri':'https://www.bger.ch/#) [UNTRUSTED LINK](https://example.org) (','citation':'Synthetic authority','quote':'Rule','section':'1','proposition':'Test rule'}
            engine=Engine(root/'run',root/'snapshot','Compare agreements',{'verified_authorities':[authority]})
            def add_authority(stage,result):
                if stage in ('bundle','synthesize'):
                    for claim in result['claims']:
                        claim['authority_ids']=['L-test']
            try:
                engine.run(FakeWorker(mutate=add_authority))
                path=render(engine);text=path.read_text();ledger=(path.parent/'evidence.md').read_text()
                self.assertIn(anchor('L-test'),text)
                self.assertIn('report.md#'+anchor('L-test'),ledger)
                self.assertNotIn('[UNTRUSTED LINK]',text)
                self.assertIn('%29%20%5BUNTRUSTED%20LINK%5D',text)
            finally:
                engine.db.close()
