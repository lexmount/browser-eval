"""Update only this report's display title; preserve its share and versions."""
import json
import os
import sys
import urllib.request
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
receipt_path=Path(sys.argv[1]) if len(sys.argv)>1 else root/'publish/artifact-hub-receipt.json'
receipt=json.loads(receipt_path.read_text())
from harness.web_fetch.artifact_auth import load_token
token=load_token()
base=os.environ.get('ARTIFACT_HUB_BASE_URL',receipt.get('base_url','https://artifact-hub.app.lexmount.com')).rstrip('/')
request=urllib.request.Request(base+'/api/sites/'+receipt['slug'],
    data=json.dumps({'title':'Web Fetch 评测'}).encode(),method='PATCH',
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=30) as response:
    data=json.load(response)
    print(json.dumps({'status':response.status,'title':data.get('title')},ensure_ascii=False))
