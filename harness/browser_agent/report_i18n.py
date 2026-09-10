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
    ("adoption/index.html", "Browsers"),
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
    "优": "Best",
    "更优": "Better",
    "环境与复现": "Reproduction",
    "变更记录": "Changes",
    "账号与安装": "Accounts and setup",
    "切换主题": "Toggle theme",
    "中文": "中文",
}

# Browser-agent-specific prose is intentionally translated here rather than in
# the shared Web Fetch catalog.  Task intent and measured evidence are not
# listed: unmatched task text stays verbatim, and Localizer preserves pre/code
# blocks containing evaluator JSON and agent output.
TEXT.update(
    {
        # Current home page and dataset-only inventory.
        "Chromium 任务通过": "Chromium tasks passed",
        "Moli 任务通过": "Moli tasks passed",
        "需要可完成性核验": "Solvability review required",
        "这是固定选择的 30 题试验，不是从全部 WebArena‑Verified 任务随机抽样；比例只描述本样本，不外推总体能力。": "This is a fixed 30-task experiment, not a random sample of all WebArena-Verified tasks. Percentages describe this sample only and are not population estimates.",
        "逻辑结果：": "Logical results:",
        "筛选并下钻两种浏览器结果。": "Filter tasks and inspect results from both browsers.",
        "60 个逻辑结果": "60 logical results",
        "查看最终判定、耗时、轨迹和核验状态。": "Inspect final verdicts, latency, trajectories, and review status.",
        "同一任务的两个浏览器最终逻辑结果。": "Final logical results from both browsers on the same task.",
        "零个不一致任务意味着当前固定样本无法估计内核方向性效应；不对全部 Browser Use 任务作统计外推。": "With no discordant tasks, this fixed sample cannot estimate a directional browser effect. It does not support statistical generalization to all Browser Use tasks.",
        "评测集· Browser Use 评测": "Dataset · Browser Use evaluation",
        "Mind2Web 家族 · 5 家": "Mind2Web family · 5 organizations",
        "WebArena 家族 · 3 家": "WebArena family · 3 organizations",
        "OpenAI、Anthropic、THUDM / ChatGLM。自托管、可重置、结果验收，更适合浏览器 A/B。": "OpenAI, Anthropic, and THUDM / ChatGLM. Self-hosting, resettable state, and outcome evaluation make it a stronger fit for browser A/B tests.",
        "WebVoyager 家族 · 2 家": "WebVoyager family · 2 organizations",
        "OpenAI、Google DeepMind。真实网站直观，但网页漂移与 LLM judge 降低精确复现性。": "OpenAI and Google DeepMind. Live websites are intuitive, but page drift and LLM judging reduce exact reproducibility.",
        "采用次数来自公开一手材料中的机构去重计数，不是质量分；证据核对日期 2026-09-08。未找到 DeepSeek、Kimi 公开的一手 UI browser-use benchmark，不等于内部没有评测。": "Adoption counts are deduplicated organizations in public primary sources, not quality scores; evidence was checked on 2026-09-08. No primary public UI browser-use benchmark was found for DeepSeek or Kimi, which does not imply that they perform no internal evaluation.",
        "WebArena‑Verified 的 ground truth 不是一条固定点击脚本。": "WebArena-Verified ground truth is not a fixed click script.",
        "每题声明一个结构化最终回答合同，并在需要确认页面副作用时附加一个或多个网络事件合同。所有合同都通过才记 1 分。任务详情同时展示合同、两种浏览器的actual回答、失败规则和 CowAgent 自主轨迹。": "Each task declares a structured final-answer contract and, when page side effects must be verified, one or more network-event contracts. Every contract must pass for a score of 1. Task details link the contract to both browsers' actual answers, failed rules, and autonomous CowAgent trajectories.",
        "题集范围": "Dataset scope",
        "本轮在模型执行前固定 30 题，覆盖四个已部署站点以及 RETRIEVE、NAVIGATE、MUTATE 三种最终合同。这里仅描述题目与验收规则；通过率、耗时、轨迹和可完成性观察统一放在结果分析中。": "The 30 tasks were fixed before model execution. They cover four deployed sites and three outcome contracts: RETRIEVE, NAVIGATE, and MUTATE. This page defines tasks and acceptance rules only; pass rates, latency, trajectories, and solvability observations belong in Results.",
        "层级 ↕": "Tier ↕",
        "站点 ↕": "Site ↕",
        "最少步 ↕": "Minimum steps ↕",
        "入选理由 ↕": "Selection rationale ↕",
        "官方任务意图 ↕": "Official task intent ↕",
        "官方 Hard ↕": "Official hard subset ↕",
        "任务合同 ↕": "Task contract ↕",
        "查看任务合同": "View task contract",
        "是": "Yes",
        "否": "No",

        # Deployment, timing, browser matrix, and participant framing.
        "图 1 · 从官方站点镜像、逐题隔离与健康检查，到 CowAgent 观察—决策—操作闭环，再到 Chromium/Moli 对照和官方程序化验收。窄屏可横向滚动查看。": "Figure 1. From official site images, per-task isolation, and health checks through CowAgent's observe-decide-act loop, then Chromium/Moli comparison and official programmatic evaluation. Scroll horizontally on narrow screens.",
        "站点确实部署在本机，不是公网代站。": "The sites were deployed locally; they were not public-web substitutes.",
        "macOS 上的 Docker 由 Colima 虚拟机承载；Shopping、Shopping Admin、Reddit、GitLab 四个官方镜像把应用端口发布到 Colima/Docker 宿主。Chromium 与 Moli 各自在独立浏览器容器中运行，通过容器内解析到的宿主网关": "On macOS, Docker runs inside a Colima VM. The four official Shopping, Shopping Admin, Reddit, and GitLab images publish their application ports to the Colima/Docker host. Chromium and Moli run in separate browser containers and reach those ports through the host gateway resolved inside each container",
        "访问这些端口。这个地址只是本轮私有网络中的宿主入口，不是评测网站或固定产品配置。": "to access the sites. This address is only the host entry point in this run's private network; it is neither a benchmark website nor a fixed product configuration.",
        "预注册文件只含任务 ID、层级和入选理由，不含点击序列。CowAgent 收到自然语言目标后，在最多 30 步内循环读取页面观察、请求模型决策并生成下一次 BrowserTool 调用。Chromium 与 Moli 分别连接独立 CDP 浏览器容器；每个逻辑结果保留最终 JSON、Agent 事件流、网络 HAR 和 Playwright trace。": "The preregistration contains task IDs, tiers, and selection rationales, but no click sequence. Given a natural-language objective, CowAgent repeatedly reads the page observation, requests a model decision, and generates the next BrowserTool call for up to 30 steps. Chromium and Moli connect to separate CDP browser containers. Each logical result retains the final JSON, agent event stream, network HAR, and Playwright trace.",
        "本轮访问地址": "Address used in this run",
        "应用端口": "Application port",
        "环境控制端口": "Environment-control port",
        "状态": "Status",
        "本轮已部署": "Deployed in this run",
        "未部署；不在 30 题": "Not deployed; outside the 30 tasks",
        "事件流能证明 CowAgent 在准备完成后产生了 BrowserTool 调用序列；镜像 ID、digest、代码提交与网关记录限定了本地运行身份。当前证据没有独立的逐次环境准备 receipt，因此报告不把“每次重置均成功”当成已经独立核验的事实。": "Event streams establish that CowAgent produced BrowserTool calls after preparation. Image IDs, digests, the code commit, and gateway records bound the local runtime identity. The evidence has no independent per-execution preparation receipt, so the report does not treat every reset as independently verified.",
        "结果口径": "Result unit",
        "每题×浏览器一个最终逻辑结果；二元通过率；逐题配对": "One final logical result per task-browser pair; binary pass rate; task-level pairing",
        "执行上限": "Execution limit",
        "代码最多允许两次执行；本报告按冻结运行协议选取正式结果，实际执行耗时和模型用量累计": "The code permits at most two executions. The frozen run protocol selects the official result, while actual execution latency and model usage are cumulative.",
        "不纳入无法稳定验收的实时任务；本轮排除需大型数据卷的 Wikipedia / Map": "Exclude live tasks without stable acceptance; this run excludes Wikipedia and Map because they require large data volumes",
        "结果分析 · Browser Use 评测": "Results · Browser Use evaluation",
        "从总体配对下钻到每个逻辑结果": "Drill down from the paired summary to every logical result",
        "先看 30 个同题配对，再定位 10 个失败结果的官方规则、actual回答、轨迹和日志。": "Start with 30 same-task pairs, then inspect the official rules, actual answers, trajectories, and logs for the 10 failed results.",
        "通过率": "Pass rate",
        "Agent 耗时中位数": "Median agent latency",
        "Agent 耗时 P95": "P95 agent latency",
        "BrowserTool 耗时中位数": "Median BrowserTool latency",
        "BrowserTool 耗时 P95": "P95 BrowserTool latency",
        "Agent 端到端耗时": "Agent end-to-end latency",
        "从浏览器会话准备开始，到 CowAgent 完成、evaluator 所需证据落盘前结束；包含登录、模型推理、调度、页面等待、BrowserTool 与收尾，不包含此前的 Docker 站点重置。": "Measured from browser-session preparation until CowAgent completes and before evaluator evidence is persisted. It includes sign-in, model inference, orchestration, page waits, BrowserTool, and cleanup, but excludes the preceding Docker site reset.",
        "BrowserTool 执行耗时": "BrowserTool execution latency",
        "是事件流中每次 browser 工具调用墙钟时间之和，包含工具封装、CDP 往返与页面等待，仍不是纯内核 CPU 时间。": "The sum of wall-clock durations for browser tool calls in the event stream. It includes tool-wrapper, CDP round-trip, and page-wait overhead, so it is still not pure browser-core CPU time.",
        "浏览器动作耗时拆分": "Browser action latency breakdown",
        "按 CowAgent 发出的 BrowserTool action 聚合调用次数与墙钟时间，用于定位 navigate、click、input、snapshot 等动作在哪个浏览器上更慢。该指标比 Agent 总耗时更接近浏览器执行层，但仍包含工具与 CDP 开销。": "Aggregate call counts and wall-clock time by BrowserTool action emitted by CowAgent to locate whether navigate, click, input, snapshot, and other actions are slower in one browser. This metric is closer to the browser execution layer than total agent latency, but still includes tool and CDP overhead.",
        "动作": "Action",
        "浏览器两两比较": "Pairwise browser comparisons",
        "新增浏览器后自动生成每一对浏览器的同题比较；每一行只使用双方都完成的任务，避免把三种以上浏览器压成一个含义不清的总胜负。": "Adding a browser automatically creates a same-task comparison for every browser pair. Each row uses only tasks completed by both browsers, avoiding an ambiguous aggregate win/loss label across three or more browsers.",
        "浏览器对": "Browser pair",
        "共同任务": "Shared tasks",
        "左侧单独通过": "Left-only passes",
        "右侧单独通过": "Right-only passes",
        "同题浏览器结果矩阵": "Same-task browser result matrix",
        "每行是一道任务，浏览器作为可扩展列生成；以后增加浏览器时沿用同一张矩阵，不再把页面结构写死为 Chromium 与 Moli。关系筛选只改变可见行，不产生新分数。": "Each row is one task, with browsers generated as extensible columns. Future browsers use the same matrix instead of hard-coding Chromium and Moli into the page structure. Relationship filters change visible rows only and create no new scores.",
        "全部通过": "All pass",
        "全部失败": "All fail",
        "结果有分歧": "Mixed results",
        "横向关系": "Cross-browser outcome",
        "任务定义": "Task definition",
        "任务合同": "Task contract",
        "失败结果定位": "Failed-result inventory",
        "默认只显示失败；可按浏览器、层级、站点、任务类型、失败环节和全文关键词筛选。": "Failures are shown by default. Filter by browser, tier, site, task type, failure stage, or full-text query.",
        "动作数": "Actions",

        # Result pages and audit boundaries.
        "Agent 端到端耗时包含会话准备、登录、模型推理、调度、工具执行和收尾，不包含 Docker 站点重置；BrowserTool 执行耗时是全部 browser 工具调用的墙钟时间之和，不等同于纯内核时间。": "Agent end-to-end latency includes session preparation, sign-in, model inference, orchestration, tool execution, and cleanup, but excludes Docker site reset. BrowserTool execution latency is the sum of wall-clock time for all browser tool calls and is not pure browser-core time.",
        "公开包包含脱敏的回答、evaluator、事件流与轨迹。原始 HAR 和 Playwright trace 可能携带登录态、Cookie、页面快照或请求正文，因此不公开；其字节数和 SHA-256 保留用于证明证据绑定。": "The public bundle contains sanitized answers, evaluator records, event streams, and trajectories. Raw HAR and Playwright traces may contain authenticated state, cookies, page snapshots, or request bodies, so they are withheld; byte counts and SHA-256 hashes retain the evidence binding.",
        "整次 Agent 运行的容器计量只作诊断；不同进程模型下不直接比较内存或 PID。": "Whole-run container measurements are diagnostic only; memory and PID are not compared directly across different process models.",
        "全部官方 evaluator 均通过。": "All official evaluators passed.",
        "核验记录 · Browser Use 评测": "Review · Browser Use evaluation",
        "证据完整性与独立结果复核是两个指标；本轮只完成前者，官方 evaluator 仍是唯一成绩来源。": "Evidence integrity and independent result review are separate metrics. This run completed only the former; the official evaluator remains the sole scoring authority.",
        "证据完整性": "Evidence integrity",
        "文件、哈希与分数一致性": "File, hash, and score consistency",
        "独立结果复核": "Independent result review",
        "尚未进行语义盲审": "No semantic blind review performed",
        "完整性未决": "Integrity issues unresolved",
        "完整性检查确认回答、HAR、Agent trace、事件和 evaluator 明细存在，并核对哈希、分数、状态与官方版本。": "Integrity checks confirm that answers, HAR, agent traces, events, and evaluator details exist, and verify hashes, scores, statuses, and the official version.",
        "复核边界：": "Review boundary:",
        "本轮没有第二名人工或模型对结果做盲审，因此“0 个完整性未决”不能解读为结果已经独立语义复核。": "No second human or model blind-reviewed the results, so '0 unresolved integrity issues' must not be interpreted as independent semantic review.",
        "审计项": "Audit item",
        "回答、事件、HAR、trace 与 evaluator 文件完整且哈希绑定": "Answers, events, HAR, traces, and evaluator files are complete and hash-bound",
        "成绩来源": "Scoring authority",
        "只接受未修改的 WebArena‑Verified evaluator 输出": "Accept only unmodified WebArena-Verified evaluator output",
        "一致性": "Consistency",
        "公开状态、分数、官方版本与原始记录一致": "Published status, score, and official version match the source record",
        "争议处理": "Dispute handling",
        "任何不一致或缺失证据均阻止发布；人工不得改分": "Any inconsistency or missing evidence blocks release; humans may not change scores",
        "逐结果覆盖": "Per-result coverage",

        # Participants, reproduction, and account boundaries.
        "方法介绍 · Browser Use 评测": "Participants · Browser Use evaluation",
        "本轮 A/B 的唯一变化项是浏览器实现；CowAgent、模型、任务、资源上限、站点与 evaluator 都是共同实验条件。": "The browser implementation is the only changed variable in this A/B test. CowAgent, the model, tasks, resource limits, sites, and evaluators are shared experimental conditions.",
        "两个参评浏览器": "Two browser participants",
        "参评浏览器": "Browser participant",
        "运行版本": "Runtime version",
        "接入方式": "Integration",
        "本轮成绩": "Result in this run",
        "独立容器 · CDP · CowAgent BrowserTool": "Dedicated container · CDP · CowAgent BrowserTool",
        "更完整地说，一个可复现的“参评方法”是整套 Agent 栈；但本轮所有栈字段都保持一致，只有浏览器后端不同，所以报告按浏览器命名选手。不能把 CowAgent、gpt‑5.6‑sol 或 WebArena‑Verified 也算成独立选手。": "Strictly, a reproducible participant is the whole agent stack. In this run every stack field is held constant except the browser backend, so participants are named by browser. CowAgent, gpt-5.6-sol, and WebArena-Verified are not additional participants.",
        "共同实验条件": "Shared experimental conditions",
        "栈字段": "Stack field",
        "本轮固定值": "Fixed value in this run",
        "角色": "Role",
        "自主观察、规划并调用 BrowserTool": "Observe, plan, and call BrowserTool autonomously",
        "模型": "Model",
        "为两个选手生成决策": "Generate decisions for both participants",
        "任务与站点": "Tasks and sites",
        "同一 30 题；同一批本地站点镜像": "Same 30 tasks; same local site images",
        "提供一致输入与网页状态": "Provide identical inputs and website state",
        "动作接口": "Action interface",
        "把 Agent 动作交给浏览器": "Deliver agent actions to the browser",
        "验收": "Evaluation",
        "WebArena‑Verified 1.2.3 官方 evaluator": "Official WebArena-Verified 1.2.3 evaluator",
        "对最终 JSON 与必要网络副作用判分": "Score final JSON and required network side effects",
        "资源与上限": "Resources and limits",
        "相同 CPU、内存、viewport 与最多 30 步": "Same CPU, memory, viewport, and 30-step limit",
        "控制运行预算": "Control the execution budget",
        "这 30 题由 CowAgent 自主执行。": "CowAgent executes all 30 tasks autonomously.",
        "仓库里的 browser_kernel 固定动作微基准属于另一条实验轨道，不是本报告的参评选手，也不计入这 30 题成绩。": "The repository's fixed-action browser_kernel microbenchmark is a separate experimental track, not a participant in this report, and is excluded from these 30 task results.",
        "Browser Use 公开使用 BU Bench V1、Odysseys、Online‑Mind2Web 和 Stealth Bench。可借鉴的是运行前确认任务可完成、保存逐题 trace、断点续跑，并把站点不可完成与 Agent 真失败分开。Stealth Bench 测反机器人检测，不能替代自主任务成功率。": "Browser Use publicly uses BU Bench V1, Odysseys, Online-Mind2Web, and Stealth Bench. Useful practices include confirming task solvability before execution, retaining per-task traces, resuming interrupted runs, and separating unsolvable sites from genuine agent failures. Stealth Bench measures bot detection and cannot replace autonomous task success rate.",
        "环境与复现 · Browser Use 评测": "Reproduction · Browser Use evaluation",
        "协议": "Protocol",
        "运行配置选择集 SHA-256": "Run-config selection SHA-256",
        "当前选择集 SHA-256": "Current selection SHA-256",
        "部署环境与网络拓扑": "Deployment environment and network topology",
        "本轮 Docker daemon 运行在 macOS 上的 Colima 虚拟机中。站点容器把应用端口发布到 Docker 宿主；两个浏览器容器通过其容器内": "The Docker daemon for this run operates in a Colima VM on macOS. Site containers publish application ports to the Docker host; both browser containers access them through the",
        "对应的网关地址访问这些端口。运行时记录的地址是": "gateway corresponding to host.docker.internal inside each container. The address recorded at runtime is",
        "。它属于本轮私有网络，不应复制为其他机器的固定地址。": ". It belongs to this run's private network and must not be copied as a fixed address for another machine.",
        "层": "Layer",
        "本轮身份": "Identity in this run",
        "作用": "Purpose",
        "宿主": "Host",
        "运行 Colima 与报告 runner": "Runs Colima and the report runner",
        "承载站点和浏览器容器": "Hosts site and browser containers",
        "浏览器到宿主网关": "Browser-to-host gateway",
        "访问站点容器发布端口": "Accesses published ports from site containers",
        "ARM 兼容模式": "ARM compatibility mode",
        "未启用": "Disabled",
        "运行部分 amd64 PHP 站点镜像": "Runs selected amd64 PHP site images",
        "站点镜像": "Site image",
        "镜像引用": "Image reference",
        "逐执行的环境准备 receipt 尚未包含在这批冻结证据中；现有记录可核对运行器配置与镜像身份，但不能独立重放每次 reset/ready/health 的完整证明链。": "Per-execution environment-preparation receipts are absent from this frozen evidence. Existing records verify runner configuration and image identities but cannot independently replay the complete reset/ready/health proof chain for every execution.",
        "每个逻辑结果的脱敏回答、evaluator、事件和轨迹位于": "Sanitized answers, evaluator records, events, and trajectories for each logical result are under",
        "给出全部 60 个逻辑结果的页面与下载路径。": "lists page and download paths for all 60 logical results.",
        "账号 · Browser Use 评测": "Accounts · Browser Use evaluation",
        "不展示密钥、Cookie 或真实个人信息；官方合同中的内置测试邮箱和用户名作为合成 ground truth 保留。": "Keys, cookies, and real personal information are not shown. Built-in test emails and usernames in official contracts remain as synthetic ground truth.",
        "权限与费用": "Access and cost",
        "四个自托管站点": "Four self-hosted sites",
        "官方 Docker 镜像与内置测试账号；每题前重置": "Official Docker images and built-in test accounts; reset before each task",
        "不需要": "Not required",
        "模型 API": "Model API",
        "需要相应 API 权限": "Corresponding API access required",
        "当前团队无权限": "Current team lacks access",
        "只发布脱敏静态报告": "Publishes only the sanitized static report",
        "发布者需已有站点权限": "Publisher must already have site access",
        "字段": "Field",
        "任务成绩": "Task score",
        "逻辑结果耗时中位数": "Median logical-result latency",
        "版本与镜像身份用于限定复现边界，不参与评分。": "Version and image identity bound reproducibility and do not affect scoring.",
        "全部逻辑结果": "All logical results",
        "每题只展示一个最终逻辑结果；端到端耗时与模型用量包含获得该结果所需的全部执行。": "Each task shows one final logical result. End-to-end latency and model usage include every execution needed to obtain it.",
        "Agent 耗时": "Agent latency",
        "BrowserTool 耗时": "BrowserTool latency",
        "失败原因": "Failure reason",
        "60 个任务×浏览器结果均保留最终回答、官方 evaluator 结果、Agent 事件和证据哈希。": "All 60 task-browser results retain the final answer, official evaluator result, agent events, and evidence hashes.",
        "60/60 个逻辑结果已覆盖，0 个未决。它不重新判题，也不是独立语义复核。": "Evidence-integrity checks cover all 60 logical results with no unresolved integrity issue. They do not rescore tasks or constitute independent semantic review.",
        "60/60 个逻辑结果完成证据完整性审计": "Evidence integrity checked for all 60 logical results",
        "每题声明一个结构化最终回答合同，并在需要确认页面副作用时附加一个或多个网络事件合同。所有合同都通过才记 1 分。任务页只解释合同；各浏览器的实际回答、失败规则和 CowAgent 自主轨迹统一放在结果页。": "Each task declares a structured final-answer contract and, where required, one or more network-event contracts for page side effects. Every contract must pass for a score of 1. Task pages explain only the contract; browser answers, failed rules, and CowAgent trajectories appear on result pages.",
        "本轮在模型执行前固定 30 题，覆盖四个已部署站点以及 RETRIEVE、NAVIGATE、MUTATE 三种最终合同。这里仅描述题目与验收规则；通过率、耗时、轨迹和可完成性观察统一放在结果分析中。": "The 30 tasks were fixed before model execution. They cover four deployed sites and three outcome contracts: RETRIEVE, NAVIGATE, and MUTATE. This page describes tasks and acceptance rules only; pass rates, latency, trajectories, and solvability observations appear in Results.",
        "运行器代码在每个执行前调用": "Before each execution, the runner calls",
        "先看 30 个同题配对，再定位 10 个失败结果的官方规则、实际回答、轨迹和日志。": "Start with the 30 same-task comparisons, then inspect the official rules, actual answers, trajectories, and logs for the 10 failed results.",
        "Agent 链路耗时中位数": "Median agent-stack latency",
        "Agent 链路耗时 P95": "P95 agent-stack latency",
        "Agent 链路累计耗时": "Cumulative agent-stack latency",
        "Agent 链路累计": "agent-stack cumulative",
        "BrowserTool 累计耗时": "Cumulative BrowserTool latency",
        "BrowserTool 累计": "BrowserTool cumulative",
        "把获得该任务结果实际发生的执行相加；每次从浏览器会话准备开始，到 CowAgent 完成、验收所需证据落盘前结束，包含登录、模型推理、调度、页面等待、BrowserTool 与收尾，不包含此前的 Docker 站点重置。": "sums all execution used to obtain the task result. Each execution runs from browser-session preparation until CowAgent completes and acceptance evidence is persisted. It includes sign-in, model inference, orchestration, page waits, BrowserTool, and cleanup, but excludes the preceding Docker site reset.",
        "是事件流中全部 browser 工具调用墙钟时间之和，包含工具封装、CDP 往返与页面等待，仍不是纯内核 CPU 时间。": "is the sum of wall-clock durations for all browser tool calls in the event stream. It includes wrapper, CDP round-trip, and page-wait overhead, so it is not pure browser-core CPU time.",
        "次调用": "calls",
        "次动作": "actions",
        "次模型请求": "model requests",
        "如何增加下一名选手": "How to add another participant",
        "原版 Mind2Web、Online-Mind2Web 与 Mind2Web 2 的任务和评分器不同，不能合并到同一成绩表。": "Original Mind2Web, Online-Mind2Web, and Mind2Web 2 use different tasks and graders and must not be merged into one score table.",
        "新增浏览器时，需要注册其容器镜像、版本与 CDP 端点，并在同一冻结任务集和共同条件下形成完整结果。总榜按浏览器逐行扩展；同题矩阵按浏览器增加列；统计比较按浏览器对单独生成，不能把多个浏览器压成一个总胜负。": "Register the new browser's container image, version, and CDP endpoint, then complete the same frozen task set under the shared controls. The leaderboard adds a browser row, the task matrix adds a browser column, and statistical comparisons are generated per browser pair rather than collapsing multiple browsers into one win/loss label.",
        "先看总体与同题比较，再通过筛选下钻到每个结果的官方规则、实际回答、轨迹和日志。": "Start with aggregate and same-task comparisons, then use filters to inspect each result's official rules, actual answer, trajectory, and logs.",
        "Agent 链路耗时": "Agent-stack latency",
        "均值": "Mean",
        "总耗时": "Total latency",
        "单次均值": "Mean per call",
        "调用数": "Call count",
        "更优": "Better",
        "标记方向明确且数值更优的指标：通过数与通过率越高越优，耗时与 Tokens 越低越优；数值相同时不标记。": "marks metrics with a clear direction and a better value: higher pass counts and rates are better, while lower latency and token usage are better. Ties are not marked.",
        "按 CowAgent 发出的 BrowserTool action 聚合总墙钟时间、单次均值与调用数。耗时较低者标记为更优；调用数描述 Agent 行为，不判优。该指标比 Agent 总耗时更接近浏览器执行层，但仍包含工具与 CDP 开销。": "Aggregate total wall-clock time, mean per call, and call count by BrowserTool action emitted by CowAgent. Lower latency is marked as better; call count describes agent behavior and is not ranked. This metric is closer to the browser execution layer than total agent latency but still includes tool and CDP overhead.",
        "核验与修订记录": "Review and revision history",
        "按轮次记录核验范围、发现、正式处理和遗留事项；单个任务的判定证据位于结果详情页。": "Each round records its scope, findings, formal corrections, and remaining items. Per-task verdict evidence remains on result detail pages.",
        "当前状态": "Current status",
        "发布门禁：": "Release gate:",
        "证据文件、哈希、分数、状态、官方版本与报告链接已通过程序化一致性检查。": "Evidence files, hashes, scores, statuses, the official version, and report links passed programmatic consistency checks.",
        "官方 WebArena‑Verified evaluator 是成绩来源；核验记录只说明报告和实现经过了哪些检查与修订，不替代任务判分。": "The official WebArena-Verified evaluator is the scoring authority. This history records checks and revisions to the implementation and report; it does not replace task scoring.",
        "核验轮次": "Review rounds",
        "核验范围": "Scope",
        "发现": "Findings",
        "处理": "Corrections",
        "遗留事项": "Remaining items",
        "已处理": "Resolved",
        "无阻塞项。": "No blocking items.",
        "参评浏览器 · Browser Use 评测": "Browser participants · Browser Use evaluation",
        "参评浏览器与统一实验条件": "Browser participants and shared experimental controls",
        "Chromium 与 Moli 作为浏览器后端参与同题对照；Agent、模型、任务、资源上限、站点与 evaluator 保持一致。": "Chromium and Moli participate as browser backends in a same-task comparison; the agent, model, tasks, resource limits, sites, and evaluators are held constant.",
        "参评浏览器是本轮唯一实验变量；完整 Agent 栈的其余组成列为统一实验条件。": "The browser participant is the only experimental variable in this run; all other components of the agent stack are shared controls.",
        "逻辑结果耗时 P50": "P50 logical-result latency",
        "WebArena‑Verified 当前提供 Shopping、Shopping Admin、Reddit、GitLab、Wikipedia、Map 六类环境。本轮 30 题只覆盖前四类；Wikipedia 与 Map 需要额外大型数据卷，因此没有部署，也没有任务进入成绩。": "WebArena-Verified currently provides six environments: Shopping, Shopping Admin, Reddit, GitLab, Wikipedia, and Map. This 30-task run covers only the first four. Wikipedia and Map require additional large data volumes, so they were not deployed and contribute no scored tasks.",
        "只保留该题所需站点；env-ctrl 报告 ready 后，再做连续应用健康检查、清浏览器状态、登录测试账号并打开起始页。当前冻结证据保存了代码提交、运行协议和镜像身份；事件流从环境准备完成后开始，因此逐次 reset、ready 与 health 结果未形成独立绑定的准备回执。新版运行协议已要求保存该回执。": "and keeps only the sites required by the task. After env-ctrl reports ready, it performs repeated application-health checks, clears browser state, signs in with test accounts, and opens the start page. The frozen evidence records the code commit, protocol, and image identities; events begin after preparation, so per-execution reset, ready, and health outcomes do not have independently bound preparation receipts. The current protocol now requires that receipt.",
        "报告模型与比较口径核验": "Report model and comparison review",
        "统计摘要、优胜表达、结果下钻、核验记录和参评浏览器说明。": "Summary statistics, winner presentation, result drill-down, review history, and browser-participant description.",
        "统计标签重复表达 P50；比较表缺少方向一致的优胜标记；结果页存在重复失败清单；核验页承担了逐结果清单职责。": "The statistics duplicated P50 terminology; comparison tables lacked directionally consistent winner marks; the results page duplicated the failure inventory; and the review page carried a per-result listing.",
        "统一为均值、P50、P95；按指标方向突出唯一优胜者；删除重复失败清单；核验页改为按轮次记录范围、发现、处理和遗留事项。": "Standardized on mean, P50, and P95; marked unique winners by metric direction; removed the duplicate failure inventory; and changed the review page to round-based scope, findings, corrections, and remaining items.",
        "当前固定样本没有浏览器单边通过任务；优胜标记主要反映耗时与 Tokens，不改变官方任务成绩。": "The fixed sample has no browser-only pass. Winner marks therefore mainly reflect latency and token usage and do not change official task scores.",
        "部署证据与计时边界核验": "Deployment evidence and timing-boundary review",
        "本地站点部署、Colima 网络路径、环境准备证据、Agent 与 BrowserTool 计时。": "Local site deployment, Colima network path, environment-preparation evidence, and Agent/BrowserTool timing.",
        "环境拓扑与网关含义缺少集中说明；历史总耗时边界描述不准确；逐执行环境准备没有独立回执。": "The environment topology and gateway lacked a consolidated explanation; the historical total-latency boundary was inaccurate; and per-execution environment preparation had no independent receipt.",
        "补充部署流程图、端口和镜像身份；区分 Agent 链路与 BrowserTool 墙钟时间；新版 runner 保存环境准备和分阶段计时。": "Added the deployment flow, ports, and image identities; separated Agent-stack and BrowserTool wall time; and made the current runner persist environment preparation and phase timings.",
        "本批冻结结果不具备逐执行准备回执，报告仅按现有证据声明可复现边界。": "This frozen run lacks per-execution preparation receipts, so the report states reproducibility boundaries only to the extent supported by existing evidence.",
        "运行器与证据完整性核验": "Runner and evidence-integrity review",
        "模型响应校验、BrowserTool 清理、HAR 留存、证据目录和恢复执行。": "Model-response validation, BrowserTool cleanup, HAR retention, evidence directories, and resume behavior.",
        "异常清理可能遮蔽原始错误；HAR 停止操作缺少幂等保护；异常响应和原始证据目录需要更严格保护。": "Cleanup exceptions could mask primary failures; HAR stop lacked idempotency; and malformed responses and raw-evidence directories needed stricter protection.",
        "隔离清理异常、使 HAR 停止幂等、加强模型响应结构校验，并阻止原始浏览器证据进入 Git。": "Isolated cleanup failures, made HAR stop idempotent, strengthened model-response validation, and prevented raw browser evidence from entering Git.",
        "统一比较统计与核验记录": "Standardize comparison statistics and review history",
        "比较摘要统一展示均值、P50、P95，并按指标方向突出唯一优胜者；结果页删除与筛选结果重复的失败清单；核验页改为按轮次记录核验与修订。Chromium 与 Moli 的官方成绩仍均为 25/30。": "Comparison summaries now show mean, P50, and P95 and mark unique winners according to metric direction. The results page no longer duplicates the filterable failure inventory, and the review page records review and revision rounds. Official scores remain 25/30 for both Chromium and Moli.",
        "统计摘要": "Summary statistics",
        "耗时和 Tokens 的集中趋势与尾部表现未采用统一展示结构。": "Latency and token central tendency and tail behavior did not use a consistent presentation.",
        "每个指标统一展示均值、P50、P95；P50 不再以另一个名称重复出现。": "Each metric now shows mean, P50, and P95; P50 is no longer duplicated under another label.",
        "只改变统计展示，官方任务分数、分母和证据不变。": "Only statistical presentation changed; official task scores, denominators, and evidence are unchanged.",
        "比较表达": "Comparison presentation",
        "读者需要逐格判断数值方向。": "Readers previously had to infer metric direction cell by cell.",
        "通过数与通过率按更高值标优，耗时与 Tokens 按更低值标优；平局和调用次数保持中性。": "Higher pass counts and rates and lower latency and token usage are marked as better; ties and call counts remain neutral.",
        "优胜标记是描述性比较，不改变任务判定。": "Winner marks are descriptive comparisons and do not change task verdicts.",
        "页面职责": "Page responsibilities",
        "失败清单与结果筛选重复，核验页混入逐结果覆盖表。": "The failure inventory duplicated result filters, and the review page included a per-result coverage table.",
        "结果库保留成功/失败筛选和任务详情；核验页只呈现核验轮次、处理和遗留事项。": "The result library retains pass/fail filters and task details; the review page now contains only review rounds, corrections, and remaining items.",
        "减少重复内容，不删除结果证据。": "Duplicate presentation was removed without deleting result evidence.",
    }
)

