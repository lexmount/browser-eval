"""Frozen profiles bind the actual adapted metric code, not only its ancestry."""
import hashlib
import json
import subprocess
import sys

import pytest

from harness.web_fetch import wcxb_scoring as scoring
from harness.web_fetch.wcxb_metrics import snippet_check, word_f1
from tests.wcxb_fixture import create_cohort

# The text hashed by the earlier inspect.getsource() binding. Certified evidence
# names its digest, and this test keeps the mapping to the current code provable.
RETIRED_DIGEST = '35dc50a11b5b5475c6b36e444847ef75ff67dcd6c64fb553655b9adab13268cf'
RETIRED_SOURCE_TEXT = """def word_f1(predicted: str, reference: str) -> tuple[float, float, float]:
    \"\"\"Official lowercase Unicode-word multiset precision, recall and F1.\"\"\"
    predicted_words = re.findall(r'\\w+', (predicted or '').lower())
    reference_words = re.findall(r'\\w+', (reference or '').lower())
    if not reference_words:
        return (1.0, 1.0, 1.0) if not predicted_words else (0.0, 0.0, 0.0)
    if not predicted_words:
        return 0.0, 0.0, 0.0
    overlap = sum((Counter(predicted_words) & Counter(reference_words)).values())
    precision, recall = overlap / len(predicted_words), overlap / len(reference_words)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1

def snippet_check(text: str, snippets: list[str]) -> float:
    \"\"\"Official case-insensitive literal substring fraction; no word gate.\"\"\"
    return sum(snippet.lower() in text.lower() for snippet in snippets) / len(snippets) if snippets else 1.0
"""


@pytest.fixture
def cohort(tmp_path):
    return create_cohort(tmp_path)


def approved_digest():
    return json.loads(scoring.METRIC_BINDINGS.read_bytes())['implementations'][scoring.UPSTREAM_EVALUATOR_SHA256]


def assert_rejected(cohort):
    before = cohort['profile_path'].read_bytes()
    with pytest.raises(ValueError, match='metric implementation'):
        scoring.score(**cohort)
    assert cohort['profile_path'].read_bytes() == before
    assert not (cohort['evidence_root'] / 'wcxb-scores.jsonl').exists()
    assert not (cohort['evidence_root'] / 'wcxb-score-summary.json').exists()


def use_metric_module(monkeypatch, tmp_path, source: bytes):
    module = tmp_path / 'wcxb_metrics.py'
    module.write_bytes(source)
    monkeypatch.setattr(scoring, 'METRIC_MODULE', module)


def metric_source() -> bytes:
    return scoring.METRIC_MODULE.read_bytes()


def test_changed_word_metric_rejects_legacy_profile(cohort, monkeypatch):
    def changed_word_metric(predicted, reference):
        return 0., 0., 0.
    monkeypatch.setattr(scoring, 'word_f1', changed_word_metric)
    assert_rejected(cohort)


def test_changed_snippet_metric_rejects_legacy_profile(cohort, monkeypatch):
    def changed_snippet_metric(text, snippets):
        return 0.
    monkeypatch.setattr(scoring, 'snippet_check', changed_snippet_metric)
    assert_rejected(cohort)


@pytest.mark.parametrize('old,new', [
    (b'\\w+', b'\\S+'),
    (b'.lower())\n    reference_words', b')\n    reference_words'),
    (b'else 1.0', b'else 0.0'),
    (b'def word_f1', b'def helper():\n    return 0\n\n\ndef word_f1'),
])
def test_changed_module_code_rejects_legacy_profile(cohort, monkeypatch, tmp_path, old, new):
    source = metric_source()
    assert old in source
    use_metric_module(monkeypatch, tmp_path, source.replace(old, new))
    assert_rejected(cohort)


@pytest.mark.parametrize('statement', [
    b'from harness.web_fetch import safety\n',
    b'import harness.web_fetch.safety\n',
    b'def helper():\n    import harness.web_fetch.safety\n',
    b'def helper():\n    from harness.web_fetch import safety\n',
    b'from . import safety\n',
])
def test_unbound_helper_import_rejects_module_before_hashing(statement):
    tampered = metric_source().replace(b'import re\n', b'import re\n' + statement)
    with pytest.raises(ValueError, match='metric implementation'):
        scoring.metric_module_digest(tampered)


