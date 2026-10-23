"""Package and submit WCXB reports through their frozen profile and file inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import quote

from harness.reporting.publication import (
    PublicationOperation, archive_inventory, atomic_write, json_bytes, package_archive,
    public_share_context, snapshot_publication,
)
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.publication import upload_large_archive
from harness.web_fetch.publication_target import publication_base, require_publication_version
from harness.web_fetch.report_publish import response_json
from lexbench_webfetch.paths import contained_path
from harness.web_fetch.safety import evidence_tree_identity, reject_evidence_symlinks
from harness.web_fetch.storage import data_home

INVENTORY = 'public-files.json'


def profile_digest(profile: dict) -> str:
    # Publication advances only the live target version, never the report inputs.
    binding = {key: value for key, value in profile.items() if key != 'artifact_hub_expected_version'}
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_inventory(report: Path, profile: dict) -> None:
    """Called only after the maintained builder has finished its private staging tree."""
    files = {name: digest for name, digest in evidence_tree_identity(report).items()
             if name not in {INVENTORY, '.generated-report'}}
    atomic_write(report / INVENTORY, json_bytes({
        'schema_version': 1, 'kind': 'wcxb_public_files',
        'profile_sha256': profile_digest(profile), 'files': files,
    }))


def inventory(report: Path, profile: dict) -> dict[str, str]:
    path = contained_path(report, INVENTORY, reject_symlinks=True)
    if not path.is_file():
        raise ValueError('WCXB public inventory is missing; rebuild the report')
    declaration = json.loads(path.read_bytes())
    if (declaration.get('schema_version') != 1 or declaration.get('kind') != 'wcxb_public_files'
            or declaration.get('profile_sha256') != profile_digest(profile)):
        raise ValueError('WCXB public inventory differs from its profile; rebuild the report')
    actual = evidence_tree_identity(report)
    expected = declaration.get('files')
    if (not isinstance(expected, dict) or 'index.html' not in expected
            or expected != {key: value for key, value in actual.items()
                            if key not in {INVENTORY, '.generated-report'}}):
        raise ValueError('WCXB report contains missing, changed or undeclared public files')
    return {**expected, INVENTORY: actual[INVENTORY]}


def package(source: Path, archive: Path, receipt: Path, *, authority_root: Path, profile_path: Path | None = None) -> dict:
    from harness.reporting.links import check as check_links
    from harness.web_fetch.wcxb_report_check import check as check_report

    profile_path = profile_path or authority_root / 'evaluation/wcxb/current-report.json'
    binding_path = profile_path.with_name('report-content-binding.json')
    reject_evidence_symlinks(profile_path)
    reject_evidence_symlinks(binding_path)
    profile = json.loads(profile_path.read_bytes())
    if profile.get('comparison_kind') != 'wcxb_frozen_html_to_markdown':
        raise ValueError('Publication authority is not WCXB')

    def validate():
        inventory(source, profile)
        check_report(source, authority_root=authority_root, profile_path=profile_path)
        check_links(source)

    def attest(staged):
        files = inventory(source, profile)
        if archive_inventory(staged) != files:
            raise ValueError('WCXB archive differs from declared public files')
        return {'schema_version': 1, 'kind': 'wcxb_package',
                'archive_sha256': hashlib.sha256(staged.read_bytes()).hexdigest(),
                'profile_sha256': hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                'content_binding_sha256': hashlib.sha256(binding_path.read_bytes()).hexdigest(),
                'files': files}

    return package_archive(source, archive, receipt, inputs=[profile_path, binding_path],
                           validate=validate, attest=attest, excluded=['.generated-report'])


def publish(*, source: Path, authority_root: Path, work: Path, transport=None, profile_path: Path | None = None) -> dict:
    """Resume the same staged update and verify every deployed byte before binding it."""
    profile_path = profile_path or authority_root / 'evaluation/wcxb/current-report.json'
    binding_path = profile_path.with_name('report-content-binding.json')
    reject_evidence_symlinks(profile_path)
    profile_bytes = profile_path.read_bytes()
    profile = json.loads(profile_bytes)
    base = publication_base(profile_path, document=profile)
    expected = require_publication_version(profile_path, document=profile)
    slug = profile.get('artifact_hub_site')
    if not isinstance(slug, str) or not slug:
        raise ValueError('WCXB publication requires a bound site')
    share_path, _ = public_share_context(base, profile.get('artifact_hub_share_url', ''))
    archive, proof = work / 'wcxb-current.zip', work / 'wcxb-package.json'
    attestation = package(source, archive, proof, authority_root=authority_root, profile_path=profile_path)
    identity = {'base_url': base, 'slug': slug,
                'archive_sha256': attestation['archive_sha256']}
    # The release identifies the operation; its mutable live version is a guard.
    operation_hash = hashlib.sha256(json_bytes(identity)).hexdigest()
    journal = work / f'wcxb-update-{operation_hash[:24]}.json'
    reject_evidence_symlinks(journal)
    operation = (json.loads(journal.read_bytes()) if journal.exists()
                 else {**identity, 'expected_version': expected})
    if any(operation.get(key) != value for key, value in identity.items()):
        raise ValueError('WCXB publication journal targets another release')
    if expected not in {operation.get('expected_version'), operation.get('versionId')}:
        raise ValueError('WCXB publication version differs from its pending operation')
    expected = operation['expected_version']

    def save():
        atomic_write(journal, json_bytes(operation))
        os.chmod(journal, 0o600)

    save()
    with snapshot_publication(archive, inputs=[source, profile_path, binding_path, proof]) as release:
        def request(path, data=None, content_type=None, auth=True, method=None):
            release.check()
            if profile_path.read_bytes() != profile_bytes:
                raise ValueError('WCXB publication profile changed')
            if not path.startswith('/') or path.startswith('//'):
                raise ValueError('WCXB request must stay on its configured service')
            headers = {'User-Agent': 'WCXB-Report-Publisher/1.0'}
            if auth:
                # Synthetic transports never load a real account credential.
                if transport is None:
                    headers['Authorization'] = 'Bearer ' + load_token(base_url=base)
            if content_type:
                headers['Content-Type'] = content_type
            if path.startswith('/api/uploads') and method != 'PUT' and data is not None:
                phase = 'commit' if '/commit?' in path else 'start'
                headers['Idempotency-Key'] = f'wcxb-{phase}-{operation_hash[:32]}'
            if transport is not None:
                status, raw = transport(path, data, content_type, auth=auth, method=method, headers=headers)
            else:
                req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=240) as response:
                    status, raw = response.status, response.read()
            release.check()
            if status == 404 and path.startswith('/api/operations/'):
                return status, raw
            if not 200 <= status < 300:
                raise RuntimeError(f'WCXB publication HTTP {status}: {path}')
            return status, raw

        site_path = '/api/sites/' + quote(slug, safe='')
        _, raw = request(site_path)
        remote = json.loads(raw)
        version = operation.get('versionId')
        if version is None:
            if remote['version']['id'] != expected:
                recovered = PublicationOperation(journal, 'wcxb-').completed(
                    f'wcxb-commit-{operation_hash[:32]}', request=request, decode=response_json,
                )
                if recovered is None:
                    raise ValueError('Remote WCXB version changed; reconcile before updating')
                response = response_json(*recovered, operation='WCXB commit recovery')
            else:
                response = json.loads(upload_large_archive(request, release.archive, slug, expected, max_workers=4))
            version = response.get('versionId')
            if (response.get('slug', slug) != slug or not isinstance(version, str)
                    or not version.startswith('ver_')):
                raise ValueError('WCXB upload returned another site or an invalid version')
            operation['versionId'] = version
            save()
        elif not isinstance(version, str) or not version.startswith('ver_'):
            raise ValueError('WCXB journal has an invalid activated version')
        _, raw = request(site_path)
        published = json.loads(raw)
        if published['version']['id'] != version or published['version']['entry'] != 'index.html':
            raise ValueError('Published WCXB site does not bind the activated home page')
        _, exported = request(site_path + '/export')
        if archive_inventory(exported) != archive_inventory(release.archive):
            raise ValueError('Deployed WCXB bytes differ from the validated package')
        _, shared = request(share_path, auth=False)
        if not shared:
            raise ValueError('WCXB public share page is empty')
        title = f"WCXB 主内容提取报告 · {profile['case_count']} 页评测"
        request(site_path, json.dumps({'title': title}, ensure_ascii=False).encode(),
                'application/json', method='PATCH')
        release.check()
        profile['artifact_hub_expected_version'] = version
        atomic_write(profile_path, json_bytes(profile))
    operation['verified'] = True
    save()
    return {'slug': slug, 'versionId': version, 'verified_files': len(attestation['files']),
            'url': profile['artifact_hub_share_url']}


def main(argv=None, *, root: Path | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authority-root', type=Path, default=root or Path.cwd())
    parser.add_argument('--source', type=Path)
    parser.add_argument('--profile', type=Path, required=True, help='external WCXB instance profile')
    parser.add_argument('--package-only', action='store_true', help='validate and create a local archive without publishing')
    parser.add_argument('--work', type=Path, default=data_home() / 'publication')
    args = parser.parse_args(argv)
    authority = args.authority_root.absolute()
    if args.package_only:
        archive, receipt = args.work / 'wcxb-current.zip', args.work / 'wcxb-package.json'
        proof = package(args.source or authority / 'reports/wcxb', archive, receipt,
                        authority_root=authority, profile_path=args.profile)
        print(json.dumps({'archive': str(archive), 'archive_sha256': proof['archive_sha256'],
                          'files': len(proof['files'])}))
        return
    print(json.dumps(publish(source=args.source or authority / 'reports/wcxb',
                             authority_root=authority, work=args.work, profile_path=args.profile), ensure_ascii=False))


if __name__ == '__main__':
    main()
