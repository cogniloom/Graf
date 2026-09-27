"""Prepare benchmark overrides for a reviewed official TrustGraph deployment archive.

Run with the isolated TrustGraph Python interpreter (requires PyYAML).
This performs local file preparation only; it never downloads or starts services.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import yaml
from trustgraph_adapter import local_endpoint

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--endpoint', default='http://127.0.0.1:18791/trustgraph/v1', type=local_endpoint)
parser.add_argument('--embedding-model', default='sentence-transformers/all-MiniLM-L6-v2')
parser.add_argument('--embedding-dims', type=int, default=384)
args=parser.parse_args()
if args.embedding_dims <= 0:
 parser.error('embedding dimensions must be positive')
project=Path(__file__).resolve().parents[2]
root=project/'.evidencekg-benchmarks/competitors/trustgraph'
archive=root/'deploy.zip'
expected_archive='df79201555351fa5d6f7853c924fe63e50c9cd0d10fb58eaf388a90b4b233c57'
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_archive:
 raise ValueError('Deployment archive differs from the reviewed official package')
with zipfile.ZipFile(archive) as z:
 for name in z.namelist():
  p=Path(name)
  if p.is_absolute() or '..' in p.parts:
   raise ValueError('unsafe deployment archive path')
 z.extractall(root/'deploy')
base=root/'deploy'
compose=yaml.safe_load((base/'docker-compose.yaml').read_text())
# Keep actual native text pipeline and observability; omit binary decoding, UI,
# web-search demo and unrelated tabular processors from this text benchmark.
for service in ['ddg-mcp-server','document-decoder','trustgraph-ui','grafana','rows']:
 compose['services'].pop(service,None)
compose['name']='graf-compare-trustgraph'
image_digests={'docker.io/cassandra:5.0.8': '259578bbeac315a7326c2a99e60eab86f3415e9e895207bf2a032af4de8c16b5', 'docker.io/qdrant/qdrant:v1.18.0': 'b3063c673f3973877c038eeecc392bad5011f072ee7892b56c9a8e204a3bdea9', 'docker.io/rabbitmq:4.1-management': '3574a8edaca320282b9c848f0b2661566b7e74f52c1b590f401b10224d294642', 'docker.io/dxflrs/garage:v2.3.0': '866bd13ed2038ba7e7190e840482bc27234c4afaf77be8cfa439ae088c1e4690', 'docker.io/alpine:3.23.2': '865b95f46d98cf867a156fe4a135ad3fe50d2056aa3f25ed31662dff6da4eb62', 'docker.io/grafana/loki:3.7.2': '191d4fdfb7264f16989f0a57f320872620a5a7c2ceeec6229212c4190ec49b86', 'docker.io/prom/prometheus:v3.11.3': 'e4254400b85610324913f0dc4acf92603d9984e7519414c5a12811aa6146acc3'}
compose['networks']={'default':{'ipam':{'config':[{'subnet':'10.231.95.0/24'}]}}}
for name,s in compose['services'].items():
 s['restart']='no'
 if s['image'] in image_digests:
  s['image']+='@sha256:'+image_digests[s['image']]
 s.pop('ports',None)
 if s['image']=='docker.io/trustgraph/trustgraph-flow:2.9.11':
  s['image']+='@sha256:41030371979dd80e7fa91f75035229dc16329875fc3f0ed9cdd238fd5cd816a5'
 if name in ['control','ingest','text-completion','triples','vector-store']:
  s['deploy']['resources']['limits']['memory']='1G'
 s['volumes']=[v.replace('./',str(base)+'/') if v.startswith('./') else v for v in s.get('volumes',[])]
compose['services']['api-gateway']['ports']=['127.0.0.1:18888:8088']
compose['services']['rabbitmq']['ports']=['127.0.0.1:15673:5672','127.0.0.1:15674:15672']
compose['services']['prometheus']['ports']=['127.0.0.1:19093:9090']
compose['volumes']['cassandra-ordered-start']={}
compose['services']['cassandra']['volumes']=['cassandra-ordered-start:/var/lib/cassandra']
compose['services']['control']['environment']={'IAM_BOOTSTRAP_TOKEN':'tg_benchmark-local'}
for name in ['embeddings','text-completion']:
 s=compose['services'][name]
 s.pop('network_mode',None)
 s['command']=[a for a in s['command'] if a != '--no-metrics']
 s['environment']={'OPENAI_BASE_URL':args.endpoint,'OPENAI_TOKEN':'benchmark-local','BENCHMARK_ENDPOINT':args.endpoint,'PYTHONPATH':'/benchmark','BENCHMARK_EMBEDDING_MODEL':args.embedding_model,'BENCHMARK_EMBEDDING_DIMS':str(args.embedding_dims)}
 s['volumes'].append(str(root/'proxy-socket')+':/benchmark-socket:ro')
 s['volumes'].append(str(project/'benchmarks/competitors/trustgraph_transport.py')+':/benchmark/trustgraph_transport.py:ro')
 launch=base/'launch'/name/'launch.yaml'
 data=yaml.safe_load(launch.read_text())
 for p in data['processors']:
  p['params'].update(rabbitmq_host='rabbitmq',rabbitmq_port=5672,prompt_timeout=1800)
  if 'text_completion.openai' in p['class']:
   p['class']='trustgraph_transport.Completion'
   p['params'].update(thinking='high',model='gpt-6-luna',max_output=16384)
  if 'embeddings.fastembed' in p['class']:
   p['class']='trustgraph_transport.Embeddings'
 launch.write_text(yaml.safe_dump(data))
for path in (base/'launch').glob('*/launch.yaml'):
 data=yaml.safe_load(path.read_text())
 for p in data['processors']:
  p['params']['prompt_timeout']=1800
 path.write_text(yaml.safe_dump(data))
config=base/'trustgraph/config.json'
c=json.loads(config.read_text())
c['parameter-type']['llm-model']['default']='gpt-6-luna'
c['parameter-type']['llm-model']['enum']=[{'id':'gpt-6-luna','description':'Benchmark subscription model'}]
c['parameter-type']['embeddings-model']['default']=args.embedding_model
c['parameter-type']['embeddings-model']['enum']=[{'id':args.embedding_model,'description':'Benchmark shared embedding model'}]
config.write_text(json.dumps(c,indent=2))
(root/'stack-config.json').write_text(json.dumps({'endpoint':args.endpoint,'embedding_model':args.embedding_model,'embedding_dims':args.embedding_dims,'model':'gpt-6-luna','reasoning_effort':'high','archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()},indent=2))
(root/'benchmark-compose.yaml').write_text(yaml.safe_dump(compose,sort_keys=False))
print('Prepared',len(compose['services']),'official stack services; model processors use task-private Unix socket proxy')
