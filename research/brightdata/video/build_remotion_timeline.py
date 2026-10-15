"""Build the film from reviewed clauses and new continuous-chapter speech."""
from pathlib import Path
import json,subprocess,math,shutil
import numpy as np
from timing import align_clauses
ROOT=Path(__file__).parent; OUT=ROOT/'remotion';FPS=30;SR=24000

def main():
    (OUT/'public').mkdir(parents=True,exist_ok=True)
    spec=json.loads((ROOT/'editorial.json').read_text())
    parts=[];cursor=0;scenes=[];captions=[];events=[]
    def silence(t):
        nonlocal cursor
        n=round(t*SR);parts.append(np.zeros(n,dtype=np.float32));cursor+=n
    for chapter in range(5):
        group=[s for s in spec['scenes'] if s['chapter']==chapter]
        meta=json.loads((ROOT/f'audio/current/chapter-{chapter}.json').read_text())
        clauses=[c for s in group for c in s['clauses']]
        aligned=align_clauses(clauses,meta['sentences'])
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(ROOT/f'audio/current/chapter-{chapter}.mp3'),'-ac','1','-ar',str(SR),'-f','f32le','-'])
        wave=np.frombuffer(raw,dtype=np.float32).copy()
        offset=0
        for s in group:
            n=len(s['clauses']); timing=aligned[offset:offset+n]
            # Keep complete utterances. Cuts only occur in silence between scene sentences.
            left=0 if offset==0 else (aligned[offset-1]['end']+timing[0]['start'])/2
            right=len(wave)/SR if offset+n==len(aligned) else (timing[-1]['end']+aligned[offset+n]['start'])/2
            lo=round(left*SR);hi=round(right*SR);clip=wave[lo:hi].copy()
            fade=min(72,len(clip)//2);clip[:fade]*=np.linspace(0,1,fade);clip[-fade:]*=np.linspace(1,0,fade)
            start=cursor/SR;silence(s['lead']);audio_start=cursor/SR
            parts.append(clip);cursor+=len(clip);speech_end=cursor/SR
            for clause,at in zip(s['clauses'],timing):
                begin=audio_start+at['start']-left;end=audio_start+at['end']-left
                captions.append({'start':begin,'end':end,'text':clause['text'],'scene':s['id']})
                events.append({'start':max(start,begin-.18),'end':end,'target':clause['target'],'scene':s['id'],'text':clause['text']})
                for a in at['anchors']:
                    events.append({'start':audio_start+a['time']-left-.12,'end':end,'target':a['target'],'scene':s['id'],'text':'spoken phrase'})
            # During thinking time, review known objects instead of leaving a frozen empty frame.
            silence(s['hold']);end=cursor/SR
            for i,target in enumerate(s['reviewTargets']):
                at=speech_end+s['hold']*i/len(s['reviewTargets'])
                events.append({'start':at,'end':end,'target':target,'scene':s['id'],'text':'visual recap'})
            scenes.append({k:v for k,v in s.items() if k not in ['clauses','reviewTargets']}|{'start':start,'end':end,'speechStart':audio_start,'speechEnd':speech_end,'from':round(start*FPS),'durationInFrames':round(end*FPS)-round(start*FPS)})
            offset+=n
    audio=np.concatenate(parts)
    # Single gain/limiter pass keeps chapter levels consistent without stretching the voice.
    subprocess.run(['ffmpeg','-v','error','-y','-f','f32le','-ar',str(SR),'-ac','1','-i','pipe:0','-af','loudnorm=I=-18:TP=-2:LRA=9','-ar',str(SR),'-c:a','pcm_s16le',str(OUT/'public/narration.wav')],input=audio.tobytes(),check=True)
    names=['公司背景与产品演进','共用能力与产品架构','产品提供什么结果','客户为什么持续付费','Lexmount 的基础与相邻机会']
    result={'fps':FPS,'durationInFrames':math.ceil(cursor/SR*FPS),'duration':cursor/SR,'speechRateMultiplier':1,'voice':spec['voice'],'scenes':scenes,'events':sorted(events,key=lambda e:e['start']),'captions':captions,'chapters':[{'title':names[c],'start':next(s['start'] for s in scenes if s['chapter']==c)} for c in range(5)]}
    (OUT/'src/timeline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    shutil.copy2(ROOT.parent/'assets/infographics/architecture.png',OUT/'public/architecture.png')
    print('seconds',round(result['duration'],2),'scenes',len(scenes),'captions',len(captions))
if __name__=='__main__':main()
