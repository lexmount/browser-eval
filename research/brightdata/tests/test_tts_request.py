import sys,unittest,json,tempfile,base64
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'video'))
from tts import synthesize
class Response:
 def __init__(self,complete=True):self.lines=[json.dumps({'code':0,'data':base64.b64encode(b'fake-audio').decode()}).encode()]+([b'{"code":20000000}'] if complete else [])
 def __enter__(self):return iter(self.lines)
 def __exit__(self,*args):pass
class VoiceRequestTests(unittest.TestCase):
 def config(self):return {'DOUBAO_TTS_API_KEY':'test-only','DOUBAO_TTS_RESOURCE_ID':'seed-tts-2.0','DOUBAO_TTS_VOICE_TYPE':'voice'}
 def test_direction_and_timestamps_are_actually_sent(self):
  with tempfile.TemporaryDirectory() as temp,patch('tts.read_config',return_value=self.config()),patch('tts.urllib.request.urlopen',return_value=Response()) as request:
   out=Path(temp)/'voice.mp3';synthesize('完整句子。',out,'new-voice',-3,'清晰自然，有重点')
   body=json.loads(request.call_args.args[0].data)['req_params']
   self.assertEqual(body['speaker'],'new-voice');self.assertTrue(body['audio_params']['enable_subtitle'])
   self.assertEqual(json.loads(body['additions'])['context_texts'],['清晰自然，有重点'])
   self.assertNotIn('test-only',out.with_suffix('.json').read_text())
 def test_incomplete_stream_cannot_be_used_as_a_finished_voice(self):
  with tempfile.TemporaryDirectory() as temp,patch('tts.read_config',return_value=self.config()),patch('tts.urllib.request.urlopen',return_value=Response(False)):
   out=Path(temp)/'voice.mp3'
   with self.assertRaises(AssertionError):synthesize('完整句子。',out)
   self.assertFalse(out.exists())
if __name__=='__main__':unittest.main()
