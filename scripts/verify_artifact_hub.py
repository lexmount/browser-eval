"""Read the existing report export and optionally verify exact uploaded files."""
import argparse
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import urllib.request
import zipfile
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.publication_target import DEFAULT_BASE


def verify_metadata(slug, base=DEFAULT_BASE):
    """Check the committed site record without downloading its published tree."""
    endpoint=base.rstrip('/')+'/api/sites/'+quote(slug,safe='')
    request=urllib.request.Request(
        endpoint,
        headers={'Authorization':'Bearer '+load_token(base_url=base)},
    )
    with urllib.request.urlopen(request,timeout=60) as response:
        data=json.load(response)
    version=(data.get('version') or {}).get('id')
    if not isinstance(version,str) or not version.startswith('ver_'):
        raise ValueError('Site metadata omitted the committed version id')
    files=data.get('files')
    if not isinstance(files,list) or not files:
        raise ValueError('Site metadata omitted the committed file inventory')
    return {
        'slug':data.get('slug'),
        'version':version,
        'files':len(files),
        'kind':data.get('kind'),
        'method':'authenticated site metadata only; no report files downloaded',
    }


def verify_public_files(slug, expected_archive, base=DEFAULT_BASE):
    endpoint=base+'/api/sites/'+quote(slug,safe='')
    def metadata():
        request=urllib.request.Request(endpoint,headers={'Authorization':'Bearer '+load_token(base_url=base)})
        with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
    before=metadata()
    with zipfile.ZipFile(expected_archive) as expected:
        wanted={};expected_html={}
        for info in expected.infolist():
            if info.is_dir():continue
            raw=expected.read(info)
            wanted[info.filename]=hashlib.sha256(raw).hexdigest()
            if info.filename.endswith('.html'):expected_html[info.filename]=raw
    if set(before['files'])!=set(wanted):raise ValueError('Remote file set differs')

    entry=before.get('file')
    authenticated_entry=False
    if entry in wanted and isinstance(before.get('content'),str):
        original=before['content'].encode()
        if hashlib.sha256(original).hexdigest()!=wanted[entry]:
            raise ValueError('Authenticated original entry differs: '+entry)
        authenticated_entry=True

    def injected_html_matches(name, served):
        """Accept only an exact submitted document plus a known Hub bootstrap."""
        original=expected_html[name]
        quoted_slug=re.escape(quote(slug,safe='').encode())
        # Newer deployments pin the injected base to the immutable served
        # version (``slug~opaque-version-token``). Keep this deliberately
        # narrow: the site slug must still match exactly and the suffix may
        # contain URL-path-safe token characters only.
        base_pattern=re.compile(
            rb'^<base href="/api/preview/'+quoted_slug+
            rb'(?:~[A-Za-z0-9._-]+)?/">'
        )

        def known_injection(injected):
            base=base_pattern.match(injected)
            if not base:return False
            bootstrap=injected[base.end():]
            # The service may add one or more bootstrap scripts. Do not normalize
            # or ignore arbitrary HTML: every injected byte must be one such script.
            while bootstrap:
                opening=b'<script data-artifact-bootstrap>'
                if not bootstrap.startswith(opening):return False
                end=bootstrap.find(b'</script>',len(opening))
                if end<0:return False
                bootstrap=bootstrap[end+len(b'</script>'):]
            return True

        # Older deployments prepend the bootstrap to the submitted document.
        if served.endswith(original):
            injected=served[:-len(original)] if original else served
            if known_injection(injected):return True

        # Current deployments insert it immediately after the literal <head> so
        # the submitted doctype and document element remain at the start.
        head_end=original.find(b'<head>')
        if head_end<0:return False
        head_end+=len(b'<head>')
        before,after=original[:head_end],original[head_end:]
        if not served.startswith(before):return False
        if after and not served.endswith(after):return False
        injected=served[len(before):len(served)-len(after) if after else None]
        return known_injection(injected)

    def digest(name):
        url=base+'/api/preview/'+quote(slug,safe='')+'/'+quote(name,safe='/')
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url,timeout=180) as response:
                    if name.endswith('.html'):
                        served=response.read()
                        digest=hashlib.sha256(served).hexdigest()
                        if digest==wanted[name]:return name,digest,'byte_identical'
                        if injected_html_matches(name,served):
                            return name,wanted[name],'injected_html'
                        return name,digest,'different'
                    digest=hashlib.sha256()
                    while chunk:=response.read(1024*1024):digest.update(chunk)
                return name,digest.hexdigest(),'byte_identical'
            except HTTPError as error:
                retryable=error.code>=500
                error.close()
                if not retryable or attempt==2:raise
            except (URLError,TimeoutError,socket.timeout,IncompleteRead):
                if attempt==2:raise
            time.sleep(2**attempt)
    with ThreadPoolExecutor(max_workers=2) as pool:
        actual={};verification={}
        for name,sha,layer in pool.map(digest,wanted):
            actual[name]=sha
            if sha!=wanted[name]:raise ValueError('Remote file differs: '+name)
            verification[name]=layer
            if len(actual)%20==0:print(f'Verified {len(actual)}/{len(wanted)} public files',flush=True)
    after=metadata()
    if before['version']['id']!=after['version']['id']:raise ValueError('Remote version changed during verification')
    exact=sum(layer=='byte_identical' for layer in verification.values())
    injected=sum(layer=='injected_html' for layer in verification.values())
    return {
        'version':after['version']['id'],
        'files':len(actual),
        'anonymous_byte_identical_files':exact,
        'anonymous_injected_html_files':injected,
        'anonymous_all_files_byte_identical':exact==len(actual),
        'authenticated_original_entry_verified':authenticated_entry,
        'submitted_bytes_verified_by_layer':exact+injected==len(actual),
        'method':('anonymous_file_bytes; injected HTML requires an exact submitted '
                  'document around validated Artifact Hub bootstrap scripts; authenticated '
                  'metadata verifies the original entry'),
    }


