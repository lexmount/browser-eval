import unittest
from harness.web_fetch.dynamic_lists import evaluate_dynamic_list,extract_named_entries

class DynamicListTests(unittest.TestCase):
 def setUp(self):self.contract={'hosts':['example.com'],'entry_path':r'/item/\d+','minimum_entries':3}
 def check(self,text,ok=True):return evaluate_dynamic_list(text,self.contract,'https://example.com',ok)
 def test_values_and_order_do_not_matter(self):
  for names in [('Alpha','Beta','Gamma'),('全新条目三','全新条目二','全新条目一')]:
   r=self.check('\n'.join(f'[{n}](/item/{i+51})' for i,n in enumerate(names)))
   self.assertTrue(r['structure_pass']);self.assertIsNone(r['factual_correctness']);self.assertIsNone(r['snapshot_f1'])
 def test_no_url_only_padding_or_duplicates(self):
  self.assertFalse(self.check('[Details](/item/1)\n[Details](/item/2)\n[Details](/item/3)')['structure_pass'])
  self.assertFalse(self.check('[A title](/item/1)\n[B title](/item/1?x=2)\n[C title](/item/1#x)')['structure_pass'])
 def test_html_and_plain_text(self):
  self.assertTrue(self.check(''.join(f'<a href="/item/{i}"><img alt="Title {i}"></a>' for i in range(3)))['structure_pass'])
  self.assertTrue(self.check('\n'.join(f'Title {i}\nhttps://example.com/item/{i}' for i in range(3)))['structure_pass'])
 def test_wrong_host_and_failed_request(self):
  text='\n'.join(f'[Title {i}](https://evil.example/item/{i})' for i in range(3))
  self.assertFalse(self.check(text)['structure_pass'])
  self.assertFalse(self.check(text.replace('evil.example','example.com'),False)['structure_pass'])
 def test_nested_image_names_and_numeric_ranks(self):
  text='\n'.join(f'[![New title {i}](https://img.test/{i}.png) TV](/item/{i})' for i in range(3))
  self.assertTrue(self.check(text)['structure_pass'])
  text='\n'.join(f'- {i}\nhttps://example.com/item/{i}' for i in range(10))
  self.assertEqual(self.check(text)['named_entry_count'],0)
 def test_prose_recommendations_are_not_list_cards(self):
  text='Watch '+', or '.join(f'[Title {i}](/item/{i})' for i in range(3))+'.'
  self.assertFalse(self.check(text)['structure_pass'])
 def test_consecutive_links_on_one_list_line_remain_distinct_records(self):
  text='- '+''.join(f'[Title {i}](/item/{i}) - ' for i in range(3))
  self.assertTrue(self.check(text)['structure_pass'])
 def test_link_dense_flattened_navigation_with_group_labels(self):
  contract={'hosts':['www.espn.com'],'entry_path':r'/[a-z0-9-]+/story/_/id/[0-9]+/.+'}
  text=('- NBA - [Buzz](https://www.espn.com/nba/story/_/id/1/buzz) - '
        '[Grades](https://www.espn.com/nba/story/_/id/2/grades) - East - '
        '[Trades](https://www.espn.com/nba/story/_/id/3/trades)')
  entries=extract_named_entries(text,contract,'https://www.espn.com/')
  self.assertEqual({entry['title'] for entry in entries},{'Buzz','Grades','Trades'})
 def test_three_links_embedded_in_prose_remain_excluded(self):
  text=('Read [Title 1](/item/1), compare it carefully with '
        '[Title 2](/item/2), and finally see [Title 3](/item/3) for context.')
  self.assertEqual(extract_named_entries(text,self.contract,'https://example.com'),[])
 def test_preserve_access_query_and_scheme(self):
  entries=extract_named_entries('[A title](/item/1?access_token=required&source=feed)',self.contract,'http://example.com')
  self.assertEqual(entries,[{'title':'A title','url':'http://example.com/item/1?access_token=required&source=feed'}])
