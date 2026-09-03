"""Update only this report's display title; preserve its share and versions."""
import json
import os
import sys
import urllib.request
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from harness.web_fetch.config import load_runtime_env
load_runtime_env()
receipt=json.loads((root/'publish/artifact-hub-receipt.json').read_text())
token=os.getenv('ARTIFACT_HUB_TOKEN','').strip()
if not token.startswith('ahp_'):raise SystemExit('Set ARTIFACT_HUB_TOKEN in the repository .env or environment')
request=urllib.request.Request('https://artifact-hub.dev.lexmount.net/api/sites/'+receipt['slug'],
    data=json.dumps({'title':'Web Fetch 评测'}).encode(),method='PATCH',
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=30) as response:
    data=json.load(response)
    print(json.dumps({'status':response.status,'title':data.get('title')},ensure_ascii=False))
