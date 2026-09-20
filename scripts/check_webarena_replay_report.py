#!/usr/bin/env python3
"""Run deterministic release checks for the WebArena replay report."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import re
from html.parser import HTMLParser
from pathlib import Path

from harness.trajectory_replay.report_languages import language_roots, report_languages
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from harness.trajectory_replay.webarena_report import LOCAL_PATH_RE


def _text_without_verified_embedded_images(text: str, image_digests: set[str]) -> str:
    """Binary bytes already shipped as PNGs are not textual credential fields."""
    def replace(match):
        try:
            payload = base64.b64decode(match.group(1), validate=True)
        except ValueError as exc:
            raise ValueError("invalid embedded article image") from exc
        if (not payload.startswith(b"\x89PNG\r\n\x1a\n")
                or hashlib.sha256(payload).hexdigest() not in image_digests):
            raise ValueError("embedded article image differs from a shipped PNG")
        return 'data-verified-image="embedded-png"'
    return re.sub(r'(?:src|href)="data:image/png;base64,([^"]+)"', replace, text)


def _check_interpretation(root: Path) -> None:
    sections = []
    for language_root in language_roots(root):
        integrated = BeautifulSoup((language_root / "interpretation.html").read_text(), "html.parser")
        offline = BeautifulSoup((language_root / "article.html").read_text(), "html.parser")
        article = integrated.select_one("article.interpretation")
        standalone = offline.select_one("article.interpretation")
        if article is None or standalone is None or article.get_text() != standalone.get_text():
            raise ValueError("integrated and standalone article content differs")
        if offline.select("script, link[rel=stylesheet]"):
            raise ValueError("standalone article must be script-free and self-contained")
        if not article.select("figure img") or not all(
            image.get("src", "").startswith("data:image/png;base64,") for image in standalone.select("img")
        ):
            raise ValueError("article needs illustrations embedded in its offline view")
        if not integrated.select('a[href="article.html"][download]'):
            raise ValueError("article download is missing")
        sections.append([section["id"] for section in article.select("section[id]")])
    if any(section != sections[0] for section in sections[1:]):
        raise ValueError("localized article sections differ")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "pre", "code"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "pre", "code"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _visible_text(html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(html)
    return " ".join(parser.parts)


def _check_legacy_retrieval_audit(
    root: Path, chinese_home: str, english_home: str
) -> None:
    legacy_audit = root / "data/legacy-retrieval-provenance.json"
    if 'class="callout historical-validity"' in chinese_home:
        if not legacy_audit.is_file():
            raise ValueError("historical retrieval provenance audit is missing")
        audit = json.loads(legacy_audit.read_text(encoding="utf-8"))
        rows = audit.get("results")
        if (
            audit.get("schema") != "trajectory_replay_legacy_retrieval_provenance/1"
            or not isinstance(rows, list)
            or audit.get("retrieval_tasks") != 37
            or audit.get("retrieval_results") != 74
            or audit.get("frozen_answer_matches") != 74
            or len(rows) != 74
            or len({row.get("task_id") for row in rows}) != 37
            or len({(row.get("browser"), row.get("task_id")) for row in rows}) != len(rows)
            or any(row.get("source") != "frozen_agent_response_not_current_page_observation"
                   for row in rows)
        ):
            raise ValueError("historical retrieval provenance audit is incomplete")
        if (
            'href="data/legacy-retrieval-provenance.json"' not in chinese_home
            or 'href="../data/legacy-retrieval-provenance.json"' not in english_home
        ):
            raise ValueError("historical retrieval provenance is not linked in both languages")
    elif legacy_audit.exists():
        raise ValueError("non-legacy report contains a legacy retrieval audit")


def _check_fixed36_output_scope(root: Path) -> None:
    path = root / "data/diagnostic-closure.json"
    if not path.is_file():
        return
    fixed = json.loads(path.read_text(encoding="utf-8")).get("fixed36")
    if not fixed:
        return
    passed = fixed["official_rule_pass_tasks"]
    source_derived = fixed["source_derived_official_pass_tasks"]
    actual = passed - source_derived
    if not 0 <= source_derived <= passed <= 36:
        raise ValueError("fixed-36 output provenance counts are invalid")
    chinese = _visible_text((root / "diagnostics.html").read_text(encoding="utf-8"))
    english = _visible_text((root / "en/diagnostics.html").read_text(encoding="utf-8"))
    if (
        f"{actual}/36 条轨迹实际输出通过官方规则" not in chinese
        or f"另有 {source_derived} 条用另行生成的现场数据回答" not in chinese
        or f"{actual}/36 actual trajectory outputs passed the official rule" not in english
        or f"{source_derived} separate source-derived answer" not in english
    ):
        raise ValueError("fixed-36 diagnostics blur actual and source-derived answers")
    task243 = next(
        (row for row in fixed.get("observations", []) if row.get("task_id") == 243), None
    )
    identity = (task243 or {}).get("identity_source_audit") or {}
    if identity:
        vote_sha = identity.get("rating_vote_audit_sha256")
        if not isinstance(vote_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", vote_sha):
            raise ValueError("task 243 rating-vote identity evidence is missing")
        zh_detail = (root / "diagnostic/243.html").read_text(encoding="utf-8")
        en_detail = (root / "en/diagnostic/243.html").read_text(encoding="utf-8")
        if (
            "对应评分投票的客户 ID 也为空" not in zh_detail
            or "the linked rating vote also has a null customer ID" not in en_detail
            or vote_sha not in zh_detail
            or vote_sha not in en_detail
        ):
            raise ValueError("task 243 guest identity source limit is not visible")


def _check_formal36_output_scope(root: Path) -> None:
    path = root / "data/diagnostic-closure.json"
    if not path.is_file():
        return
    formal = json.loads(path.read_text(encoding="utf-8")).get("formal36")
    if not formal:
        return
    rows = formal.get("rows") or []
    if (
        formal.get("status") != "complete"
        or formal.get("pass_count") != 36
        or formal.get("denominator") != 36
        or len(rows) != 36
        or len({row.get("task_id") for row in rows}) != 36
        or not formal.get("reference_unchanged")
        or any(row.get("status") != "pass" or row.get("official_score") != 1.0
               or row.get("completed_actions") != row.get("action_count") for row in rows)
    ):
        raise ValueError("formal original-36 result is incomplete")
    for lang in report_languages(root):
        parent = root if lang == "zh" else root / "en"
        overview = _visible_text((parent / "diagnostics.html").read_text(encoding="utf-8"))
        if "36/36" not in overview or ("90 题" if lang == "zh" else "90-task") not in overview:
            raise ValueError("formal cohort scope is missing from the reader overview")
        if any((row.get("reference_validity") or {}).get("status") == "unverifiable" for row in rows):
            if ("不能宣称 36 题均已真实解决" if lang == "zh" else "not a claim that all 36 tasks were truly solved") not in overview:
                raise ValueError("unverifiable reference is missing from the reader overview")
        for row in rows:
            detail = (parent / "diagnostic" / f'{row["task_id"]}.html').read_text(encoding="utf-8")
            if (
                (("新版 Moli 官方规则通过；题意未证实" if lang == "zh" else "New Moli official-rule pass; task answer unverified")
                 if (row.get("reference_validity") or {}).get("status") == "unverifiable"
                 else ("新版 Moli 受影响题目复测：通过" if lang == "zh" else "New Moli affected-task cohort: passed")) not in detail
                or f'{row["completed_actions"]}/{row["action_count"]}' not in detail
                or row["evidence_sha256"]["evaluation"] not in detail
            ):
                raise ValueError(f'task {row["task_id"]} formal result detail is missing')
    for visual in formal.get("visuals", {}).values():
        asset = root / visual["asset"]
        if not asset.is_file() or hashlib.sha256(asset.read_bytes()).hexdigest() != visual["sha256"]:
            raise ValueError("formal before/after visual is missing or changed")


def _check_english_localization(pages: list[Path]) -> None:
    cjk = re.compile(r"[\u3400-\u9fff]")
    localized_attribute = re.compile(
        r'(?:aria-label|placeholder|title)=["\']([^"\']*)',
        flags=re.I,
    )
    for page in pages:
        text = page.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", text, flags=re.I | re.S)
        if title and cjk.search(title.group(1)):
            raise ValueError(f"English report title is not localized: {page}")
        if any(cjk.search(value) for value in localized_attribute.findall(text)):
            raise ValueError(f"English report accessibility text is not localized: {page}")
        visible = _visible_text(text).replace("中文", "")
        match = cjk.search(visible)
        if match:
            excerpt = visible[max(0, match.start() - 40) : match.start() + 80]
            raise ValueError(
                f"English reader-facing page contains Chinese prose: {page}: {excerpt}"
            )


def _semantic_page_inventory(
    root: Path, core_names: tuple[str, ...]
) -> tuple[list[Path], list[Path], list[Path]]:
    core = [
        *(root / name for name in core_names),
        *(root / "en" / name for name in core_names if "en" in report_languages(root)),
    ]
    chinese_details = [
        *(root / "fix").glob("*.html"),
        *(root / "diagnostic").glob("*.html"),
    ]
    english_details = [
        *(root / "en/fix").glob("*.html"),
        *(root / "en/diagnostic").glob("*.html"),
    ]
    return core, chinese_details, english_details


def _local_target(page: Path, raw: str) -> Path | None:
    parsed = urlsplit(raw)
    if parsed.scheme or raw.startswith(("#", "mailto:", "javascript:")):
        return None
    return (page.parent / unquote(parsed.path)).resolve()


def _check_metric_populations(metrics: dict, results: list[dict], participants: list[str]) -> None:
    """Audit denominators against the result inventory, not renderer claims."""
    if (
        metrics.get("distribution_scope") != "all_declared_tasks"
        or metrics.get("successful_scope") != "per_method_success"
        or metrics.get("passing_intersection_scope") != "diagnostic_only"
    ):
        raise ValueError(
            "metric distributions must preserve all tasks and separate successful subsets"
        )
    methods = metrics.get("methods", [])
    if len(methods) != len(participants) or {row.get("participant_id") for row in methods} != set(
        participants
    ):
        raise ValueError("metric participants differ from the result inventory")
    names = (
        "duration",
        "cpu",
        "memory",
        "memory_peak",
        "network_rx",
        "network_tx",
        "cpu_utilization",
    )
    for method in methods:
        rows = [row for row in results if row["participant_id"] == method["participant_id"]]
        total = len(rows)
        passed = sum(row["status"] == "pass" for row in rows)
        if method.get("total") != total or method.get("passed") != passed:
            raise ValueError("metric outcome counts differ from the result inventory")
        for distributions, population in ((method, total), (method.get("successful", {}), passed)):
            for name in names:
                sample = distributions.get(name, {})
                n, missing = sample.get("n"), sample.get("missing")
                if (
                    type(n) is not int
                    or type(missing) is not int
                    or n < 0
                    or missing < 0
                    or n + missing != population
                    or sample.get("population") != population
                ):
                    raise ValueError(f"metric population or missing count is invalid: {name}")
                for statistic in ("mean", "p50", "p95"):
                    value = sample.get(statistic)
                    if (n == 0 and value is not None) or (
                        n > 0
                        and (
                            type(value) not in (int, float) or not math.isfinite(value) or value < 0
                        )
                    ):
                        raise ValueError(
                            f"missing or nonfinite metric is represented as measured: {name}"
                        )


def _check_affected36_comparison(root: Path, compare: list[dict], results: list[dict], affected_ids: set[int], participants: list[str]) -> None:
    if [item.get("id") for item in compare] != participants:
        raise ValueError("affected-task comparison omits a registered method")
    for item in compare:
        matched = [row for row in results if row["participant_id"] == item["id"] and row["task_id"] in affected_ids]
        if len(matched) != 36 or item.get("total") != 36 or item.get("passed") != sum(row["status"] == "pass" for row in matched):
            raise ValueError("affected-task comparison uses a wrong denominator or score")
    maximum = max(item["passed"] for item in compare)
    winners = [item["id"] for item in compare if item["passed"] == maximum]
    unique_winner = winners[0] if len(winners) == 1 else None
    for language_root in language_roots(root):
        home = (language_root / "index.html").read_text(encoding="utf-8")
        if ("36 题" if language_root == root else "36 failures") not in home:
            raise ValueError("affected-task method comparison is absent from the home page")
        for item in compare:
            match = re.search(
                rf'<tr><td><a href="results\.html\?participant={re.escape(item["id"])}&status=">{re.escape(item["label"])}</a></td><td data-metric="affected36-pass"[^>]*>(.*?)</td>',
                home,
                re.S,
            )
            if not match or not match.group(1).startswith(f'{item["passed"]}/36'):
                raise ValueError("affected-task score or evidence link is missing from the home page")
            marked = 'class="winner"' in match.group(1)
            if marked != (item["id"] == unique_winner):
                raise ValueError("affected-task highlight is missing or assigned to a tie")


def _check_core_comparison(root: Path, metrics: dict, results: list[dict], participants: list[str]) -> None:
    methods = metrics["methods"]
    if [row["participant_id"] for row in methods] != participants:
        raise ValueError("core comparison does not follow the method registry")
    scores = {
        participant: sum(row["status"] == "pass" for row in results if row["participant_id"] == participant)
        for participant in participants
    }
    maximum = max(scores.values())
    leaders = [participant for participant in participants if scores[participant] == maximum]
    winner = leaders[0] if len(leaders) == 1 else None
    denominator = len(results) // len(participants)
    validity_by_task: dict[int, str] = {}
    for result in results:
        validity = result.get("semantic_status")
        if validity not in {"valid", "unverified"}:
            raise ValueError("every result must declare reference validity")
        task_id = int(result["task_id"])
        if task_id in validity_by_task and validity_by_task[task_id] != validity:
            raise ValueError("reference validity differs across methods on one task")
        validity_by_task[task_id] = validity
    valid_total = sum(value == "valid" for value in validity_by_task.values())
    valid_scores = {
        participant: sum(
            row["status"] == "pass" and row["semantic_status"] == "valid"
            for row in results if row["participant_id"] == participant
        ) for participant in participants
    }
    valid_maximum = max(valid_scores.values())
    valid_leaders = [participant for participant in participants if valid_scores[participant] == valid_maximum]
    valid_winner = valid_leaders[0] if len(valid_leaders) == 1 else None
    show_valid_reference = valid_total != denominator
    for language_root in language_roots(root):
        home = (language_root / "index.html").read_text(encoding="utf-8")
        if (home.count('data-metric="core-pass"') != len(participants)
                or home.count('data-metric="verifiable-pass"')
                != (len(participants) if show_valid_reference else 0)):
            raise ValueError("home must have one core score per method")
        for row in methods:
            participant = row["participant_id"]
            match = re.search(
                rf'<tr><td>{re.escape(html.escape(row["label"]))}</td><td data-metric="core-pass" data-metric-direction="higher" data-sort="([0-9]+)">(.*?)</td>',
                home, re.S,
            )
            if not match or int(match.group(1)) != scores[participant] or f'{scores[participant]}/{denominator}' not in match.group(2):
                raise ValueError("core score differs from measured results")
            marked = 'class="winner"' in match.group(2)
            if marked != (participant == winner):
                raise ValueError("core score highlight disagrees with unique winner or tie")
            if (
                row.get("valid_reference_total") != valid_total
                or row.get("valid_reference_passed") != valid_scores[participant]
            ):
                raise ValueError("verifiable-task score differs from measured results")
            if not show_valid_reference:
                continue
            valid_match = re.search(
                rf'<tr><td>{re.escape(html.escape(row["label"]))}</td>(?:(?!</tr>).)*?<td data-metric="verifiable-pass" data-metric-direction="higher" data-sort="([0-9]+)">(.*?)</td>',
                home, re.S,
            )
            if (
                not valid_match
                or int(valid_match.group(1)) != valid_scores[participant]
                or f'{valid_scores[participant]}/{valid_total}' not in valid_match.group(2)
            ):
                raise ValueError("verifiable-task score differs from measured results")
            if ('class="winner"' in valid_match.group(2)) != (participant == valid_winner):
                raise ValueError("verifiable-task highlight disagrees with unique winner or tie")


def _check_result_detail_semantics(text: str, evidence: dict, status: str) -> None:
    if text.count('class="verdict-line ') != 1 or text.count('id="evidence"') != 1:
        raise ValueError("result detail duplicates or omits its summary/evidence section")
    if status == "pass":
        if 'id="diagnosis"' in text:
            raise ValueError("successful result duplicates a failure summary")
        return
    failure = evidence.get("programmatic_failure") or {}
    if (
        text.count('id="diagnosis"') != 1
        or not str(failure.get("message") or "").strip()
        or html.escape(str(failure["message"]), quote=True) not in text
        or (failure.get("stage") in {"official_evaluation", "side_effect_not_observed"}
            and not evidence.get("official_evaluation"))
        or (failure.get("stage") == "browser_tool_execution"
            and not evidence.get("trajectory"))
    ):
        raise ValueError("failure detail lacks exact error or evaluation logs")


def _check_failure_matrix(page: str, results: list[dict], participants: list[str]) -> None:
    """Recompute cells independently; also verify each count's filter target."""
    from collections import Counter
    from urllib.parse import parse_qs

    soup = BeautifulSoup(page, "html.parser")
    matrix = soup.select_one("[data-failure-matrix]")
    if matrix is None:
        raise ValueError("failure category matrix is missing")
    method_rows = matrix.select("tbody tr[data-method]")
    if [row["data-method"] for row in method_rows] != participants:
        raise ValueError("failure matrix participant inventory differs")
    counts = Counter((row["participant_id"], row.get("cause_code") or "unreviewed")
                     for row in results if row["status"] != "pass")
    categories = {code for _, code in counts}
    headers = matrix.select("thead [data-category]")
    if len(headers) != len(categories) or {cell["data-category"] for cell in headers} != categories:
        raise ValueError("failure matrix category inventory differs")
    for row in method_rows:
        method = row["data-method"]
        population = [item for item in results if item["participant_id"] == method]
        failed = sum(item["status"] != "pass" for item in population)
        total = row.select_one("[data-failure-total]")
        if total is None or total.text != f"{failed} / {len(population)}" or int(total["data-failure-total"]) != failed:
            raise ValueError("failure matrix denominator differs")
        cells = row.select("td[data-category]")
        if len(cells) != len(categories) or {cell["data-category"] for cell in cells} != categories:
            raise ValueError("failure matrix cells are missing or duplicated")
        for cell in cells:
            code = cell["data-category"]
            count = counts[method, code]
            if cell.get("data-count") != str(count) or cell.get_text(strip=True) != str(count):
                raise ValueError("failure matrix count differs from evidence")
            if not count or code == "unreviewed":
                continue
            link = cell.select_one("a[href]")
            url = urlsplit(link["href"]) if link else None
            source = next(item for item in population if item.get("cause_code") == code)
            expected = {"participant": [method], "status": ["fail"], "cause": [source["cause_zh"]]}
            if url is None or url.path != "results.html" or url.fragment != "result-inventory" or parse_qs(url.query) != expected:
                raise ValueError("failure matrix drilldown target differs")
            if soup.select_one('#result-inventory') is None:
                raise ValueError("failure matrix inventory anchor is missing")


