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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.config import load_runtime_env


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-archive',type=Path)
    parser.add_argument('--slug',default='UeZE1vfcEOrM')
    args=parser.parse_args()
    load_runtime_env()
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
