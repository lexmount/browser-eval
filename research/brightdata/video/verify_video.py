"""Check the current encoded film against its shared editorial time line."""
from pathlib import Path
import subprocess,json,re,hashlib
from current_film import current_film
ROOT=Path(__file__).parent;video=ROOT.parent/'assets/video/brightdata.mp4'
p=current_film();timeline=p['timeline']
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]))
v=next(s for s in probe['streams'] if s['codec_type']=='video');a=next(s for s in probe['streams'] if s['codec_type']=='audio')
assert (v['width'],v['height'])==(1920,1080);assert v['avg_frame_rate']=='30/1';assert a['codec_name']=='aac'
actual=float(probe['format']['duration']);assert abs(actual-timeline['duration'])<.1,(actual,timeline['duration'])
assert int(v['nb_frames'])==timeline['durationInFrames']
# Silence should be intentional thinking time, never a missing narration segment.
measure=subprocess.run(['ffmpeg','-hide_banner','-i',str(video),'-af','silencedetect=noise=-45dB:d=2.6','-vn','-f','null','-'],stderr=subprocess.PIPE,text=True,check=True).stderr
intervals=[(float(x),float(y)) for x,y in re.findall(r'silence_start: ([0-9.]+).*?silence_end: ([0-9.]+)',measure,re.S)]
expected=[(s['start'],s['speechStart']) for s in timeline['scenes']]+[(s['speechEnd'],s['end']) for s in timeline['scenes']]
# Merge adjacent end-hold and next-scene orientation windows.
merged=[]
for start,end in sorted(expected):
 if merged and start<=merged[-1][1]+.05:merged[-1][1]=max(end,merged[-1][1])
 else:merged.append([start,end])
unexpected=[(start,end) for start,end in intervals if not any(start>=lo-1.0 and end<=hi+1.0 for lo,hi in merged)]
assert not unexpected,unexpected
black=subprocess.run(['ffmpeg','-hide_banner','-i',str(video),'-vf','blackdetect=d=.15:pix_th=.1','-an','-f','null','-'],stderr=subprocess.PIPE,text=True,check=True).stderr
assert 'black_start:' not in black
watch=(ROOT.parent/'watch.html').read_text();assert watch.count('data-seek=')==len(p['scenes'])
for chapter in p['scenes']:assert chapter['narration'] in watch
result={'duration_seconds':actual,'width':v['width'],'height':v['height'],'fps':v['avg_frame_rate'],'audio_codec':a['codec_name'],'chapters':len(p['scenes']),'visual_scenes':len(timeline['scenes']),'subtitle_cues':len(timeline['captions']),'planned_long_pauses':intervals,'unexpected_long_pauses':unexpected,'black_frames':False,'sha256':hashlib.sha256(video.read_bytes()).hexdigest()}
(ROOT/'review/current/technical-qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
