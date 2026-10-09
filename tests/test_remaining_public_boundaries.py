"""Public offline diagnostics preserve evidence and report interaction contracts."""
import json
import os

import pytest

from harness.web_fetch.providers.cowagent import probe_extractor, probe_main
from harness.web_fetch.wcxb_report_check import check_visual


def test_trusted_extractor_probe_and_existing_output(tmp_path):
    source = tmp_path / 'source.py'
    source.write_text('raise RuntimeError("module must not be imported")\n'
                      'class WebFetch:\n'
                      '    @staticmethod\n'
                      '    def _extract_text(html):\n'
                      '        return re.sub(r"<[^>]+>", "", html)\n')
    expected = probe_extractor(source.read_text())
    output = tmp_path / 'probe.json'
    probe_main([str(source), '--output', str(output)])
    assert json.loads(output.read_text()) == expected
    assert expected['observations']['body_preserved']
    assert expected['observations']['navigation_preserved']
    assert not expected['observations']['detail_url_preserved']
    before = output.read_bytes()
    source.unlink()
    with pytest.raises(ValueError, match='output already exists'):
        probe_main([str(source), '--output', str(output)])
    assert output.read_bytes() == before


@pytest.mark.skipif(os.environ.get("BROWSER_EVAL_FULL_VISUAL_TESTS") != "1",
                    reason="explicit full visual gate requires local Chrome")
def test_wcxb_visual_generated_report(tmp_path):
    from harness.reporting.visual import browser_executable
    from harness.web_fetch.report import generate_wcxb
    from tests.wcxb_fixture import create_report_inputs

    inputs = create_report_inputs(tmp_path, english_source=False)
    # Synthetic report needs both reader-facing result states and repair specimens.
    diagnostic = inputs[1][-1]
    diagnostic['diagnosis'] = 'low_precision'
    diagnostic['f1'] = 0.25
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><text y="15">test</text></svg>'
    image = inputs[0] / 'specimen.svg'
    image.write_text(svg)
    card = ('<article class="repair-case"><figure><img src="specimen.svg" alt="synthetic" />'
            '</figure><div class="repair-samples"><pre>source</pre><pre>before</pre>'
            '<pre>after</pre></div></article>')
    inputs[-1]['guide']['zh'] = '<section id="guide-repair">' + card * 3 + '</section>'
    (inputs[0] / 'evidence').mkdir()
    for case_id in inputs[3]:
        outputs = {row['provider']: 'Independent article 0' for row in inputs[1]
                   if row['case_id'] == case_id}
        (inputs[0] / 'evidence' / f'{case_id}.json').write_text(json.dumps({
            'source_html': '<main>Independent article 0</main>', 'outputs': outputs}))
    generate_wcxb(*inputs)
    (inputs[0] / "wcxb-scores.jsonl").write_text("".join(json.dumps(row) + "\n" for row in inputs[1]))
    check_visual(inputs[0], browser_executable())


@pytest.mark.parametrize('entry', ['trace-catalog', 'catalog-actions', 'replay-merge'])
def test_existing_evidence_is_preserved_before_loading_inputs(tmp_path, entry):
    from harness.trajectory_replay.catalog_action_audit import main_audit
    from harness.trajectory_replay.public_webarena.webarena import main_catalog
    from harness.trajectory_replay.webarena_results import main_merge

    output = tmp_path / 'evidence.json'
    output.write_bytes(b'previous evidence')
    commands = {
        'trace-catalog': (main_catalog, ['--folder-html', 'missing', '--webarena-dataset', 'missing',
                                       '--webarena-commit', 'a', '--verified-dataset', 'missing',
                                       '--verified-commit', 'b']),
        'catalog-actions': (main_audit, ['--catalog', 'missing', '--portable-suite', 'missing',
                                        '--action-overrides', 'missing']),
        'replay-merge': (main_merge, ['--seeds', 'missing', '--qualified-dataset', 'missing',
                                     '--base-receipt', 'missing']),
    }
    function, args = commands[entry]
    with pytest.raises(ValueError, match='output already exists'):
        function([*args, '--output', str(output)])
    assert output.read_bytes() == b'previous evidence'