def download_export(request, destination, attempts=3):
    """Download one immutable site export, retrying transient gateway/read failures."""
    for attempt in range(attempts):
        path=None
        complete=False
        try:
            with urllib.request.urlopen(request,timeout=180) as response:
                version=response.headers.get('x-artifact-version')
                if not version or not version.startswith('ver_'):
                    raise ValueError('Export omitted version baseline')
                with tempfile.NamedTemporaryFile(dir=destination,prefix='remote-export-',suffix='.zip',delete=False) as stream:
                    path=Path(stream.name)
                    while chunk:=response.read(1024*1024):stream.write(chunk)
            complete=True
            return version,path
        except HTTPError as error:
            retryable=error.code>=500
            error.close()
            if not retryable or attempt==attempts-1:raise
        except (URLError,TimeoutError,socket.timeout,IncompleteRead):
            if attempt==attempts-1:raise
        finally:
            if path is not None and not complete:
                path.unlink(missing_ok=True)
        time.sleep(2**attempt)
    raise AssertionError('unreachable')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-archive',type=Path)
    parser.add_argument('--slug',required=True)
    parser.add_argument('--base-url',default=os.environ.get('ARTIFACT_HUB_BASE_URL',DEFAULT_BASE))
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--export',action='store_true',help='Diagnostic only: download and verify the complete site export')
    mode.add_argument('--public-files',action='store_true',help='Diagnostic only: download and verify every served file')
    args=parser.parse_args()
    base=args.base_url.rstrip('/')
    if args.public_files:
        if not args.expected_archive:parser.error('--public-files requires --expected-archive')
        print(json.dumps(verify_public_files(args.slug,args.expected_archive,base)),flush=True)
        return
    if not args.export:
        if args.expected_archive:parser.error('--expected-archive requires --export or --public-files')
        print(json.dumps(verify_metadata(args.slug,base)),flush=True)
        return
    request=urllib.request.Request(base+'/api/sites/'+quote(args.slug,safe='')+'/export',
        headers={'Authorization':'Bearer '+load_token(base_url=base)})
    (ROOT/'publish').mkdir(exist_ok=True)
    version,path=download_export(request,ROOT/'publish')
    with zipfile.ZipFile(path) as remote:
        actual={x.filename:hashlib.sha256(remote.read(x)).hexdigest() for x in remote.infolist() if not x.is_dir()}
        if args.expected_archive:
            with zipfile.ZipFile(args.expected_archive) as expected:
                wanted={x.filename:hashlib.sha256(expected.read(x)).hexdigest() for x in expected.infolist() if not x.is_dir()}
            if actual!=wanted:raise ValueError('Remote export differs from submitted archive')
    print(json.dumps({'version':version,'export_path':str(path),'files':len(actual),
        'submitted_bytes_verified':bool(args.expected_archive)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
