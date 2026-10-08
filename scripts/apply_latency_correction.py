"""Supersede selected logical latency values from a complete final diagnostic round."""
import argparse
import hashlib
import json
import shutil
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from harness.record_io import parse_jsonl
from harness.web_fetch.reference_lifecycle import scoring_revision_history_sha256
from harness.web_fetch.result_review import rebind_unchanged_result_review


def digest(data):return hashlib.sha256(data).hexdigest()
def percentile(values,p):
 values=sorted(values);position=(len(values)-1)*p;low=int(position);high=min(low+1,len(values)-1)
 return values[low]+(values[high]-values[low])*(position-low)

def validated_final_round(diagnostic):
 if not diagnostic:raise SystemExit('diagnostic correction scope is empty')
 rounds=sorted({row['round'] for row in diagnostic});selected_round=rounds[-1]
 selected=[row for row in diagnostic if row['round']==selected_round]
 keys={(row['provider'],row['case_id']) for row in selected}
 if (len(rounds)<2 or len(keys)!=len(selected)
     or len(diagnostic)!=len(rounds)*len(keys)
     or any({(row['provider'],row['case_id']) for row in diagnostic if row['round']==round_id}!=keys for round_id in rounds)):
  raise SystemExit('diagnostic rounds are incomplete, duplicated or have different scopes')
 if any(not row['ok'] for row in diagnostic):
  raise SystemExit('diagnostic correction scope contains a failed call')
 return selected_round,selected,keys

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('base',type=Path);parser.add_argument('diagnostic',type=Path);parser.add_argument('output',type=Path)
 args=parser.parse_args()
 if args.output.exists():raise SystemExit('output already exists: '+str(args.output))
 before=(args.base/'results.jsonl').read_bytes();dataset=(args.base/'dataset.json').read_bytes()
 diagnostic_raw=(args.diagnostic/'results.jsonl').read_bytes()
 diagnostic=parse_jsonl(diagnostic_raw)
 selected_round,selected,keys=validated_final_round(diagnostic)
 identities=json.loads((args.diagnostic/'identity.json').read_text())
 records=parse_jsonl(before);by_key={(r['provider'],r['case_id']):r for r in records}
 if not keys<=by_key.keys():raise SystemExit('diagnostic correction scope is incomplete')
 shutil.copytree(args.base,args.output)
 changes=[]
 for row in selected:
  record=by_key[(row['provider'],row['case_id'])];metadata=record.setdefault('provider_metadata',{})
  if metadata.get('binary_sha256')!=identities[row['provider']]['binary_sha256']:
   raise SystemExit('binary identity mismatch: '+row['provider'])
  original=record['elapsed_ms'];record['elapsed_ms']=row['elapsed_ms']
  metadata.update(row.get('provider_timings',{}))
  metadata['latency_correction']={'kind':'documented_final_round_supersession','selected_round':selected_round,
   'original_elapsed_ms':original,'corrected_elapsed_ms':row['elapsed_ms'],
   'diagnostic_results_sha256':digest(diagnostic_raw),'diagnostic_content_sha256':row['content_sha256'],
   'content_byte_identical':record['metrics']['content_sha256']==row['content_sha256']}
  changes.append({'provider':row['provider'],'case_id':row['case_id'],'before_ms':original,'after_ms':row['elapsed_ms']})
 after=(''.join(json.dumps(r,ensure_ascii=True)+'\n' for r in records)).encode()
 (args.output/'results.jsonl').write_bytes(after)
 rebound_at=datetime.now(timezone.utc).isoformat()
 rebind_unchanged_result_review(args.output/'result-review.json',dataset,before,dataset,after,rebound_at=rebound_at)
 new_hash=digest(after);old_hash=digest(before);dataset_hash=digest(dataset)
 reference_path=args.output/'reference-revisions.json'
 if reference_path.is_file():
  rows=json.loads(reference_path.read_text())
  for receipt in rows:
   if receipt.get('output_dataset_sha256')==dataset_hash and receipt.get('output_results_sha256')==old_hash:
    receipt['prior_output_results_sha256']=old_hash;receipt['output_results_sha256']=new_hash
    receipt['rebound_after_latency_only_correction']={'at':rebound_at,'provider_calls':len(diagnostic)}
  reference_path.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
 history_path=args.output/'scoring-revisions.json';history=json.loads(history_path.read_text())
 review_hash=digest((args.output/'result-review.json').read_bytes())
 history.append({'kind':'audited_assembled_scoring_revision','scored_at':rebound_at,
  'input_dataset_sha256':dataset_hash,'input_results_sha256':old_hash,
  'output_dataset_sha256':dataset_hash,'output_results_sha256':new_hash,
  'changed_cases':[],'task_decision_changes':[],'provider_calls':0,
  'immutable_call_fields':'task inputs, output bytes/hash, request status, metrics and verdicts retained; only selected elapsed time superseded',
  'source_scoring_receipts_sha256':scoring_revision_history_sha256(history),
  'independent_result_review_sha256':review_hash,'latency_correction_receipt':'latency-corrections.json'})
 history_path.write_text(json.dumps(history,ensure_ascii=False,indent=2)+'\n')
 summary={}
 for provider in sorted({row['provider'] for row in diagnostic}):
  values=[row['elapsed_ms'] for row in diagnostic if row['provider']==provider]
  summary[provider]={'calls':len(values),'mean_ms':round(statistics.mean(values),2),
   'p50_ms':round(statistics.median(values),2),'p95_ms':round(percentile(values,.95),2)}
 receipt={'schema_version':1,'corrected_at':rebound_at,'reason':'supersede unstable selected latency measurements with the final round of a three-round rotated paired correction',
  'selection':'same four cases for all three Moli versions; final round always selected, never best-of',
  'diagnostic_results_sha256':digest(diagnostic_raw),'physical_calls':len(diagnostic),'selected_round':selected_round,
  'superseded_logical_results':len(changes),'input_results_sha256':old_hash,'output_results_sha256':new_hash,
  'task_decision_changes':0,'diagnostic_summary':summary,'changes':changes}
 (args.output/'latency-corrections.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(receipt,ensure_ascii=False))

if __name__=='__main__':main()
