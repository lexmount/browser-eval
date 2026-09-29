"""Disjoint, evidence-derived failure counts for the replay result inventory."""

from collections import Counter
from html import escape
from urllib.parse import urlencode


# These are diagnostic families, not assertions that the browser owns the fault.
FAMILIES = (
    ("geometry", "布局与可见性", "Layout and visibility", (
        "zero_area_target_geometry", "zero_width_forum_link",
        "zero_width_candidate_changes_visible_ordinal",
        "visible_locator_timeout_without_geometry",
        "suggestion_dom_selection",
        "configuration_wizard_readiness_predicate",
    )),
    ("interaction", "输入与动作效果", "Input and action effects", (
        "input_navigation_lifecycle", "disabled_control_readiness_race",
        "implicit_search_submission_absent",
    )),
    ("network", "网络传输与记录", "Network transport and recording", (
        "fetch_transport_timeout", "recorded_referer_mismatch",
    )),
    ("document", "文档表示", "Document representation", (
        "json_document_dom_contract",
    )),
)


def _reviewed_failures(rows: list[dict], participant: str) -> list[dict]:
    selected = [
        row
        for row in rows
        if row["participant_id"] == participant and row["status"] != "pass"
    ]
    if any(not row.get("cause_code") for row in selected):
        raise ValueError(f"failure profile has an unreviewed result: {participant}")
    return selected


def _cause_groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["cause_code"]), []).append(row)
    return sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))


def failure_signature_cards(
    methods: dict, rows: list[dict], lang: str, *, participants: tuple[str, ...]
) -> str:
    """Render a compact, data-bearing failure signature for summary pages."""
    zh = lang == "zh"
    cards = []
    for participant in participants:
        if participant not in methods:
            raise ValueError(f"failure signature participant is not registered: {participant}")
        failures = _reviewed_failures(rows, participant)
        population = sum(row["participant_id"] == participant for row in rows)
        groups = _cause_groups(failures)
        dominant = groups[:3]
        items = []
        for _code, members in dominant:
            sample = members[0]
            label = sample.get("cause_zh" if zh else "cause_en") or sample["cause_code"]
            share = len(members) / len(failures) * 100 if failures else 0
            query = urlencode(
                {
                    "participant": participant,
                    "status": "fail",
                    "cause": sample.get("cause_zh") or sample["cause_code"],
                }
            )
            items.append(
                '<li><div><span>'
                + escape(str(label))
                + f'</span><strong>{len(members)}</strong></div>'
                + f'<div class="failure-mini-track"><i style="width:{share:.2f}%"></i></div>'
                + f'<a href="results.html?{escape(query, quote=True)}#result-inventory">'
                + ("查看这些任务" if zh else "Inspect tasks")
                + "</a></li>"
            )
        remainder = len(failures) - sum(len(members) for _, members in dominant)
        if remainder:
            items.append(
                f'<li class="failure-remainder"><span>{"其余类型" if zh else "Other subtypes"}</span>'
                f'<strong>{remainder}</strong></li>'
            )
        cards.append(
            f'<article data-method="{escape(participant)}"><header><div><span>{"官方失败" if zh else "Official failures"}</span>'
            f'<strong>{len(failures)} / {population}</strong></div><b>{escape(methods[participant]["label"])}</b></header>'
            f'<ul>{"".join(items)}</ul></article>'
        )
    heading = "四种浏览器的主导错误" if zh else "Dominant failures in four browsers"
    note = (
        "每条失败只进入一个复核子类。这里显示各方法数量最多的三类；完整分类、原因和逐题证据见报告解读。"
        if zh
        else "Each failure belongs to one reviewed subtype. These are the three largest for each method; Interpretation contains every subtype, explanation, and case evidence."
    )
    return (
        f'<section class="summary-slide" data-failure-signatures><div class="section-heading"><div><h2>{heading}</h2>'
        f'<p>{note}</p></div><a href="interpretation.html#browser-diagnoses">'
        + ("查看四章详解" if zh else "Read four browser chapters")
        + f'</a></div><div class="failure-signature-grid">{"".join(cards)}</div></section>'
    )


