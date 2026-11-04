"""Package and submit WCXB reports through their frozen profile and file inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lexbench_webfetch.paths import contained_path

from harness.record_io import canonical_json_sha256, json_bytes
from harness.reporting import links as report_links
from harness.reporting.publication import (
    PublicationClient,
    archive_inventory,
    atomic_write,
    package_archive,
    public_share_context,
)
from harness.reporting.publication_target import publication_base, require_publication_version
from harness.reporting.publish import StagedUpdate, publish_staged_update
from harness.web_fetch import wcxb_report_check
from harness.web_fetch.safety import evidence_tree_identity, reject_evidence_symlinks
from harness.data_home import data_home

INVENTORY = 'public-files.json'
CLIENT = PublicationClient('WCXB-Report-Publisher/1.0', authorization_first=False)


def profile_digest(profile: dict) -> str:
    # Publication advances only the live target version, never the report inputs.
    binding = {key: value for key, value in profile.items() if key != 'artifact_hub_expected_version'}
    return canonical_json_sha256(binding, ensure_ascii=True)


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
    profile_path = profile_path or authority_root / 'evaluation/wcxb/current-report.json'
    binding_path = profile_path.with_name('report-content-binding.json')
    reject_evidence_symlinks(profile_path)
    reject_evidence_symlinks(binding_path)
    profile = json.loads(profile_path.read_bytes())
    if profile.get('comparison_kind') != 'wcxb_frozen_html_to_markdown':
        raise ValueError('Publication authority is not WCXB')

    def validate():
        inventory(source, profile)
        wcxb_report_check.check(source, authority_root=authority_root, profile_path=profile_path)
        report_links.check(source)

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
    """Build the frozen package, then run its staged update against the profile's own target."""
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

    def unchanged_profile():
        if profile_path.read_bytes() != profile_bytes:
            raise ValueError('WCXB publication profile changed')

    def bind(version):
        profile['artifact_hub_expected_version'] = version
        atomic_write(profile_path, json_bytes(profile))

    version = publish_staged_update(
        StagedUpdate(
            label='WCXB', prefix='wcxb', client=CLIENT, base=base, slug=slug,
            expected_version=expected, share_path=share_path,
            title=f"WCXB 主内容提取报告 · {profile['case_count']} 页评测",
            archive=archive, archive_sha256=attestation['archive_sha256'],
            inputs=(source, profile_path, binding_path, proof), work=work,
            guard=unchanged_profile, bind=bind,
        ),
        transport=transport,
    )
    return {'slug': slug, 'versionId': version, 'verified_files': len(attestation['files']),
            'url': profile['artifact_hub_share_url']}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authority-root', type=Path, default=Path.cwd())
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
