"""A cache configured after import still goes through the verified input owner."""
import hashlib
import io
import json
import shutil
import tarfile

import pytest

from harness.benchmarks import data
from lexbench_webfetch import article_dataset, benchmark, risk_control_dataset
from lexbench_websearch import inputs as search_inputs


@pytest.fixture
def configured_cache(tmp_path, monkeypatch):
    files = {
        "registry.json": {"schema_version": 1, "suites": []},
        "evaluation/articles/cohort.json": {"schema_version": 1, "case_count": 1,
                                               "cases": [{"id": "article", "url": "https://fixture.example/"}]},
        "evaluation/articles/rules.json": {"schema_version": 1, "rules": [{"id": "article"}]},
        "evaluation/risk-control/qualification/cohort/cohort-manifest.json": {},
    }
    content = {name: json.dumps(value).encode() for name, value in files.items()}
    for name in ("first-30-rule-review.md", "remaining-70-rule-review.md"):
        content["evaluation/risk-control/" + name] = name.encode()
    definitions = tmp_path / "cache/webfetch" / ("a" * 40) / "definitions"
    bindings = {}
    for relative, raw in content.items():
        path = definitions / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        bindings["definitions/" + relative] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    search = tmp_path / "cache/websearch" / ("a" * 40) / "definitions"
    search.mkdir(parents=True)
    raw = b'{"datasets":{},"selection":"configured-after-import"}'
    (search / "registry.json").write_bytes(raw)
    selected = {
        "webfetch": {"files": bindings},
        "websearch": {"files": {"definitions/registry.json": {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}}},
    }
    for item in selected.values():
        item.update(repository="Lexmount/fixture", revision="a" * 40)
    monkeypatch.setattr(data, "binding", lambda track, revision=None: selected[track])
    monkeypatch.setenv("BROWSER_EVAL_DATASET_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(article_dataset, "compile_case", lambda *args: {
        "id": "article", "url": "https://fixture.example/"})
    monkeypatch.setattr(risk_control_dataset, "EXPECTED_RULE_HASHES", {
        name: bindings["definitions/evaluation/risk-control/" + name]["sha256"]
        for name in ("first-30-rule-review.md", "remaining-70-rule-review.md")})
    monkeypatch.setattr(risk_control_dataset, "EXPECTED_COHORT_HASH", bindings[
        "definitions/evaluation/risk-control/qualification/cohort/cohort-manifest.json"]["sha256"])
    data._resolve.cache_clear()
    yield definitions, search
    data._resolve.cache_clear()


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("corrupt", [False, True])
def test_article_compiler_rechecks_the_active_installed_directory(configured_cache, tmp_path, explicit, corrupt):
    definitions, _ = configured_cache
    data.snapshot("webfetch")
    if corrupt:
        # Still valid authoring JSON; only the locked byte identity changes.
        path = definitions / "evaluation/articles/rules.json"
        path.write_text('{"schema_version":1,"rules":[{"id":"article","unbound":true}]}')
    directory = definitions / "evaluation/articles" if explicit else None
    if corrupt:
        with pytest.raises(ValueError, match="hash or size"):
            article_dataset.compile_dataset(tmp_path, directory)
    else:
        result = article_dataset.compile_dataset(tmp_path, directory)
        assert result["cases"][0]["id"] == "article"


@pytest.mark.parametrize("corrupt", [False, True])
def test_benchmark_and_search_default_reads_follow_the_post_import_cache(configured_cache, corrupt):
    definitions, search = configured_cache
    data.snapshot("webfetch")
    data.snapshot("websearch")
    if corrupt:
        for root in (definitions, search):
            (root / "registry.json").write_text('{"datasets":{},"schema_version":1,"suites":[]}')
        for read in (benchmark.registry, search_inputs.registry):
            with pytest.raises(ValueError, match="hash or size"):
                read()
    else:
        assert benchmark.registry() == {"schema_version": 1, "suites": []}
        assert search_inputs.registry()["selection"] == "configured-after-import"


