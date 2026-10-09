"""Synthetic independent observations for reference promotion boundaries."""
import hashlib
import json

from harness.web_fetch.body_coverage import body_requirements
from harness.web_fetch.reference_lifecycle import scope_identity

ROOT_CAUSE=('The independent reference selector included mutable site chrome, so '
            'browser-rendered main content must be retained and bound separately.')
REGRESSIONS=['reference-scope-retains-main-content',
             'reference-scope-excludes-site-chrome']

def create_promotion_inputs(root):
    captures=root/'captures';captures.mkdir()
    (captures/'audit.json').write_text('[]')
    checked='2026-09-11T00:00:00+00:00'
    old='Old rendered reference body'
    new='New independently rendered reference body'
    case={'id':'javascript-spa','name':'Rendered fixture',
          'url':'https://quotes.toscrape.com/js/','track':'web',
          'task_kind':'body','task_prompt':'Read the rendered body',
          'ground_truth_main_content':old,
          'body_requirements':body_requirements(old),'required_facts':[],
          'source_verification':{'state':'valid','checked_at':checked,
              'method':'Independent visible browser DOM; complete declared scope',
              'selector':'body','reference_sha256':
              hashlib.sha256(old.encode()).hexdigest()}}
    dataset={'cases':[case],'coverage_plan':{'web':['p']}}
    dataset_path=root/'dataset.json'
    dataset_path.write_text(json.dumps(dataset))
    observation={'case_id':'javascript-spa','independent':True,
        'checked_at':checked,'content_text':new,'selector':'body',
        'observation_method':'visible_inner_text','observed_records':10,
        'scope_identity':scope_identity(case),
        'observer':{'browser':'Chromium','binary':'explicit'},
        'completion':{'satisfied':True,'elapsed_ms':10,'timeout_ms':1000}}
    observations_path=root/'observations.json'
    observations_path.write_text(json.dumps({'javascript-spa':observation}))
    return dict(dataset_path=dataset_path, captures=captures, observations_path=observations_path,
                observation=observation, new_reference=new, checked_at=checked)
