"""Synthesize the reviewed film in continuous chapters, with word timestamps."""
from pathlib import Path
import json
from tts import synthesize
ROOT=Path(__file__).parent

def main():
    spec=json.loads((ROOT/'editorial.json').read_text())
    for chapter in range(5):
        text='\n\n'.join(''.join(c['text'] for c in s['clauses']) for s in spec['scenes'] if s['chapter']==chapter)
        out=ROOT/'audio/current'/f'chapter-{chapter}.mp3'
        meta=out.with_suffix('.json')
        signature={'text':text,'voice':spec['voice'],'speech_rate':spec['speechRate'],'context':spec['voiceDirection']}
        old=json.loads(meta.read_text()) if meta.exists() else {}
        if out.exists() and all(old.get(k)==v for k,v in signature.items()):continue
        synthesize(text,out,spec['voice'],spec['speechRate'],spec['voiceDirection'])
if __name__=='__main__':main()
