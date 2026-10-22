"""Build a route-for-route English mirror of the browser-use report."""

from __future__ import annotations

import html
import json
import posixpath
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from harness.web_fetch.report_i18n import translate_text as translate_common_text

NAV = (
    ("index.html", "Browser Use"),
    ("datasets/index.html", "Dataset"),
    ("methodology/index.html", "Methodology"),
    ("analysis/index.html", "Results"),
    ("review/index.html", "Review"),
    ("fixes/index.html", "Fix log"),
    ("adoption/index.html", "Browsers"),
    ("reproduce/index.html", "Reproduction"),
    ("changes/index.html", "Changes"),
    ("accounts/index.html", "Accounts and setup"),
)


_TRANSLATIONS = json.loads(Path(__file__).with_name("report-translations.json").read_text(encoding="utf-8"))

TEXT = dict(_TRANSLATIONS['TEXT_0'])

_FAILURE_ANALYSIS = json.loads(
    (Path(__file__).parents[2] / "research/browser-use-failure-analysis.json").read_text(
        encoding="utf-8"
    )
)
for _diagnosis in _FAILURE_ANALYSIS["tasks"].values():
    for _field in ("root_cause", "owner", "proof", "corrective_action", "verification"):
        _localized = _diagnosis.get(_field)
        if isinstance(_localized, dict) and _localized.get("zh") and _localized.get("en"):
            TEXT[str(_localized["zh"])] = str(_localized["en"])

_FAILURE_CAUSE_REVIEW = json.loads(
    (Path(__file__).parents[2] / "research/browser-use-failure-cause-review.json").read_text(
        encoding="utf-8"
    )
)
_failure_cause_results = _FAILURE_CAUSE_REVIEW.get("results") or {}
for _cohort in (_FAILURE_CAUSE_REVIEW.get("cohorts") or {}).values():
    _failure_cause_results.update(_cohort.get("results") or {})
for _cause in _failure_cause_results.values():
    for _field in ("subcategory", "reason"):
        _localized = _cause.get(_field)
        if isinstance(_localized, dict) and _localized.get("zh") and _localized.get("en"):
            TEXT[str(_localized["zh"])] = str(_localized["en"])

# Browser Use-specific prose is intentionally translated here rather than in
# the shared Web Fetch catalog.  Task intent and measured evidence are not
# listed: unmatched task text stays verbatim, and Localizer preserves pre/code
# blocks containing evaluator JSON and agent output.
TEXT.update(
    dict(_TRANSLATIONS['TEXT_2'])
)

# Report-authored interface copy introduced by the multi-model renderer. Task
# names, task intents, expected/actual values, and execution excerpts are
# deliberately not covered here: they are measured material and may remain in
# their source language.
TEXT.update(
    dict(_TRANSLATIONS['TEXT_3'])
)


def _register_report_change_translations() -> None:
    """Load explicit English release/review copy from the shared report model.

    Release history changes frequently, so keeping its English prose beside the
    Chinese source prevents a new release from depending on a hand-maintained
    global exact-string table.
    """

    path = Path(__file__).parents[2] / "research/report-changes.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))

    history_i18n = data.get("history_scope_i18n") or {}
    if isinstance(data.get("history_scope"), str) and isinstance(history_i18n.get("en"), str):
        TEXT[data["history_scope"]] = history_i18n["en"]

    for collection in ("browser_use_reviews", "browser_use_releases"):
        for record in data.get(collection) or []:
            english = ((record.get("i18n") or {}).get("en") or {})
            for field in ("title", "status", "scope", "findings", "corrections", "remaining", "summary"):
                source, target = record.get(field), english.get(field)
                if isinstance(source, str) and isinstance(target, str):
                    TEXT[source] = target
            source_rows, target_rows = record.get("changes") or [], english.get("changes") or []
            if len(source_rows) != len(target_rows):
                continue
            for source_row, target_row in zip(source_rows, target_rows, strict=True):
                if len(source_row) != len(target_row):
                    continue
                for source, target in zip(source_row, target_row, strict=True):
                    if isinstance(source, str) and isinstance(target, str):
                        TEXT[source] = target


_register_report_change_translations()

TEXT.update(
    dict(_TRANSLATIONS['TEXT_4'])
)

TEXT.update(
    dict(_TRANSLATIONS['TEXT_5'])
)


PHRASES = dict(_TRANSLATIONS['PHRASES_1'])

TEXT.update(
    dict(_TRANSLATIONS['TEXT_6'])
)


