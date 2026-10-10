"""Compact, synthetic sample evidence; no provider, model, or official scorer calls."""
import hashlib
import json

from harness.web_fetch.webmainbench_evidence import digest, scored_shard_bindings
from harness.web_fetch.webmainbench_scoring import CORE_METRICS, SCORER_COMMIT, aggregate


def write_json(path, value):
    path.write_text(json.dumps(value))


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def sample_fixture(root, *, providers=('engine-a', 'new-engine'), size=2, parent_size=3):
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'source.jsonl'
    parents = [{'track_id': f'case-{index}', 'html': f'<main>Fact {index}</main>',
                'groundtruth_content': f'Fact {index}', 'url': f'https://example.invalid/{index}',
                'meta': {}} for index in range(parent_size)]
    write_rows(source, parents)
    manifest = root / 'sample.json'
    selected = parents[:size]
    write_json(manifest, {'schema_version': 1, 'dataset_id': 'synthetic-sample',
                         'parent': {'sha256': digest(source), 'records': parent_size},
                         'sampling': {'size': size},
                         'cases': [{'track_id': row['track_id']} for row in selected]})
    metric_sources = {'metric.py': 'c' * 64}
    profile = root / 'profile.json'
    write_json(profile, {'dataset_id': 'synthetic-sample', 'comparison_kind': 'webmainbench_frozen_html_to_markdown',
                         'case_count': size, 'providers': list(providers),
                         'source_dataset_sha256': digest(source), 'sample_manifest_sha256': digest(manifest),
                         'timeout_seconds': 45, 'runs_per_case': 1, 'concurrency': 1, 'max_failure_retries': 1,
                         'official_scorer_commit': SCORER_COMMIT, 'metric_config': {'use_llm': False},
                         'metric_source_manifest_sha256': hashlib.sha256(json.dumps(metric_sources, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                         'publication_state': 'diagnostic_not_for_release'})
    shards = root / 'shards'
    shards.mkdir()
    for provider in providers:
        shard = shards / provider
        (shard / 'artifacts' / provider).mkdir(parents=True)
        rows, calls = [], []
        for index, case in enumerate(selected):
            track_id = case['track_id']
            content = case['groundtruth_content']
            relative = f'artifacts/{provider}/{track_id}.md'
            (shard / relative).write_text(content)
            input_hash = hashlib.sha256(case['html'].encode()).hexdigest()
            reference_hash = hashlib.sha256(content.encode()).hexdigest()
            chain = []
            for physical, body in enumerate(('first call failed', content) if index == 0 else (content,), 1):
                physical_path = f'artifacts/{provider}/{track_id}-p{physical:02d}.txt'
                (shard / physical_path).write_text(body)
                chain.append({'provider': provider, 'track_id': track_id,
                              'attempt_id': f'{provider}__{track_id}__p{physical:02d}',
                              'physical_attempt': physical, 'elapsed_ms': 10,
                              'physical_artifact_path': physical_path,
                              'ok': body == content, 'error_type': None if body == content else 'timeout',
                              'output_sha256': digest(shard / physical_path),
                              'input_html_sha256': input_hash, 'reference_sha256': reference_hash})
            calls.extend(chain)
            metrics = {name: {'score': 1.0 if name == 'text_edit' else 0.0,
                              'success': name == 'text_edit'} for name in CORE_METRICS}
            rows.append({'provider': provider, 'track_id': track_id,
                         'attempt_id': f'{provider}__{track_id}__r001', 'ok': True, 'error_type': None,
                         'artifact_path': relative, 'output_sha256': digest(shard / relative),
                         'input_html_sha256': input_hash, 'reference_sha256': reference_hash,
                         'elapsed_ms': 10, 'total_elapsed_ms': 10 * len(chain),
                         'physical_attempt_count': len(chain),
                         'retry_selection': 'final_physical_attempt_is_authoritative', 'metrics': metrics})
        write_rows(shard / 'scored-results.jsonl', rows)
        write_rows(shard / 'results.jsonl', [{key: value for key, value in row.items() if key != 'metrics'} for row in rows])
        write_rows(shard / 'physical-attempts.jsonl', calls)
        selection = {'sha256': digest(manifest)}
        write_json(shard / 'dataset.json', {'case_count': size, 'case_ids': [row['track_id'] for row in selected], 'selection_manifest': selection})
        write_json(shard / 'execution.json', {'provider': provider, 'case_count': size,
                                              'timeout_seconds': 45, 'max_failure_retries': 1,
                                              'source_dataset_sha256': digest(source), 'selection_manifest': selection,
                                              'network_isolation_probe': {'mode': 'cloudflare_reject_external_resource_types'}})
        write_json(shard / 'score-summary.json', {'provider': provider, 'case_count': size,
                                                  'official_aggregate': aggregate(rows),
                                                  'scorer': {'commit': SCORER_COMMIT, 'metric_config': {'use_llm': False},
                                                             'metric_source_sha256': metric_sources}})
        (shard / 'private-notes.txt').write_text('Never copy unbound files')
    review = root / 'review.json'
    write_json(review, {'schema_version': 1, 'status': 'passed', 'failures': [],
                        'dataset_sha256': digest(source), 'sample_manifest_sha256': digest(manifest),
                        'profile_sha256': digest(profile), 'sample_case_count': size,
                        'providers': list(providers), 'checks': [{'id': 'synthetic control', 'status': 'passed'}],
                        'bindings': {'provider_files_sha256': scored_shard_bindings(shards, list(providers))}})
    return {'root': shards, 'source': source, 'manifest': manifest, 'profile': profile,
            'review': review, 'output': root / 'assembled'}
