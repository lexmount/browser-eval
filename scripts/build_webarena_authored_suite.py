"""Build original Chromium paths for WebArena cases with no transferable recording."""

from __future__ import annotations

import argparse
import importlib.resources
import json
from pathlib import Path
from urllib.parse import quote

from harness.trajectory_replay.public_webarena.suite import load_suite

MAP = "http://openstreetmap.example.com:3000"
SHOP = "http://onestopmarket.example.com:7770"
GITLAB = "http://gitlab.example.com:8023"
WIKI = "http://wikipedia.example.com:8888"
SOURCE = "https://github.com/lexmount/webarena-verified/blob/7d060ec92b611d66b48d667acb9d92c35cfbdbda/assets/dataset/webarena-verified.json"


def navigate(url, save_as="page"):
    return {"action": "navigate", "url": url, "save_as": save_as}


def click(selector=None, *, target=None):
    result = {"action": "click"}
    result["selector" if selector else "target"] = selector or target
    return result


def fill(selector, text):
    return {"action": "fill", "selector": selector, "text": text}


def role(kind: str, label: str, *, exact: bool = True) -> dict:
    return {"by": "role", "role": kind, "name": label, "exact": exact}


def response_builder(task_id):
    if task_id == 789:
        return {
            "kind": "observed_not_found",
            "task_type": "retrieve",
            "output": "search_result",
            "evidence_regex": r"couldn't find any projects matching huggingface dataset",
        }
    task_type = "navigate" if task_id in {356, 757, 758, 763, 764, 765, 766, 767} else "mutate"
    return {"kind": "null_after_verified_path", "task_type": task_type}


def route_actions(start, end, *, foot=False):
    return [
        navigate(MAP + "/directions", "directions_form"),
        fill("#route_from:visible", start),
        fill("#route_to:visible", end),
        {
            "action": "select",
            "selector": "select[name=routing_engines]:visible",
            "value": "0" if foot else "1",
        },
        click("form:has(#route_from:visible) input[name=commit]:visible"),
        {
            "action": "get_text",
            "target": {"by": "text", "value": "Directions courtesy of OSRM", "exact": False},
            "save_as": "route_confirmation",
        },
    ]


ROUTES = {
    356: (
        "Gates and Hillman Centers, Carnegie Mellon University, Pittsburgh",
        "Independence Hall, Philadelphia",
        False,
    ),
    757: ("Three Rivers Stadium, Pittsburgh", "Giants Stadium, East Rutherford", False),
    758: ("New York City, New York", "Portland, Maine", False),
    763: ("401 Shady Avenue, Pittsburgh", "Trader Joe's, Pittsburgh", True),
    764: ("401 Shady Avenue, Pittsburgh", "Target, Pittsburgh", True),
    765: ("401 Shady Avenue, Pittsburgh", "Tokyo Japanese Food Store, Pittsburgh", True),
    766: ("401 Shady Avenue, Pittsburgh", "Whole Foods Market, Pittsburgh", True),
    767: ("401 Shady Avenue, Pittsburgh", "Giant Eagle, Pittsburgh", True),
}

SELECTED_CART_INDEX = {431: 1, 432: 0, 433: 0, 434: 1, 435: 1}


def cart_actions(row):
    task_id = row["task_id"]
    urls = [url.replace("__SHOPPING__", SHOP) for url in row["start_urls"]]
    actions = []
    for index, url in enumerate(urls):
        actions += [
            navigate(url, f"candidate_{index}_page"),
            {"action": "get_text", "selector": "h1:visible", "save_as": f"candidate_{index}_title"},
            {
                "action": "get_text",
                "selector": ".price:visible",
                "save_as": f"candidate_{index}_price",
            },
        ]
    actions += [navigate(urls[SELECTED_CART_INDEX[task_id]], "selected_product")]
    actions += [
        click(target={"by": "role", "role": "button", "name": "Add to Cart", "exact": True})
    ]
    return actions


ISSUE_REPOS = {
    661: "convexegg/chatgpt",
    662: "0ang3el/aem-hacker",
    663: "root/metaseq",
    664: "vinta/awesome-python",
    665: "byteblaze/a11y-syntax-highlighting",
}


def issue_actions(row):
    task_id = row["task_id"]
    if task_id == 789:
        return [
            navigate(GITLAB + "/search?search=" + quote("huggingface dataset"), "project_search"),
            {"action": "get_text", "selector": "main:visible", "save_as": "search_result"},
        ]
    values = row["instantiation_dict"]
    return [
        navigate(GITLAB + "/" + ISSUE_REPOS[task_id] + "/-/issues/new", "new_issue"),
        fill("#issue_title", values["title"]),
        fill("#issue_description", values["issue"]),
        click(target={"by": "role", "role": "button", "name": "Create issue", "exact": True}),
    ]


