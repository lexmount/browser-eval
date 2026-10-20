import unittest
from unittest.mock import patch
from harness.web_fetch.report_methods import adoption_counts
from harness.web_fetch.report_components import metric_legend,metric_guide
from harness.web_fetch.methods import METHOD_REGISTRY, method_info, method_page_model
from harness.web_fetch.report_i18n import translate_text

class AdoptionTests(unittest.TestCase):
 def test_source_link_follows_method_type_and_registered_participant(self):
  import json,tempfile
  from pathlib import Path
  from harness.web_fetch.report_methods import render_methods
  from harness.web_fetch.report import hero,section,table,cell,link,esc
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)
   (out/'dataset.json').write_text(json.dumps({
       'comparison':{'kind':'uniform_native_local_measurement'}}))
   records=[{'provider':'renamed-moli','provider_type':'moli_webfetch',
             'provider_metadata':{}}]
   def render():
    return render_methods(out,records,hero=hero,section=section,
                          table=table,cell=cell,link=link,esc=esc)
   body=render()
   self.assertIn('href="https://github.com/lexmount/moli"',body)
   with patch('harness.web_fetch.methods.browser_method_registry',return_value={
       'renamed-moli':{'source_url':'https://example.org/build-source'}}):
    self.assertIn('href="https://example.org/build-source"',render())

 def test_missing_run_configuration_does_not_imply_default_or_unpaid_execution(self):
  for provider_type in ('custom_adapter', 'fixture', 'cowagent_builtin'):
   model=method_page_model('custom','Custom',[
       {'provider_type':provider_type,'provider_metadata':{}}])
   self.assertNotIn('默认正文',model['configuration'])
   self.assertNotIn('未启用',model['configuration'])

 def test_observed_routes_disclose_coverage_without_inventing_missing_routes(self):
  records=[{'provider_type':'headless_markdown','provider_metadata':{
      'extractor':'moli_native_markdown','output_contract':'markdown'}},
      {'provider_type':'headless_markdown','provider_metadata':{}}]
  model=method_page_model('custom','Custom',records)
  self.assertIn('1/2',model['configuration'])
  self.assertIn('Moli CLI',model['configuration'])
  self.assertNotIn('未启用',model['configuration'])
  empty=method_page_model('custom','Custom',records[1:])['configuration']
  self.assertIn('0/1',empty)
  self.assertIn('未记录',empty)
  self.assertNotIn(method_info('headless_markdown')['endpoint'],empty)
  for configuration in (model['configuration'],empty):
   translated=translate_text(configuration)
   self.assertNotRegex(translated,r'[\u4e00-\u9fff]')
   self.assertIn('records',translated)

 def test_method_page_model_resolves_moli_profile_before_rendering(self):
  record={'provider_type':'moli_webfetch','provider_metadata':{
      'reported_version':'moli 1.1.9','dump':'markdown',
      'route':'dynamic_native_markdown','wait_until':'done',
      'wait':{'strategy':'native_default_completion'}}}
  model=method_page_model('moli-webfetch','Moli',[record]*100)
  self.assertEqual(model['title'],'Moli · Moli 1.1.9')
  self.assertEqual(model['endpoint'],'moli fetch --dump markdown --wait-until done URL')
  self.assertIn('本轮 100 例',model['configuration'])
  self.assertIn('默认 done',model['configuration'])

 def test_method_page_model_reports_uniform_dynamic_family_completion(self):
  record={'provider_type':'moli_webfetch','provider_metadata':{
      'reported_version':'moli 1.1.15','dump':'markdown',
      'route':'dynamic_native_markdown','wait_until':'domstable',
      'layout':True,'with_frames':True,
      'wait':{'strategy':'native_task_family_completion'}}}
  model=method_page_model('moli','Moli',[record]*100)
  self.assertEqual(
      model['endpoint'],
      'moli fetch --dump markdown --layout --with-frames --wait-until domstable URL')
  self.assertIn('100 个动态网站正文任务',model['configuration'])
  self.assertIn('统一使用',model['configuration'])
  self.assertIn('没有逐题等待元素',model['configuration'])
  self.assertIn('--layout',model['configuration'])
  self.assertIn('--with-frames',model['configuration'])

 def test_future_method_page_is_registry_driven_without_renderer_changes(self):
  method={'endpoint':'future fetch URL','contract':'URL → text','output':'text',
          'freshness':'fresh process','leaderboard':'body',
          'report':{'configuration':'Configured from registry.',
                    'pricing_anchor':'pricing-future'}}
  with patch.dict(METHOD_REGISTRY,{'future_method':method}):
   model=method_page_model(
       'future-provider','Future Provider',
       [{'provider_type':'future_method','provider_metadata':{}}],
   )
  self.assertEqual(model,{
      'title':'Future Provider','contract':'URL → text','endpoint':'future fetch URL',
      'freshness':'fresh process','configuration':'Configured from registry.',
      'pricing_anchor':'pricing-future',
  })

 def test_tavily_method_reports_the_advanced_route_used_by_the_cohort(self):
  output=method_info('tavily_extract')['output']
  self.assertIn('advanced',output)
  self.assertNotIn('basic',output)

 def test_cloudflare_method_discloses_default_quick_actions_cache(self):
  for provider_type in ('cloudflare_markdown', 'cloudflare_content'):
   freshness=method_info(provider_type)['freshness']
   self.assertIn('默认最多缓存 5 秒',freshness)
   self.assertNotIn('实时',freshness)
   translated=translate_text(freshness)
   self.assertIn('up to 5 seconds',translated)
   self.assertNotIn('Real-time',translated)
 def test_failed_candidate_verification_preserves_snapshot(self):
  import json,sys,tempfile
  from pathlib import Path
  from unittest.mock import patch
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
  import refresh_adoption_candidates as survey
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'research').mkdir()
   dest=root/'research/provider-adoption.json'
   original={'provider_signatures':{},'included':[]}
   dest.write_text(json.dumps(original))
   (root/'research/webfetch-adopters.json').write_text(json.dumps({'included':[]}))
   before=dest.read_bytes()
   with patch.object(survey,'ROOT',root),patch.object(survey,'metadata_batch',side_effect=RuntimeError('network failure')):
    with self.assertRaises(RuntimeError):survey.refresh(discovered={'provider_signatures':{},'included':[]})
   self.assertEqual(dest.read_bytes(),before)
 def test_candidate_discovery_keeps_known_hits(self):
  import sys
  from pathlib import Path
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
  from refresh_adoption_candidates import candidate_union
  current={'provider_signatures':{'langchain-webbase':['langchain-web-base-loader']},'included':[]}
  old={'included':[{'repo':'example/project','matches':[{'signature':'langchain-web-base-loader','path':'loader.py'},{'signature':'generic-web-fetch-tool','path':'other.py'}]}]}
  self.assertEqual(dict(candidate_union(current,old)),{('example/project','loader.py'):{'langchain-web-base-loader'}})
  current['candidate_catalog']=[{'repo':'inactive/project','path':'old.py','signatures':['langchain-web-base-loader']}]
  self.assertIn(('inactive/project','old.py'),candidate_union(current,{'included':[]}))
 def test_counts_deduplicate_repository_and_file(self):
  row={'repo':'Owner/Repo','url':'https://example.org','matches':[{'signature':s,'path':p,'url':'https://example.org/code'} for s,p in [('firecrawl-v1','a.py'),('firecrawl-sdk','a.py'),('firecrawl-v2','b.py'),('generic-web-fetch-tool','c.py')]]}
  result=adoption_counts({'included':[row,row],'provider_signatures':{'firecrawl':['firecrawl-v1','firecrawl-sdk','firecrawl-v2'],'lexmount-global':['lexmount-global-extract'],'exa':['exa-contents']}},['firecrawl','lexmount-global','exa'])
  by={x['provider']:x for x in result}
  self.assertEqual(by['firecrawl']['repository_count'],1)
  self.assertEqual(by['firecrawl']['file_count'],2)
  self.assertEqual(by['exa']['repository_count'],0)
  self.assertEqual(by['lexmount-global']['repository_count'],0)
 def test_generic_hits_do_not_establish_adoption(self):
  row={'repo':'a/b','url':'https://example.org','matches':[{'signature':'generic-web-fetch-tool','path':'a.py'}]}
  self.assertEqual(adoption_counts({'included':[row],'provider_signatures':{'firecrawl':['firecrawl-sdk']}},['firecrawl'])[0]['repository_count'],0)
 def test_metric_definitions_cover_each_ranking(self):
  for title in ['请求成功率','任务通过率','结构达标率','资源命中率','文件取得率','信息通过率','完整度','纯净度','F1','均值','P50','P95']:
   self.assertIn('<dt>'+title+'</dt>',metric_legend([title]))
  self.assertNotIn('条件通过率',metric_guide())