TEXT.update(
    {
        # Project-authored task labels/rationales. The adjacent official task
        # intent remains untranslated measurement input.
        "打开一个需要登录的 GitLab 目标页": "Open an authenticated GitLab target page",
        "打开一份报表并读取第一个条目": "Open a report and read its first entry",
        "打开评论列表并读取一种状态的数量": "Open a review list and read the count for one status",
        "搜索一张发票并读取总金额": "Search for an invoice and read its total",
        "进入一个论坛、选择最新帖子并点赞": "Enter a forum, choose the newest post, and upvote it",
        "进入项目议题列表并应用排序和状态筛选": "Open a project's issue list and apply sorting and status filters",
        "把需求转成商品查询并打开匹配详情页": "Turn a requirement into a product query and open the matching detail page",
        "进入订单历史，经翻页或排序后读取最早日期": "Open order history and read the earliest date after paging or sorting",
        "找到项目贡献者活动页并限定一个日期": "Find a project's contributor activity page and constrain it to one date",
        "打开个人项目并检查星标与项目标识": "Open a personal project and inspect its star and project identifier",
        "按标题和更新时间筛选个人议题，再读取状态": "Filter personal issues by title and update time, then read status",
        "按库存量筛选后台表格并收集 SKU": "Filter an admin table by inventory and collect SKUs",
        "按状态筛选订单，打开最新一单并读取总额": "Filter orders by status, open the newest order, and read its total",
        "筛选并排序订单，再打开最新的匹配客户": "Filter and sort orders, then open the newest matching customer",
        "打开个人资料设置，修改简介并保存": "Open profile settings, edit the biography, and save",
        "找到最新论坛帖子，并按作者和票数条件检查多条评论": "Find the newest forum post and inspect multiple comments by author and vote criteria",
        "检查一整年的分页订单并汇总数量和支出": "Inspect a full year of paginated orders and total their count and spend",
        "检查十个排序帖子，并根据描述归类推荐内容": "Inspect ten sorted posts and classify recommendations from their descriptions",
        "检查十个排序帖子，并从合格描述中提取两个字段": "Inspect ten sorted posts and extract two fields from qualifying descriptions",
        "应用日期和状态条件，并汇总五个月度数量": "Apply date and status conditions and total five monthly counts",
        "应用日期和状态条件，并汇总十一个月度数量": "Apply date and status conditions and total eleven monthly counts",
        "搜索商品、检查结果页并计算价格区间": "Search products, inspect the result page, and calculate a price range",
        "结合搜索词排名与独立的商品可用性检查": "Combine query ranking with an independent product-availability check",
        "检查日期区间内的订单、归类商品并汇总行项目金额": "Inspect orders in a date range, classify products, and total line-item amounts",
        "筛选库存、检查变体并返回成对商品字段": "Filter inventory, inspect variants, and return paired product fields",
        "排序已完成订单、检查两份详情并合计付款": "Sort completed orders, inspect two details, and total payments",
        "检查两组各四笔订单，并计算付款差额": "Inspect two groups of four orders and calculate the payment difference",
        "找到最新完成订单，并规范化、排序多个行项目": "Find the latest completed order, then normalize and sort multiple line items",
        "清空购物车，按预算和评分筛选品类，选择商品并结账": "Empty the cart, filter a category by budget and rating, choose a product, and check out",
        "定位项目文件、修改标题、提交到默认分支并验证": "Locate a project file, change its title, commit to the default branch, and verify",

        # Standalone interpretation article.
        "报告解读 · Browser Use 评测": "Interpretation · Browser Use evaluation",
        "从 25 比 25 读出下一轮该怎么做": "What the 25-to-25 result implies for the next run",
        "完整文章与首页共用同一份测量数据；另提供无 JavaScript 的独立导出版。": "The full article and home page use the same measured data. A standalone JavaScript-free export is also available.",
        "独立导出": "Standalone export",
        "打开独立文章": "Open standalone article",
        "下载论点证据": "Download claim evidence",
        "Browser Agent 实测解读": "Browser Agent evaluation interpretation",
        "25 比 25 不是“内核等价”：当前样本没有产生分歧": "25 to 25 does not mean browser-core equivalence: this sample produced no disagreement",
        "同一 CowAgent 与同一模型替换浏览器后，30 个配对任务没有一个出现单边胜出。这个结果证明对照流程跑通，却没有为内核优劣提供方向性证据。": "With the same CowAgent and model and only the browser changed, none of the 30 paired tasks produced a one-browser-only win. The controlled workflow ran successfully, but the result provides no directional evidence about browser-core superiority.",
        "测量口径：WebArena‑Verified 1.2.3；每题×浏览器一个最终逻辑结果；官方确定性 evaluator；无 LLM judge。": "Measurement unit: WebArena-Verified 1.2.3; one final logical result per task-browser pair; official deterministic evaluators; no LLM judge.",
        "一、最重要的结果不是平分，而是零个不一致配对": "1. The key result is not the tie but the absence of discordant pairs",
        "Chromium 与 Moli 分别通过 25/30 和 25/30。逐题配对后，25 题共同通过、5 题共同失败，两个浏览器单边通过都是 0。由于固定样本里没有不一致任务，这批数据不能估计浏览器的方向性效应，也不对 WebArena‑Verified 全集作统计外推。": "Chromium and Moli each passed 25/30. Pairing by task gives 25 joint passes, five joint failures, and zero one-browser-only passes. Because this fixed sample contains no discordant tasks, it cannot estimate a directional browser effect or support statistical generalization to the full WebArena-Verified set.",
        "表 1｜30 任务的逐题最终结果配对。": "Table 1 | Paired final results for 30 tasks.",
        "可支持的判断": "Supported conclusion",
        "当前组合可完成": "Current stack can complete",
        "严谨表述只能是“本轮没有观察到成功率差异”，不能写成“两种内核等价”。30 题对小效应检出力有限；更关键的是，样本没有诱发内核分歧。": "The defensible statement is only that this run observed no success-rate difference, not that the browser cores are equivalent. Thirty tasks have limited power for small effects; more importantly, this sample did not elicit browser-core disagreement.",
        "二、共同失败是待核验线索，不是已证实根因": "2. Shared failures are leads to verify, not confirmed root causes",
        "5 个共同失败任务均未满足官方验收合同。下表只陈述官方expected与actual结果的差异，不把差异直接归因给 Agent、浏览器或站点。": "All five shared-failure tasks missed the official acceptance contract. The table states only the difference between official expected and actual results and does not directly attribute it to the agent, browser, or site.",
        "表 2｜任务详情公开官方合同、actual回答、逐规则判定与执行轨迹。": "Table 2 | Task details expose the official contract, actual answer, per-rule verdicts, and execution trajectory.",
        "公开诊断": "Published diagnosis",
        "官方expected包含多本书；四次执行的actual回答都只包含一本。": "The official expected value contains multiple books; the actual answer in all four executions contains only one.",
        "actual回答中的价格范围与该题官方目标商品范围不一致。": "The price range in the actual answer differs from the official target-product range.",
        "actual订单汇总与官方expected值不一致。": "The actual order aggregate differs from the official expected value.",
        "actual回答为空列表，而官方expected为非空项目列表。": "The actual answer is an empty list, while the official expected value is a non-empty project list.",
        "官方网络副作用合同未通过；购物车最终状态未满足题目要求。": "The official network-side-effect contract failed; the final cart state did not satisfy the task.",
        "这五题在本轮没有任何successful trajectory，因此当前站点快照的可完成性也未被本报告独立证明。下一步应先做不参与计分的人工或 oracle 可完成性核验，再区分 Agent、工具抽象、步数上限、站点状态和浏览器因素。": "None of these five tasks has a successful trajectory in this run, so the report does not independently establish solvability on the current site snapshot. The next step is a non-scoring human or oracle solvability check, followed by separating agent, tool abstraction, step-limit, site-state, and browser factors.",
        "三、简单、中等、困难都覆盖了，但“难度”只是操作长度": "3. Easy, medium, and hard are covered, but difficulty means operation length",
        "WebArena‑Verified 没有官方 easy/medium/hard 三档。本项目在运行前按最少可见 UI 操作数注册为 1–4、5–7、8+ 三档，配额 5/10/15。它控制操作链覆盖，不等同于推理难度。": "WebArena-Verified has no official easy/medium/hard tiers. Before execution, this project registered tasks by minimum visible UI actions as 1-4, 5-7, and 8+, with quotas of 5/10/15. This controls operation-chain coverage and is not equivalent to reasoning difficulty.",
        "表 3｜各层级最终通过数/任务数。": "Table 3 | Final passes/tasks by tier.",
        "本项目分层": "Project tier",
        "表 4｜四个可重置自托管站点。": "Table 4 | Four resettable self-hosted sites.",
        "四、选择 WebArena‑Verified 是为了归因，不是因为它最流行": "4. WebArena-Verified was chosen for attribution, not popularity",
        "Mind2Web 家族采用面更广，但原版主要评离线动作预测；Online‑Mind2Web 和 WebVoyager贴近实时网页，却引入网页漂移和模型裁判。WebArena‑Verified 可以自托管、逐题重置，并用结构化最终答案和必要网络副作用做程序化验收，更适合“只替换浏览器”的 A/B。": "The Mind2Web family has broader adoption, but its original version mainly evaluates offline action prediction. Online-Mind2Web and WebVoyager are closer to live browsing but introduce page drift and model judging. WebArena-Verified is self-hosted, resettable per task, and programmatically evaluates structured final answers and required network side effects, making it better suited to a browser-only A/B test.",
        "代价也明确：部署重，环境兼容本身会成为工程变量；官方 evaluator 不衡量路径效率，也不能覆盖开放互联网泛化。": "The tradeoff is substantial deployment, with environment compatibility becoming an engineering variable. The official evaluator does not measure path efficiency or open-web generalization.",
        "五、下一轮应主动寻找内核敏感样本": "5. The next run should actively seek browser-sensitive tasks",
        "保留 CowAgent、模型、reasoning=low 和每题最多两次执行的固定协议，扩大稳定可验收的配对样本；新增长页面、上传下载、复杂编辑器、弹窗、多标签页等更可能诱发内核差异的任务。实时网站另开 Online‑Mind2Web / WebVoyager 泛化轨道，LLM judge 与人工抽检单列。": "Keep the fixed CowAgent, model, reasoning=low, and at-most-two-executions protocol while expanding the stable, evaluable paired sample. Add long pages, uploads/downloads, complex editors, dialogs, multiple tabs, and other tasks more likely to elicit browser differences. Use a separate Online-Mind2Web / WebVoyager generalization track for live sites, with LLM judging and human spot checks reported separately.",
        "决策边界": "Decision boundary",
        "本轮支持“流程可比、当前样本无方向性差异”；不支持“内核等价”“Moli 已全面替代 Chromium”或“端到端耗时就是纯内核速度”。Astra 获权后必须形成独立运行。": "This run supports a comparable workflow and no directional difference in the current sample. It does not support browser-core equivalence, full replacement of Chromium by Moli, or interpreting end-to-end latency as pure browser-core speed. Astra requires a separate run once access is available.",
    }
)


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
