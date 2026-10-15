"""Current film metadata; original production remains the narration source."""
from pathlib import Path
import json
ROOT=Path(__file__).parent

def current_film():
    timeline=json.loads((ROOT/'remotion/src/timeline.json').read_text())
    source=json.loads((ROOT/'chapter-sources.json').read_text())
    chapters=[]
    for i,original in enumerate(source['scenes']):
        scenes=[s for s in timeline['scenes'] if s['chapter']==i]
        ids={s['id'] for s in scenes}
        captions=[c for c in timeline['captions'] if c['scene'] in ids]
        chapters.append({'id':original['id'],'title':original['title'],'evidence':original['evidence'],
          'start':scenes[0]['start'],'duration':scenes[-1]['end']-scenes[0]['start'],
          'narration':''.join(c['text'] for c in captions),'visual_beats':scenes})
    return {'scenes':chapters,'measured_duration_seconds':timeline['duration'],'timeline':timeline}
