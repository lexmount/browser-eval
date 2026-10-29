"""A cache configured after import still goes through the verified input owner."""
import hashlib
import json

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