def test_repeated_registry_reads_verify_consumed_file_without_rehashing_unrelated_inputs(
    configured_cache, monkeypatch
):
    definitions, _ = configured_cache
    data.snapshot("webfetch")
    original = data.verify_file
    verified = []
    def verify(path, expected):
        verified.append(path)
        return original(path, expected)
    monkeypatch.setattr(data, "verify_file", verify)
    for _ in range(4):
        assert benchmark.registry()["schema_version"] == 1
    assert verified == [definitions / "registry.json"] * 4


def test_relative_installed_paths_are_verified_after_import(configured_cache, tmp_path, monkeypatch):
    definitions, _ = configured_cache
    monkeypatch.chdir(tmp_path)
    relative = definitions.relative_to(tmp_path) / "evaluation/articles"
    assert data.resolve_definition_path("webfetch", relative) == definitions / "evaluation/articles"
    (definitions / "evaluation/articles/rules.json").write_text("changed")
    with pytest.raises(ValueError, match="hash or size"):
        data.resolve_definition_path("webfetch", relative)


@pytest.mark.parametrize("internal", [False, True])
def test_aliases_into_or_out_of_installed_snapshots_cannot_bypass_verification(
    configured_cache, tmp_path, internal
):
    definitions, _ = configured_cache
    if internal:
        external = tmp_path / "authoring"
        external.mkdir()
        (external / "rules.json").write_text("changed")
        alias = definitions / "alias"
        alias.symlink_to(external, target_is_directory=True)
        path = alias / "rules.json"
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(definitions, target_is_directory=True)
        path = alias / "evaluation/articles/rules.json"
    with pytest.raises(ValueError, match="symlink"):
        data.resolve_definition_path("webfetch", path)


def test_external_authoring_files_remain_explicit_local_inputs(configured_cache, tmp_path):
    path = tmp_path / "authoring/rules.json"
    path.parent.mkdir()
    path.write_text("caller-owned")
    assert data.resolve_definition_path("webfetch", path) == path


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("corrupt", [False, True])
def test_risk_compiler_verifies_installed_inputs_before_parsing(configured_cache, tmp_path, monkeypatch, explicit, corrupt):
    definitions, _ = configured_cache
    data.snapshot("webfetch")
    root = definitions / "evaluation/risk-control"
    if corrupt:
        (root / "first-30-rule-review.md").write_text("changed rules")
    class ParsedVerifiedRules(Exception):
        pass
    def parse(paths):
        assert all(path.parent == root for path in paths)
        raise ParsedVerifiedRules
    monkeypatch.setattr(risk_control_dataset, "parse_sections", parse)
    with pytest.raises(ValueError if corrupt else ParsedVerifiedRules,
                       match="hash or size" if corrupt else None):
        risk_control_dataset.build(tmp_path / "output", root if explicit else None)


@pytest.mark.parametrize('explicit', [False, True])
def test_benchmark_warm_registry_mutation_is_rejected(configured_cache, explicit):
    definitions, _ = configured_cache
    read = (lambda: benchmark.read(definitions / 'registry.json')) if explicit else benchmark.registry
    assert read() == {'schema_version': 1, 'suites': []}
    (definitions / 'registry.json').write_text('{"schema_version":1,"suites":[],"unbound":true}')
    with pytest.raises(ValueError, match='hash or size'):
        read()


@pytest.mark.parametrize('defect', ['changed-member', 'extra-member'])
def test_single_file_read_and_explicit_directory_have_distinct_integrity_scopes(
    configured_cache, defect
):
    definitions, _ = configured_cache
    assert benchmark.registry()['schema_version'] == 1
    assert data.resolve_definition_path('webfetch', definitions) == definitions
    if defect == 'changed-member':
        (definitions / 'evaluation/articles/rules.json').write_text('{"unbound":true}')
    else:
        (definitions / 'unregistered.json').write_text('{"unbound":true}')
    # Reading one unchanged file must not silently request the entire namespace.
    assert benchmark.registry()['schema_version'] == 1
    assert benchmark.read(definitions / 'registry.json')['schema_version'] == 1
    # An explicitly requested directory continues to validate its complete inventory.
    with pytest.raises(ValueError, match='hash or size|inventory differs'):
        data.resolve_definition_path('webfetch', definitions)