def browser_failure_profile(methods: dict, rows: list[dict], lang: str, participant: str) -> str:
    """Render one browser's complete reviewed subtype distribution and evidence links."""
    if participant not in methods:
        raise ValueError(f"failure profile participant is not registered: {participant}")
    zh = lang == "zh"
    selected = [row for row in rows if row["participant_id"] == participant]
    failures = _reviewed_failures(rows, participant)
    groups = _cause_groups(failures)
    if not selected or not failures:
        raise ValueError(f"failure profile requires a failing population: {participant}")
    maximum = max(len(members) for _, members in groups)
    bars = []
    explanations = []
    for code, members in groups:
        sample = min(members, key=lambda row: int(row["task_id"]))
        count = len(members)
        label = sample.get("cause_zh" if zh else "cause_en") or code
        reason = sample.get("reason_zh" if zh else "reason_en") or label
        share = count / len(failures) * 100
        width = count / maximum * 100
        bars.append(
            f'<li data-cause="{escape(code)}"><span>{escape(str(label))}</span>'
            f'<div class="failure-bar-track"><i style="width:{width:.2f}%"></i></div>'
            f'<strong>{count}</strong><small>{share:.1f}%</small></li>'
        )
        detail = f'result/{escape(str(sample["key"]), quote=True)}.html'
        explanations.append(
            f'<article data-cause-detail="{escape(code)}"><header><h3>{escape(str(label))}</h3>'
            f'<strong>{count} {"题" if zh else "tasks"}</strong></header><p>{escape(str(reason))}</p>'
            f'<a href="{detail}">{"代表题" if zh else "Representative task"} #{int(sample["task_id"])} →</a></article>'
        )
    passed = sum(row["status"] == "pass" for row in selected)
    label = methods[participant]["label"]
    return (
        f'<div class="browser-failure-profile" data-browser-failure-profile="{escape(participant)}">'
        f'<div class="browser-profile-overview"><div class="browser-score"><span>{escape(label)}</span>'
        f'<strong>{passed}/{len(selected)}</strong><small>{"官方通过" if zh else "official passes"}</small></div>'
        f'<div class="browser-failure-total"><strong>{len(failures)}</strong><span>{"条失败，全部已复核" if zh else "failures, all reviewed"}</span></div></div>'
        f'<ol class="failure-bars">{"".join(bars)}</ol>'
        f'<div class="failure-explanations">{"".join(explanations)}</div></div>'
    )


def failure_category_matrix(methods: dict, rows: list[dict], lang: str, *, embedded: bool = False) -> str:
    """Count each failed method-task once, including unreviewed failures."""
    zh = lang == "zh"
    labels = {}
    counts = Counter()
    totals = Counter()
    population = Counter()
    seen = set()
    for row in rows:
        method = row["participant_id"]
        identity = (method, row["task_id"])
        if identity in seen:
            raise ValueError("duplicate method-task result in failure matrix")
        if method not in methods:
            raise ValueError("unregistered method in failure matrix")
        seen.add(identity)
        population[method] += 1
        if row["status"] == "pass":
            continue
        code = row.get("cause_code") or "unreviewed"
        labels[code] = (
            row.get("cause_zh") or "未复核",
            row.get("cause_en") or "Unreviewed",
        )
        counts[method, code] += 1
        totals[method] += 1

    families = []
    assigned = set()
    for family, cn, en, codes in FAMILIES:
        present = [code for code in codes if code in labels]
        if present:
            families.append((family, cn if zh else en, present))
            assigned.update(present)
    remaining = sorted(set(labels) - assigned)
    if remaining:
        families.append(("other", "其他／未复核" if zh else "Other / unreviewed", remaining))
    columns = [code for _, _, codes in families for code in codes]
    groups = "".join(
        f'<th scope="colgroup" colspan="{len(codes)}" data-family="{family}">{escape(label)}</th>'
        for family, label, codes in families
    )
    headers = "".join(
        f'<th scope="col" data-category="{escape(code)}">{escape(labels[code][0 if zh else 1])}</th>'
        for code in columns
    )
    body = []
    for method, entry in methods.items():
        cells = []
        for code in columns:
            count = counts[method, code]
            query = {"participant": method, "status": "fail"}
            # The existing inventory uses the Chinese review label as a stable
            # filter value in both localizations.
            if code != "unreviewed":
                query["cause"] = labels[code][0]
            url = "results.html?" + urlencode(query) + "#result-inventory"
            value = f'<a href="{escape(url, quote=True)}">{count}</a>' if count and code != "unreviewed" else str(count)
            cells.append(f'<td data-category="{escape(code)}" data-count="{count}">{value}</td>')
        body.append(
            f'<tr data-method="{escape(method)}"><th scope="row">{escape(entry["label"])}</th>'
            f'<td data-failure-total="{totals[method]}">{totals[method]} / {population[method]}</td>'
            + "".join(cells) + "</tr>"
        )
    title = "失败分布：大类与子类" if zh else "Failures by family and subtype"
    note = (
        "每个方法的全部任务为分母；每条失败只计入一个子类。横向滚动查看全部分类，点击计数查看对应题目。分类描述故障位置，责任归属与根因证据见逐题诊断。"
        if zh else
        "The denominator is all tasks for each method. Each failure belongs to one subtype. Scroll horizontally for all categories and select a count to inspect its cases; fault ownership and root-cause evidence are in each diagnosis."
    )
    tag = "div" if embedded else "section"
    heading = "" if embedded else f"<h2>{title}</h2>"
    return (
        f'<{tag} data-failure-matrix>{heading}<p>{note}</p>'
        f'<div class="table-scroll failure-category-matrix" tabindex="0" role="region" aria-label="{title}"><table><thead><tr>'
        f'<th scope="col" rowspan="2">{"方法" if zh else "Method"}</th>'
        f'<th scope="col" rowspan="2">{"失败 / 任务" if zh else "Failures / tasks"}</th>'
        f'{groups}</tr><tr>{headers}</tr></thead><tbody>{"".join(body)}</tbody></table></div></{tag}>'
    )
