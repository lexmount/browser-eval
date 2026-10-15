"""Behavior scenarios: preserve material and reject broken audiovisual timelines."""
import importlib.util,json,copy,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('timeline_validator',ROOT.parents[1]/'.agents/skills/video-gen/scripts/validate_timeline.py')
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
class TimelineTests(unittest.TestCase):
 def example(self):
  return {'scenes':[{'id':'a','start':0,'speechStart':1,'speechEnd':4,'end':6}], 'captions':[{'scene':'a','start':1,'end':4,'text':'A complete thought.'}], 'events':[{'scene':'a','start':.8,'target':'object'}], 'targets':{'a':['object']}}
 def test_intentional_orientation_and_hold_are_valid(self):self.assertEqual(m.validate(self.example()),[])
 def test_renamed_object_cannot_leave_silent_animation(self):
  t=self.example();t['events'][0]['target']='removed';self.assertTrue(m.validate(t))
 def test_moving_scene_cannot_truncate_utterance(self):
  t=self.example();t['scenes'][0]['end']=3;self.assertTrue(m.validate(t))
 def test_renderer_migration_cannot_drop_retained_material(self):
  self.assertTrue(m.validate(self.example(),{'retained':[{'id':'original-map','scenes':['lost']}]}))
 def test_caption_must_belong_to_visible_scene(self):
  t=self.example();t['captions'][0]['scene']='wrong';self.assertTrue(m.validate(t))
 @unittest.skipUnless((ROOT/"video/remotion/src/timeline.json").is_file() and (ROOT/"video/content-contract.json").is_file(),"Requires the local publication inputs")
 def test_current_film_retains_required_material(self):
  t=json.loads((ROOT/'video/remotion/src/timeline.json').read_text());c=json.loads((ROOT/'video/content-contract.json').read_text())
  self.assertEqual(m.validate(t,c,ROOT),[])
  # A scene break follows a complete utterance, not the middle of the next thought.
  for s in t['scenes']:
   caps=[c for c in t['captions'] if c['scene']==s['id']]
   self.assertTrue(caps[-1]['text'].endswith(('。','！','？')),s['id'])
 @unittest.skipUnless((ROOT/"assets/diagrams/original-architecture.mmd").is_file(),"Requires the retained source diagram")
 def test_dependency_relations_survive_redrawing(self):
  import re,ast
  original=(ROOT/'assets/diagrams/original-architecture.mmd').read_text()
  names={'A':'network','B':'access','B2':'geo','C':'extract','D':'browser','E':'datasets','F':'firehose','G':'archive','H':'intelligence','I':'managed','J':'entries','K':'search','L':'agents','M':'business'}
  expected={(names[a],names[b]) for a,b in re.findall(r'(\w+)(?:\[".*?"\])?\s*-->\s*(\w+)',original)}
  source=(ROOT/'video/remotion/src/Film.tsx').read_text()
  actual={tuple(e) for e in ast.literal_eval(re.search(r'const edges\s*=\s*(\[.*?\]);',source,re.S)[1])}
  self.assertEqual(actual,expected)
if __name__=='__main__':unittest.main()
