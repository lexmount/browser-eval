"""Map reviewed clauses to spoken word times; refuse missing or reordered words."""
import unicodedata
import re
from difflib import SequenceMatcher

def silence_cut(wave, sample_rate, previous_end, next_start):
    """Locate a sustained quiet interval, never assume timestamps imply silence.

    Provider alignment may place an English name after its audible onset. Search
    from the preceding utterance's end rather than bisecting that reported gap.
    Refuse an ambiguous cut so a build cannot publish a split spoken word.
    """
    import math
    step = max(1, round(sample_rate * .01))
    lo = max(0, math.ceil(previous_end * sample_rate / step))
    hi = min(len(wave) // step, math.floor(next_start * sample_rate / step))
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

def align_recognized_clauses(clauses, recognition, wave=None, sample_rate=24000):
    """Use independently heard words for captions and referents, not TTS labels.

    Reviewed wording remains authoritative. Recognition supplies timing only;
    missing, reordered or poorly matched speech fails instead of inventing cues.
    Traditional recognition is normalized without altering published text.
    """
    from opencc import OpenCC
    from pypinyin import pinyin, Style
    convert = OpenCC('t2s').convert
    norm = lambda text: normalize(convert(text))
    chars, spans = [], []
    previous = 0.0
    for segment in recognition['segments']:
        for word in segment['words']:
            start, end = float(word['start']), float(word['end'])
            if start < previous - .04 or end < start:
                raise ValueError('Independent speech timestamps are reversed')
            previous = end
            for char in norm(word['word']):
                chars.append(char); spans.append((start, end))
    texts = [norm(c['text']) for c in clauses]
    wanted = ''.join(texts)
    heard = ''.join(chars)
    matcher = SequenceMatcher(None,wanted,heard,autojunk=False)
    mapping = {a+i:b+i for a,b,n in matcher.get_matching_blocks() for i in range(n)}
    explained_heard = set(mapping.values())
    for tag,a,b,c,d in matcher.get_opcodes():
        def number(value):
            digits = dict(zip('零〇一二三四五六七八九', [0,0,1,2,3,4,5,6,7,8,9]))
            units = {'十':10,'百':100,'千':1000,'万':10000,'亿':100000000}
            if value.isascii() and value.isdigit(): return int(value)
            if not value or any(x not in digits and x not in units for x in value): return None
            if all(x in digits for x in value): return int(''.join(str(digits[x]) for x in value))
            total = part = digit = 0
            for x in value:
                if x in digits: digit = digits[x]
                elif units[x] < 10000: part += (digit or 1)*units[x]; digit=0
                else: total += (part+digit)*units[x]; part=digit=0
            return total+part+digit
        if tag == 'replace' and number(wanted[a:b]) is not None and number(wanted[a:b]) == number(heard[c:d]):
            for i in range(b-a): mapping[a+i] = c+min(d-c-1,i*(d-c)//(b-a))
            explained_heard.update(range(c,d))
        if tag == 'replace' and b-a == d-c and b-a <= 3:
            for left,right in zip(range(a,b),range(c,d)):
                x,y = wanted[left],heard[right]
                if ('\u4e00' <= x <= '\u9fff' and '\u4e00' <= y <= '\u9fff'
                    and set(pinyin(x,style=Style.TONE3,heteronym=True)[0]) & set(pinyin(y,style=Style.TONE3,heteronym=True)[0])):
                    mapping[left] = right
                    explained_heard.add(right)
    if any(('\u4e00' <= char <= '\u9fff' or char.isdigit()) and i not in explained_heard for i,char in enumerate(heard)):
        raise ValueError('Independent recognition contains unexplained spoken words')
    if len(mapping)/max(1,len(wanted)) < .88:
        raise ValueError('Independent recognition does not cover the reviewed narration')

    def onset(index):
        start, end = spans[index]
        if wave is None or end <= start:
            return start
        # ASR sometimes assigns a leading silence to the first word. Locate
        # actual energy inside that word, never borrow the following word.
        step = max(1, round(sample_rate*.01))
        lo, hi = round(start*sample_rate), min(len(wave),round(end*sample_rate))
        for at in range(lo,max(lo,hi-step),step):
            chunk = wave[at:at+step]
            if sum(float(x)**2 for x in chunk)/len(chunk) >= 10**(-42/10):
                return at/sample_rate
        return start

    rows, offset = [], 0
    for clause, text in zip(clauses,texts):
        matched = [i for i in range(offset,offset+len(text)) if i in mapping]
        if len(matched)/max(1,len(text)) < .80:
            raise ValueError('Unrecognized clause: '+clause['text'])
        # A common middle fragment cannot stand in for an omitted sentence edge.
        margin = max(2,min(6,round(len(text)*.25)))
        if matched[0]-offset > margin or offset+len(text)-1-matched[-1] > margin:
            raise ValueError('Unrecognized clause edge: '+clause['text'])
        for edge in (offset,offset+len(text)-1):
            if '\u4e00' <= wanted[edge] <= '\u9fff' and edge not in mapping:
                raise ValueError('Unrecognized Chinese clause edge: '+clause['text'])
        missing = ''.join(wanted[i] if i not in mapping else ' ' for i in range(offset,offset+len(text)))
        if re.search('[\u4e00-\u9fff0-9]',missing):
            raise ValueError('Unrecognized phrase inside clause: '+clause['text'])
        row = {'start':onset(mapping[matched[0]]),'cutStart':spans[mapping[matched[0]]][0],'end':spans[mapping[matched[-1]]][1],'anchors':[]}
        for anchor in clause.get('anchors',[]):
            phrase = norm(anchor['phrase']); at = text.find(phrase)
            if at < 0: raise ValueError('Missing speech anchor: '+anchor['phrase'])
            indices = [mapping[i] for i in range(offset+at,offset+at+len(phrase)) if i in mapping]
            if len(indices)/max(1,len(phrase)) < .65:
                raise ValueError('Unrecognized speech anchor: '+anchor['phrase'])
            row['anchors'].append({'time':onset(indices[0]),'target':anchor['target']})
        if rows and rows[-1]['end'] > row['start']+.04:
            raise ValueError('Independent recognition overlaps authored clauses')
        rows.append(row); offset += len(text)
    return rows
