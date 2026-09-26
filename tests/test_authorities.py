import unittest
from lawcase_authorities import official_url, extract
import corpus

class AuthorityTests(unittest.TestCase):
    def test_official_url_rejects_lookalikes_and_private_targets(self):
        for url in ['http://www.bger.ch/', 'https://bger.ch.evil.test/', 'https://127.0.0.1/',
                    'https://user@www.bger.ch/', 'https://www.bger.ch:8443/', 'file:///etc/passwd']:
            with self.subTest(url=url), self.assertRaises(corpus.CorpusError):
                official_url(url)
        self.assertEqual(official_url('https://www.bger.ch/a'), 'https://www.bger.ch/a')
    def test_html_removes_executable_text(self):
        self.assertEqual(extract(b'<p>Official text</p><script>bad()</script>', 'text/html'), 'Official text')

    def test_fetch_is_not_verification_and_modified_snapshot_rejected(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from lawcase_authorities import research, load_verified
        class Worker:
            supported=True
            def call(self,stage,payload):
                if stage=='research':
                    return {'authorities':[{'url':'https://www.bger.ch/example','citation':'Synthetic',
                        'section':'1','proposition':'A proposition','quote':'Official source text', 'date':'2026-01-01'}], 'limitations':[]}
                return {'supported':self.supported,'section_matches':True,'reason':'Synthetic verification', 'qualifications':[]}
        with tempfile.TemporaryDirectory() as td, patch('lawcase_authorities.fetch',return_value=('https://www.bger.ch/example',b'<p>Official source text</p>','text/html')):
            root=Path(td)
            worker=Worker()
            worker.supported=False
            result=research('Public synthetic issue',root/'rejected',worker)
            self.assertEqual(result['authorities'][0]['verification_status'],'unverified')
            self.assertEqual(load_verified(root/'rejected/research.json'),[])
            worker.supported=True
            result=research('Public synthetic issue',root/'accepted',worker)
            self.assertEqual(len(load_verified(root/'accepted/research.json')),1)
            record=result['authorities'][0]
            (Path(record['snapshot'])/'source.txt').write_text('Tampered')
            with self.assertRaisesRegex(corpus.CorpusError,'checksum'):
                load_verified(root/'accepted/research.json')

    def test_actual_pdfium_text_extraction(self):
        objects=[b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                 b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                 b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
        content=b'BT /F1 12 Tf 72 720 Td (Official source text) Tj ET'
        objects.append(b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream')
        data=b'%PDF-1.4\n';offsets=[0]
        for i,obj in enumerate(objects,1):
            offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        start=len(data)
        data+=b'xref\n0 6\n0000000000 65535 f \n'
        for offset in offsets[1:]:
            data+=f'{offset:010d} 00000 n \n'.encode()
        data+=b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'
        self.assertEqual(extract(data,'application/pdf'),'Official source text')

    def test_metadata_tampering_and_pause_are_fail_closed(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from lawcase_authorities import research, load_verified, verify_proposals
        class Worker:
            pause=False
            def call(self,stage,payload):
                if stage=='research':
                    return {'authorities':[{'url':'https://www.bger.ch/example','citation':'Synthetic',
                        'section':'1','proposition':'A proposition','quote':'Official source text','date':'2026-01-01'}], 'limitations':[]}
                if self.pause:
                    raise corpus.Pause('Synthetic quota pause')
                return {'supported':True,'section_matches':True,'reason':'Synthetic','qualifications':[]}
        with tempfile.TemporaryDirectory() as td, patch('lawcase_authorities.fetch',return_value=('https://www.bger.ch/example',b'<p>Official source text</p>','text/html')):
            root=Path(td);worker=Worker()
            result=research('Public synthetic issue',root,worker)
            result['authorities'][0]['proposition']='Altered proposition'
            (root/'research.json').write_text(json.dumps(result))
            with self.assertRaisesRegex(corpus.CorpusError,'metadata differs'):
                load_verified(root/'research.json')
            paused=root/'paused';worker.pause=True
            with self.assertRaises(corpus.Pause):
                research('Public synthetic issue',paused,worker)
            self.assertEqual(json.loads((paused/'research.json').read_text())['status'],'paused')
            worker.pause=False
            verify_proposals('Public synthetic issue',json.loads((paused/'proposals.json').read_text()),paused,worker)
            self.assertEqual(len(load_verified(paused/'research.json')),1)
