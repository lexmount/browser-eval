"""Independently review every selected attempt with Codex or Claude; resumable by case batch."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.metrics import task_passed

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'attempts': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'attempt_id': {'type': 'string'},
            'verdict': {'enum': ['pass', 'fail', 'uncertain']},
            'evidence': {'type': 'string'},
        }, 'required': ['attempt_id', 'verdict', 'evidence']}}},
    'required': ['attempts'],
}

BLINDED_ATTEMPT_KEYS = (
    'attempt_id', 'provider', 'artifact', 'request_ok', 'status_code',
    'final_url', 'upstream_status', 'error_type', 'returned_links', 'file_artifacts',
    'raw_response_artifact',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chunks(values, size):
    for index in range(0, len(values), size):
        yield values[index:index+size]


def independent_case(case):
    """Keep declared acceptance evidence, but do not duplicate a huge full page.

    Body requirements already contain the exact frozen passages the task asks
    the reviewer to verify. The complete frozen page remains in dataset.json
    for on-demand inspection.
    """
    return {key: value for key, value in case.items()
            if key != 'ground_truth_main_content'}


def independent_attempt(row):
    # Do not prime the independent reviewer with machine verdicts, scores or
    # large derived URL inventories. Those are joined only after review.
    result = {key: row.get(key) for key in BLINDED_ATTEMPT_KEYS
              if key not in {'file_artifacts', 'raw_response_artifact'}}
    if row.get('file_artifacts'):
        result['file_artifacts'] = row['file_artifacts']
    if row.get('raw_response_artifact'):
        result['raw_response_artifact'] = row['raw_response_artifact']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--batch-cases', type=int, default=5)
    parser.add_argument('--workers', type=int, default=1,
                        help='Independent Claude Code batches to run concurrently')
    parser.add_argument('--timeout-seconds', type=int, default=900,
                        help='Hard deadline for each independent review batch')
    parser.add_argument('--protocol-retries', type=int, default=2,
                        help='Retries only API/protocol failures, never a substantive verdict')
    parser.add_argument('--model', required=True,
                        help='Exact reviewer model route; aliases can drift and are intentionally rejected here')
    parser.add_argument('--reviewer', choices=('codex','claude','internal'), default='codex')
    parser.add_argument('--provider', action='append', default=[],
                        help='Limit review packets to one or more exact provider IDs')
    parser.add_argument('--prepare-only', action='store_true',
                        help='Write blinded input packets, then stop before invoking a reviewer')
    parser.add_argument('--scope', choices=('all','machine-failures'), default='all',
                        help='Review every output or only outputs currently judged failed')
    parser.add_argument('--adjudications', type=Path)
    parser.add_argument('--install', action='store_true',
                        help='Copy the complete, fully adjudicated review into the evidence bundle')
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    work = args.work.resolve(); work.mkdir(parents=True, exist_ok=True)
    dataset = json.loads((evidence/'dataset.json').read_text())
    cases = {case['id']: case for case in dataset['cases']}
    records = [json.loads(line) for line in (evidence/'results.jsonl').read_text().splitlines()]
    by_case = {case_id: [] for case_id in cases}
    selected_providers = set(args.provider)
    unknown_providers = selected_providers - {record['provider'] for record in records}
    if unknown_providers:
        raise SystemExit(f'Unknown provider(s): {", ".join(sorted(unknown_providers))}')
    for record in records:
        if selected_providers and record['provider'] not in selected_providers:
            continue
        machine = task_passed(record['ok'], record['metrics'], cases[record['case_id']].get('task_kind', 'body'))
        machine_verdict = 'pass' if machine else 'fail'
        if args.scope == 'machine-failures' and machine_verdict != 'fail':
            continue
        by_case[record['case_id']].append({
            'attempt_id': record['attempt_id'], 'provider': record['provider'],
            'machine_verdict': machine_verdict,
            'artifact': str(evidence/record['artifact_path']),
            'request_ok': record['ok'], 'error_type': record.get('error_type'),
            'status_code': record.get('status_code'),
            'final_url': record.get('final_url'),
            'upstream_status': record.get('provider_metadata', {}).get('upstream_status'),
            'returned_links': record.get('provider_metadata', {}).get('links', []),
            'metrics': record['metrics'],
            'file_artifacts': [str(evidence/file['artifact_path']) for file in
                               record.get('provider_metadata',{}).get('file_evaluation',{}).get('files',[])
                               if file.get('artifact_path')],
            'raw_response_artifact': (str(evidence/record['provider_metadata']['raw_stdout_path'])
                                      if record.get('provider_metadata',{}).get('raw_stdout_path') else None),
        })
    ordered = [case['id'] for case in dataset['cases'] if by_case[case['id']]]
    schema_path=work/'review-schema.json'
    schema_path.write_text(json.dumps(SCHEMA,ensure_ascii=False,indent=2)+'\n')
    def review_batch(job):
        batch_index, case_ids = job
        output = work/f'batch-{batch_index:03d}.json'
        packet = work/f'batch-{batch_index:03d}-input.json'
        expected = {row['attempt_id'] for cid in case_ids for row in by_case[cid]}
        if output.is_file():
            if not packet.is_file():
                raise ValueError(f'Batch {batch_index} has output without its blinded input packet')
            saved_packet = json.loads(packet.read_text())
            if any(set(row) - set(BLINDED_ATTEMPT_KEYS)
                   for row in saved_packet.get('attempts', [])):
                raise ValueError(f'Batch {batch_index} exposes machine judgments; use a new review directory')
            result = json.loads(output.read_text())
        else:
            packet.write_text(json.dumps({
                'full_dataset': str(evidence/'dataset.json'),
                'cases': [independent_case(cases[cid]) for cid in case_ids],
                'attempts': [independent_attempt(row) for cid in case_ids for row in by_case[cid]],
            }, ensure_ascii=False, indent=2)+'\n')
            if args.prepare_only:
                return []
            prompt = f'''Independently audit every attempt in {packet}. This is a result-verification stage, not a code review.
Inspect every artifact named for an attempt, including returned_links, file_artifacts and raw_response_artifact when present, and compare it with that case's task, exact body-region passages, required facts/resources/patterns, stable contract, and exclusions. returned_links is part of the saved provider response and is the evidence source for named-link structure even when the plain-text artifact omits URLs. For direct-file tasks, the text artifact may intentionally be empty: verify the supplied file artifact's signature, size and reference hash instead of treating empty text as failure. Use Read, Grep and read-only hashing/file-identification commands as needed; the full frozen page is available at full_dataset when context beyond the declared acceptance evidence is necessary. The packet deliberately omits machine verdicts and scores. Decide whether the saved output satisfies the declared task. Review successes and failures equally. Return exactly one row per attempt ID ({len(expected)} rows), no omissions or extras. Use uncertain only when the frozen evidence is genuinely insufficient. Evidence must be a short, concrete observable reason; do not expose chain-of-thought.
Stable text anchors are literal requirements after the benchmark's declared normalization: Unicode NFKC, case-folding, Markdown cleanup and whitespace normalization. Only a declared alternative can substitute for one; semantically related text is not a hit. Before returning pass, locate every required fact group, stable anchor, resource and record constraint in the supplied evidence; never claim a passage or link is present without finding it. Evidence for a pass must name the observed required anchors or fields, not merely say that all criteria are met.'''
            system=('You are an independent benchmark evidence auditor. Follow the task exactly. '
                    'Use only read operations, report observable evidence, and never trust stored verdicts. ')
            raw_output=work/f'batch-{batch_index:03d}-raw.json'
            if args.reviewer=='internal':
                raise SystemExit(
                    f'Internal review output missing for batch {batch_index}; '
                    f'write {output} from the blinded packet before aggregation')
            if args.reviewer=='codex':
                command=['codex','exec','--model',args.model,'--sandbox','read-only',
                         '--add-dir',str(evidence),'--ephemeral',
                         '-c','model_reasoning_effort="high"',
                         '--output-schema',str(schema_path),
                         '--output-last-message',str(raw_output),system+prompt]
            else:
                command = ['claude', '--print', '--model', args.model, '--effort', 'high',
                           '--permission-mode', 'dontAsk', '--allowedTools', 'Read,Glob,Grep',
                           '--add-dir', str(evidence), '--output-format', 'json',
                           '--json-schema', json.dumps(SCHEMA), '--no-session-persistence', prompt]
                command[2:2] = ['--system-prompt',system]
            problem = ''
            for protocol_attempt in range(args.protocol_retries + 1):
                try:
                    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                                               timeout=args.timeout_seconds)
                    if completed.returncode:
                        problem = completed.stderr or completed.stdout
                        continue
                    if args.reviewer=='codex':
                        result=json.loads(raw_output.read_text())
                    else:
                        envelope = json.loads(completed.stdout)
                        result = envelope.get('structured_output') or json.loads(envelope['result'])
                    ids = [row['attempt_id'] for row in result['attempts']]
                    if len(ids) == len(set(ids)) and set(ids) == expected:
                        break
                    problem = f'Batch {batch_index} did not cover its exact attempt set'
                except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, TypeError) as error:
                    problem = f'Batch {batch_index} protocol error: {error}'
            else:
                raise SystemExit(problem)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            print(f'batch {batch_index}/{len(jobs)} reviewed ({len(expected)} attempts)',flush=True)
        return result['attempts']
    jobs=list(enumerate(chunks(ordered, args.batch_cases),1))
    if args.prepare_only:
        for job in jobs:
            review_batch(job)
        print(f'prepared={len(jobs)} work={work}')
        return
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        reviewed=list(pool.map(review_batch,jobs))
    all_rows=[row for batch in reviewed for row in batch]
    reviewed_ids={row['attempt_id'] for rows in by_case.values() for row in rows}
    if {row['attempt_id'] for row in all_rows} != reviewed_ids:
        raise SystemExit('Review batches do not cover the selected results')
    machine = {row['attempt_id']: row for rows in by_case.values() for row in rows}
    adjudications = json.loads(args.adjudications.read_text()) if args.adjudications else {}
    attempts = []
    for row in all_rows:
        aid = row['attempt_id']; agent = row['verdict']; automated = machine[aid]['machine_verdict']
        final = agent if agent == automated else (adjudications.get(aid) or {}).get('final_verdict')
        attempts.append({'attempt_id': aid, 'machine_verdict': automated,
                         'agent_verdict': agent, 'agent_evidence': row['evidence'],
                         'final_verdict': final,
                         'adjudication': (adjudications.get(aid) or {}).get('reason')})
    review = {'schema_version': 1,
              'bindings': {'dataset_sha256': sha(evidence/'dataset.json'),
                           'results_sha256': sha(evidence/'results.jsonl')},
              'reviewer': {
                  'agent': ('Codex task subagents' if args.reviewer == 'internal' else
                            ('Codex' if args.reviewer=='codex' else 'Claude Code')),
                  'model': args.model,
                  'effort': 'high',
                  'cli_version': ('Codex desktop task runtime' if args.reviewer == 'internal' else
                                  subprocess.check_output(
                                      ['codex' if args.reviewer=='codex' else 'claude','--version'],
                                      text=True).strip())},
              'review_protocol': {'machine_judgments_withheld': True,
                                  'complete_artifact_paths_supplied': True,
                                  'declared_acceptance_evidence_supplied': True,
                                  'provider_filter': sorted(selected_providers),
                                  'scope': args.scope},
              'total_attempts':len(records),
              'accepted_unreviewed_passes':len(records)-len(reviewed_ids),
              'expected_attempts': len(reviewed_ids), 'reviewed_attempts': len(attempts),
              'disagreements': sum(row['agent_verdict'] != row['machine_verdict'] for row in attempts),
              'unresolved': sum(row['final_verdict'] not in {'pass','fail'} for row in attempts),
              'attempts': attempts}
    target = work/'result-review.json'
    target.write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
    if args.install:
        if review['unresolved']:
            raise SystemExit('Cannot install a review with unresolved verdicts')
        shutil.copy2(target, evidence/'result-review.json')
    print(target)
    print(f"reviewed={len(attempts)} disagreements={review['disagreements']} unresolved={review['unresolved']}")


if __name__ == '__main__':
    main()
