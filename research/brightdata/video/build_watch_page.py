from pathlib import Path
from html import escape
ROOT=Path(__file__).parent;report=ROOT.parent
from current_film import current_film
p=current_film();aligned={s['id']:s['start'] for s in p['scenes']};offset=0;sections=[];chapters=[]
for i,s in enumerate(p['scenes'],1):
 offset=aligned[s['id']];sec=int(offset);stamp=f'{sec//60:02d}:{sec%60:02d}'
 chapters.append(f'<li><button type="button" data-seek="{offset:.3f}"><time>{stamp}</time><span>{escape(s["title"])}</span></button></li>')
 sections.append(f'<section id="{s["id"]}"><h3>{i:02d} · {escape(s["title"])}</h3><p>{escape(s["narration"])}</p><a href="{s["evidence"]}">查看报告依据</a></section>')
 offset+=s['duration']
beat_count=sum(len(scene['visual_beats']) for scene in p['scenes'])
page='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bright Data · 视频讲解</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f5f3ec;color:#15343b;font:17px/1.85 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1440px;margin:32px auto;padding:0 28px}h1{font-size:clamp(26px,3vw,40px);line-height:1.4;margin-bottom:16px}h2{font-size:26px}h3{font-size:21px}a{color:#087f79}p{max-width:950px}.intro{color:#587080}.layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:24px;align-items:start}.player{background:#15343b;border-radius:10px;overflow:hidden}video{display:block;width:100%;aspect-ratio:16/9}aside{background:#fff;padding:14px;border:1px solid #d4dfd9;border-radius:10px;max-height:600px;overflow:auto}aside h2{font-size:19px;margin:0 8px 12px}ol{list-style:none;padding:0;margin:0}button{display:flex;gap:12px;width:100%;text-align:left;border:0;background:transparent;padding:9px 8px;font:inherit;font-size:15px;line-height:1.5;color:inherit;cursor:pointer;border-radius:5px}button:hover,button:focus-visible{background:#e1f0e8}button[aria-current="true"]{background:#e1f0e8;color:#087f79;font-weight:600}time{font-variant-numeric:tabular-nums;color:#587080}.links{margin:14px 0 28px}.transcript{max-width:1000px;background:#fff;padding:24px;margin-top:30px;border-radius:10px}.transcript section{border-top:1px solid #d4dfd9;padding:14px 0}.transcript a{font-size:14px}.note{font-size:14px;color:#587080}@media(max-width:900px){.layout{grid-template-columns:1fr}aside{max-height:250px}}@media(max-width:600px){main{padding:0 16px;margin:18px auto}.transcript{padding:18px}body{font-size:16px}}
</style></head><body><main><a href="report.html">← 阅读研究报告</a><h1>Bright Data 的产品体系与商业逻辑</h1><p class="intro">从代理网络出发，看清 Bright Data 如何复用访问能力、逐层形成数据产品，并把这条产品路线放回 Lexmount 的现有能力与下一步。</p><div class="layout"><div><div class="player"><video id="film" controls playsinline preload="metadata" poster="assets/video/poster.jpg"><source src="assets/video/brightdata.mp4" type="video/mp4"><p>浏览器不支持视频播放。<a href="assets/video/brightdata.mp4">下载视频</a></p></video></div><p class="note">DURATION · 中文配音与字幕 · CHAPTER_COUNT 个章节 · 1080p · BEAT_COUNT 幅讲解画面。</p><p class="links"><a href="assets/video/brightdata.mp4" download>下载视频</a> · <a href="assets/video/subtitles.vtt" download>下载字幕</a> · <a href="assets/video/narration.txt" download>下载讲稿</a></p></div><aside aria-label="视频章节"><h2>按章节观看</h2><ol>CHAPTERS</ol></aside></div><div class="transcript"><h2>讲稿与依据</h2><p class="note">研究资料核对：2026-10-13；代表场景实测：2026-10-09。年化收入与 AI 使用规模采用公司公开披露口径，具体出处见对应报告。</p>SECTIONS</div></main><script>
const video=document.getElementById('film');const buttons=[...document.querySelectorAll('[data-seek]')];buttons.forEach(button=>button.addEventListener('click',()=>{video.currentTime=Number(button.dataset.seek);video.play().catch(()=>{});}));video.addEventListener('timeupdate',()=>{let current=buttons[0];for(const button of buttons){if(Number(button.dataset.seek)<=video.currentTime)current=button;}for(const button of buttons)button.setAttribute('aria-current',String(button===current));});
</script></body></html>'''.replace('CHAPTERS',''.join(chapters)).replace('SECTIONS',''.join(sections))
seconds=round(p['measured_duration_seconds']);page=page.replace('DURATION',f'{seconds//60} 分 {seconds%60:02} 秒')
page=page.replace('CHAPTER_COUNT',str(len(p['scenes'])))
page=page.replace('BEAT_COUNT',str(beat_count))
(report/'watch.html').write_text(page)
asset=report/'assets/video';asset.mkdir(exist_ok=True);(asset/'narration.txt').write_text('\n\n'.join(s['title']+'\n'+s['narration'] for s in p['scenes']))
(report/'assets/source/video-script.txt').write_text((asset/'narration.txt').read_text())
print('Watch page created:',len(sections),'chapters')

# The downloadable subtitles share the exact same clock as the burned-in captions.
def stamp(seconds):
 total=round(seconds*1000);h,total=divmod(total,3600000);m,total=divmod(total,60000);sec,ms=divmod(total,1000)
 return f"{h:02}:{m:02}:{sec:02}.{ms:03}"
vtt="WEBVTT\n\n"+"\n\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(p['timeline']['captions'],1))+"\n"
(asset/'subtitles.vtt').write_text(vtt)
