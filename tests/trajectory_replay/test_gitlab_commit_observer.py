"""Exercise the actual frozen observers against GitLab-shaped responses."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("task_id", [132, 303])
@pytest.mark.parametrize("duplicate", [False, True])
def test_commit_observer_filters_locally_without_optional_total(task_id, duplicate):
    node = shutil.which("node")
    assert node, "Node is required to execute observer regression controls"
    path = Path(
        f"evaluation/trajectory-replay/webarena-verified/retrieval-candidates/task-{task_id}.json"
    )
    script = json.loads(path.read_text())["tool_calls"][-1]["script"]
    commits = [
        {"id": "a" * 40, "author_name": "Kilian Valkhof", "committed_date": "2023-03-05T19:47:04Z"},
        {
            "id": ("a" if duplicate else "b") * 40,
            "author_name": "Eric Bailey",
            "committed_date": "2023-03-05T20:00:00Z",
        },
    ]
    harness = """
global.location={origin:'http://gitlab.test'};
global.fetch=async address=>{
 const url=new URL(address);
 const isCommits=url.pathname.endsWith('/commits');
 if(isCommits && url.searchParams.has('author')) throw Error('unsupported author filter');
 return {status:200,redirected:false,
  headers:{get:key=>({'content-type':'application/json','x-page':'1','x-next-page':''}[key]??null)},
  json:async()=>isCommits?COMMITS:{id:174,path_with_namespace:'a11yproject/a11yproject.com',default_branch:'main'}};
};
Promise.resolve(OBSERVER).then(value=>console.log(JSON.stringify(value))).catch(error=>{console.error(error.message);process.exitCode=1});
""".replace("COMMITS", json.dumps(commits)).replace("OBSERVER", script)
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=10)
    if duplicate:
        assert result.returncode != 0
        assert "commit identity" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value["answer"] == 1
        assert len(value["observed_commits"]) == 2
        assert value["complete"] is True
