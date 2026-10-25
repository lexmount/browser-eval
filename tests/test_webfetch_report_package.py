"""Publication scenarios independent of a historical benchmark size or dataset ID."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from harness.web_fetch import report_package


def report_tree(root, *, sampled=False):
    source = root / "report"
    (source / "assets").mkdir(parents=True)
    (source / "assets/style.css").write_text("body { color: black; }")
    (source / "assets/report.js").write_text("window.addEventListener('report-ready',initReport);")
    (source / "index.html").write_text(
        '<html lang="zh-CN"><body><a href="detail.html">详情</a></body></html>'
    )
    (source / "detail.html").write_text('<html><body><a href="index.html">返回</a></body></html>')
    dataset = {"id": "new-browser-cohort", "cases": [{"id": "alpha"}, {"id": "beta"}]}
    records = []
    if sampled:
        dataset.update(
            comparison={"kind": "webmainbench_frozen_html_to_markdown"},
            coverage_plan={"static_html": ["first", "second"]},
            sample_manifest_sha256="frozen-sample",
        )
        for provider in ("first", "second"):
            for case in ("alpha", "beta"):
                records.append(
                    {
                        "attempt_id": provider + "-" + case,
                        "provider": provider,
                        "case_id": case,
                        "artifact_path": provider + "-" + case + ".txt",
                    }
                )
                (source / records[-1]["artifact_path"]).write_text("measured output")
        review = {
            "status": "passed",
            "failures": [],
            "sample_case_count": 2,
            "providers": ["first", "second"],
            "sample_manifest_sha256": "frozen-sample",
        }
        (source / "result-review.json").write_text(json.dumps(review))
    else:
        (source / "reliability.json").write_text('{"complete":true}')
    (source / "dataset.json").write_text(json.dumps(dataset))
    (source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
    return source, root / "published.zip"


@pytest.mark.parametrize("damage", [None, "missing", "failure-only", "partial", "stale-binding"])
def test_full_review_cannot_be_bypassed_by_stale_complete_flag(tmp_path, damage):
    source, archive = report_tree(tmp_path)
    dataset = json.loads((source / "dataset.json").read_text())
    dataset["comparison"] = {
        "require_independent_result_review": True,
        "independent_result_review_scope": "all",
    }
    (source / "dataset.json").write_text(json.dumps(dataset))
    records = [{"attempt_id": "native-" + name, "provider": "native",
                "case_id": name, "artifact_path": name + ".txt",
                "ok": True, "metrics": {"content_pass": True}}
               for name in ("alpha", "beta")]
    for row in records:
        (source / row["artifact_path"]).write_text("The declared synthetic answer.")
    (source / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records))
    review = {
        "schema_version": 1,
        "bindings": {"dataset_sha256": hashlib.sha256(
                         (source / "dataset.json").read_bytes()).hexdigest(),
                     "results_sha256": hashlib.sha256(
                         (source / "results.jsonl").read_bytes()).hexdigest()},
        "review_protocol": {"scope": "all", "machine_judgments_withheld": True},
        "attempts": [{"attempt_id": row["attempt_id"], "machine_verdict": "pass",
                      "agent_verdict": "pass", "final_verdict": "pass"}
                     for row in records],
    }
    if damage == "failure-only":
        review["review_protocol"]["scope"] = "machine-failures"
        review["attempts"] = []
    elif damage == "partial":
        review["attempts"].pop()
    elif damage == "stale-binding":
        review["bindings"]["results_sha256"] = "0" * 64
    if damage != "missing":
        (source / "result-review.json").write_text(json.dumps(review))
    archive.write_bytes(b"previous verified release")
    if damage:
        with pytest.raises(ValueError):
            report_package.build(source, archive)
        assert archive.read_bytes() == b"previous verified release"
    else:
        report_package.build(source, archive)
        assert zipfile.is_zipfile(archive)


def test_declared_small_matrix_packages_through_public_command(tmp_path):
    source, archive = report_tree(tmp_path, sampled=True)
    entry = Path(__file__).resolve().parents[1] / "scripts/package_artifact_hub.py"
    subprocess.run(
        [sys.executable, str(entry), str(source), str(archive)], check=True, capture_output=True
    )
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["architecture"]["logical_routes"] == 2
        assert (
            bundle.read("loader.js")
            == Path(report_package.__file__)
            .with_name("assets")
            .joinpath("report-loader.js")
            .read_bytes()
        )


def test_retained_success_and_failure_rerun_can_be_packaged_together(tmp_path):
    source, archive = report_tree(tmp_path, sampled=True)
    rows = [json.loads(line) for line in (source / 'results.jsonl').read_text().splitlines()]
    for row in rows:
        row['provider_metadata'] = {'binary_sha256':
            'retained-build' if row['case_id'] == 'alpha' else 'repair-build'}
    (source / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    report_package.build(source, archive)
    assert archive.is_file()


@pytest.mark.parametrize("damage", [None, "missing", "changed", "dropped-binding", "wrong-label"])
def test_ordinary_package_and_publication_require_the_bound_benchmark_edition(tmp_path, damage):
    from lexbench_webfetch.scoring import score_attempt
    from lexbench_webfetch.versions import create_release
    from harness.web_fetch.publication_check import check

    source, archive = report_tree(tmp_path, sampled=True)
    dataset_path = source / "dataset.json"
    dataset = json.loads(dataset_path.read_text())
    for case in dataset["cases"]:
        case["url"] = "https://example.test/" + case["id"]
    dataset_path.write_text(json.dumps(dataset))
    cases = {case["id"]: case for case in dataset["cases"]}
    rows = [json.loads(line) for line in (source / "results.jsonl").read_text().splitlines()]
    for row in rows:
        row["ok"] = True
        row["metrics"] = score_attempt(cases[row["case_id"]],
                                       (source / row["artifact_path"]).read_bytes(), ok=True)
    (source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    release = create_release(dataset, benchmark="sample", version="1.0.0")
    sidecar = source / "benchmark-release.json"
    sidecar.write_text(json.dumps(release))
    manifest = {"benchmark_release": {"path": sidecar.name,
        "sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
        "benchmark": "sample", "version": "1.0.0"}}
    (source / "report-manifest.json").write_text(json.dumps(manifest))
    home = source / "index.html"
    home.write_text(home.read_text() + '<a data-benchmark-release="1.0.0" '
                    'href="benchmark-release.json">sample v1.0.0</a>')
    report_package.build(source, archive)
    check(archive, source)
    before = archive.read_bytes()
    if damage is None:
        return
    if damage == "missing":
        sidecar.unlink()
    elif damage == "changed":
        release["version"] = "99.0.0"
        sidecar.write_text(json.dumps(release))
    elif damage == "dropped-binding":
        (source / "report-manifest.json").write_text('{}')
    else:
        home.write_text(home.read_text().replace('data-benchmark-release="1.0.0"',
                                                 'data-benchmark-release="99.0.0"'))
    with pytest.raises(ValueError, match="[Bb]enchmark"):
        report_package.build(source, archive)
    assert archive.read_bytes() == before
    with pytest.raises(ValueError, match="[Bb]enchmark"):
        check(archive, source)


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "extra",
        "wrong-provider",
        "review-count",
        "review-providers",
        "review-binding",
        "unreviewed",
    ],
)
def test_incomplete_or_misbound_matrix_preserves_existing_archive(tmp_path, damage):
    source, archive = report_tree(tmp_path, sampled=True)
    rows = [json.loads(line) for line in (source / "results.jsonl").read_text().splitlines()]
    review_path = source / "result-review.json"
    review = json.loads(review_path.read_text())
    if damage == "missing":
        rows.pop()
    elif damage == "duplicate":
        rows[-1] = rows[0]
    elif damage == "extra":
        rows.append(rows[0])
    elif damage == "wrong-provider":
        rows[-1]["provider"] = "undeclared"
    elif damage == "review-count":
        review["sample_case_count"] = 200
    elif damage == "review-providers":
        review["providers"] = ["first"]
    elif damage == "review-binding":
        review["sample_manifest_sha256"] = "different-sample"
    else:
        review["status"] = "pending"
    (source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    review_path.write_text(json.dumps(review))
    archive.write_bytes(b"previous verified release")
    with pytest.raises(ValueError):
        report_package.build(source, archive)
    assert archive.read_bytes() == b"previous verified release"
    assert not list(tmp_path.glob(".webfetch-package-*"))


def test_optimized_python_still_rejects_broken_links(tmp_path):
    source, archive = report_tree(tmp_path)
    (source / "index.html").write_text('<a href="absent.html">Broken target</a>')
    archive.write_bytes(b"previous verified release")
    entry = Path(__file__).resolve().parents[1] / "scripts/package_artifact_hub.py"
    rejected = subprocess.run(
        [sys.executable, "-O", str(entry), str(source), str(archive)],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0 and "absent.html" in rejected.stderr
    assert archive.read_bytes() == b"previous verified release"


@pytest.mark.parametrize("changed", ["source", "destination"])
def test_mutation_during_packaging_is_not_installed(tmp_path, monkeypatch, changed):
    source, archive = report_tree(tmp_path)
    archive.write_bytes(b"previous verified release")
    project = report_package.project_file
    changed_once = False

    def mutate(name, data):
        nonlocal changed_once
        if not changed_once:
            changed_once = True
            if changed == "source":
                (source / "new-evidence.txt").write_text("new evidence")
            else:
                archive.write_bytes(b"independently installed release")
        return project(name, data)

    monkeypatch.setattr(report_package, "project_file", mutate)
    with pytest.raises(ValueError, match="changed during packaging"):
        report_package.build(source, archive)
    assert archive.read_bytes() == (
        b"previous verified release" if changed == "source" else b"independently installed release"
    )
    assert not list(tmp_path.glob(".webfetch-package-*"))


@pytest.mark.parametrize("linked", ["file", "directory", "source", "destination"])
def test_symlinks_cannot_enter_publication(tmp_path, linked):
    source, archive = report_tree(tmp_path)
    if linked == "file":
        (source / "linked.txt").symlink_to(source / "index.html")
    elif linked == "directory":
        (source / "linked").symlink_to(source / "assets", target_is_directory=True)
    elif linked == "source":
        alias = tmp_path / "alias"
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    else:
        original = tmp_path / "original.zip"
        original.write_bytes(b"original")
        archive.symlink_to(original)
    with pytest.raises(ValueError, match="[Ss]ymlink|regular file"):
        report_package.build(source, archive)


def test_importing_command_does_not_parse_arguments_or_create_files(tmp_path):
    entry = Path(__file__).resolve().parents[1] / "scripts/package_artifact_hub.py"
    code = 'import runpy,sys; sys.argv=["package", "--unknown"]; runpy.run_path(sys.argv[1])'
    # Supply the file independently so command arguments stay deliberately invalid.
    code = code.replace("sys.argv[1]", repr(str(entry)))
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, capture_output=True)
    assert not list(tmp_path.iterdir())


def test_default_paths_follow_explicit_command_root_from_another_directory(tmp_path, monkeypatch):
    source, _ = report_tree(tmp_path)
    target = tmp_path / "reports/webfetch"
    target.parent.mkdir()
    source.rename(target)
    other = tmp_path / "another-working-directory"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setattr(sys, "argv", ["package"])
    report_package.main(root=tmp_path)
    assert (tmp_path / "publish/webfetch-sites.zip").is_file()
    assert not (other / "publish").exists()


def test_inline_acceptance_contract_survives_missing_file_and_cached_repack(tmp_path):
    from harness.reporting.packs import read_packed_file, write_packs
    from harness.web_fetch.publication_check import check
    source, archive = report_tree(tmp_path, sampled=True)
    records = [json.loads(line) for line in (source/'results.jsonl').read_text().splitlines()]
    for row in records:
        row['output_sha256'] = hashlib.sha256((source/row['artifact_path']).read_bytes()).hexdigest()
    (source/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
    (source/'attempts').mkdir()
    target = 'attempts/first-alpha.html'
    inline = '<pre data-kind="contract">{&quot;minimum&quot;:1}</pre>'
    (source/target).write_text('<html><body>'+inline+'<pre data-kind="metadata">{}</pre></body></html>')
    report_package.build(source,archive)
    check(archive,source)
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        expected = read_packed_file(bundle,target)
        assert inline.encode() in expected
        assert b'data-file="contracts/alpha.json"' not in expected
        cached = tmp_path/'cached'
        cached.mkdir()
        for name in ('index.html','style.css','report.js','loader.js'):
            (cached/name).write_bytes(bundle.read(name))
        blocks = {}
        for name,entry in manifest['files'].items():
            raw = read_packed_file(bundle,name)
            if name == target:
                raw = raw.replace(inline.encode(),b'<pre data-file="contracts/alpha.json"></pre>')
            digest = hashlib.sha256(raw).hexdigest()
            blocks[digest] = raw
            entry.update(parts=[digest] if raw else [],size=len(raw),sha256=digest)
        manifest['blocks'],_ = write_packs(blocks.items(),cached,limit=4*1024*1024,
                                           pattern='data/{index:03d}.gz',compresslevel=9)
        (cached/'manifest.json').write_text(json.dumps(manifest))
    report_package.build(source,archive,cached_content=cached)
    check(archive,source)
    with zipfile.ZipFile(archive) as bundle:
        assert read_packed_file(bundle,target) == expected
        receipt = json.loads(read_packed_file(bundle,'publication-privacy.json'))
        assert receipt['packaging_projections'][0]['path'] == target


def test_data_attachments_preserve_report_and_require_fixed_published_versions(tmp_path,monkeypatch):
    from harness.web_fetch.publication_check import check
    source,archive = report_tree(tmp_path,sampled=True)
    rows = [json.loads(line) for line in (source/'results.jsonl').read_text().splitlines()]
    for row in rows:
        row['output_sha256'] = hashlib.sha256((source/row['artifact_path']).read_bytes()).hexdigest()
    (source/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    partition = report_package.partition_content
    monkeypatch.setattr(report_package,'partition_content',
                        lambda content,output:partition(content,output,max_files=5))
    directory = tmp_path/'parts'
    report_package.build(source,archive,parts_directory=directory)
    plan_path = directory/'parts.json'
    parts = report_package.part_archives(plan_path)
    assert parts
    with pytest.raises(ValueError,match='attachment inventory'):
        check(archive,source)
    with pytest.raises(ValueError,match='not bound'):
        check(archive,source,external_archives=parts)
    check(archive,source,external_archives=parts,allow_pending_external=True)
    publications = {label:{'base_url':'https://artifact.example','site_slug':'data-'+label,
                           'version_id':'ver_fixed_snapshot'} for label in parts}
    report_package.bind_data_parts(archive,plan_path,publications)
    bound = archive.read_bytes()
    report_package.bind_data_parts(archive,plan_path,publications)
    assert archive.read_bytes() == bound
    check(archive,source,external_archives=parts)
    label,next_archive = next(iter(parts.items()))
    damaged = tmp_path/'damaged.zip'
    with zipfile.ZipFile(next_archive) as original,zipfile.ZipFile(damaged,'w') as target:
        for name in original.namelist():
            raw = original.read(name)
            target.writestr(name,raw+b'tampered' if name != 'index.html' else raw)
    with pytest.raises(ValueError,match='attachment pack differs'):
        check(archive,source,external_archives={**parts,label:damaged})


def test_cached_privacy_refresh_keeps_source_identity(tmp_path):
    from harness.reporting.packs import write_compact_packs, read_packed_manifest, reconstruct_packed_file
    import gzip
    source = tmp_path/'source'
    cached = tmp_path/'cached'
    output = tmp_path/'output'
    source.mkdir()
    (source/'results.jsonl').write_text('')
    name = 'native.response.txt'
    raw = json.dumps({'result':'Example: {"clientSecret": "string"}'}).encode()
    (source/name).write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    privacy = json.dumps({'files':[], 'scored_outputs_privacy_projected':0}).encode()
    write_compact_packs([(name,{'source_sha256':digest,'size':len(raw),'sha256':digest},raw),
                         ('publication-privacy.json',{'size':len(privacy),'sha256':hashlib.sha256(privacy).hexdigest()},privacy)],cached)
    for filename in ('index.html','style.css','report.js'):
        (cached/filename).write_text('')
    report_package._compact_cached_content(cached,output,source=source,refresh_privacy=True)
    manifest = read_packed_manifest(lambda path:(output/path).read_bytes())
    def read(path):
        return reconstruct_packed_file(manifest,path,lambda pack:gzip.decompress((output/pack).read_bytes()))
    assert (source/name).read_bytes() == raw
    assert b'string' not in read(name)
    assert manifest['files'][name]['source_sha256'] == digest
    receipt = json.loads(read('publication-privacy.json'))['files'][0]
    assert receipt['original_sha256'] == digest
    assert receipt['published_sha256'] == hashlib.sha256(read(name)).hexdigest()


def test_cached_results_refresh_decoded_public_representation_from_source(tmp_path):
    import gzip
    from harness.reporting.packs import write_compact_packs, read_packed_manifest, reconstruct_packed_file
    from harness.web_fetch.publication_privacy import project_file, publication_file_risk
    source,cached,output = (tmp_path/name for name in ('source','cached','output'))
    source.mkdir()
    original = (json.dumps({'ok':True,'metrics':{'task_pass':True},
        'provider_metadata':{'raw_response':'{"clientSecret":"string"}'}})+'\n').encode()
    (source/'results.jsonl').write_bytes(original)
    expected,disclosure = project_file('results.jsonl',original)
    legacy = json.loads(expected)
    legacy['provider_metadata']['raw_response'] = '{"clientSecret":"[REDACTED]"}'
    legacy = (json.dumps(legacy)+'\n').encode()
    assert legacy != expected and publication_file_risk('results.jsonl',legacy) is None
    disclosure.update(published_sha256=hashlib.sha256(legacy).hexdigest(),published_bytes=len(legacy))
    privacy=json.dumps({'files':[disclosure],'scored_outputs_privacy_projected':0}).encode()
    entries=[]
    for name,raw in [('results.jsonl',legacy),('publication-privacy.json',privacy)]:
        metadata={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        if name=='results.jsonl':metadata['source_sha256']=hashlib.sha256(original).hexdigest()
        entries.append((name,metadata,raw))
    write_compact_packs(entries,cached)
    for name in ('index.html','style.css','report.js'):(cached/name).write_text('')
    report_package._compact_cached_content(cached,output,source=source)
    manifest=read_packed_manifest(lambda name:(output/name).read_bytes())
    def read(name):return reconstruct_packed_file(manifest,name,lambda p:gzip.decompress((output/p).read_bytes()))
    assert read('results.jsonl')==expected
    assert (source/'results.jsonl').read_bytes()==original
    receipt=json.loads(read('publication-privacy.json'))
    updated=next(r for r in receipt['files'] if r['path']=='results.jsonl')
    assert updated['original_sha256']==hashlib.sha256(original).hexdigest()
    assert updated['published_sha256']==hashlib.sha256(expected).hexdigest()
    assert updated['replacements']==project_file('results.jsonl',original)[1]['replacements']
    (source/'results.jsonl').write_bytes(original.replace(b'true',b'false',1))
    with pytest.raises(ValueError,match='Cached results identify another source'):
        report_package._compact_cached_content(cached,tmp_path/'rejected',source=source)


@pytest.mark.parametrize('damage',[None,'missing_binding','changed_alias'])
def test_public_package_checks_original_and_retained_view_hashes(tmp_path,damage,capsys):
    from harness.web_fetch.publication_privacy import project_evidence_view
    from harness.web_fetch.publication_check import check
    source,archive=report_tree(tmp_path,sampled=True)
    rows=[json.loads(line) for line in (source/'results.jsonl').read_text().splitlines()]
    for row in rows:
        row['output_sha256']=hashlib.sha256((source/row['artifact_path']).read_bytes()).hexdigest()
    original=b'Answer saved at /Users/example/private/answer.txt'
    original_hash=hashlib.sha256(original).hexdigest()
    name='public-evidence/answer.txt'
    public,receipt=project_evidence_view(name,original,role='scored_answer')
    (source/'public-evidence').mkdir()
    (source/name).write_bytes(public)
    def save(name,value):
        raw=json.dumps(value).encode()
        (source/name).write_bytes(raw)
        return {'path':name,'sha256':hashlib.sha256(raw).hexdigest()}
    proof=save('public-evidence/receipt.json',receipt)
    artifact='artifacts/first/answer.txt'
    (source/'artifacts/first').mkdir(parents=True)
    (source/artifact).write_bytes(public if damage!='changed_alias' else b'Unbound answer')
    rows[0].update(artifact_path=artifact,output_sha256=original_hash)
    index=save('public-evidence/index.json',{'kind':'post_score_public_evidence_view_inventory',
        'provider_calls_added':0,'files':[{'original_path':artifact,
        'original_sha256':original_hash,'view_receipt':proof}]})
    save('presentation-binding.json',{'publication_evidence_views':index} if damage!='missing_binding' else {})
    (source/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    report_package.build(source,archive)
    if damage:
        with pytest.raises(ValueError,match='Original scored output hash differs'):
            check(archive,source)
    else:
        check(archive,source)
        summary=json.loads(capsys.readouterr().out.splitlines()[-1])
        assert summary['scored_outputs_byte_identical']==3
        assert summary['scored_outputs_privacy_projected']==1
        assert summary['scored_outputs_retained_public_views']==1
