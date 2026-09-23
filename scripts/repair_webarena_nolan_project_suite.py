"""Remove obsolete namespace dropdown actions from Nolan GitLab project paths."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite


TASK_IDS = tuple(range(556, 562))
NAMESPACE_SELECTOR = '#blank-project-pane [data-qa-selector="select_namespace_dropdown"] button.dropdown-toggle'


def repair_suite(source_path: Path) -> dict:
    suite = json.loads(source_path.read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in suite["tasks"]}
    for task_id in TASK_IDS:
        task = tasks[f"webarena-{task_id}"]
        actions = task["trajectory"]["actions"]
        if (
            actions[5].get("target", {}).get("value") != NAMESPACE_SELECTOR
            or "ByteBlaze" not in actions[6].get("target", {}).get("value", "")
            or actions[7].get("action") != "fill"
            or actions[7].get("target", {}).get("value") != "#blank-project-pane #project_path"
        ):
            raise ValueError(f"task {task_id} project form no longer matches the repair")
        del actions[5:7]
        expected = task["ground_truth"]["expected_actions"]
        if expected[5:7] != ["click", "click"]:
            raise ValueError(f"task {task_id} expected actions changed")
        del expected[5:7]
        slug_action = actions[5]
        slug = slug_action.get("text")
        name = actions[4].get("text")
        if (
            slug_action.get("action") != "fill"
            or not isinstance(slug, str)
            or not isinstance(name, str)
            or actions[6].get("action") != "click"
        ):
            raise ValueError(f"task {task_id} project slug action changed")
        actions[5:7] = [{
            "action": "evaluate",
            "script": (
                "() => {"
                "const nameInput=document.querySelector('#blank-project-pane #project_name');"
                "const pathInput=document.querySelector('#blank-project-pane #project_path');"
                "if(!nameInput||!pathInput||!nameInput.form||nameInput.form!==pathInput.form)"
                "throw Error('project form missing');"
                f"const name={json.dumps(name)};"
                f"const slug={json.dumps(slug)};"
                "nameInput.value=name;pathInput.value=slug;"
                "const form=nameInput.form,data=new FormData(form);"
                "if(data.get('project[name]')!==name||data.get('project[path]')!==slug"
                "||data.get('project[namespace_id]')!=='2505')"
                "throw Error('project form does not match task');"
                "form.submit();return {name,slug};}"
            ),
            "save_as": "project_submission",
            "network_settle_ms": 1000,
            "accept_navigation_url_regex": rf"/byteblaze/{re.escape(slug)}(?:/|$)",
        }]
        if expected[5:7] != ["fill", "click"]:
            raise ValueError(f"task {task_id} slug action contract changed")
        expected[5:7] = ["evaluate"]
        actions.insert(6, {
            "action": "wait_for_url",
            "url": f"http://gitlab.example.com:8023/byteblaze/{slug}",
            "wait_until": "domcontentloaded",
        })
        expected.insert(6, "wait_for_url")
        task["trajectory"]["provenance"] = {
            "kind": "expert_repair",
            "repair": "submit the preselected ByteBlaze form with the exact project name and slug",
        }
    return suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = repair_suite(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_suite(args.output)
    print(json.dumps({"repaired_task_ids": TASK_IDS}))


if __name__ == "__main__":
    main()
