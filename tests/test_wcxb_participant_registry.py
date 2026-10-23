"""A report instance may use participant IDs absent from historical cohorts."""
from harness.web_fetch.report import generate_wcxb
from tests.wcxb_fixture import create_report_inputs


def test_wcxb_renderer_accepts_entirely_new_participant_ids(tmp_path):
    root, rows, summary, ground_truth, identities, profile, content = create_report_inputs(tmp_path)
    names = {name: 'participant-' + str(i) for i, name in enumerate(summary['providers'])}
    for row in rows:
        row['provider'] = names[row['provider']]
    summary['providers'] = {names[name]: values for name, values in summary['providers'].items()}
    identities = {names[name]: values for name, values in identities.items()}
    profile['providers'] = [names[name] for name in profile['providers']]
    profile['versions'] = {names[name]: value for name, value in profile['versions'].items()}
    profile['paired_comparison'] = {'baseline': names['moli'], 'candidate': names['moli-pr643']}
    for name, replacement in names.items():
        (root / 'artifacts' / name).rename(root / 'artifacts' / replacement)
    generate_wcxb(root, rows, summary, ground_truth, identities, profile, content, include_english=True)
    for prefix in ('', 'en/'):
        home = (root / prefix / 'index.html').read_text()
        assert all('providers/' + name + '.html' in home for name in names.values())
        assert 'data-baseline="' + names['moli'] + '"' in home
        assert 'data-candidate="' + names['moli-pr643'] + '"' in home
        assert 'Only Kitesurf' not in (root / prefix / 'accounts/index.html').read_text()


def test_public_wcxb_build_check_package_accept_external_nonhistorical_cohort(tmp_path):
    import hashlib
    import json
    import subprocess
    import sys
    from pathlib import Path
    from harness.web_fetch.wcxb_scoring import score
    from tests.wcxb_fixture import create_cohort, write_json

    repository = Path(__file__).resolve().parents[1]
    cohort = create_cohort(tmp_path / 'cohort', providers=('alpha', 'beta', 'gamma'))
    summary = score(**cohort)
    evidence = cohort['evidence_root']
    profile = json.loads(cohort['profile_path'].read_bytes())
    review_path = evidence / 'report-assets/research/wcxb-representative-review.json'
    write_json(review_path, {'scope': 'targeted_representative_anomaly_review',
        'case_count': 0, 'result_count': 0, 'total_results': 6, 'unreviewed_results': 6, 'cases': []})
    profile.update(source_repository='https://example.invalid/benchmark', source_commit='a' * 40,
                   versions={name: 'synthetic' for name in profile['providers']},
                   report_assets={'research/wcxb-representative-review.json':
                                  hashlib.sha256(review_path.read_bytes()).hexdigest()})
    anchors = ('guide-results', 'guide-score', 'guide-cases', 'guide-methods',
               'guide-timing', 'guide-conclusion')
    rows = []
    for name, values in summary['providers'].items():
        rows.append('<tr><td><a href="providers/' + name + '.html">' + name + '</a></td>'
                    '<td data-metric="f1" data-metric-direction="max" data-sort="' + str(values['quality_f1']) + '">'
                    + format(values['quality_f1'] * 100, '.2f') + '%</td>'
                    '<td data-metric="mean" data-metric-direction="min" data-sort="' + str(values['mean_latency_ms']) + '">'
                    + format(values['mean_latency_ms'], ',.0f') + ' ms</td></tr>')
    table = '<table><thead><tr><th>Method</th><th>F1</th><th>Latency</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
    body = '<nav class="essay-toc">' + ''.join('<a href="#' + anchor + '">' + anchor + '</a>' for anchor in anchors) + '</nav>'
    for anchor in anchors:
        detail = table if anchor in ('guide-results', 'guide-methods') else '<p>Frozen inputs and retained outputs are compared by the same official evaluator.</p>'
        if anchor == 'guide-cases':
            detail += '<a href="cases/case-000.html">Inspect source, reference and outputs</a>'
        if anchor == 'guide-conclusion':
            detail = ('<p>The complete population contains two frozen pages and three methods. '
                      'One method has a retained request failure, which stays in the score and latency population. '
                      'The other methods tie on these synthetic inputs. These fixtures test evidence handling; '
                      'they do not measure browser capability or support an adoption recommendation.</p>')
        body += '<section id="' + anchor + '"><h2>' + anchor + '</h2>' + detail + '</section>'
    content_path = evidence / 'report-assets/report-content.json'
    write_json(content_path, {'schema_version': 1, 'guide': {'zh': body, 'en': body}})
    profile_path = tmp_path / 'instance/profile.json'
    write_json(profile_path, profile)
    binding = {'schema_version': 1, 'relative_path': 'report-content.json',
        'sha256': hashlib.sha256(content_path.read_bytes()).hexdigest(),
        'evidence': {'wcxb-score-summary.json': hashlib.sha256((evidence / 'wcxb-score-summary.json').read_bytes()).hexdigest()},
        'authority': {'configs/report-templates.json': hashlib.sha256((repository / 'configs/report-templates.json').read_bytes()).hexdigest()},
        'rendered_sha256': {'guide': {lang: hashlib.sha256(body.encode()).hexdigest() for lang in ('zh', 'en')}}}
    write_json(profile_path.with_name('report-content-binding.json'), binding)
    output = tmp_path / 'report'
    commands = [
        ['scripts/build_report.py', '--profile', str(profile_path), '--evidence', str(evidence),
         '--wcxb-source', str(cohort['source']), '--wcxb-sample', str(cohort['sample_manifest']),
         '--output', str(output), '--language', 'en'],
        ['scripts/check_report.py', str(output), '--profile', str(profile_path)],
        ['scripts/publish_wcxb_artifact_site.py', '--package-only', '--source', str(output),
         '--profile', str(profile_path), '--work', str(tmp_path / 'package')],
    ]
    for command in commands:
        result = subprocess.run([sys.executable, *command], cwd=repository, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / 'package/wcxb-current.zip').is_file()
