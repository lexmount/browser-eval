"""Build a score audit of a published WebFetch snapshot with the native renderer."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from harness.record_io import canonical_json_sha256, file_sha256, load_jsonl
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
from .scoring_revision import ALLOWED_FIELDS, correction_provenance


def _bound(root, binding):
    path = contained_path(root, binding['path'], reject_symlinks=True)
    if file_sha256(path) != binding['sha256']:
        raise ValueError('Snapshot audit input changed: ' + binding['path'])
    return path


def validate_snapshot_audit(source, profile_path):
    """Validate the full matrix, immutable calls and every score-bearing view."""
    source, profile_path = Path(source).resolve(), Path(profile_path).resolve()
    config = json.loads(profile_path.read_text())
    if config.get('variant') != 'published-snapshot-audit' or config.get('certified') is not False:
        raise ValueError('Published snapshot audits are explicitly diagnostic')
    inputs = {key: _bound(profile_path.parent, value) for key, value in config['inputs'].items()}
    snapshot = json.loads(inputs['snapshot_manifest'].read_text())
    paths = list(source.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise ValueError('Published snapshots reject symlinks')
    actual = {path.relative_to(source).as_posix() for path in paths if path.is_file()}
    if actual - {'.generated-report'} != set(snapshot['files']):
        raise ValueError('Published snapshot has missing or undeclared files')
    if set(config.get('source',{})) != {'url','version','archive_sha256'}:
        raise ValueError('Snapshot source needs a pinned public identity')
    for relative, item in snapshot['files'].items():
        if file_sha256(contained_path(source, relative, reject_symlinks=True)) != item['sha256']:
            raise ValueError('Published snapshot changed: '+relative)
    baseline = json.loads(contained_path(source, 'dataset.json').read_text())
    previous = load_jsonl(contained_path(source, 'results.jsonl'))
    dataset = json.loads(inputs['dataset'].read_text())
    records = load_jsonl(inputs['results'])
    receipt = json.loads(inputs['receipt'].read_text())
    audit = json.loads(inputs['review'].read_text())
    old_cases = {row['id']: row for row in baseline['cases']}
    cases = {row['id']: row for row in dataset['cases']}
    old_rows = {row['attempt_id']: row for row in previous}
    rows = {row['attempt_id']: row for row in records}
    if (len(cases) != len(dataset['cases']) or cases.keys() != old_cases.keys()
            or dataset.get('coverage_plan') != baseline.get('coverage_plan')
            or len(rows) != len(records) or rows.keys() != old_rows.keys()):
        raise ValueError('Snapshot audit changed the task-by-method matrix')
    if (receipt.get('kind') != 'published_snapshot_rescore' or receipt.get('provider_calls_added') != 0
            or receipt.get('input_dataset_sha256') != file_sha256(source / 'dataset.json')
            or receipt.get('input_results_sha256') != file_sha256(source / 'results.jsonl')
            or receipt.get('scoring_source_sha256') != SCORING_SOURCE_SHA256
            or receipt.get('corrected_dataset_sha256') != canonical_json_sha256(dataset)):
        raise ValueError('Snapshot audit does not bind the saved calls and current scorer')
    correction_provenance(**config['correction_provenance'])
    for identifier, old in old_cases.items():
        changed = {key for key in old.keys() | cases[identifier].keys() if old.get(key) != cases[identifier].get(key)}
        if changed - ALLOWED_FIELDS:
            raise ValueError('Snapshot audit changed collection inputs: ' + identifier)
    views = {item['attempt_id']: item for item in receipt['inputs']}
    if len(views) != len(records) or views.keys() != rows.keys():
        raise ValueError('Snapshot audit lacks complete scoring input provenance')
    for identifier, old in old_rows.items():
        row, view = rows[identifier], views[identifier]
        facts = lambda value: {key: item for key, item in value.items()
                              if key not in {'metrics', 'scoring', 'diagnosis', 'failure_category'}}
        if facts(old) != facts(row):
            raise ValueError('Snapshot audit changed a physical call: ' + identifier)
        artifact = contained_path(source, old['artifact_path'], reject_symlinks=True)
        if (view['path'] != old['artifact_path'] or view['published_sha256'] != file_sha256(artifact)
                or view['original_sha256'] != old['metrics']['content_sha256']
                or row['metrics']['content_sha256'] != view['scored_sha256']
                or row['metrics']['scoring_source_sha256'] != SCORING_SOURCE_SHA256
                or row.get('scoring', {}).get('artifact_view') != view['view']):
            raise ValueError('Snapshot audit artifact identity changed: ' + identifier)
        expected = {'original': view['original_sha256'], 'recovered_original': view['original_sha256'],
                    'published_projection': view['published_sha256']}.get(view['view'])
        if (view['scored_sha256'] != expected
                or view['view'] == 'original' and view['original_sha256'] != view['published_sha256']
                or view['view'] == 'published_projection' and view['original_sha256'] == view['published_sha256']):
            raise ValueError('Snapshot audit confuses original and public bytes')
        if view['view'] not in {'original', 'recovered_original', 'published_projection'}:
            raise ValueError('Unknown snapshot scoring view')
    historic = json.loads(inputs['historical_verdicts'].read_text())['verdicts']
    initial = {row['attempt_id']:row for row in load_jsonl(inputs['initial_results'])}
    if set(historic) != rows.keys() or initial.keys() != rows.keys() or any(type(v) is not bool for v in historic.values()):
        raise ValueError('Snapshot audit comparison lacks the complete original matrix')
    current = {key:task_passed(row['ok'],row['metrics'],cases[row['case_id']].get('task_kind','body')) for key,row in rows.items()}
    first = {key:task_passed(row['ok'],row['metrics'],old_cases[row['case_id']].get('task_kind','body')) for key,row in initial.items()}
    required_reviews = {key for key in rows if historic[key] != first[key] or historic[key] != current[key]}
    if {item['attempt_id'] for item in audit.get('changes',[])} != required_reviews:
        raise ValueError('Snapshot audit omits a changed decision')
    expected_providers = {row['provider'] for row in rows.values()}
    if {item['provider'] for item in audit['providers']} != expected_providers:
        raise ValueError('Snapshot audit omits a participant')
    for item in audit['providers']:
        keys = [key for key,row in rows.items() if row['provider']==item['provider']]
        expected = {'total':len(keys),'historical_passes':sum(historic[k] for k in keys),
                    'initial_rescore_passes':sum(first[k] for k in keys),'corrected_passes':sum(current[k] for k in keys)}
        if any(item.get(key) != value for key,value in expected.items()):
            raise ValueError('Snapshot audit comparison counts differ: '+item['provider'])
    if 'method_registry' in inputs:
        methods=json.loads(inputs['method_registry'].read_text())
        if set(methods) != expected_providers or any(not m.get('engine') or not m.get('label') for m in methods.values()):
            raise ValueError('Snapshot audit method registry is incomplete')
        for row in records:
            engine=row.get('provider_metadata',{}).get('engine')
            if engine and engine != methods[row['provider']]['engine']:
                raise ValueError('Snapshot audit method registry changes the measured engine')
    reviewed = audit.get('changes', [])
    review_ids = [item['attempt_id'] for item in reviewed]
    if len(review_ids) != len(set(review_ids)) or not set(review_ids) <= rows.keys():
        raise ValueError('Snapshot audit review scope is invalid')
    for item in reviewed:
        row = rows[item['attempt_id']]
        if (item.get('after_fix_pass') != task_passed(row['ok'], row['metrics'], cases[row['case_id']].get('task_kind','body'))
                or any(not item.get(key) for key in ('requirement', 'actual', 'reason', 'treatment', 'classification'))):
            raise ValueError('Snapshot audit lacks a concrete, current disposition: ' + item['attempt_id'])
        for binding in item.get('evidence', []):
            _bound(source, binding)
    if audit.get('total_attempts') != len(records) or audit.get('reviewed_attempts') != len(reviewed):
        raise ValueError('Snapshot audit review counts differ')
    return config, inputs, dataset, records, receipt, audit


def build_snapshot_audit(source, output, *, profile):
    """Keep the original report routes and metric populations, adding score review."""
    from .report import generate
    from .report_build import _install_report_tree, write_frozen_result_summary
    from harness.reporting.templates import resolve_template
    from harness.report_languages import generated_languages
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Snapshot audit needs a separate output tree')
    if output.exists() and not (output / '.generated-report').is_file():
        raise ValueError('Refusing to replace an unmarked output')
    config, inputs, dataset, records, receipt, audit = validate_snapshot_audit(source, profile)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.snapshot-audit-', dir=output.parent) as work:
        staging = Path(work) / 'report'
        shutil.copytree(source, staging)
        shutil.rmtree(staging / 'en', ignore_errors=True)
        history = staging / 'audit/history'
        history.mkdir(parents=True)
        for name in ('dataset.json', 'results.jsonl', 'result-review.json'):
            if (staging / name).is_file():
                shutil.copy2(staging / name, history / name)
        for item in audit['changes']:
            for evidence in item.get('evidence',[]):
                if evidence.get('historical'):
                    target=contained_path(history,evidence['path'])
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(contained_path(source,evidence['path']),target)
        (staging / 'result-review.json').unlink(missing_ok=True)
        (staging / 'archived-reading.json').unlink(missing_ok=True)
        for key, name in [('historical_verdicts','historical-verdicts.json'), ('initial_results','initial-results.jsonl'), ('receipt','scoring-inputs.json'), ('review','score-changes.json'), ('figure','scoring-explained.png'), ('interpretation','interpretation.json')]:
            shutil.copy2(inputs[key], staging / 'audit' / name)
        if 'release_history' in inputs:
            (staging/'report-assets').mkdir(exist_ok=True)
            shutil.copy2(inputs['release_history'],staging/'report-assets/report-changes.json')
        comparison = dataset.setdefault('comparison', {})
        if 'method_registry' in inputs:
            comparison['method_registry'] = json.loads(inputs['method_registry'].read_text())
        for key in ('independent_review', 'report_interpretation'):
            comparison.pop(key, None)
        comparison['report_interpretation'] = {'path':'audit/interpretation.json','sha256':file_sha256(staging/'audit/interpretation.json')}
        comparison['require_independent_result_review'] = False
        comparison['publication_state'] = 'published_snapshot_score_audit'
        comparison['score_audit'] = {'path': 'audit/score-changes.json',
            'sha256': file_sha256(staging / 'audit/score-changes.json'),
            'scoring_receipt': 'audit/scoring-inputs.json',
            'source': config['source'], 'reviewed_attempts': len(audit['changes']),
            'total_attempts': len(records), 'projected_scoring_inputs': receipt['projected_scoring_inputs'],
            'recovered_originals': receipt['recovered_originals'],
            'figure': 'audit/scoring-explained.png'}
        (staging / 'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2)+'\n')
        shutil.copy2(inputs['results'], staging / 'results.jsonl')
        batches = json.loads((staging / 'execution.json').read_text())['batches']
        generate(staging, dataset, records, batches, authored_interpretation=True, frozen_scores=True)
        write_frozen_result_summary(staging, dataset, records)
        (staging / '.generated-report').write_text('webfetch\n')
        manifest = {'schema_version':1, 'template':resolve_template('webfetch'),
            'evidence_state':'published_snapshot_score_audit', 'certified':False,
            'dataset_sha256':file_sha256(staging/'dataset.json'),
            'results_sha256':file_sha256(staging/'results.jsonl'),
            'generated_languages':list(generated_languages(staging)),
            'score_audit':comparison['score_audit'], 'profile_sha256':file_sha256(profile)}
        (staging/'report-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        check_snapshot_audit(staging)
        _install_report_tree(staging, output)
    return output / 'index.html'


def check_snapshot_audit(root):
    """Deterministic checks for matrix values, disclosure and reviewed evidence."""
    from bs4 import BeautifulSoup
    from .report_check import check_saved_result_summary
    root = Path(root)
    manifest=json.loads((root/'report-manifest.json').read_text())
    if manifest.get('evidence_state')!='published_snapshot_score_audit' or manifest.get('certified') is not False:
        raise ValueError('Snapshot audit lost its diagnostic identity')
    for name in ['dataset','results']:
        relative='dataset.json' if name=='dataset' else 'results.jsonl'
        if manifest.get(name+'_sha256')!=file_sha256(root/relative):
            raise ValueError('Snapshot audit input changed: '+name)
    dataset = json.loads((root/'dataset.json').read_text())
    rows = load_jsonl(root/'results.jsonl')
    binding = dataset['comparison']['score_audit']
    audit = json.loads(_bound(root, binding).read_text())
    check_saved_result_summary(root, dataset, rows)
    for relative in ['index.html','guide/index.html','audit/index.html','analysis/index.html']:
        soup = BeautifulSoup((root/relative).read_text(), 'html.parser')
        if not soup.select_one('[data-evidence-state="published_snapshot_score_audit"]'):
            raise ValueError('Snapshot report lost its evidence disclosure: '+relative)
    audit_html = BeautifulSoup((root/'audit/index.html').read_text(), 'html.parser')
    from .report_explainer import check_current_browser_article
    from harness.reporting.presentation import check_presentation
    check_current_browser_article(root,{case['id']:case for case in dataset['cases']},rows,dataset['comparison']['report_interpretation'])
    for route in ['index.html','guide/index.html','analysis/index.html','audit/index.html']:
        check_presentation((root/route).read_text())
    for item in audit['providers']:
        element = audit_html.select_one('[data-audit-summary-provider="'+item['provider']+'"]')
        expected = [str(item[key]) for key in ['published_passes','historical_passes','initial_rescore_passes','corrected_passes','total']]
        if element is None or [cell.get_text(' ',strip=True) for cell in element.select('td')][-5:] != expected:
            raise ValueError('Snapshot audit rendered comparison differs: '+item['provider'])
    cases = {case['id']:case for case in dataset['cases']}
    indexed = {row['attempt_id']:row for row in rows}
    for item in audit['changes']:
        identifier=item['attempt_id'];row=indexed[identifier]
        article=audit_html.find(id=identifier)
        if article is None or item['reason'] not in article.get_text():
            raise ValueError('Snapshot audit lost a reviewed disposition: '+identifier)
        if item['after_fix_pass'] != task_passed(row['ok'],row['metrics'],cases[row['case_id']].get('task_kind','body')):
            raise ValueError('Snapshot audit verdict differs: '+identifier)
        for evidence in item.get('evidence',[]):
            archived={**evidence,'path':'audit/history/'+evidence['path']} if evidence.get('historical') else evidence
            _bound(root,archived)
        detail=BeautifulSoup((root/'attempts'/f'{identifier}.html').read_text(),'html.parser')
        if not detail.select_one('[data-score-audit]'):
            raise ValueError('Snapshot result lost its audit explanation: '+identifier)
    return f'PASS: {len(cases)} tasks, {len(rows)} saved calls, {len(audit["changes"])} reviewed changes; zero new calls'


def read_snapshot_public_file(root, binding):
    """Inspect original bytes or a hash-bound historical publication projection."""
    path = contained_path(root, binding['path'], reject_symlinks=True)
    observed = file_sha256(path)
    if observed == binding['sha256']:
        return path
    privacy = json.loads(contained_path(root, 'publication-privacy.json').read_text())
    proof = next((item for item in privacy['files'] if item['path'] == binding['path']), {})
    if (proof.get('original_sha256') != binding['sha256']
            or proof.get('published_sha256') != observed
            or proof.get('published_bytes') != path.stat().st_size):
        raise ValueError('Unbound historical publication view: '+binding['path'])
    return path


def check_snapshot_visual(root, receipt):
    """Exercise native route classes, themes, narrow layouts and evidence dialogs."""
    from playwright.sync_api import sync_playwright
    from harness.reporting.visual import browser_executable, local_report_server, settle_theme
    root, receipt = Path(root).resolve(), Path(receipt).resolve()
    if receipt == root or root in receipt.parents:
        raise ValueError('Visual receipts belong outside the public report')
    check_snapshot_audit(root)
    dataset=json.loads((root/'dataset.json').read_text())
    rows=load_jsonl(root/'results.jsonl');cases={c['id']:c for c in dataset['cases']}
    routes=['index.html','guide/index.html','audit/index.html','analysis/index.html',
            'datasets/index.html','methodology/index.html','review/index.html',
            'changes/index.html','adoption/index.html','reproduce/index.html','accounts/index.html','en/index.html']
    for folder in ['cases','providers']:
        pages=sorted((root/folder).glob('*.html'))
        if pages:routes.append(pages[0].relative_to(root).as_posix())
    for passed in [False,True]:
        row=next(r for r in rows if task_passed(r['ok'],r['metrics'],cases[r['case_id']].get('task_kind','body')) is passed)
        routes.append('attempts/'+row['attempt_id']+'.html')
    states=[];interactions=[]
    receipt.parent.mkdir(parents=True,exist_ok=True)
    with local_report_server(root) as origin, sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=browser_executable(),headless=True)
        page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        for route in routes:
            for width,height in [(1440,1000),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                for theme in ['light','dark']:
                    page.goto(origin+'/'+route,wait_until='networkidle')
                    page.evaluate('(t)=>{document.documentElement.dataset.theme=t;document.body.classList.toggle("dark",t==="dark");localStorage.setItem("theme",t)}',theme)
                    settle_theme(page)
                    page.evaluate('() => Promise.all([...document.images].map(i=>{i.loading="eager";return i.decode().catch(()=>{})}))')
                    state=page.evaluate('''() => ({overflow:document.documentElement.scrollWidth-innerWidth,
                        brokenImages:[...document.images].filter(i=>!i.complete||i.naturalWidth===0).map(i=>i.getAttribute('src')),
                        style:document.styleSheets.length})''')
                    state.update(route=route,width=width,height=height,theme=theme);states.append(state)
                    if abs(state['overflow'])>2 or state['brokenImages'] or (not state['style'] and route!='en/index.html') or errors:
                        raise ValueError('Snapshot visual check failed: '+json.dumps({'state':state,'errors':errors}))
                    if route in ['index.html','guide/index.html','audit/index.html']:
                        page.screenshot(path=str(receipt.parent/(route.split('/')[0]+'-'+str(width)+'-'+theme+'.png')),full_page=False)
            if route.startswith('attempts/'):
                page.goto(origin+'/'+route,wait_until='networkidle')
                button=page.locator('[data-call-log-open]').first
                if button.count():
                    button.evaluate('(b)=>{for(let e=b.parentElement;e;e=e.parentElement)if(e.tagName==="DETAILS")e.open=true}')
                    button.click();page.locator('dialog[open]').wait_for()
                    if not page.locator('dialog[open]').inner_text().strip():raise ValueError('Empty evidence dialog')
                    page.keyboard.press('Escape')
                    if page.locator('dialog[open]').count():raise ValueError('Evidence dialog did not close')
                    interactions.append({'route':route,'open_close':'passed'})
                page.locator('header a[href]').first.click();page.wait_for_load_state('networkidle')
                if not page.url.endswith('/index.html'):raise ValueError('Nested report navigation failed')
        browser.close()
    receipt.write_text(json.dumps({'schema':'webfetch_snapshot_visual/1','states':states,'interactions':interactions,'errors':errors},ensure_ascii=False,indent=2)+'\n')
    return f'PASS: {len(states)} visual states and {len(interactions)} evidence dialogs'
