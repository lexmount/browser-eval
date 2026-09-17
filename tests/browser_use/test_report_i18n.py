import json
import re
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from harness.browser_use.report_i18n import localize_html, translate_text


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            self.stack = self.stack[: len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def handle_data(self, data: str) -> None:
        if not any(tag in self.stack for tag in ("pre", "code", "script", "style")):
            self.values.append(data)


def test_browser_use_change_records_have_complete_structured_english() -> None:
    data = json.loads(Path("research/report-changes.json").read_text(encoding="utf-8"))
    assert data["history_scope_i18n"]["en"]
    for collection in ("browser_use_reviews", "browser_use_releases"):
        for record in data[collection]:
            english = record["i18n"]["en"]
            for field in ("title", "summary") if collection.endswith("releases") else (
                "title",
                "status",
                "scope",
                "findings",
                "corrections",
                "remaining",
            ):
                assert english[field]
                assert not re.search(r"[\u4e00-\u9fff]", english[field])
            if collection.endswith("releases"):
                assert len(english["changes"]) == len(record.get("changes") or [])
                assert all(
                    len(target) == len(source) == 4
                    for source, target in zip(
                        record.get("changes") or [], english["changes"], strict=True
                    )
                )
                assert not re.search(
                    r"[\u4e00-\u9fff]",
                    json.dumps(english["changes"], ensure_ascii=False),
                )


def test_english_ui_is_localized_but_measured_text_is_preserved() -> None:
    source = (
        '<html lang="zh-CN"><body><h1>Browser Use 联合多模型能力实测</h1>'
        '<h2>查看本题运行结果</h2><p>评测集页面保持为冻结输入；实测输出、判分和轨迹在结果页查看。</p>'
        '<div><b>程序自动定位：</b><b>核验根因子类：</b><b>核验原因：</b></div>'
        '<pre>{"raw": "原始测量证据"}</pre><p>创建一个命名明确的空仓库</p></body></html>'
    )
    localized = localize_html(source, PurePosixPath("results/example.html"))
    parser = _VisibleText()
    parser.feed(localized)
    visible = " ".join(parser.values)
    for leaked in (
        "联合多模型能力实测",
        "查看本题运行结果",
        "评测集页面保持",
        "程序自动定位",
        "核验根因子类",
        "核验原因",
    ):
        assert leaked not in visible
    assert "原始测量证据" in localized
    assert "创建一个命名明确的空仓库" in visible


def test_current_interpretation_and_expansion_copy_is_localized() -> None:
    samples = (
        (
            "下表仅供诊断，限定为所有配置最终都通过的 54 题，并以每题最终逻辑结果"
            "包含的全部物理执行计算 P50。这个事后选出的子集不用于性能排名；完整样本的指标见上表。"
        ),
        (
            "完整 90 题官方结果是本报告的主口径；76 题事后诊断子集只用于分离已确认的"
            "外部干扰，不改写成绩。"
        ),
        (
            "若另行扩充 Browser Use 评测，当前四个已实跑站点可预检 655 个单站任务；"
            "计入只依赖这四站的跨站任务后，共有 678 个候选，其中 hard 子集 233 个。"
            "轨迹回放使用独立任务合同，不由本报告决定题量。"
        ),
        (
            "先对四个已验证站点的 678 个候选做可完成性预检和分层抽样；"
            "Wikipedia、Map 等数据环境须先完成独立部署资格验证。"
        ),
        (
            "账号隔离只用于避免用户级对象冲突，不能替代环境 lane。"
            "本轮有 14 题经复核确认受外部干扰；这些任务只在事后诊断子集中按整题排除，"
            "官方分数仍完整保留。"
        ),
        (
            "前者核对证据包与 evaluator 记录，后者检查失败的直接原因；"
            "已通过结果没有再做独立语义盲审，其判定依据仍是官方 evaluator。"
        ),
        (
            "通过率、耗时和资源的具体差异以上表为准。每个配置每题只有一个最终逻辑结果；"
            "模型会改变动作数量，站点和验收也可能影响分数，不能把端到端差异单独归因于浏览器。"
            "已确认的外部干扰按整题隔离并保留原始分数，修复后须另行完整复测。"
        ),
        "查看 90 题的全配置矩阵 →",
        "查看 GPT-5.6 Sol · Chromium 自主轨迹示例",
        "CowAgent；GPT-5.6 Sol、GPT-6 Astra；模型推理强度为低",
        "GPT-5.6 Sol、GPT-6 Astra，reasoning=low",
        "90 题 Browser Use 完整结果：Chromium、Moli 1.1.3 的成功率、代价与失败根因",
        (
            "首页按预注册的 90 题展示 4 种配置的官方 evaluator 结果。其中 14 题经复核存在"
            "验收合同、站点、共享状态或参考状态干扰；这些题仍保留在 90 题官方结果中，"
            "另外提供 76 题事后诊断子集。"
        ),
        (
            "首页主结果始终使用完整 90 题官方 evaluator 分母。为了观察排除已知外部干扰"
            "后的差异，下表再按整题、全部 4 种配置一致排除 14 题，得到 76 题事后诊断子集。"
        ),
        (
            "2 个模型层的浏览器配对共出现 9 次单边通过，可用于定位兼容性问题；"
            "同一任务可能出现在多个配对中，这不是去重任务数。样本量和方向都不支持总体优劣判断。"
        ),
        "修正任务合同、验收器或证据采集，通过独立预检后重跑全部配置。",
        (
            "4 种配置先在同一张表和同一任务矩阵中比较；失败结果可直接打开诊断，"
            "查看发生位置、程序报错、复核根因、轨迹、画面和日志。"
        ),
        (
            "4 种配置的通过率、耗时、Tokens 与浏览器资源总表已放在首页，避免在分析页重复。"
            "这里直接进入同题矩阵和逐结果失败诊断。"
        ),
        (
            "每行是一道预注册任务，4 列分别对应模型×浏览器配置；"
            "可筛出结果有差异、全部通过或全部失败的任务。"
        ),
        (
            "均值、P50、P95 都在全部注册配置中统一比较。CPU、内存和网络只计浏览器容器，"
            "不含 CowAgent 或模型服务；模型会改变动作数量和页面停留时间，因此这里比较整套配置，"
            "不能单独归因于浏览器内核。"
        ),
        (
            "仅对方向明确的指标标记全部注册配置中数值唯一最低者；"
            "占用率与网络传输量不标优劣。CPU 时间与网络总量累计逻辑结果使用的全部执行；"
            "内存来自 Browser Use 执行阶段的浏览器容器采样。"
        ),
    )
    for source in samples:
        assert not re.search(r"[\u3400-\u9fff]", translate_text(source)), source


def test_release_translation_comes_from_structured_record() -> None:
    data = json.loads(Path("research/report-changes.json").read_text(encoding="utf-8"))
    release = data["browser_use_releases"][0]
    assert translate_text(release["title"]) == release["i18n"]["en"]["title"]


def test_failure_summary_and_scope_labels_are_fully_localized() -> None:
    assert translate_text("Agent / 模型行为") == "Agent / model behavior"
    assert translate_text("浏览器证据接口") == "Browser evidence interface"
    assert translate_text("保留并修复") == "Retain and fix"
    assert translate_text(
        "75 个失败结果已经逐条核验。与任务合同、站点、共享环境或参考状态有关的任务"
        "不会进入诊断子集，修复后需完整重跑。"
    ) == (
        "All 75 failed results have been reviewed individually. Tasks affected by task-contract, "
        "site, shared-environment, or reference-state issues are excluded from the diagnostic "
        "subset and require a complete rerun after correction."
    )


def test_excluded_task_ids_use_english_punctuation() -> None:
    source = '<html lang="zh-CN"><body><p>本轮剔除 3 题：#1、#2、#3。</p></body></html>'
    localized = localize_html(source, PurePosixPath("analysis/index.html"))

    assert "3 tasks excluded in this run: #1, #2, #3." in localized
    assert "、" not in localized


def test_all_release_and_review_copy_localizes_without_chinese() -> None:
    data = json.loads(Path("research/report-changes.json").read_text(encoding="utf-8"))
    values = [data["history_scope"]]
    for record in data["browser_use_reviews"]:
        values.extend(
            record[field]
            for field in ("title", "status", "scope", "findings", "corrections", "remaining")
        )
    for record in data["browser_use_releases"]:
        values.extend((record["title"], record["summary"]))
        values.extend(cell for row in record.get("changes") or [] for cell in row)

    source = '<html lang="zh-CN"><body>' + "".join(
        f"<p>{value}</p>" for value in values
    ) + "</body></html>"
    localized = localize_html(source, PurePosixPath("changes/index.html"))
    parser = _VisibleText()
    parser.feed(localized)

    assert not re.search(r"[\u4e00-\u9fff]", " ".join(parser.values))
