# -*- coding: utf-8 -*-
from pathlib import Path
from html import escape
import json,re
R=Path(__file__).parent;root=R.parent;p=__import__('current_film').current_film();seconds=round(p['measured_duration_seconds']);stamp=f'{seconds//60} 分 {seconds%60:02} 秒'
f=root/'report.html';s=f.read_text()
legacy_css='.film-intro{margin-bottom:36px;padding-bottom:24px;border-bottom:1px solid #dbe6ec}.film-intro video{display:block;width:100%;aspect-ratio:16/9;background:#15343b;border-radius:8px}.film-intro h2{margin-top:0}@media print{.film-intro{display:none}}'
s=s.replace(legacy_css,'')
start=s.index('<article class="page"',s.index('<main id="report-main"'));close=s.index('>',start)+1
chapters=''.join(f'<div class="video-text-part"><h3>{i:02d} · {escape(scene["title"])}</h3><p>{escape(scene["narration"])}</p><p class="note"><a href="{scene["evidence"]}">查看正文依据</a></p></div>' for i,scene in enumerate(p['scenes'],1))
entry=f'''<section id="film-intro" class="film-intro"><h2>先用一段视频看清产品体系</h2><p>从底层访问到数据与浏览器执行，看清 Bright Data 如何复用能力、逐层形成产品，以及这条路线对 Lexmount 产品演进的启发。</p><video controls playsinline preload="metadata" poster="assets/video/poster.jpg"><source src="assets/video/brightdata.mp4" type="video/mp4"></video><p class="note">{stamp} · 中文配音与字幕 · <a href="watch.html">按章节观看、下载视频</a></p><details id="video-text" class="video-text"><summary>阅读视频文字版</summary><p class="reading-path"><strong>这份文字版用于快速建立整体认识。</strong>后面的六章报告给出客户对应、实测和建设依据；原版深度研究保留更完整的材料与推导。</p>{chapters}</details><p><a href="#opening-conclusion">继续阅读研究结论</a> · <a href="original-research.html">阅读原版深度研究</a></p></section>'''
s=re.sub(r'<section id="film-intro".*?</section>','',s,flags=re.S)
start=s.index('<article class="page"',s.index('<main id="report-main"'));close=s.index('>',start)+1;s=s[:close]+entry+s[close:]
f.write_text(s)
print('Updated video entry only; report chapters retained')
