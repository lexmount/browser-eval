"""Publish the validated package to the user-specified Artifact Hub service."""
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

BASE='https://artifact-hub.dev.lexmount.net'
ROOT=Path(__file__).resolve().parents[1]
token=Path('/Users/lianglinkai/.config/artifact-hub/token').read_text().strip()
assert token.startswith('ahp_')
receipt=ROOT/'publish/artifact-hub-receipt.json'
updating='--update' in sys.argv
expected=sys.argv[sys.argv.index('--expected-version')+1] if '--expected-version' in sys.argv else None
if updating:assert receipt.exists() and expected and expected.startswith('ver_')

def request(path,data=None,content_type=None,auth=True):
 headers={'User-Agent':'WebFetch-Report-Publisher/1.0'}
 if auth:headers['Authorization']='Bearer '+token
 if content_type:headers['Content-Type']=content_type
 req=urllib.request.Request(BASE+path if path.startswith('/') else path,data=data,headers=headers)
 with urllib.request.urlopen(req,timeout=240) as response:return response.status,response.read()

if receipt.exists():
 result=json.loads(receipt.read_text())
 print('Using saved publication receipt; not creating a duplicate site.',flush=True)
if not receipt.exists() or updating:
 archive=ROOT/'publish/webfetch-sites.zip';boundary='----webfetch'+uuid.uuid4().hex
 parts=[]
 for key,value in [('mode','zip'),('title','Web Fetch 评测')]:
  parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
 parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="webfetch.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode()+archive.read_bytes()+b'\r\n')
 parts.append(f'--{boundary}--\r\n'.encode())
 print('Uploading validated report package',flush=True)
 endpoint='/api/sites/'+result['slug']+'/versions?expected_version='+expected if updating else '/api/sites'
 status,raw=request(endpoint,b''.join(parts),'multipart/form-data; boundary='+boundary)
 response=json.loads(raw);assert status==200,response
 if updating:
  result['last_update']=response
 else:
  result=response;assert result.get('slug'),result
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
