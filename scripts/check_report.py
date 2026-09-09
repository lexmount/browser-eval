"""Data and generated-output acceptance checks for the current report."""
import hashlib
import json
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.reliability import require_reliable

class Tables(HTMLParser):
 def __init__(self):super().__init__();self.in_th=False;self.first=False;self.headers=[]
 def handle_starttag(self,tag,attrs):
  if tag=='table':self.first=True
  if tag=='th' and self.first:self.in_th=True;self.first=False;self.headers.append('')
 def handle_data(self,text):
  if self.in_th:self.headers[-1]+=text
 def handle_endtag(self,tag):
  if tag=='th':self.in_th=False

class VisibleText(HTMLParser):
 def __init__(self):super().__init__();self.skip=0;self.parts=[]
 def handle_starttag(self,tag,attrs):
  if tag in {'pre','script','style'}:self.skip+=1
 def handle_endtag(self,tag):
  if tag in {'pre','script','style'}:self.skip=max(0,self.skip-1)
 def handle_data(self,text):
  if not self.skip:self.parts.append(text)

root=Path(sys.argv[1]);d=json.loads((root/'dataset.json').read_text());rs=[json.loads(l) for l in (root/'results.jsonl').read_text().splitlines()]
names={r['provider'] for r in rs}
inventory=json.loads((Path(__file__).resolve().parents[1]/'configs/evaluation-inventory.json').read_text())
if d.get('comparison',{}).get('kind') in {'headless_html','headless_markdown'}:
 headless=json.loads((Path(__file__).resolve().parents[1]/'configs/providers.headless.json').read_text())
 inventory={track:{**group,'providers':list(headless['providers'])} for track,group in inventory.items()}
assert len({c['id'] for c in d['cases']})==len(d['cases']),'Duplicate dataset case IDs'
inventory_ids=[cid for group in inventory.values() for cid in group['case_ids']]
assert len(set(inventory_ids))==len(inventory_ids),'Duplicate inventory case IDs'
assert {(c['id'],c.get('track','web')) for c in d['cases']}=={(cid,t) for t,v in inventory.items() for cid in v['case_ids']},'Dataset differs from reviewed independent inventory'
plan=d.get('coverage_plan',{})
assert {t:set(p) for t,p in plan.items()}=={t:set(v['providers']) for t,v in inventory.items()},'Provider coverage differs from reviewed inventory'
expected_pairs={(p,c['id']) for c in d['cases'] for p in plan.get(c.get('track','web'),names)}
expected=len(expected_pairs)
assert len(rs)==expected
assert {(r['provider'],r['case_id']) for r in rs}==expected_pairs
assert len({r['attempt_id'] for r in rs})==expected
assert len({(r['provider'],r['case_id']) for r in rs})==expected
case_by_id={c['id']:c for c in d['cases']}
reliability=require_reliable(d,rs)
saved_reliability=json.loads((root/'reliability.json').read_text())
assert saved_reliability==reliability,'Saved reliability status differs from report evidence'
for r in rs:
 p=root/r['artifact_path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==r['metrics']['content_sha256'],p
 if not r['ok']:assert not r['metrics']['content_pass']
 if r['metrics'].get('word_f1') is not None:assert case_by_id[r['case_id']].get('ground_truth_main_content')
for c in d['cases']:
 if c['track'] in {'web','dynamic','document'} and not c.get('ground_truth_main_content'):
  unavailable=c.get('source_verification',{}).get('state')=='unavailable'
  stable_checks=bool(c.get('required_facts') or c.get('required_patterns') or c.get('acceptance_contract'))
  assert unavailable and stable_checks,c['id']
 if c.get('ground_truth_main_content'):
  expected=c['ground_truth_main_content'].encode()
  assert hashlib.sha256(expected).hexdigest()==c['source_verification']['reference_sha256'],c['id']
  assert (root/'references'/f"{c['id']}.txt").read_bytes()==expected,c['id']
 assert {r['provider'] for r in rs if r['case_id']==c['id']}==set(plan.get(c.get('track','web'),names)),c['id']
for p in root.rglob('*.html'):
 text=p.read_text();reader=VisibleText();reader.feed(text);visible=' '.join(reader.parts)
 for banned in ['人工复核','人工检查','短参考答案词级','组 · 查看','已确认有效','标准答案漂移候选队列','需辅助','需用户辅助','请自行登录','补采记录','本轮解析修正','内容已合并','build_v8_report','report_v8.py','任务修订','评分 8.2','已执行/总案例','已判分/已调用']:
  assert banned not in visible,(p,banned)
 assert not re.search(r'\bHIL\b',visible),(p,'HIL')
 parser=Tables();parser.feed(text)
 expected_index_header='No.' if p.relative_to(root).parts[0]=='en' else '序号'
 assert all(h==expected_index_header for h in parser.headers),(p,parser.headers)
 if p.parent.name=='attempts':
  record=next(r for r in rs if r['attempt_id']==p.stem)
  case=case_by_id[record['case_id']]
  heading=('目标文件' if case.get('task_kind')=='file' else
           '标准答案与验收规则' if case.get('task_kind')=='structure' else
           '完整标准答案')
  assert text.count('<h3>'+heading+'</h3>')==1,p
  assert text.count('<h3>实际返回</h3>')==1,p
  for delivered in record.get('provider_metadata',{}).get('file_evaluation',{}).get('files',[]):
   assert '../'+delivered['artifact_path'] in text and delivered['sha256'] in text,p
execution=json.loads((root/'execution.json').read_text())['batches']
assert Counter(i for b in execution for i in b['attempt_ids'])==Counter(r['attempt_id'] for r in rs)
home=(root/'index.html').read_text()
assert home.index('<h2>六类任务概览</h2>') < home.index('<h2>综合通过率总榜</h2>') < home.index('<h2>六类分榜</h2>'),'Task overview and overall ranking must precede detailed rankings'
assert '本类最高：' in home and '判分完整性' in home
methodology=(root/'methodology/index.html').read_text()
for phrase in ['计入耗时','不计入耗时','供应商上报的服务端耗时只作附加证据','形成可送入统一评分器的最终答案']:
 assert phrase in methodology,phrase
print(f'PASS: {len(d["cases"])} tasks / {len(rs)} records / {len(names)} providers; {len(list(root.rglob("*.html")))} logical report routes; reference integrity, all numbered tables, no workflow leftovers.')
