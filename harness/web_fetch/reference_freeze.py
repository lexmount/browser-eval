"""Freeze independent references; require explicit promotion for scope changes."""
import argparse
import hashlib
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, urlsplit

from harness.record_io import read_completed_records
from harness.web_fetch.artifacts import materialize_task_references
from harness.web_fetch.body_coverage import body_requirements
from harness.web_fetch.metrics import normalize_for_match
from harness.web_fetch.reference_inputs import input_tree, read_dataset_bytes
from harness.web_fetch.reference_lifecycle import (
    completed_case_records,
    reference_cases,
    requires_browser_observation,
    requires_fresh_reference,
    scope_identity,
    timestamp,
)
from harness.web_fetch.reference_quality import (
    clean_reference_text,
    reference_anchor_in_text,
)
from harness.web_fetch.scoring_regressions import valid_correction_provenance
from harness.web_fetch.source_capture import capture

ROOT = Path(__file__).resolve().parents[2]

REFERENCE_PROMOTION_REGRESSION_IDS = {
    'reference-scope-retains-main-content',
    'reference-scope-excludes-site-chrome',
}


def _digest_json(value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest()


def browser_observation_bytes(observation):
    """Return the exact portable bytes retained for one browser observation."""
    return (json.dumps(observation,ensure_ascii=False,indent=2)+'\n').encode()


def _source_revision(case, capture, *, browser_observation=False):
    url=case['url']
    if browser_observation:
        content_sha=hashlib.sha256(capture['content_text'].encode()).hexdigest()
        observation_sha=hashlib.sha256(browser_observation_bytes(capture)).hexdigest()
        return {'url':url,'revision':'browser-observation:'+content_sha,
                'rendered_content_sha256':content_sha,
                'browser_observation_sha256':observation_sha}
    oldid=parse_qs(urlsplit(url).query).get('oldid')
    snapshot=capture.get('sha256')
    if snapshot is None:
        snapshot=hashlib.sha256(capture['content_text'].encode()).hexdigest()
    revision='oldid:'+oldid[0] if oldid else 'snapshot:'+snapshot
    return {'url':url,'revision':revision,
            'source_snapshot_sha256':snapshot}


def _validate_approval(receipt):
    if (not isinstance(receipt.get('approved_by'),str) or not receipt['approved_by'].strip()
            or not isinstance(receipt.get('approved_at'),str) or not receipt['approved_at'].strip()):
        raise ValueError('Reference promotion receipt lacks explicit approval identity/time')
    try:
        approved=datetime.fromisoformat(receipt['approved_at'].replace('Z','+00:00'))
    except ValueError as error:
        raise ValueError('Reference promotion approval time is invalid') from error
    if approved.utcoffset() is None:
        raise ValueError('Reference promotion approval time requires an explicit timezone')


def _require_promotion(path, *, dataset_sha256, capture_audit_sha256,
                       candidate_dataset_sha256, changes):
    if not changes:
        if path is not None:
            raise ValueError('Reference promotion was supplied but no reference scope changed')
        return None
    if path is None:
        raise ValueError(
            'Reference text or body requirements changed; pass an explicit '
            '--reference-promotion receipt after reviewing the affected scope')
    if path.is_symlink():
        raise ValueError('Reference promotion receipt must not be a symlink')
    receipt=json.loads(path.read_text())
    expected={
        'schema_version':2,
        'kind':'reference_freeze_promotion',
        'input_dataset_sha256':dataset_sha256,
        'capture_audit_sha256':capture_audit_sha256,
        'candidate_dataset_sha256':candidate_dataset_sha256,
        'changes':changes,
    }
    if any(receipt.get(key)!=value for key,value in expected.items()):
        raise ValueError('Reference promotion receipt does not match the frozen change')
    _validate_approval(receipt)
    if not valid_correction_provenance(receipt):
        raise ValueError('Reference promotion receipt lacks valid correction provenance')
    cited=set(receipt['correction_provenance']['regression_ids'])
    if not REFERENCE_PROMOTION_REGRESSION_IDS.issubset(cited):
        raise ValueError('Reference promotion receipt lacks paired scope regressions')
    return receipt


def select_freeze_cases(dataset, case_ids=None, exclude_case_ids=None):
    """Return the exact requested subset; shared by prepare and freeze."""
    selected=deepcopy(dataset)
    selected.setdefault('comparison',{})['require_independent_result_review']=True
    if case_ids:
        wanted=set(case_ids.split(','))
        selected['cases']=[case for case in selected['cases'] if case['id'] in wanted]
        if len(selected['cases'])!=len(wanted):raise ValueError('Unknown case ID')
    if exclude_case_ids:
        excluded=set(exclude_case_ids.split(','))
        known={case['id'] for case in selected['cases']}
        if excluded-known:raise ValueError('Unknown excluded case ID')
        selected['cases']=[case for case in selected['cases'] if case['id'] not in excluded]
    return selected


def load_browser_observations(dataset, path=None, directory=None):
    if path and directory:
        raise ValueError('Choose one browser observation source')
    observations=json.loads(path.read_text()) if path else {}
    if not isinstance(observations,dict):
        raise ValueError('Browser observations must be keyed by case ID')
    if directory:
        for case in dataset['cases']:
            candidate=directory/case['id']/'current-browser-observation.json'
            if candidate.is_file():
                row=json.loads(candidate.read_text())
                if row.get('case_id') in observations:
                    raise ValueError('Duplicate browser observation: '+str(row.get('case_id')))
                observations[row['case_id']]=row
    cases={case['id']:case for case in dataset['cases']}
    unknown=set(observations)-set(cases)
    if unknown:
        raise ValueError('Unknown browser observation case: '+sorted(unknown)[0])
    for cid,row in observations.items():
        if not isinstance(row,dict) or row.get('case_id')!=cid:
            raise ValueError('Browser observation case ID mismatch: '+cid)
        if row.get('scope_identity')!=scope_identity(cases[cid]):
            raise ValueError('Browser observation scope identity mismatch: '+cid)
        binary=(row.get('observer') or {}).get('binary')
        if (not isinstance(binary,str) or Path(binary).is_absolute()
                or PureWindowsPath(binary).drive or '/' in binary or '\\' in binary):
            raise ValueError('Browser observation must not publish a local binary path: '+cid)
    return observations


def compute_reference_update(dataset, captures, observations=None):
    """Compute the one authoritative frozen dataset/change set from captures."""
    from harness.web_fetch.reference_lifecycle import _completion

    frozen=deepcopy(dataset);observations=observations or {}
    audit_rows=json.loads((captures/'audit.json').read_text())
    audit={row['case_id']:row for row in audit_rows}
    changes=[];contexts={}
    for case in frozen['cases']:
        cid=case['id']
        refresh=requires_fresh_reference(case)
        if not refresh:
            # A previously unavailable body reference must be allowed to
            # recover when a later independent HTTP capture is complete.  Do
            # not turn every still-blocked source into a publication blocker:
            # recovery is considered only after a successful, nonempty,
            # quality-clean capture containing every declared fact.
            row=audit.get(cid,{})
            reference=captures/cid/'reference.txt'
            recoverable=(
                case.get('track','web') in {'web','dynamic','challenge'}
                and case.get('task_kind','body')=='body'
                and not case.get('acceptance_contract')
                and bool(case.get('source_verification'))
                and not (case.get('ground_truth_main_content') or '').strip()
                and row.get('status')==200
                and row.get('state')=='captured'
                and not row.get('quality_issues')
                and reference.is_file()
                and bool(clean_reference_text(reference.read_text()).strip())
            )
            if recoverable:
                candidate=clean_reference_text(reference.read_text())
                recoverable=all(
                    any(reference_anchor_in_text(candidate,alt)
                        for alt in fact['alternatives'])
                    for fact in case.get('required_facts',[]))
            if not recoverable:
                continue
        if requires_browser_observation(case) and cid not in observations:
            raise ValueError('Reference requires independent browser observation: '+cid)
        if cid in observations:
            row=observations[cid]
            if row.get('independent') is not True:
                raise ValueError('Browser observation is not independent')
            _completion(row.get('completion'))
            raw_text=row['content_text'];text=clean_reference_text(raw_text)
            timestamp=row['checked_at'];selector=row['selector']
            method=('Independent rendered browser DOM; complete declared scope'
                    if row.get('observation_method') ==
                    'rendered_dom_text_including_preloaded_hidden_panels'
                    else 'Independent visible browser DOM; complete declared scope')
        else:
            row=audit[cid]
            if row.get('status')!=200:
                raise ValueError('Reference requires independent browser observation: '+cid)
            raw_text=(captures/cid/'reference.txt').read_text()
            text=clean_reference_text(raw_text);timestamp=row['checked_at']
            selector=row['selector']
            method='Independent HTTP response and reviewed article selector'
        missing_facts=[fact['id'] for fact in case.get('required_facts',[])
                       if not any(reference_anchor_in_text(text, alt)
                                  for alt in fact['alternatives'])]
        if missing_facts:
            raise ValueError('Required fact missing from independent reference: '
                             +cid+'/'+missing_facts[0])
        old=case['ground_truth_main_content']
        if normalize_for_match(old)==normalize_for_match(text):text=old
        old_requirements=case.get('body_requirements')
        new_requirements=(body_requirements(text) if old_requirements is not None else None)
        affected=[]
        if text!=old:affected.append('ground_truth_main_content')
        if new_requirements!=old_requirements:affected.append('body_requirements')
        if affected:
            changes.append({'case_id':cid,'affected_scope':affected,
                'old_reference_sha256':hashlib.sha256(old.encode()).hexdigest(),
                'new_reference_sha256':hashlib.sha256(text.encode()).hexdigest(),
                'old_body_requirements_sha256':_digest_json(old_requirements),
                'new_body_requirements_sha256':_digest_json(new_requirements),
                'source_revision':_source_revision(
                    case,row,browser_observation=cid in observations)})
        case['ground_truth_main_content']=text
        if old_requirements is not None:case['body_requirements']=new_requirements
        case['source_verification'].update(state='valid',checked_at=timestamp,
            selector=selector,method=method,
            reference_sha256=hashlib.sha256(text.encode()).hexdigest())
        if 'observation_route' in case['source_verification']:
            case['source_verification']['observation_route'] = (
                'browser' if cid in observations else 'http')
        if cid not in observations:
            case['source_verification'].update(http_status=row['status'],
                source_snapshot_sha256=row['sha256'],
                extracted_reference_sha256=row.get('extracted_sha256'))
            if row.get('extracted_sha256')==hashlib.sha256(text.encode()).hexdigest():
                case['source_verification'].pop('extracted_reference_sha256',None)
                case['source_verification'].pop('reference_normalization',None)
        else:
            case['source_verification'].update(
                rendered_content_sha256=hashlib.sha256(raw_text.encode()).hexdigest(),
                browser_observation_sha256=hashlib.sha256(
                    browser_observation_bytes(row)).hexdigest())
            for key in ('http_status','source_snapshot_sha256',
                        'extracted_reference_sha256','reference_normalization'):
                case['source_verification'].pop(key,None)
        if raw_text!=text:
            case['source_verification'].update(
                extracted_reference_sha256=hashlib.sha256(raw_text.encode()).hexdigest(),
                reference_normalization=(
                    'Remove U+200B, U+FEFF and discretionary U+00AD; raw capture unchanged.'))
        contexts[cid]={'row':row,'missing_facts':missing_facts,
                       'browser_observation':cid in observations}
    return frozen,changes,contexts


def main(argv=None):
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
    p.add_argument('--reference-promotion',type=Path,
                   help='Reviewed receipt required when reference text/body requirements change')
    args = p.parse_args(argv)
    if args.output.exists():
        p.error('Destination exists')
    dataset_bytes=read_dataset_bytes(args.dataset)
    try:
        dataset=select_freeze_cases(json.loads(dataset_bytes),args.case_ids,
                                    args.exclude_case_ids)
        observations=load_browser_observations(
            dataset,args.browser_observations,args.browser_observation_directory)
        dataset,changes,contexts=compute_reference_update(
            dataset,args.captures,observations)
    except (OSError,KeyError,ValueError,json.JSONDecodeError) as error:
        p.error(str(error))
    audit_path=args.captures/'audit.json'
    audit_bytes=audit_path.read_bytes()
    try:
        promotion=_require_promotion(
            args.reference_promotion,
            dataset_sha256=hashlib.sha256(dataset_bytes).hexdigest(),
            capture_audit_sha256=hashlib.sha256(audit_bytes).hexdigest(),
            candidate_dataset_sha256=hashlib.sha256(json.dumps(
                dataset,ensure_ascii=False,indent=2).encode()+b'\n').hexdigest(),
            changes=changes,
        )
    except (OSError,ValueError,json.JSONDecodeError) as error:
        p.error(str(error))
    input_trees = {folder: input_tree(args.dataset, folder) for folder in ('sources', 'list-references')}
    args.output.mkdir(parents=True)
    materialize_task_references(args.output, input_trees, [case['id'] for case in dataset['cases']])
    for case in dataset['cases']:
        if not requires_fresh_reference(case):
            continue
        cid = case['id']; target = args.output/'sources'/cid
        target.mkdir(parents=True, exist_ok=True)
        if cid in observations:
            (target/'current-browser-observation.json').write_bytes(
                browser_observation_bytes(observations[cid]))
        else:
            shutil.copy2(args.captures/cid/'source.html', target/'current-source.html.txt')
            context=contexts[cid];row=context['row']
            capture_record = dict(row)
            capture_record['missing_facts'] = context['missing_facts']
            capture_record['quality_issues'] = [
                issue for issue in row.get('quality_issues', [])
                if not (issue.startswith('fact_absent_from_reference:')
                        or issue.startswith('duplicate_fact:'))]
            capture_record['state'] = ('captured' if row.get('status') == 200
                                       and not capture_record['quality_issues'] else 'needs_review')
            (target/'current-capture.json').write_text(
                json.dumps(capture_record,ensure_ascii=False,indent=2)+'\n')
        (target/'reference.txt').write_text(case['ground_truth_main_content'])
    (args.output/'dataset.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2)+'\n')
    refresh={'schema_version':1,'changed':changes,
        'reference_changed':bool(changes),'task_rules_changed':bool(changes),
        'created_at':datetime.now(timezone.utc).isoformat()}
    if promotion is not None:
        shutil.copy2(args.reference_promotion,args.output/'reference-promotion.json')
        promotion_sha256=hashlib.sha256(
            (args.output/'reference-promotion.json').read_bytes()).hexdigest()
        refresh['reference_promotion_sha256']=promotion_sha256
        dataset.setdefault('comparison',{})['reference_freeze_promotion']={
            'receipt_sha256':promotion_sha256,
            'kind':promotion['kind'],
            'approved_by':promotion['approved_by'],
            'approved_at':promotion['approved_at'],
            'changes':promotion['changes'],
        }
        (args.output/'dataset.json').write_text(
            json.dumps(dataset,ensure_ascii=False,indent=2)+'\n')
    (args.output/'reference-refresh.json').write_text(
        json.dumps(refresh,ensure_ascii=False,indent=2)+'\n')
    print('Frozen exact-reference cases:',sum(requires_fresh_reference(c) for c in dataset['cases']),'; changed full-text references:', len(changes))


def load_resumable_observations(checkpoint, cases):
    if not checkpoint.exists():
        return {}
    return {row['case_id']:row for row in json.loads(checkpoint.read_text())
            if row.get('case_id') in cases}


def postcheck_main(argv=None):
    p=argparse.ArgumentParser(
        description='Acquire independent post-call observations for completed matched cases.')
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--captures',type=Path,required=True)
    p.add_argument('--browser-observations',type=Path,
                   help='Post-call browser observations for browser-contract or HTTP-unavailable references')
    p.add_argument('--timeout',type=int,default=3600)
    p.add_argument('--resume',action='store_true')
    args=p.parse_args(argv)
    dataset=json.loads((args.run_dir/'dataset.json').read_text())
    if args.timeout < 0:
        p.error('--timeout must be nonnegative')
    cases=reference_cases(dataset)
    if args.captures.exists() and not args.resume:p.error('Capture destination exists')
    args.captures.mkdir(parents=True,exist_ok=args.resume)
    pending={};done={};started=time.monotonic()
    checkpoint=args.captures/'observations.json'
    if args.resume and checkpoint.exists():
        done=load_resumable_observations(checkpoint,cases)
    def observe(case):
        begin=time.monotonic()
        existing=args.captures/case['id']/'evidence.json'
        row=json.loads(existing.read_text()) if args.resume and existing.exists() else capture(case,args.captures)
        text_path=args.captures/case['id']/'reference.txt'
        return {'case_id':case['id'],'independent':True,
            'checked_at':row['checked_at'],
            'content_text':text_path.read_text() if text_path.exists() else '',
            'scope_identity':scope_identity(case),
            'completion':{'satisfied':row.get('status')==200 and not row.get('missing_facts'),
                          'elapsed_ms':(time.monotonic()-begin)*1000,'timeout_ms':120000}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        while len(done)<len(cases):
            records=read_completed_records(args.run_dir/'results.jsonl')
            for cid,case in cases.items():
                relevant=completed_case_records(dataset,case,records)
                if relevant is None or cid in done or cid in pending:
                    continue
                rows=(json.loads(args.browser_observations.read_text())
                      if args.browser_observations and args.browser_observations.exists() else {})
                if requires_browser_observation(case) or cid in rows:
                    if cid not in rows:continue
                    row=rows.get(cid,{})
                    latest=max(timestamp(r.get('completed_at',r['started_at'])) for r in relevant)
                    if timestamp(row.get('checked_at'))>latest:
                        done[cid]={'case_id':cid,**row,'scope_identity':scope_identity(case)}
                    continue
                pending[cid]=pool.submit(observe,case)
            for cid,future in list(pending.items()):
                if future.done():
                    done[cid]=future.result();del pending[cid]
            (args.captures/'observations.json').write_text(json.dumps(list(done.values()),ensure_ascii=False,indent=2)+'\n')
            if len(done)<len(cases):
                if time.monotonic()-started>=args.timeout:
                    raise TimeoutError('Post-call observation deadline')
                time.sleep(1)
    print('Post-call observations complete:',len(done),flush=True)
