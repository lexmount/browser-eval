"""Data and generated-output acceptance checks for the current report."""
import hashlib
import json
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.reliability import require_reliable
from harness.web_fetch.reference_lifecycle import validate_resource_source_evidence
from scripts.report_asset_parity import ArticleAssetParity

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

class EnglishNarrative(HTMLParser):
 def __init__(self,allowed):super().__init__();self.skip=0;self.allowed=sorted(allowed,key=len,reverse=True);self.untranslated=[]
 def handle_starttag(self,tag,attrs):
  if tag in {'pre','code','script','style'}:self.skip+=1
 def handle_endtag(self,tag):
  if tag in {'pre','code','script','style'}:self.skip=max(0,self.skip-1)
 def handle_data(self,text):
  value=text.strip()
  remainder=value
  for allowed in self.allowed:remainder=remainder.replace(allowed,'')
  if not self.skip and re.search(r'[\u4e00-\u9fff]',remainder):
   self.untranslated.append(value)

class PageShape(HTMLParser):
 def __init__(self,page=None,assets=None):
  super().__init__();self.tags=Counter();self.ids=set();self.data=[];self.links=[];self.skip=0;self.numbers=[]
  self.page=page;self.assets=assets;self.figures=[];self.images=[]
 def handle_starttag(self,tag,attrs):
  if tag=='figure':self.figures.append(dict(attrs).get('id'))
  if self.assets and self.figures:attrs=self.assets.rewrite(self.page,self.figures[-1],tag,attrs)
  values=dict(attrs);self.tags[tag]+=1
  if values.get('id'):self.ids.add(values['id'])
  self.data.extend((key,value) for key,value in attrs if key.startswith('data-'))
  if values.get('href') and not values.get('hreflang'):self.links.append(values['href'])
  if tag=='img' and values.get('src'):self.images.append(values['src'])
  if tag in {'pre','code','script','style'}:self.skip+=1
 def handle_endtag(self,tag):
  if tag=='figure' and self.figures:self.figures.pop()
  if tag in {'pre','code','script','style'}:self.skip=max(0,self.skip-1)
 def handle_data(self,text):
  if not self.skip:self.numbers.extend(re.findall(r'\d+(?:[.,]\d+)*(?:%|\b)',text))

root=Path(sys.argv[1]).resolve();d=json.loads((root/'dataset.json').read_text());rs=[json.loads(l) for l in (root/'results.jsonl').read_text().splitlines()]
repository=Path(__file__).resolve().parents[1]
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
for case in d['cases']:
 validate_resource_source_evidence(case,repository/'evaluation/dataset.json')
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
zh_pages={p.relative_to(root) for p in root.rglob('*.html') if 'en' not in p.relative_to(root).parts}
en_pages={Path(*p.relative_to(root).parts[1:]) for p in (root/'en').rglob('*.html')}
assert zh_pages==en_pages,('Chinese/English route inventories differ',zh_pages^en_pages)
def canonical_link(page,value):
 parsed=urlsplit(value)
 if parsed.scheme or parsed.netloc:return value
 if not parsed.path:return '#'+parsed.fragment
 target=(page.parent/parsed.path).resolve().relative_to(root)
 if target.parts and target.parts[0]=='en' and target.suffix.lower() in {'.html','.htm'}:
  target=Path(*target.parts[1:])
 return target.as_posix()+(('?'+parsed.query) if parsed.query else '')+(('#'+parsed.fragment) if parsed.fragment else '')

bilingual_issues=[]
asset_manifest=Path(__file__).resolve().parents[1]/'research/editorial-figures.json'
article_assets=ArticleAssetParity(root,asset_manifest) if asset_manifest.exists() and (root/'assets/editorial').exists() else None
for relative in sorted(zh_pages):
 zh_path=root/relative;en_path=root/'en'/relative
 zh=PageShape(zh_path,article_assets);zh.feed(zh_path.read_text());en=PageShape(en_path,article_assets);en.feed(en_path.read_text())
 if zh.tags!=en.tags:bilingual_issues.append((relative,'semantic tag counts differ'))
 if zh.ids!=en.ids:bilingual_issues.append((relative,'element IDs differ'))
 if Counter(zh.data)!=Counter(en.data):bilingual_issues.append((relative,'data attributes differ'))
 zh_numbers=Counter(zh.numbers);en_numbers=Counter(en.numbers)
 if zh_numbers!=en_numbers:
  bilingual_issues.append((relative,'visible numeric values differ',zh_numbers-en_numbers,en_numbers-zh_numbers))
 zh_links=Counter(canonical_link(zh_path,value) for value in zh.links)
 en_links=Counter(canonical_link(en_path,value) for value in en.links)
 if zh_links!=en_links:bilingual_issues.append((relative,'evidence or navigation links differ'))
 if relative in {Path('guide/index.html'),Path('guide/article.html')}:
  if Counter(canonical_link(zh_path,value) for value in zh.images)!=Counter(canonical_link(en_path,value) for value in en.images):
   bilingual_issues.append((relative,'article image sources differ'))
assert not bilingual_issues,('Chinese/English page parity failed',bilingual_issues[:200],len(bilingual_issues))
english_primary=[Path(name) for name in ('index.html','methodology/index.html','review/index.html',
 'guide/index.html','adoption/index.html','reproduce/index.html','changes/index.html','accounts/index.html')]
allowed_english_source_text={'中文'}|{case['name'] for case in d['cases'] if case.get('name')}
for relative in english_primary:
 if relative not in en_pages:continue
 parser=EnglishNarrative(allowed_english_source_text);parser.feed((root/'en'/relative).read_text())
 assert not parser.untranslated,(relative,'untranslated English report narrative',parser.untranslated[:20])
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
  actual_heading=({'目标文件':'Target file','标准答案与验收规则':'Reference and acceptance contract',
                   '完整标准答案':'Complete reference answer'}[heading]
                  if p.relative_to(root).parts[0]=='en' else heading)
  assert text.count('<h3>'+actual_heading+'</h3>')==1,p
  assert text.count('<h3>'+('Actual output' if p.relative_to(root).parts[0]=='en' else '实际返回')+'</h3>')==1,p
  for delivered in record.get('provider_metadata',{}).get('file_evaluation',{}).get('files',[]):
   assert '../'+delivered['artifact_path'] in text and delivered['sha256'] in text,p
execution=json.loads((root/'execution.json').read_text())['batches']
assert Counter(i for b in execution for i in b['attempt_ids'])==Counter(r['attempt_id'] for r in rs)
home=(root/'index.html').read_text()
track_count=len({case['track'] for case in d['cases']})
assert home.index(f'<h2>{track_count} 类任务概览</h2>') < home.index('<h2>综合通过率总榜</h2>') < home.index(f'<h2>{track_count} 类分榜</h2>'),'Task overview and overall ranking must precede detailed rankings'
assert '本类最高：' in home and '判分完整性' in home
methodology=(root/'methodology/index.html').read_text()
for phrase in ['计入耗时','不计入耗时','供应商上报的服务端耗时只作附加证据','形成可送入统一评分器的最终答案']:
 assert phrase in methodology,phrase
print(f'PASS: {len(d["cases"])} tasks / {len(rs)} records / {len(names)} providers; {len(list(root.rglob("*.html")))} logical report routes; reference integrity, all numbered tables, no workflow leftovers.')
