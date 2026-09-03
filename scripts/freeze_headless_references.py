"""Freeze independently captured web references without changing task rules."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.metrics import normalize_for_match


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--captures', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--browser-observations', type=Path)
    p.add_argument('--case-ids', help='Freeze a fresh short cohort, preserving task definitions')
    args = p.parse_args()
    if args.output.exists():
        p.error('Destination exists')
    dataset = json.loads((ROOT/'evaluation/dataset.json').read_text())
    if args.case_ids:
        wanted=set(args.case_ids.split(','))
        dataset['cases']=[c for c in dataset['cases'] if c['id'] in wanted]
        if len(dataset['cases'])!=len(wanted):p.error('Unknown case ID')
    observations = json.loads(args.browser_observations.read_text()) if args.browser_observations else {}
    audit = {r['case_id']: r for r in json.loads((args.captures/'audit.json').read_text())}
    changes = []
    for case in dataset['cases']:
        if case.get('track') != 'web':
            continue
        cid = case['id']
        if cid in observations:
            row = observations[cid]
            from harness.web_fetch.reference_lifecycle import _completion
            if row.get('independent') is not True:p.error('Browser observation is not independent')
            _completion(row.get('completion'))
            text = row['content_text']
            timestamp = row['checked_at']
            selector = row['selector']
            method = 'Independent visible browser DOM; complete declared scope'
        else:
            row = audit[cid]
            if row.get('status') != 200 or row.get('missing_facts'):
                p.error('Reference requires independent browser observation: ' + cid)
            text = (args.captures/cid/'reference.txt').read_text()
            timestamp = row['checked_at']
            selector = row['selector']
            method = 'Independent HTTP response and reviewed article selector'
        for fact in case.get('required_facts', []):
            if not any(normalize_for_match(a) in normalize_for_match(text) for a in fact['alternatives']):
                p.error('Required fact missing from independent reference: ' + cid + '/' + fact['id'])
        old = case['ground_truth_main_content']
        # Preserve exact original bytes when independently observed normalized
        # text is unchanged. No provider output is read by this script.
        if normalize_for_match(old) == normalize_for_match(text):
            text = old
        if text != old:
            changes.append({'case_id': cid, 'old_sha256': hashlib.sha256(old.encode()).hexdigest(),
                            'new_sha256': hashlib.sha256(text.encode()).hexdigest()})
        case['ground_truth_main_content'] = text
        case['source_verification'].update(state='valid', checked_at=timestamp, selector=selector,
            method=method, reference_sha256=hashlib.sha256(text.encode()).hexdigest())
    args.output.mkdir(parents=True)
    for folder in ('sources', 'list-references'):
        if folder=='sources':
            for case in dataset['cases']:
                source=ROOT/'evaluation'/folder/case['id']
                if source.exists():shutil.copytree(source,args.output/folder/case['id'])
        else:shutil.copytree(ROOT/'evaluation'/folder, args.output/folder)
    for case in dataset['cases']:
        if case.get('track') != 'web':
            continue
        cid = case['id']; target = args.output/'sources'/cid
        target.mkdir(parents=True, exist_ok=True)
        if cid in observations:
            (target/'current-browser-observation.json').write_text(json.dumps(observations[cid],ensure_ascii=False,indent=2))
        else:
            shutil.copy2(args.captures/cid/'source.html', target/'current-source.html.txt')
            shutil.copy2(args.captures/cid/'evidence.json', target/'current-capture.json')
        (target/'reference.txt').write_text(case['ground_truth_main_content'])
    (args.output/'dataset.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2)+'\n')
    (args.output/'reference-refresh.json').write_text(json.dumps({'changed':changes,
        'task_rules_changed':False, 'created_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2))
    print('Frozen web references:',sum(c.get('track')=='web' for c in dataset['cases']),'; changed full-text references:', len(changes))


if __name__ == '__main__':
    main()
