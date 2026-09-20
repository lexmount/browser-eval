"use strict";
const root = document.documentElement,
  base = root.dataset.base || "",
  zh = root.lang.startsWith("zh");
const D = {
  zh: {
    nav: {
      home: "总览",
      dataset: "评测集",
      method: "评测方法",
      analysis: "结果分析",
      results: "逐题结果",
      review: "核验记录",
      guide: "报告解读",
      reproduce: "环境与复现",
    },
    modes: { off: "无布局", on: "开布局" },
    strata: {
      raw_read: "原始 CDP 读取",
      raw_geometry_input: "几何与坐标输入",
      playwright: "Playwright 常规操作",
      puppeteer: "Puppeteer 常规操作",
      agent_tool: "Agent 工具",
      multi_step: "多步交互",
      agent_scenario: "Agent 场景",
    },
    pass: "通过",
    fail: "失败",
    all: "全部",
    open: "查看",
    evidence: "证据",
    missing: "暂无证据",
    loading: "正在载入…",
    error: "数据读取失败",
    noHuman: "未进行独立人工逐次复核",
    methodLimit:
      "覆盖 Moli 0.1.1 的全部 372 个历史失败用例；结果适用于这一失败子集。",
    more: "查看详情",
    task: "任务",
    mode: "模式",
    attempt: "第 {n} 次",
    status: "状态",
    duration: "耗时",
    cpu: "CPU 时间",
    pss: "结束 PSS",
    traffic: "站点下行",
    failure: "程序错误",
    source: "脱敏证据",
    search: "搜索任务、驱动或程序错误",
    relation: "结果关系",
    stratum: "任务类型",
    bothPass: "两种模式均通过",
    gain: "开启后新增通过用例",
    mixed: "部分通过，次数相同",
    bothFail: "两种模式均失败",
    loss: "开启后退步用例",
    back: "返回逐题结果",
    close: "关闭",
    unavailable: "未提供",
    title: "Moli 1.1.4 / 1.1.8 / 1.1.9 · 成功率与资源对比",
    start: "查看逐题结果",
    method: "查看方法",
    metricNote:
      "P50 是中位数；P95 是第 95 百分位。耗时为驱动执行与验收的墙钟时间，不含浏览器启动、外围目标检查及最终证据落盘。",
    winner: "较低",
    higher: "较高",
    scope: "适用范围",
    observed: "观测到的采样最大值",
    filterFail: "涉及失败或变化",
    methodIntro:
      "题目与验收预先冻结，两种浏览器配置只相差是否启用 --layout。默认模式使用低成本模拟几何；--layout 启用按需真实布局，并非持续绘制。可选图片等资源抓取由另一开关控制，本轮未改变。1.1.9 的每个固定配置各测一轮资源，557 题各五次；372 题成功率另测，每题三次。",
    analysisIntro: "按相同任务与执行次数配对。成功变化和资源代价分开解读。",
    guideIntro:
      "从 Agent 使用者角度，先看能否完成，再看失败等待和完成任务的资源。",
    details: "展开证据",
    calibration: "采样校准",
    resultCount: "显示 {n} / {total} 个任务",
    rawContract: "原始任务合同",
    noRows: "没有符合筛选条件的任务。",
  },
  en: {
    nav: {
      home: "Overview",
      dataset: "Dataset",
      method: "Method",
      analysis: "Analysis",
      results: "Task results",
      review: "Verification",
      guide: "Interpretation",
      reproduce: "Environment",
    },
    modes: { off: "Layout off", on: "Layout on" },
    strata: {
      raw_read: "Raw CDP reading",
      raw_geometry_input: "Geometry and input",
      playwright: "Playwright operations",
      puppeteer: "Puppeteer operations",
      agent_tool: "Agent tools",
      multi_step: "Multi-step flows",
      agent_scenario: "Agent scenarios",
    },
    pass: "Pass",
    fail: "Fail",
    all: "All",
    open: "Open",
    evidence: "Evidence",
    missing: "No evidence available",
    loading: "Loading…",
    error: "Could not load data",
    noHuman: "No independent per-call review",
    methodLimit:
      "This cohort includes all 372 historical Moli 0.1.1 failures; results apply to this failure subset.",
    more: "View details",
    task: "Task",
    mode: "Mode",
    attempt: "Attempt {n}",
    status: "Status",
    duration: "Duration",
    cpu: "CPU time",
    pss: "Ending PSS",
    traffic: "Fixture downlink",
    failure: "Program error",
    source: "Sanitized evidence",
    search: "Search task, driver, or program error",
    relation: "Outcome relation",
    stratum: "Task family",
    bothPass: "Both modes pass",
    gain: "Cases gained with layout",
    mixed: "Equal partial passes",
    bothFail: "Both modes fail",
    loss: "Cases lost with layout",
    back: "Back to task results",
    close: "Close",
    unavailable: "Unavailable",
    title: "Moli 1.1.4 / 1.1.8 / 1.1.9 · Compatibility and resources",
    start: "Explore task results",
    method: "Read methodology",
    metricNote:
      "P50 is the median and P95 the 95th percentile. Duration covers driver execution and grading, excluding browser startup, outer target checks and final evidence writing.",
    winner: "Lower",
    higher: "Higher",
    scope: "Scope",
    observed: "Observed sampled maximum",
    filterFail: "Failures or changes",
    methodIntro:
      "Task contracts were frozen in advance. The browser configurations differ only in Moli's --layout flag. The default uses low-cost mock geometry; --layout enables real geometry on demand rather than continuous painting. Optional image and media fetching is a separate control and was unchanged. Each fixed 1.1.9 configuration has one 557-task resource run with five calls per task. Success is measured separately on 372 cases with three calls per task.",
    analysisIntro:
      "Pair the same task and attempt. Outcome changes and resource costs are interpreted separately.",
    guideIntro:
      "For an agent user, completion comes first, followed by failure waiting time and cost on completed tasks.",
    details: "Expand evidence",
    calibration: "Profiling calibration",
    resultCount: "Showing {n} / {total} tasks",
    rawContract: "Raw task contract",
    noRows: "No tasks match these filters.",
  },
};
Object.assign(D.zh.strata, {
  node_cdp_probe: "Node CDP",
  raw_cdp: "Raw CDP",
  tool_agent_browser: "agent-browser",
  thin_cdp_use: "cdp-use",
  thin_chromedp: "chromedp",
  thin_chrome_remote_interface: "chrome-remote-interface",
  thin_ferrum: "Ferrum",
  mcp_chrome_devtools: "Chrome DevTools MCP",
  thin_chromiumoxide: "Chromiumoxide",
  framework_puppeteer: "Puppeteer",
  framework_playwright: "Playwright",
  thin_pydoll: "Pydoll",
  thin_rod: "Rod",
  framework_stagehand: "Stagehand",
  webdriver_selenium: "Selenium",
});
Object.assign(D.en.strata, {
  node_cdp_probe: "Node CDP",
  raw_cdp: "Raw CDP",
  tool_agent_browser: "agent-browser",
  thin_cdp_use: "cdp-use",
  thin_chromedp: "chromedp",
  thin_chrome_remote_interface: "chrome-remote-interface",
  thin_ferrum: "Ferrum",
  mcp_chrome_devtools: "Chrome DevTools MCP",
  thin_chromiumoxide: "Chromiumoxide",
  framework_puppeteer: "Puppeteer",
  framework_playwright: "Playwright",
  thin_pydoll: "Pydoll",
  thin_rod: "Rod",
  framework_stagehand: "Stagehand",
  webdriver_selenium: "Selenium",
});
const T = zh ? D.zh : D.en;
let M = null,
  dialog = document.getElementById("evidence-dialog"),
  trigger = null;
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fmt = (v, d = 0) =>
  v === null || v === undefined
    ? "—"
    : Number(v).toLocaleString(zh ? "zh-CN" : "en-US", {
        maximumFractionDigits: d,
        minimumFractionDigits: d,
      });
const population = () =>
  zh
    ? `同一组预先固定的 ${M.task_count} 个任务，每种模式各运行 ${M.protocol.k_runs} 次，共 ${M.attempts_per_mode} 次调用。`
    : `The same ${M.task_count} preselected tasks, ${M.protocol.k_runs} calls each and ${M.attempts_per_mode} calls per mode.`;
const casePopulation = () =>
  zh
    ? `通过数按 ${M.task_count} 个用例统计：本轮每题已记录的 ${M.protocol.k_runs} 次无采样执行全部通过，才计为 1 个通过用例。`
    : `Pass counts use ${M.task_count} cases: a case passes only when all ${M.protocol.k_runs} recorded unprofiled attempts pass.`;
const caseVerdict = (n) =>
  statusChip(n === M.protocol.k_runs ? "pass" : "fail");
const historicalScope = () =>
  zh
    ? `历史 Moli 0.1.1 通过 1,556/1,928 个用例（80.71%），372 个用例未达到三次全部通过。1.1.8 关闭和开启布局均直接复测全部 372 题。1.1.8 关闭布局只恢复 1 题，因此 1.1.4 关闭布局只对这唯一候选题复测三次；该题三次均失败，其余 371 题沿用两版本均失败的集合关系，结果为 0/372。历史通过的 1,556 题未重测，不能据此推算新版本全量通过率。`
    : `Moli 0.1.1 originally passed 1,556/1,928 cases (80.71%); 372 did not pass all three attempts. Moli 1.1.8 was directly tested on all 372 with layout off and on. Because layout-off 1.1.8 recovered only one case, layout-off 1.1.4 was tested three times on that sole candidate; all three attempts failed, while the other 371 retain the shared-failure relation, yielding 0/372. The 1,556 historical passes were not rerun, so no new full-suite rate is inferred.`;
const historicalSection = () =>
  section(
    zh ? "与历史 80.7% 的关系" : "Relation to the historical 80.7%",
    historicalScope(),
    `<a href="https://github.com/lexmount/Lexbench-Headless-Browser/blob/main/docs/reports/four-engine-report-20260812.md">${esc(zh ? "原始四引擎报告 · 2026-08-12" : "Original four-engine report · 2026-08-12")}</a>`,
  );
const metricPopulation = () =>
  zh
    ? `全部 ${M.attempts_per_mode} 次调用，成功与失败都计入；平均、P50 和 P95 使用同一组调用。`
    : `All ${M.attempts_per_mode} predeclared calls, including failures; Mean, P50 and P95 use the same population.`;
const reportSubtitle = () =>
  Object.keys(M.fixed_candidates || {}).length
    ? zh
      ? `比较 ${M.functional_methods.length} 种固定配置在 ${M.task_count} 个历史失败用例上的成功率，并展示独立资源实验。`
      : `Compare ${M.functional_methods.length} fixed configurations on ${M.task_count} historical failures, with separate resource experiments.`
    : zh
      ? `比较 Moli 1.1.4 关闭布局、1.1.8 关闭布局与 1.1.8 开启布局对 ${M.task_count} 个历史失败用例的恢复情况，并配套展示独立资源实验。`
      : `Compare recovery of ${M.task_count} historical failures with Moli 1.1.4 layout off, Moli 1.1.8 layout off and Moli 1.1.8 layout on, with a separate resource experiment.`;
const reportVersion = () =>
  Object.keys(M?.fixed_candidates || {}).length
    ? "Moli 1.1.4 / 1.1.8 / 1.1.9"
    : "Moli 1.1.4 / 1.1.8";
const reportTitle = () =>
  Object.keys(M?.fixed_candidates || {}).length
    ? zh
      ? `${reportVersion()} · 成功率与资源对比`
      : `${reportVersion()} · Compatibility and resources`
    : T.title;
const datasetIntro = () =>
  zh
    ? `依据 Moli 0.1.1 原始结果选取全部 372 个失败用例，覆盖 15 类驱动；不使用本轮结果决定入选。`
    : `All 372 historical Moli 0.1.1 failures are included across 15 driver types, without selection based on current outcomes.`;
const reproduceIntro = () =>
  zh
    ? `历史失败集恢复测试和独立的 557 题资源实验均为诊断实验，不计入完整 1,928 题正式分数。1.1.8 直接覆盖 372 题；1.1.4 使用唯一差异候选题验证。三种固定配置使用 Moli 1.1.4 / 1.1.8 Linux aarch64 发行版；1.1.9 修复版使用固定的 macOS arm64 debug 二进制，官方版使用发布资产；每个版本的功能与资源实验绑定同一 SHA-256。`
    : `The historical-failure recovery check and separate 557-task resource experiment are diagnostic and excluded from the formal 1,928-case score. Moli 1.1.8 directly covers all 372 cases; Moli 1.1.4 uses the sole differential candidate. The three fixed configurations use Moli 1.1.4 / 1.1.8 Linux aarch64 releases. Patched Moli 1.1.9 uses the frozen macOS arm64 debug binary; official 1.1.9 uses the release asset. Functional and resource runs are bound to the same binary hash for each build.`;
