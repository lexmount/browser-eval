"""Data and generated-output acceptance checks for the current report."""
import hashlib
import json
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

class Tables(HTMLParser):
 def __init__(self):super().__init__();self.in_th=False;self.first=False;self.headers=[]
 def handle_starttag(self,tag,attrs):
  if tag=='table':self.first=True
  if tag=='th' and self.first:self.in_th=True;self.first=False;self.headers.append('')
 def handle_data(self,text):
  if self.in_th:self.headers[-1]+=text
 def handle_endtag(self,tag):
  if tag=='th':self.in_th=False

root=Path(sys.argv[1]);d=json.loads((root/'dataset.json').read_text());rs=[json.loads(l) for l in (root/'results.jsonl').read_text().splitlines()]
names={r['provider'] for r in rs}
expected=len(d['cases'])*len(names)
assert len(d['cases'])==140 and len(rs)==expected
assert Counter(c['track'] for c in d['cases'])==dict(web=100,document=10,image=10,video=10,challenge=10)
assert set(Counter(r['provider'] for r in rs).values())=={140}
assert len({r['attempt_id'] for r in rs})==expected
assert len({(r['provider'],r['case_id']) for r in rs})==expected
case_by_id={c['id']:c for c in d['cases']}
for r in rs:
 p=root/r['artifact_path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==r['metrics']['content_sha256'],p
 if not r['ok']:assert not r['metrics']['content_pass']
 if r['metrics'].get('word_f1') is not None:assert case_by_id[r['case_id']].get('ground_truth_main_content')
for c in d['cases']:
 if c['track'] in {'web','document'}:assert c.get('ground_truth_main_content'),c['id']
 if c.get('ground_truth_main_content'):
  expected=c['ground_truth_main_content'].encode()
  assert hashlib.sha256(expected).hexdigest()==c['source_verification']['reference_sha256'],c['id']
  assert (root/'references'/f"{c['id']}.txt").read_bytes()==expected,c['id']
 assert {r['provider'] for r in rs if r['case_id']==c['id']}==names,c['id']
for p in root.rglob('*.html'):
 text=p.read_text();visible=re.sub(r'<pre>.*?</pre>','',text,flags=re.S)
 for banned in ['人工复核','人工检查','HIL','短参考答案词级','组 · 查看','已确认有效','标准答案漂移候选队列','需辅助','需用户辅助','请自行登录','补采记录','本轮解析修正','内容已合并','build_v8_report','report_v8.py','任务修订','评分 8.2']:
  assert banned not in visible,(p,banned)
 parser=Tables();parser.feed(text);assert all(h=='序号' for h in parser.headers),(p,parser.headers)
 if p.parent.name=='attempts':
  assert text.count('<h3>完整参考 / 目标结果</h3>')==1,p
  assert text.count('<h3>本次完整输出</h3>')==1,p
execution=json.loads((root/'execution.json').read_text())['batches']
assert Counter(i for b in execution for i in b['attempt_ids'])==Counter(r['attempt_id'] for r in rs)
print(f'PASS: {len(d["cases"])} tasks / {len(rs)} records / {len(names)} providers; {len(list(root.rglob("*.html")))} HTML pages; reference integrity, all numbered tables, no workflow leftovers.')