def test_formatting_docstrings_and_comments_keep_the_binding(cohort, monkeypatch, tmp_path):
    source = metric_source()
    rewritten = (source
                 .replace(b'"""Official lowercase Unicode-word multiset precision, recall and F1."""', b'"""Changed."""')
                 .replace(b'"""Official case-insensitive literal substring fraction; no word gate."""\n', b'')
                 .replace(b"(predicted or '')", b'(predicted or "")')
                 .replace(b"    overlap = sum", b"    # Same multiset overlap.\n    overlap = sum")
                 .replace(b'overlap / len(predicted_words), overlap', b'(overlap / len(predicted_words)), overlap')
                 .replace(b'import re\n', b'\nimport re\n'))
    assert rewritten != source
    use_metric_module(monkeypatch, tmp_path, rewritten)
    assert scoring.score(**cohort)['metric_implementation_sha256'] == approved_digest()


def test_ruff_format_keeps_the_binding():
    pytest.importorskip('ruff')
    formatted = subprocess.run([sys.executable, '-m', 'ruff', 'format', '--isolated', '-'], input=metric_source(),
                               capture_output=True, check=True).stdout
    assert scoring.metric_module_digest(formatted) == approved_digest()


@pytest.mark.parametrize('answer,reference,expected', [
    ('Alpha, BETA!', 'alpha beta', (1.0, 1.0, 1.0)),
    ('alpha alpha beta', 'alpha beta beta beta', (0.6666666666666666, 0.5, 0.5714285714285715)),
    ('a 1 1', 'A 1', (0.6666666666666666, 1.0, 0.8)),
    ('Straße', 'STRASSE', (0.0, 0.0, 0.0)),
    ('alpha_beta', 'alpha beta', (0.0, 0.0, 0.0)),
    ('你好，世界', '你好 世界', (1.0, 1.0, 1.0)),
    ('', '', (1.0, 1.0, 1.0)),
    ('!!!', '', (1.0, 1.0, 1.0)),
    ('extra', '', (0.0, 0.0, 0.0)),
    ('', 'required', (0.0, 0.0, 0.0)),
    ('!!!', 'required', (0.0, 0.0, 0.0)),
    ('one two three four five six seven', 'two four six eight', (0.42857142857142855, 0.75, 0.5454545454545454)),
    ('the the the cat', 'the cat sat on the mat', (0.75, 0.5, 0.6)),
    ('no shared words', 'entirely different tokens', (0.0, 0.0, 0.0)),
    ('İstanbul café', 'i̇stanbul CAFÉ', (1.0, 1.0, 1.0)),
    ('line one\nline\ttwo', 'LINE ONE LINE TWO', (1.0, 1.0, 1.0)),
])
def test_word_metric_matches_upstream_evaluator_output(answer, reference, expected):
    assert word_f1(answer, reference) == expected


@pytest.mark.parametrize('text,snippets,expected', [
    ('Cartography', ['art'], 1.0),
    ('Alpha Beta', ['ALPHA', 'beta', 'absent'], 0.6666666666666666),
    ('Straße', ['STRASSE'], 0.0),
    ('anything', [], 1.0),
    ('', ['required'], 0.0),
    ('Repeat repeat', ['repeat', 'repeat'], 1.0),
    ('Nav: Home | About', ['nav: home', 'home  |', 'about'], 0.6666666666666666),
    ('text', [''], 1.0),
])
def test_snippet_metric_matches_upstream_evaluator_output(text, snippets, expected):
    assert snippet_check(text, snippets) == expected


def test_superseded_digest_is_the_retired_source_of_the_bound_code():
    assert hashlib.sha256(RETIRED_SOURCE_TEXT.encode()).hexdigest() == RETIRED_DIGEST
    imports = b'from __future__ import annotations\n\nimport re\nfrom collections import Counter\n\n'
    retired_module = scoring.metric_module_digest(imports + RETIRED_SOURCE_TEXT.encode())
    assert retired_module == approved_digest()
    authority = json.loads(scoring.METRIC_BINDINGS.read_bytes())
    assert authority['superseded_source_text_digests'] == {RETIRED_DIGEST: approved_digest()}


def frozen_profile():
    return {'official_evaluator_sha256': scoring.UPSTREAM_EVALUATOR_SHA256, 'metric_implementation_sha256': RETIRED_DIGEST}


def frozen_evidence():
    return {'metric_implementation_sha256': RETIRED_DIGEST}


