"""Hub names change without changing frozen editions or historical provenance."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.benchmarks import data, pinning
from harness.web_fetch.reference_inputs import benchmark_input_path
from harness.web_search.runner import validate_resume_datasets


NAMES = {
    "webfetch": "LexBench-Web-Fetch",
    "websearch": "LexBench-Web-Search",
    "simpleqa-verified": "LexBench-SimpleQA-Verified",
    "webmainbench": "LexBench-WebMainBench",
    "wcxb": "LexBench-WCXB",
    "trajectory-replay": "LexBench-Trajectory-Replay",
    "webarena-replay": "LexBench-WebArena-Replay",
}


@pytest.mark.parametrize("track,name", NAMES.items())
def test_current_names_and_cards_use_the_canonical_hub_identity(track, name):
    selected = data.binding(track)
    metadata = json.loads(Path(data.__file__).with_name("cards").joinpath("metadata.json").read_bytes())
    assert selected["repository"] == metadata[track]["repo_id"] == "Lexmount/" + name
    assert metadata[track]["pretty_name"] == name
    assert Path(data.__file__).with_name("cards").joinpath(track + ".md").read_text().splitlines()[0] == "# " + name
    for item in selected.get("history", []):
        assert item["repository"] == selected["repository"]


@pytest.mark.parametrize("track,name", NAMES.items())
def test_previous_repo_names_preserve_the_same_frozen_commit(track, name):
    selected = data.binding(track)
    old = "Lexmount/lexbench-" + track
    assert data.canonical_repository(old) == "Lexmount/" + name
    imported = {"repository": "https://huggingface.co/datasets/" + old,
                "revision": selected["revision"], "definition_files": {}}
    original = copy.deepcopy(imported)
    assert pinning.validate_selected_binding(selected, imported) == selected["revision"]
    assert imported == original
    imported["revision"] = "0" * 40
    with pytest.raises(ValueError, match="disagree"):
        pinning.validate_selected_binding(selected, imported)


@pytest.mark.parametrize("repository", ["Lexmount/LexBench-Web-Fetch", "https://other.example/Lexmount/lexbench-webfetch"])
def test_repo_aliases_do_not_admit_unbound_url_forms(repository):
    selected = data.binding("webfetch")
    with pytest.raises(ValueError, match="disagree"):
        pinning.validate_selected_binding(selected, {"repository": repository, "revision": selected["revision"]})


@pytest.mark.parametrize("source", [
    "https://github.com/lexmount/lexbench-webfetch", "Lexmount/LexBench-Browser"
])
def test_other_repository_identities_are_not_a_hub_repo_alias(source):
    assert data.canonical_repository(source) == source


def test_previous_search_run_contract_keeps_its_bytes_and_commits():
    selected = data.dependencies("websearch")
    old = copy.deepcopy(selected)
    for track in old:
        old[track]["repository"] = "Lexmount/lexbench-" + track
    contract = {"installed_benchmark_datasets": old}
    original = copy.deepcopy(contract)
    assert validate_resume_datasets(contract) == selected
    assert contract == original
    contract["installed_benchmark_datasets"]["websearch"]["revision"] = "0" * 40
    with pytest.raises(ValueError, match="datasets"):
        validate_resume_datasets(contract)


def test_previous_hf_checkout_binding_resolves_only_its_exact_locked_bytes(tmp_path, monkeypatch):
    revision = "a" * 40
    relative = "evaluation/example/source-inputs.json"
    raw = b'{"schema_version":2,"id":"historical"}'
    expected = hashlib.sha256(raw).hexdigest()
    checkout = tmp_path / "checkout"
    (checkout / "evaluation").mkdir(parents=True)
    authority = checkout / "evaluation/lexbench-webfetch.json"
    authority.write_text(json.dumps({"kind": "huggingface_benchmark_binding",
        "repository": "https://huggingface.co/datasets/Lexmount/lexbench-webfetch",
        "revision": revision, "definition_files": {relative: expected}}))
    original = authority.read_bytes()
    installed = tmp_path / "installed/source-inputs.json"
    installed.parent.mkdir()
    installed.write_bytes(raw)
    selected = {"repository": "Lexmount/LexBench-Web-Fetch", "revision": revision,
                "files": {"definitions/" + relative: {"sha256": expected}}}
    monkeypatch.setattr(data, "binding", lambda track, version=None: selected)
    calls = []
    def definition(track, path, version):
        calls.append((track, path, version))
        return installed
    monkeypatch.setattr(data, "definition_path", definition)
    assert benchmark_input_path(checkout / relative) == installed
    assert calls == [("webfetch", relative, revision)]
    assert authority.read_bytes() == original