FORK_REPOS = {
    394: "yjlou/2019-nCov",
    395: "eriklindernoren/PyTorch-GAN",
    396: "convexegg/chatgpt",
    397: "root/metaseq",
}

MULTI_FORK_REPOS = {
    398: [
        "aklsh/CacheEval",
        "aklsh/nvidia-patch",
        "aklsh/SimCache",
        "aklsh/viewgrades-scraper",
        "aklsh/dots",
    ],
    522: ["facebook/create-react-app", "facebook/buck"],
}

NOLAN_READMES = {
    556: [
        "Following",
        "Memento",
        "Insomnia",
        "Batman Begins",
        "The Prestige",
        "The Dark Knight",
        "Inception",
        "The Dark Knight Rises",
        "Interstellar",
        "Dunkirk",
        "Tenet",
        "Oppenheimer",
    ],
    557: ["Following", "Memento", "Insomnia", "Batman Begins", "The Prestige", "The Dark Knight"],
    558: ["The Dark Knight Rises", "Interstellar", "Dunkirk", "Tenet", "Oppenheimer"],
    559: [
        "1993–2003: Early career and breakthrough",
        "2003–2013: Widespread recognition",
        "2014–2019: Established Hollywood auteur",
        "2020–present",
    ],
    560: ["The Dark Knight", "Inception", "Interstellar", "Dunkirk", "Tenet"],
    561: [
        "Batman Begins",
        "The Dark Knight",
        "Inception",
        "The Dark Knight Rises",
        "Interstellar",
        "Dunkirk",
        "Tenet",
    ],
}

GROUP_IDS = {799, 800, 801, 802, 803}


def group_actions(row):
    values = row["instantiation_dict"]
    name = values["name"]
    members = list(dict.fromkeys(part.strip() for part in values["members"].split(",")))
    actions = [
        navigate(GITLAB + "/groups/new", "new_group_start"),
        click(target=role("link", "Create group Assemble related projects", exact=False)),
        {"action": "fill", "target": role("textbox", "Group name"), "text": name},
        {"action": "fill", "target": role("textbox", "Group URL"), "text": name},
        click(target=role("button", "Create group")),
        navigate(GITLAB + f"/groups/{name}/-/group_members", "new_group_members"),
        click(target=role("button", "Invite members")),
    ]
    for username in members:
        actions += [
            {
                "action": "fill",
                "target": role("textbox", "Username or email address"),
                "text": username,
            },
            click(target={"by": "role", "role": "menuitem", "name": username, "exact": False}),
        ]
    actions += [click(target=role("button", "Invite"))]
    return actions


def nolan_actions(row):
    name = row["instantiation_dict"]["name"]
    task_id = row["task_id"]
    content = "# " + name + "\n\n" + "\n".join("- " + title for title in NOLAN_READMES[task_id])
    article = "Christopher_Nolan"
    if task_id in {560, 561}:
        article = "List_of_awards_and_nominations_received_by_Christopher_Nolan"
    wiki_actions = [
        navigate(
            WIKI + "/content/wikipedia_en_all_maxi_2022-05/A/" + article,
            "wiki_article",
        )
    ]
    if task_id == 559:
        wiki_actions.append(
            {
                "action": "get_all_text",
                "selector": "details:has(#Career) h3",
                "save_as": "career_headings",
            }
        )
    elif task_id in {560, 561}:
        wiki_actions.append(
            {
                "action": "get_text",
                "selector": "details:has(#Awards_and_nominations)",
                "save_as": "awards_source",
            }
        )
    else:
        wiki_actions.append(
            {
                "action": "get_text",
                "selector": "details:has(#Filmography)",
                "save_as": "filmography_source",
            }
        )
    return wiki_actions + [
        navigate(GITLAB + "/projects/new", "new_project_start"),
        click(target=role("link", "Create blank project Create a blank project", exact=False)),
        {
            "action": "fill",
            "target": {"by": "label", "value": "Project name", "exact": False},
            "text": name,
        },
        {
            "action": "uncheck",
            "target": {
                "by": "label",
                "value": "Initialize repository with a README",
                "exact": False,
            },
        },
        click(target=role("button", "Create project")),
        navigate(GITLAB + f"/-/ide/project/byteblaze/{name}/edit/main/-/", "web_ide"),
        click(target=role("button", "New file")),
        {
            "action": "fill",
            "target": {"by": "role", "role": "textbox", "name": "dir/file_name", "exact": True},
            "text": "README.md",
        },
        click(target=role("button", "Create file")),
        {
            "action": "fill",
            "target": {
                "by": "label",
                "value": "Editor content;Press Alt+F1 for Accessibility Options.",
                "exact": False,
            },
            "text": content,
        },
        click(target=role("button", "Create commit...")),
        {"action": "check", "target": role("radio", "Commit to main branch")},
        click(target=role("button", "Commit")),
    ]