def test_report_labels_distinguish_release_and_source_build_with_same_version(tmp_path):
    import json
    from harness.web_fetch.methods import with_measured_method_labels
    from harness.web_fetch.report import provider_label
    from harness.web_fetch.report_methods import display_name

    identities={'implementations':[
        {'provider':'moli-release','binary_sha256':'stable','version':'1.1.15'},
        {'provider':'moli-candidate','binary_sha256':'branch',
         'version':'1.1.17 · candidate build (99a22382)'}]}
    (tmp_path/'implementation-provenance.json').write_text(json.dumps(identities))
    records=[{'provider':x['provider'],'provider_metadata':{'binary_sha256':x['binary_sha256']}}
             for x in identities['implementations']]
    original=provider_label('moli-release')

    @with_measured_method_labels
    def render(out,dataset,rows):
        for label in (provider_label,display_name):
            assert label('moli-release')=='Moli 1.1.15'
            assert label('moli-candidate')=='Moli 1.1.17 · candidate build (99a22382)'

    render(tmp_path,{},records)
    assert provider_label('moli-release')==original
    records[0]['provider_metadata']['binary_sha256']='different-build'

    @with_measured_method_labels
    def unrelated(out,dataset,rows):
        assert provider_label('moli-release')==original

    unrelated(tmp_path,{},records)
    assert provider_label('moli-release')==original
