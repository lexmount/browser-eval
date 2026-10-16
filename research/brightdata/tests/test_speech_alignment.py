"""Audience scenarios: changing voices must not detach emphasis from speech."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'video'))
from timing import align_recognized_clauses, silence_cut
class SpeechTimingTests(unittest.TestCase):
 def test_leading_decimal_point_preserves_quantity(self):
  for left,right in [('.5','0.5'),('．５','零点五'),('-.5','-0.5')]:
   for authored,spoken in [(left,right),(right,left)]:
    with self.subTest(authored=authored,spoken=spoken):
     align_recognized_clauses([{'text':authored+'秒'}],self.recognized(spoken+'秒'))
 def test_leading_decimal_point_cannot_disappear(self):
  for left,right in [('.5','5'),('-.5','.5'),('-.5','5')]:
   for authored,spoken in [(left,right),(right,left)]:
    with self.subTest(authored=authored,spoken=spoken),self.assertRaises(ValueError):
     align_recognized_clauses([{'text':authored+'秒'}],self.recognized(spoken+'秒'))
 def test_grouped_and_mixed_unit_numbers_remain_equivalent(self):
  for left,right in [('1000','1,000'),('一千','1千'),('两千五百','2千5百'),('一百二十','1百2十'),('1500','1.5千'),('一百万','1,000,000'),('1000.5','1,000.50')]:
   for authored,spoken in [(left,right),(right,left)]:
    with self.subTest(authored=authored,spoken=spoken):
     align_recognized_clauses([{'text':authored+'条记录'}],self.recognized(spoken+'条记录'))
 def test_invalid_numeric_grouping_cannot_change_quantity(self):
  for left,right in [('1,5','15'),('10,00','1000'),('1234,000','1234000'),('1,000,00','100000'),('2千5百','2050')]:
   for authored,spoken in [(left,right),(right,left)]:
    with self.subTest(authored=authored,spoken=spoken),self.assertRaises(ValueError):
     align_recognized_clauses([{'text':authored}],self.recognized(spoken))
 def test_numeric_operators_and_separators_cannot_disappear(self):
  for left,right in [('-1.5','1.5'),('- 1.5','1.5'),('1%','1'),('1 %','1'),('1/5','15'),('1:5','15'),('1。5','15'),('１．５','１５'),('1/5','1+5')]:
   for authored,spoken in [(left,right),(right,left)]:
    with self.subTest(authored=authored,spoken=spoken),self.assertRaises(ValueError):
     align_recognized_clauses([{'text':authored}],self.recognized(spoken))
 def test_fullwidth_number_is_equivalent_to_ascii(self):
  align_recognized_clauses([{'text':'１．５'}],self.recognized('1.5'))
 def test_decimal_point_changes_quantity_in_both_directions(self):
  for left,right in [('1.5','15'),('15','1.5'),('一点五','十五'),('十五','一点五')]:
   with self.subTest(left=left,right=right),self.assertRaises(ValueError):
    align_recognized_clauses([{'text':'每天处理'+left+'亿次请求'}],self.recognized('每天处理'+right+'亿次请求'))
 def test_equivalent_number_notation_keeps_value_and_timing(self):
  for left,right in [('1.5','一点五'),('一点五','1.50'),('七十二','72'),('72','七十二'),('十万','100000'),('三亿','3 亿'),('1.5亿','一点五亿')]:
   with self.subTest(left=left,right=right):
    text='处理'+right+'条记录'
    rows=align_recognized_clauses([{'text':'处理'+left+'条记录'}],self.recognized(text))
    self.assertAlmostEqual(rows[0]['end'],len(text)*.2)
 def test_english_omissions_and_insertions_cannot_hide_in_long_clause(self):
  prefix='这些客户需要持续访问不同网站并检查每次任务的运行结果然后使用'
  for left,right in [('Browser API','Browser'),('Browser','Browser API'),('Web Scraper API','Web API'),('Web API','Web Scraper API')]:
   with self.subTest(left=left,right=right),self.assertRaises(ValueError):
    align_recognized_clauses([{'text':prefix+left+'完成后续工作','anchors':[{'phrase':left,'target':'product'}]}],self.recognized(prefix+right+'完成后续工作'))
 def test_gap_after_next_syllable_is_not_a_safe_scene_cut(self):
  wave=[.2]*1000+[0.0]*100+[.2]*40+[0.0]*200+[.2]*500
  with self.assertRaises(ValueError):silence_cut(wave,1000,1,1.1)
 def test_short_next_syllable_cannot_be_reclassified_as_previous_tail(self):
  wave=[.2]*1000+[0.0]*100+[.2]*40+[0.0]*200+[.2]*660
  recognition={'segments':[{'words':[{'word':'访问','start':0,'end':1},{'word':'网站','start':1.1,'end':2}]}]}
  rows=align_recognized_clauses([{'text':'访问'},{'text':'网站'}],recognition,wave,1000)
  self.assertEqual(rows[1]['cutStart'],1.1)
  with self.assertRaises(ValueError):silence_cut(wave,1000,rows[0]['end'],rows[1]['cutStart'])
 def test_one_missing_or_added_word_can_reverse_meaning_or_change_quantity(self):
  for intended,heard in [('这些公开案例不能证明客户采用这种产品','这些公开案例能证明客户采用这种产品'),('这些公开案例能证明客户采用这种产品','这些公开案例不能证明客户采用这种产品'),('覆盖十六个国家和地区','覆盖六个国家和地区'),('覆盖六个国家和地区','覆盖十六个国家和地区')]:
   with self.subTest(intended=intended,heard=heard),self.assertRaises(ValueError):
    align_recognized_clauses([{'text':intended}],self.recognized(heard))
 def recognized(self,text,start=0,step=.2):
  return {'segments':[{'words':[{'word':c,'start':start+i*step,'end':start+(i+1)*step} for i,c in enumerate(text)]}]}
 def test_caption_and_focus_follow_heard_name_not_delayed_provider_label(self):
  text='Browser API提供浏览器会话'
  clauses=[{'text':text,'anchors':[{'phrase':'Browser API','target':'browser'}]}]
  heard=align_recognized_clauses(clauses,self.recognized(text,start=2))[0]
  delayed_start=3.2
  self.assertEqual(heard['start'],2)
  self.assertEqual(heard['anchors'][0]['time'],2)
  self.assertGreater(delayed_start-heard['start'],1)
 def test_traditional_and_homophone_recognition_keep_authored_field_anchor(self):
  clauses=[{'text':'网页提取字段正确性','anchors':[{'phrase':'字段正确性','target':'fields'}]}]
  result=align_recognized_clauses(clauses,self.recognized('網頁提取自斷正確性'))
  self.assertEqual(result[0]['anchors'][0]['time'],.8)
  self.assertEqual(clauses[0]['text'],'网页提取字段正确性')
 def test_independent_alignment_rejects_an_omitted_sentence(self):
  clauses=[{'text':'浏览器执行网页操作'},{'text':'字段提取业务数据'},{'text':'持续更新历史记录'}]
  with self.assertRaises(ValueError):
   align_recognized_clauses(clauses,self.recognized('浏览器执行网页操作持续更新历史记录'))
 def test_numeric_transcription_does_not_look_like_missing_speech(self):
  result=align_recognized_clauses([{'text':'覆盖七十二个国家'}],self.recognized('覆盖72个国家'))
  self.assertEqual(result[0]['start'],0)
  self.assertAlmostEqual(result[0]['end'],1.4)
 def test_missing_clause_tail_is_rejected_even_with_high_total_coverage(self):
  text='网页提取服务会保留标题链接与来源以方便客户继续检查'
  with self.assertRaises(ValueError):
   align_recognized_clauses([{'text':text}],self.recognized(text[:-1]))
 def test_missing_inner_phrase_is_not_hidden_by_long_sentence(self):
  text='网页提取服务会保留完整的商品价格以及名称链接和来源供客户逐条检查'
  with self.assertRaises(ValueError):
   align_recognized_clauses([{'text':text}],self.recognized(text.replace('商品价格','')))
 def test_independent_alignment_rejects_reordered_speech(self):
  clauses=[{'text':'浏览器执行网页操作'},{'text':'字段提取业务数据'}]
  with self.assertRaises(ValueError):
   align_recognized_clauses(clauses,self.recognized('字段提取业务数据浏览器执行网页操作'))
 def test_unclear_anchor_cannot_be_invented_from_surrounding_words(self):
  clauses=[{'text':'我们重点检查每条记录的价格和时间以及库存字段是否完整','anchors':[{'phrase':'价格','target':'price'}]}]
  with self.assertRaises(ValueError):
   align_recognized_clauses(clauses,self.recognized('我们重点检查每条记录的名称和时间以及库存字段是否完整'))
 def test_asr_leading_silence_is_not_the_visible_speech_onset(self):
  result=align_recognized_clauses([{'text':'网页'}],self.recognized('网页',step=.5),[0.0]*200+[.1]*800,sample_rate=1000)
  self.assertAlmostEqual(result[0]['start'],.2)
 def test_boundary_follows_actual_spoken_phrase_not_provider_label(self):
  recognition={'segments':[{'words':[
   {'word':'返回JSON','start':0,'end':1},
   {'word':'页面响应','start':1.4,'end':2.5},
   {'word':'浏览器会话','start':2.5,'end':4}]}]}
  rows=align_recognized_clauses([{'text':'返回 JSON。'},{'text':'页面响应、浏览器会话。'}],recognition)
  self.assertEqual((rows[0]['end'],rows[1]['start']),(1,1.4))
 def test_unrecognized_boundary_cannot_pass_as_reviewed(self):
  with self.assertRaises(ValueError):align_recognized_clauses([{'text':'完整的一句话'},{'text':'接下来的内容'}],{'segments':[]})
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
 def test_new_voice_duration_moves_the_focus_anchor(self):
  clauses=[{'text':'先看访问，再看提取。','anchors':[{'phrase':'提取','target':'extract'}]}]
  quick=align_recognized_clauses(clauses,self.recognized('先看访问再看提取',step=.2))[0]
  slow=align_recognized_clauses(clauses,self.recognized('先看访问再看提取',step=.5))[0]
  self.assertGreater(slow['anchors'][0]['time'],quick['anchors'][0]['time'])
  self.assertEqual(slow['anchors'][0]['time'],3)
 def test_missing_sentence_cannot_be_silently_displayed(self):
  with self.assertRaises(ValueError):align_recognized_clauses([{'text':'执行提供网页。'},{'text':'评测反馈问题。'}],self.recognized('执行提供网页'))
 def test_punctuation_and_english_word_grouping_do_not_break_alignment(self):
  words=[{'word':'Browser','start':0,'end':.5},{'word':'API，','start':.5,'end':1},{'word':'执行。','start':1.2,'end':2}]
  result=align_recognized_clauses([{'text':'Browser API，'},{'text':'执行。'}],{'segments':[{'words':words}]})
  self.assertEqual(result[1]['start'],1.2)
 def test_missing_focus_phrase_is_an_error(self):
  with self.assertRaises(ValueError):align_recognized_clauses([{'text':'执行','anchors':[{'phrase':'不存在','target':'wrong'}]}],self.recognized('执行'))
 def test_reversed_speech_time_is_rejected(self):
  with self.assertRaises(ValueError):align_recognized_clauses([{'text':'访问'},{'text':'提取'}],{'segments':[{'words':[{'word':'访问','start':3,'end':4},{'word':'提取','start':1,'end':2}]}]})
if __name__=='__main__':unittest.main()