LICENSE_REPOS = {
    411: [("cloud-to-butt", "master", "LICENSE.txt", "MIT License", "edit")],
    412: [("accessible-html-content-patterns", "main", "LICENSE", "Apache License 2.0", "edit")],
    413: [
        ("a11y-syntax-highlighting", "main", "LICENSE", "GNU General Public License v3.0", "edit")
    ],
    414: [("dotfiles", "main", "LICENSE", "MIT License", "new")],
    736: [
        ("gimmiethat.space", "main", "LICENSE", "MIT License", "edit"),
        ("dotfiles", "main", "LICENSE", "MIT License", "edit"),
    ],
}


def license_actions(task_id):
    actions = []
    for repo, branch, filename, template, mode in LICENSE_REPOS[task_id]:
        path = (
            f"/byteblaze/{repo}/-/edit/{branch}/{filename}"
            if mode == "edit"
            else f"/byteblaze/{repo}/-/new/{branch}?commit_message=Add+LICENSE&file_name=LICENSE"
        )
        actions += [
            navigate(GITLAB + path, "license_editor"),
            click(
                target={"by": "role", "role": "button", "name": "Apply a template", "exact": True}
            ),
            click(target={"by": "role", "role": "link", "name": template, "exact": True}),
            click(target={"by": "role", "role": "button", "name": "Commit changes", "exact": True}),
        ]
    return actions


def fork_actions(repo):
    return [
        navigate(GITLAB + "/" + repo, "source_project"),
        click(target={"by": "role", "role": "link", "name": "Fork", "exact": True}),
        click(
            target={"by": "role", "role": "button", "name": "Select a namespace", "exact": False}
        ),
        {
            "action": "fill",
            "target": {"by": "test_id", "value": "select_namespace_dropdown_search_field"},
            "text": "byteblaze",
        },
        click(target={"by": "text", "value": "byteblaze", "exact": True}),
        click(target={"by": "role", "role": "button", "name": "Fork project", "exact": True}),
    ]


def task_definition(row, actions):
    task_id = row["task_id"]
    return {
        "id": f"webarena-{task_id}",
        "title": row["intent"],
        "trajectory": {
            "schema": "lex_browser_trajectory/1",
            "actions": actions,
            "provenance": {"kind": "browser_observed_fixed_path"},
        },
        "difficulty": "medium",
        "ground_truth": {
            "oracle": "webarena_verified",
            "expected_actions": [action["action"] for action in actions],
            "response_builder": response_builder(task_id),
        },
        "evaluator": {"kind": "webarena_verified", "task_id": task_id, "revision": row["revision"]},
        "source": {"url": SOURCE},
        "environment": {"kind": "webarena_verified"},
    }


def build_authored_suite(official_path: Path) -> dict:
    official = json.loads(official_path.read_text(encoding="utf-8"))
    tasks = []
    for row in official:
        task_id = row["task_id"]
        if task_id in ROUTES:
            actions = route_actions(*ROUTES[task_id][:2], foot=ROUTES[task_id][2])
        elif task_id in SELECTED_CART_INDEX:
            actions = cart_actions(row)
        elif task_id in ISSUE_REPOS or task_id == 789:
            actions = issue_actions(row)
        elif task_id in FORK_REPOS:
            actions = fork_actions(FORK_REPOS[task_id])
        elif task_id in MULTI_FORK_REPOS:
            actions = [
                action for repo in MULTI_FORK_REPOS[task_id] for action in fork_actions(repo)
            ]
        elif task_id in LICENSE_REPOS:
            actions = license_actions(task_id)
        elif task_id in NOLAN_READMES:
            actions = nolan_actions(row)
        elif task_id in GROUP_IDS:
            actions = group_actions(row)
        else:
            continue
        tasks.append(task_definition(row, actions))
    ids = [int(task["id"].removeprefix("webarena-")) for task in tasks]
    if ids != sorted(ids) or len(ids) != len(set(ids)) or len(ids) != 41:
        raise ValueError("authored task coverage differs from the 41 uncovered cases")
    return {"schema": "lex_browser_use_tasks/4", "tasks": tasks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    official_path = args.official_dataset or Path(
        str(
            importlib.resources.files("webarena_verified").joinpath(
                "assets/dataset/webarena-verified.json"
            )
        )
    )
    result = build_authored_suite(official_path)
    if args.output.exists():
        raise ValueError("authored suite output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    suite = load_suite(args.output)
    print(json.dumps({"authored_tasks": len(suite.tasks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
