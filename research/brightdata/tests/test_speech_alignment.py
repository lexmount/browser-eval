"""Audience scenarios: changing voices must not detach emphasis from speech."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'video'))
from timing import align_clauses, silence_cut, recognized_boundaries
class SpeechTimingTests(unittest.TestCase):
 def test_boundary_follows_actual_spoken_phrase_not_provider_label(self):
  recognition={'segments':[{'words':[
   {'word':'返回JSON','start':0,'end':1},
   {'word':'页面响应','start':1.4,'end':2.5},
   {'word':'浏览器会话','start':2.5,'end':4}]}]}
  self.assertEqual(recognized_boundaries(['返回 JSON。','页面响应、浏览器会话。'],recognition),[(1,1.4)])
 def test_unrecognized_boundary_cannot_pass_as_reviewed(self):
  with self.assertRaises(ValueError):recognized_boundaries(['完整的一句话','接下来的内容'],{'segments':[]})
 def test_delayed_word_timestamp_does_not_cut_audible_name(self):
  # Speech ends at 1s, resumes at 1.3s, but the provider timestamps the
  # following English name at 2s. The former midpoint falls inside speech.
  wave=[.2]*1000+[0.0]*300+[.2]*1000
  cut=silence_cut(wave,1000,1.0,2.0)
  self.assertGreater(cut,1.05)
  self.assertLess(cut,1.25)
 def test_continuous_speech_cannot_be_split_for_a_visual_pause(self):
  with self.assertRaises(ValueError):silence_cut([.1]*3000,1000,1,2)
 def test_tiny_gap_between_syllables_is_not_a_scene_boundary(self):
  wave=[.2]*1000+[0.0]*40+[.2]*1000
  with self.assertRaises(ValueError):silence_cut(wave,1000,1,1.5)
 def words(self,text,start=0,step=.3):
  return [{'words':[{'word':c,'startTime':start+i*step,'endTime':start+(i+1)*step} for i,c in enumerate(text)]}]
 def test_new_voice_duration_moves_the_focus_anchor(self):
  clauses=[{'text':'先看访问，再看提取。','anchors':[{'phrase':'提取','target':'extract'}]}]
  quick=align_clauses(clauses,self.words('先看访问再看提取',step=.2))[0]
  slow=align_clauses(clauses,self.words('先看访问再看提取',step=.5))[0]
  self.assertGreater(slow['anchors'][0]['time'],quick['anchors'][0]['time'])
  self.assertEqual(slow['anchors'][0]['time'],3)
 def test_missing_sentence_cannot_be_silently_displayed(self):
  with self.assertRaises(ValueError):align_clauses([{'text':'执行提供网页。'},{'text':'评测反馈问题。'}],self.words('执行提供网页'))
 def test_punctuation_and_english_word_grouping_do_not_break_alignment(self):
  words=[{'word':'Browser','startTime':0,'endTime':.5},{'word':'API，','startTime':.5,'endTime':1},{'word':'执行。','startTime':1.2,'endTime':2}]
  result=align_clauses([{'text':'Browser API，'},{'text':'执行。'}],[{'words':words}])
  self.assertEqual(result[1]['start'],1.2)
 def test_missing_focus_phrase_is_an_error(self):
  with self.assertRaises(ValueError):align_clauses([{'text':'执行','anchors':[{'phrase':'不存在','target':'wrong'}]}],self.words('执行'))
 def test_reversed_speech_time_is_rejected(self):
  with self.assertRaises(ValueError):align_clauses([{'text':'访问'},{'text':'提取'}],[{'words':[{'word':'访问','startTime':3,'endTime':4},{'word':'提取','startTime':1,'endTime':2}]}])
if __name__=='__main__':unittest.main()
