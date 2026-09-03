"""Apply reviewed mixed task rules and rescore saved outputs, without API calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.stable_annotations import annotate_case, bind_annotation_evidence, CASE_ANNOTATIONS
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
from scripts.rescore_content import rescore


def apply(source: Path):
    source = source.resolve()
    originals = {name: contained_path(source, name).read_bytes()
                 for name in ('dataset.json', 'results.jsonl')}
    dataset = json.loads(originals['dataset.json'])
    records = [json.loads(line) for line in originals['results.jsonl'].splitlines()]
    validate_report_ids(dataset, records)
    old_cases = {c['id']: c for c in dataset['cases']}
    ids = set(old_cases) & CASE_ANNOTATIONS.keys()
    updated = {c['id']: annotate_case(c) for c in dataset['cases']}
    for key in ids:
        bind_annotation_evidence(updated[key], source)
    hashes = {key: hashlib.sha256(json.dumps(updated[key]['acceptance_contract'],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest() for key in ids}
    scoring_source_sha256 = hashlib.sha256(b'\0'.join((ROOT/'harness/web_fetch'/name).read_bytes()
        for name in ('stable_annotations.py', 'stable_tasks.py', 'metrics.py', 'reference_quality.py', 'dynamic_lists.py', 'scoring.py'))).hexdigest()
    if (all(updated[k] == old_cases[k] for k in ids) and
            all(r['metrics'].get('acceptance_contract_sha256') == hashes[r['case_id']]
                and r.get('scoring', {}).get('annotation_source_sha256') == scoring_source_sha256
                and r['metrics'].get('scoring_source_sha256') == SCORING_SOURCE_SHA256
                for r in records if r['case_id'] in ids)):
        return {'changed': False, 'reason': 'Current rules already applied'}
    for row in records:
        raw = contained_path(source, row['artifact_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['metrics']['content_sha256']:
            raise ValueError('Output integrity failure: ' + row['attempt_id'])
    before = {r['attempt_id']: r.get('scoring', {}).get('previous_task_pass',
                task_passed(r['ok'], r['metrics'], old_cases[r['case_id']].get('task_kind', 'body')))
              for r in records}
    scored_at = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix='stable-rescore-', dir=source.parent) as temporary:
        staging = Path(temporary)/'evaluation'
        shutil.copytree(source, staging)
        dataset['cases'] = [updated[c['id']] for c in dataset['cases']]
        (staging/'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2)+'\n')
        rescore(staging, case_ids=ids)
        new_records = [json.loads(line) for line in (staging/'results.jsonl').read_text().splitlines()]
        changes = []
        for old, new in zip(records, new_records):
            if old['case_id'] not in ids:
                assert old == new, 'Unselected record changed'
                continue
            for key in old.keys() - {'metrics', 'diagnosis', 'failure_category', 'scoring'}:
                assert old[key] == new[key], 'Original call metadata changed: ' + key
            after = task_passed(new['ok'], new['metrics'], updated[new['case_id']].get('task_kind', 'body'))
            new['scoring'] = {**new.get('scoring',{}), 'mode': 'offline_rescore', 'scored_at': scored_at,
                'annotation_source_sha256': scoring_source_sha256,
                'rule_sha256': hashes[new['case_id']], 'previous_task_pass': before[new['attempt_id']],
                'task_decision': 'stable_text_and_record_contract',
                'word_metric_scope': updated[new['case_id']]['source_benchmark']['snapshot_metric_scope']}
            if after != before[new['attempt_id']]:
                changes.append({'attempt_id': new['attempt_id'], 'before': before[new['attempt_id']], 'after': after})
        (staging/'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in new_records))
        for name, raw in originals.items():
            if contained_path(source, name).read_bytes() != raw:
                raise ValueError('Canonical data changed during rescoring')
        try:
            for name in originals:
                pending = contained_path(source, name+'.next')
                shutil.copyfile(staging/name, pending)
                pending.replace(source/name)
        except BaseException:
            for name, raw in originals.items():
                (source/name).write_bytes(raw)
            raise
    return {'changed': True, 'mode': 'offline_rescore', 'scored_at': scored_at,
            'cases': len(ids), 'records': sum(r['case_id'] in ids for r in records),
            'original_outputs_and_call_metadata_unchanged': True, 'task_result_changes': changes}


if __name__ == '__main__':
    print(json.dumps(apply(ROOT/'evaluation'), ensure_ascii=False, indent=2))
