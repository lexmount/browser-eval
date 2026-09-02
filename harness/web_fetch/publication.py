"""Artifact Hub large-bundle transport; credentials remain with the caller."""
import json
import zipfile
from pathlib import PurePosixPath
from urllib.parse import quote


def upload_large_archive(request, archive, slug=None, expected=None):
    """Upload unpacked bytes to staging and commit the full tree once."""
    if slug and not expected:
        raise ValueError('An existing site requires an exported version baseline')

    def check_baseline():
        if slug:
            _, raw = request('/api/sites/' + quote(slug, safe=''))
            if json.loads(raw)['version']['id'] != expected:
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
        payload = {'slug': slug} if slug else {'title': 'Web Fetch 评测'}
        _, raw = request('/api/uploads' + query, json.dumps(payload).encode(), 'application/json')
        session = json.loads(raw)['versionId']
        endpoint = '/api/uploads/' + quote(session, safe='')
        for index, info in enumerate(files, 1):
            request(endpoint + '/files/' + quote(info.filename, safe='/'),
                    bundle.read(info), 'application/octet-stream', method='PUT')
            print(f'Uploaded {index}/{len(files)} files', flush=True)
        # The manual only guarantees atomic expected_version enforcement on
        # /versions and /edit. Also recheck before chunk commit; this read is
        # explicitly not claimed to be an atomic server-side lock.
        check_baseline()
        _, raw = request(endpoint + '/commit' + query, b'{}', 'application/json')
        if slug and json.loads(raw).get('slug', slug) != slug:
            raise RuntimeError('Publication returned an unexpected site')
        return raw
