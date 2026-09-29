"""Publish the validated package to the user-specified Artifact Hub service."""
import hashlib
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

BASE=os.environ.get('ARTIFACT_HUB_BASE_URL','https://artifact-hub.app.lexmount.com').rstrip('/')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.publication_target import require_publication_site, require_publication_version
token=load_token(base_url=BASE)
receipt=(Path(sys.argv[sys.argv.index('--receipt')+1]) if '--receipt' in sys.argv
         else ROOT/'publish/artifact-hub-receipt.json')
updating='--update' in sys.argv
creating='--create' in sys.argv
requested_expected=(sys.argv[sys.argv.index('--expected-version')+1]
                    if '--expected-version' in sys.argv else None)
archive=(Path(sys.argv[sys.argv.index('--archive')+1]) if '--archive' in sys.argv
         else ROOT/'publish/webfetch-sites.zip')
source=(Path(sys.argv[sys.argv.index('--source')+1]) if '--source' in sys.argv
        else ROOT/'reports/webfetch')
title=(sys.argv[sys.argv.index('--title')+1] if '--title' in sys.argv else 'Web Fetch 评测')
if creating and updating:raise ValueError('Choose exactly one of --create or --update')
if updating and not receipt.exists():
 raise ValueError('--update requires an existing receipt')
expected=(require_publication_version(ROOT/'evaluation/current-report.json',requested_expected)
          if updating else None)
archive_sha256=None
if creating or updating:
 with archive.open('rb') as stream:archive_sha256=hashlib.file_digest(stream,'sha256').hexdigest()

def request(path,data=None,content_type=None,auth=True,method=None):
 headers={'User-Agent':'WebFetch-Report-Publisher/1.0'}
 if auth:headers['Authorization']='Bearer '+token
 if content_type:headers['Content-Type']=content_type
 if archive_sha256 and data is not None and method is None and (
         path.startswith('/api/uploads?') or path.startswith('/api/uploads/') and '/commit' in path):
  operation='start' if path.startswith('/api/uploads?') else 'commit'
  seed='|'.join((operation,archive_sha256,expected or '',path))
  headers['Idempotency-Key']='webfetch-'+operation+'-'+hashlib.sha256(seed.encode()).hexdigest()
 req=urllib.request.Request(BASE+path if path.startswith('/') else path,data=data,headers=headers,method=method)
 with urllib.request.urlopen(req,timeout=90 if method=='PUT' else 240) as response:return response.status,response.read()

if not creating and not receipt.exists():
 raise ValueError('Current report requires its existing site receipt; refusing to create a duplicate')
if creating and receipt.exists():
 raise ValueError('--create requires a new receipt path')
result={} if creating else json.loads(receipt.read_text())
if not creating:
 require_publication_site(result,ROOT/'evaluation/current-report.json',BASE)
 print('Using saved publication receipt; not creating a duplicate site.',flush=True)
if creating or updating:
 boundary='----webfetch'+uuid.uuid4().hex
 from scripts.check_publication_bundle import check
 from scripts.report_release_gate import check_report_release
 check_report_release(source,ROOT/'evaluation/current-report.json')
 check(archive,source)
 large_archive=archive.stat().st_size > 23*1024*1024
 parts=[]
 for key,value in [('mode','zip'),('title',title)]:
  parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
 if not large_archive:
  parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="webfetch.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode()+archive.read_bytes()+b'\r\n')
 parts.append(f'--{boundary}--\r\n'.encode())
 print('Uploading validated report package',flush=True)
 endpoint=('/api/sites' if creating else
           '/api/sites/'+result['slug']+'/versions?expected_version='+expected)
 if large_archive:
  # The service caps one-shot requests at 24 MB. Upload the unpacked files
  # into a staging session, then activate the complete tree in one commit.
  from harness.web_fetch.publication import upload_large_archive
  raw=upload_large_archive(request,archive,
      None if creating else result['slug'],None if creating else expected)
  status=200
 else:
  status,raw=request(endpoint,b''.join(parts),'multipart/form-data; boundary='+boundary)
 response=json.loads(raw);assert status==200,response
 if creating:
  result=response
  if not result.get('slug'):raise ValueError('Publication did not return a site slug')
 else:
  from harness.web_fetch.publication import updated_receipt
  result=updated_receipt(result,response)
 # Defend against legacy or service-returned aliases after an update. A create
 # response may still use ``version`` as its only initial-version field.
 if updating:result.pop('version',None)
 result['base_url']=BASE
 # Retain manageability without putting edit/claim credentials in console output.
 receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2));os.chmod(receipt,0o600)
 print(('Report created: ' if creating else 'Report updated: ')+result['slug'],flush=True)
if not result.get('share_url'):
 status,raw=request('/api/sites/'+result['slug']+'/shares',json.dumps({'policy':'public'}).encode(),'application/json')
 share=json.loads(raw);result['share_url']=share['url']
 receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2));os.chmod(receipt,0o600)
version=result.get('versionId',result.get('version'))
if isinstance(version,dict):version=version.get('id')
if not isinstance(version,str) or not version.startswith('ver_'):
 raise ValueError('Publication receipt omitted an activated version')
share_url=result['share_url'];parsed=urlsplit(share_url);base=urlsplit(BASE)
if parsed.scheme not in ('http','https') or parsed.netloc != base.netloc or not parsed.path:
 raise ValueError('Publication did not return a valid share URL for the target service')
# A successful commit/share response is the script's terminal condition. Do not
# read the public page or export back through HTTP; the caller opens share_url in
# a browser for the user-visible smoke check.
print(json.dumps({'slug':result['slug'],'versionId':version,'url':share_url},ensure_ascii=False),flush=True)
