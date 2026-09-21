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
      review: "证据与可靠性",
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
      "覆盖 Moli 0.1.1 的全部 372 个所选用例；结果适用于这一失败子集。",
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
      "题目与验收预先冻结，同一构建的两种布局配置只相差是否启用 --layout。默认模式使用低成本模拟几何；--layout 启用按需真实布局，并非持续绘制。可选图片等资源抓取由另一开关控制，未改变。1.1.9 的每个固定配置各测一轮资源，557 题各五次；372 题成功率另测，每题三次。",
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
      review: "Evidence quality",
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
      "This cohort includes all 372 cases selected from Moli 0.1.1 failures; results apply to this failure subset.",
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
      "Task contracts were frozen in advance. Within each build, the two layout configurations differ only in Moli's --layout flag. The default uses low-cost mock geometry; --layout enables real geometry on demand rather than continuous painting. Optional image and media fetching is a separate control and was unchanged. Each fixed 1.1.9 configuration has one 557-task resource run with five calls per task. Success is measured separately on 372 cases with three calls per task.",
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
    ? `通过数按 ${M.task_count} 个用例统计：每题已记录的 ${M.protocol.k_runs} 次无采样执行全部通过，才计为 1 个通过用例。`
    : `Pass counts use ${M.task_count} cases: a case passes only when all ${M.protocol.k_runs} recorded unprofiled attempts pass.`;
const caseVerdict = (n) =>
  statusChip(n === M.protocol.k_runs ? "pass" : "fail");
const metricPopulation = () =>
  zh
    ? `全部 ${M.attempts_per_mode} 次调用，成功与失败都计入；平均、P50 和 P95 使用同一组调用。`
    : `All ${M.attempts_per_mode} predeclared calls, including failures; Mean, P50 and P95 use the same population.`;
const reportSubtitle = () =>
  Object.keys(M.fixed_candidates || {}).length
    ? zh
      ? `比较 ${M.functional_methods.length} 种固定配置在 ${M.task_count} 个所选用例上的成功率，并展示独立资源实验。`
      : `Compare ${M.functional_methods.length} fixed configurations on ${M.task_count} selected cases, with separate resource experiments.`
    : zh
      ? `比较 Moli 1.1.4 关闭布局、1.1.8 关闭布局与 1.1.8 开启布局对 ${M.task_count} 个所选用例的恢复情况，并配套展示独立资源实验。`
      : `Compare recovery of ${M.task_count} selected cases with Moli 1.1.4 layout off, Moli 1.1.8 layout off and Moli 1.1.8 layout on, with a separate resource experiment.`;
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
    ? `依据 Moli 0.1.1 原始结果选取全部 372 个失败用例，覆盖 15 类驱动；不使用结果决定入选。`
    : `All 372 cases selected from Moli 0.1.1 failures are included across 15 driver types, without selection based on current outcomes.`;