def check(report_dir: Path) -> dict[str, int]:
    root = report_dir.resolve()
    manifest = json.loads((root / "report-manifest.json").read_text(encoding="utf-8"))
    if report_languages(root) == ("zh",):
        english_files = {path.relative_to(root / "en").as_posix() for path in (root / "en").rglob("*") if path.is_file()}
        if english_files != {"index.html"} or manifest.get("english_status") != "not_generated":
            raise ValueError("Chinese-only report contains stale English output")
        notice = (root / "en/index.html").read_text(encoding="utf-8")
        if 'data-english-not-generated' not in notice or '英文报告未生成' not in notice or 'href="../index.html"' not in notice:
            raise ValueError("English not-generated notice or return link is missing")
        if list((root / "assets/interpretation").glob("*-en.png")):
            raise ValueError("Chinese-only report contains unused English illustrations")
    results = json.loads((root / "data/results.json").read_text(encoding="utf-8"))
    required_manifest_fields = (
        "task_count",
        "participant_count",
        "participants",
        "logical_results",
        "failed_results",
        "reviewed_failures",
    )
    missing_manifest_fields = [field for field in required_manifest_fields if field not in manifest]
    if missing_manifest_fields:
        raise ValueError(
            "report manifest is missing required fields: " + ", ".join(missing_manifest_fields)
        )
    participants = manifest.get("participants") or []
    task_count = int(manifest.get("task_count") or 0)
    participant_count = int(manifest.get("participant_count") or 0)
    expected_results = task_count * participant_count
    if (
        task_count < 1
        or participant_count < 1
        or len(participants) != participant_count
        or len(set(participants)) != participant_count
        or participant_count != 3
        or "historical_participants" in manifest
        or "affected_task_participants" in manifest
        or manifest["logical_results"] != expected_results
        or len(results) != expected_results
    ):
        raise ValueError(
            "report must contain the complete manifest-declared participant-task matrix"
        )
    task_ids = {int(item["task_id"]) for item in results}
    result_keys = {(str(item["participant_id"]), int(item["task_id"])) for item in results}
    expected_keys = {(participant, task_id) for participant in participants for task_id in task_ids}
    if len(task_ids) != task_count or result_keys != expected_keys or len(result_keys) != len(results):
        raise ValueError(
            "report results do not match the manifest-declared participant-task matrix"
        )
    for task_id in task_ids:
        contract_hashes = {
            row.get("task_contract_sha256")
            for row in results if row["task_id"] == task_id
        }
        if len(contract_hashes) != 1 or not re.fullmatch(r"[0-9a-f]{64}", str(next(iter(contract_hashes)))):
            raise ValueError(f"task {task_id} mixes result contract versions")
    metrics = json.loads((root / "data/metrics.json").read_text(encoding="utf-8"))
    _check_metric_populations(metrics, results, participants)
    _check_core_comparison(root, metrics, results, participants)
    for language_root in language_roots(root):
        _check_failure_matrix((language_root / "results.html").read_text(encoding="utf-8"), results, participants)
    if manifest.get("formal_reference_validity") is True:
        if task_count != 90 or manifest.get("site_reset_between_tasks") is not False:
            raise ValueError("formal report requires the complete method-reset 90-task cohort")
        for language_root in language_roots(root):
            environment = (language_root / "environment.html").read_text(encoding="utf-8")
            if "Rainbond" not in environment or "Colima" in environment:
                raise ValueError("formal environment page does not describe the measured deployment")
        for participant in participants:
            for task_id in task_ids:
                evidence_path = root / "data/evidence" / f"{participant}-{task_id}.json"
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                if not isinstance(evidence.get("site_reset"), dict) or evidence.get("readiness") is None:
                    raise ValueError(f"formal result lacks method reset or task readiness evidence: {participant}-{task_id}")
    if metrics.get("affected36_methods"):
        raise ValueError("report must not contain a diagnostic-only comparison")
    if any((root / route).exists() for route in ("diagnostics.html", "fixes.html", "data/diagnostic-closure.json")):
        raise ValueError("three-method report contains a separate diagnostic appendix")
    failures = [item for item in results if item["status"] != "pass"]
    if manifest["failed_results"] != len(failures) or manifest["reviewed_failures"] != len(
        failures
    ):
        raise ValueError("report must contain a review for every final failure")
    if any(not item[f"reason_{lang}"] for item in failures for lang in report_languages(root)):
        raise ValueError("every failed result needs a reviewed cause in each generated language")

    zh_details = {path.name for path in (root / "result").glob("*.html")}
    en_details = {path.name for path in (root / "en/result").glob("*.html")}
    if ("en" in report_languages(root) and zh_details != en_details) or len(zh_details) != expected_results:
        raise ValueError("Chinese and English result routes must match one-to-one")
    zh_tasks = {path.name for path in (root / "task").glob("*.html")}
    en_tasks = {path.name for path in (root / "en/task").glob("*.html")}
    if ("en" in report_languages(root) and zh_tasks != en_tasks) or zh_tasks != {f"{task_id}.html" for task_id in task_ids}:
        raise ValueError("every task needs matching Chinese and English contract routes")
    for task_id in task_ids:
        frozen_path = root / "data/trajectory" / f"{task_id}.json"
        if not frozen_path.is_file():
            raise ValueError(f"qualified trajectory is missing for task {task_id}")
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        action_count = len(frozen.get("tool_calls") or [])
        if frozen.get("task_id") != task_id or action_count < 1:
            raise ValueError(f"qualified trajectory is invalid for task {task_id}")
        for participant in participants:
            key = f"{participant}-{task_id}"
            planned_path = root / "data/trajectory" / f"{key}.json"
            if not planned_path.is_file():
                raise ValueError(f"method-specific trajectory is missing: {key}")
            planned = json.loads(planned_path.read_text(encoding="utf-8"))
            planned_count = len(planned.get("tool_calls") or [])
            planned_digest = hashlib.sha256(
                json.dumps(planned.get("tool_calls") or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            matching = next(item for item in results if item["participant_id"] == participant and item["task_id"] == task_id)
            if (
                planned.get("participant_id") != participant
                or planned.get("task_id") != task_id
                or planned_count != matching["action_count"]
                or planned.get("planned_actions_sha256") != planned_digest
            ):
                raise ValueError(f"method-specific trajectory binding is invalid: {key}")
            evidence_path = root / "data/evidence" / f"{key}.json"
            if not evidence_path.is_file():
                raise ValueError(f"method-specific result evidence is missing: {key}")
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            if (
                (evidence.get("result") or {}).get("planned_actions_sha256") != planned_digest
                or (evidence.get("result") or {}).get("task_contract_sha256") != matching.get("task_contract_sha256")
                or (evidence.get("result") or {}).get("status") != matching["status"]
                or (evidence.get("reference_validity") or {}).get("status")
                != ("valid" if matching["semantic_status"] == "valid" else "unverifiable")
            ):
                raise ValueError(f"method-specific result evidence changed: {key}")
            for language_root in language_roots(root):
                page = language_root / "result" / f"{key}.html"
                text = page.read_text(encoding="utf-8")
                relative_root = "../" if language_root == root else "../../"
                if (
                    text.count('class="aligned-step ') != planned_count
                    or f'data-standard-url="{relative_root}data/trajectory/{key}.json"'
                    not in text
                    or f'data-evidence-url="{relative_root}data/evidence/{key}.json"' not in text
                    or 'id="step-dialog"' not in text
                ):
                    raise ValueError(f"result detail lacks aligned step evidence: {page}")
                _check_result_detail_semantics(text, evidence, matching["status"])
    core_names = (
        "index.html",
        "dataset.html",
        "results.html",
        "environment.html",
        "review.html",
        "evidence-viewer.html",
    )
    if manifest.get("interpretation"):
        _check_interpretation(root)
        core_names += ("interpretation.html", "article.html")
    core, chinese_details, english_details = _semantic_page_inventory(root, core_names)
    forbidden = (
        "用户提示词",
        "我的思考",
        "assistant reasoning",
        "内部工作日志",
        "中位数",
        "median",
        "后续 179 题",
    )
    for page in [*core, *chinese_details, *english_details]:
        text = page.read_text(encoding="utf-8")
        if any(term in text for term in forbidden):
            raise ValueError(f"formal report contains internal or retired prose: {page}")
        if "<svg" in text.lower():
            raise ValueError(f"report source contains a hand-authored SVG: {page}")

    untranslated_zh_markers = (
        "tasks · model calls",
        'aria-label="Close"',
        ">Browser Trajectory Replay</title>",
        ">Dataset</title>",
        ">Results</title>",
        ">Environment</title>",
        ">Review history</title>",
        ">Evidence viewer</title>",
        ">Action</th>",
        ">Target</th>",
        ">Time</th>",
        ">Status</th>",
        ">Result</th>",
        " calls</small>",
    )
    for page in [*core[: len(core_names)], *chinese_details, *(root / "result").glob("*.html")]:
        text = page.read_text(encoding="utf-8")
        if any(marker in text for marker in untranslated_zh_markers):
            raise ValueError(f"Chinese report contains untranslated interface text: {page}")
    _check_english_localization(
        [
            *core[len(core_names) :],
            *english_details,
            *(root / "en/result").glob("*.html"),
            *(root / "en/task").glob("*.html"),
        ]
    )

    checked_links = 0
    for page in root.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        for raw in re.findall(r'(?:href|src)=["\']([^"\']+)', text):
            target = _local_target(page, raw)
            if target is None:
                continue
            checked_links += 1
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file() or (target != root and root not in target.parents):
                raise ValueError(f"broken or escaping local link in {page}: {raw}")

    _check_legacy_retrieval_audit(
        root,
        (root / "index.html").read_text(encoding="utf-8"),
        (root / "en/index.html").read_text(encoding="utf-8"),
    )
    _check_fixed36_output_scope(root)
    _check_formal36_output_scope(root)

    image_digests = {hashlib.sha256(path.read_bytes()).hexdigest() for path in (root / "assets").rglob("*.png")}
    distributable = "\n".join(
        _text_without_verified_embedded_images(path.read_text(encoding="utf-8", errors="ignore"), image_digests)
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".html", ".json", ".js", ".css"}
    )
    if LOCAL_PATH_RE.search(distributable) or re.search(r"sk-[A-Za-z0-9_-]{12,}", distributable):
        raise ValueError("report contains a local path or credential-like token")
    css = (root / "assets/report.css").read_text(encoding="utf-8")
    if ".inventory{height:" not in css or "overflow:auto" not in css:
        raise ValueError("result inventory must be fixed-height and horizontally scrollable")
    results_html = (root / "results.html").read_text(encoding="utf-8")
    if '<option value="fail">' not in results_html or 'id="diagnosis-dialog"' not in results_html:
        raise ValueError("result inventory must default to failure and open a diagnosis dialog")
    for language_root in language_roots(root):
        dataset_html = (language_root / "dataset.html").read_text(encoding="utf-8")
        filters = set(re.findall(r'data-dataset-filter="([^\"]+)"', dataset_html))
        if filters != {"q", "site", "type", "source"}:
            raise ValueError("dataset must support task, site, type and source filters")
        if dataset_html.count('data-task="') != task_count:
            raise ValueError("dataset must contain one filterable row per task")
        for task_id in task_ids:
            if (
                f'href="task/{task_id}.html"' not in dataset_html
                or f'href="results.html?task={task_id}&status="' not in dataset_html
            ):
                raise ValueError(
                    "every dataset task must drill down to its contract and measured outcomes"
                )
        if "data-dataset-count" not in dataset_html or "data-dataset-empty" not in dataset_html:
            raise ValueError("dataset filters must show result count and empty state")
        review_html = (language_root / "review.html").read_text(encoding="utf-8")
        if (
            'class="table-scroll review-table"' not in review_html
            or review_html.count("<tbody><tr>") != 1
        ):
            raise ValueError(
                "review history must describe review rounds, not enumerate per-result audits"
            )
    js = (root / "assets/app.js").read_text(encoding="utf-8")
    if "const task=params.get('task');" not in js or "row.dataset.task===task" not in js:
        raise ValueError("dataset-to-results drilldown must select an exact task ID")
    if (
        "const stepDialog=document.querySelector('#step-dialog');" not in js
        or "Promise.all([standard(),evidence()])" not in js
    ):
        raise ValueError("step dialog must load qualified and actual evidence on demand")
    for page in (parent / "results.html" for parent in language_roots(root)):
        rendered = page.read_text(encoding="utf-8")
        baseline = re.search(r"<select data-matrix-baseline>(.*?)</select>", rendered, re.S)
        if baseline is None:
            raise ValueError("same-task comparison needs a baseline browser selector")
        baseline_ids = re.findall(r'<option value="([^"]+)">', baseline.group(1))
        comparison_ids = re.findall(r'data-matrix-method value="([^"]+)"', rendered)
        if baseline_ids != participants or comparison_ids != participants:
            raise ValueError(
                "same-task comparison controls must come from the complete participant registry"
            )
        if (
            rendered.count('data-pass="') != task_count
            or rendered.count('data-fail="') != task_count
        ):
            raise ValueError("same-task comparison needs one grouped outcome row per task")
    return {"html_pages": len(list(root.rglob("*.html"))), "checked_links": checked_links}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.report_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
