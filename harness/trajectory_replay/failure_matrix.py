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
        "input_navigation_lifecycle", "cart_add_effect_absent",
        "implicit_search_submission_absent",
    )),
    ("network", "网络传输与记录", "Network transport and recording", (
        "fetch_transport_timeout", "recorded_referer_mismatch",
    )),
    ("document", "文档表示", "Document representation", (
        "json_document_dom_contract",
    )),
)


def failure_category_matrix(methods: dict, rows: list[dict], lang: str) -> str:
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
    return (
        f'<section data-failure-matrix><h2>{title}</h2><p>{note}</p>'
        f'<div class="table-scroll failure-category-matrix" tabindex="0" role="region" aria-label="{title}"><table><thead><tr>'
        f'<th scope="col" rowspan="2">{"方法" if zh else "Method"}</th>'
        f'<th scope="col" rowspan="2">{"失败 / 任务" if zh else "Failures / tasks"}</th>'
        f'{groups}</tr><tr>{headers}</tr></thead><tbody>{"".join(body)}</tbody></table></div></section>'
    )
