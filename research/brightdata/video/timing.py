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

_ARABIC_NUMBER = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'
_NUMBER_PART = rf'(?:{_ARABIC_NUMBER}|[零〇一二两三四五六七八九十百千万亿]+)(?:点[零〇一二三四五六七八九]+)?'
_NUMBER = re.compile(rf'{_NUMBER_PART}(?:\s*[十百千万亿](?:{_NUMBER_PART})?)*')
_DIGITS = dict(zip('零〇一二两三四五六七八九', '001223456789'))

def _number_value(text):
    from decimal import Decimal
    text = ''.join(text.split()).replace(',', '')
    # Resolve large units before decimal notation, including 1.5亿 / 一点五亿.
    for unit, scale in [('亿', 100000000), ('万', 10000), ('千', 1000), ('百', 100), ('十', 10)]:
        if unit in text:
            left, right = text.split(unit, 1)
            return _number_value(left or '一') * scale + (_number_value(right) if right else 0)
    if '点' in text:
        left, right = text.split('点')
        return _number_value(left) + Decimal('0.' + ''.join(_DIGITS[c] for c in right))
    if text.isascii():
        return Decimal(text)
    return Decimal(''.join(_DIGITS[c] for c in text))

def _normalized_stream(text, spans):
    """Canonicalize quantities before dropping punctuation, retaining source times."""
    expanded = [(c, span) for raw, span in zip(text, spans)
                for c in unicodedata.normalize('NFKC', raw)]
    text = ''.join(c for c, _ in expanded)
    spans = [span for _, span in expanded]
    chars, times = [], []
    def append_literal(i):
        # Unsupported numeric operators remain significant rather than silently
        # converting a signed value, percentage or ratio into another number.
        neighbors = text[:i].rstrip()[-1:] + text[i+1:].lstrip()[:1]
        numeric_operator = text[i] in '+-−/%:‰' and any(
            c.isdigit() or c in '零〇一二两三四五六七八九十百千万亿' for c in neighbors)
        if unicodedata.category(text[i])[0] in 'LN' or numeric_operator:
            chars.append(text[i].lower()); times.append(spans[i])
    position = 0
    for match in _NUMBER.finditer(text):
        for i in range(position, match.start()):
            append_literal(i)
        value = format(_number_value(match.group()), 'f')
        if '.' in value: value = value.rstrip('0').rstrip('.')
        span = (spans[match.start()][0], spans[match.end()-1][1])
        # Boundaries distinguish two quantities separated by punctuation from
        # one larger quantity, even when ASR splits a number into timed words.
        value = '[' + value + ']'
        chars.extend(value); times.extend([span] * len(value))
        position = match.end()
    for i in range(position, len(text)):
        append_literal(i)
    return ''.join(chars), times

def normalize(text):
    return _normalized_stream(text, [(0, 0)] * len(text))[0]

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
            for char in convert(word['word']):
                chars.append(char); spans.append((start, end))
    texts = [norm(c['text']) for c in clauses]
    wanted = ''.join(texts)
    heard, spans = _normalized_stream(''.join(chars), spans)
    matcher = SequenceMatcher(None,wanted,heard,autojunk=False)
    mapping = {a+i:b+i for a,b,n in matcher.get_matching_blocks() for i in range(n)}
    explained_heard = set(mapping.values())
    for tag,a,b,c,d in matcher.get_opcodes():
        if tag == 'replace' and b-a == d-c and b-a <= 3:
            for left,right in zip(range(a,b),range(c,d)):
                x,y = wanted[left],heard[right]
                if ('\u4e00' <= x <= '\u9fff' and '\u4e00' <= y <= '\u9fff'
                    and set(pinyin(x,style=Style.TONE3,heteronym=True)[0]) & set(pinyin(y,style=Style.TONE3,heteronym=True)[0])):
                    mapping[left] = right
                    explained_heard.add(right)
    if len(explained_heard) != len(heard):
        raise ValueError('Independent recognition contains unexplained spoken words')
    if len(mapping) != len(wanted):
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
