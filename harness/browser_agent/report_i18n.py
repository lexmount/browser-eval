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
    "Chromium 与 Moli：Browser Use 能力实测": "Chromium vs. Moli: a Browser Use capability evaluation",
    "评测方法 · Browser Use 评测": "Methodology · Browser Use evaluation",
    "这 30 题如何部署并由 Agent 执行": "How the 30 tasks were deployed and run by the agent",
    "图 1 · 运行器从官方 WebArena‑Verified 镜像启动本地站点；每个试次重建该题所需站点、等待环境控制器与应用健康、清理浏览器状态并登录测试账号。图是流程说明，真实性证据来自下方运行身份及 70 个试次页的事件、HAR、trace 哈希。": "Figure 1. The runner starts local sites from official WebArena-Verified images. Every attempt rebuilds the sites required by the task, waits for environment-control and application health, clears browser state, and signs in with benchmark accounts. The figure explains the flow; the runtime identities and the events, HAR, and trace hashes on all 70 attempt pages provide the evidence.",
    "不是“先人工部署一次，再连接一个现成网页”。": "This was not a one-time manual deployment followed by connections to an existing website.",
    "本轮运行器直接调用 WebArena‑Verified 的 ContainerManager.start()；每次正式执行和诊断重试前都以 reset=True 启动该题所需的官方站点容器，并停止无关站点。环境控制服务报告 ready 后，还要通过连续应用级健康检查；随后才清 Cookie、登录内置测试账号、打开任务起始页并开始计入 Agent 执行。": "The runner directly calls WebArena-Verified's ContainerManager.start(). Before every primary run and diagnostic retry, it starts the official site containers required by that task with reset=True and stops unrelated sites. After environment control reports ready, the app must pass repeated application-level health checks. Only then does the runner clear cookies, sign in with benchmark accounts, open the task's start page, and begin agent execution.",
    "也不是固定动作回放。": "This was not a fixed action replay either.",
    "预注册文件只含任务 ID、层级和入选理由，不含点击序列。CowAgent 收到自然语言目标后，在最多 30 步内循环读取页面观察、请求模型决策并生成下一次 BrowserTool 调用。Chromium 与 Moli 的试次分别连接到独立 CDP 浏览器容器；每个试次保存模型最终 JSON、Agent 事件流、网络 HAR 和 Playwright trace。": "The preregistration contains task IDs, tiers, and selection rationales, but no click sequences. Given the natural-language objective, CowAgent repeatedly reads the page observation, requests a model decision, and generates the next BrowserTool call for up to 30 steps. Chromium and Moli attempts connect to separate CDP browser containers. Every attempt retains the final model JSON, agent event stream, network HAR, and Playwright trace.",
    "站点来源": "Site source",
    "环节": "Stage",
    "本轮实际做法": "Implementation in this run",
    "可核验证据": "Verifiable evidence",
    "四个官方自托管镜像：Shopping、Shopping Admin、Reddit、GitLab": "Four official self-hosted images: Shopping, Shopping Admin, Reddit, and GitLab",
    "运行清单记录镜像 ID、digest、架构与 WebArena‑Verified 1.2.3 checksum": "The run manifest records image IDs, digests, architectures, and the WebArena-Verified 1.2.3 checksum",
    "逐试次准备": "Per-attempt preparation",
    "重建题目所需容器 → env-ctrl ready → 连续健康检查 → 清状态并登录": "Rebuild required containers → env-ctrl ready → repeated health checks → clear state and sign in",
    "运行器实现与 manifest 的 environment_reset；站点准备失败不会产生可计分试次": "Runner implementation and manifest environment_reset; failed site preparation cannot produce a scored attempt",
    "CowAgent + 模型依据当前页面逐步调用 BrowserTool；无动作脚本": "CowAgent and the model call BrowserTool step by step from the current page; no action script",
    "每个试次页公开按时间排序的 BrowserTool 轨迹和脱敏事件下载": "Every attempt page publishes the ordered BrowserTool trajectory and a sanitized event download",
    "结果验收": "Outcome evaluation",
    "最终 JSON 交给 AgentResponseEvaluator；需要副作用的任务同时检查 HAR": "AgentResponseEvaluator checks the final JSON; tasks requiring side effects also inspect the HAR",
    "每个试次页公开规则级 expected / actual；原始 HAR 与 trace 仅公开 SHA-256 和字节数以避免泄露登录态": "Every attempt page publishes rule-level expected/actual values; raw HAR and trace files expose only SHA-256 and byte counts to protect authenticated state",
    "证据边界：": "Evidence boundary:",
    "事件流能证明 CowAgent 在运行时产生了不同的 BrowserTool 调用序列，站点镜像和运行配置能证明本地环境身份；它们不能公开模型隐藏思维，也不把成功轨迹当成唯一标准路径。": "Event streams establish that CowAgent produced BrowserTool call sequences at runtime, while site images and run configuration establish the local environment identity. They do not expose hidden model reasoning or make a successful trajectory the only valid path.",
    "查看 Chromium 自主轨迹示例": "View a Chromium autonomous trajectory",
    "查看 Moli 自主轨迹示例": "View a Moli autonomous trajectory",
    "核对镜像与运行身份": "Verify image and runtime identities",
    "自托管真实交互": "Self-hosted interactive websites",
    "最终 JSON + 必要网络副作用": "Final JSON plus required network side effects",
    "程序化；无 LLM judge": "Programmatic; no LLM judge",
    "部署重，最适合内核 A/B": "Heavy deployment; strongest fit for browser A/B testing",
    "离线网页快照": "Offline webpage snapshots",
    "每步目标元素与操作": "Target element and action at each step",
    "程序化动作指标": "Programmatic action metrics",
    "评过程模仿": "Evaluates process imitation",
    "136 个真实网站": "136 live websites",
    "动作历史与最终完成": "Action history and final completion",
    "WebJudge + 人工标签": "WebJudge plus human labels",
    "漂移、CAPTCHA、judge 偏差": "Drift, CAPTCHA, and judge bias",
    "实时网站视觉交互": "Visual interaction with live websites",
    "最终回答与执行证据": "Final answer and execution evidence",
    "多模态 LLM + 人工核验": "Multimodal LLM plus human verification",
    "精确复现较弱": "Weaker exact reproducibility",
    "长链多站搜索": "Long-horizon multi-site search",
    "带引用研究答案": "Research answer with citations",
    "rubric Agent-as-a-Judge": "Rubric Agent-as-a-Judge",
    "非 UI 内核首选": "Not the first choice for UI browser cores",
    "100 个混合任务": "100 mixed tasks",
    "最终结果与轨迹": "Final outcomes and trajectories",
    "Google judge LLM": "Google judge LLM",
    "云浏览器条件影响可比性": "Cloud-browser conditions affect comparability",
    "200 个长链多站任务": "200 long-horizon multi-site tasks",
    "rubric 完成度": "Rubric completion",
    "模型评审": "Model grading",
    "成本高": "High cost",
    "官方数据 812 题全部配置 AgentResponseEvaluator，其中 663 题还配置 NetworkEventEvaluator。所有已配置 evaluator 都返回 1.0 才通过。它检查最终结果和必要副作用，不要求复刻参考动作序列。": "All 812 official tasks configure AgentResponseEvaluator, and 663 also configure NetworkEventEvaluator. A task passes only when every configured evaluator returns 1.0. The evaluators inspect final outcomes and required side effects, not reproduction of a reference action sequence.",
    "Agent / 模型": "Agent / model",
    "CowAgent；gpt-5.6-sol；reasoning=low": "CowAgent; gpt-5.6-sol; reasoning=low",
    "同题、同数据、同模型、同提示和资源上限，只替换浏览器": "Same task, data, model, prompt, and resource limits; only the browser changes",
    "每题×浏览器第一次正式执行；二元 pass@1；逐题配对": "First formal run for each task-browser pair; binary pass@1; task-level pairing",
    "首轮失败后最多一次，只做恢复诊断；模型传输失败同请求最多重发一次": "At most one retry after primary failure, used only for recovery diagnosis; at most one resend for a model transport failure",
    "不纳入无法稳定验收的实时任务；首轮排除需大型数据卷的 Wikipedia / Map": "Exclude live tasks without stable acceptance; the pilot also excludes Wikipedia and Map because of their large data volumes",
    "官方程序化 evaluator；无 LLM judge；人工不得改分": "Official programmatic evaluators; no LLM judge; no manual score changes",
    "Agent 最终 JSON": "Agent final JSON",
    "先按 JSON schema 和数据类型归一化，再比较 task_type、status 与 retrieved_data；按题可声明列表有序或无序。": "Normalize by JSON schema and data type, then compare task_type, status, and retrieved_data; each task may declare ordered or unordered lists.",
    "最终回答符合该题公开答案合同。": "The final answer satisfies the task's public answer contract.",
    "没有检查回答是通过哪条 UI 路径得到的，也不评操作效率。": "It does not inspect which UI path produced the answer or evaluate action efficiency.",
    "本次浏览器 HAR": "HAR from this browser attempt",
    "在网络事件中匹配 URL、方法、状态码以及按需声明的 query、POST、响应字段或 Cookie 条件。": "Match URL, method, status code, and, when declared, query, POST, response-field, or cookie conditions in network events.",
    "导航或页面变更产生了指定的可观察副作用。": "Navigation or state changes produced the specified observable side effect.",
    "没有要求复刻固定点击序列；未声明的页面状态不在验收范围内。": "It does not require a fixed click sequence; undeclared page state is outside the acceptance scope.",
    "一题配置的每个 evaluator 都必须得分 1.0，整题才通过。这里没有 LLM-as-a-judge，也没有人工改分。": "Every evaluator configured for a task must score 1.0 for the task to pass. There is no LLM-as-a-judge and no manual score override.",
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
                r"在同一 CowAgent、同一 (.+?)（reasoning=low）和同一 WebArena‑Verified 30 题样本下，对比两种浏览器完成真实网页任务的能力。",
                lambda match: (
                    f"Compare how Chromium and Moli complete real browser tasks with the same CowAgent, {match.group(1)} (reasoning=low), and the same 30-task WebArena-Verified sample."
                ),
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


def localize_html(page: str, source: PurePosixPath) -> str:
    english = PurePosixPath("en") / source
    page = page.replace('<html lang="zh-CN">', '<html lang="en">', 1)
    page = page.replace(
        'placeholder="任务、意图、答案、错误"', 'placeholder="Task, intent, answer, or error"'
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
