import hashlib
import json

import pytest

from harness.web_fetch.webmainbench_correction import validate


def test_complete_correction_rejects_partial_scope_changed_input_and_output(tmp_path):
    names = ['moli', 'moli-fixed', 'chromium', 'lightpanda', 'obscura', 'kitesurf']
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    originals, new, calls = [], [], []
    for name in names:
        artifact = tmp_path / (name + '.md')
        artifact.write_text('狼人杀规则：村民投票，狼人夜间行动。')
        old = dict(provider=name, case_id='case', attempt_id=name + '-old',
                   input_html_sha256='input', reference_sha256='reference', provider_metadata={})
        originals.append(old)
        new.append({**old, 'attempt_id': name + '-new', 'supersedes_attempt_id': old['attempt_id'],
                    'artifact_path': artifact.name, 'output_sha256': digest(artifact), 'physical_attempt_count': 1})
        calls.append(dict(provider=name, track_id='case', attempt_id=name + '-new',
                          physical_artifact_path=artifact.name, output_sha256=digest(artifact)))
    plan = dict(providers=names, case_id='case', parent_results_sha256='parent')
    def freeze():
        for filename, values in [('results.jsonl', new), ('physical-attempts.jsonl', calls)]:
            (tmp_path / filename).write_text(''.join(json.dumps(r) + '\n' for r in values))
        (tmp_path / 'certificate.json').write_text(json.dumps(dict(kind='webmainbench_origin_correction_v1',
            preflight=plan, postflight=plan, results_sha256=digest(tmp_path / 'results.jsonl'),
            physical_sha256=digest(tmp_path / 'physical-attempts.jsonl'))))
    freeze()
    assert len(validate(tmp_path, originals, 'parent')[1]) == 6
    saved = new.pop()
    freeze()
    with pytest.raises(ValueError, match='every method'):
        validate(tmp_path, originals, 'parent')
    new.append(saved)
    new[0]['input_html_sha256'] = 'changed'
    freeze()
    with pytest.raises(ValueError, match='frozen input'):
        validate(tmp_path, originals, 'parent')
    new[0]['input_html_sha256'] = 'input'
    freeze()
    (tmp_path / 'moli.md').write_text('empty error page')
    with pytest.raises(ValueError, match='output changed'):
        validate(tmp_path, originals, 'parent')


def test_correction_accepts_an_explicit_provider_subset(tmp_path):
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    originals = [
        dict(provider=name, case_id='case', attempt_id=name + '-old',
             input_html_sha256='input', reference_sha256='reference', provider_metadata={})
        for name in ('historical-a', 'historical-b', 'current')
    ]
    selected = originals[:2]
    rows, calls = [], []
    for old in selected:
        artifact = tmp_path / (old['provider'] + '.md')
        artifact.write_text('corrected')
        rows.append({**old, 'attempt_id': old['provider'] + '-new',
                     'supersedes_attempt_id': old['attempt_id'],
                     'artifact_path': artifact.name, 'output_sha256': digest(artifact),
                     'physical_attempt_count': 1})
        calls.append(dict(provider=old['provider'], track_id='case',
                          attempt_id=old['provider'] + '-new',
                          physical_artifact_path=artifact.name,
                          output_sha256=digest(artifact)))
    for filename, values in (('results.jsonl', rows), ('physical-attempts.jsonl', calls)):
        (tmp_path / filename).write_text(''.join(json.dumps(row) + '\n' for row in values))
    plan = dict(providers=[row['provider'] for row in selected], case_id='case',
                parent_results_sha256='parent')
    (tmp_path / 'certificate.json').write_text(json.dumps({
        'kind': 'webmainbench_origin_correction_v1', 'preflight': plan, 'postflight': plan,
        'results_sha256': digest(tmp_path / 'results.jsonl'),
        'physical_sha256': digest(tmp_path / 'physical-attempts.jsonl'),
    }))
    assert [row['provider'] for row in validate(tmp_path, originals, 'parent')[1]] == plan['providers']
