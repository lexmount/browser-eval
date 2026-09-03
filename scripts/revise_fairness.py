"""Audited offline correction: preserve call identity and record before/after scores."""
import hashlib
import json
import sys
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from correct_evaluation_contracts import correct
from rescore_content import rescore
from harness.web_fetch.metrics import task_passed, task_measured


def run(source, baseline_ref=None):
    def baseline(name):
        return (subprocess.check_output(['git', 'show', f'{baseline_ref}:evaluation/{name}'], cwd=ROOT)
                if baseline_ref else (source/name).read_bytes())
    original = baseline('results.jsonl')
    dataset = baseline('dataset.json')
    before = [json.loads(line) for line in original.splitlines()]
    correct(source)
    rescore(source)
    after = [json.loads(line) for line in (source/'results.jsonl').read_bytes().splitlines()]
    changes = []
    for old, new in zip(before, after, strict=True):
        for key in ('attempt_id', 'started_at', 'elapsed_ms', 'artifact_path'):
            assert old[key] == new[key], key
        assert old['metrics']['content_sha256'] == new['metrics']['content_sha256']
        def values(record, legacy=False):
            m = record['metrics']
            field = {'structure':'list_task_pass','resource':'resource_pass','file':'raw_file_pass'}.get(m.get('task_kind'), 'content_pass')
            old_pass = bool(record['ok'] and m.get('stable_task_pass', m.get(field)) and not m.get('forbidden_hits') and not m.get('stable_blocked_page'))
            return {'request_ok': record['ok'], 'task_in_denominator': True if legacy else task_measured(m),
                    'task_pass': old_pass if legacy else task_passed(record['ok'], m),
                    **{key: m.get(key) for key in ('content_pass', 'stable_task_pass', 'word_precision',
                        'word_recall', 'word_f1', 'scoring_source_sha256')}}
        changes.append({'attempt_id': old['attempt_id'], 'output_sha256': old['metrics']['content_sha256'],
                        'before': values(old, legacy=bool(baseline_ref)), 'after': values(new)})
    receipt = {'kind': 'offline_scoring_revision', 'scored_at': datetime.now(timezone.utc).isoformat(),
        'input_results_sha256': hashlib.sha256(original).hexdigest(),
        'input_dataset_sha256': hashlib.sha256(dataset).hexdigest(),
        'output_dataset_sha256': hashlib.sha256((source/'dataset.json').read_bytes()).hexdigest(),
        'scope': ['reviewed CSS/Django reference scopes', 'distributed body requirements',
                  'shared answer/status normalization', 'declared React field coverage',
                  'deadline eligibility', 'unmeasured historical file tasks'],
        'historical_limit': 'No new provider calls. Original output hashes, call identities and timings retained. Historical timeout enforcement and runtime overhead were not remeasured.',
        'records': changes}
    target = source/'scoring-revisions.json'
    history = json.loads(target.read_text()) if target.exists() else []
    # Rebuilding the same correction updates its final derived scores, not its
    # original baseline or immutable call evidence. Other revisions remain.
    history = [r for r in history if r['input_results_sha256'] != receipt['input_results_sha256']]
    history.append(receipt)
    target.write_text(json.dumps(history, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'records': len(after), 'decision_changes': sum(c['before']['task_pass'] != c['after']['task_pass'] for c in changes),
                      'unmeasured': sum(not task_measured(r['metrics']) for r in after)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-ref', help='Explicit local git revision containing the original measured records')
    run(ROOT/'evaluation', parser.parse_args().baseline_ref)
