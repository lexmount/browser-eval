# Compare half-size luminance structure: ignore codec chroma subsampling, retain labels and focus geometry.
from pathlib import Path
import json,subprocess,re,concurrent.futures,hashlib
R=Path(__file__).parent;t=json.loads((R/'src/timeline.json').read_text());out=R.parent/'review/current/encoded';out.mkdir(exist_ok=True)
review=json.loads((R.parent/'review/current/dom-review.json').read_text())
assert review['timelineSha256']==hashlib.sha256((R/'src/timeline.json').read_bytes()).hexdigest(), 'Review timing must match the current timeline'
assert not review['failures'], 'Visual review must pass before encoded comparison'
records={row['eventIndex']:row for row in review['records']}
def compare(pair):
 i,e=pair;expected=R.parent/f'review/current/focus/{i:03}-{e["scene"]}-{e["target"]}.png'
 if i and t['events'][i-1]['scene']==e['scene'] and t['events'][i-1]['target']==e['target']:return None
 if not expected.exists():raise FileNotFoundError(expected)
 record=records[i]
 assert record['scene']==e['scene'] and record['target']==e['target'], 'Review sample belongs to a different event'
 stamp=record['time'];frame=out/f'{i:03}-{record["frame"]}.png'
 if not frame.exists() or frame.stat().st_mtime<(R/'output.mp4').stat().st_mtime:
  subprocess.run(['ffmpeg','-v','error','-y','-ss',str(stamp),'-i',str(R/'output.mp4'),'-frames:v','1',str(frame)],check=True)
 result=subprocess.run(['ffmpeg','-hide_banner','-i',str(frame),'-i',str(expected),'-filter_complex','[0:v]scale=960:540,format=gray[a];[1:v]scale=960:540,format=gray[b];[a][b]ssim','-f','null','-'],capture_output=True,text=True,check=True)
 value=float(re.search(r'All:([0-9.]+)',result.stderr)[1]);return {'time':stamp,'scene':e['scene'],'target':e['target'],'ssim':value}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=[x for x in pool.map(compare,enumerate(t['events'])) if x]
(R.parent/'review/current/encoded-alignment.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));bad=[x for x in results if x['ssim']<.99];print('Encoded alignment:',len(results),'samples; minimum SSIM:',min(x['ssim'] for x in results));print('Mismatches',bad);assert not bad
