"""Published viewers retain task hierarchy and evidence-based language labels."""
import hashlib
import json
import tarfile
from dataclasses import replace

import pytest
import yaml

from harness.benchmarks.publication import DatasetRelease, encoded


def release(track, cases, *, identity="sample", annotations=None):
    raw = encoded({"cases": cases})
    binding = {"path": "tasks.json", "count": len(cases), "sha256": hashlib.sha256(raw).hexdigest()}
    registry = {"datasets": {identity: binding}}
    if track == "webfetch":
        registry = {"suites": [{"id": identity, "directory": ".", "case_count": len(cases),
                                "files": {"tasks.json": binding["sha256"]}}]}
    options = {} if annotations is None else {"language_annotations": annotations}
    return DatasetRelease(track, "1" * 40, {"definitions/registry.json": encoded(registry),
                          "definitions/tasks.json": raw},
                          **options)


def exported_rows(output, name="sample"):
    return [json.loads(line) for line in (output / f"data/{name}.jsonl").read_text().splitlines()]


def metadata(output):
    return yaml.safe_load((output / "README.md").read_text().split("---", 2)[1])


def test_fetch_viewer_exposes_original_hierarchy_without_changing_execution_inputs(tmp_path):
    tracks = ["web", "document", "image", "video", "challenge", "dynamic"]
    names = ["general-web", "embedded-documents", "media-information", "media-information"]
    cases = [{"id": f"original-{track}", "track": track, "category": f"specific_{track}",
              "url": "https://example.test", "language": "en"} for track in tracks]
    source = release("webfetch", cases, identity="webfetch")
    manifest = source.build(tmp_path)
    assert {item["config_name"] for item in metadata(tmp_path)["configs"]} == set(names)
    for track, name, case in zip(tracks[:4], names, cases[:4]):
        row, = [row for row in exported_rows(tmp_path, name) if row['track'] == track]
        assert (row["id"], row["source_suite"], row["track"], row["category"]) == (
            case["id"], "webfetch", track, case["category"])
        assert json.loads(row["task"]) == case
    assert list(manifest["datasets"]) == ["webfetch"]
    with tarfile.open(tmp_path / "definitions.tar.gz") as archive:
        assert {item.name: archive.extractfile(item).read() for item in archive} == source.files
    body = (tmp_path / "README.md").read_text()
    assert "{{dataset_summary}}" not in body and "{{total_records}}" not in body
    assert "| `embedded-documents` | `webfetch` | 1 |" in body


@pytest.mark.parametrize(("track", "case", "expected"), [
    ("webfetch", {"target_language": "pt-BR"}, "pt"),
    ("webfetch", {"source_benchmark": {"risk_control": {"navigation_contract": {"target_language": "zh-CN"}}}}, "zh"),
    ("webmainbench", {"meta": {"language": "de"}}, "de"),
    ("websearch", {"language": "fr"}, "fr"),
    ("simpleqa-verified", {"language": "en"}, "en"),
])
def test_languages_come_from_declared_task_fields_and_drive_discovery(tmp_path, track, case, expected):
    case = {"id": "frozen-task", **case}
    release(track, [case]).build(tmp_path)
    assert exported_rows(tmp_path)[0]["language"] == expected
    assert metadata(tmp_path)["language"] == [expected]


def test_reviewed_languages_preserve_source_annotations_and_bound_task_bytes(tmp_path):
    cases = [{"id": "wrong-source", "language": "en", "meta": {"language": "en"}},
             {"id": "mixed", "language": None}, {"id": "binary", "language": None}]
    labels = [("zh", ["zh"]), ("mul", ["ar", "zh"]), ("zxx", [])]
    annotations = {"sample": {case["id"]: {"case_sha256": hashlib.sha256(encoded(case)).hexdigest(),
                    "language": code, "languages": codes, "basis": "frozen-reference-review"}
                    for case, (code, codes) in zip(cases, labels)}}
    result = release("webmainbench", cases, annotations=annotations).build(tmp_path)
    rows = exported_rows(tmp_path)
    assert [row["language"] for row in rows] == ["zh", "mul", "zxx"]
    assert [json.loads(row["task"]) for row in rows] == cases
    assert metadata(tmp_path)["language"] == ["ar", "zh"]
    assert result["viewer"]["language_annotations"] == annotations
    cases[0]["meta"]["language"] = "changed-input"
    with pytest.raises(ValueError, match="annotation.*frozen task"):
        release("webmainbench", cases, annotations=annotations).build(tmp_path / "changed")


def test_missing_language_is_explicit_unknown_not_guessed_from_url_or_prompt(tmp_path):
    release("webfetch", [{"id": "unknown", "url": "https://example.cn/en", "task_prompt": "中文任务说明"}]).build(tmp_path)
    assert exported_rows(tmp_path)[0]["language"] == "und"


def test_published_instructions_can_name_an_available_immutable_toolkit(tmp_path):
    source = release("webfetch", [{"id": "sample", "language": "en"}])
    revision = "a" * 40
    result = replace(source, tooling_revision=revision).build(tmp_path)
    card = (tmp_path / "README.md").read_text()
    assert "git checkout " + revision in card
    assert f"https://github.com/lexmount/browser-eval/blob/{revision}/src/harness/benchmarks/data-lock.json" in card
    assert result["tooling"]["revision"] == revision
    assert "{{tooling_checkout}}" not in card
    with pytest.raises(ValueError, match="full Git commit"):
        replace(source, tooling_revision="main").build(tmp_path / "symbolic")


