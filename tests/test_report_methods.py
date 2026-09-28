import unittest
from unittest.mock import patch
from harness.web_fetch.report_methods import adoption_counts
from harness.web_fetch.report_components import metric_legend,metric_guide
from harness.web_fetch.methods import METHOD_REGISTRY, method_info, method_page_model
from harness.web_fetch.report_i18n import translate_text

class AdoptionTests(unittest.TestCase):
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
