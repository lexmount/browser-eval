"""A frozen publication plan is checked before authentication or network calls."""
import json

import pytest

from harness.web_search import publication, profiles
from harness.web_search.core import digest


def publication_files(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(publication, "ROOT", tmp_path)
    monkeypatch.setattr(publication, "current_selection", lambda *args: {"publication_target": "target.json"})
    report = tmp_path / "report"
    work = tmp_path / "release"
    report.mkdir()
    (work / "delivery").mkdir(parents=True)
    manifest = {"profile": {"cases": [{}]}, "rows": [{"attempts": [{}]}]}
    (report / "manifest.json").write_text(json.dumps(manifest))
    manifest_hash = digest((report / "manifest.json").read_bytes())
    files = {
        "report_manifest": (report / "manifest.json").read_bytes(),
        "visual_acceptance": json.dumps({"manifest_sha256": manifest_hash}).encode(),
        "delivery_index": b"{}", "relevance": b"{}", "independent_review": b"{}", "content": b"{}"
    }
    bindings = {}
    for name, raw in files.items():
        source = work / (name + ".json")
        source.write_bytes(raw)
        bindings[name] = {"path": str(source.relative_to(tmp_path)), "sha256": digest(raw)}
    manifest.update(relevance_sha256=bindings["relevance"]["sha256"], independent_review_sha256=bindings["independent_review"]["sha256"], content_sha256=bindings["content"]["sha256"])
    (report / "manifest.json").write_text(json.dumps(manifest))
    manifest_hash = digest((report / "manifest.json").read_bytes())
    (work / "report_manifest.json").write_bytes((report / "manifest.json").read_bytes())
    bindings["report_manifest"]["sha256"] = manifest_hash
    (work / "visual_acceptance.json").write_text(json.dumps({"manifest_sha256": manifest_hash}))
    bindings["visual_acceptance"]["sha256"] = digest((work / "visual_acceptance.json").read_bytes())
    (work / "delivery/delivery-index.json").write_bytes(b"{}")
    import zipfile
    with zipfile.ZipFile(work / "report.zip", "w") as archive:
        archive.writestr("index.html", "<html>Fixture</html>")
    (work / "inputs").mkdir()
    (work / "delivery/index.html").write_text("<html>Fixture</html>")
    target = {"bindings": bindings, "cases": 1, "logical_results": 1, "physical_calls": 1, "archive_sha256": digest((work / "report.zip").read_bytes())}
    target.update(base_url="https://reports.example", slug="site-one", versionId="ver_old", visibility="private", url="https://reports.example/s/site-one")
    (tmp_path / "target.json").write_text(json.dumps(target))
    instance = tmp_path / "instance.json"
    instance.write_text("{}")
    plan = {"instance": {"path": str(instance), "sha256": digest(instance.read_bytes())}, "expected_target": {k: target[k] for k in ("base_url", "slug", "versionId", "visibility")}, "target": target, "archive_sha256": target["archive_sha256"]}
    (work / "publication-plan.json").write_text(json.dumps(plan))
    # The identity validation itself is covered by CurrentProfileScenarios.
    monkeypatch.setattr(profiles, "publication_authority", lambda *args, **kwargs: target)
    return report, work


def test_publication_plan_uses_configured_data_home(tmp_path, monkeypatch):
    report, work = publication_files(tmp_path, monkeypatch)
    assert publication.verify_plan(report, work)["target"]["cases"] == 1


@pytest.mark.parametrize("relative", ["report.zip", "visual_acceptance.json", "relevance.json", "independent_review.json", "content.json", "delivery/delivery-index.json"])
def test_changed_release_inputs_stop_before_authentication(tmp_path, monkeypatch, relative):
    report, work = publication_files(tmp_path, monkeypatch)
    (work / relative).write_bytes(b"changed")
    from harness.web_fetch import report_publish
    monkeypatch.setattr(report_publish, "load_token", lambda **kwargs: pytest.fail("Authenticated before checking the frozen package"))
    with pytest.raises((ValueError, json.JSONDecodeError)):
        publication.publish(report, work)


@pytest.mark.parametrize("failure", [None, "upload-response"])
def test_search_publisher_recovers_one_activation_and_preserves_private_site(tmp_path, monkeypatch, failure):
    from tests.test_webfetch_report_publish import Service
    from harness.web_fetch import report_publish
    report, work = publication_files(tmp_path, monkeypatch)
    monkeypatch.setattr(publication.subprocess, "check_output", lambda *a, **kw: b"test-commit")
    monkeypatch.setattr(report_publish, "STAGED_UPLOAD_THRESHOLD", 0)
    class PrivateService(Service):
        def __call__(self, path, data=None, content_type=None, *, method=None, extra_headers=None):
            if path.endswith('/sharing'):
                assert data is None
                return 200, b'{"visibility":"private"}'
            return super().__call__(path, data, content_type, method=method, extra_headers=extra_headers)
    service = PrivateService(failure)
    if failure:
        with pytest.raises(OSError):
            publication.publish(report, work, transport=service)
    assert publication.publish(report, work, transport=service) == "https://reports.example/s/site-one"
    assert len(service.uploads) == 1
    assert not (work / "operation-pending.json").exists()
    target = json.loads((tmp_path / "target.json").read_bytes())
    assert target["versionId"] == "ver_new" and target["visibility"] == "private"
    # The binding step itself is also restartable after the shared publisher cleared its journal.
    assert publication.publish(report, work, transport=service) == target["url"]
    assert len(service.uploads) == 1


def test_prepare_freezes_new_counts_and_keeps_correction_provenance(tmp_path, monkeypatch):
    from harness.web_search import report as reports, visual, delivery
    monkeypatch.setenv('BROWSER_EVAL_DATA_HOME', str(tmp_path))
    repo = tmp_path / 'repo'; repo.mkdir()
    monkeypatch.setattr(publication, 'ROOT', repo)
    verify_inputs = profiles.verify_publication_inputs
    monkeypatch.setattr(profiles, 'verify_publication_inputs', lambda manifest, target, **kwargs: verify_inputs(manifest, target, root=repo))
    source, report, work = tmp_path / 'source', tmp_path / 'report', tmp_path / 'release'
    source.mkdir(); report.mkdir()
    (source / 'receipt.json').write_text('{}')
    (source / 'profile.json').write_text('{}')
    (repo / 'evaluation').mkdir()
    (repo / 'evaluation/profile.json').write_text('{}')
    binding = {'path': 'evaluation/profile.json', 'sha256': digest(b'{}')}
    selection = {'profile': binding, 'publication_target': 'target.json'}
    (repo / 'instance.json').write_text(json.dumps(selection))
    monkeypatch.setattr(publication, 'current_selection', lambda *args: selection)
    correction = tmp_path / 'correction.json'
    correction.write_text('{}')
    target = {'base_url': 'https://reports.example', 'slug': 'site-one', 'versionId': 'ver_old', 'visibility': 'private',
              'bindings': {'correction': {'path': 'correction.json', 'sha256': digest(b'{}')}},
              'correction_history': {'binding_names': ['correction'], 'superseded_physical_calls': 89}}
    (repo / 'target.json').write_text(json.dumps(target))
    monkeypatch.setattr(profiles, 'publication_authority', lambda *a, **kw: target)
    authored = {'title': {'zh': '接口评测'}}
    content = tmp_path / 'content.json'; content.write_text(json.dumps(authored))
    judgments = {'plan': {'case_ids': ['a'], 'scope': 'all_custom'}, 'labels': {'a': {'candidate': 3}}}
    audit = {key: 1 for key in ('case_total','case_reviewed','output_total','output_reviewed','candidate_total','candidate_reviewed')}
    audit.update(accepted_unreviewed_passes=0, adjudications={})
    for name, value in [('relevance-judgments.json', judgments), ('independent-review.json', audit)]:
        (report / name).write_text(json.dumps(value))
    manifest = {'profile': {'title': 'Source profile', 'methods': [{'id': 'method'}], 'cases': [{'id': 'a'}]},
                'authored': authored, 'rows': [{'attempts': [{}, {}]}],
                'receipt': {'certified': True, 'receipt_sha256': digest(b'{}'), 'profile_sha256': digest(b'{}')},
                'relevance_sha256': digest((report/'relevance-judgments.json').read_bytes()),
                'independent_review_sha256': digest((report/'independent-review.json').read_bytes()),
                'content_sha256': digest(content.read_bytes())}
    (report / 'manifest.json').write_text(json.dumps(manifest))
    proof = tmp_path / 'visual.json'; proof.write_text(json.dumps({'manifest_sha256': digest((report/'manifest.json').read_bytes())}))
    monkeypatch.setattr(reports, 'check', lambda *a: True)
    monkeypatch.setattr(visual, 'verify', lambda *a: True)
    def export(*args):
        dest = args[1]; dest.mkdir(); (dest/'index.html').write_text('<html>Verified fixture</html>'); (dest/'delivery-index.json').write_text('{}')
    monkeypatch.setattr(delivery, 'export', export)
    monkeypatch.setattr(delivery, 'verify', lambda *a: True)
    publication.prepare(report, proof, content, {'primary': source}, work, profile=repo/'instance.json')
    plan = publication.verify_plan(report, work)
    assert plan['title'] == '接口评测'
    assert plan['target']['physical_calls'] == 2
    assert plan['target']['correction_history']['superseded_physical_calls'] == 89
    assert 'correction' in plan['target']['bindings']
    assert plan['target']['independent_review']['reference_dispositions'] == {}
    assert (work/'report.zip').is_file()
