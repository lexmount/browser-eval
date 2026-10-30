"""A source Git revision can name several independently frozen Hub editions."""
import hashlib
import json

import pytest

from harness.benchmarks import data
from harness.web_fetch.reference_inputs import benchmark_input_path


@pytest.mark.parametrize('matching', ['current', 'middle', 'last', None])
def test_historical_binding_selects_its_exact_definition_bytes(tmp_path, monkeypatch, matching):
    source_revision = 'f' * 40
    revisions = ['a' * 40, 'b' * 40, 'c' * 40]
    selected = dict(zip(['current', 'middle', 'last'], revisions))
    checkout = tmp_path / 'checkout'
    (checkout / 'evaluation').mkdir(parents=True)
    relative = 'evaluation/example/source-inputs.json'
    expected = hashlib.sha256(b'frozen older definition').hexdigest()
    (checkout / 'evaluation/lexbench-webfetch.json').write_text(json.dumps({
        'kind': 'external_benchmark_binding', 'repository': 'https://github.com/lexmount/lexbench-webfetch',
        'revision': source_revision, 'definition_files': {relative: expected}}))
    entries = [{'revision': rev, 'source_revision': source_revision, 'files': {
        'definitions/' + relative: {'sha256': expected if selected.get(matching) == rev else 'different'}}}
        for rev in revisions]
    locked = {**entries[0], 'history': entries[1:]}
    monkeypatch.setattr(data, 'binding', lambda *args: locked)
    calls = []
    paths = {}
    for name, revision in selected.items():
        target = tmp_path / revision / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b'frozen older definition' if name == matching else b'different valid edition')
        paths[revision] = target
    def definition(track, name, revision):
        assert track == 'webfetch' and name == relative
        calls.append(revision)
        return paths[revision]
    monkeypatch.setattr(data, 'definition_path', definition)
    requested = checkout / relative
    if matching is None:
        with pytest.raises(ValueError, match='differs from checkout binding'):
            benchmark_input_path(requested)
        assert calls == []
    else:
        assert benchmark_input_path(requested) == paths[selected[matching]]
        assert calls == [selected[matching]]
