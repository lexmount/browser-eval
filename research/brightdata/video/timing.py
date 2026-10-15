"""Map reviewed clauses to spoken word times; refuse missing or reordered words."""
import unicodedata
from difflib import SequenceMatcher

def normalize(text):
    return ''.join(c.lower() for c in text if unicodedata.category(c)[0] in 'LN')

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