def test_frozen_evidence_naming_the_superseded_digest_keeps_verifying():
    assert scoring.metric_implementation_binding(frozen_profile(), certificate=frozen_evidence(),
                                                 summary=frozen_evidence(), receipt=frozen_evidence()) == approved_digest()
    legacy = {'official_evaluator_sha256': scoring.UPSTREAM_EVALUATOR_SHA256}
    assert scoring.metric_implementation_binding(legacy, certificate=frozen_evidence(),
                                                 summary=frozen_evidence()) == approved_digest()


def test_frozen_cohort_rescoring_keeps_profile_and_certificate_bytes(cohort):
    profile = json.loads(cohort['profile_path'].read_bytes())
    profile['metric_implementation_sha256'] = RETIRED_DIGEST
    cohort['profile_path'].write_text(json.dumps(profile))
    certificate = cohort['evidence_root'] / 'certification.json'
    value = json.loads(certificate.read_bytes())
    value['metric_implementation_sha256'] = RETIRED_DIGEST
    certificate.write_text(json.dumps(value))
    before = cohort['profile_path'].read_bytes(), certificate.read_bytes()
    assert scoring.score(**cohort)['metric_implementation_sha256'] == approved_digest()
    assert (cohort['profile_path'].read_bytes(), certificate.read_bytes()) == before


@pytest.mark.parametrize('document', ['certificate', 'summary', 'receipt'])
def test_unknown_digest_in_frozen_evidence_is_rejected(document):
    with pytest.raises(ValueError, match='metric implementation binding mismatch'):
        scoring.metric_implementation_binding(frozen_profile(), **{document: {'metric_implementation_sha256': '0' * 64}})


def test_superseded_digest_stops_verifying_once_the_code_changes(monkeypatch, tmp_path):
    use_metric_module(monkeypatch, tmp_path, metric_source().replace(b'else 1.0', b'else 0.0'))
    with pytest.raises(ValueError, match='metric implementation binding mismatch'):
        scoring.metric_implementation_binding(frozen_profile())


def test_explicit_profile_implementation_hash_must_match(cohort):
    profile = json.loads(cohort['profile_path'].read_bytes())
    profile['metric_implementation_sha256'] = '0' * 64
    cohort['profile_path'].write_text(json.dumps(profile))
    assert_rejected(cohort)


@pytest.mark.parametrize('explicit', [False, True])
def test_known_adaptation_scores_legacy_and_explicit_profiles_without_rewriting(cohort, explicit):
    if explicit:
        profile = json.loads(cohort['profile_path'].read_bytes())
        profile['metric_implementation_sha256'] = approved_digest()
        cohort['profile_path'].write_text(json.dumps(profile))
    before = cohort['profile_path'].read_bytes()
    summary = scoring.score(**cohort)
    assert summary['metric_implementation_sha256'] == approved_digest()
    assert summary['official_evaluator_sha256'] == scoring.UPSTREAM_EVALUATOR_SHA256
    assert summary['providers']['alpha']['f1'] == 1
    assert cohort['profile_path'].read_bytes() == before


def test_certificate_metric_binding_must_agree_with_actual_implementation(cohort):
    certificate = cohort['evidence_root'] / 'certification.json'
    value = json.loads(certificate.read_bytes())
    value['metric_implementation_sha256'] = '0' * 64
    certificate.write_text(json.dumps(value))
    assert_rejected(cohort)


@pytest.mark.parametrize('changed', ['module', 'authority'])
def test_metric_inputs_changed_during_scoring_reject_all_outputs(cohort, monkeypatch, tmp_path, changed):
    use_metric_module(monkeypatch, tmp_path, metric_source())
    authority = tmp_path / 'wcxb-metric-bindings.json'
    authority.write_bytes(scoring.METRIC_BINDINGS.read_bytes())
    monkeypatch.setattr(scoring, 'METRIC_BINDINGS', authority)
    # A trailing newline keeps the digest and the authority's meaning, so only the byte guard can see it.
    target = authority if changed == 'authority' else scoring.METRIC_MODULE
    classify = scoring.classify

    def change_during_scoring(*args):
        target.write_bytes(target.read_bytes() + b'\n')
        return classify(*args)

    monkeypatch.setattr(scoring, 'classify', change_during_scoring)
    with pytest.raises(ValueError, match='changed during scoring'):
        scoring.score(**cohort)
    assert not (cohort['evidence_root'] / 'wcxb-scores.jsonl').exists()
    assert not (cohort['evidence_root'] / 'wcxb-score-summary.json').exists()