const reproduceIntro = () =>
  zh
    ? `所选用例测试和独立的 557 题资源实验均为诊断实验，不计入完整 1,928 题正式分数。1.1.8 直接覆盖 372 题；1.1.4 使用唯一差异候选题验证。三种固定配置使用 Moli 1.1.4 / 1.1.8 Linux aarch64 发行版；1.1.9 的资源实验均使用 macOS arm64 release 构建。修复版成功率沿用 87c87e95 构建，资源使用 f315ff26 release；两类实验分别绑定二进制 SHA-256。`
    : `The selected-case evaluation and separate 557-task resource experiment are diagnostic and excluded from the formal 1,928-case score. Moli 1.1.8 directly covers all 372 cases; Moli 1.1.4 uses the sole differential candidate. The three fixed configurations use Moli 1.1.4 / 1.1.8 Linux aarch64 releases. All 1.1.9 resource configurations use macOS arm64 release builds. Patched functional outcomes retain build 87c87e95; resources use release build f315ff26. The two experiments bind their binary hashes separately.`;
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
function functionalMethodSummary() {
  if (!Object.keys(M.fixed_candidates || {}).length) return "";
  let rows = M.functional_methods,
    total = M.task_count,
    max = Math.max(...rows.map((x) => x.final_passed_cases)),
    unique = rows.filter((x) => x.final_passed_cases === max).length === 1;
  let resourceRows = M.combined_comparison?.configurations || [];
  let body = rows.map((row, index) => {
    let resource = resourceRows.find((r) => r.id === row.id);
    let resourceCells = resource
      ? ["memory", "cpu"]
          .map((metric) => {
            let key = metric === "memory" ? "memory_mib" : "cpu_ms";
            let delta = resource[metric + "_change_pct"];
            let peers = resourceRows.filter(
              (r) => r.platform === resource.platform,
            );
            let minimum = Math.min(...peers.map((r) => r[key]));
            let winner =
              resource[key] === minimum &&
              peers.filter((r) => r[key] === minimum).length === 1;
            let unit =
              metric === "memory" ? resource.memory_kind + " MiB" : "ms";
            return `<td class="num ${winner ? "winner" : ""}" data-combined-metric="${metric}" data-platform="${resource.platform}" data-value="${resource[key]}">${fmt(resource[key], 2)} ${unit}<div class="subtle">${delta === null ? esc(zh ? "跨平台 · 不计算变化" : "Cross-platform · no delta") : signed(delta, 2)}</div></td>`;
          })
          .join("")
      : "";
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
    return `<tr data-functional-method="${esc(row.id)}"><td><strong><span class="method-number">${index + 1}</span> ${esc(zh ? row.label : row.label_en)}</strong>${resource ? `<div class="subtle">${esc(resource.platform)}${row.id === "114_off" ? (zh ? " · 基线" : " · baseline") : ""}</div>` : ""}${evidence}</td><td>${esc(policy)}</td><td>${esc(scope)}</td><td class="num ${unique && row.final_passed_cases === max ? "winner" : ""}" data-functional-final="${row.final_passed_cases}">${fmt(row.final_passed_cases)}/${total} (${fmt((row.final_passed_cases / total) * 100, 1)}%)</td><td class="num">${signed(((row.final_passed_cases - rows[0].final_passed_cases) / total) * 100, 2, zh ? " 个百分点" : " pp")}</td><td class="num">${fmt(row.physical_calls || (row.id === "114_off" ? 3 : total * 3))}</td>${resourceCells}</tr>`;
  });
  return section(
    zh
      ? `${rows.length} 种固定配置的成功率`
      : `Success rates for ${rows.length} fixed configurations`,
    zh
      ? `同一 ${total} 题分母，每题三次全部通过才算成功。开启和关闭 layout 分别统计各自的实际结果；资源使用独立的 557 题实验。变化以 1.1.4 关闭 layout 为基准；资源列为图中的 209 题样本。PSS 与 RSS 不直接比较，绿色标识同平台内唯一最低资源值。`
      : `The same ${total}-case denominator is used; all three attempts must pass. Layout on and off use their own observed results. Resources come from a separate 557-task experiment. Changes are relative to 1.1.4 layout off; resource columns use the plotted 209-task sample. PSS and RSS are not directly comparable. Green marks the unique resource minimum within each platform.`,
    table(
      [
        zh ? "方法" : "Method",
        zh ? "布局" : "Layout",
        zh ? "证据范围" : "Evidence scope",
        zh ? "通过用例" : "Passed cases",
        zh ? "通过率变化" : "Success-rate change",
        zh ? "调用数" : "Calls",
        ...(resourceRows.length
          ? [
              zh
                ? "峰值内存 P50 / 基线变化"
                : "Peak memory P50 / baseline change",
              zh ? "CPU 均值 / 基线变化" : "Mean CPU / baseline change",
            ]
          : []),
      ],
      body,
      "functional-method-table",
    ),
  );
}
function combinedSummaryChart() {
  const chart = `moli-all-success-resources-${zh ? "zh" : "en"}.svg`;
  return `<figure class="summary-chart" data-combined-chart><img src="${base}assets/${chart}" alt="${esc(zh ? `${M.functional_methods.length} 种固定配置的成功率与资源，顺序与总表一致，以 1.1.4 关闭布局为基线` : `${M.functional_methods.length} fixed configurations, aligned with the summary table; baseline: 1.1.4 layout off`)}"><figcaption>${esc(zh ? "成功率：372 个所选用例，每题 3 次全部通过；资源：独立的 557 题 × 5 次实验，图中使用预先冻结的 209 题、1,045 次样本。成功率变化以 1.1.4 为基线；内存和 CPU 仅在同一平台内计算相对基线的变化。" : "Success: 372 selected cases, all three attempts must pass. Resources: a separate 557-task × 5-call experiment; the plot uses the pre-frozen 209-task / 1,045-call sample. Success changes use 1.1.4 as baseline; memory and CPU changes are calculated only within the baseline platform.")}</figcaption></figure>`;
}
function macosResourceMethods() {
  let result = [];
  for (let method of M.functional_methods) {
    if (!method.id.startsWith("119_")) continue;
    const key = method.id.startsWith("119_official_")
      ? "official_resource"
      : "macos_resource";
    const layout = method.policy === "fixed_off" ? "off" : "on";
    let resource = M[key];
    if (!resource?.summary.configurations[layout]) continue;
    result.push({
      id: method.id,
      label: zh ? method.label : method.label_en,
      config: resource.summary.configurations[layout],
      resource,
    });
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
        zh ? "图表样本：209 题" : "Chart sample: 209 tasks",
        1045,
      ],
    ];
  let specs = [
    ["rss_peak_mib", "mean", zh ? "峰值 RSS 均值" : "Peak RSS mean", "MiB"],
    ["rss_peak_mib", "p50", zh ? "峰值 RSS P50" : "Peak RSS P50", "MiB"],
    ["rss_peak_mib", "p95", zh ? "峰值 RSS P95" : "Peak RSS P95", "MiB"],
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
      const reference =
        methods.find(
          (x) =>
            x.id === `119_official_${item.id.endsWith("_off") ? "off" : "on"}`,
        ) || baseline;
      let m = item.config.metrics[scope],
        b = reference.config.metrics[scope];
      return `<tr data-macos-method="${esc(item.id)}"><td><strong>${esc(item.label)}</strong></td><td class="num">${fmt(calls)}</td>${specs.map(([metric, stat, label, unit]) => `<td class="num ${unique(metric, stat, m[metric][stat]) ? "winner" : ""}" data-resource-metric="${metric}-${stat}" data-population="${scope}" data-value="${m[metric][stat]}">${fmt(m[metric][stat], 2)} ${unit}</td>`).join("")}<td class="num">${signed((m.rss_peak_mib.p50 / b.rss_peak_mib.p50 - 1) * 100, 2)}</td><td class="num">${signed((m.cpu_time_ms.mean / b.cpu_time_ms.mean - 1) * 100, 2)}</td></tr>`;
    });
    return (
      `<h3>${esc(title)}</h3><p>${esc(zh ? `峰值 RSS P50 最低：${leader("rss_peak_mib", "p50")}；单次 CPU 均值最低：${leader("cpu_time_ms", "mean")}。资源最低与通过率最高分别判断。` : `Lowest peak RSS P50: ${leader("rss_peak_mib", "p50")}; lowest mean CPU per call: ${leader("cpu_time_ms", "mean")}. Resource minima and task success are evaluated separately.`)}</p>` +
      table(
        [
          zh ? "方法" : "Method",
          zh ? "调用数" : "Calls",
          ...specs.map((x) => x[2]),
          zh ? "峰值 RSS P50 变化" : "Peak RSS P50 change",
          zh ? "CPU 均值变化" : "CPU mean change",
        ],
        rows,
        "candidate-resource-table",
      )
    );
  });
  const referenceLabel = methods.some((x) => x.id === "119_official_off")
    ? zh
      ? "相同 layout 设置的 Moli 1.1.9"
      : "Moli 1.1.9 with the same layout setting"
    : baseline.label;
  let intro = zh
    ? `资源实验与 372 题成功率测试分开：每个配置固定 layout，557 题各执行 5 次。P50 是中位数，P95 是第 95 百分位；每组统计包含同一批成功与失败调用。变化基准：${referenceLabel}。macOS 测量进程树 RSS；Linux PSS 另表展示。`
    : `Resources are measured separately from success on 372 cases: each configuration fixes layout and runs 557 tasks five times. P50 is the median and P95 the 95th percentile; every metric in a scope includes the same passing and failing calls. Change baseline: ${referenceLabel}. macOS measures process-tree RSS; Linux PSS is shown separately.`;
  let evidence = methods
    .filter((x, i, a) => a.findIndex((y) => y.resource === x.resource) === i)
    .map(
      (x) =>
        `<button data-evidence="${esc(x.resource.evidence.url)}" data-sha="${esc(x.resource.evidence.sha256)}" data-title="${esc(x.label)}">${esc(!x.id.startsWith("119_official_") ? (zh ? "修复版资源证据" : "Patched resource evidence") : zh ? "1.1.9 资源证据" : "1.1.9 resource evidence")}</button>`,
    )
    .join(" ");
  if (M.resource_matrix) {
    const item = M.resource_matrix.evidence;
    evidence += ` <button data-evidence="${esc(item.url)}" data-sha="${esc(item.sha256)}" data-title="${esc(zh ? "资源对照调用矩阵" : "Matched resource call matrix")}">${esc(zh ? "串行对照与构建证据" : "Serial comparison and build evidence")}</button>`;
  }
  return section(
    zh ? "Moli 1.1.9 资源统计" : "Moli 1.1.9 resource statistics",
    intro,
    tables.join("") +
      `<p>${esc(zh ? "四组均使用 release 构建，共享同一主机，各自保留独立浏览器进程和采样。任务调用按配置轮换、串行执行，只统计当前配置的进程树。CPU 为用户态与内核态时间之和。" : "All four release configurations share one host and retain separate browser processes and samplers. Task calls run serially in rotating order; each measurement covers only its own process tree. CPU is user plus system time.")}</p><div class="panel">${evidence}</div>`,
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
  return (
    hero(
      reportTitle(),
      reportSubtitle(),
      `<a class="button" href="${link("analysis")}">${esc(T.nav.analysis)}</a><a class="button secondary" href="${link("method")}">${esc(T.method)}</a>`,
    ) +
    functionalMethodSummary() +
    combinedSummaryChart() +
    `<details class="resource-disclosure"><summary>${esc(zh ? "查看完整资源统计与测量范围" : "Inspect resource statistics and measurement scope")}</summary>${candidateResourceSection()}<p>${esc(zh ? "Linux PSS 与 CPU 数值见总表。全部资源统计包含成功与失败调用；图表样本在运行前固定。" : "Linux PSS and CPU values appear in the summary table. Resource statistics include successful and failed calls; the chart sample is fixed before execution.")}</p><a href="${base}data/version-comparison.json">${esc(zh ? "下载 Linux 资源数据" : "Download Linux resource data")}</a></details>` +
    section(
      zh ? "如何查看结果" : "Explore the results",
      "",
      `<p>${esc(zh ? "资源均为 release 构建；macOS RSS 与 Linux PSS 分开比较。修复版成功率来自 87c87e95，资源来自 f315ff26，构建身份见评测方法。" : "Resources use release builds; macOS RSS and Linux PSS are compared separately. Patched success uses 87c87e95 and resources use f315ff26; see the method page for build identities.")}</p><a href="${link("analysis")}">${esc(zh ? "比较新增通过与仍然失败的用例" : "Compare gained passes and remaining failures")}</a> · <a href="${link("guide")}">${esc(T.nav.guide)}</a> · <a href="${link("reproduce")}">${esc(T.nav.reproduce)}</a>`,
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
          `<div class="panel"><p>${esc(zh ? "选取 1,928 题中未能三次全部通过的全部 372 题。保留 Chrome 未通过的题目；Chrome 的结果不作为排除依据。两个布局组使用同一组冻结任务与验收规则。" : M.selection)}</p><p class="subtle">SHA-256: <code>${esc(M.manifest_sha256)}</code></p></div>`,
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
  return (
    hero(
      T.nav.method,
      zh
        ? "固定任务、固定布局与独立资源实验。"
        : "Fixed tasks, fixed layout and a separate resource experiment.",
    ) +
    section(
      zh ? "评测流程" : "Evaluation protocol",
      "",
      `<div class="protocol-diagram"><pre class="mermaid">flowchart TB
A["${zh ? "冻结任务、二进制与验收规则" : "Freeze tasks, binaries and acceptance rules"}"] --> B["${zh ? "固定版本与 layout 配置" : "Fix version and layout configuration"}"]
B --> C["${zh ? "功能：372 题，每题 3 次" : "Functional: 372 cases, three attempts each"}"]
B --> D["${zh ? "资源：557 题，每题 5 次" : "Resources: 557 tasks, five calls each"}"]
C --> E["${zh ? "三次全部通过才算通过" : "A case passes only when all three attempts pass"}"]
D --> F["${zh ? "全部调用统计与固定 209 题图表样本" : "All-call statistics and fixed 209-task chart sample"}"]
E --> G["${zh ? "配置总表与总图" : "Configuration table and chart"}"]
F --> G</pre></div>`,
    ) +
    section(
      zh ? "功能评分与基线" : "Functional scoring and baseline",
      "",
      `<p>${esc(zh ? "所有配置共用 372 题分母。每题三次执行全部通过运行与验收检查，才记为一个通过用例。1.1.8 的两种布局、修复版 1.1.9 的两种布局和 1.1.9 的两种布局均覆盖全部 372 题。" : "All configurations use the same 372-case denominator. A case passes only when execution and acceptance checks pass in all three attempts. Both 1.1.8 layouts, both patched 1.1.9 layouts and both 1.1.9 layouts directly cover all 372 cases.")}</p><p>${esc(zh ? "1.1.4 关闭布局为基线：对 1.1.8 关闭布局唯一通过的候选题实测三次，均失败；其余 371 题沿用共同失败关系。因此表中的 0/372 包含 1 题直接验证和 371 题继承结果，并非对 1.1.4 的完整重测。基线通过率为 0%，通过率变化用百分点表示。" : "Layout-off 1.1.4 is the baseline: the sole candidate passed by layout-off 1.1.8 failed all three direct checks; the other 371 retain the shared-failure relation. Its 0/372 consists of one directly checked case and 371 inherited outcomes, not a complete 1.1.4 rerun. Because baseline success is zero, success changes are expressed in percentage points.")}</p>`,
    ) +
    section(
      zh ? "任务来源与适用范围" : "Task selection and scope",
      "",
      `<p>${esc(zh ? "372 题选自 Moli 0.1.1 在 1,928 题中未达到三次全部通过的全部用例，覆盖 15 类驱动。Chrome 结果不影响入选。其余 1,556 题不在评测范围内，这里的通过率不代表完整 1,928 题的通过率。" : "The 372 cases are all cases from the 1,928-case suite that Moli 0.1.1 did not pass in all three attempts, spanning 15 driver types. Chrome outcomes do not affect selection. The other 1,556 cases are outside this evaluation, so these rates do not describe the full suite.")}</p><a href="${link("dataset")}">${esc(T.nav.dataset)}</a>`,
    ) +
    section(
      zh ? "资源测量与可比范围" : "Resource measurement and comparability",
      "",
      `<p>${esc(zh ? "资源实验单独执行 557 题，每题五次，每种配置共 2,785 次调用。图和总表使用运行前固定的 209 题、1,045 次样本；完整资源表另列全部 557 题。P50 是中位数，P95 是第 95 百分位。" : "Resources use a separate 557-task experiment with five calls per task, or 2,785 calls per configuration. The chart and overview table use 1,045 calls on 209 tasks fixed before execution; detailed resource tables also show all 557 tasks. P50 is the median and P95 the 95th percentile.")}</p><p>${esc(zh ? "1.1.4 / 1.1.8 的资源来自 Linux PSS；1.1.9 来自 macOS RSS。CPU 为进程树用户态与内核态时间之和。跨平台的内存和 CPU 不计算相对 1.1.4 的百分比变化；同平台百分比是样本数值对照。四组 macOS 资源配置均为 release 构建，使用同一冻结调用矩阵，逐次轮换顺序并串行执行。" : "Moli 1.1.4 / 1.1.8 resources use Linux PSS; 1.1.9 uses macOS RSS. CPU is process-tree user plus system time. Cross-platform memory and CPU changes relative to 1.1.4 are not calculated; same-platform percentages describe the measured sample. All four macOS resource configurations use release builds and one frozen call matrix, executed serially with rotating order.")}</p>`,
    ) +
    section(
      zh ? "执行与证据" : "Execution and evidence",
      "",
      `<p>${esc(zh ? "资源实验全局同时只执行一次任务调用。修复版功能结果来自 87c87e95 debug 构建，资源来自 f315ff26 release 构建；成功率仅适用于对应的功能构建。任务定义、程序判定、标准输出与错误日志可从逐题详情检查。未进行独立人工逐次复核。" : "The resource experiment executes one task call globally at a time. Patched functional outcomes use the 87c87e95 debug build; resources use the f315ff26 release build. Success applies to the functional build only. Task definitions, program verdicts, stdout and error logs are available in task details. No independent per-call human review was performed.")}</p><a href="${link("reproduce")}">${esc(T.nav.reproduce)}</a>`,
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
        zh ? "同为开启 layout：修复版与 1.1.9" : "Layout on: patched vs. 1.1.9",
      ],
      [
        "119_official_off",
        "119_official_on",
        zh ? "1.1.9：开启 layout 的变化" : "1.1.9: enabling layout",
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
      ? "同一修复版的开关对照用于判断布局变化；固定开启 layout 的 1.1.9 与修复版对照用于定位版本间变化。下表保留新增通过和退步，不能只看净增加。"
      : "Compare layout off/on within the patched build to measure layout changes. Compare 1.1.9 and patched 1.1.9 with layout on to locate version differences. Gains and losses are both retained.",
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
    section(
      zh ? "任务要求" : "Task requirements for this comparison",
      c.task_definition.description,
      `<details><summary>${esc(zh ? "查看冻结任务定义" : "Inspect frozen task definition")}</summary><p>SHA-256: <code>${esc(c.task_sha256)}</code></p><pre>${esc(JSON.stringify(c.task_definition, null, 2))}</pre></details>`,
    ) +
    details +
    section(
      zh ? "同题版本对照" : "Same-task version comparison",
      "",
      `<a href="${link("task/" + id)}">${esc(zh ? "查看 1.1.8 任务要求与结果" : "View 1.1.8 task requirements and results")}</a>`,
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
      zh ? "1.1.8 用例通过关系" : "Moli 1.1.8 case outcomes",
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
  let rows = M.functional_methods.map(
    (m) =>
      `<tr><td>${esc(zh ? m.label : m.label_en)}</td><td>${m.direct_cases}</td><td>${m.inherited_cases}</td><td>${m.physical_calls || (m.id === "114_off" ? 3 : 1116)}</td><td><code>${esc(m.moli_sha256)}</code></td></tr>`,
  );
  return (
    hero(
      T.nav.review,
      zh
        ? "核对评测覆盖、二进制身份与证据来源。"
        : "Verify evaluation coverage, binary identities and evidence sources.",
    ) +
    section(
      zh ? "功能证据覆盖" : "Functional evidence coverage",
      "",
      table(
        [
          zh ? "方法" : "Method",
          zh ? "直接验证题数" : "Direct cases",
          zh ? "继承结果题数" : "Inherited cases",
          zh ? "调用数" : "Calls",
          "SHA-256",
        ],
        rows,
      ),
    ) +
    candidateResourceQuality() +
    section(
      zh ? "评分与复核范围" : "Scoring and review coverage",
      "",
      `<p>${esc(zh ? "结果来自冻结验收规则的程序判定。报告检查调用矩阵、版本和任务哈希、资源样本数、聚合数值及证据文件完整性。未进行独立人工逐次复核；程序通过结果不等同于人工审查结论。" : "Outcomes come from programmatic evaluation against frozen acceptance rules. Report checks cover call matrices, binary and task hashes, resource sample counts, aggregates and evidence integrity. No independent per-call human review was performed; program passes are not human-review conclusions.")}</p><a href="${base}data/model.json">${esc(zh ? "下载完整数据与证据索引" : "Download the data model and evidence index")}</a>`,
    )
  );
}
function guidePage() {
  const relations = fixedRelations();
  const example = M.fixed_case_outcomes.find(
    (c) => c.passes["119_official_on"] !== 3 && c.passes["119_on"] === 3,
  );
  const patched = M.functional_methods.find((m) => m.id === "119_on");
  const official = M.functional_methods.find((m) => m.id === "119_official_on");
  const relationText = relations
    .map(
      (r) =>
        `<p><strong>${esc(r.label)}</strong>：${esc(zh ? `新增通过 ${r.counts.gain} 题，退步 ${r.counts.loss} 题。` : `${r.counts.gain} cases gained, ${r.counts.loss} lost.`)}</p>`,
    )
    .join("");
  return (
    hero(
      T.nav.guide,
      zh
        ? `修复版开启布局通过 ${patched.final_passed_cases}/${M.task_count} 题，1.1.9 开启布局通过 ${official.final_passed_cases}/${M.task_count} 题。`
        : `Patched layout on passes ${patched.final_passed_cases}/${M.task_count} cases; 1.1.9 layout on passes ${official.final_passed_cases}/${M.task_count}.`,
    ) +
    functionalMethodSummary() +
    combinedSummaryChart() +
    section(
      zh ? "布局与版本分别改变了什么" : "Effects of layout and version",
      "",
      relationText +
        `<a href="${link("analysis")}">${esc(zh ? "查看对应的新增通过和失败用例" : "Inspect gained passes and failures")}</a>`,
    ) +
    section(
      zh ? "如何理解一个通过结果" : "What a passed case means",
      "",
      `<p>${esc(zh ? "每个任务定义固定操作和验收条件。单次工具返回成功不足以判为用例通过；每次执行必须满足评分器检查，而且三次执行全部通过。" : "Each task defines fixed actions and acceptance conditions. A successful tool response alone is insufficient: the evaluator must accept every execution, and all three attempts must pass.")}</p>${example ? `<p><code>${esc(example.id)}</code> · ${esc(zh ? `1.1.9 开启布局 ${example.passes["119_official_on"]}/3，修复版开启布局 ${example.passes["119_on"]}/3。` : `1.1.9 layout on: ${example.passes["119_official_on"]}/3; patched layout on: ${example.passes["119_on"]}/3.`)} <a href="${link("fixed/" + example.id)}">${esc(zh ? "检查任务要求、实际输出和评分证据" : "Inspect requirements, actual output and evaluator evidence")}</a></p>` : ""}`,
    ) +
    candidateResourceSection() +
    section(
      zh ? "结论与适用范围" : "Conclusions and scope",
      "",
      `<p>${esc(zh ? "固定开启布局显著扩大了修复版在这组任务上的通过范围；在相同开启布局设置下，修复版比 1.1.9 通过更多任务。资源比较采用 release 构建的同环境测量；修复版功能与资源来自不同构建，两类结论分别适用于对应构建。1.1.4 的验证范围及任务选择条件见评测方法。" : "Fixed layout on substantially expands the patched build's passing coverage on this cohort. Under layout on, the patched build passes more tasks than Moli 1.1.9. The resource comparison uses release builds measured in the same environment. Patched functional and resource results come from different builds, and each conclusion applies to its corresponding build. The method page defines task selection and 1.1.4 coverage.")}</p><a href="${link("method")}">${esc(T.nav.method)}</a>`,
    ) +
    `<details class="resource-disclosure"><summary>${esc(zh ? "1.1.8 的补充资源指标" : "Additional 1.1.8 resource metrics")}</summary>${resourceGroups()}${successfulDuration()}</details>`
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
    ) +
      (M.resource_matrix
        ? `<p><button data-evidence="reproduction/run_resource_matrix.py" data-sha="${esc(M.resource_matrix.receipt.orchestrator_sha256)}" data-title="${esc(zh ? "资源测量脚本" : "Resource measurement script")}">${esc(zh ? "查看本次资源测量使用的脚本" : "Inspect the script used for these resource measurements")}</button></p>`
        : ""),
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
// Preview hosts may inject a base URL pointing at the Chinese entry.
// Keep report routes in the current document, including its language.
document.addEventListener("click", (event) => {
  const anchor = event.target.closest("a[href]");
  const href = anchor?.getAttribute("href");
  if (
    !href?.startsWith("#page=") ||
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    anchor.target ||
    anchor.hasAttribute("download")
  )
    return;
  event.preventDefault();
  window.location.hash = href;
});
render();
