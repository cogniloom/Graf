import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import corpus
from lawcase_worker import CodexWorker

FAKE = '''#!/usr/bin/env python3
import json,sys
from pathlib import Path
if '--version' in sys.argv:
 print('codex-test'); sys.exit()
if '--help' in sys.argv:
 print('--output-schema --output-last-message --ephemeral --json --skip-git-repo-check');sys.exit()
if 'login' in sys.argv:
 print('Logged in using ChatGPT');sys.exit()
prompt=sys.stdin.read()
out=Path(sys.argv[sys.argv.index('--output-last-message')+1])
out.write_text(json.dumps({'answer':'ok'}))
if 'TOOL_TRAP' in prompt:
 print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'bad'}}))
print(json.dumps({'type':'turn.completed'}))
'''

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.binary = self.path / 'fake-codex'
        self.binary.write_text(FAKE)
        self.binary.chmod(0o700)
        self.schema = {'map': corpus.object_schema({'answer': {'type':'string'}})}
    def worker(self, **kwargs):
        return CodexWorker(self.path / 'state', self.schema, executable=str(self.binary), **kwargs)
    def test_actual_subprocess_receipt_and_budget(self):
        worker = self.worker(max_calls=1)
        self.assertEqual(worker.call('map', {'text':'evidence'}), {'answer':'ok'})
        with self.assertRaises(corpus.Pause):
            worker.call('map', {'text':'next'})
        receipts=list((self.path / 'state/worker-calls').glob('*/receipt.json'))
        self.assertEqual(len(receipts),1)
        self.assertEqual(json.loads(receipts[0].read_text())['status'],'schema_valid')
    def test_tool_activity_rejected_even_with_valid_output(self):
        with self.assertRaisesRegex(corpus.CorpusError,'Unexpected worker tool'):
            self.worker().call('map', {'text':'TOOL_TRAP'})

    def test_application_instructions_precede_untrusted_payload(self):
        worker = self.worker(trusted_instructions={'map': 'TRUSTED_FIXED_STAGE'})
        worker.call('map', {'instruction': 'UNTRUSTED_DOCUMENT_COMMAND'})
        prompt = next((self.path / 'state/worker-calls').glob('*/prompt.txt')).read_text()
        prefix, payload = prompt.split('INPUT_JSON\n', 1)
        self.assertIn('TRUSTED_FIXED_STAGE', prefix)
        self.assertNotIn('UNTRUSTED_DOCUMENT_COMMAND', prefix)
        self.assertIn('UNTRUSTED_DOCUMENT_COMMAND', payload)
    def test_oversized_prompt_not_truncated(self):
        with self.assertRaisesRegex(corpus.CorpusError,'refusing truncation'):
            self.worker().call('map', {'text':'a'*110001})
    def test_api_credentials_removed(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY':'test','CODEX_API_KEY':'test','OPENAI_BASE_URL':'https://bad'}):
            env=corpus.clean_codex_env(self.path)
        for key in ('OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL'):
            self.assertNotIn(key,env)

    def test_bound_schema_rejects_descendant_or_invented_basis(self):
        from lawcase_worker import bind_references
        from lawcase_engine import STAGE_SCHEMAS
        import jsonschema
        schema=bind_references(STAGE_SCHEMAS['synthesize'], {'inputs':{'items':[{'id':'parent','basis_ids':['descendant'],'quotes':[{'unit_id':'unit'}]}]},'verified_authorities':[]})
        basis=schema['properties']['claims']['items']['properties']['basis_ids']
        jsonschema.validate(['parent'],basis)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(['descendant'],basis)
        self.assertEqual(schema['properties']['claims']['items']['properties']['quotes']['items']['properties']['unit_id']['enum'],['unit'])
        self.assertEqual(schema['properties']['claims']['items']['properties']['authority_ids']['maxItems'],0)
        self.assertNotIn('enum',STAGE_SCHEMAS['synthesize']['properties']['claims']['items']['properties']['basis_ids']['items'])
