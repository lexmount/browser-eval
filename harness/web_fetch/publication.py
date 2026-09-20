"""Artifact Hub large-bundle transport; credentials remain with the caller."""
import json
import math
import socket
import time
import zipfile
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote


def updated_receipt(receipt, response):
    """Keep the receipt's concurrency baseline aligned with the activated version."""
    version = response.get('versionId')
    if not isinstance(version, str) or not version.startswith('ver_'):
        raise ValueError('Publication response omitted activated versionId')
    # Older receipts used ``version``. Keeping it alongside ``versionId`` leaves
    # two apparently authoritative baselines, one of which becomes stale here.
    current = {key: value for key, value in receipt.items() if key != 'version'}
    return {**current, 'versionId': version, 'last_update': response}


def put_file(request, path, data):
    """Retry only the identical idempotent staging PUT with bounded waits."""
    for attempt in range(4):
        try:
            return request(path, data, 'application/octet-stream', method='PUT')
        except HTTPError as error:
            if error.code >= 500:
                if attempt > 0:
                    raise
                delay=1.0
            elif error.code == 429:
                if attempt == 3:
                    raise
                header = error.headers.get('Retry-After', '') if error.headers else ''
                delay = 30.0
                if header:
                    try:
                        delay = float(header)
                    except ValueError:
                        try:
                            delay = parsedate_to_datetime(header).timestamp() - time.time()
                        except (TypeError, ValueError, OverflowError):
                            pass
                if not math.isfinite(delay) or delay > 120:
                    raise  # Never retry earlier than a longer server-requested wait.
                delay = max(1.0, delay)
            else:
                raise
            error.close()
            print(f'Upload failed with HTTP {error.code}; retrying the same file in {delay:.0f}s', flush=True)
            while delay > 0:
                interval = min(delay, 30)
                time.sleep(interval)
                delay -= interval
        except (TimeoutError, socket.timeout, URLError):
            if attempt == 3:
                raise
            delay=min(2**attempt,4)
            print(f'Upload response unavailable; retrying the same file in {delay}s',flush=True)
            time.sleep(delay)


def upload_large_archive(request, archive, slug=None, expected=None, title='Web Fetch 评测', *, max_workers=1):
    """Upload unpacked bytes to staging and commit the full tree once."""
    if slug and not expected:
        raise ValueError('An existing site requires an exported version baseline')

    def check_baseline():
        if slug:
            _, raw = request('/api/sites/' + quote(slug, safe=''))
            site = json.loads(raw)
            nested = site.get('site') if isinstance(site.get('site'), dict) else site
            observed = nested.get('currentVersionId') or (nested.get('version') or {}).get('id')
            if observed != expected:
                raise RuntimeError('Remote version changed; export and reconcile before publishing')

    check_baseline()
    with zipfile.ZipFile(archive) as bundle:
        files = [info for info in bundle.infolist() if not info.is_dir()]
        names = [info.filename for info in files]
        if not files or len(set(names)) != len(names) or 'index.html' not in names:
            raise ValueError('Bundle must contain unique files and a root index.html')
        for name in names:
            if (name.startswith('/') or '\\' in name
                    or any(part in ('', '..') or part.startswith('.')
                           or part.lower() == 'node_modules' for part in name.split('/'))
                    or str(PurePosixPath(name)) != name):
                raise ValueError('Unsafe bundle path: ' + name)
        query = '?expected_version=' + quote(expected, safe='') if expected else ''
        payload = {'slug': slug} if slug else {'title': title}
        _, raw = request('/api/uploads' + query, json.dumps(payload).encode(), 'application/json')
        session = json.loads(raw)['versionId']
        endpoint = '/api/uploads/' + quote(session, safe='')
        # The current service may choose the first uploaded HTML as the
        # version entry. Stage the root page before concurrent file uploads.
        entry = next(info for info in files if info.filename == 'index.html')
        put_file(request, endpoint + '/files/index.html', bundle.read(entry))
        remaining = [info for info in files if info.filename != 'index.html']
        if max_workers > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                pending = {
                    pool.submit(put_file, request,
                                endpoint + '/files/' + quote(info.filename, safe='/'),
                                bundle.read(info)): info.filename
                    for info in remaining
                }
                for index, future in enumerate(as_completed(pending), 2):
                    future.result()
                    if index % 100 == 0 or index == len(files):
                        print(f'Uploaded {index}/{len(files)} files', flush=True)
        else:
            for index, info in enumerate(remaining, 2):
                put_file(request, endpoint + '/files/' + quote(info.filename, safe='/'),
                         bundle.read(info))
                print(f'Uploaded {index}/{len(files)} files', flush=True)
        # The manual only guarantees atomic expected_version enforcement on
        # /versions and /edit. Also recheck before chunk commit; this read is
        # explicitly not claimed to be an atomic server-side lock.
        check_baseline()
        _, raw = request(endpoint + '/commit' + query, b'{}', 'application/json')
        if slug and json.loads(raw).get('slug', slug) != slug:
            raise RuntimeError('Publication returned an unexpected site')
        return raw
