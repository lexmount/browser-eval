"""Publish the validated package to the user-specified Artifact Hub service."""
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

BASE='https://artifact-hub.dev.lexmount.net'
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.config import load_runtime_env
load_runtime_env()
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.publication_target import require_publication_site
token=load_token()
receipt=(Path(sys.argv[sys.argv.index('--receipt')+1]) if '--receipt' in sys.argv
         else ROOT/'publish/artifact-hub-receipt.json')
updating='--update' in sys.argv
expected=sys.argv[sys.argv.index('--expected-version')+1] if '--expected-version' in sys.argv else None
if updating:assert receipt.exists() and expected and expected.startswith('ver_')

def request(path,data=None,content_type=None,auth=True,method=None):
 headers={'User-Agent':'WebFetch-Report-Publisher/1.0'}
 if auth:headers['Authorization']='Bearer '+token
 if content_type:headers['Content-Type']=content_type
 req=urllib.request.Request(BASE+path if path.startswith('/') else path,data=data,headers=headers,method=method)
 with urllib.request.urlopen(req,timeout=240) as response:return response.status,response.read()

if not receipt.exists():
 raise ValueError('Current report requires its existing site receipt; refusing to create a duplicate')
result=json.loads(receipt.read_text())
require_publication_site(result,ROOT/'evaluation/current-report.json')
print('Using saved publication receipt; not creating a duplicate site.',flush=True)
if updating:
 archive=ROOT/'publish/webfetch-sites.zip';boundary='----webfetch'+uuid.uuid4().hex
 from scripts.check_publication_bundle import check
 check(archive,ROOT/'reports/webfetch')
 large_archive=archive.stat().st_size > 23*1024*1024
 parts=[]
 for key,value in [('mode','zip'),('title','Web Fetch 评测')]:
  parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
 if not large_archive:
  parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="webfetch.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode()+archive.read_bytes()+b'\r\n')
 parts.append(f'--{boundary}--\r\n'.encode())
 print('Uploading validated report package',flush=True)
 endpoint='/api/sites/'+result['slug']+'/versions?expected_version='+expected
 if large_archive:
  # The service caps one-shot requests at 24 MB. Upload the unpacked files
  # into a staging session, then activate the complete tree in one commit.
  from harness.web_fetch.publication import upload_large_archive
  raw=upload_large_archive(request,archive,result['slug'],expected)
  status=200
 else:
  status,raw=request(endpoint,b''.join(parts),'multipart/form-data; boundary='+boundary)
 response=json.loads(raw);assert status==200,response
 result['last_update']=response
 # Retain manageability without putting edit/claim credentials in console output.
 receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2));os.chmod(receipt,0o600)
 print('Report created: '+result['slug'],flush=True)
if not result.get('share_url'):
 status,raw=request('/api/sites/'+result['slug']+'/shares',json.dumps({'policy':'public'}).encode(),'application/json')
 share=json.loads(raw);result['share_url']=share['url']
 receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2));os.chmod(receipt,0o600)
status,_=request(result['share_url'],auth=False)
assert status==200,status
print(json.dumps({'slug':result['slug'],'url':result['share_url'],'unauthenticated_status':status},ensure_ascii=False),flush=True)
