"""Synthesize reviewed narration through the configured Doubao HTTP service."""
from pathlib import Path
import argparse,base64,json,re,urllib.request,urllib.error,uuid,hashlib,os

def read_config():
    needed={'DOUBAO_TTS_API_KEY','DOUBAO_TTS_RESOURCE_ID','DOUBAO_TTS_VOICE_TYPE','DOUBAO_TTS_CONTEXT'}
    result={}
    config_file=Path.home()/'.env'
    for line in (config_file.read_text().splitlines() if config_file.exists() else []):
        match=re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)',line)
        if match and match[1] in needed:
            value=match[2].strip()
            if value[:1] in ['"',"'"] and value[-1:]==value[:1]:value=value[1:-1]
            result[match[1]]=value
    result.update({k:os.environ[k] for k in needed if os.environ.get(k)})
    return result

def synthesize(text,out,voice_override=None,speech_rate=0, context=None):
    cfg=read_config(); key=cfg.get('DOUBAO_TTS_API_KEY','');resource=cfg.get('DOUBAO_TTS_RESOURCE_ID','');voice=cfg.get('DOUBAO_TTS_VOICE_TYPE','')
    assert key and resource and voice,'Doubao voice configuration is incomplete'
    voice=voice_override or voice
    params={'text':text,'speaker':voice,'audio_params':{'format':'mp3','sample_rate':24000,'speech_rate':speech_rate,'enable_subtitle':True},'additions':json.dumps({'context_texts':[context or cfg.get('DOUBAO_TTS_CONTEXT','清晰自然地讲解，重点有起伏，结句利落。')]},ensure_ascii=False)}
    body=json.dumps({'req_params':params},ensure_ascii=False).encode()
    req=urllib.request.Request('https://openspeech.bytedance.com/api/v3/tts/unidirectional',body,{'Content-Type':'application/json','X-Api-Key':key,'X-Api-Resource-Id':resource,'X-Api-Request-Id':str(uuid.uuid4()),'X-Control-Require-Usage-Tokens-Return':'*'})
    audio=[];sentences=[];usage=[];codes=[]
    try:
        with urllib.request.urlopen(req,timeout=90) as response:
            for line in response:
                if not line.strip():continue
                item=json.loads(line);code=item.get('code',0);codes.append(code)
                if code not in [0,20000000]:raise RuntimeError('TTS service code '+str(code)+': '+str(item.get('message',''))[:180])
                if item.get('data'):audio.append(base64.b64decode(item['data']))
                if item.get('sentence'):sentences.append(item['sentence'])
                if item.get('usage'):usage.append(item['usage'])
    except urllib.error.HTTPError as error:
        raise RuntimeError('TTS HTTP '+str(error.code)+': '+error.read().decode(errors='replace')[:300]) from None
    assert codes and codes[-1]==20000000,'TTS stream ended before completion'
    raw=b''.join(audio);assert raw,'TTS returned no audio';out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(raw)
    meta={'provider':'doubao','resource':resource,'voice':voice,'speech_rate':speech_rate,'context':context or cfg.get('DOUBAO_TTS_CONTEXT',''),'text':text,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'codes':codes,'sentences':sentences,'usage':usage}
    out.with_suffix('.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    print(json.dumps({'audio':str(out),'bytes':len(raw),'timed_sentences':len(sentences),'codes':sorted(set(codes))},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--text');parser.add_argument('--text-file');parser.add_argument('--output',required=True);parser.add_argument('--voice');parser.add_argument('--speech-rate',type=int,default=0);args=parser.parse_args();text=Path(args.text_file).read_text() if args.text_file else args.text;assert text;synthesize(text,args.output,args.voice,args.speech_rate)
