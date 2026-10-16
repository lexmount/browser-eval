"""Check timing, target references and preserved material; not viewer comprehension.

Timeline: scenes(id,start,end,speechStart,speechEnd), captions(scene,start,end,
text), events(scene,start,target), optional targets mapping scene to allowed IDs.
Contract: retained entries(id,scenes, optional asset).
"""
import argparse,json
from pathlib import Path

def validate(timeline, contract=None, asset_root=None):
    errors=[];scenes=timeline.get('scenes',[]);ids=[s['id'] for s in scenes]
    if len(ids)!=len(set(ids)):errors.append('duplicate scene ID')
    by_id={s['id']:s for s in scenes}
    for i,s in enumerate(scenes):
        if not s['start']<=s['speechStart']<s['speechEnd']<=s['end']:errors.append(f"invalid speech interval: {s['id']}")
        if i and abs(s['start']-scenes[i-1]['end'])>.04:errors.append(f"gap or overlap before scene: {s['id']}")
    last_end=-1
    for c in timeline.get('captions',[]):
        s=by_id.get(c['scene'])
        if not s or not s['start']<=c['start']<c['end']<=s['end']:errors.append('caption outside scene')
        if c['start']<last_end-.04:errors.append('overlapping captions')
        last_end=c['end']
        if not c['text'].strip():errors.append('empty caption')
    for e in timeline.get('events',[]):
        s=by_id.get(e['scene'])
        if not s or not s['start']<=e['start']<s['end']:errors.append('focus outside scene')
        targets=timeline.get('targets',{}).get(e['scene'])
        if targets is not None and e['target'] not in targets:errors.append(f"unknown focus target: {e['scene']}/{e['target']}")
    for item in (contract or {}).get('retained',[]):
        if not item.get('scenes') or any(x not in by_id for x in item['scenes']):errors.append(f"retained content has no current location: {item['id']}")
        if item.get('asset') and asset_root and not (Path(asset_root)/item['asset']).is_file():errors.append(f"retained asset missing: {item['id']}")
    return errors

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('timeline');p.add_argument('--contract');p.add_argument('--asset-root');a=p.parse_args()
    errors=validate(json.loads(Path(a.timeline).read_text()),json.loads(Path(a.contract).read_text()) if a.contract else None,a.asset_root)
    print('\n'.join(errors) if errors else 'Timeline consistency passed; playback comprehension requires separate review.')
    raise SystemExit(bool(errors))
