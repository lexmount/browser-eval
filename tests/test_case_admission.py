import json, threading, time
from pathlib import Path
from types import SimpleNamespace
from harness.web_fetch.admission import CaseAdmission
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import run_evaluation

def method(name):return SimpleNamespace(name=name)
def case(host):return SimpleNamespace(id=host,url='https://'+host+'/')

def test_same_site_waits_and_distinct_cloud_local_can_overlap(tmp_path):
 local=CaseAdmission(tmp_path,group='local',site_cooldown=0)
 cloud=CaseAdmission(tmp_path,group='cloud',site_cooldown=0)
 a=local.acquire(method('a'),case('www.example.com'));seen=[]
 t=threading.Thread(target=lambda:seen.append(cloud.acquire(method('b'),case('api.example.com'))));t.start();time.sleep(.15);assert not seen
 local.release(a);t.join(1);assert seen;cloud.release(seen[0])
 a=local.acquire(method('a'),case('one.test'));b=cloud.acquire(method('b'),case('two.test'));state=json.loads((tmp_path/'state.json').read_text());assert len(state['active'])==2;local.release(a);cloud.release(b)

def test_second_local_waits_and_parallel_timeout_reduces_to_serial(tmp_path):
 local=CaseAdmission(tmp_path,group='local',site_cooldown=0);cloud=CaseAdmission(tmp_path,group='cloud',site_cooldown=0)
 a=local.acquire(method('a'),case('one.test'));seen=[];t=threading.Thread(target=lambda:seen.append(local.acquire(method('b'),case('two.test'))));t.start();time.sleep(.15);assert not seen
 c=cloud.acquire(method('c'),case('three.test'));cloud.release(c,record={'error_type':'timeout','attempt_id':'c'});assert not json.loads((tmp_path/'state.json').read_text())['parallel_enabled'];local.release(a);t.join(1);assert seen;local.release(seen[0])

def test_pending_call_keeps_site_and_resource_reserved(tmp_path):
 local=CaseAdmission(tmp_path,group='local',site_cooldown=0);a=local.acquire(method('a'),case('one.test'));local.release(a,pending=SimpleNamespace(is_alive=lambda:True));state=json.loads((tmp_path/'state.json').read_text());assert state['active'][a['key']]['quarantined'];assert a['key'] in state['active']

def test_admission_wait_excluded_from_answer_timeout_and_latency(tmp_path):
 class Fixture(Provider):
  def fetch(self,case,timeout):return ProviderResult(True,'Expected')
 class Gate:
  def describe(self):return {'fixture':True}
  def acquire(self,provider,case):time.sleep(.3);return 'token'
  def release(self,token,**kwargs):assert token=='token'
 p=Fixture('fixture',{'type':'fixture'});c=EvalCase('case','Case','https://example.com','static',['Expected'])
 start=time.monotonic();records,_=run_evaluation(output_dir=tmp_path/'run',dataset_meta={'id':'test'},dataset_path=Path('dataset.json'),cases=[c],providers=[p],provider_config_path=Path('providers.json'),runs=1,timeout=.1,delay=0,case_admission=Gate());assert time.monotonic()-start>=.3;assert records[0]['ok'];assert records[0]['elapsed_ms']<100;assert records[0].get('error_type') is None


def test_same_method_interval_is_not_replaced_by_site_cooldown(tmp_path):
    gate = CaseAdmission(tmp_path, group='local', site_cooldown=.3)
    a = gate.acquire(method('a'), case('one.test'))
    gate.release(a)
    begin = time.monotonic()
    a = gate.acquire(method('a'), case('one.test'))
    assert time.monotonic()-begin < .1
    gate.release(a)
    begin = time.monotonic()
    b = gate.acquire(method('b'), case('one.test'))
    assert time.monotonic()-begin >= .2
    gate.release(b)
