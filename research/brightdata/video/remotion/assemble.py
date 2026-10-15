"""Restore held intervals after rendering each unique visual frame with Remotion."""
from pathlib import Path
import json,subprocess,concurrent.futures
ROOT=Path(__file__).parent
r=json.loads((ROOT/'src/reel.json').read_text());work=ROOT/'render-parts';work.mkdir(exist_ok=True)
def segment(item):
 i,s=item;n=s['end']-s['start'];source=s['reelLength'];out=work/f'{i:04}.mp4'
 args=['ffmpeg','-v','error','-y','-ss',f"{s['reelStart']/30:.9f}",'-i',str(ROOT/'reel.mp4'),'-an','-vf',f'trim=end_frame={source},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={n/30+1}', '-frames:v',str(n),'-r','30','-c:v','libx264','-preset','veryfast','-crf','20','-threads','2','-pix_fmt','yuv420p',str(out)]
 subprocess.run(args,check=True);return out
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 outputs=list(pool.map(segment,enumerate(r['segments'])))
listing=work/'concat.txt';listing.write_text(''.join("file '"+p.as_posix()+"'\n" for p in outputs))
subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-i',str(ROOT/'public/narration.wav'),'-map','0:v','-map','1:a','-vf','setpts=N/(30*TB)','-r','30','-c:v','libx264','-preset','veryfast','-crf','20','-threads','4','-c:a','aac','-b:a','160k','-movflags','+faststart',str(ROOT/'output.mp4')],check=True)
print('Assembled',len(outputs),'intervals to output.mp4')