@pytest.mark.parametrize(("track", "tags", "source"), [
    ("webfetch", {"web-fetch", "web-content-extraction"}, "https://browser.lexmount.com/blog/webfetch-benchmark"),
    ("websearch", {"web-search", "search-ranking"}, "https://browser.lexmount.com/blog/websearch-benchmark"),
    ("webmainbench", {"main-content-extraction", "html-to-markdown"}, "https://huggingface.co/datasets/opendatalab/WebMainBench"),
    ("simpleqa-verified", {"information-retrieval", "simpleqa"}, "https://huggingface.co/datasets/google/simpleqa-verified"),
    ("wcxb", {"wcxb", "web-content-extraction"}, "https://github.com/Murrough-Foley/web-content-extraction-benchmark"),
    ("trajectory-replay", {"trajectory-replay", "browser-evaluation"}, "https://github.com/lexmount/Lexbench-Headless-Browser"),
    ("webarena-replay", {"webarena", "trajectory-replay"}, "https://huggingface.co/datasets/AmineHA/WebArena-Verified"),
])
def test_cards_explain_use_and_credit_sources_with_specific_discovery_terms(tmp_path, track, tags, source):
    release(track, [{"id": "task", "language": "en"}]).build(tmp_path)
    assert tags <= set(metadata(tmp_path)["tags"])
    text = (tmp_path / "README.md").read_text()
    assert source in text
    if track == 'websearch':
        assert '(definitions/sources.json)' in text
        assert '(definitions/NOTICE.txt)' in text
    assert "https://github.com/lexmount/browser-eval" in text
    assert "{{dataset_summary}}" not in text
    # Hub promotes direct paper links into a dataset-level ArXiv association.
    # Derivatives credit upstream through its dataset/repository instead.
    assert "arxiv.org/" not in text and "huggingface.co/papers/" not in text
    assert "arxiv:" not in text.lower()
    assert "```mermaid" not in text  # The Hub displays Mermaid as source rather than a diagram.
    assert not any(tag.startswith("arxiv:") for tag in metadata(tmp_path)["tags"])


def full_fetch_release():
    suites = {
        'webfetch': [(track, count) for track, count in
                     [('web', 100), ('document', 10), ('image', 10), ('video', 10), ('challenge', 10), ('dynamic', 10)]],
        'dynamic-content': [('dynamic', 100)], 'popular-sites': [('challenge', 100)],
        'access-restrictions': [('risk_control', 100)], 'articles': [('articles', 100)],
    }
    files, rows, sources = {}, [], {}
    for identity, groups in suites.items():
        cases = []
        for track, count in groups:
            for index in range(count):
                kind = 'file' if (track == 'image' and index < 2) or (track == 'video' and index < 1) else 'body'
                cases.append({'id': f'{identity}-{track}-{index}', 'track': track, 'task_kind': kind,
                              'url': 'https://example.test', 'language': 'en'})
        raw = encoded({'cases': cases})
        directory = 'evaluation/' + identity
        files['definitions/' + directory + '/tasks.json'] = raw
        rows.append({'id': identity, 'directory': directory, 'case_count': len(cases),
                     'files': {'tasks.json': hashlib.sha256(raw).hexdigest()}})
        sources[identity] = cases
    files['definitions/registry.json'] = encoded({'suites': rows})
    return DatasetRelease('webfetch', '1' * 40, files, language_annotations={}), sources


def test_current_fetch_viewer_has_seven_contract_categories_and_keeps_all_raw_inputs(tmp_path):
    source, originals = full_fetch_release()
    manifest = source.build(tmp_path)
    expected = {'general-web': 100, 'dynamic-sites': 100, 'popular-sites': 100,
                'access-restrictions': 100, 'embedded-documents': 10,
                'media-information': 17, 'direct-media-files': 3}
    assert {row['config']: row['count'] for row in manifest['viewer']['configurations']} == expected
    assert [row['config'] for row in manifest['viewer']['configurations']] == list(expected)
    assert [row['config_name'] for row in metadata(tmp_path)['configs']] == list(expected)
    rows = [row for name in expected for row in exported_rows(tmp_path, name)]
    primary = {case['id']: case for identity, cases in originals.items() for case in cases
               if identity != 'articles' and not (identity == 'webfetch' and case['track'] in {'challenge', 'dynamic'})}
    assert {row['id'] for row in rows} == set(primary)
    assert len(rows) == len(primary) == 430
    assert all(json.loads(row['task']) == primary[row['id']] for row in rows)
    assert {row['benchmark_category'] for row in exported_rows(tmp_path, 'media-information')} == {'web_media'}
    assert {row['benchmark_category'] for row in exported_rows(tmp_path, 'direct-media-files')} == {'media_file'}
    assert sum(binding['count'] for binding in manifest['datasets'].values()) == 550
    assert not (tmp_path / 'data/articles.jsonl').exists()
    with tarfile.open(tmp_path / 'definitions.tar.gz') as archive:
        assert {entry.name: archive.extractfile(entry).read() for entry in archive} == source.files


def test_unknown_current_fetch_category_is_rejected(tmp_path):
    source = release('webfetch', [{'id': 'unknown-category', 'track': 'unknown'}], identity='webfetch')
    with pytest.raises(ValueError, match='Unregistered viewer task group'):
        source.build(tmp_path)


def test_current_dataset_cards_link_to_existing_browser_eval_code_and_guides():
    import re
    from pathlib import Path
    from harness.benchmarks.publication import CARDS

    root = Path(__file__).resolve().parents[2]
    for card in CARDS.glob('*.md'):
        for target in re.findall(r'https://github.com/lexmount/browser-eval/blob/main/([^\s)#]+)', card.read_text()):
            assert (root / target).exists(), (card.name, target)
