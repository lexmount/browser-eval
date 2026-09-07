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
from harness.web_fetch.body_coverage import body_requirements
from harness.web_fetch.reference_lifecycle import requires_fresh_reference
from harness.web_fetch.reference_quality import clean_reference_text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--captures', type=Path, required=True)
    p.add_argument('--dataset', type=Path, default=ROOT/'evaluation/dataset.json',
                   help='Task dataset to freeze; defaults to the repository canonical dataset')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--browser-observations', type=Path)
    p.add_argument('--browser-observation-directory', type=Path,
                   help='Sources root containing <case>/current-browser-observation.json files')
    p.add_argument('--case-ids', help='Freeze a fresh short cohort, preserving task definitions')
    p.add_argument('--exclude-case-ids', help='Comma-separated cases to omit when a fresh independent reference is unavailable')
    args = p.parse_args()
    if args.output.exists():
        p.error('Destination exists')
    dataset = json.loads(args.dataset.read_text())
    dataset.setdefault('comparison', {})['require_independent_result_review'] = True
    if args.case_ids:
        wanted=set(args.case_ids.split(','))
        dataset['cases']=[c for c in dataset['cases'] if c['id'] in wanted]
        if len(dataset['cases'])!=len(wanted):p.error('Unknown case ID')
    if args.exclude_case_ids:
        excluded = set(args.exclude_case_ids.split(','))
        known = {c['id'] for c in dataset['cases']}
        if excluded - known:p.error('Unknown excluded case ID')
        dataset['cases'] = [c for c in dataset['cases'] if c['id'] not in excluded]
    if args.browser_observations and args.browser_observation_directory:
        p.error('Choose one browser observation source')
    observations = json.loads(args.browser_observations.read_text()) if args.browser_observations else {}
    if args.browser_observation_directory:
        for case in dataset['cases']:
            candidate=args.browser_observation_directory/case['id']/'current-browser-observation.json'
            if candidate.is_file():
                row=json.loads(candidate.read_text())
                observations[row['case_id']]=row
    audit = {r['case_id']: r for r in json.loads((args.captures/'audit.json').read_text())}
    changes = []
    for case in dataset['cases']:
        if not requires_fresh_reference(case):
            continue
        cid = case['id']
        if cid in observations:
            row = observations[cid]
            from harness.web_fetch.reference_lifecycle import _completion
            if row.get('independent') is not True:p.error('Browser observation is not independent')
            _completion(row.get('completion'))
            raw_text = row['content_text']
            text = clean_reference_text(raw_text)
            timestamp = row['checked_at']
            selector = row['selector']
            method = ('Independent rendered browser DOM; complete declared scope'
                      if row.get('observation_method') == 'rendered_dom_text_including_preloaded_hidden_panels'
                      else 'Independent visible browser DOM; complete declared scope')
        else:
            row = audit[cid]
            if row.get('status') != 200 or row.get('missing_facts'):
                p.error('Reference requires independent browser observation: ' + cid)
            raw_text = (args.captures/cid/'reference.txt').read_text()
            text = clean_reference_text(raw_text)
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
        if case.get('body_requirements') is not None:
            case['body_requirements'] = body_requirements(text)
        case['source_verification'].update(state='valid', checked_at=timestamp, selector=selector,
            method=method, reference_sha256=hashlib.sha256(text.encode()).hexdigest())
        if raw_text != text:
            case['source_verification'].update(
                extracted_reference_sha256=hashlib.sha256(raw_text.encode()).hexdigest(),
                reference_normalization='Remove U+200B, U+FEFF and discretionary U+00AD; raw capture unchanged.')
    args.output.mkdir(parents=True)
    for folder in ('sources', 'list-references'):
        if folder=='sources':
            for case in dataset['cases']:
                source=ROOT/'evaluation'/folder/case['id']
                if source.exists():shutil.copytree(source,args.output/folder/case['id'])
        else:shutil.copytree(ROOT/'evaluation'/folder, args.output/folder)
    for case in dataset['cases']:
        if not requires_fresh_reference(case):
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
    print('Frozen exact-reference cases:',sum(requires_fresh_reference(c) for c in dataset['cases']),'; changed full-text references:', len(changes))


if __name__ == '__main__':
    main()
