"""Assemble measured batches for append; never call providers or alter scores."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.append_dynamic_content import cases_by_id, frozen_case
from harness.web_fetch.safety import contained_path, validate_report_ids


def merge(reference, runs, output):
    dataset = json.loads((reference/'dataset.json').read_text())
    expected = {k:frozen_case(c) for k,c in cases_by_id(dataset).items()}
    records, batches, artifacts = [], [], {}
    for run in runs:
        actual = json.loads((run/'dataset.json').read_text())
        if expected != {k:frozen_case(c) for k,c in cases_by_id(actual).items()}:
            raise ValueError('Run differs from frozen cases')
        rows = [json.loads(line) for line in (run/'results.jsonl').read_text().splitlines()]
        validate_report_ids(dataset, rows)
        for row in rows:
            path = contained_path(run, row['artifact_path'])
            if row['artifact_path'] in artifacts or hashlib.sha256(path.read_bytes()).hexdigest()!=row['metrics']['content_sha256']:
                raise ValueError('Duplicate artifact or hash mismatch')
            artifacts[row['artifact_path']] = path
        for batch in json.loads((run/'execution.json').read_text())['batches']:
            public = deepcopy(batch)
            # Omit host/private filesystem paths only; IDs, time, settings and
            # measured hashes remain unchanged. Originals stay in the run dir.
            public.get('environment',{}).pop('hostname',None)
            public['dataset']['source'] = 'frozen-dynamic-supplement/dataset.json'
            public['provider_config_source'] = 'configs/providers.resources.json'
            identity = run/'runtime-identity.json'
            if identity.exists():
                public['runtime_identity_record'] = json.loads(identity.read_text())
                public['runtime_identity_sha256'] = hashlib.sha256(identity.read_bytes()).hexdigest()
                public['provider_config_source'] = 'isolated local overrides; see runtime_identity_record.provider_config_sha256'
            batches.append(public)
        records.extend(rows)
    validate_report_ids(dataset, records)
    output.mkdir(parents=True,exist_ok=False)
    for relative, source in artifacts.items():
        target=contained_path(output,relative)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    for filename,value in [('dataset.json',dataset),('execution.json',{'batches':batches})]:
        (output/filename).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (output/'results.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in records))
    print(f'Assembled {len(records)} unchanged measurements in {len(batches)} original batches')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--runs',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    merge(args.reference,args.runs,args.output)
