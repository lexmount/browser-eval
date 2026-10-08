"""Update the current WCXB Artifact Site from the validated report tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.publication import upload_large_archive, verify_published_tree
from harness.web_fetch.publication_target import require_publication_version
from scripts.check_report_links import check as check_links
from scripts.check_wcxb_report import check as check_report


def package(source: Path, archive: Path) -> dict[str, str]:
    files = sorted(path for path in source.rglob('*') if path.is_file() and path.name != '.generated-report')
    names = [path.relative_to(source).as_posix() for path in files]
    if 'index.html' not in names or any(path.is_symlink() for path in files):
        raise ValueError('WCXB publication requires a complete, link-free report tree')
    digests = {}
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path, name in zip(files, names):
            raw = path.read_bytes()
            digests[name] = hashlib.sha256(raw).hexdigest()
            bundle.writestr(name, raw)
    return digests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'reports/wcxb')
    args = parser.parse_args()
    source = args.source.resolve()
    check_report(source)
    check_links(source)
    profile_path = ROOT / 'evaluation/wcxb/current-report.json'
    profile = json.loads(profile_path.read_text())
    if profile['comparison_kind'] != 'wcxb_frozen_html_to_markdown':
        raise ValueError('Current publication target is not WCXB')
    base = profile['artifact_hub_base_url'].rstrip('/')
    slug = profile['artifact_hub_site']
    expected = require_publication_version(profile_path, profile['artifact_hub_expected_version'])
    token = load_token(base_url=base)
    private = Path.home() / '.browser-eval' / 'publication'
    private.mkdir(parents=True, exist_ok=True)
    archive = private / 'wcxb-current.zip'
    digests = package(source, archive)
    package_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    operation_hash = hashlib.sha256((package_hash + expected).encode()).hexdigest()
    journal = private / f'wcxb-update-{operation_hash[:24]}.json'
    if journal.exists():
        operation = json.loads(journal.read_text())
        if operation['expected_version'] != expected or operation['slug'] != slug:
            raise ValueError('An existing publication operation targets another version or site')
    else:
        operation = {'slug': slug, 'expected_version': expected, 'archive_sha256': package_hash,
                     'start_key': f'wcxb-start-{operation_hash[:32]}',
                     'commit_key': f'wcxb-commit-{operation_hash[:32]}'}
        journal.write_text(json.dumps(operation, indent=2))
        os.chmod(journal, 0o600)

    def request(path, data=None, content_type=None, auth=True, method=None):
        headers = {'User-Agent': 'WCXB-Report-Publisher/1.0'}
        if auth:
            headers['Authorization'] = 'Bearer ' + token
        if content_type:
            headers['Content-Type'] = content_type
        if path.startswith('/api/uploads') and method != 'PUT' and data is not None:
            headers['Idempotency-Key'] = (operation['commit_key'] if path.endswith('/commit?expected_version=' + quote(expected, safe=''))
                                          else operation['start_key'])
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=240) as response:
            return response.status, response.read()

    _, raw = request('/api/sites/' + quote(slug, safe=''))
    remote = json.loads(raw)
    if remote['version']['id'] != expected:
        raise ValueError('Remote WCXB version changed; reconcile before updating')
    response = json.loads(upload_large_archive(request, archive, slug, expected, max_workers=4))
    version = response.get('versionId')
    if response.get('slug', slug) != slug or not isinstance(version, str) or not version.startswith('ver_'):
        raise ValueError('Update response did not bind the expected WCXB site and version')

    _, raw = request('/api/sites/' + quote(slug, safe=''))
    published = json.loads(raw)
    if published['version']['id'] != version or published['version']['entry'] != 'index.html':
        raise ValueError('Published WCXB version does not open at the report home page')

    _, exported = request('/api/sites/' + quote(slug, safe='') + '/export')
    verify_published_tree(source, exported)
    title = 'WCXB 五款浏览器主内容提取报告 · 504 页评测'
    request('/api/sites/' + quote(slug, safe=''), json.dumps({'title': title}, ensure_ascii=False).encode(),
            'application/json', method='PATCH')
    profile['artifact_hub_expected_version'] = version
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'slug': slug, 'versionId': version, 'verified_files': len(digests),
                      'url': profile['artifact_hub_share_url']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
