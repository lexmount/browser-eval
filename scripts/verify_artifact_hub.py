"""Read the existing report export and optionally verify exact uploaded files."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import urllib.request
import zipfile
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.config import load_runtime_env


def verify_public_files(slug, expected_archive):
    base='https://artifact-hub.dev.lexmount.net'
    endpoint=base+'/api/sites/'+quote(slug,safe='')
    def metadata():
        request=urllib.request.Request(endpoint,headers={'Authorization':'Bearer '+load_token()})
        with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
    before=metadata()
    with zipfile.ZipFile(expected_archive) as expected:
        wanted={x.filename:hashlib.sha256(expected.read(x)).hexdigest() for x in expected.infolist() if not x.is_dir()}
    if set(before['files'])!=set(wanted):raise ValueError('Remote file set differs')
    def digest(name):
        if name==before['file'] and isinstance(before.get('content'),str):
            return name,hashlib.sha256(before['content'].encode()).hexdigest()
        # HTML preview injects runtime markup, so compare original source via metadata.
        if name.endswith('.html'):raise ValueError('Additional HTML requires original-source export')
        url=base+'/api/preview/'+quote(slug,safe='')+'/'+quote(name,safe='/')
        for attempt in range(2):
            try:
                with urllib.request.urlopen(url,timeout=90) as response:
                    digest=hashlib.sha256()
                    while chunk:=response.read(1024*1024):digest.update(chunk)
                return name,digest.hexdigest()
            except HTTPError as error:
                if error.code<500 or attempt:raise
                error.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual={}
        for name,sha in pool.map(digest,wanted):
            actual[name]=sha
            if sha!=wanted[name]:raise ValueError('Remote file differs: '+name)
            if len(actual)%20==0:print(f'Verified {len(actual)}/{len(wanted)} public files',flush=True)
    after=metadata()
    if before['version']['id']!=after['version']['id']:raise ValueError('Remote version changed during verification')
    return {'version':after['version']['id'],'files':len(actual),'submitted_bytes_verified':True,'method':'public_files_and_original_entry'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-archive',type=Path)
    parser.add_argument('--slug',default='bWkRP157EtF6')
    parser.add_argument('--public-files',action='store_true',help='Verify individual served files when ZIP export is unavailable')
    args=parser.parse_args()
    load_runtime_env()
    if args.public_files:
        if not args.expected_archive:parser.error('--public-files requires --expected-archive')
        print(json.dumps(verify_public_files(args.slug,args.expected_archive)),flush=True)
        return
    request=urllib.request.Request('https://artifact-hub.dev.lexmount.net/api/sites/'+quote(args.slug,safe='')+'/export',
        headers={'Authorization':'Bearer '+load_token()})
    (ROOT/'publish').mkdir(exist_ok=True)
    with urllib.request.urlopen(request,timeout=180) as response:
        version=response.headers.get('x-artifact-version')
        if not version or not version.startswith('ver_'):
            raise ValueError('Export omitted version baseline')
        with tempfile.NamedTemporaryFile(dir=ROOT/'publish',prefix='remote-export-',suffix='.zip',delete=False) as stream:
            path=Path(stream.name)
            while chunk:=response.read(1024*1024):stream.write(chunk)
    with zipfile.ZipFile(path) as remote:
        actual={x.filename:hashlib.sha256(remote.read(x)).hexdigest() for x in remote.infolist() if not x.is_dir()}
        if args.expected_archive:
            with zipfile.ZipFile(args.expected_archive) as expected:
                wanted={x.filename:hashlib.sha256(expected.read(x)).hexdigest() for x in expected.infolist() if not x.is_dir()}
            if actual!=wanted:raise ValueError('Remote export differs from submitted archive')
    print(json.dumps({'version':version,'export_path':str(path),'files':len(actual),
        'submitted_bytes_verified':bool(args.expected_archive)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
