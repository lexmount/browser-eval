"""Build a route-for-route English mirror of the browser-agent report."""

from __future__ import annotations

import html
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
    ("guide/index.html", "Interpretation"),
    ("adoption/index.html", "Methods"),
    ("reproduce/index.html", "Reproduction"),
    ("changes/index.html", "Changes"),
    ("accounts/index.html", "Accounts and setup"),
)


TEXT = {
    "首页 · Browser Use 评测": "Home · Browser Use evaluation",
    "Browser Use 评测": "Browser Use evaluation",
    "同一自主 Agent、同一模型，只替换浏览器": "One autonomous agent and model; only the browser changes",
    "本轮没有观察到浏览器成功率差异；这证明对照流程可比，不证明两个内核等价。": "This run found no browser success-rate difference. It establishes a comparable controlled run, not browser equivalence.",
    "归因边界：": "Attribution boundary:",
    "任务族覆盖": "Task-family coverage",
    "样本同时包含信息提取、页面导航和状态变更任务；类别来自每题官方最终答案合同。": "The sample covers retrieval, navigation, and state-changing tasks. Each category comes from the task's official outcome contract.",
    "查看这类任务 →": "View this task family →",
    "运行与证据是否完整": "Run and evidence completeness",
    "正式调用：": "Formal executions:",
    "证据完整性审计：": "Evidence integrity audit:",
    "可完成性覆盖：": "Measured-solvability coverage:",
    "任务数": "Tasks",
    "打开逐题横向对照 →": "Open the task-by-task comparison →",
    "记录中的验收差异": "Observed acceptance mismatch",
    "已观察到的验收差异：": "Observed acceptance mismatch:",
    "证据完整性审计": "Evidence integrity audit",
    "已审计": "Audited",
    "记录不一致": "Record inconsistencies",
    "审计结论": "Audit result",
    "先看结论": "Headline result",
    "当前结论：": "Current conclusion:",
    "本轮没有观察到浏览器成功率差异；这证明对照流程可比，不证明两个内核等价。五个共同失败更值得优先检查 Agent 的跨页聚合与最终状态验证。": "This run found no browser success-rate difference. It establishes a comparable controlled run, not browser equivalence. The five shared failures make cross-page aggregation and final-state verification the first agent-level issues to investigate.",
    "单边胜出": "One-browser-only wins",
    "个完整配对": "complete pairs",
    "失败重试恢复": "Failure retry recoveries",
    "不回写主分": "Excluded from pass@1",
    "调用与核验状态": "Execution and review status",
    "调用完整：": "Execution complete:",
    "次计划首轮均有结果；失败项的唯一诊断重试也全部完成。": "planned primary runs have results; every single diagnostic retry after a failure is also complete.",
    "独立核验：": "Independent review:",
    "个试次；未决": "attempts; unresolved",
    "。查看核验轮次 →": ". View review history →",
    "三类任务概览": "Three task families",
    "只在相同任务族与同一批任务内比较；RETRIEVE、NAVIGATE 与 MUTATE 的合同不同。": "Compare browsers within the same task family and task cohort. RETRIEVE, NAVIGATE, and MUTATE use different acceptance contracts.",
    "个任务": "tasks",
    "两种浏览器": "Both browsers",
    "本类首轮": "primary runs in this family",
    "浏览器总览": "Browser overview",
    "首轮完成": "Primary coverage",
    "首轮通过": "Primary passes",
    "失败重试": "Failure retries",
    "审查结果": "Review result",
    "已核验": "reviewed",
    "从结论进入证据": "From result to evidence",
    "对零分歧、共同失败和下一轮设计的完整论证。": "A complete interpretation of zero disagreements, shared failures, and the next evaluation design.",
    "阅读完整解读 →": "Read the interpretation →",
    "筛选并下钻两种浏览器试次。": "Filter tasks and inspect both browsers' attempts.",
    "打开任务库 →": "Open the task library →",
    "首轮、唯一重试和核验状态。": "Primary runs, the single retry, and review status.",
    "打开结果库 →": "Open all results →",
    "逐题配对比总分更重要": "Paired tasks are more informative than the aggregate score",
    "同一任务的两个首轮结果；重试不参与配对主分。": "Both primary results for each task; retries do not enter the paired primary score.",
    "配对结果": "Pair outcome",
    "两者都通过": "Both pass",
    "仅 Chromium": "Chromium only",
    "仅 Moli": "Moli only",
    "两者都失败": "Both fail",
    "本轮未观察到": "Not observed",
    "优先排查共同链路": "Inspect the shared stack first",
    "精确 McNemar 双侧 p=1.000。零个不一致配对意味着当前样本无法估计内核方向性效应。": "Exact two-sided McNemar p=1.000. With no discordant pairs, this sample cannot estimate a directional browser effect.",
    "结论只适用于本轮预注册样本、Agent、模型与运行配置；展示调整不改变测量结果。": "Conclusions apply only to this preregistered sample, agent, model, and runtime configuration. Presentation changes do not alter measured results.",
    "候选 benchmark、选择依据与 30 个预注册任务": "Benchmark candidates, selection rationale, and 30 preregistered tasks",
    "先区分最终结果、动作过程和模型裁判，再谈采用率；同一家族不同变体的成绩不能合并。": "First separate final-outcome evaluation, action prediction, and model judging. Scores from different variants of one benchmark family are not interchangeable.",
    "公开采用信号 Top 3": "Top three public adoption signals",
    "采用次数是公开一手材料中的机构数，不是质量分；未找到 DeepSeek、Kimi 公开的一手 UI browser-use benchmark，不等于内部没有评测。": "Counts are institutions found in primary public sources, not quality scores. No primary public UI browser-use benchmark was found for DeepSeek or Kimi; that does not show they have no internal evaluation.",
    "Mind2Web 的拆分不是 easy / medium / hard": "Mind2Web splits are not easy, medium, and hard",
    "原始 Mind2Web 使用 cross-task、cross-website、cross-domain 衡量泛化；Online‑Mind2Web 没有统一三档；Mind2Web 2 的 short / medium / long 按动作跨度划分。本报告的简单 5、中等 10、困难 15 是预注册的操作长度层级。": "Original Mind2Web uses cross-task, cross-website, and cross-domain splits. Online-Mind2Web has no common three-level difficulty split. Mind2Web 2 uses short, medium, and long action spans. This report's 5/10/15 easy, medium, and hard groups were preregistered by estimated visible operation length.",
    "怎样阅读任务 ground truth": "How to read task ground truth",
    "参考状态与可完成性证据": "Reference status and solvability evidence",
    "“有成功路径证据”不等于存在唯一标准路径；WebArena‑Verified 不要求复刻人工动作序列。": "Evidence of a successful path does not imply one canonical path. WebArena-Verified does not require replaying a human action sequence.",
    "30 个任务": "30 tasks",
    "全文搜索": "Full-text search",
    "任务、意图、答案、错误": "Task, intent, answer, or error",
    "层级": "Tier",
    "站点": "Site",
    "类型": "Type",
    "可完成性": "Solvability",
    "本轮已有成功轨迹": "Successful trajectory observed",
    "仅官方合同": "Official contract only",
    "任务": "Task",
    "最少步": "Minimum steps",
    "入选理由": "Selection rationale",
    "官方任务意图": "Official task intent",
    "可完成性证据": "Solvability evidence",
    "简单": "Easy",
    "中等": "Medium",
    "困难": "Hard",
    "通过": "Pass",
    "失败": "Fail",
    "首轮": "Primary",
    "重试": "Retry",
    "失败诊断重试": "Diagnostic retry",
    "无": "None",
    "官方任务合同、两种浏览器的实际结果与完整自主执行证据。": "Official task contract, both browsers' measured outcomes, and complete autonomous execution evidence.",
    "任务身份": "Task identity",
    "本项目层级": "Project tier",
    "任务类型": "Task type",
    "官方 hard subset": "Official hard subset",
    "本轮可完成性证据": "Solvability evidence in this run",
    "已有成功轨迹": "Successful trajectory observed",
    "尚无成功轨迹": "No successful trajectory observed",
    "公开任务意图": "Public task intent",
    "官方来源：": "Official source:",
    "Ground truth · 官方验收合同": "Ground truth · official acceptance contract",
    "没有唯一参考点击路径。": "There is no single reference click path.",
    "WebArena‑Verified 允许 Agent 自主选择任意 UI 路径；ground truth 是以下最终回答与必要网络副作用的合取。报告因此比较结果合同，而不把某条人工路径当作标准答案。": "WebArena-Verified lets the agent choose any UI path. Ground truth is the conjunction of the final-answer and required network-side-effect contracts below. The report therefore compares outcome contracts rather than treating a human path as the answer key.",
    "程序化验收": "Programmatic acceptance",
    "该规则必须得分 1.0。": "This rule must score 1.0.",
    "匹配选项与结果 schema": "Matching options and result schema",
    "参考状态 · 当前环境是否有成功路径证据": "Reference status · evidence of a successful path in the current environment",
    "有。": "Yes.",
    "本轮至少一个自主试次通过全部官方合同，因此当前站点快照存在已实测的可完成路径。成功轨迹不是唯一标准路径，也不要求另一浏览器复刻。": "At least one autonomous attempt passed every official contract, so this site snapshot has a measured successful path. That trajectory is not the only valid path, and the other browser need not reproduce it.",
    "本轮没有。": "Not in this run.",
    "官方任务包给出了确定性验收合同，但两种浏览器的首轮和唯一诊断重试都未通过。因此本报告不能独立证明当前快照存在可完成路径，也不能把失败直接归因给浏览器；需要另做不参与计分的人工或 oracle 可完成性核验。": "The official task bundle provides a deterministic acceptance contract, but both browsers failed the primary run and the single diagnostic retry. This report therefore cannot independently prove that the current snapshot is solvable or attribute the failure directly to the browser. A separate non-scoring human or oracle readiness check is required.",
    "两种浏览器的全部正式试次": "All formal attempts from both browsers",
    "口径": "Attempt class",
    "官方结果": "Official verdict",
    "端到端耗时": "End-to-end latency",
    "证据": "Evidence",
    "查看试次": "View attempt",
    "共同失败诊断：": "Shared-failure diagnosis:",
    "全部试次结果对照": "All-attempt comparison",
    "实际最终回答": "Actual final answer",
    "失败规则": "Failed rules",
    "为什么这样判": "Verdict rationale",
    "执行过程": "Execution",
    "评最终结果，不评参考点击路径": "Evaluate final outcomes, not a reference click path",
    "唯一成绩来源是未修改的 WebArena‑Verified 1.2.3 evaluator；确定性证据审计只验证记录一致性。": "The unmodified WebArena-Verified 1.2.3 evaluator is the only scoring authority. Deterministic evidence review checks record integrity only.",
    "候选 benchmark 的验收边界": "Acceptance boundaries of candidate benchmarks",
    "评什么": "What it evaluates",
    "验收对象": "Acceptance target",
    "判分": "Scoring",
    "问题": "Limit",
    "本轮判分链": "Scoring chain for this run",
    "预注册 30 题": "Preregister 30 tasks",
    "逐题重置站点": "Reset sites per task",
    "CowAgent 自主规划": "CowAgent plans autonomously",
    "只替换浏览器": "Change only the browser",
    "官方 evaluator 验收": "Run official evaluators",
    "确定性证据复核": "Review deterministic evidence",
    "固定协议": "Fixed protocol",
    "项目": "Item",
    "规则": "Rule",
    "唯一变量": "Only changed variable",
    "主分": "Primary metric",
    "排除": "Exclusions",
    "裁判": "Judge",
    "两类程序化 evaluator 到底检查什么": "What the two programmatic evaluators check",
    "输入": "Input",
    "比较方式": "Comparison",
    "能证明什么": "What it establishes",
    "不能证明什么": "What it does not establish",
    "合取规则：": "Conjunction rule:",
    "从总体配对下钻到每一次执行": "Drill down from paired outcomes to every execution",
    "先定位十个首轮失败的规则与直接原因，再筛选全部 70 次执行并下钻回答、轨迹和日志。": "First locate the exact failed rules for ten primary failures, then filter all 70 executions and inspect answers, trajectories, and logs.",
    "汇总": "Summary",
    "浏览器": "Browser",
    "耗时中位数": "Median latency",
    "耗时 P95": "P95 latency",
    "Tokens 中位数": "Median tokens",
    "耗时包含模型响应、页面等待、站点重置与浏览器执行，不能解释为纯内核速度。": "Latency includes model responses, page waits, site resets, and browser execution. It is not isolated browser-core speed.",
    "同题首轮关系对比": "Paired primary-outcome comparison",
    "每行固定为同一任务的 Chromium 与 Moli 首轮。关系筛选只改变可见行，不产生新分数。": "Each row fixes Chromium and Moli primary runs for the same task. Relationship filters change visible rows only and do not create new scores.",
    "关系": "Relationship",
    "共同通过": "Both pass",
    "共同失败": "Both fail",
    "有分歧": "Disagreement",
    "找到": "Showing",
    "个配对任务": "paired tasks",
    "对照结论": "Pair outcome",
    "查看两种浏览器全部试次": "View all attempts for both browsers",
    "首轮失败定位": "Primary-failure inventory",
    "两种浏览器在同五题失败。下表不是人工猜测：失败环节来自官方 evaluator，补充说明来自 Agent 最终错误和执行记录。": "Both browsers failed the same five tasks. The failure stage comes from the official evaluators; the additional diagnosis comes from the agent's final error and execution record.",
    "首轮试次": "Primary attempt",
    "直接原因": "Recorded mismatch",
    "同题对照": "Same-task comparison",
    "任务与两浏览器对照": "Task and both browsers",
    "70 个试次": "70 attempts",
    "默认只显示失败；可按浏览器、层级、站点、任务类型、首轮/重试、失败环节和全文关键词筛选。": "Failures are shown by default. Filter by browser, tier, site, task type, primary/retry, failure stage, or full-text query.",
    "失败环节": "Failure stage",
    "最终回答": "Final answer",
    "网络副作用": "Network side effect",
    "多项": "Multiple stages",
    "试次": "Attempt",
    "传输重试": "Transport retries",
    "从官方 ground truth、实际回答、规则级判定下钻到 CowAgent 的每一步 BrowserTool 调用。": "Follow the official ground truth, actual answer, and rule-level verdict down to every CowAgent BrowserTool call.",
    "答案对照": "Answer comparison",
    "规则判定": "Rule verdicts",
    "执行轨迹": "Trajectory",
    "运行与证据": "Runtime and evidence",
    "结果与运行": "Outcome and runtime",
    "失败定位": "Failure diagnosis",
    "自主操作": "Autonomous actions",
    "模型 tokens": "Model tokens",
    "模型请求": "Model requests",
    "传输错误": "Transport errors",
    "证据复核": "Evidence review",
    "Ground truth 与实际最终回答": "Ground truth and actual final answer",
    "官方任务合同": "Official task contract",
    "Agent 实际回答": "Agent's actual answer",
    "官方 evaluator 逐规则判定": "Official evaluator verdicts by rule",
    "所有规则都必须得分 1.0；这里使用 evaluator 已归一化的实际值与期望值直接对照。": "Every rule must score 1.0. This section directly compares the evaluator's normalized actual value with the expected value.",
    "官方期望": "Official expectation",
    "实际归一化结果": "Normalized actual result",
    "断言与错误": "Assertions and errors",
    "CowAgent 自主执行轨迹": "CowAgent autonomous trajectory",
    "以下顺序来自本次执行的 Agent 事件流，不是固定动作回放。展开可查看调用参数和输出摘录；完整脱敏日志可下载。": "This sequence comes from the run's agent event stream, not a fixed action replay. Expand a step for arguments and an output excerpt; the complete sanitized log is downloadable.",
    "调用参数": "Call arguments",
    "执行输出节选": "Execution output excerpt",
    "运行、日志与证据": "Runtime, logs, and evidence",
    "最终回答 JSON": "Final answer JSON",
    "Evaluator 明细 JSON": "Evaluator details JSON",
    "动作轨迹 JSON": "Trajectory JSON",
    "脱敏事件日志 JSON": "Sanitized event log JSON",
    "原始证据哈希": "Raw-evidence hashes",
    "指标": "Metric",
    "值": "Value",
    "证据完整性检查": "Evidence completeness checks",
    "检查": "Check",
    "原始证据文件绑定": "Raw evidence file bindings",
    "存在": "Present",
    "字节": "Bytes",
    "公开策略": "Publication policy",
    "返回任务页": "Return to task",
    "个试次均完成确定性证据复核": "attempts received deterministic evidence review",
    "复核不重新判题，也不是 LLM-as-a-judge；官方 evaluator 仍是唯一成绩来源。": "Review does not rescore tasks and is not LLM-as-a-judge. The official evaluator remains the only scoring authority.",
    "核验结论": "Review result",
    "记录试次": "Recorded attempts",
    "已复核": "Reviewed",
    "记分分歧": "Score disagreements",
    "未决": "Unresolved",
    "复核协议": "Review protocol",
    "逐试次覆盖": "Per-attempt coverage",
    "结论": "Outcome",
    "失败检查数": "Failed check count",
    "失败检查项": "Failed checks",
    "两条轨道回答不同问题，不能混分": "Two tracks answer different questions and cannot share a score",
    "固定 BrowserTool 动作轨道与自主 Agent 轨道都有效，但控制变量和结论边界不同。": "A fixed BrowserTool action track and an autonomous-agent track are both useful, but they control different variables and support different conclusions.",
    "仓库中的两个 harness": "The repository's two harnesses",
    "轨道": "Track",
    "Agent 是否自主": "Agent autonomy",
    "回答的问题": "Question answered",
    "Browser Use 官方实践": "Browser Use's published evaluation practice",
    "公开依据": "Public sources",
    "本轮浏览器运行身份、完整任务结果与失败证据入口。": "Runtime identity, complete task results, and failure evidence for this browser.",
    "运行身份": "Runtime identity",
    "架构": "Architecture",
    "镜像 ID": "Image ID",
    "首轮成绩": "Primary score",
    "首轮耗时中位数": "Median primary latency",
    "全部执行记录": "All execution records",
    "首轮决定主分；仅失败任务有一次诊断重试，重试不回写主分。": "The primary run determines pass@1. Only failed tasks receive one diagnostic retry, and retries never replace the primary score.",
    "复现从公开仓库、预注册题集和固定运行身份开始": "Reproduction starts with the public repository, preregistered cohort, and fixed runtime identities",
    "公开包提供脱敏投影；模型回答、Cookie 和完整网络正文不进入 Git 或站点。": "The public bundle provides sanitized projections. Model credentials, cookies, and complete network bodies do not enter Git or the site.",
    "浏览器与代码身份": "Browser and code identities",
    "公开复现入口": "Public reproduction entry points",
    "是代码、题集和锁文件的唯一来源。": "is the canonical source for code, task definitions, and lockfiles.",
    "本地站点账号、模型权限与发布权限分开处理": "Local site accounts, model access, and publication access are separate",
    "这里只说明权限边界，不展示密钥、Cookie、用户名或个人信息。": "This page describes access boundaries without exposing keys, cookies, usernames, or personal information.",
    "权限与安装": "Access and setup",
    "范围": "Scope",
    "本轮做法": "Configuration in this run",
    "取得与安装": "Acquisition and setup",
    "外部付费账号": "Paid external account",
    "当前发布": "Current release",
    "变更": "Change",
    "原因": "Reason",
    "处理": "Resolution",
    "影响": "Impact",
    "报告解读": "Interpretation",
    "评测集": "Dataset",
    "评测方法": "Methodology",
    "结果分析": "Results",
    "核验记录": "Review",
    "方法介绍": "Methods",
    "环境与复现": "Reproduction",
    "变更记录": "Changes",
    "账号与安装": "Accounts and setup",
    "切换主题": "Toggle theme",
    "中文": "中文",
}