def translate_text(value: str) -> str:
    leading = value[: len(value) - len(value.lstrip())]
    trailing = value[len(value.rstrip()) :]
    core = value.strip()
    if not core:
        return value
    translated = TEXT.get(core)
    if translated is None:
        patterns = (
            (
                r"原始 Mind2Web 使用 cross-task、cross-website、cross-domain 衡量泛化；Online‑Mind2Web 没有统一三档；Mind2Web 2 的 short / medium / long 按动作跨度划分。本报告的简单 (\d+)、中等 (\d+)、困难 (\d+) 是预注册的操作长度层级。",
                lambda m: f"Original Mind2Web measures cross-task, cross-website and cross-domain generalization. Online-Mind2Web has no standard three-tier split; Mind2Web 2 groups short, medium and long tasks by action span. This report preregisters {m[1]} easy, {m[2]} medium and {m[3]} hard tasks by operation length.",
            ),
            (
                r"本轮在模型执行前固定 (\d+) 个任务，覆盖四个已部署站点以及 RETRIEVE、NAVIGATE、MUTATE 三种最终合同。这里仅描述题目与验收规则；通过率、耗时、轨迹和可完成性观察统一放在结果分析中。",
                lambda m: f"All {m[1]} tasks were fixed before model execution, covering four deployed sites and RETRIEVE, NAVIGATE and MUTATE outcome contracts. This page describes tasks and acceptance rules. Pass rates, timings, trajectories and feasibility observations belong in results analysis.",
            ),
            (
                r"首页按预注册的 (\d+) 题展示四种配置的官方 evaluator 结果。其中 (\d+) 题经复核存在验收合同、站点、共享状态或参考状态干扰；这些题仍保留在 (\d+) 题官方结果中，另外提供 (\d+) 题事后诊断子集。",
                lambda m: f"The homepage shows the official evaluator results for all {m[1]} preregistered tasks across four configurations. Review identified acceptance-contract, site, shared-state or reference-state interference in {m[2]} tasks. They remain in the official {m[3]}-task results; a separate {m[4]}-task post-hoc diagnostic subset is also available.",
            ),
            (
                r"首页主结果始终使用完整 (\d+) 题官方 evaluator 分母。为了观察排除已知外部干扰后的差异，下表再按整题、四种配置一致排除 (\d+) 题，得到 (\d+) 题事后诊断子集。",
                lambda m: f"The primary result always uses the complete official {m[1]}-task denominator. The table below excludes the same {m[2]} whole tasks across four configurations to show a {m[3]}-task post-hoc diagnostic subset without known external interference.",
            ),
            (
                r"这是事后诊断子集，不是正式分数，也不替代 (\d+) 题官方结果。(\d+) 个原始失败记录继续保存在结果页和核验记录中。",
                lambda m: f"This post-hoc diagnostic subset is not an official score and does not replace the official {m[1]}-task result. All {m[2]} original failure records remain in the result and review pages.",
            ),
            (
                r"完整 (\d+) 题官方结果是本报告的主口径；(\d+) 题事后诊断子集只用于分离已知外部干扰，不改写成绩。",
                lambda m: f"The complete official {m[1]}-task result is the primary view. The {m[2]}-task post-hoc diagnostic subset separates known external interference and does not rewrite scores.",
            ),
            (
                r"这里保留预注册 (\d+) 题的全部官方结果；其中 (\d+) 题已标记外部干扰。默认显示失败；点击“查看诊断”可核对发生位置、程序报错、复核根因、轨迹、画面和日志。",
                lambda m: f"All official results for the {m[1]} preregistered tasks are retained; {m[2]} tasks are marked as affected by external interference. Failures are shown by default. Open a diagnosis to inspect the failure stage, program error, reviewed cause, trajectory, screenshots and logs.",
            ),
            (
                r"预注册文件只含任务 ID、层级和入选理由，不含点击序列。CowAgent 收到自然语言目标后，在最多 (.+) 步内循环读取页面观察、请求模型决策并生成下一次 BrowserTool 调用。Chromium 与 Moli 分别连接独立 CDP 浏览器容器；每次物理执行都保留最终 JSON、Agent 事件流、网络 HAR 和 Playwright trace。",
                lambda m: f"Preregistration contains task IDs, tiers and selection reasons, without click sequences. Given a natural-language goal, CowAgent reads page observations, requests model decisions and produces the next BrowserTool call, up to {TEXT.get(m[1], m[1])} steps. Chromium and Moli connect to separate CDP browser containers. Each physical execution retains the final JSON, Agent event stream, network HAR and Playwright trace.",
            ),
            (r"候选 benchmark、选择依据与 (\d+) 个预注册任务", lambda m: f"Candidate benchmarks, selection rationale and {m[1]} preregistered tasks"),
            (r"(\d+) 次 / (\d+) 次传输", lambda m: f"{m[1]} calls / {m[2]} transport attempts"),
            (r"(\d+) 次", lambda m: f"{m[1]} times"),
            (r"首次 (\d+/\d+)", lambda m: f"First attempt {m.group(1)}"),
            (r"失败后重试 (\d+) 题", lambda m: f"{m.group(1)} failed tasks retried"),
            (r"查看 (\d+) 题的四配置矩阵 →", lambda m: f"View the four-configuration matrix for all {m.group(1)} tasks →"),
            (
                r"Sol × Chromium 首次 (\d+/\d+)，有 (\d+) 个任务在重试中恢复，最终 (\d+/\d+)；Astra × Chromium 首次和最终均为 (\d+/\d+)。",
                lambda m: f"Sol × Chromium passed {m.group(1)} on the first attempt, recovered {m.group(2)} tasks on retry, and finished at {m.group(3)}. Astra × Chromium was {m.group(4)} both before and after retries.",
            ),
            (
                r"(\d+) 题审查合格比较集：Chromium 与 Moli 的结果、代价与失败位置",
                lambda m: f"{m.group(1)}-task review-qualified set: Chromium vs. Moli outcomes, cost, and failure points",
            ),
            (
                r"预注册的 (\d+) 题中，(\d+) 题确认受到验收合同、站点、共享状态或参考状态干扰。首页只用剩余 (\d+) 题比较四种配置；全部原始结果仍可下钻审计。",
                lambda m: f"Of {m.group(1)} preregistered tasks, {m.group(2)} were confirmed to be affected by acceptance contracts, sites, shared state, or reference-state drift. The homepage compares the four configurations only on the remaining {m.group(3)} tasks; every raw result remains available for audit.",
            ),
            (
                r"(\d+) 题退出正式比较",
                lambda m: f"{m.group(1)} tasks are excluded from the formal comparison",
            ),
            (
                r"只有在 Chromium 的最终分数上，Astra 的 (\d+/\d+) 低于 Sol 的 (\d+/\d+)。首次执行时，Sol 为 (\d+/\d+)，Astra 为 (\d+/\d+)；差值来自 Sol 恢复的 (\d+) 个任务。两批模型运行使用了不同的 browser‑eval 提交，因此这里保留为观察，不作严格模型排名。",
                lambda m: f"Astra trails Sol only in the final Chromium result, {m.group(1)} versus {m.group(2)}. On the first attempt Sol scored {m.group(3)} and Astra {m.group(4)}; the difference comes from {m.group(5)} Sol recoveries. The two model cohorts used different browser-eval commits, so this remains an observation rather than a strict model ranking.",
            ),
            (
                r"对已证实受任务 / evaluator、站点、共享环境或参考状态干扰的 (\d+) 题，按整题排除四种配置，剩余 (\d+) 题。原始 evaluator 输出不改写，但不再用作首页比较分母。",
                lambda m: f"For {m.group(1)} tasks confirmed to be affected by task/evaluator, site, shared-state, or reference-state problems, all four configurations are excluded together, leaving {m.group(2)} tasks. Raw evaluator outputs are preserved but no longer serve as the homepage comparison denominator.",
            ),
            (
                r"这是本报告的正式比较分母。被排除任务及其 (\d+) 个原始失败记录继续保存在结果页和核验记录中；修复并按同一协议补跑四种配置后，任务才可重新进入比较集。",
                lambda m: f"This is the report's formal comparison denominator. Excluded tasks and their {m.group(1)} raw failure records remain in the result and review pages. A task can re-enter only after correction and a four-configuration rerun under the same protocol.",
            ),
            (
                r"(\d+)/(\d+) 个原始最终失败结果逐条阅读错误、断言、最终回答、HAR 和 Agent 轨迹，归类并给出处理边界。",
                lambda m: f"All {m.group(1)}/{m.group(2)} raw final failures received case-by-case review of errors, assertions, final answers, HAR, and agent trajectories, followed by classification and treatment boundaries.",
            ),
            (
                r"(\d+) 题审查合格比较集显示，四种端到端配置都完成了约九成任务，浏览器之间的单边差异集中在少数任务。有效失败主要来自 Agent 行为和浏览器证据接口，不再混入已确认的合同、站点或共享状态问题。",
                lambda m: f"On the {m.group(1)}-task review-qualified set, all four end-to-end configurations complete about nine in ten tasks, with one-sided browser differences concentrated in a few tasks. Remaining failures are mainly agent behavior and browser-evidence-interface issues, without confirmed contract, site, or shared-state interference.",
            ),
            (
                r"Moli 的内存占用明显更低，但 HAR 完整性、工具耗时和部分任务兼容性还需修复；Chromium 在两个模型层都多通过任务，但当前每种配置只有一个正式样本，不能外推成总体优势。",
                lambda m: "Moli uses substantially less memory, while HAR completeness, tool latency, and some task compatibility still need work. Chromium passes more tasks in both model cohorts, but one formal sample per configuration cannot establish a general advantage.",
            ),
            (
                r"四种配置总览：(\d+) 题审查合格比较集",
                lambda m: f"Four-configuration overview: {m.group(1)}-task review-qualified set",
            ),
            (
                r"同题全配置对比：(\d+) 题",
                lambda m: f"Same-task comparison across all configurations: {m.group(1)} tasks",
            ),
            (
                r"Astra 首次通过 (\d+/\d+)，Sol 为 (\d+/\d+)",
                lambda m: f"Astra passed {m.group(1)} on the first attempt; Sol passed {m.group(2)}",
            ),
            (
                r"Sol × Chromium 有 (\d+) 个重试任务恢复成功，最终到 (\d+/\d+)。Astra 的 (\d+/\d+) 并非首次表现落后。",
                lambda m: f"{m.group(1)} Sol × Chromium tasks recovered on retry, raising its final score to {m.group(2)}. Astra's {m.group(3)} does not reflect weaker first-attempt performance.",
            ),
            (
                r"两层模型共有 (\d+) 个单边通过配对",
                lambda m: f"{m.group(1)} task pairs were one-sided across the two model cohorts",
            ),
            (
                r"(.+)：(\d+) 题共同通过、(\d+) 题仅 (.+) 通过、(\d+) 题仅 (.+) 通过；(.+)：(\d+) 题共同通过、(\d+) 题仅 (.+) 通过、(\d+) 题仅 (.+) 通过。",
                lambda m: f"{m.group(1)}: both browsers passed {m.group(2)} tasks, only {m.group(4)} passed {m.group(3)}, and only {m.group(6)} passed {m.group(5)}. {m.group(7)}: both browsers passed {m.group(8)} tasks, only {m.group(10)} passed {m.group(9)}, and only {m.group(12)} passed {m.group(11)}.",
            ),
            (
                r"(\d+) 题含已确认的外部干扰",
                lambda m: f"{m.group(1)} tasks contain confirmed external interference",
            ),
            (
                r"(\d+) 个失败结果已经逐条核验。与任务合同、站点、共享环境或参考状态有关的任务不会进入诊断子集，修复后需完整重跑。",
                lambda m: f"All {m.group(1)} failed results have been reviewed individually. Tasks affected by task-contract, site, shared-environment, or reference-state issues are excluded from the diagnostic subset and require a complete rerun after correction.",
            ),
            (
                r"干扰来自验收合同、站点、共享环境或参考状态。官方分数保留，另用 (\d+) 题事后诊断子集观察目标变量。",
                lambda m: f"The interference comes from acceptance contracts, sites, shared state, or reference state. Official scores remain unchanged; a post-hoc {m.group(1)}-task subset is used only for diagnosis.",
            ),
            (
                r"首次通过 (\d+/\d+)，重试恢复 (\d+) 题，最终通过 (\d+/\d+)",
                lambda m: f"First-attempt passes: {m.group(1)}; recovered on retry: {m.group(2)}; final passes: {m.group(3)}",
            ),
            (
                r"只有在 Chromium 的最终分数上，Astra 的 (\d+/\d+) 低于 Sol 的 (\d+/\d+)。首次执行时，Sol 为 (\d+/\d+)，Astra 为 (\d+/\d+)；差值来自 Sol 恢复的 (\d+) 个任务。Astra 的 Task 77 还受共享环境污染。",
                lambda m: f"Astra trails Sol only in the final Chromium score, {m.group(1)} versus {m.group(2)}. On the first attempt Sol scored {m.group(3)} and Astra {m.group(4)}; the difference comes from {m.group(5)} Sol recoveries. Astra's Task 77 was also contaminated by shared-state concurrency.",
            ),
            (
                r"(\d+) 题、四种配置：相近成绩背后的不同原因",
                lambda m: f"{m.group(1)} tasks and four configurations: different causes behind similar scores",
            ),
            (
                r"四种配置的官方分数很接近；(\d+) 个零分经复核后分散在多个责任层，不能统一归给 Agent 或浏览器。",
                lambda m: f"The four official scores are close. Review placed the {m.group(1)} zeros across several owning layers rather than assigning them all to the agent or browser.",
            ),
            (
                r"两个模型层合计有 (\d+) 个单边通过，可用于定位兼容性问题；样本量和方向都不支持总体优劣判断。",
                lambda m: f"Across both model cohorts, {m.group(1)} task pairs were one-sided. They help locate compatibility defects, while the sample size and direction do not support an overall ranking.",
            ),
            (
                r"(\d+)/(\d+) 次物理执行检查了 HAR、Agent trace、最终回答、evaluator 输出和环境准备回执。",
                lambda m: f"All {m.group(1)}/{m.group(2)} physical executions were checked for HAR, agent trace, final answer, evaluator output, and environment-preparation receipts.",
            ),
            (
                r"(\d+)/(\d+) 个证据包由程序核对文件存在性、哈希、JSON 可读性、回答 / 分数 / 状态一致性和运行身份。",
                lambda m: f"Programs checked all {m.group(1)}/{m.group(2)} evidence bundles for file presence, hashes, JSON readability, response/score/status consistency, and runtime identity.",
            ),
            (
                r"(\d+)/(\d+) 个最终失败结果逐条查看错误、断言、轨迹与网络记录，归类并给出处理边界。",
                lambda m: f"All {m.group(1)}/{m.group(2)} final failures were reviewed against errors, assertions, trajectories, and network records, then classified with a defined treatment boundary.",
            ),
            (
                r"(\d+)/(\d+) 个最终失败结果逐条阅读错误、断言、最终回答、HAR 和 Agent 轨迹，归类并给出处理边界。",
                lambda m: f"All {m.group(1)}/{m.group(2)} final failures received case-by-case review of errors, assertions, final answers, HAR, and agent trajectories, followed by classification and treatment boundaries.",
            ),
            (
                r"(\d+) 个失败结果的复核原因分布",
                lambda m: f"Reviewed cause distribution for {m.group(1)} failed results",
            ),
            (
                r"(\d+) 题通过",
                lambda m: f"Passes on {m.group(1)} tasks",
            ),
            (
                r"每行只比较同一模型下的 (\d+) 个配对任务。",
                lambda m: f"Each row compares the {m.group(1)} paired tasks within one model cohort.",
            ),
            (
                r"下表限定为首次执行共同成功的 (\d+) 题，每个配置只取第一次执行并报告 P50。这既排除了重试累计，也避免把“很快失败”错当成速度优势。",
                lambda m: f"The table uses the {m.group(1)} tasks that all four configurations passed on the first attempt. It reports P50 from the first execution only, excluding retry accumulation and the false speed advantage of fast failures.",
            ),
            (
                r"下表限定为四种配置最终都通过的 (\d+) 题，并以每题最终逻辑结果包含的全部物理执行计算 P50；这样不会把快速失败误当成性能优势。",
                lambda m: f"The table uses the {m.group(1)} tasks finally passed by all four configurations. P50 includes every physical execution belonging to each final logical result, so fast failures are not mistaken for a performance advantage.",
            ),
            (
                r"(\d+) 题首次共同成功样本中，Moli 峰值内存 P50 为 ([0-9.]+)–([0-9.]+) MiB；Chromium 为 ([0-9.]+)–([0-9.]+) MiB。",
                lambda m: f"Across {m.group(1)} common first-attempt successes, Moli's P50 peak memory spans {m.group(2)}-{m.group(3)} MiB, versus {m.group(4)}-{m.group(5)} MiB for Chromium.",
            ),
            (
                r"(\d+) 题最终共同成功样本中，Moli 峰值内存 P50 为 ([0-9.]+)–([0-9.]+) MiB；Chromium 为 ([0-9.]+)–([0-9.]+) MiB。",
                lambda m: f"Across {m.group(1)} common final successes, Moli's P50 peak memory spans {m.group(2)}-{m.group(3)} MiB, versus {m.group(4)}-{m.group(5)} MiB for Chromium.",
            ),
            (
                r"BrowserTool P50：Chromium ([0-9.]+)–([0-9.]+) 秒，Moli ([0-9.]+)–([0-9.]+) 秒；CPU 时间 P50：Chromium ([0-9.]+)–([0-9.]+) 秒，Moli ([0-9.]+)–([0-9.]+) 秒。",
                lambda m: f"BrowserTool P50: Chromium {m.group(1)}-{m.group(2)} seconds, Moli {m.group(3)}-{m.group(4)} seconds. CPU-time P50: Chromium {m.group(5)}-{m.group(6)} seconds, Moli {m.group(7)}-{m.group(8)} seconds.",
            ),
            (
                r"两个模型层在不同日期、不同 browser‑eval 提交上运行（(.+)）。任务、CowAgent 提交、浏览器镜像与 evaluator 数据相同，但这仍不是严格的模型 A/B。",
                lambda m: f"The model cohorts ran on different dates and browser-eval commits ({m.group(1).replace('、', '; ')}). They share tasks, CowAgent commit, browser images, and evaluator data, but this is still not a strict model A/B test.",
            ),
            (
                r"官方 (\d+) 题成绩显示，四种端到端配置都完成了大约八成任务，浏览器之间的单边差异集中在少数任务。(\d+) 个零分分布在 Agent、验收规则、站点、浏览器证据接口和共享环境等多层。",
                lambda m: f"The official {m.group(1)}-task scores show that all four end-to-end configurations complete about four-fifths of the sample, with one-sided browser differences concentrated in a few tasks. The {m.group(2)} zeros span agent behavior, acceptance rules, sites, browser-evidence interfaces, and shared state.",
            ),
            (
                r"Moli 的内存占用明显更低，但当前 HAR 完整性、工具耗时和部分任务兼容性还需修复；Chromium 在 (\d+) 题诊断子集中两个模型层都多通过任务，但当前只有一个正式样本，不能外推成总体优势。",
                lambda m: f"Moli uses substantially less memory, while HAR completeness, tool latency, and some task compatibility still need work. Chromium passes more tasks in both model cohorts on the {m.group(1)}-task diagnostic subset, but one formal sample cannot establish a general advantage.",
            ),
            (
                r"先修复并重跑 (\d+) 题干扰项，再对同一冻结代码的四种配置增加重复次数。在此之前，这份报告用于定位兼容性和证据链问题，不发布模型或浏览器排名。",
                lambda m: f"First fix and rerun the {m.group(1)} interference tasks; then add repetitions for all four configurations on one frozen codebase. Until then, this report is for locating compatibility and evidence-chain defects, not for publishing a model or browser ranking.",
            ),
            (
                r"(\d+) 个结果与失败诊断",
                lambda m: f"{m.group(1)} results and failure diagnoses",
            ),
            (
                r"失败发生在(.+)；下方保留程序报错、复核根因和对应执行证据。",
                lambda m: f"The failure occurred at {translate_text(m.group(1))}. The exact program error, reviewed root cause, and corresponding execution evidence are provided below.",
            ),
            (
                r"四种配置均使用同一组 (\d+) 题。模型和浏览器同时变化时，表格比较的是整套配置，不能把差值只归因于浏览器。",
                lambda m: f"All four configurations use the same {m.group(1)} tasks. When both model and browser vary, the table compares complete configurations; differences cannot be attributed to the browser alone.",
            ),
            (
                r"下表统一比较四种配置。CPU、内存和网络只计浏览器容器，不含 CowAgent 或模型服务；均值、P50、P95 都按 (\d+) 个逻辑任务结果统计。模型会改变动作数量和页面停留时间，因此这里比较的是整套配置，不能单独归因于浏览器内核。",
                lambda m: f"The table compares all four configurations. CPU, memory, and network measurements cover only the browser container, excluding CowAgent and the model service; mean, P50, and P95 are computed across {m.group(1)} logical task results. Because the model changes action counts and time spent on pages, these are whole-configuration comparisons and cannot be attributed to the browser core alone.",
            ),
            (
                r"(.+?) · (Chromium|Moli 1\.1\.3) 结果",
                lambda m: f"{m.group(1)} · {m.group(2)} result",
            ),
            (
                r"(简单|中等|困难) · (\d+) 步起",
                lambda m: f"{ {'简单': 'Easy', '中等': 'Medium', '困难': 'Hard'}[m.group(1)] } · {m.group(2)} minimum steps",
            ),
            (
                r"第 (\d+) 步 BrowserTool 执行异常",
                lambda m: f"Step {m.group(1)}: BrowserTool execution error",
            ),
            (
                r"第 (\d+) 步后达到步骤上限",
                lambda m: f"After {m.group(1)} steps: step limit reached",
            ),
            (
                r"第 (\d+) 步后的最终回答/任务状态",
                lambda m: f"After {m.group(1)} steps: final answer/task state",
            ),
            (
                r"第 (\d+) 步后的站点响应/保存",
                lambda m: f"After {m.group(1)} steps: site response/save",
            ),
            (
                r"(.+?) · 单次调用墙钟时间",
                lambda m: f"{m.group(1)} · per-call wall time",
            ),
            (
                r"判分 ([01]) · Agent 链路 ([\d.]+) 秒 · BrowserTool (\d+) 次 / ([\d.]+) 秒 · 模型 tokens ([\d,]+)",
                lambda m: f"Score {m.group(1)} · Agent path {m.group(2)} s · BrowserTool {m.group(3)} calls / {m.group(4)} s · model tokens {m.group(5)}",
            ),
            (
                r"，只保留该题所需站点；env-ctrl 报告 ready 后，再做连续应用健康检查、清浏览器状态、登录测试账号并打开起始页。本轮 (\d+)/(\d+) 个物理执行都保存了环境准备回执，并与对应执行记录绑定。",
                lambda m: f", keeping only the sites required by the task. After env-ctrl reports ready, the runner performs repeated application-health checks, clears browser state, signs in with test accounts, and opens the start page. All {m.group(1)}/{m.group(2)} physical executions retain preparation receipts bound to their execution records.",
            ),
            (
                r"预注册文件只含任务 ID、层级和入选理由，不含点击序列。CowAgent 收到自然语言目标后，在最多 (\d+) 步内循环读取页面观察、请求模型决策并生成下一次 BrowserTool 调用。Chromium 与 Moli 分别连接独立 CDP 浏览器容器；每次物理执行都保留最终 JSON、Agent 事件流、网络 HAR 和 Playwright trace。",
                lambda m: f"The preregistration contains task IDs, tiers, and selection rationales, but no click sequence. Given a natural-language objective, CowAgent repeatedly reads the page, requests a model decision, and generates the next BrowserTool call for up to {m.group(1)} steps. Chromium and Moli connect to separate CDP browser containers. Every physical execution retains the final JSON, Agent event stream, network HAR, and Playwright trace.",
            ),
            (
                r"(\d+)/(\d+) 个物理执行均记录 reset、ready、应用健康检查和登录准备状态；事件流随后证明 CowAgent 自主产生 BrowserTool 调用序列。",
                lambda m: f"All {m.group(1)}/{m.group(2)} physical executions record reset, readiness, application-health, and sign-in preparation status. The subsequent event streams show CowAgent generating BrowserTool calls autonomously.",
            ),
            (
                r"本轮 (\d+)/(\d+) 个物理执行均保存环境准备回执；公开环境摘要保留逐项状态，原始登录态不发布。",
                lambda m: f"All {m.group(1)}/{m.group(2)} physical executions retain environment-preparation receipts. The public summary preserves each status while authenticated session state remains private.",
            ),
            (
                r"每次物理执行的脱敏回答、evaluator、事件和轨迹位于",
                lambda _m: "The sanitized answer, evaluator output, events, and trajectory for each physical execution are located under",
            ),
            (
                r"给出全部 (\d+) 个逻辑结果及其物理执行、下载路径和公开文件哈希。",
                lambda m: f"lists all {m.group(1)} logical results, their physical executions, download paths, and public-file hashes.",
            ),
            (
                r"CowAgent 使用 (.+)，在同一组 WebArena‑Verified (\d+) 题上分别操作 (.+)。",
                lambda m: f"CowAgent uses {m.group(1)} to operate {m.group(3)} on the same {m.group(2)} WebArena-Verified tasks.",
            ),
            (
                r"(.+?)：(.+?) (\d+)/(\d+)（([\d.]+)%），(.+?) (\d+)/(\d+)（([\d.]+)%）；(.+?) 多通过 (\d+) 题。",
                lambda m: f"{m.group(1)}: {m.group(2)} passed {m.group(3)}/{m.group(4)} ({m.group(5)}%); {m.group(6)} passed {m.group(7)}/{m.group(8)} ({m.group(9)}%). {m.group(10)} passed {m.group(11)} more task(s).",
            ),
            (
                r"(.+?)：(.+?) (\d+)/(\d+)（([\d.]+)%），(.+?) (\d+)/(\d+)（([\d.]+)%）；通过数相同。",
                lambda m: f"{m.group(1)}: {m.group(2)} passed {m.group(3)}/{m.group(4)} ({m.group(5)}%); {m.group(6)} passed {m.group(7)}/{m.group(8)} ({m.group(9)}%). The pass counts are tied.",
            ),
            (
                r"：(.+?) (\d+)/(\d+)（([\d.]+)%），(.+?) (\d+)/(\d+)（([\d.]+)%）；(.+?) 多通过 (\d+) 题。",
                lambda m: f": {m.group(1)} passed {m.group(2)}/{m.group(3)} ({m.group(4)}%); {m.group(5)} passed {m.group(6)}/{m.group(7)} ({m.group(8)}%). {m.group(9)} passed {m.group(10)} more task(s).",
            ),
            (
                r"：(.+?) (\d+)/(\d+)（([\d.]+)%），(.+?) (\d+)/(\d+)（([\d.]+)%）；通过数相同。",
                lambda m: f": {m.group(1)} passed {m.group(2)}/{m.group(3)} ({m.group(4)}%); {m.group(5)} passed {m.group(6)}/{m.group(7)} ({m.group(8)}%). The pass counts are tied.",
            ),
            (
                r"这 (\d+) 题是固定选择的样本，结果不能直接外推到 WebArena‑Verified 全集，也不能仅凭总耗时判断浏览器内核速度。",
                lambda m: f"These {m.group(1)} tasks are a fixed cohort. Results do not generalize directly to the full WebArena-Verified benchmark, and total elapsed time alone does not measure browser-engine speed.",
            ),
            (
                r"两组模型合计有 (\d+) 个同题结果不同；不同模型的运行互不配对。",
                lambda m: f"Across both model runs, {m.group(1)} same-task results differ between browsers. Runs from different models are not paired.",
            ),
            (
                r"(\d+) 个失败是怎么造成的",
                lambda m: f"What caused the {m.group(1)} failures",
            ),
            (
                r"(\d+) 个失败结果已经逐条核验。与任务合同、站点、共享环境或参考状态有关的任务不会进入诊断子集，修复后需完整重跑。",
                lambda m: f"All {m.group(1)} failed results have been reviewed individually. Tasks affected by task-contract, site, shared-environment, or reference-state issues are excluded from the diagnostic subset and require a complete rerun after correction.",
            ),
            (
                r"以上共 (\d+) 个失败结果；每个结果只计一次。",
                lambda m: f"The table covers {m.group(1)} failed results, each counted once.",
            ),
            (
                r"(\d+) 个模型 × (\d+) 个浏览器 × (\d+) 题都有最终判定",
                lambda m: f"Every result has a final verdict: {m.group(1)} models × {m.group(2)} browsers × {m.group(3)} tasks",
            ),
            (
                r"另有 (\d+) 题在本轮没有成功轨迹",
                lambda m: f"Another {m.group(1)} tasks have no successful trajectory in this run",
            ),
            (r"查看 (\d+) 个任务", lambda m: f"View {m.group(1)} tasks"),
            (r"查看 (\d+) 个结果", lambda m: f"View {m.group(1)} results"),
            (
                r"(\d+) 个任务中，Chromium 与 Moli 的差异在哪里",
                lambda m: f"Where Chromium and Moli differ across {m.group(1)} tasks",
            ),
            (
                r"WebArena‑Verified 没有官方 easy、medium、hard 三档。本项目在运行前按最少可见 UI 操作数分为 1–4、5–7、8\+ 三档，各有 (\d+)、(\d+)、(\d+) 题。这个分组描述操作长度，不代表推理难度。",
                lambda m: f"WebArena-Verified has no official easy, medium, or hard tiers. Before execution, this project grouped tasks by minimum visible UI actions: 1-4, 5-7, and 8+, containing {m.group(1)}, {m.group(2)}, and {m.group(3)} tasks. These groups describe action length, not reasoning difficulty.",
            ),
            (
                r"两次运行合计有 (\d+) 个同题结果不同。Sol 和 Astra 的方向并不一致，因此现有结果不足以支持某个浏览器在所有任务上更好。",
                lambda m: f"Across the two runs, {m.group(1)} same-task results differ between browsers. The Sol and Astra runs point in different directions, so the current evidence does not show that one browser is better on every task.",
            ),
            (
                r"本轮固定 (\d+) 题，覆盖四个已验证站点。结果适用于当前 CowAgent、模型、任务和运行协议。它没有证明两个浏览器内核等价，也不能证明 Moli 已经可以全面替代 Chromium。端到端耗时包含模型、调度、页面等待和工具开销。",
                lambda m: f"This run fixes {m.group(1)} tasks across four verified sites. Results apply to the current CowAgent, models, tasks, and run protocol. They do not establish browser-engine equivalence or show that Moli can fully replace Chromium. End-to-end time includes model, scheduling, page waits, and tool overhead.",
            ),
            (
                r"最终数据缺少 (\d+) 项；官方期望 (\d+) 项，匹配 (\d+) 项。",
                lambda match: (
                    f"The final data is missing {match.group(1)} item(s); "
                    f"{match.group(3)} of {match.group(2)} expected item(s) matched."
                ),
            ),
            (
                r"Browser Use Capability Evaluation · (.+)",
                lambda m: "Browser Use Capability Evaluation · "
                + ("Not recorded" if m.group(1) == "未记录" else m.group(1)),
            ),
            (
                r"最终状态不符：期望 (.+)，实际为 (.+)。",
                lambda match: (
                    f"Final status mismatch: expected {match.group(1)}, observed {match.group(2)}."
                ),
            ),
            (
                r"在同一 CowAgent、同一 (.+?)（reasoning=low）和同一 WebArena‑Verified (\d+) 题样本下，对比两种浏览器完成真实网页任务的能力。",
                lambda match: (
                    f"Compare how Chromium and Moli complete real browser tasks with the same CowAgent, {match.group(1)} (reasoning=low), and the same {match.group(2)}-task WebArena-Verified sample."
                ),
            ),
            (
                r"(.+?) 任务通过",
                lambda match: f"{match.group(1)} tasks passed",
            ),
            (
                r"本轮观察到 (\d+) 个浏览器间不一致配对；结论只适用于这批受控任务，不证明内核等价或全面替代关系。",
                lambda match: f"This run observed {match.group(1)} discordant browser pair(s). The conclusion applies only to this controlled cohort and does not establish browser-core equivalence or comprehensive substitutability.",
            ),
            (
                r"这是固定选择的 (\d+) 题试验，不是从全部 WebArena‑Verified 任务随机抽样；比例只描述本样本，不外推总体能力。",
                lambda match: f"This is a fixed {match.group(1)}-task experiment, not a random sample of all WebArena-Verified tasks. Percentages describe this cohort only and are not population estimates.",
            ),
            (
                r"从 (\d+)/(\d+) 对 (\d+)/(\d+) 读出下一轮该怎么做",
                lambda match: f"What {match.group(1)}/{match.group(2)} versus {match.group(3)}/{match.group(4)} implies for the next run",
            ),
            (
                r"(\d+)/(\d+) 对 (\d+)/(\d+)：(\d+) 个任务出现浏览器分歧",
                lambda match: f"{match.group(1)}/{match.group(2)} versus {match.group(3)}/{match.group(4)}: {match.group(5)} task(s) show a browser disagreement",
            ),
            (
                r"同一 CowAgent、模型、任务、站点状态和 evaluator 下，只替换浏览器后端。总分给出结果，(\d+) 个同题配对用于定位差异；每个失败结果还要沿官方合同、最终回答、网络证据和自主轨迹追到可复核根因。",
                lambda match: f"The browser backend is the only changed variable; CowAgent, model, tasks, site state, and evaluator are held constant. Aggregate scores summarize outcomes, while {match.group(1)} paired tasks locate differences. Every failed result is then traced through the official contract, final answer, network evidence, and autonomous trajectory to a reviewable root cause.",
            ),
            (
                r"Chromium 与 Moli 分别通过 (\d+)/(\d+) 和 (\d+)/(\d+)。逐题配对后，(\d+) 题共同通过、(\d+) 题共同失败、(\d+) 题仅 Chromium 通过、(\d+) 题仅 Moli 通过。",
                lambda m: f"Chromium and Moli passed {m.group(1)}/{m.group(2)} and {m.group(3)}/{m.group(4)}, respectively. Across paired tasks, both passed {m.group(5)}, both failed {m.group(6)}, Chromium alone passed {m.group(7)}, and Moli alone passed {m.group(8)}.",
            ),
            (
                r"表 1｜(\d+) 个任务的逐题最终结果配对。",
                lambda match: f"Table 1 | Paired final outcomes for {match.group(1)} tasks.",
            ),
            (
                r"固定样本中观察到 (\d+) 个不一致配对。这个数字用于发现浏览器相关差异，但不能单独证明整个浏览器内核的优劣，也不对 WebArena‑Verified 全集作统计外推。",
                lambda match: f"The fixed cohort contains {match.group(1)} discordant pair(s). This identifies browser-related differences, but does not by itself establish overall browser-core superiority or support statistical generalization to the complete WebArena-Verified benchmark.",
            ),
            (
                r"WebArena‑Verified 没有官方 easy/medium/hard 三档。本项目在运行前按最少可见 UI 操作数注册为 1–4、5–7、8\+ 三档，配额 (\d+)/(\d+)/(\d+)。它控制操作链覆盖，不等同于推理难度。",
                lambda m: f"WebArena-Verified has no official easy/medium/hard tiers. Before execution, this project registered tasks by minimum visible UI actions as 1-4, 5-7, and 8+, with quotas of {m.group(1)}/{m.group(2)}/{m.group(3)}. This controls operation-chain coverage and is not equivalent to reasoning difficulty.",
            ),
            (
                r"本轮固定 (\d+) 题，覆盖四个已验证站点。结果只说明当前 CowAgent、模型、任务与运行协议下的浏览器差异；不支持把总分直接解释为浏览器内核等价、全面替代关系或纯内核速度。端到端耗时包含模型、调度、页面等待和工具开销，动作延迟也包含 CDP 往返。",
                lambda match: f"This run fixes {match.group(1)} tasks across four verified sites. Results describe browser differences only under the current CowAgent, model, tasks, and run protocol. Aggregate scores do not establish browser-core equivalence, comprehensive substitutability, or pure engine speed. End-to-end latency includes model, scheduling, page waits, and tool overhead; action latency also includes CDP round trips.",
            ),
            (
                r"(\d+) 个逻辑结果",
                lambda match: f"{match.group(1)} logical results",
            ),
            (
                r"本轮剔除 (\d+) 题：(.+)。",
                lambda match: (
                    f"{match.group(1)} tasks excluded in this run: "
                    f"{match.group(2).replace('、', ', ')}."
                ),
            ),
            (
                r"(\d+) 个任务×浏览器结果均保留最终回答、官方 evaluator 结果、Agent 事件和证据哈希。",
                lambda match: f"All {match.group(1)} task-browser results retain the final answer, official evaluator output, agent events, and evidence hashes.",
            ),
            (
                r"(\d+)/(\d+) 个逻辑结果已覆盖，(\d+) 个未决。它不重新判题，也不是独立语义复核。",
                lambda m: f"Evidence-integrity review covers {m.group(1)}/{m.group(2)} logical results, with {m.group(3)} unresolved. It does not rescore tasks or constitute independent semantic review.",
            ),
            (r"预注册 (\d+) 题", lambda m: f"Preregister {m.group(1)} tasks"),
            (
                r"这 (\d+) 题如何部署并由 Agent 执行",
                lambda m: f"How the {m.group(1)} tasks were deployed and run by the agent",
            ),
            (
                r"WebArena‑Verified 当前提供 Shopping、Shopping Admin、Reddit、GitLab、Wikipedia、Map 六类环境。本轮 (\d+) 题只覆盖前四类；Wikipedia 与 Map 需要额外大型数据卷，因此没有部署，也没有任务进入成绩。",
                lambda m: f"WebArena-Verified provides six environments: Shopping, Shopping Admin, Reddit, GitLab, Wikipedia, and Map. This {m.group(1)}-task run covers only the first four. Wikipedia and Map require additional large data volumes, so they were not deployed and contribute no scored tasks.",
            ),
            (
                r"预注册文件只含任务 ID、层级和入选理由，不含点击序列。CowAgent 收到自然语言目标后，在最多 (.+) 步内循环读取页面观察、请求模型决策并生成下一次 BrowserTool 调用。Chromium 与 Moli 分别连接独立 CDP 浏览器容器；每个逻辑结果保留最终 JSON、Agent 事件流、网络 HAR 和 Playwright trace。",
                lambda m: f"The preregistration contains task IDs, tiers, and selection rationales, but no click sequence. Given a natural-language objective, CowAgent repeatedly reads the page, requests a model decision, and generates the next BrowserTool call for up to {'an unrecorded number of' if m.group(1) == '未记录' else m.group(1)} steps. Chromium and Moli connect to separate CDP browser containers. Every logical result retains the final JSON, agent event stream, network HAR, and Playwright trace.",
            ),
            (
                r"未部署；不在 (\d+) 题",
                lambda m: f"Not deployed; outside the {m.group(1)} tasks",
            ),
            (
                r"给出全部 (\d+) 个逻辑结果的页面与下载路径。",
                lambda m: f"lists page and download paths for all {m.group(1)} logical results.",
            ),
            (
                r"同一 (\d+) 题；同一批本地站点镜像",
                lambda m: f"Same {m.group(1)} tasks; same local site images",
            ),
            (
                r"相同 CPU、内存与最多 (.+) 步；历史视口未统一",
                lambda m: f"Same CPU, memory, and up to {'an unrecorded number of' if m.group(1) == '未记录' else m.group(1)} steps; historical viewports differed",
            ),
            (
                r"这 (\d+) 题由 CowAgent 自主执行。",
                lambda m: f"CowAgent executes all {m.group(1)} tasks autonomously.",
            ),
            (
                r"仓库中的 Browser Trajectory Replay（轨迹重放）固定动作微基准属于另一条实验轨道，不是本报告的参评选手，也不计入这 (\d+) 题成绩。",
                lambda m: f"The repository's fixed-action Browser Trajectory Replay microbenchmark is a separate evaluation track, not a participant in this report, and is excluded from these {m.group(1)} task results.",
            ),
            (
                r"CowAgent \+ (.+?)（reasoning=low）在预注册的 (\d+) 题 WebArena‑Verified 样本上对比 Chromium 与 Moli。",
                lambda match: (
                    f"CowAgent + {match.group(1)} (reasoning=low) compares Chromium and Moli on a preregistered {match.group(2)}-task WebArena-Verified sample."
                ),
            ),
            (r"(\d+) 个完整配对", lambda match: f"{match.group(1)} complete pairs"),
            (
                r"(\d+) 个共同失败任务没有任何成功轨迹。报告只能确认结果未满足官方合同，尚不能证明任务在当前快照可完成，也不能归因给 Agent 或浏览器。",
                lambda match: (
                    f"The {match.group(1)} shared-failure tasks have no successful trajectory in this run. The report establishes only that their official contracts were not met; it does not prove current-snapshot solvability or attribute failure to the agent or browser."
                ),
            ),
            (
                r"(\d+) 个试次均保留回答、官方 evaluator 结果、Agent 事件和证据哈希；每题每浏览器首轮一次，只有失败任务再诊断重试一次。",
                lambda match: (
                    f"All {match.group(1)} attempts retain the answer, official evaluator result, agent events, and evidence hashes. Each task-browser pair has one primary run; only failures receive one diagnostic retry."
                ),
            ),
            (
                r"(\d+)/(\d+) 个试次完成确定性一致性检查，(\d+) 个未决。它不重新判题，也不是独立语义复核。",
                lambda match: (
                    f"{match.group(1)}/{match.group(2)} attempts completed deterministic consistency checks, with {match.group(3)} unresolved. This does not rescore tasks and is not an independent semantic review."
                ),
            ),
            (
                r"(\d+)/(\d+) 题至少有一条自主成功轨迹；其余 (\d+) 题仅有官方验收合同。",
                lambda match: (
                    f"{match.group(1)}/{match.group(2)} tasks have at least one autonomous successful trajectory; the remaining {match.group(3)} have only an official acceptance contract."
                ),
            ),
            (
                r"(\d+) 项规则未通过：(.+)。",
                lambda match: (
                    f"{match.group(1)} rule{'s' if match.group(1) != '1' else ''} failed: "
                    + match.group(2).replace("规则 ", "Rule ").replace("、", ", ")
                    + "."
                ),
            ),
        )
        for pattern, replacement in patterns:
            match = re.fullmatch(pattern, core)
            if match:
                translated = replacement(match)
                break
    if translated is None:
        translated = translate_common_text(core)
        if translated == core:
            for source, target in sorted(
                PHRASES.items(), key=lambda item: len(item[0]), reverse=True
            ):
                translated = translated.replace(source, target)
            if translated == core:
                translated = None
    return leading + (translated if translated is not None else core) + trailing


class Localizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs) -> None:
        self.stack.append(tag)
        self.parts.append(self._tag(tag, attrs))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.parts.append(self._tag(tag, attrs, closed=True))

    @staticmethod
    def _tag(tag: str, attrs, *, closed: bool = False) -> str:
        rendered = []
        for key, value in attrs:
            if value is None:
                rendered.append(key)
                continue
            if key in {"title", "aria-label", "placeholder", "alt", "data-search"}:
                value = translate_text(value)
            rendered.append(f'{key}="{html.escape(value, quote=True)}"')
        suffix = " /" if closed else ""
        return f"<{tag}{(' ' + ' '.join(rendered)) if rendered else ''}{suffix}>"

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            self.stack = self.stack[: len(self.stack) - 1 - self.stack[::-1].index(tag)]
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if any(tag in self.stack for tag in ("pre", "code", "script", "style")):
            self.parts.append(data)
        else:
            self.parts.append(translate_text(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")


def _relative(source: PurePosixPath, target: PurePosixPath) -> str:
    return posixpath.relpath(str(target), str(source.parent))


def _rewrite_url(value: str, source: PurePosixPath, english: PurePosixPath) -> str:
    parsed = urlsplit(html.unescape(value))
    if parsed.scheme or parsed.netloc or value.startswith(("mailto:", "data:", "javascript:")):
        return value
    if not parsed.path:
        return value
    # Generated report pages declare a root-relative <base>, so local links are
    # resolved from the report root even when the source page is nested.
    resolved = PurePosixPath(posixpath.normpath(parsed.path.lstrip("/")))
    is_page = resolved.suffix.lower() in {".html", ".htm"} or not resolved.suffix
    target = PurePosixPath("en") / resolved if is_page else resolved
    return html.escape(urlunsplit(("", "", str(target), parsed.query, parsed.fragment)), quote=True)


def _rewrite_links(page: str, source: PurePosixPath, english: PurePosixPath) -> str:
    pattern = re.compile(r'(?P<prefix>\b(?:href|src)=)(?P<quote>["\'])(?P<url>.*?)(?P=quote)')
    return pattern.sub(
        lambda match: (
            match.group("prefix")
            + match.group("quote")
            + _rewrite_url(match.group("url"), source, english)
            + match.group("quote")
        ),
        page,
    )


def _header(source: PurePosixPath, english: PurePosixPath) -> str:
    links = "".join(
        f'<a href="{html.escape(str(PurePosixPath("en") / path), quote=True)}">{label}</a>'
        for path, label in NAV
    )
    chinese = str(source)
    return (
        '<header class="top"><nav class="wrap">'
        + links
        + f'<a href="{html.escape(chinese, quote=True)}" hreflang="zh-CN">中文</a>'
        + '<button class="theme-toggle" onclick="theme()">Toggle theme</button></nav></header>'
    )


def bilingual_text(zh: str, en: str) -> str:
    """Keep dynamic interface text and its English form beside the same values."""
    return f'<span data-report-en="{html.escape(en, quote=True)}">{html.escape(zh)}</span>'


def localize_html(page: str, source: PurePosixPath) -> str:
    english = PurePosixPath("en") / source
    page = re.sub(r'<span data-report-en="([^"]*)">[^<]*</span>',
                  lambda match: html.escape(html.unescape(match[1])), page)
    page = page.replace('<html lang="zh-CN">', '<html lang="en">', 1)
    page = re.sub(
        r"<title>(.*?) · Browser Use 评测</title>",
        lambda match: (
            "<title>"
            + (
                f"Task {match.group(1)[3:]}"
                if match.group(1).startswith("任务 ")
                else translate_text(match.group(1))
            )
            + " · Browser Use evaluation</title>"
        ),
        page,
        count=1,
    )
    page = re.sub(
        r'<header class="top">.*?</header>',
        "<header data-english-nav></header>",
        page,
        count=1,
        flags=re.S,
    )
    had_base = bool(re.search(r'<base\s+href=["\'][^"\']*["\']>', page, flags=re.I))
    page = re.sub(
        r'<base\s+href=["\'][^"\']*["\']>', "<base data-english-base>", page, count=1, flags=re.I
    )
    parser = Localizer()
    parser.feed(page)
    parser.close()
    localized = _rewrite_links("".join(parser.parts), source, english)
    localized = localized.replace("<header data-english-nav></header>", _header(source, english), 1)
    if had_base:
        base = _relative(english, PurePosixPath(".")) + "/"
        localized = localized.replace("<base data-english-base>", f'<base href="{base}">', 1)
    return localized


def write_english_report(out: Path) -> int:
    """Mirror every Chinese HTML route and return the route count."""
    english_root = out / "en"
    if english_root.exists():
        shutil.rmtree(english_root)
    sources = sorted(
        path for path in out.rglob("*.html") if "en" not in path.relative_to(out).parts
    )
    for source_path in sources:
        source = PurePosixPath(source_path.relative_to(out).as_posix())
        target = out / "en" / Path(str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            localize_html(source_path.read_text(encoding="utf-8"), source), encoding="utf-8"
        )
    return len(sources)
