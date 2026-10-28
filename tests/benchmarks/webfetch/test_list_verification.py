import unittest
from lexbench_webfetch.list_verification import verify_list, identity, name_matches

class ListVerificationTests(unittest.TestCase):
 def setUp(self):
  self.contract={'hosts':['example.com'],'entry_path':r'/item/\d+','minimum_entries':3}
  self.ref={'case_id':'test','items':[{'title':f'Novel {i}','url':f'https://example.com/item/{i}'} for i in range(5)]}
 def run_check(self,items):return verify_list('\n'.join(f'[{name}](https://example.com/item/{i})' for i,name in items),self.contract,'https://example.com',self.ref)
 def test_three_correct_identities_pass(self):
  self.assertTrue(self.run_check([(4,'Novel 4'),(3,'Novel 3'),(0,'Novel 0')])['list_task_pass'])
 def test_correct_names_wrong_links_not_pass(self):
  r=self.run_check([(0,'Novel 3'),(1,'Novel 4'),(2,'Novel 0')]);self.assertFalse(r['list_task_pass']);self.assertEqual(r['confirmed_entries'],0)
 def test_no_cherry_picking_after_first_three(self):
  self.assertFalse(self.run_check([(4,'Unknown'),(0,'Novel 0'),(1,'Novel 1'),(2,'Novel 2')])['list_task_pass'])
 def test_unknown_not_invented_wrong(self):
  r=self.run_check([(9,'New item')]);self.assertEqual(r['list_checks'][0]['verdict'],'unconfirmed');self.assertFalse(r['list_task_pass'])
 def test_access_feedback_is_not_content_truth(self):
  self.ref['access_checks']=[{'url':'https://example.com/item/9','restriction':True,'restriction_message':'Open the app','checked_at':'2026-09-02'}]
  r=self.run_check([(9,'New item')]);x=r['list_checks'][0]
  self.assertEqual(x['verdict'],'unconfirmed');self.assertTrue(x['access_restricted']);self.assertEqual(x['access_note'],'Open the app');self.assertFalse(r['list_task_pass'])
 def test_formatting_not_fuzzy_matching(self):
  self.assertTrue(name_matches('1. The Book — 1999 5 stars','The Book'))
  self.assertFalse(name_matches('The Books','The Book'));self.assertFalse(name_matches('Startup','Up'))
  self.assertFalse(name_matches('Not The Book','The Book'))
  self.assertTrue(name_matches('Free Reacher TV','Reacher'))
  self.assertTrue(name_matches('Top RatedHaunted House Candle','Haunted House Candle'))
 def test_identity(self):
  self.assertEqual(identity('https://www.amazon.com/name/dp/B000123456/ref=foo'),identity('https://amazon.com/dp/B000123456'))
  self.assertNotEqual(identity('https://example.com/item/1'),identity('https://example.com/item/2'))
 def test_signed_link_is_preserved_while_matching_identity(self):
  text='\n'.join(f'[Novel {i}](/item/{i}?access_token=required)' for i in range(3))
  r=verify_list(text,self.contract,'https://example.com',self.ref)
  self.assertTrue(r['list_task_pass'])
  self.assertTrue(all(x['url'].endswith('?access_token=required') for x in r['list_checks']))