def test_lazy_namespace_materializes_cold_registry_then_rejects_warm_mutation(
    configured_cache, monkeypatch
):
    definitions, _ = configured_cache
    selected = data.binding('webfetch')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for path in sorted(definitions.rglob('*')):
            if path.is_file():
                payload = path.read_bytes()
                member = tarfile.TarInfo(path.relative_to(definitions.parent).as_posix())
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
    payload = buffer.getvalue()
    selected['archive'] = {'path': 'definitions.tar.gz', 'size': len(payload),
                           'sha256': hashlib.sha256(payload).hexdigest()}
    shutil.rmtree(definitions.parent)
    downloads = []
    def fetch(repository, revision, specification):
        downloads.append((repository, revision))
        return payload
    monkeypatch.delenv('HF_HUB_OFFLINE')
    monkeypatch.setattr(data, 'fetch_archive', fetch)
    assert benchmark.definitions() == definitions
    assert not definitions.exists()
    assert downloads == []
    assert benchmark.registry() == {'schema_version': 1, 'suites': []}
    assert definitions.is_dir()
    assert downloads == [('Lexmount/fixture', 'a' * 40)]
    (definitions / 'registry.json').write_text('{"schema_version":1,"suites":[],"unbound":true}')
    with pytest.raises(ValueError, match='hash or size'):
        benchmark.registry()
    assert len(downloads) == 1


def test_explicit_local_definition_override_remains_caller_owned(tmp_path, monkeypatch):
    root = tmp_path / 'authoring'
    root.mkdir()
    path = root / 'registry.json'
    monkeypatch.setattr(benchmark, 'definitions', lambda: root)
    for marker in ['first', 'edited']:
        path.write_text(json.dumps({'schema_version': 1, 'suites': [], 'marker': marker}))
        assert benchmark.registry()['marker'] == marker



@pytest.mark.parametrize('defect', ['deleted', 'changed', 'extra'])
def test_benchmark_annotation_enumeration_rechecks_warm_directory(
    configured_cache, monkeypatch, defect
):
    definitions, _ = configured_cache
    selected = data.binding('webfetch')
    documents = {
        'sample/dataset-catalog.json': {'id': 'sample', 'case_count': 0, 'cases': [],
                                        'dataset_sha256': '0' * 64},
        'sample/tasks.json': {'id': 'sample', 'cases': []},
        'annotation-revisions/revision.json': {'id': 'revision', 'parents': {}},
    }
    for relative, document in documents.items():
        raw = json.dumps(document).encode()
        path = definitions / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        selected['files']['definitions/' + relative] = {
            'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        }
    monkeypatch.setattr(benchmark, 'suite', lambda identifier: (
        {'dataset_id': 'sample', 'case_count': 0}, definitions / 'sample'))
    monkeypatch.setattr(benchmark, 'load_manifest', lambda path: {
        'catalog': 'dataset-catalog.json', 'files': {}, 'trees': {},
    })
    assert benchmark.validate('sample')['case_count'] == 0
    revision = definitions / 'annotation-revisions/revision.json'
    if defect == 'deleted':
        revision.unlink()
    elif defect == 'changed':
        revision.write_text('{"id":"revision","parents":{},"unbound":true}')
    else:
        revision.with_name('unregistered.json').write_text('{"id":"unregistered","parents":{}}')
    with pytest.raises((ValueError, FileNotFoundError), match='hash or size|inventory differs|Unknown'):
        benchmark.validate('sample')