PHRASES = {
    "规则 ": "Rule ",
    " 秒": " s",
    " 次 BrowserTool 调用": " BrowserTool calls",
    " 次调用 / ": " calls / ",
    " 次传输尝试": " transport attempts",
    "查看 ": "View ",
    " 步轨迹": "-step trajectory",
    "成功轨迹": "successful trajectory",
    "任务 #": "Task #",
    "个试次": " attempts",
    "个任务": " tasks",
    "实际": "actual",
    "期望": "expected",
    "暂无发布记录。": "No release has been recorded.",
}


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
                r"CowAgent \+ (.+?)（reasoning=low）在预注册的 (\d+) 题 WebArena‑Verified 样本上对比 Chromium 与 Moli。",
                lambda match: f"CowAgent + {match.group(1)} (reasoning=low) compares Chromium and Moli on a preregistered {match.group(2)}-task WebArena-Verified sample.",
            ),
            (r"(\d+) 个完整配对", lambda match: f'{match.group(1)} complete pairs'),
            (
                r"(\d+) 个共同失败任务没有任何成功轨迹。报告只能确认结果未满足官方合同，尚不能证明任务在当前快照可完成，也不能归因给 Agent 或浏览器。",
                lambda match: f"The {match.group(1)} shared-failure tasks have no successful trajectory in this run. The report establishes only that their official contracts were not met; it does not prove current-snapshot solvability or attribute failure to the agent or browser.",
            ),
            (
                r"(\d+) 个试次均保留回答、官方 evaluator 结果、Agent 事件和证据哈希；每题每浏览器首轮一次，只有失败任务再诊断重试一次。",
                lambda match: f"All {match.group(1)} attempts retain the answer, official evaluator result, agent events, and evidence hashes. Each task-browser pair has one primary run; only failures receive one diagnostic retry.",
            ),
            (
                r"(\d+)/(\d+) 个试次完成确定性一致性检查，(\d+) 个未决。它不重新判题，也不是独立语义复核。",
                lambda match: f"{match.group(1)}/{match.group(2)} attempts completed deterministic consistency checks, with {match.group(3)} unresolved. This does not rescore tasks and is not an independent semantic review.",
            ),
            (
                r"(\d+)/(\d+) 题至少有一条自主成功轨迹；其余 (\d+) 题仅有官方验收合同。",
                lambda match: f"{match.group(1)}/{match.group(2)} tasks have at least one autonomous successful trajectory; the remaining {match.group(3)} have only an official acceptance contract.",
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
            for source, target in sorted(PHRASES.items(), key=lambda item: len(item[0]), reverse=True):
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
        self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.parts.append(self.get_starttag_text())

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
        lambda match: match.group("prefix")
        + match.group("quote")
        + _rewrite_url(match.group("url"), source, english)
        + match.group("quote"),
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


def localize_html(page: str, source: PurePosixPath) -> str:
    english = PurePosixPath("en") / source
    page = page.replace('<html lang="zh-CN">', '<html lang="en">', 1)
    page = page.replace('placeholder="任务、意图、答案、错误"', 'placeholder="Task, intent, answer, or error"')
    page = re.sub(
        r'<header class="top">.*?</header>', '<header data-english-nav></header>', page, count=1, flags=re.S
    )
    had_base = bool(re.search(r'<base\s+href=["\'][^"\']*["\']>', page, flags=re.I))
    page = re.sub(r'<base\s+href=["\'][^"\']*["\']>', '<base data-english-base>', page, count=1, flags=re.I)
    parser = Localizer()
    parser.feed(page)
    parser.close()
    localized = _rewrite_links("".join(parser.parts), source, english)
    localized = localized.replace('<header data-english-nav></header>', _header(source, english), 1)
    if had_base:
        base = _relative(english, PurePosixPath(".")) + "/"
        localized = localized.replace('<base data-english-base>', f'<base href="{base}">', 1)
    return localized


def write_english_report(out: Path) -> int:
    """Mirror every Chinese HTML route and return the route count."""
    english_root = out / "en"
    if english_root.exists():
        shutil.rmtree(english_root)
    sources = sorted(path for path in out.rglob("*.html") if "en" not in path.relative_to(out).parts)
    for source_path in sources:
        source = PurePosixPath(source_path.relative_to(out).as_posix())
        target = out / "en" / Path(str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            localize_html(source_path.read_text(encoding="utf-8"), source), encoding="utf-8"
        )
    return len(sources)
