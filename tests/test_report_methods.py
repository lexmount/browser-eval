import unittest
from browser_harness_eval.report_methods import adoption_counts
from browser_harness_eval.report_components import metric_legend,metric_guide

class AdoptionTests(unittest.TestCase):
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
