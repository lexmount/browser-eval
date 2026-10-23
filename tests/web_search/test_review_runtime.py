"""The reviewer really starts a constrained child and reuses only completed work."""
import json
import sys

import pytest

from harness.web_search import review
from tests.web_search.test_review import example, opinion


def stub_backend(root, contract, *, exit_code=0):
    replies = {}
    for entry in contract['packets']:
        packet = json.loads((root / entry['path']).read_bytes())
        replies[entry['sha256']] = opinion(packet, entry['sha256'])
    executable = root / 'fixture-codex'
    executable.write_text(f'''#!{sys.executable}
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
output=Path(args[args.index('--output-last-message')+1])
text=sys.stdin.read();payload=json.loads(text[text.index('{{"packet_sha256"'):])
(output.parent/'observed.json').write_text(json.dumps({{"args":args,"environment":dict(os.environ)}}))
if {exit_code}: sys.exit({exit_code})
replies=json.loads({json.dumps(json.dumps(replies))})
output.write_text(json.dumps(replies[payload['packet_sha256']]))
print(json.dumps({{"model":"gpt-6.1-sol","usage":{{"output_tokens":12}}}}))
''')
    executable.chmod(0o700)
    return str(executable)


@pytest.mark.parametrize("source_mode", ["frozen", "live"])
def test_real_child_is_isolated_then_reused(tmp_path, monkeypatch, source_mode):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'account'))
    monkeypatch.setenv('REVIEW_SECRET', 'must-not-reach-child')
    _, root, contract = example(tmp_path, source_mode=source_mode)
    account = tmp_path / "account"
    account.mkdir(exist_ok=True)
    (account / "auth.json").write_text("fixture credential")
    backend = stub_backend(root, contract)
    assert review.run(root, workers=1, limit=1, executable=backend) == [('0001', 'completed')]
    status = json.loads((root / 'jobs/0001/status.json').read_bytes())
    observed = json.loads((root / 'jobs/0001/attempt-001/observed.json').read_bytes())
    from pathlib import Path
    isolated = Path(observed['environment']['CODEX_HOME'])
    assert not isolated.exists()
    assert observed['environment']['HOME'] != str(Path.home())
    assert not (root / 'codex-home/auth.json').exists()
    assert '--json' in observed['args']
    assert 'REVIEW_SECRET' not in observed['environment']
    assert 'mcp_servers' not in observed.get('config', '')
    expected_search = "live" if contract["source_mode"] == "live" else "disabled"
    assert f'web_search="{expected_search}"' in observed['args']
    for feature in ('shell_tool', 'unified_exec', 'apps', 'plugins', 'view_image', 'browser_use', 'computer_use', 'multi_agent'):
        i = observed['args'].index(feature)
        assert observed['args'][i - 1] == '--disable'
    assert status['observed_model'] == 'gpt-6.1-sol'
    assert len(status['session_log_sha256']) == 64
    assert review.run(root, workers=1, limit=1, executable='must-not-start') == [('0001', 'reused')]


def test_failed_child_earns_no_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'account'))
    _, root, contract = example(tmp_path)
    backend = stub_backend(root, contract, exit_code=7)
    assert review.run(root, workers=1, limit=1, executable=backend) == [('0001', 'failed')]
    assert review.collect(root)['output_reviewed'] == 0
    with pytest.raises(ValueError, match='incomplete'):
        review.collect(root, require_complete=True)