const ms = (v) =>
  v === null || v === undefined
    ? "—"
    : fmt(v, Math.abs(v) < 10 ? 1 : 0) + " ms";
const mib = (v) =>
  v === null || v === undefined ? "—" : fmt(v / 1048576, 2) + " MiB";
const link = (r) => "#page=" + encodeURIComponent(r);
const route = () =>
  decodeURIComponent((location.hash.match(/page=([^&]+)/) || [])[1] || "home");
const section = (title, intro, html, id = "") =>
  `<section class="section" ${id ? `id="${id}"` : ""}><div class="section-head"><h2>${esc(title)}</h2></div>${intro ? `<p class="section-intro">${esc(intro)}</p>` : ""}${html}</section>`;
const hero = (title, subtitle, links = "") =>
  `<div class="hero"><div class="eyebrow">${esc(reportVersion())} · Lexbench</div><h1>${esc(title)}</h1><p class="subtitle">${esc(subtitle)}</p>${links ? `<div class="hero-links">${links}</div>` : ""}</div>`;
const table = (heads, rows, klass = "") =>
  `<div class="table-scroll ${klass}"><table><thead><tr>${heads.map((x) => `<th>${esc(x)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
const card = (label, value, detail) =>
  `<div class="metric-card"><div class="label">${esc(label)}</div><div class="value">${value}</div><div class="detail">${detail}</div></div>`;
function best(a, b, direction) {
  if (a === null || b === null || a === b) return null;
  return direction === "max" ? (a > b ? "off" : "on") : a < b ? "off" : "on";
}
function metricRow(label, off, on, format, direction = "min", note = "") {
  let w = best(off, on, direction);
  return `<tr><td>${esc(label)}${note ? `<div class="subtle">${esc(note)}</div>` : ""}</td><td class="num ${w === "off" ? "winner" : ""}" data-metric="${esc(label)}" data-metric-direction="${direction}" data-sort="${off}">${format(off)}${w === "off" ? ` <span class="mini">${esc(direction === "min" ? T.winner : T.higher)}</span>` : ""}</td><td class="num ${w === "on" ? "winner" : ""}" data-metric="${esc(label)}" data-metric-direction="${direction}" data-sort="${on}">${format(on)}${w === "on" ? ` <span class="mini">${esc(direction === "min" ? T.winner : T.higher)}</span>` : ""}</td></tr>`;
}
const signed = (value, digits = 2, suffix = "%") =>
  `${value >= 0 ? "+" : ""}${fmt(value, digits)}${suffix}`;
function contractCorrectionNotice() {
  let c = M.functional_contract_correction;
  if (!c) return "";
  return `<div class="notice"><strong>${esc(zh ? "修正合同补测" : "Corrected-contract retest")}</strong> · ${esc(zh ? `受 Chromium 基线修正影响的 ${c.task_count} 题，已对三种 Moli 配置各重跑 3 次，共 ${c.calls} 次调用；三组均仍为失败，所以下表成功率不变。` : `The ${c.task_count} cases affected by the Chromium baseline correction were rerun three times for each Moli configuration (${c.calls} calls). All three configurations still fail both cases, so the success rates below are unchanged.`)}</div>`;
}
function functionalMethodSummary() {
  if (!Object.keys(M.fixed_candidates || {}).length) return "";
  let rows = M.functional_methods,
    total = M.task_count,
    max = Math.max(...rows.map((x) => x.final_passed_cases)),
    unique = rows.filter((x) => x.final_passed_cases === max).length === 1;
  let body = rows.map((row) => {
    let policy =
      row.policy === "fixed_on"
        ? zh
          ? "全程开启"
          : "Always on"
        : zh
          ? "全程关闭"
          : "Always off";
    let scope = row.inherited_cases
      ? `${row.direct_cases} ${zh ? "题实测" : "direct"} + ${row.inherited_cases} ${zh ? "题继承共同失败关系" : "shared failures"}`
      : `${row.direct_cases} ${zh ? "题实测" : "direct cases"}`;
    let evidence = row.evidence
      ? `<div class="method-evidence"><button data-evidence="${esc(row.evidence.url)}" data-sha="${esc(row.evidence.sha256)}" data-title="${esc(zh ? "固定布局功能证据" : "Fixed-layout functional evidence")}">${esc(zh ? "查看逐题证据" : "Inspect case evidence")}</button></div>`
      : "";
    return `<tr data-functional-method="${esc(row.id)}"><td><strong>${esc(zh ? row.label : row.label_en)}</strong>${evidence}</td><td>${esc(policy)}</td><td>${esc(scope)}</td><td class="num ${unique && row.final_passed_cases === max ? "winner" : ""}" data-functional-final="${row.final_passed_cases}">${fmt(row.final_passed_cases)}/${total} (${fmt((row.final_passed_cases / total) * 100, 1)}%)</td><td class="num">${signed(((row.final_passed_cases - rows[0].final_passed_cases) / total) * 100, 2, zh ? " 个百分点" : " pp")}</td><td class="num">${fmt(row.physical_calls || (row.id === "114_off" ? 3 : total * 3))}</td></tr>`;
  });
  return section(
    zh
      ? `${rows.length} 种固定配置的成功率`
      : `Success rates for ${rows.length} fixed configurations`,
    zh
      ? `同一 ${total} 题分母，每题三次全部通过才算成功。开启和关闭 layout 分别统计各自的实际结果；资源使用独立的 557 题实验。变化以 1.1.4 关闭 layout 为基准。`
      : `The same ${total}-case denominator is used; all three attempts must pass. Layout on and off use their own observed results. Resources come from a separate 557-task experiment. Changes are relative to 1.1.4 layout off.`,
    table(
      [
        zh ? "方法" : "Method",
        zh ? "布局" : "Layout",
        zh ? "证据范围" : "Evidence scope",
        zh ? "通过用例" : "Passed cases",
        zh ? "通过率变化" : "Success-rate change",
        zh ? "调用数" : "Calls",
      ],
      body,
      "functional-method-table",
    ),
  );
}
function versionSummary() {
  let comparison = M.version_comparison,
    rows = comparison.configurations;
  let maxSuccess = Math.max(...rows.map((row) => row.success_rate_pct)),
    minPss = Math.min(...rows.map((row) => row.pss_mib)),
    minCpu = Math.min(...rows.map((row) => row.cpu_ms));
  let body = rows.map(
    (row) =>
      `<tr data-version-config="${esc(row.id)}"><td><strong>${esc(zh ? row.label : row.label_en)}</strong>${row.id === comparison.baseline ? `<div class="subtle">${esc(zh ? "变化基线 · 差异验证" : "Change baseline · differential check")}</div>` : ""}</td><td class="num ${row.success_rate_pct === maxSuccess ? "winner" : ""}" data-version-metric="success" data-direction="max" data-value="${row.success_rate_pct}">${fmt(row.passed_cases)}/${fmt(row.total_cases)} (${fmt(row.success_rate_pct, 1)}%)</td><td class="num">${signed(row.success_change_pp, 1, zh ? " 个百分点" : " pp")}</td><td class="num ${row.pss_mib === minPss ? "winner" : ""}" data-version-metric="pss" data-direction="min" data-value="${row.pss_mib}">${fmt(row.pss_mib, 2)} MiB</td><td class="num">${signed(row.pss_change_pct, 2)}</td><td class="num ${row.cpu_ms === minCpu ? "winner" : ""}" data-version-metric="cpu" data-direction="min" data-value="${row.cpu_ms}">${fmt(row.cpu_ms, 2)} ms</td><td class="num">${signed(row.cpu_change_pct, 2)}</td></tr>`,
  );
  let summary = table(
    [
      zh ? "配置" : "Configuration",
      zh ? "通过用例" : "Passed cases",
      zh ? "通过率变化（相对 1.1.4）" : "Success-rate change vs. 1.1.4",
      zh ? "峰值 PSS 中位数" : "Median peak PSS",
      zh ? "PSS 变化（相对 1.1.4）" : "PSS change vs. 1.1.4",
      zh ? "平均 CPU 时间 / 次" : "Mean CPU time / call",
      zh ? "CPU 变化（相对 1.1.4）" : "CPU change vs. 1.1.4",
    ],
    body,
    "version-summary-table",
  );
  let population = zh
    ? `纵轴使用同一组 372 个历史失败用例。1.1.8 两种模式直接各跑 372 题 × 3 次；1.1.4 对 1.1.8 关闭布局唯一通过的候选题跑三次，该题三次均失败，其余 371 题沿用两版本共同失败关系，因此为 0/372。横轴资源来自独立的 557 题 × 5 次实验；图表使用运行前冻结的 ${comparison.resource_population.chart_calls} 个任务-执行样本（${comparison.resource_population.chart_tasks} 题），采样间隔 ${comparison.resource_population.sample_interval_ms} ms。表格和图中的百分比变化均以 1.1.4 关闭 layout 为基准。`
    : `The vertical axis uses the same set of 372 historical failures. Both 1.1.8 modes directly ran 372 cases × 3; 1.1.4 ran the sole case passed by layout-off 1.1.8 three times, failed all three, and retains the shared-failure relation for the other 371, yielding 0/372. Horizontal resources come from a separate 557-task × 5-call experiment; the chart uses ${comparison.resource_population.chart_calls} pre-frozen task-attempt samples (${comparison.resource_population.chart_tasks} tasks) at ${comparison.resource_population.sample_interval_ms} ms intervals. Percentage changes in the table and chart use 1.1.4 layout off as baseline.`;
  let note = zh
    ? `原图标注的 71 MiB 没有随 PR #535 提供对应执行记录；按可复现公开流程，本轮 1.1.4 测得 ${fmt(rows[0].pss_mib, 2)} MiB。CPU 是进程树用户态与内核态时间之和，不是墙钟耗时或 CPU 利用率。`
    : `The published 71 MiB label has no matching execution record attached to PR #535; the reproducible public procedure measures ${fmt(rows[0].pss_mib, 2)} MiB for 1.1.4 here. CPU is process-tree user plus system time, not wall time or utilization.`;
  let chart = zh
    ? "moli-version-success-resources-zh.svg"
    : "moli-version-success-resources-en.svg";
  return `<div class="version-summary">${contractCorrectionNotice()}${summary}<figure class="summary-chart"><img src="${base}assets/${chart}" alt="${esc(zh ? `${rows.length} 种 Moli 配置在 372 题成功率、峰值 PSS 和 CPU 时间上的对比图` : `${rows.length} Moli configurations compared by 372-case success rate, peak PSS and CPU time`)}"><figcaption>${esc(population)}</figcaption></figure><p class="subtle">${esc(note)} <a href="${base}data/version-comparison.json">${esc(zh ? "下载机器可读数据" : "Download machine-readable data")}</a></p></div>`;
}
function macosResourceMethods() {
  let result = [];
  for (let [key, layouts] of [
    ["macos_resource", ["off", "on"]],
    ["official_resource", ["on"]],
  ]) {
    let resource = M[key];
    if (!resource) continue;
    for (let layout of layouts) {
      let id =
          key === "official_resource" ? "119_official_on" : `119_${layout}`,
        method = M.functional_methods.find((row) => row.id === id);
      result.push({
        id,
        label: zh ? method.label : method.label_en,
        config: resource.summary.configurations[layout],
        resource,
      });
    }
  }
  return result;
}
function candidateResourceSection() {
  let methods = macosResourceMethods();
  if (!methods.length) return "";
  let baseline = methods.find((x) => x.id === M.macos_comparison.baseline),
    scopes = [
      [
        "all_predeclared_calls",
        zh ? "557 题全部调用" : "All calls on 557 tasks",
        2785,
      ],
      [
        "frozen_historical_common_pass_calls",
        zh
          ? "原图口径的冻结样本：209 题"
          : "Frozen legacy-aligned sample: 209 tasks",
        1045,
      ],
    ];
  let specs = [
    ["rss_peak_mib", "mean", zh ? "峰值 RSS 均值" : "Peak RSS mean", "MiB"],
    ["rss_peak_mib", "p50", zh ? "峰值 RSS P50" : "峰值 RSS P50", "MiB"],
    ["rss_peak_mib", "p95", zh ? "峰值 RSS P95" : "峰值 RSS P95", "MiB"],
    ["cpu_time_ms", "mean", zh ? "CPU 均值" : "CPU mean", "ms"],
    ["cpu_time_ms", "p50", zh ? "CPU P50" : "CPU P50", "ms"],
    ["cpu_time_ms", "p95", zh ? "CPU P95" : "CPU P95", "ms"],
  ];
  let tables = scopes.map(([scope, title, calls]) => {
    let minFor = (metric, stat) =>
      Math.min(...methods.map((x) => x.config.metrics[scope][metric][stat]));
    let unique = (metric, stat, value) =>
      value === minFor(metric, stat) &&
      methods.filter((x) => x.config.metrics[scope][metric][stat] === value)
        .length === 1;
    let leader = (metric, stat) => {
      let winners = methods.filter(
        (x) => x.config.metrics[scope][metric][stat] === minFor(metric, stat),
      );
      return winners.length === 1 ? winners[0].label : zh ? "并列" : "Tie";
    };
    let rows = methods.map((item) => {
      let m = item.config.metrics[scope],
        b = baseline.config.metrics[scope];
      return `<tr data-macos-method="${esc(item.id)}"><td><strong>${esc(item.label)}</strong></td><td class="num">${fmt(calls)}</td>${specs.map(([metric, stat, label, unit]) => `<td class="num ${unique(metric, stat, m[metric][stat]) ? "winner" : ""}" data-resource-metric="${metric}-${stat}" data-population="${scope}" data-value="${m[metric][stat]}">${fmt(m[metric][stat], 2)} ${unit}</td>`).join("")}<td class="num">${signed((m.rss_peak_mib.p50 / b.rss_peak_mib.p50 - 1) * 100, 2)}</td><td class="num">${signed((m.cpu_time_ms.mean / b.cpu_time_ms.mean - 1) * 100, 2)}</td></tr>`;
    });
    return (
      `<h3>${esc(title)}</h3><p>${esc(zh ? `峰值 RSS P50 最低：${leader("rss_peak_mib", "p50")}；单次 CPU 均值最低：${leader("cpu_time_ms", "mean")}。资源最低与通过率最高分别判断。` : `Lowest peak 峰值 RSS P50: ${leader("rss_peak_mib", "p50")}; lowest mean CPU per call: ${leader("cpu_time_ms", "mean")}. Resource minima and task success are evaluated separately.`)}</p>` +
      table(
        [
          zh ? "方法" : "Method",
          zh ? "调用数" : "Calls",
          ...specs.map((x) => x[2]),
          zh ? "峰值 RSS P50 变化" : "峰值 RSS P50 change",
          zh ? "CPU 均值变化" : "CPU mean change",
        ],
        rows,
        "candidate-resource-table",
      )
    );
  });
  let chart = zh
    ? "moli-macos-success-resources-zh.svg"
    : "moli-macos-success-resources-en.svg";
  let intro = zh
    ? `资源实验与 372 题成功率测试分开：每个配置固定 layout，557 题各执行 5 次。P50 是中位数，P95 是第 95 百分位；每组统计包含同一批成功与失败调用。变化基准：${baseline.label}。macOS 测量进程树 RSS；历史 Linux PSS 另表展示。`
    : `Resources are measured separately from success on 372 cases: each configuration fixes layout and runs 557 tasks five times. P50 is the median and P95 the 95th percentile; every metric in a scope includes the same passing and failing calls. Change baseline: ${baseline.label}. macOS measures process-tree RSS; historical Linux PSS is shown separately.`;
  let evidence = methods
    .filter((x, i, a) => a.findIndex((y) => y.resource === x.resource) === i)
    .map(
      (x) =>
        `<button data-evidence="${esc(x.resource.evidence.url)}" data-sha="${esc(x.resource.evidence.sha256)}" data-title="${esc(x.label)}">${esc(x.resource.summary.candidate.profile === "debug" ? (zh ? "修复版资源证据" : "Patched resource evidence") : zh ? "官方版资源证据" : "Official resource evidence")}</button>`,
    )
    .join(" ");
  return section(
    zh
      ? "Moli 1.1.9：成功率、RSS 与 CPU"
      : "Moli 1.1.9: success rate, RSS and CPU",
    intro,
    tables.join("") +
      `<figure class="summary-chart"><img src="${base}assets/${chart}" alt="${esc(zh ? "Moli 1.1.9 固定布局配置的成功率与 RSS、CPU 对比" : "Moli 1.1.9 fixed-layout success rates vs. RSS and CPU")}"><figcaption>${esc(zh ? `纵轴为 372 题通过率，横轴使用运行前冻结的 209 题、1,045 次调用；括号内百分比相对 ${baseline.label}。` : `Success rates use 372 cases; resource axes use 1,045 calls on 209 tasks frozen before execution. Parenthesized changes are relative to ${baseline.label}.`)}</figcaption></figure><p>${esc(zh ? "修复版使用提供的 debug 二进制，官方版使用发布资产。两者资源差异同时包含代码与构建配置差异。CPU 指进程树用户态与内核态时间之和。" : "The patched build uses the supplied debug binary; the official build uses the release asset. Their resource differences include both code and build configuration. CPU is the sum of process-tree user and system time.")}</p><div class="panel">${evidence}</div>`,
  );
}
function resourceGroups() {
  let off = M.metrics.off.profile,
    on = M.metrics.on.profile,
    rx = M.network_rx,
    cold = M.cold_start;
  let cpu = table(
    [zh ? "CPU 指标" : "CPU metric", T.modes.off, T.modes.on],
    [
      metricRow(
        zh ? "单次 CPU 均值" : "Mean per-call CPU",
        off.cpu_ms_all.mean,
        on.cpu_ms_all.mean,
        ms,
      ),
      metricRow(
        zh ? "累计 CPU 时间" : "Total CPU time",
        off.cpu_ms_all.sum,
        on.cpu_ms_all.sum,
        (v) => fmt(v / 1000, 2) + " s",
      ),
      metricRow(
        zh ? "单次 CPU P50" : "Per-call CPU P50",
        off.cpu_ms_all.p50,
        on.cpu_ms_all.p50,
        ms,
      ),
      metricRow(
        zh ? "单次 CPU P95" : "Per-call CPU P95",
        off.cpu_ms_all.p95,
        on.cpu_ms_all.p95,
        ms,
      ),
    ],
  );
  let memory = table(
    [zh ? "内存指标" : "Memory metric", T.modes.off, T.modes.on],
    [
      metricRow(
        zh ? "观测 PSS 最大值均值" : "Mean observed PSS maximum",
        off.pss_peak_mib_all.mean,
        on.pss_peak_mib_all.mean,
        (v) => fmt(v, 2) + " MiB",
      ),
      metricRow(
        zh ? "观测 PSS 最大值 P50" : "Observed PSS maximum P50",
        off.pss_peak_mib_all.p50,
        on.pss_peak_mib_all.p50,
        (v) => fmt(v, 2) + " MiB",
      ),
      metricRow(
        zh ? "观测 PSS 最大值 P95" : "Observed PSS maximum P95",
        off.pss_peak_mib_all.p95,
        on.pss_peak_mib_all.p95,
        (v) => fmt(v, 2) + " MiB",
      ),
      metricRow(
        zh ? "PSS 增量 P50" : "PSS increase P50",
        off.pss_delta_mib_all.p50,
        on.pss_delta_mib_all.p50,
        (v) => fmt(v, 2) + " MiB",
      ),
    ],
  );
  let network = table(
    [
      zh ? "站点应用层流量" : "Fixture application traffic",
      T.modes.off,
      T.modes.on,
    ],
    [
      metricRow(
        zh ? "下行总量" : "Total downlink",
        off.fixture_tx_bytes_all.sum,
        on.fixture_tx_bytes_all.sum,
        (v) => fmt(v) + " B",
      ),
      metricRow(
        zh ? "下行 P50" : "Downlink P50",
        off.fixture_tx_bytes_all.p50,
        on.fixture_tx_bytes_all.p50,
        (v) => fmt(v) + " B",
      ),
      metricRow(
        zh ? "上行总量" : "Total uplink",
        rx.off.total_bytes,
        rx.on.total_bytes,
        (v) => fmt(v) + " B",
      ),
      metricRow(
        zh ? "上行 P50" : "Uplink P50",
        rx.off.p50_bytes,
        rx.on.p50_bytes,
        (v) => fmt(v) + " B",
      ),
    ],
  );
  let startup = table(
    [
      zh
        ? `冷启动指标 · 关闭 ${M.cold_start.off.n} / 开启 ${M.cold_start.on.n} 次`
        : `Cold-start metric · off ${M.cold_start.off.n} / on ${M.cold_start.on.n}`,
      T.modes.off,
      T.modes.on,
    ],
    [
      metricRow(
        zh ? "就绪时间 P50" : "Ready time P50",
        cold.off.ready_ms.p50,
        cold.on.ready_ms.p50,
        ms,
      ),
      metricRow(
        zh ? "启动 CPU P50" : "Launch CPU P50",
        cold.off.launch_cpu_ms.p50,
        cold.on.launch_cpu_ms.p50,
        ms,
      ),
      metricRow(
        zh ? "启动观测 PSS P50" : "Launch observed PSS P50",
        cold.off.launch_pss_bytes.p50,
        cold.on.launch_pss_bytes.p50,
        mib,
      ),
    ],
  );
  let leaderOf = (a, b) => {
    let winner = best(a, b, "min");
    return winner ? T.modes[winner] : zh ? "持平" : "tie";
  };
  let leader = `<div class="notice"><strong>${esc(zh ? "采样执行中的资源数值" : "Observed profiled resource values")}</strong> · ${esc(zh ? `全部调用 CPU：${leaderOf(off.cpu_ms_all.sum, on.cpu_ms_all.sum)}；观测 PSS 最大值 P50：${leaderOf(off.pss_peak_mib_all.p50, on.pss_peak_mib_all.p50)}；站点下行：${leaderOf(off.fixture_tx_bytes_all.sum, on.fixture_tx_bytes_all.sum)}。这些是采样执行的观测值；主耗时采用无采样轮。` : `All-call CPU: ${leaderOf(off.cpu_ms_all.sum, on.cpu_ms_all.sum)}; observed PSS maximum P50: ${leaderOf(off.pss_peak_mib_all.p50, on.pss_peak_mib_all.p50)}; fixture downlink: ${leaderOf(off.fixture_tx_bytes_all.sum, on.fixture_tx_bytes_all.sum)}. These are profiled observations; primary duration uses unprofiled calls.`)}</div>`;
  return (
    leader +
    `<div class="grid"><div data-resource-group="cpu"><h3>${esc(zh ? "CPU 与计算" : "CPU and computation")}</h3>${cpu}</div><div data-resource-group="memory"><h3>${esc(zh ? "内存" : "Memory")}</h3>${memory}</div><div data-resource-group="network"><h3>${esc(zh ? "网络流量" : "Network traffic")}</h3>${network}</div><div data-resource-group="startup"><h3>${esc(zh ? "冷启动" : "Cold start")}</h3>${startup}</div></div>`
  );
}
function pairStatus(c) {
  let a = c.off_passes === M.protocol.k_runs,
    b = c.on_passes === M.protocol.k_runs;
  return a && b ? "bothPass" : b ? "gain" : a ? "loss" : "bothFail";
}
function statusChip(s) {
  return `<span class="status ${s === "pass" ? "pass" : "fail"}">${esc(s === "pass" ? T.pass : T.fail)}</span>`;
}
function simpleCard(label, value, detail) {
  return card(label, `<span>${value}</span>`, esc(detail));
}
function home() {
  let a = M.metrics.off,
    b = M.metrics.on;
  let off = a.baseline,
    on = b.baseline;
  let common = M.common_pass.count;
  let core = table(
    [zh ? "指标" : "Metric", T.modes.off, T.modes.on],
    [
      metricRow(
        zh ? "通过用例" : "Passed cases",
        M.case_outcomes.off,
        M.case_outcomes.on,
        (v) =>
          `${fmt(v)}/${M.task_count} (${fmt((v / M.task_count) * 100, 1)}%)`,
        "max",
      ),
      metricRow(
        zh ? "累计任务耗时" : "Total task time",
        off.duration_ms_all.sum,
        on.duration_ms_all.sum,
        (v) => fmt(v / 1000, 2) + " s",
      ),
      metricRow(
        zh ? "平均耗时" : "Mean duration",
        off.duration_ms_all.mean,
        on.duration_ms_all.mean,
        ms,
      ),
      metricRow(
        zh ? "P50 耗时" : "P50 duration",
        off.duration_ms_all.p50,
        on.duration_ms_all.p50,
        ms,
      ),
      metricRow(
        zh ? "P95 耗时" : "P95 duration",
        off.duration_ms_all.p95,
        on.duration_ms_all.p95,
        ms,
      ),
    ],
    "core-table",
  );
  let commonRows = table(
    [zh ? "共同成功的同题指标" : "Same-task metric", T.modes.off, T.modes.on],
    [
      metricRow(
        zh ? "累计 CPU" : "Total CPU",
        M.common_pass.cpu_total_ms.off,
        M.common_pass.cpu_total_ms.on,
        ms,
      ),
      metricRow(
        zh ? "结束 PSS 均值" : "Mean ending PSS",
        M.common_pass.pss_end_mean_bytes.off,
        M.common_pass.pss_end_mean_bytes.on,
        mib,
      ),
      metricRow(
        zh ? "站点下行量" : "Fixture downlink",
        M.common_pass.fixture_tx_total_bytes.off,
        M.common_pass.fixture_tx_total_bytes.on,
        (v) => fmt(v) + " B",
      ),
    ],
  );
  return (
    (M.host_context.concurrent_test_observed_at_start
      ? `<div class="notice warn">${esc(zh ? "本轮与同机其他测试并行，耗时和资源代表共享宿主机条件；具体观测见核验记录。" : "Other tests were active on the same host. Duration and resource values reflect shared-host conditions; see Verification for observations.")}</div>`
      : "") +
    hero(
      reportTitle(),
      reportSubtitle(),
      `<a class="button" href="${link("results")}">${esc(T.start)}</a><a class="button secondary" href="${link("method")}">${esc(T.method)}</a>`,
    ) +
    functionalMethodSummary() +
    section(
      zh
        ? "历史 Linux 配置：成功率与资源"
        : "Historical Linux configurations: success and resources",
      zh
        ? "以下三个配置使用 Linux PSS。1.1.9 的 macOS RSS/CPU 另表展示；成功率均覆盖同一 372 个历史失败用例。"
        : "These three configurations use Linux PSS. Moli 1.1.9 macOS RSS/CPU is shown separately; success rates cover the same 372 historical failures.",
      versionSummary(),
    ) +
    candidateResourceSection() +
    section(
      zh ? "1.1.8 布局模式核心结果" : "Moli 1.1.8 layout-mode results",
      casePopulation() + " " + metricPopulation(),
      core,
    ) +
    section(
      zh ? "1.1.8 的 CPU、内存与网络" : "Moli 1.1.8 CPU, memory and network",
      zh
        ? `同一 ${M.attempts_per_mode} 次调用。绿色仅标识同一指标中较低的原始数值，不代表统计显著性或整体优胜。`
        : `The same ${M.attempts_per_mode} calls. Green marks the lower raw value for the same metric, not statistical significance or overall leadership.`,
      resourceGroups(),
    ) +
    section(
      zh ? "各模式成功调用耗时" : "Duration on each mode’s accepted calls",
      zh
        ? "每种模式仅统计自身通过的调用，任务范围不同；平均、P50、P95 使用表头标出的同一分母。"
        : "Each mode includes only its own accepted calls, with different task coverage. Mean, P50 and P95 share the population shown in each column heading.",
      successfulDuration(),
    ) +
    section(
      zh ? "共同成功任务的条件成本" : "Conditional cost on shared successes",
      `${common}/${M.attempts_per_mode} ${zh ? "个同题同次配对在两种模式、无采样和采样四轮中均通过。这个诊断子集不决定任务入选或整体排行。" : "paired calls passed all four baseline and profiled rounds. This diagnostic subset does not select tasks or determine an overall ranking."}`,
      commonRows,
    ) +
    section(
      zh ? "任务类别概览" : "Task families",
      T.methodLimit + " " + casePopulation(),
      strataTable(),
    )
  );
}
function successfulDuration() {
  let a = M.metrics.off.baseline,
    b = M.metrics.on.baseline;
  return table(
    [
      zh ? "成功调用耗时" : "Accepted-call duration",
      `${T.modes.off} · n=${a.passes}`,
      `${T.modes.on} · n=${b.passes}`,
    ],
    [
      ["mean", zh ? "成功调用平均耗时" : "Mean accepted-call duration"],
      ["p50", zh ? "成功调用 P50 耗时" : "Accepted-call P50 duration"],
      ["p95", zh ? "成功调用 P95 耗时" : "Accepted-call P95 duration"],
    ].map(([key, label]) =>
      metricRow(label, a.duration_ms_pass[key], b.duration_ms_pass[key], ms),
    ),
  );
}
function strataTable() {
  let rows = M.strata.map((s) => {
    let a = M.cases.filter((c) => c.stratum === s),
      off = a.filter((c) => c.off_passes === M.protocol.k_runs).length,
      on = a.filter((c) => c.on_passes === M.protocol.k_runs).length,
      total = a.length;
    return `<tr><td><a href="${link("results")}&stratum=${encodeURIComponent(s)}">${esc(T.strata[s] || s)}</a><div class="subtle">${a.length} ${zh ? "题" : "tasks"}</div></td><td class="num">${off}/${total}</td><td class="num">${on}/${total}</td><td><div class="bar" aria-label="${off} / ${on}"><span class="pass" style="width:${(on / total) * 100}%"></span><span class="fail" style="width:${((total - on) / total) * 100}%"></span></div></td></tr>`;
  });
  return table(
    [
      zh ? "任务类别" : "Family",
      T.modes.off,
      T.modes.on,
      zh ? "开布局通过占比" : "Layout-on pass share",
    ],
    rows,
  );
}
function inventory(kind) {
  let title =
    kind === "dataset"
      ? T.nav.dataset
      : kind === "analysis"
        ? T.nav.analysis
        : T.nav.results;
  let intro =
    kind === "dataset"
      ? datasetIntro()
      : kind === "analysis"
        ? T.analysisIntro
        : population();
  let filters = `<div class="filters"><label>${T.search}<input id="query" type="search" placeholder="${esc(T.search)}"></label><label>${T.stratum}<select id="stratum"><option value="">${T.all}</option>${M.strata.map((s) => `<option value="${s}">${esc(T.strata[s] || s)}</option>`).join("")}</select></label><label>${T.relation}<select id="relation"><option value="">${T.all}</option><option value="failure" ${kind === "results" ? "selected" : ""}>${esc(T.filterFail)}</option>${["bothPass", "gain", "bothFail", "loss", "mixed"].map((s) => `<option value="${s}">${esc(T[s])}</option>`).join("")}</select></label></div><div class="subtle" id="count"></div>`;
  return (
    hero(title, intro) +
    section(
      zh ? "查找与筛选" : "Find and filter",
      "",
      filters + `<div id="inventory-table"></div>`,
    ) +
    (kind === "dataset"
      ? section(
          zh ? "选择规则" : "Selection rule",
          T.methodLimit,
          `<div class="panel"><p>${esc(zh ? "选取历史 1,928 题中未能三次全部通过的全部 372 题。保留 Chrome 历史失败的题目；Chrome 的结果不作为排除依据。两个布局组使用同一组冻结任务与验收规则。" : M.selection)}</p><p class="subtle">SHA-256: <code>${esc(M.manifest_sha256)}</code></p></div>`,
        )
      : "")
  );
}
function failureSummary(c) {
  let lines = [];
  for (let mode of ["off", "on"]) {
    for (let detail of c[mode + "_fail_details"] || []) {
      lines.push(
        `<button type="button" data-diagnosis="${esc(c.id)}" data-mode="${mode}" title="${esc(detail)}">${esc(T.modes[mode])}: ${esc(detail)}</button>`,
      );
    }
  }
  return lines.length
    ? `<div class="failure-cell">${lines.join("")}</div>`
    : "—";
}
function renderInventory() {
  let dataset = route() === "dataset";
  let q = (document.getElementById("query")?.value || "").toLowerCase(),
    s = document.getElementById("stratum")?.value || "",
    r = document.getElementById("relation")?.value || "";
  let rows = M.cases.filter(
    (c) =>
      (!q ||
        (
          c.id +
          " " +
          c.title +
          " " +
          c.subset +
          " " +
          (c.off_fail_details || []).join(" ") +
          " " +
          (c.on_fail_details || []).join(" ")
        )
          .toLowerCase()
          .includes(q)) &&
      (!s || c.stratum === s) &&
      (!r ||
        (r === "failure" ? pairStatus(c) !== "bothPass" : pairStatus(c) === r)),
  );
  document.getElementById("count").textContent = T.resultCount
    .replace("{n}", rows.length)
    .replace("{total}", M.cases.length);
  if (dataset) {
    document.getElementById("relation").closest("label").hidden = true;
    document.getElementById("inventory-table").innerHTML = table(
      [T.task, T.stratum, zh ? "子集" : "Subset", T.rawContract],
      rows.map(
        (c) =>
          `<tr><td>${esc(c.title)}<div class="subtle"><code>${esc(c.id)}</code></div></td><td>${esc(T.strata[c.stratum] || c.stratum)}</td><td>${esc(c.subset)}</td><td><a href="${link("contract/" + c.id)}">${esc(T.open)}</a></td></tr>`,
      ),
      "inventory",
    );
    return;
  }
  document.getElementById("inventory-table").innerHTML = rows.length
    ? table(
        [
          T.task,
          T.stratum,
          T.modes.off,
          T.modes.on,
          T.relation,
          T.failure,
          T.open,
        ],
        rows.map(
          (c) =>
            `<tr><td class="title-cell"><a href="${link("task/" + c.id)}"><strong>${esc(c.title)}</strong></a><div class="subtle"><code>${esc(c.id)}</code></div></td><td>${esc(T.strata[c.stratum] || c.stratum)}</td><td class="num">${caseVerdict(c.off_passes)}</td><td class="num">${caseVerdict(c.on_passes)}</td><td>${esc(T[pairStatus(c)])}</td><td>${failureSummary(c)}</td><td><a href="${link("result/" + c.id + "/off")}">${esc(T.modes.off)}</a> · <a href="${link("result/" + c.id + "/on")}">${esc(T.modes.on)}</a></td></tr>`,
        ),
        "inventory",
      )
    : "<div class='empty'>" + esc(T.noRows) + "</div>";
}
function wireInventory() {
  let query = new URLSearchParams(location.hash.split("&").slice(1).join("&"));
  let initial = query.get("stratum");
  if (initial && M.strata.includes(initial))
    document.getElementById("stratum").value = initial;
  for (let id of ["query", "stratum", "relation"])
    document
      .getElementById(id)
      ?.addEventListener(id === "query" ? "input" : "change", renderInventory);
  renderInventory();
}
const facts = (items) =>
  `<dl class="facts">${items.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
async function getCase(id) {
  if (!M.cases.some((c) => c.id === id)) throw Error("Unknown task");
  let r = await fetch(base + "data/cases/" + id + ".json", {
    cache: "no-store",
  });
  if (!r.ok) throw Error("Task data unavailable");
  return r.json();
}
function pairedAttempts(c) {
  let rows = Array.from({ length: M.protocol.k_runs }, (_, i) => {
    let off = c.modes.off.attempts[i],
      on = c.modes.on.attempts[i],
      n = i + 1;
    let status = (mode, a) =>
      `<a href="${link("result/" + c.id + "/" + mode)}&attempt=${n}" aria-label="${esc(T.modes[mode] + " · " + T.attempt.replace("{n}", n) + " · " + (a.status === "pass" ? T.pass : T.fail))}">${statusChip(a.status)}</a>`;
    return `<tr data-pair-row="${n}"><td>${esc(T.attempt.replace("{n}", n))}</td><td>${status("off", off)}</td><td>${status("on", on)}</td><td class="num">${esc(ms(off.duration_ms))}</td><td class="num">${esc(ms(on.duration_ms))}</td><td class="num">${esc(ms(off.resource.cpu_total_ms))}</td><td class="num">${esc(ms(on.resource.cpu_total_ms))}</td><td class="num">${esc(mib(off.resource.pss_end_bytes))}</td><td class="num">${esc(mib(on.resource.pss_end_bytes))}</td></tr>`;
  });
  return table(
    [
      zh ? "序号" : "Attempt",
      zh ? "无布局状态" : "Off status",
      zh ? "开布局状态" : "On status",
      zh ? "无布局耗时" : "Off duration",
      zh ? "开布局耗时" : "On duration",
      zh ? "无布局 CPU" : "Off CPU",
      zh ? "开布局 CPU" : "On CPU",
      zh ? "无布局结束 PSS" : "Off ending PSS",
      zh ? "开布局结束 PSS" : "On ending PSS",
    ],
    rows,
    "paired-matrix",
  );
}
async function taskPage(id, contract = false) {
  let c = await getCase(id);
  let f = facts([
    [zh ? "任务 ID" : "Task ID", `<code>${esc(c.id)}</code>`],
    [zh ? "子集" : "Subset", esc(c.subset)],
    [
      zh ? "任务定义 SHA-256" : "Task SHA-256",
      `<code>${esc(c.task_sha256)}</code>`,
    ],
    [
      zh ? "协议特性" : "Features",
      c.features.map((x) => `<span class="tag">${esc(x)}</span>`).join(" ") ||
        "—",
    ],
  ]);
  let results = table(
    [T.mode, zh ? "用例判定" : "Case verdict", T.open],
    ["off", "on"].map(
      (m) =>
        `<tr><td>${esc(T.modes[m])}</td><td>${caseVerdict(c.modes[m].passes)}</td><td><a href="${link("result/" + id + "/" + m)}">${esc(T.more)} →</a></td></tr>`,
    ),
  );
  return (
    `<div class="breadcrumb"><a href="${link("results")}">${esc(T.nav.results)}</a> / ${esc(c.id)}</div>` +
    hero(c.title, c.description) +
    (contract
      ? ""
      : section(
          zh ? "同题结果" : "Same-task results",
          casePopulation(),
          results,
        ) +
        section(
          zh ? "逐次配对" : "Paired attempts",
          zh
            ? "耗时来自无采样轮；CPU 和结束 PSS 来自同题同次的资源采样轮。点击状态可直达对应调用的证据。"
            : "Duration comes from the unprofiled run; CPU and ending PSS come from the matching profiled call. Open a status to inspect that exact call's evidence.",
          pairedAttempts(c),
        )) +
    (contract
      ? section(
          T.rawContract,
          "",
          `<div class="detail-grid"><div class="panel">${f}</div><div class="panel"><h3>${esc(zh ? "驱动步骤" : "Driver steps")}</h3><pre>${esc(JSON.stringify(c.driver, null, 2))}</pre></div></div>`,
        ) +
        section(
          zh ? "验收合同" : "Acceptance contract",
          "",
          `<div class="grid two"><div class="panel"><h3>${esc(zh ? "场景" : "Scene")}</h3><pre>${esc(JSON.stringify(c.scene, null, 2))}</pre></div><div class="panel"><h3>${esc(zh ? "评分规则" : "Grader")}</h3><pre>${esc(JSON.stringify(c.grader, null, 2))}</pre></div></div>`,
        )
      : section(
          T.rawContract,
          "",
          `<a href="${link("contract/" + id)}">${esc(T.open)} →</a>`,
        ))
  );
}
function evidenceButtons(a, mode, id) {
  let groups = [];
  for (let phase of ["baseline", "profile"]) {
    let files = a.evidence[phase] || {},
      phaseName =
        phase === "baseline"
          ? zh
            ? "无采样"
            : "Unprofiled"
          : zh
            ? "资源采样"
            : "Profiled";
    for (let [name, info] of Object.entries(files))
      groups.push(
        `<button type="button" data-evidence="${esc(info.url)}" data-find="${esc((a.failure?.detail || " ").split(":").at(-1).trim())}" data-sha="${esc(info.sha256)}" data-title="${esc(id)} · ${esc(T.modes[mode])} · ${a.ordinal} · ${phaseName} · ${name}">${esc(phaseName + " · " + name)}</button>`,
      );
  }
  return groups.join("") || `<span class="subtle">${esc(T.missing)}</span>`;
}
function attemptCard(a, mode, id) {
  let r = a.resource || {},
    err = a.failure?.detail || "",
    details = `<div class="detail-grid"><div><h3>${esc(zh ? "评分与响应" : "Verdict and response")}</h3>${facts(
      [
        [T.status, statusChip(a.status)],
        [zh ? "采样轮状态" : "Profiled verdict", statusChip(a.profile_status)],
        [T.duration, esc(ms(a.duration_ms))],
        [zh ? "响应" : "Answer", `<code>${esc(a.answer || "—")}</code>`],
        [T.failure, esc(a.failure?.class || "—")],
      ],
    )}</div><div><h3>${esc(zh ? "Moli 资源" : "Moli resources")}</h3>${facts([
      [T.cpu, esc(ms(r.cpu_total_ms))],
      [T.pss, esc(mib(r.pss_end_bytes))],
      [
        zh ? "观测 PSS 最大值" : "Observed PSS maximum",
        esc(mib(r.pss_peak_bytes)),
      ],
      [T.traffic, esc(fmt(r.fixture_tx_bytes) + " B")],
      [zh ? "采样点" : "Samples", esc(fmt(r.samples_seen))],
    ])}</div></div>${err ? `<div class="error-message"><strong>${esc(T.failure)}:</strong> ${esc(err)}</div>` : ""}<div class="evidence-links">${evidenceButtons(a, mode, id)}</div>`;
  return `<div class="attempt" data-report-detail="${esc(id)}-${mode}-${a.ordinal}"><div class="attempt-head"><strong>${esc(T.attempt.replace("{n}", a.ordinal))}</strong>${statusChip(a.status)}<span class="scalar">${esc(ms(a.duration_ms))} · ${esc(ms(r.cpu_total_ms))} CPU</span></div><details><summary>${esc(T.details)}</summary>${details}</details></div>`;
}
async function resultPage(id, mode) {
  if (!["off", "on"].includes(mode)) throw Error("Unknown mode");
  let c = await getCase(id),
    x = c.modes[mode],
    y = c.modes[mode === "off" ? "on" : "off"];
  let firstFail = x.attempts.find((a) => a.status !== "pass");
  let summary = `<div class="result-summary">${card(zh ? "用例判定" : "Case verdict", caseVerdict(x.passes), zh ? `${x.passes}/${M.protocol.k_runs} 次执行通过；全部通过才计通过用例` : `${x.passes}/${M.protocol.k_runs} attempts passed; all must pass`)}${card(T.duration, ms(x.attempts.reduce((n, a) => n + a.duration_ms, 0)), zh ? `${M.protocol.k_runs} 次总耗时` : `Total for ${M.protocol.k_runs} calls`)}${card(T.relation, esc(T[pairStatus({ off_passes: c.modes.off.passes, on_passes: c.modes.on.passes })]), zh ? "与另一模式比较" : "Compared with the other mode")}</div>`;
  let locator = firstFail
    ? `<div class="notice warn"><strong>${esc(zh ? "失败定位" : "Failure locator")}</strong> · ${esc(T.attempt.replace("{n}", firstFail.ordinal))} · ${esc(firstFail.failure.class || T.unavailable)}<div class="mini">${esc(firstFail.failure.detail || T.unavailable)}</div></div>`
    : `<div class="notice">${esc(zh ? `本模式 ${M.protocol.k_runs} 次调用均通过。` : `All ${M.protocol.k_runs} calls passed in this mode.`)}</div>`;
  return (
    `<div class="breadcrumb"><a href="${link("results")}">${esc(T.nav.results)}</a> / <a href="${link("task/" + id)}">${esc(id)}</a> / ${esc(T.modes[mode])}</div>` +
    hero(c.title, `${T.modes[mode]} · ${c.subset}`) +
    section(zh ? "结果摘要" : "Result summary", "", summary + locator) +
    section(
      zh ? "运行标识" : "Run identities",
      "",
      `<div class="panel">${facts([
        [
          zh ? "无采样轮" : "Unprofiled run",
          `<code>${esc(M.runs[mode + "_baseline"].run_id)}</code>`,
        ],
        [
          zh ? "资源采样轮" : "Profiled run",
          `<code>${esc(M.runs[mode + "_engine"].run_id)}</code>`,
        ],
      ])}</div>`,
    ) +
    section(
      zh
        ? `${M.protocol.k_runs} 次调用与证据`
        : `${M.protocol.k_runs} calls and evidence`,
      zh
        ? "每次调用保留无采样的结果与耗时，以及同题同次资源采样值。点击证据可查看原始评分、输出、日志或 CDP 消息。"
        : "Each call pairs the unprofiled verdict and duration with the profiled resource observation. Open evidence for exact grader, output, log or CDP data.",
      `<div class="attempts">${x.attempts.map((a) => attemptCard(a, mode, id)).join("")}</div>`,
    ) +
    section(
      zh ? "另一模式" : "Other mode",
      "",
      `<div class="panel"><a href="${link("result/" + id + "/" + (mode === "off" ? "on" : "off"))}">${esc(T.modes[mode === "off" ? "on" : "off"])} · ${y.passes === M.protocol.k_runs ? T.pass : T.fail} →</a></div>`,
    )
  );
}
function methodPage() {
  let row = table(
    [zh ? "控制项" : "Control", zh ? "设定" : "Setting"],
    [
      ...[
        [zh ? "浏览器" : "Browser", "Moli 1.1.8 Linux aarch64"],
        [
          zh ? "二进制 SHA-256" : "Binary SHA-256",
          `<code>${M.binary_sha256}</code>`,
        ],
        [
          zh ? "任务" : "Tasks",
          `${M.task_count} × ${M.protocol.k_runs} × ${zh ? "2 种模式" : "2 modes"}`,
        ],
        [
          zh ? "执行" : "Execution",
          zh
            ? "固定脚本；单工作进程；相同种子和任务顺序"
            : "Fixed scripts; one worker; same seed and task order",
        ],
        [
          zh ? "进程复用" : "Process reuse",
          zh
            ? "按 runner 规则复用进程，崩溃或任务要求时重建；轮次之间重新启动"
            : "Runner-controlled reuse; restart on crashes or task requirements and between rounds",
        ],
        [
          zh ? "资源" : "Resources",
          zh
            ? "500 ms 进程树 CPU/PSS，站点应用层流量"
            : "500 ms process-tree CPU/PSS and fixture application traffic",
        ],
        [
          zh ? "冷启动" : "Cold start",
          zh
            ? "交替 5 次/模式，独立容器；不计入 372 题通过率"
            : "Five alternating starts per mode in separate containers; excluded from the 372-case pass rate",
        ],
      ].map(([a, b]) => `<tr><td>${esc(a)}</td><td>${b}</td></tr>`),
    ],
  );
  let versionMethod =
    (zh
      ? "汇总层先比较三种固定配置。1.1.8 关闭和开启布局直接各跑 372 题 × 3 次；关闭布局只通过 1 题。1.1.4 因此只验证这唯一候选题，三次均失败，其余 371 题沿用共同失败关系，得到 0/372。"
      : "The summary first compares three fixed configurations. Both Moli 1.1.8 modes directly ran 372 cases × 3, and layout off passed only one case. Moli 1.1.4 therefore checks that sole candidate; it failed all three attempts, while the other 371 retain the shared-failure relation, yielding 0/372.") +
    (Object.keys(M.fixed_candidates || {}).length
      ? zh
        ? " 1.1.9 修复版分别统计固定关闭与固定开启 layout 的 372 题结果，每题三次。官方 1.1.9 仅测开启 layout。"
        : " Patched Moli 1.1.9 reports fixed layout off and on over 372 cases with three attempts each. Official 1.1.9 is measured with layout on only."
      : "") +
    (zh
      ? " 三种固定配置的资源对照使用独立的 557 题 × 5 次 Linux 实验，图中使用运行前冻结的 1,045 个任务-执行样本。1.1.9 使用相同 557 题 × 5 次 macOS 实验，固定关闭与固定开启 layout 各执行一轮资源采样；资源分别对应同一布局配置的单次调用。逐题证据页聚焦 1.1.8 的两种布局配置。"
      : " The three fixed configurations use a separate 557-task × 5-call Linux resource experiment and a pre-frozen 1,045 task-attempt sample. Moli 1.1.9 uses the same 557-task × 5-call macOS scope, with one profiled run each for fixed layout off and fixed layout on. Resources represent individual calls in the corresponding fixed-layout configuration. Per-task evidence pages focus on the two Moli 1.1.8 layout configurations.");
  return (
    hero(T.nav.method, T.methodIntro) +
    section(
      Object.keys(M.fixed_candidates || {}).length
        ? zh
          ? "固定配置汇总口径"
          : "Fixed-configuration summary"
        : zh
          ? "三方法汇总口径"
          : "Three-configuration summary method",
      versionMethod,
      "",
    ) +
    section(
      zh ? "实验流程" : "Protocol flow",
      "",
      `<div class="protocol-diagram"><pre class="mermaid">flowchart TB
A["${zh ? `冻结 ${M.task_count} 题、版本与种子` : `Freeze ${M.task_count} tasks, versions and seed`}"] --> B["${zh ? "无采样：先无布局，再开布局" : "Unprofiled: layout off, then on"}"]
B --> C["${M.resource_window_corrections?.length ? (zh ? "500 ms 采样：两种模式完整重测" : "500 ms profiling: complete reruns of both modes") : zh ? "500 ms 采样：先开布局，再无布局" : "500 ms profiling: layout on, then off"}"]
C --> D["${zh ? "按任务与调用序号核验；无失败恢复重跑" : "Verify paired task-attempts; no layout recovery retry"}"]</pre></div>`,
    ) +
    section(zh ? "共同控制" : "Shared controls", "", row) +
    section(
      zh ? "指标边界" : "Metric boundaries",
      T.metricNote,
      `<div class="grid two"><div class="panel"><h3>${esc(zh ? "全部调用" : "All calls")}</h3><p>${esc(zh ? "成功、失败和超时都保留。它表示 Agent 用户为同一批任务实际等待及消耗的资源。" : "Passes, failures and timeouts stay in the denominator, representing the user's full task cost.")}</p></div><div class="panel"><h3>${esc(zh ? `共同成功的 ${M.common_pass.count} 次调用` : `The ${M.common_pass.count} calls both modes passed`)}</h3><p>${esc(zh ? "仅用于隔离模式成本的诊断；不能按结果重新选择要测试的任务。" : "A diagnostic to isolate the mode's cost; it cannot select tasks after outcomes are known.")}</p></div><div class="panel"><h3>${esc(zh ? "内存采样" : "Memory sampling")}</h3><p>${esc(zh ? "短任务常只有起点和终点。观测最大值不是未采到的瞬时峰值；结束 PSS 更适合比较常驻内存。" : "Short calls often have only start and end points. The observed maximum is not a high-resolution instantaneous peak; ending PSS compares resident footprint.")}</p></div><div class="panel"><h3>${esc(zh ? "网络与 CPU" : "Traffic and CPU")}</h3><p>${esc(zh ? "站点流量是应用层 HTTP/WebSocket 字节；CPU 来自 /proc 进程树，短命子进程可能漏计。驱动与容器启停不计入 Moli 进程指标。" : "Fixture traffic is application-layer HTTP/WebSocket bytes; CPU uses the /proc process tree and may miss short-lived child processes. Harness and container startup are excluded from Moli process metrics.")}</p></div></div>`,
    )
  );
}
function costSection() {
  let rows = M.task_cost
    .slice(0, 3)
    .map(
      (c) =>
        `<tr><td><a href="${link("task/" + c.id)}">${esc(c.id)}</a></td><td>${caseVerdict(c.off_passes)} → ${caseVerdict(c.on_passes)}</td><td class="num">${fmt(c.off_duration_ms / 1000, 2)} → ${fmt(c.on_duration_ms / 1000, 2)} s</td><td class="num">${fmt((c.off_duration_ms - c.on_duration_ms) / 1000, 2)} s</td><td class="num">${fmt((c.off_cpu_ms - c.on_cpu_ms) / 1000, 2)} s</td></tr>`,
    );
  let total =
      M.metrics.off.baseline.duration_ms_all.sum -
      M.metrics.on.baseline.duration_ms_all.sum,
    top = M.task_cost
      .slice(0, 3)
      .reduce((n, c) => n + c.off_duration_ms - c.on_duration_ms, 0),
    share = total === 0 ? "—" : fmt((top / total) * 100, 1);
  return section(
    zh ? "耗时下降来自哪些任务" : "Where the time reduction came from",
    zh
      ? `以下三题贡献净耗时变化的 ${share}%。正值表示开启布局后耗时下降；负值表示增加。CPU 差值来自资源采样轮，耗时差值来自无采样轮。`
      : `These three tasks account for ${share}% of the net duration change. Positive differences mean lower duration with layout; negative values mean higher duration. CPU differences use profiled calls and duration differences use unprofiled calls.`,
    table(
      [
        T.task,
        zh ? "用例判定" : "Case verdict",
        zh
          ? `${M.protocol.k_runs} 次总耗时`
          : `${M.protocol.k_runs}-call duration`,
        zh ? "耗时减少" : "Time reduction",
        zh ? "CPU 减少" : "CPU reduction",
      ],
      rows,
      "cost-contributors",
    ),
  );
}
function fixedRelations() {
  let ids = Object.keys(M.fixed_candidates || {}),
    pairs = [
      [
        "119_off",
        "119_on",
        zh
          ? "同一修复版：开启 layout 的变化"
          : "Same patched build: enabling layout",
      ],
      [
        "119_official_on",
        "119_on",
        zh
          ? "同为开启 layout：修复版与官方版"
          : "Layout on: patched vs. official",
      ],
    ];
  return pairs
    .filter(([a, b]) => ids.includes(a) && ids.includes(b))
    .map(([a, b, label]) => {
      let counts = { gain: 0, loss: 0, bothPass: 0, bothFail: 0 };
      for (let c of M.fixed_case_outcomes) {
        let x = c.passes[a] === 3,
          y = c.passes[b] === 3;
        counts[x && y ? "bothPass" : y ? "gain" : x ? "loss" : "bothFail"]++;
      }
      return { a, b, label, counts };
    });
}
function fixedComparisonSection() {
  let pairs = fixedRelations();
  if (!pairs.length) return "";
  let rows = pairs.map(
    (p) =>
      `<tr><td>${esc(p.label)}</td><td class="num">${p.counts.gain}</td><td class="num">${p.counts.loss}</td><td class="num">${p.counts.bothPass}</td><td class="num">${p.counts.bothFail}</td></tr>`,
  );
  return section(
    zh ? "布局变化与版本变化" : "Layout changes and version changes",
    zh
      ? "同一修复版的开关对照用于判断布局变化；固定开启 layout 的官方版与修复版对照用于定位版本间变化。下表保留新增通过和退步，不能只看净增加。"
      : "Compare layout off/on within the patched build to measure layout changes. Compare the official and patched builds with layout on to locate version differences. Gains and losses are both retained.",
    table(
      [
        zh ? "对照" : "Comparison",
        zh ? "新增通过" : "Gained",
        zh ? "退步" : "Lost",
        zh ? "均通过" : "Both pass",
        zh ? "均失败" : "Both fail",
      ],
      rows,
    ) +
      `<div class="filters"><label>${esc(zh ? "对照" : "Comparison")}<select id="fixed-relation">${pairs.map((p, i) => `<option value="${i}">${esc(p.label)}</option>`).join("")}</select></label><label>${esc(zh ? "结果关系" : "Outcome relation")}<select id="fixed-outcome"><option value="gain">${esc(zh ? "新增通过" : "Gained")}</option><option value="loss">${esc(zh ? "退步" : "Lost")}</option><option value="bothFail">${esc(zh ? "均失败" : "Both fail")}</option><option value="bothPass">${esc(zh ? "均通过" : "Both pass")}</option><option value="all">${esc(T.all)}</option></select></label></div><div id="fixed-explorer"></div>`,
  );
}
function wireFixedExplorer() {
  let target = document.getElementById("fixed-explorer");
  if (!target) return;
  let pairs = fixedRelations(),
    methods = M.functional_methods.filter((x) => M.fixed_candidates[x.id]);
  let draw = () => {
    let p = pairs[Number(document.getElementById("fixed-relation").value)],
      filter = document.getElementById("fixed-outcome").value,
      items = M.fixed_case_outcomes.filter((c) => {
        let a = c.passes[p.a] === 3,
          b = c.passes[p.b] === 3,
          key = a && b ? "bothPass" : b ? "gain" : a ? "loss" : "bothFail";
        return filter === "all" || key === filter;
      });
    target.innerHTML =
      `<p>${items.length} / ${M.task_count}</p>` +
      table(
        [T.task, ...methods.map((m) => (zh ? m.label : m.label_en))],
        items.map(
          (c) =>
            `<tr><td><a href="${link("fixed/" + c.id)}"><code>${esc(c.id)}</code></a></td>${methods.map((m) => `<td><a href="${link("fixed/" + c.id)}">${c.passes[m.id]}/3 · ${esc(c.passes[m.id] === 3 ? T.pass : T.fail)}</a></td>`).join("")}</tr>`,
        ),
        "fixed-explorer-table",
      );
  };
  document.getElementById("fixed-relation").addEventListener("change", draw);
  document.getElementById("fixed-outcome").addEventListener("change", draw);
  draw();
}
async function fixedCasePage(id) {
  if (!M.fixed_case_outcomes.some((c) => c.id === id))
    throw Error("Unknown case");
  let response = await fetch(
    base + "data/fixed-cases/" + encodeURIComponent(id) + ".json",
  );
  if (!response.ok) throw Error("Case evidence unavailable");
  let c = await response.json(),
    methods = M.functional_methods.filter((x) => c.methods[x.id]);
  let summary = table(
    [
      zh ? "方法" : "Method",
      zh ? "通过次数" : "Passed attempts",
      zh ? "用例判定" : "Case verdict",
    ],
    methods.map(
      (m) =>
        `<tr><td>${esc(zh ? m.label : m.label_en)}</td><td>${c.methods[m.id].passes}/3</td><td>${esc(c.methods[m.id].passes === 3 ? T.pass : T.fail)}</td></tr>`,
    ),
  );
  let details = methods
    .map((m) =>
      section(
        zh ? m.label : m.label_en,
        `SHA-256: ${m.moli_sha256}`,
        c.methods[m.id].attempts
          .map((a) => {
            let evidence = Object.entries(a.evidence)
              .map(
                ([name, e]) =>
                  `<button data-evidence="${esc(e.url)}" data-sha="${esc(e.sha256)}" data-title="${esc(name)}">${esc(name)}</button>`,
              )
              .join(" ");
            return `<details data-fixed-detail="${esc(m.id)}-${a.ordinal}"><summary>${esc(T.attempt.replace("{n}", a.ordinal))} · ${esc(a.status === "pass" ? T.pass : T.fail)}</summary><p>${esc(a.answer || T.unavailable)}</p>${a.status !== "pass" ? `<div class="error-message">${esc(a.failure.detail || a.failure.original_detail || T.unavailable)}</div><p>${esc(a.failure.class || T.unavailable)} · ${esc(T.noHuman)}</p>` : ""}<div class="evidence-links">${evidence}</div></details>`;
          })
          .join(""),
      ),
    )
    .join("");
  return (
    `<div class="breadcrumb"><a href="${link("analysis")}">${esc(T.nav.analysis)}</a> / <code>${esc(id)}</code></div>` +
    hero(c.title, id) +
    section(zh ? "结果摘要" : "Result summary", "", summary) +
    details +
    section(
      zh ? "任务与历史结果" : "Task and historical results",
      "",
      `<a href="${link("task/" + id)}">${esc(zh ? "查看任务定义与 1.1.8 对照" : "View task definition and 1.1.8 comparison")}</a>`,
    )
  );
}
function analysisPage() {
  let p = M.case_outcomes.paired,
    gains = M.cases.filter((c) => pairStatus(c) === "gain"),
    losses = M.cases.filter((c) => pairStatus(c) === "loss");
  let changed = (items) =>
    table(
      [T.task, T.stratum, T.modes.off, T.modes.on],
      items.map(
        (c) =>
          `<tr><td><a href="${link("task/" + c.id)}">${esc(c.id)}</a></td><td>${esc(T.strata[c.stratum] || c.stratum)}</td><td>${caseVerdict(c.off_passes)}</td><td>${caseVerdict(c.on_passes)}</td></tr>`,
      ),
    );
  return (
    hero(T.nav.analysis, T.analysisIntro) +
    fixedComparisonSection() +
    section(
      zh ? "用例通过关系" : "Case outcomes",
      casePopulation(),
      `<div class="grid two">${[
        ["gain", p.off_fail_on_pass],
        ["loss", p.off_pass_on_fail],
        ["bothPass", p.both_pass],
        ["bothFail", p.both_fail],
      ]
        .map(([key, n]) => card(T[key], `${n} / ${M.task_count}`, ""))
        .join("")}</div>`,
    ) +
    section(zh ? "新增通过的用例" : "Cases gained", "", changed(gains)) +
    section(zh ? "退步的用例" : "Cases lost", "", changed(losses)) +
    section(
      zh ? "失败证据" : "Failure evidence",
      "",
      `<div class="notice"><a href="${link("results")}">${esc(zh ? "打开失败筛选，查看每题的程序错误和完整日志。" : "Open the failure filter for exact program errors and full logs.")}</a></div>`,
    ) +
    costSection() +
    section(zh ? "任务类型比较" : "By task family", "", strataTable())
  );
}
function hostLoadTable() {
  if (!M.host_context.rounds) return "";
  let labels = zh
    ? {
        off_baseline: "无布局 · 无采样",
        on_baseline: "开布局 · 无采样",
        off_engine: "无布局 · 资源采样",
        on_engine: "开布局 · 资源采样",
      }
    : {
        off_baseline: "Layout off · unprofiled",
        on_baseline: "Layout on · unprofiled",
        off_engine: "Layout off · profiled",
        on_engine: "Layout on · profiled",
      };
  return section(
    zh ? "宿主机实际负载" : "Observed host load",
    zh
      ? "macOS 每 5 秒记录一次。1 分钟负载包含全部宿主机工作负载；数值不等于 CPU 利用率，也不用于性能排名。"
      : "Recorded on macOS every five seconds. One-minute load includes all host workloads, is not CPU utilization, and is not ranked.",
    table(
      [
        zh ? "运行条件" : "Run condition",
        zh ? "观测点数" : "Samples",
        zh ? "1 分钟负载中位数" : "Median one-minute load",
        zh ? "1 分钟负载最大值" : "Maximum one-minute load",
      ],
      Object.entries(M.host_context.rounds).map(
        ([key, x]) =>
          `<tr><td>${esc(labels[key])}</td><td>${x.samples}</td><td>${fmt(x.macos_load1_median, 2)}</td><td>${fmt(x.macos_load1_max, 2)}</td></tr>`,
      ),
    ),
  );
}
function correctionSection() {
  return (M.resource_window_corrections || [])
    .map((c) =>
      section(
        `${T.modes[c.mode]} · ${zh ? "资源校准更正" : "Resource calibration correction"}`,
        zh
          ? `原 ${c.calls} 次资源测量的耗时变化中位数超过 10% 校准阈值，保留为诊断证据，并以同任务、同种子、同二进制的完整重测替换。成功率与无采样耗时口径保持不变。`
          : `The original ${c.calls}-call resource round exceeded the 10% median timing calibration threshold. It remains diagnostic evidence and was replaced by a full rerun with identical tasks, seed and binary. Acceptance and unprofiled duration are unchanged.`,
        table(
          [
            zh ? "项目" : "Item",
            zh ? "原采样轮" : "Original profiled round",
            zh ? "完整重测轮" : "Complete replacement",
          ],
          [
            `<tr><td>${esc(zh ? "运行 ID" : "Run ID")}</td><td><code>${esc(c.superseded_run)}</code></td><td><code>${esc(c.replacement_run)}</code></td></tr>`,
            `<tr><td>${esc(zh ? "累计 Moli CPU" : "Total Moli CPU")}</td><td>${fmt(c.old_cpu_ms / 1000, 2)} s</td><td>${fmt(c.replacement_cpu_ms / 1000, 2)} s</td></tr>`,
            `<tr><td>${esc(zh ? "观测 PSS 最大值均值" : "Mean observed PSS maximum")}</td><td>${fmt(c.old_pss_observed_max_mean_mib, 2)} MiB</td><td>${fmt(c.replacement_pss_observed_max_mean_mib, 2)} MiB</td></tr>`,
          ],
        ) +
          `<button data-evidence="${esc(c.evidence.url)}" data-sha="${esc(c.evidence.sha256)}" data-title="${esc(T.modes[c.mode])} · ${esc(T.calibration)}">${esc(zh ? "查看原轮次校准数据" : "Inspect original calibration data")}</button>`,
      ),
    )
    .join("");
}
function functionalCorrectionSection() {
  let c = M.functional_contract_correction;
  if (!c) return "";
  return section(
    zh
      ? "Chromium 基线合同修正后的 Moli 补测"
      : "Moli retest after Chromium baseline contract corrections",
    zh
      ? `两道受影响题在三种现有 Moli 配置下各重跑三次，共 ${c.calls} 次调用。结果均仍为失败，因此 0/372、1/372、255/372 不变。`
      : `The two affected cases were rerun three times under each existing Moli configuration (${c.calls} calls). All remain failures, so 0/372, 1/372 and 255/372 are unchanged.`,
    `<div class="panel">${facts([
      [
        zh ? "Lexbench 修正提交" : "Lexbench correction commit",
        `<code>${esc(c.harness_commit)}</code>`,
      ],
      [
        zh ? "覆盖" : "Coverage",
        `${c.task_count} × ${c.configuration_count} × 3 = ${c.calls}`,
      ],
      [
        zh ? "成功率变化" : "Success-rate change",
        esc(c.success_rates_changed ? (zh ? "有" : "Yes") : zh ? "无" : "None"),
      ],
    ])}<button data-evidence="${esc(c.evidence.url)}" data-sha="${esc(c.evidence.sha256)}" data-title="${esc(zh ? "修正合同补测回执" : "Corrected-contract retest receipt")}">${esc(zh ? "查看补测回执" : "Inspect retest receipt")}</button></div>`,
  );
}
function candidateResourceQuality() {
  let methods = macosResourceMethods();
  if (!methods.length) return "";
  let rows = methods.map((item) => {
    let m = item.config.metrics.all_predeclared_calls,
      h = item.config.quality;
    return `<tr><td>${esc(item.label)}</td><td>${fmt(m.rss_peak_mib.n)}/2,785</td><td>${fmt(m.cpu_time_ms.n)}/2,785</td><td>${esc(h.profiled_host.polluted ? (zh ? "未通过" : "Failed") : zh ? "通过" : "Pass")}</td></tr>`;
  });
  return section(
    zh ? "Moli 1.1.9 资源证据质量" : "Moli 1.1.9 resource evidence quality",
    zh
      ? "每个固定配置测一轮，核验完整任务矩阵、二进制身份、资源覆盖与宿主机观测。未单独测量采样器带来的耗时影响。"
      : "Each fixed configuration has one measured run. Checks cover the complete task matrix, binary identity, resource coverage, and host observations. Profiler overhead was not separately measured.",
    table(
      [
        zh ? "配置" : "Configuration",
        zh ? "RSS 覆盖" : "RSS coverage",
        zh ? "CPU 覆盖" : "CPU coverage",
        zh ? "宿主机检查" : "Host check",
      ],
      rows,
    ),
  );
}
function reviewPage() {
  let q = M.quality;
  let rows = ["off", "on"].map(
    (m) =>
      `<tr><td>${esc(T.modes[m])}</td><td>${M.attempts_per_mode}/${M.attempts_per_mode}</td><td>${M.metrics[m].baseline.passes} / ${M.metrics[m].profile.passes}</td><td>${q[m].status_mismatch_count}</td><td>${q[m].baseline_host.polluted || q[m].engine_host.polluted ? esc(zh ? "有" : "Yes") : esc(zh ? "无" : "No")}</td><td>${q[m].resource_missing}</td></tr>`,
  );
  return (
    hero(
      T.nav.review,
      zh
        ? "完整性、采样前后判定与运行环境分别核验。"
        : "Completeness, profiling verdict changes and environment observations are checked separately.",
    ) +
    functionalCorrectionSection() +
    candidateResourceQuality() +
    section(
      zh ? "机器证据完整性" : "Machine evidence coverage",
      "",
      table(
        [
          T.mode,
          zh ? "调用完整" : "Complete calls",
          zh ? "无采样 / 采样通过数" : "Unprofiled / profiled passes",
          zh ? "采样前后判定变化" : "Verdict changes",
          zh ? "容器内环境标记" : "In-container environment flags",
          zh ? "资源缺口" : "Missing resources",
        ],
        rows,
      ),
    ) +
    section(
      T.calibration,
      zh
        ? "与无采样轮逐对比较；时间差包含宿主机负载变化，不能单独解释为采样器开销。主耗时始终采用无采样值。10% 门槛约束 P50，不约束 P95；尾部偏移另列。"
        : "Paired differences include changing host conditions and do not isolate profiler overhead. Primary duration always uses unprofiled values. The 10% limit applies to P50, not P95; tail differences are listed separately.",
      table(
        [
          T.mode,
          zh ? "耗时变化 P50" : "Duration change P50",
          zh ? "耗时变化 P95" : "Duration change P95",
          zh ? "含采集变化 P50" : "Collection-inclusive change P50",
          zh ? "含采集变化 P95" : "Collection-inclusive change P95",
          zh ? "P50 校准阈值检查" : "P50 calibration threshold check",
        ],
        ["off", "on"].map(
          (m) =>
            `<tr><td>${esc(T.modes[m])}</td><td>${fmt(q[m].calibration.median_duration_delta_pct, 2)}%</td><td>${fmt(q[m].calibration.p95_duration_delta_pct, 2)}%</td><td>${fmt(q[m].calibration.median_collection_wall_delta_pct, 2)}%</td><td>${fmt(q[m].calibration.p95_collection_wall_delta_pct, 2)}%</td><td>${esc(q[m].calibration.acceptable ? (zh ? "通过" : "Pass") : zh ? "未通过，见数据模型" : "Failed; see data model")}</td></tr>`,
        ),
      ),
    ) +
    section(
      zh ? "宿主机观测范围" : "Host observation scope",
      "",
      `<div class="notice warn">${esc(M.host_context.shared_macos_host ? (zh ? "容器运行于共享 macOS 宿主机，共用 CPU 与内存。宿主机负载与容器内遥测分别记录，观测范围见下表。" : "Containers share the macOS host CPU and memory. Host load and in-container telemetry are recorded separately; observed coverage appears below.") : zh ? "本组运行时未发现并行测试。容器内遥测的观测范围为 Docker Linux 环境。" : "No concurrent test was observed for this cohort. In-container telemetry covers the Docker Linux environment.")}</div>`,
    ) +
    section(
      zh ? "独立复核" : "Independent review",
      "",
      `<div class="notice warn">${esc(T.noHuman)} · 0/${M.attempts_per_mode * 4} ${zh ? `次调用经过独立逐项审查。${M.review_coverage.unreviewed_program_passes} 次程序通过结果未作独立复核。` : `calls independently reviewed. ${M.review_coverage.unreviewed_program_passes} program passes have not been independently reviewed.`}</div>`,
    ) +
    hostLoadTable() +
    correctionSection() +
    section(
      zh ? "冻结标识" : "Frozen identities",
      "",
      `<div class="panel">${facts([
        [
          zh ? "任务清单 SHA-256" : "Manifest SHA-256",
          `<code>${esc(M.manifest_sha256)}</code>`,
        ],
        [
          zh ? "Moli 二进制 SHA-256" : "Moli binary SHA-256",
          `<code>${esc(M.binary_sha256)}</code>`,
        ],
      ])}</div>`,
    )
  );
}
function guidePage() {
  let p = M.case_outcomes.paired;
  return (
    hero(T.nav.guide, T.guideIntro) +
    functionalMethodSummary() +
    fixedComparisonSection() +
    section(
      zh ? "完成任务的变化" : "Changes in completion",
      "",
      `<div class="notice">${esc(zh ? `按同一组 ${M.task_count} 个用例比较：开启布局新增通过 ${p.off_fail_on_pass} 个用例，${p.off_pass_on_fail} 个用例由通过变为失败。` : `Across the same ${M.task_count} cases, layout gained ${p.off_fail_on_pass} passed cases and lost ${p.off_pass_on_fail}.`)}</div>`,
    ) +
    historicalSection() +
    candidateResourceSection() +
    costSection() +
    section(
      zh ? "资源分项对照" : "Resource comparison",
      zh
        ? "全部调用与独立冷启动样本分开统计。每项绿色标记只表示本列原始数值更低。"
        : "All calls and the separate cold-start sample are distinct populations. Green marks only the lower raw value in each metric.",
      resourceGroups(),
    ) +
    section(
      T.scope,
      T.methodLimit,
      `<div class="notice warn">${esc(zh ? `这 ${M.task_count} 题是 Moli 0.1.1 的全部历史失败用例。通过率描述这一失败子集的恢复情况；耗时包括超时与失败等待，不能当作布局计算的纯开销。` : `These ${M.task_count} tasks are all historical Moli 0.1.1 failures. Acceptance describes recovery within this failure subset. Duration includes timeouts and failed waits and does not isolate layout computation overhead.`)}</div>`,
    )
  );
}
function candidateReproductionSection() {
  let methods = macosResourceMethods();
  if (!methods.length) return "";
  let rows = methods.map((item) => {
    let run = item.config.provenance.profiled;
    return `<tr><td>${esc(item.label)}</td><td><code>${esc(run.run_id)}</code></td><td>2,785/2,785</td><td><code>${esc(run.manifest_sha256.slice(0, 16))}</code></td><td><code>${esc(item.resource.summary.candidate.binary_sha256)}</code></td></tr>`;
  });
  return section(
    zh ? "Moli 1.1.9 资源运行身份" : "Moli 1.1.9 resource run identities",
    zh
      ? "每个固定配置执行 557 题 × 5 次；任务、种子与资源采样协议相同。"
      : "Each fixed configuration runs 557 tasks × 5 calls with the same tasks, seed and sampling protocol.",
    table(
      [
        zh ? "配置" : "Configuration",
        zh ? "运行 ID" : "Run ID",
        zh ? "覆盖" : "Coverage",
        zh ? "清单 SHA-256 前缀" : "Manifest SHA-256 prefix",
        zh ? "二进制 SHA-256" : "Binary SHA-256",
      ],
      rows,
    ),
  );
}
function reproducePage() {
  let rows = Object.entries(M.runs).map(
    ([k, v]) =>
      `<tr><td><code>${esc(k)}</code></td><td><code>${esc(v.run_id)}</code></td><td>${M.attempts_per_mode}/${M.attempts_per_mode}</td><td><code>${esc(v.manifest_sha256.slice(0, 16))}</code></td></tr>`,
  );
  return (
    hero(T.nav.reproduce, reproduceIntro()) +
    section(
      zh ? "运行身份" : "Run identities",
      "",
      table(
        [
          T.mode,
          zh ? "运行 ID" : "Run ID",
          zh ? "结果覆盖" : "Coverage",
          zh ? "清单哈希前缀" : "Manifest hash prefix",
        ],
        rows,
      ),
    ) +
    candidateReproductionSection() +
    section(
      zh ? "复现要点" : "Reproduction controls",
      "",
      `<div class="panel"><p>${esc(zh ? "使用官方 Moli 1.1.8 Linux aarch64 二进制，核对 SHA-256。固定任务文件、种子、单工作进程、相同任务顺序和 Docker 镜像。两种模式分别执行无采样与 500 ms 采样轮，每题每轮三次，比较采样前后判定。" : "Use official Moli 1.1.8 Linux aarch64, verify SHA-256, and freeze tasks, seed, one-worker order and Docker image. Run each mode unprofiled and with 500 ms profiling, three attempts per case per round, then compare verdicts.")}</p>${facts(
        [
          [
            zh ? "宿主机" : "Host",
            esc(
              `${M.environment.macos.cpu} · ${M.environment.macos.logical_cpus} logical CPUs · ${M.environment.macos.memory_bytes / 1073741824} GiB`,
            ),
          ],
          [
            zh ? "Linux 虚拟机" : "Linux VM",
            esc(
              `${M.environment.linux_vm.virtualization} · ${M.environment.linux_vm.cpu_count} vCPU · ${fmt(M.environment.linux_vm.memory_bytes / 1073741824, 2)} GiB · aarch64`,
            ),
          ],
          [
            zh ? "容器镜像 SHA-256" : "Container image SHA-256",
            `<code>${esc(M.environment.image_id)}</code>`,
          ],
          [
            zh ? "Moli SHA-256" : "Moli SHA-256",
            `<code>${esc(M.binary_sha256)}</code>`,
          ],
          [
            zh ? "任务清单 SHA-256" : "Task manifest SHA-256",
            `<code>${esc(M.manifest_sha256)}</code>`,
          ],
          [
            zh ? "评测器源码 SHA-256" : "Runner source SHA-256",
            `<code>${esc(M.protocol.runner_sha256)}</code>`,
          ],
          [
            zh ? "测试站点 SHA-256" : "Fixture tree SHA-256",
            `<code>${esc(M.protocol.fixtures_sha256)}</code>`,
          ],
          [zh ? "随机种子" : "Seed", `<code>${esc(M.protocol.seed)}</code>`],
          [
            zh ? "每题调用次数与并发" : "Calls per task and concurrency",
            `${M.protocol.k_runs} / ${M.protocol.jobs}`,
          ],
        ],
      )}<p><a href="${base}reproduction/cohort.json">${esc(zh ? "下载冻结任务集" : "Download frozen cohort")}</a> · <a href="${base}reproduction/run.py">${esc(zh ? "下载复现脚本" : "Download reproduction script")}</a></p><p><a href="${base}data/model.json">${esc(zh ? "下载报告数据模型" : "Download report data model")}</a></p></div>`,
    )
  );
}
function setNav(page) {
  document
    .getElementById("menu-toggle")
    .setAttribute("aria-label", zh ? "展开或收起导航" : "Toggle navigation");
  document
    .getElementById("site-nav")
    .setAttribute("aria-label", zh ? "报告导航" : "Report navigation");
  document.getElementById("site-nav").innerHTML = Object.entries(T.nav)
    .map(
      ([k, v]) =>
        `<a href="${link(k)}" ${page === k ? 'aria-current="page"' : ""}>${esc(v)}</a>`,
    )
    .join("");
  let other = zh ? "en.html" : "index.html";
  document.getElementById("language").href = base + other + location.hash;
  document.getElementById("language").textContent = zh ? "EN" : "中文";
  document.querySelector(".skip").textContent = zh
    ? "跳到正文"
    : "Skip to content";
  document
    .getElementById("theme")
    .setAttribute("aria-label", zh ? "切换明暗主题" : "Toggle theme");
  document.getElementById("dialog-close").setAttribute("aria-label", T.close);
  document.querySelector(".brand small").textContent = zh
    ? "/ 版本与布局"
    : "/ Versions and layout";
  document.querySelector(".footer span").textContent = zh
    ? `${reportVersion()} · 成功率与资源实验`
    : `${reportVersion()} · compatibility and resource study`;
  document.querySelector(".footer a").textContent = zh
    ? "方法与证据"
    : "Method & evidence";
}
async function render() {
  let r = route(),
    parts = r.split("/"),
    page = parts[0];
  delete document.getElementById("main").dataset.route;
  if (dialog.open) dialog.close();
  try {
    if (!M) {
      let resp = await fetch(base + "data/model.json", { cache: "no-store" });
      if (!resp.ok) throw Error("Model unavailable");
      M = await resp.json();
    }
    setNav(page);
    let html =
      page === "home"
        ? home()
        : ["dataset", "analysis", "results"].includes(page)
          ? page === "analysis"
            ? analysisPage()
            : inventory(page)
          : page === "task"
            ? await taskPage(parts[1])
            : page === "contract"
              ? await taskPage(parts[1], true)
              : page === "fixed"
                ? await fixedCasePage(parts[1])
                : page === "result"
                  ? await resultPage(parts[1], parts[2])
                  : page === "method"
                    ? methodPage()
                    : page === "review"
                      ? reviewPage()
                      : page === "guide"
                        ? guidePage()
                        : page === "reproduce"
                          ? reproducePage()
                          : home();
    document.getElementById("main").innerHTML = html;
    wireFixedExplorer();
    if (page === "method" && window.mermaid) {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme:
          document.documentElement.dataset.theme === "dark"
            ? "dark"
            : "default",
      });
      await mermaid.run({ querySelector: ".mermaid" });
    }
    let caseTitle = M.cases.find((c) => c.id === parts[1])?.title;
    document.title =
      (caseTitle || T.nav[page] || reportTitle()) + ` · ${reportVersion()}`;
    if (["dataset", "results"].includes(page)) wireInventory();
    document.getElementById("site-nav").classList.remove("open");
    document
      .getElementById("menu-toggle")
      .setAttribute("aria-expanded", "false");
    window.scrollTo(0, 0);
    if (page === "result") {
      let n = new URLSearchParams(
        location.hash.split("&").slice(1).join("&"),
      ).get("attempt");
      if (/^[1-5]$/.test(n || "")) {
        let key = parts[1] + "-" + parts[2] + "-" + n;
        let target = [
          ...document.querySelectorAll("[data-report-detail]"),
        ].find((x) => x.dataset.reportDetail === key);
        if (target) {
          target.querySelector("details").open = true;
          target.scrollIntoView({ block: "center" });
          target.querySelector("summary").focus();
        }
      }
    }
    document.getElementById("main").dataset.route = r;
  } catch (e) {
    document.getElementById("main").innerHTML = hero(T.error, e.message);
  }
}

async function showDiagnosis(button) {
  if (!dialog.open) trigger = button;
  let id = button.dataset.diagnosis,
    mode = button.dataset.mode;
  document.getElementById("evidence-title").textContent =
    (zh ? "失败诊断" : "Failure diagnosis") + " · " + T.modes[mode];
  document.getElementById("evidence-body").textContent = T.loading;
  dialog.showModal();
  try {
    let c = await getCase(id),
      a = c.modes[mode].attempts.find((x) => x.status !== "pass");
    if (!a) throw Error("No failure in this result");
    document.getElementById("evidence-body").innerHTML =
      "<h3>" +
      esc(c.title) +
      "</h3>" +
      facts([
        [T.task, "<code>" + esc(id) + "</code>"],
        [T.attempt.replace("{n}", a.ordinal), esc(T.modes[mode])],
        [
          zh ? "程序分类" : "Program classification",
          esc(a.failure.class || T.unavailable),
        ],
        [zh ? "独立复核原因" : "Independently reviewed cause", esc(T.noHuman)],
      ]) +
      '<div class="error-message">' +
      esc(a.failure.detail || T.unavailable) +
      '</div><p><a data-canonical-result href="' +
      link("result/" + id + "/" + mode) +
      "&attempt=" +
      a.ordinal +
      '">' +
      esc(zh ? "完整结果与对应调用" : "Full result and exact attempt") +
      ' →</a></p><div class="evidence-links">' +
      evidenceButtons(a, mode, id) +
      "</div>";
  } catch (e) {
    document.getElementById("evidence-body").textContent =
      T.error + ": " + e.message;
  }
}
document.addEventListener("click", (e) => {
  let b = e.target.closest("[data-diagnosis]");
  if (b) showDiagnosis(b);
});

let close = () => {
  dialog.close();
  trigger?.focus();
};
document.getElementById("dialog-close").addEventListener("click", close);
dialog.addEventListener("close", () => trigger?.focus());
document.addEventListener("click", async (e) => {
  let b = e.target.closest("[data-evidence]");
  if (!b) return;
  if (!dialog.open) trigger = b;
  let title = b.dataset.title || T.evidence;
  document.getElementById("evidence-title").textContent = title;
  document.getElementById("evidence-body").innerHTML =
    `<p class="subtle">SHA-256: <code>${esc(b.dataset.sha)}</code></p><div class="loading">${esc(T.loading)}</div>`;
  dialog.showModal();
  try {
    let resp = await fetch(base + b.dataset.evidence, { cache: "no-store" });
    if (!resp.ok) throw Error("Evidence unavailable");
    let raw = await resp.text();
    document.getElementById("evidence-body").innerHTML =
      `<p class="subtle">SHA-256: <code>${esc(b.dataset.sha)}</code></p><div class="evidence-search"><label for="evidence-query">${esc(zh ? "查找证据" : "Find in evidence")}</label><input id="evidence-query" type="search"><span id="evidence-matches" class="subtle"></span></div><pre id="evidence-text">${esc(raw)}</pre><p><a href="${base + encodeURI(b.dataset.evidence)}" target="_blank" rel="noopener">${esc(T.source)} ↗</a></p>`;
    let field = document.getElementById("evidence-query"),
      content = document.getElementById("evidence-text");
    let find = () => {
      let needle = field.value,
        index = needle ? raw.toLowerCase().indexOf(needle.toLowerCase()) : -1;
      content.innerHTML =
        index < 0
          ? esc(raw)
          : esc(raw.slice(0, index)) +
            '<mark class="evidence-match">' +
            esc(raw.slice(index, index + needle.length)) +
            "</mark>" +
            esc(raw.slice(index + needle.length));
      document.getElementById("evidence-matches").textContent = needle
        ? index < 0
          ? zh
            ? "未找到"
            : "No match"
          : zh
            ? "已定位首处"
            : "First match located"
        : "";
      content.querySelector("mark")?.scrollIntoView({ block: "center" });
    };
    field.addEventListener("input", find);
    if (b.dataset.find && raw.includes(b.dataset.find)) {
      field.value = b.dataset.find;
      find();
    }
  } catch (err) {
    document.getElementById("evidence-body").textContent =
      T.error + ": " + err.message;
  }
});
document.getElementById("theme").addEventListener("click", () => {
  let next = root.dataset.theme === "dark" ? "light" : "dark";
  root.dataset.theme = next;
  try {
    localStorage.setItem("moli-layout-theme", next);
  } catch {
    // Sandboxed report viewers may disable storage; theme switching still works.
  }
  if (route() === "method") render();
});
try {
  root.dataset.theme = localStorage.getItem("moli-layout-theme") || "light";
} catch {
  root.dataset.theme = "light";
}
document.getElementById("menu-toggle").addEventListener("click", (e) => {
  let nav = document.getElementById("site-nav"),
    open = nav.classList.toggle("open");
  e.currentTarget.setAttribute("aria-expanded", String(open));
});
window.addEventListener("hashchange", render);
render();
