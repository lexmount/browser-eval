"""Map reviewed clauses to spoken word times; refuse missing or reordered words."""
import unicodedata
from difflib import SequenceMatcher

def silence_cut(wave, sample_rate, previous_end, next_start):
    """Locate a sustained quiet interval, never assume timestamps imply silence.

    Provider alignment may place an English name after its audible onset. Search
    from the preceding utterance's end rather than bisecting that reported gap.
    Refuse an ambiguous cut so a build cannot publish a split spoken word.
    """
    import math
    step = max(1, round(sample_rate * .01))
    lo = max(0, int((previous_end - .08) * sample_rate) // step)
    hi = min(len(wave) // step, math.ceil((next_start + .2) * sample_rate / step))
    runs = []; start = None
    for frame in range(lo, hi):
        chunk = wave[frame * step:(frame + 1) * step]
        quiet = sum(float(x) ** 2 for x in chunk) / len(chunk) < 10 ** (-38 / 10)
        if quiet and start is None: start = frame
        if not quiet and start is not None:
            if frame - start >= 12: runs.append((start, frame))
            start = None
    if start is not None and hi - start >= 12: runs.append((start, hi))
    if not runs: raise ValueError(f'No verified silence between {previous_end:.3f} and {next_start:.3f}')
    start, end = runs[0]
    return (start + end) * step / (2 * sample_rate)

def normalize(text):
    return ''.join(c.lower() for c in text if unicodedata.category(c)[0] in 'LN')

def recognized_boundaries(scene_texts, recognition):
    """Locate scene boundaries in independent recognition, retaining authored text."""
    chars=[]; spans=[]
    for segment in recognition['segments']:
        for word in segment['words']:
            for char in normalize(word['word']):
                chars.append(char); spans.append((word['start'],word['end']))
    texts=[normalize(text) for text in scene_texts]
    wanted=''.join(texts)
    blocks=SequenceMatcher(None,wanted,''.join(chars),autojunk=False).get_matching_blocks()
    mapping={a+i:b+i for a,b,n in blocks for i in range(n)}
    boundaries=[]; offset=0
    for left,right in zip(texts,texts[1:]):
        offset+=len(left)
        before=[mapping[i] for i in range(max(0,offset-8),offset) if i in mapping]
        after=[mapping[i] for i in range(offset,min(len(wanted),offset+8)) if i in mapping]
        if len(before)<4 or len(after)<4:
            raise ValueError('Independent recognition cannot identify both sides of scene boundary')
        end=spans[before[-1]][1]; start=spans[after[0]][0]
        if end>start+.1: raise ValueError('Recognition overlaps scene boundary')
        boundaries.append((end,start))
    return boundaries

def align_clauses(clauses, sentences):
    chars=[];spans=[]
    for sentence in sentences:
        for w in sentence['words']:
            token=normalize(w['word'])
            for c in token:
                chars.append(c);spans.append((w['startTime'],w['endTime']))
    wanted=''.join(normalize(c['text']) for c in clauses);heard=''.join(chars)
    matches=SequenceMatcher(None,wanted,heard,autojunk=False).get_matching_blocks()
    mapping={a+i:b+i for a,b,n in matches for i in range(n)}
    if len(mapping)/max(1,len(wanted))<.97:raise ValueError('Narration does not cover the reviewed text')
    result=[];offset=0
    for clause in clauses:
        text=normalize(clause['text']);indices=[mapping[i] for i in range(offset,offset+len(text)) if i in mapping]
        if len(indices)<max(1,len(text)*.9):raise ValueError('Missing clause: '+clause['text'])
        row={'start':spans[indices[0]][0],'end':spans[indices[-1]][1],'anchors':[]}
        for anchor in clause.get('anchors',[]):
            at=text.find(normalize(anchor['phrase']))
            if at<0 or offset+at not in mapping:raise ValueError('Missing speech anchor: '+anchor['phrase'])
            row['anchors'].append({'time':spans[mapping[offset+at]][0],'target':anchor['target']})
        result.append(row);offset+=len(text)
    if any(a['end']>b['start']+.04 for a,b in zip(result,result[1:])):raise ValueError('Overlapping spoken clauses')
    return result
