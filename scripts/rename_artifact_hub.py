"""Update only this report's display title; preserve its share and versions."""
import json
import urllib.request
from pathlib import Path

root=Path(__file__).resolve().parents[1]
receipt=json.loads((root/'publish/artifact-hub-receipt.json').read_text())
token=Path('/Users/lianglinkai/.config/artifact-hub/token').read_text().strip()
request=urllib.request.Request('https://artifact-hub.dev.lexmount.net/api/sites/'+receipt['slug'],
    data=json.dumps({'title':'Web Fetch 评测'}).encode(),method='PATCH',
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=30) as response:
    data=json.load(response)
    print(json.dumps({'status':response.status,'title':data.get('title')},ensure_ascii=False))
