"use strict";
const root = document.documentElement,
  base = root.dataset.base || "",
  zh = root.lang.startsWith("zh");
const D = JSON.parse(document.getElementById("report-messages").textContent);


const T = D[zh ? "zh" : "en"];
const message = (key, values = {}) => T.messages[key].replace(/\{(value\d+)\}/g, (_, name) => String(values[name]));
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
  message("the_same_preselected_tasks_calls_each_and_calls_per_mode", {value0: (M.task_count), value1: (M.protocol.k_runs), value2: (M.attempts_per_mode)});
const casePopulation = () =>
  message("pass_counts_use_cases_a_case_passes_only_when_all_recorded_unprof", {value0: (M.task_count), value1: (M.protocol.k_runs)});
const caseVerdict = (n) =>
  statusChip(n === M.protocol.k_runs ? "pass" : "fail");
const metricPopulation = () =>
  message("all_predeclared_calls_including_failures_mean_p50_and_p95_use_the", {value0: (M.attempts_per_mode)});
const reportSubtitle = () => message("compare_fixed_configurations_on_selected_cases_with_separate_reso", {value0: (M.functional_methods.length), value1: (M.task_count)});
const reportVersion = () => {
  const versions = [...new Set((M?.functional_methods || []).flatMap((method) =>
    [...(method.label_en || method.label || "").matchAll(/Moli\s+(\d+\.\d+\.\d+)/g)].map((match) => match[1]),
  ))];
  return versions.length ? `Moli ${versions.join(" / ")}` : "Moli";
};
const reportTitle = () => message("compatibility_and_resources", {value0: (reportVersion())});
const datasetIntro = () =>
  message("all_372_cases_selected_from_moli_0_1_1_failures_are_included_acro");
const reproduceIntro = () => {
  const platforms = M.linux_resource_profiles ? "Linux" :
    M.macos_resource || M.official_resource ? "Linux / macOS" : "Linux";
  return message("functional_cases_directly_tested_cases_have_recorded_attempts_eac", {value0: (M.task_count), value1: (M.protocol.k_runs), value2: (platforms)});
};
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
  if (!M.functional_methods?.length) return "";
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
            return `<td class="num ${winner ? "winner" : ""}" data-combined-metric="${metric}" data-platform="${resource.platform}" data-value="${resource[key]}">${fmt(resource[key], 2)} ${unit}<div class="subtle">${delta === null ? esc(zh ? "—" : "—") : signed(delta, 2)}</div></td>`;
          })
          .join("")
      : "";
    let policy =
      row.policy === "fixed_on"
        ? message("always_on")
        : message("always_off");
    let scope = row.inherited_cases
      ? `${row.direct_cases} ${message("direct")} + ${row.inherited_cases} ${message("shared_failures")}`
      : `${row.direct_cases} ${message("direct_cases")}`;
    let evidence = row.evidence
      ? `<div class="method-evidence"><button data-evidence="${esc(row.evidence.url)}" data-sha="${esc(row.evidence.sha256)}" data-title="${esc(message("fixed_layout_functional_evidence"))}">${esc(message("inspect_case_evidence"))}</button></div>`
      : "";
    return `<tr data-functional-method="${esc(row.id)}"><td><strong><span class="method-number">${index + 1}</span> ${esc(zh ? row.label : row.label_en)}</strong>${resource ? `<div class="subtle">${esc(resource.platform === "Linux" ? "Docker · Linux" : "macOS")}${row.id === "114_off" ? (message("baseline")) : ""}</div>` : ""}${evidence}</td><td>${esc(policy)}</td><td>${esc(scope)}</td><td class="num ${unique && row.final_passed_cases === max ? "winner" : ""}" data-functional-final="${row.final_passed_cases}">${fmt(row.final_passed_cases)}/${total} (${fmt((row.final_passed_cases / total) * 100, 1)}%)</td><td class="num">${signed(((row.final_passed_cases - rows[0].final_passed_cases) / total) * 100, 2, message("pp"))}</td><td class="num">${fmt(row.physical_calls || (row.id === "114_off" ? 3 : total * 3))}</td>${resourceCells}</tr>`;
  });
  return section(
    message("success_rates_for_fixed_configurations", {value0: (rows.length)}),
    M.resource_metrics
      ? message("cases_all_3_attempts_must_pass_resources_use_the_fixed_209_task_s", {value0: (total)})
      : message("cases_all_3_attempts_must_pass_resources_use_the_fixed_209_task_s_49ab3d", {value0: (total)}),
    table(
      [
        message("method"),
        message("layout"),
        message("evidence_scope"),
        message("passed_cases"),
        message("success_rate_change"),
        message("calls"),
        ...(resourceRows.length
          ? [
              message("peak_memory_p50_baseline_change"),
              message("mean_cpu_baseline_change"),
            ]
          : []),
      ],
      body,
      "functional-method-table",
    ),
  );
}
function configurationLabel(id) {
  const method = M.functional_methods.find((row) => row.id === id);
  return method ? (zh ? method.label : method.label_en) : id;
}
function combinedSummaryChart() {
  const chart = `comparison-all-success-resources-${zh ? "zh" : "en"}.svg`;
  return `<figure class="summary-chart" data-combined-chart><img src="${base}assets/${chart}" alt="${esc(message("summary_chart", {value0: M.functional_methods.length, value1: configurationLabel(M.version_comparison?.baseline || M.functional_methods[0]?.id)}))}"></figure>`;
}
function resourceBuildNote() {
  return message("resources_are_compared_within_each_execution_environment_function");
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
  if (M.resource_metrics) return unifiedResourceSection();
  let methods = macosResourceMethods();
  if (!methods.length) return "";
  let baseline = methods.find((x) => x.id === M.macos_comparison.baseline),
    scopes = [
      [
        "all_predeclared_calls",
        message("all_calls_on_557_tasks"),
        2785,
      ],
      [
        "frozen_historical_common_pass_calls",
        message("chart_sample_209_tasks"),
        1045,
      ],
    ];
  let specs = [
    ["rss_peak_mib", "mean", message("peak_rss_mean"), "MiB"],
    ["rss_peak_mib", "p50", message("peak_rss_p50"), "MiB"],
    ["rss_peak_mib", "p95", message("peak_rss_p95"), "MiB"],
    ["cpu_time_ms", "mean", message("cpu_mean"), "ms"],
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
      return winners.length === 1 ? winners[0].label : message("tie");
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
      `<h3>${esc(title)}</h3><p>${esc(message("lowest_peak_rss_p50_lowest_mean_cpu_per_call_resource_minima_and_", {value0: (leader("rss_peak_mib", "p50")), value1: (leader("cpu_time_ms", "mean"))}))}</p>` +
      table(
        [
          message("method"),
          message("calls"),
          ...specs.map((x) => x[2]),
          message("peak_rss_p50_change"),
          message("cpu_mean_change"),
        ],
        rows,
        "candidate-resource-table",
      )
    );
  });
  const referenceLabel = methods.some((x) => x.id === "119_official_off")
    ? message("moli_1_1_9_with_the_same_layout_setting")
    : baseline.label;
  let intro = message("resources_are_measured_separately_from_success_on_372_cases_each_", {value0: (referenceLabel)});
  let evidence = methods
    .filter((x, i, a) => a.findIndex((y) => y.resource === x.resource) === i)
    .map(
      (x) =>
        `<button data-evidence="${esc(x.resource.evidence.url)}" data-sha="${esc(x.resource.evidence.sha256)}" data-title="${esc(x.label)}">${esc(!x.id.startsWith("119_official_") ? (message("patched_resource_evidence")) : message("1_1_9_resource_evidence"))}</button>`,
    )
    .join(" ");
  if (M.resource_matrix) {
    const item = M.resource_matrix.evidence;
    evidence += ` <button data-evidence="${esc(item.url)}" data-sha="${esc(item.sha256)}" data-title="${esc(message("matched_resource_call_matrix"))}">${esc(message("serial_comparison_and_build_evidence"))}</button>`;
  }
  return section(
    message("moli_1_1_9_resource_statistics"),
    intro,
    tables.join("") +
      `<p>${esc(message("all_four_release_configurations_share_one_host_and_retain_separat"))}</p><div class="panel">${evidence}</div>`,
  );
}
function unifiedResourceSection() {
  const methods = M.functional_methods;
  const specs = [
    ["pss_peak_mib", "mean", message("peak_pss_mean"), "MiB"],
    ["pss_peak_mib", "p50", message("peak_pss_p50"), "MiB"],
    ["pss_peak_mib", "p95", message("peak_pss_p95"), "MiB"],
    ["cpu_time_ms", "mean", message("cpu_mean"), "ms"],
    ["cpu_time_ms", "p50", zh ? "CPU P50" : "CPU P50", "ms"],
    ["cpu_time_ms", "p95", zh ? "CPU P95" : "CPU P95", "ms"],
  ];
  let content = [
    ["all_predeclared_calls", message("all_calls_on_557_tasks")],
    [
      "frozen_historical_common_pass_calls",
      message("chart_sample_209_tasks"),
    ],
  ]
    .map(([scope, title]) => {
      const metric = (id, name, stat) =>
        M.resource_metrics[id][scope][name][stat];
      const winners = (name, stat) => {
        const minimum = Math.min(
          ...methods.map((m) => metric(m.id, name, stat)),
        );
        return methods.filter((m) => metric(m.id, name, stat) === minimum);
      };
      const winnerName = (name, stat) => {
        const found = winners(name, stat);
        return found.length === 1
          ? zh
            ? found[0].label
            : found[0].label_en
          : message("tie");
      };
      const baseline = M.resource_metrics["114_off"][scope];
      const rows = methods.map((method) => {
        const values = M.resource_metrics[method.id][scope];
        const cells = specs
          .map(([name, stat, label, unit]) => {
            const best = winners(name, stat);
            const winner = best.length === 1 && best[0].id === method.id;
            return `<td class="num ${winner ? "winner" : ""}" data-resource-metric="${name}-${stat}" data-population="${scope}" data-value="${values[name][stat]}">${fmt(values[name][stat], 2)} ${unit}</td>`;
          })
          .join("");
        return `<tr data-resource-method="${esc(method.id)}"><td><strong>${esc(zh ? method.label : method.label_en)}</strong></td><td class="num">${fmt(values.pss_peak_mib.n)}</td>${cells}<td class="num">${signed((values.pss_peak_mib.p50 / baseline.pss_peak_mib.p50 - 1) * 100, 2)}</td><td class="num">${signed((values.cpu_time_ms.mean / baseline.cpu_time_ms.mean - 1) * 100, 2)}</td></tr>`;
      });
      return (
        `<h3>${esc(title)}</h3><p>${esc(message("lowest_peak_pss_p50_lowest_mean_cpu_per_call_resource_consumption", {value0: (winnerName("pss_peak_mib", "p50")), value1: (winnerName("cpu_time_ms", "mean"))}))}</p>` +
        table(
          [
            message("method"),
            message("calls"),
            ...specs.map((s) => s[2]),
            message("pss_p50_change"),
            message("mean_cpu_change"),
          ],
          rows,
          "unified-resource-table",
        )
      );
    })
    .join("");
  content += `<p>${[
    [
      M.resource_metrics_source,
      message("1_1_4_1_1_8_resource_evidence"),
    ],
    [
      M.linux_resource_profiles?.evidence,
      message("1_1_9_measurements_and_calibration"),
    ],
  ]
    .filter(([evidence]) => evidence?.url && evidence?.sha256)
    .map(
      ([evidence, label]) =>
        `<button data-evidence="${esc(evidence.url)}" data-sha="${esc(evidence.sha256)}" data-title="${esc(label)}">${esc(label)}</button>`,
    )
    .join(" ")}</p>`;
  return section(
    message("resource_statistics_for_seven_configurations"),
    message("all_metrics_include_the_same_passing_and_failing_calls_p50_is_the"),
    content,
  );
}
function unifiedResourceMethodology() {
  const paragraphs = T.resource_methodology;
  return section(
    message("resource_measurement_and_comparability"),
    "",
    paragraphs.map((p) => `<p>${esc(p)}</p>`).join(""),
  );
}
function resourceGroups() {
  let off = M.metrics.off.profile,
    on = M.metrics.on.profile,
    rx = M.network_rx,
    cold = M.cold_start;
  let cpu = table(
    [message("cpu_metric"), T.modes.off, T.modes.on],
    [
      metricRow(
        message("mean_per_call_cpu"),
        off.cpu_ms_all.mean,
        on.cpu_ms_all.mean,
        ms,
      ),
      metricRow(
        message("total_cpu_time"),
        off.cpu_ms_all.sum,
        on.cpu_ms_all.sum,
        (v) => fmt(v / 1000, 2) + " s",
      ),
      metricRow(
        message("per_call_cpu_p50"),
        off.cpu_ms_all.p50,
        on.cpu_ms_all.p50,
        ms,
      ),
      metricRow(
        message("per_call_cpu_p95"),
        off.cpu_ms_all.p95,
        on.cpu_ms_all.p95,
        ms,
      ),
    ],
  );
  let memory = table(
    [message("memory_metric"), T.modes.off, T.modes.on],
    [
      metricRow(
        message("mean_observed_pss_maximum"),
        off.pss_peak_mib_all.mean,
        on.pss_peak_mib_all.mean,
        (v) => fmt(v, 2) + " MiB",
      ),
      metricRow(
        message("observed_pss_maximum_p50"),
        off.pss_peak_mib_all.p50,
        on.pss_peak_mib_all.p50,
        (v) => fmt(v, 2) + " MiB",
      ),
      metricRow(
        message("observed_pss_maximum_p95"),
        off.pss_peak_mib_all.p95,
        on.pss_peak_mib_all.p95,
        (v) => fmt(v, 2) + " MiB",
      ),
      metricRow(
        message("pss_increase_p50"),
        off.pss_delta_mib_all.p50,
        on.pss_delta_mib_all.p50,
        (v) => fmt(v, 2) + " MiB",
      ),
    ],
  );
  let network = table(
    [
      message("fixture_application_traffic"),
      T.modes.off,
      T.modes.on,
    ],
    [
      metricRow(
        message("total_downlink"),
        off.fixture_tx_bytes_all.sum,
        on.fixture_tx_bytes_all.sum,
        (v) => fmt(v) + " B",
      ),
      metricRow(
        message("downlink_p50"),
        off.fixture_tx_bytes_all.p50,
        on.fixture_tx_bytes_all.p50,
        (v) => fmt(v) + " B",
      ),
      metricRow(
        message("total_uplink"),
        rx.off.total_bytes,
        rx.on.total_bytes,
        (v) => fmt(v) + " B",
      ),
      metricRow(
        message("uplink_p50"),
        rx.off.p50_bytes,
        rx.on.p50_bytes,
        (v) => fmt(v) + " B",
      ),
    ],
  );
  let startup = table(
    [
      message("cold_start_metric_off_on", {value0: (M.cold_start.off.n), value1: (M.cold_start.on.n)}),
      T.modes.off,
      T.modes.on,
    ],
    [
      metricRow(
        message("ready_time_p50"),
        cold.off.ready_ms.p50,
        cold.on.ready_ms.p50,
        ms,
      ),
      metricRow(
        message("launch_cpu_p50"),
        cold.off.launch_cpu_ms.p50,
        cold.on.launch_cpu_ms.p50,
        ms,
      ),
      metricRow(
        message("launch_observed_pss_p50"),
        cold.off.launch_pss_bytes.p50,
        cold.on.launch_pss_bytes.p50,
        mib,
      ),
    ],
  );
  let leaderOf = (a, b) => {
    let winner = best(a, b, "min");
    return winner ? T.modes[winner] : message("tie_f4719e");
  };
  let leader = `<div class="notice"><strong>${esc(message("observed_profiled_resource_values"))}</strong> · ${esc(message("all_call_cpu_observed_pss_maximum_p50_fixture_downlink_these_are_", {value0: (leaderOf(off.cpu_ms_all.sum, on.cpu_ms_all.sum)), value1: (leaderOf(off.pss_peak_mib_all.p50, on.pss_peak_mib_all.p50)), value2: (leaderOf(off.fixture_tx_bytes_all.sum, on.fixture_tx_bytes_all.sum))}))}</div>`;
  return (
    leader +
    `<div class="grid"><div data-resource-group="cpu"><h3>${esc(message("cpu_and_computation"))}</h3>${cpu}</div><div data-resource-group="memory"><h3>${esc(message("memory"))}</h3>${memory}</div><div data-resource-group="network"><h3>${esc(message("network_traffic"))}</h3>${network}</div><div data-resource-group="startup"><h3>${esc(message("cold_start"))}</h3>${startup}</div></div>`
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
function inputVersionNote() {
  const count = Object.keys(M.input_definition_variants || {}).length;
  if (!count) return "";
  const shared = M.task_count - count;
  return `<p class="scope-note" data-input-definition-variants="${count}">${esc(message("tasks_used_different_definition_versions_across_configurations_th", {value0: (count), value1: (shared)}))}</p>`;
}

function home() {
  return (
    hero(
      reportTitle(),
      reportSubtitle(),
      `<a class="button" href="${link("analysis")}">${esc(T.nav.analysis)}</a><a class="button secondary" href="${link("method")}">${esc(T.method)}</a>`,
    ) +
    functionalMethodSummary() +
    inputVersionNote() +
    combinedSummaryChart() +
    `<details class="resource-disclosure"><summary>${esc(message("inspect_resource_statistics_and_measurement_scope"))}</summary>${candidateResourceSection()}</details>` +
    section(
      message("explore_the_results"),
      "",
      `<p>${esc(resourceBuildNote())}</p><a href="${link("analysis")}">${esc(message("compare_gained_passes_and_remaining_failures"))}</a> · <a href="${link("guide")}">${esc(T.nav.guide)}</a> · <a href="${link("reproduce")}">${esc(T.nav.reproduce)}</a>`,
    )
  );
}
function successfulDuration() {
  let a = M.metrics.off.baseline,
    b = M.metrics.on.baseline;
  return table(
    [
      message("accepted_call_duration"),
      `${T.modes.off} · n=${a.passes}`,
      `${T.modes.on} · n=${b.passes}`,
    ],
    [
      ["mean", message("mean_accepted_call_duration")],
      ["p50", message("accepted_call_p50_duration")],
      ["p95", message("accepted_call_p95_duration")],
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
    return `<tr><td><a href="${link("results")}&stratum=${encodeURIComponent(s)}">${esc(T.strata[s] || s)}</a><div class="subtle">${a.length} ${message("tasks")}</div></td><td class="num">${off}/${total}</td><td class="num">${on}/${total}</td><td><div class="bar" aria-label="${off} / ${on}"><span class="pass" style="width:${(on / total) * 100}%"></span><span class="fail" style="width:${((total - on) / total) * 100}%"></span></div></td></tr>`;
  });
  return table(
    [
      message("family"),
      T.modes.off,
      T.modes.on,
      message("layout_on_pass_share"),
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
      message("find_and_filter"),
      "",
      filters + `<div id="inventory-table"></div>`,
    ) +
    (kind === "dataset"
      ? section(
          message("selection_rule"),
          T.methodLimit,
          `<div class="panel"><p>${esc(M.selection)}</p><p class="subtle">SHA-256: <code>${esc(M.manifest_sha256)}</code></p></div>`,
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
      [T.task, T.stratum, message("subset"), T.rawContract],
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
      message("attempt"),
      message("off_status"),
      message("on_status"),
      message("off_duration"),
      message("on_duration"),
      message("off_cpu"),
      message("on_cpu"),
      message("off_ending_pss"),
      message("on_ending_pss"),
    ],
    rows,
    "paired-matrix",
  );
}
async function taskPage(id, contract = false) {
  let c = await getCase(id);
  let f = facts([
    [message("task_id"), `<code>${esc(c.id)}</code>`],
    [message("subset"), esc(c.subset)],
    [
      message("task_sha_256"),
      `<code>${esc(c.task_sha256)}</code>`,
    ],
    [
      message("features"),
      c.features.map((x) => `<span class="tag">${esc(x)}</span>`).join(" ") ||
        "—",
    ],
  ]);
  let results = table(
    [T.mode, message("case_verdict"), T.open],
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
          message("same_task_results"),
          casePopulation(),
          results,
        ) +
        section(
          message("paired_attempts"),
          message("duration_comes_from_the_unprofiled_run_cpu_and_ending_pss_come_fr"),
          pairedAttempts(c),
        )) +
    (contract
      ? section(
          T.rawContract,
          "",
          `<div class="detail-grid"><div class="panel">${f}</div><div class="panel"><h3>${esc(message("driver_steps"))}</h3><pre>${esc(JSON.stringify(c.driver, null, 2))}</pre></div></div>`,
        ) +
        section(
          message("acceptance_contract"),
          "",
          `<div class="grid two"><div class="panel"><h3>${esc(message("scene"))}</h3><pre>${esc(JSON.stringify(c.scene, null, 2))}</pre></div><div class="panel"><h3>${esc(message("grader"))}</h3><pre>${esc(JSON.stringify(c.grader, null, 2))}</pre></div></div>`,
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
          ? message("unprofiled")
          : message("profiled");
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
    details = `<div class="detail-grid"><div><h3>${esc(message("verdict_and_response"))}</h3>${facts(
      [
        [T.status, statusChip(a.status)],
        [message("profiled_verdict"), statusChip(a.profile_status)],
        [T.duration, esc(ms(a.duration_ms))],
        [message("answer"), `<code>${esc(a.answer || "—")}</code>`],
        [T.failure, esc(a.failure?.class || "—")],
      ],
    )}</div><div><h3>${esc(message("moli_resources"))}</h3>${facts([
      [T.cpu, esc(ms(r.cpu_total_ms))],
      [T.pss, esc(mib(r.pss_end_bytes))],
      [
        message("observed_pss_maximum"),
        esc(mib(r.pss_peak_bytes)),
      ],
      [T.traffic, esc(fmt(r.fixture_tx_bytes) + " B")],
      [message("samples"), esc(fmt(r.samples_seen))],
    ])}</div></div>${err ? `<div class="error-message"><strong>${esc(T.failure)}:</strong> ${esc(err)}</div>` : ""}<div class="evidence-links">${evidenceButtons(a, mode, id)}</div>`;
  return `<div class="attempt" data-report-detail="${esc(id)}-${mode}-${a.ordinal}"><div class="attempt-head"><strong>${esc(T.attempt.replace("{n}", a.ordinal))}</strong>${statusChip(a.status)}<span class="scalar">${esc(ms(a.duration_ms))} · ${esc(ms(r.cpu_total_ms))} CPU</span></div><details><summary>${esc(T.details)}</summary>${details}</details></div>`;
}
async function resultPage(id, mode) {
  if (!["off", "on"].includes(mode)) throw Error("Unknown mode");
  let c = await getCase(id),
    x = c.modes[mode],
    y = c.modes[mode === "off" ? "on" : "off"];
  let firstFail = x.attempts.find((a) => a.status !== "pass");
  let summary = `<div class="result-summary">${card(message("case_verdict"), caseVerdict(x.passes), message("attempts_passed_all_must_pass", {value0: (x.passes), value1: (M.protocol.k_runs)}))}${card(T.duration, ms(x.attempts.reduce((n, a) => n + a.duration_ms, 0)), message("total_for_calls", {value0: (M.protocol.k_runs)}))}${card(T.relation, esc(T[pairStatus({ off_passes: c.modes.off.passes, on_passes: c.modes.on.passes })]), message("compared_with_the_other_mode"))}</div>`;
  let locator = firstFail
    ? `<div class="notice warn"><strong>${esc(message("failure_locator"))}</strong> · ${esc(T.attempt.replace("{n}", firstFail.ordinal))} · ${esc(firstFail.failure.class || T.unavailable)}<div class="mini">${esc(firstFail.failure.detail || T.unavailable)}</div></div>`
    : `<div class="notice">${esc(message("all_calls_passed_in_this_mode", {value0: (M.protocol.k_runs)}))}</div>`;
  return (
    `<div class="breadcrumb"><a href="${link("results")}">${esc(T.nav.results)}</a> / <a href="${link("task/" + id)}">${esc(id)}</a> / ${esc(T.modes[mode])}</div>` +
    hero(c.title, `${T.modes[mode]} · ${c.subset}`) +
    section(message("result_summary"), "", summary + locator) +
    section(
      message("run_identities"),
      "",
      `<div class="panel">${facts([
        [
          message("unprofiled_run"),
          `<code>${esc(M.runs[mode + "_baseline"].run_id)}</code>`,
        ],
        [
          message("profiled_run"),
          `<code>${esc(M.runs[mode + "_engine"].run_id)}</code>`,
        ],
      ])}</div>`,
    ) +
    section(
      message("calls_and_evidence", {value0: (M.protocol.k_runs)}),
      message("each_call_pairs_the_unprofiled_verdict_and_duration_with_the_prof"),
      `<div class="attempts">${x.attempts.map((a) => attemptCard(a, mode, id)).join("")}</div>`,
    ) +
    section(
      message("other_mode"),
      "",
      `<div class="panel"><a href="${link("result/" + id + "/" + (mode === "off" ? "on" : "off"))}">${esc(T.modes[mode === "off" ? "on" : "off"])} · ${y.passes === M.protocol.k_runs ? T.pass : T.fail} →</a></div>`,
    )
  );
}
function methodPage() {
  return (
    hero(
      T.nav.method,
      message("fixed_tasks_fixed_layout_and_a_separate_resource_experiment"),
    ) +
    section(
      message("evaluation_protocol"),
      "",
      `<div class="protocol-diagram"><pre class="mermaid">flowchart TB
A["${message("freeze_tasks_binaries_and_acceptance_rules")}"] --> B["${message("fix_version_and_layout_configuration")}"]
B --> C["${message("functional_372_cases_three_attempts_each")}"]
B --> D["${message("resources_557_tasks_five_calls_each")}"]
C --> E["${message("a_case_passes_only_when_all_three_attempts_pass")}"]
D --> F["${message("all_call_statistics_and_fixed_209_task_chart_sample")}"]
E --> G["${message("configuration_table_and_chart")}"]
F --> G</pre></div>`,
    ) +
    section(
      message("functional_scoring_and_baseline"),
      "",
      `<p>${esc(message("all_configurations_use_the_same_372_case_denominator_a_case_passe"))}</p><p>${esc(message("layout_off_1_1_4_is_the_baseline_the_sole_candidate_passed_by_lay"))}</p>`,
    ) +
    section(
      message("task_selection_and_scope"),
      "",
      `<p>${esc(message("the_372_cases_are_all_cases_from_the_1_928_case_suite_that_moli_0"))}</p><a href="${link("dataset")}">${esc(T.nav.dataset)}</a>`,
    ) +
    (M.linux_resource_profiles
      ? unifiedResourceMethodology()
      : section(
          message("resource_measurement_and_comparability"),
          "",
          `<p>${esc(message("resources_use_a_separate_557_task_experiment_with_five_calls_per_"))}</p><p>${esc(message("moli_1_1_4_1_1_8_run_in_docker_linux_containers_launched_from_the"))}</p>`,
        )) +
    section(
      message("rss_and_pss_memory_definitions"),
      message("both_use_mib_but_account_for_shared_memory_differently_and_cannot"),
      table(
        [message("property"), "RSS", "PSS"],
        [
          [
            message("what_is_counted"),
            message("pages_currently_resident_in_physical_memory_for_the_process"),
            message("private_resident_pages_plus_a_proportional_share_of_shared_reside"),
          ],
          [
            message("shared_pages"),
            message("counted_for_each_mapping_process_process_tree_sums_can_count_shar"),
            message("counted_proportionally_reducing_double_counting_across_processes"),
          ],
          [
            message("example"),
            message("20_mib_private_plus_10_mib_shared_equally_with_one_other_process_"),
            message("same_example_pss_20_10_2_25_mib"),
          ],
          [
            message("collection_in_this_report"),
            M.linux_resource_profiles
              ? message("this_report_consistently_compares_pss")
              : message("macos_ps_rss_field_summed_over_the_process_tree"),
            message("linux_pss_in_proc_pid_smaps_rollup_summed_over_the_process_tree"),
          ],
          [
            message("comparison_conditions"),
            message("matching_execution_environment_sampling_and_task_scope_cpu_compar"),
            message("matching_conditions_and_shared_process_environment_pss_is_not_rss"),
          ],
        ].map(
          (cells) =>
            `<tr>${cells.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`,
        ),
        "memory-definition-table",
      ) +
        `<p class="subtle"><a href="https://docs.kernel.org/filesystems/proc.html">${esc(message("linux_kernel_rss_and_pss_definitions"))}</a> · <a href="https://developer.apple.com/library/archive/documentation/Performance/Conceptual/ManagingMemory/Articles/VMPages.html">${esc(message("apple_resident_memory_and_shared_mappings"))}</a></p>`,
    ) +
    section(
      message("execution_and_evidence"),
      "",
      `<p>${esc(message("the_resource_experiment_executes_one_task_call_globally_at_a_time"))}</p><a href="${link("reproduce")}">${esc(T.nav.reproduce)}</a>`,
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
    message("where_the_time_reduction_came_from"),
    message("these_three_tasks_account_for_of_the_net_duration_change_positive", {value0: (share)}),
    table(
      [
        T.task,
        message("case_verdict"),
        message("call_duration", {value0: (M.protocol.k_runs)}),
        message("time_reduction"),
        message("cpu_reduction"),
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
        message("same_patched_build_enabling_layout"),
      ],
      [
        "119_official_on",
        "119_on",
        message("layout_on_patched_vs_1_1_9"),
      ],
      [
        "119_official_off",
        "119_official_on",
        message("1_1_9_enabling_layout"),
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
    message("layout_changes_and_version_changes"),
    message("compare_layout_off_on_within_the_patched_build_to_measure_layout_"),
    table(
      [
        message("comparison"),
        message("gained"),
        message("lost"),
        message("both_pass"),
        message("both_fail"),
      ],
      rows,
    ) +
      `<div class="filters"><label>${esc(message("comparison"))}<select id="fixed-relation">${pairs.map((p, i) => `<option value="${i}">${esc(p.label)}</option>`).join("")}</select></label><label>${esc(message("outcome_relation"))}<select id="fixed-outcome"><option value="gain">${esc(message("gained"))}</option><option value="loss">${esc(message("lost"))}</option><option value="bothFail">${esc(message("both_fail"))}</option><option value="bothPass">${esc(message("both_pass"))}</option><option value="all">${esc(T.all)}</option></select></label></div><div id="fixed-explorer"></div>`,
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
      message("method"),
      message("passed_attempts"),
      message("case_verdict"),
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
    section(message("result_summary"), "", summary) +
    section(
      message("task_requirements_for_this_comparison"),
      c.task_definition.description,
      `<details><summary>${esc(message("inspect_frozen_task_definition"))}</summary><p>SHA-256: <code>${esc(c.task_sha256)}</code></p><pre>${esc(JSON.stringify(c.task_definition, null, 2))}</pre></details>`,
    ) +
    details +
    section(
      message("same_task_version_comparison"),
      "",
      `<a href="${link("task/" + id)}">${esc(message("view_1_1_8_task_requirements_and_results"))}</a>`,
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
      message("moli_1_1_8_case_outcomes"),
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
    section(message("cases_gained"), "", changed(gains)) +
    section(message("cases_lost"), "", changed(losses)) +
    section(
      message("failure_evidence"),
      "",
      `<div class="notice"><a href="${link("results")}">${esc(message("open_the_failure_filter_for_exact_program_errors_and_full_logs"))}</a></div>`,
    ) +
    costSection() +
    section(message("by_task_family"), "", strataTable())
  );
}
function candidateResourceQuality() {
  if (M.linux_resource_profiles) {
    const rows = M.functional_methods
      .filter((m) => M.linux_resource_profiles.configurations[m.id])
      .map((method) => {
        const config = M.linux_resource_profiles.configurations[method.id];
        return `<tr><td>${esc(zh ? method.label : method.label_en)}</td><td>${fmt(config.metrics.all_predeclared_calls.pss_peak_mib.n)}/2,785</td><td>${fmt(config.metrics.all_predeclared_calls.cpu_time_ms.n)}/2,785</td><td>${esc(config.calibration.acceptable ? (message("pass")) : message("fail"))}</td><td>${esc(Object.values(config.host).every((h) => !h.polluted) ? (message("pass")) : message("fail"))}</td></tr>`;
      });
    return section(
      message("1_1_9_resource_measurement_quality"),
      message("the_official_procedure_pairs_profiler_off_calibration_with_profil"),
      table(
        [
          message("configuration"),
          message("pss_coverage"),
          message("cpu_coverage"),
          message("profiler_calibration"),
          message("host_check"),
        ],
        rows,
      ),
    );
  }
  let methods = macosResourceMethods();
  if (!methods.length) return "";
  let rows = methods.map((item) => {
    let m = item.config.metrics.all_predeclared_calls,
      h = item.config.quality;
    return `<tr><td>${esc(item.label)}</td><td>${fmt(m.rss_peak_mib.n)}/2,785</td><td>${fmt(m.cpu_time_ms.n)}/2,785</td><td>${esc(h.profiled_host.polluted ? (message("failed")) : message("pass"))}</td></tr>`;
  });
  return section(
    message("moli_1_1_9_resource_evidence_quality"),
    message("each_fixed_configuration_has_one_measured_run_checks_cover_the_co"),
    table(
      [
        message("configuration"),
        message("rss_coverage"),
        message("cpu_coverage"),
        message("host_check"),
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
      message("verify_evaluation_coverage_binary_identities_and_evidence_sources"),
    ) +
    section(
      message("functional_evidence_coverage"),
      "",
      table(
        [
          message("method"),
          message("direct_cases_5505c5"),
          message("inherited_cases"),
          message("calls"),
          "SHA-256",
        ],
        rows,
      ),
    ) +
    candidateResourceQuality() +
    section(
      message("scoring_and_review_coverage"),
      "",
      `<p>${esc(message("outcomes_come_from_programmatic_evaluation_against_frozen_accepta"))}</p><a href="${base}data/model.json">${esc(message("download_the_data_model_and_evidence_index"))}</a>`,
    )
  );
}
function guidePage() {
  const relations = fixedRelations();
  const selected = relations.find((r) => r.counts.gain || r.counts.loss);
  const example = selected && (M.fixed_case_outcomes || []).find((c) =>
    (c.passes[selected.a] === M.protocol.k_runs) !== (c.passes[selected.b] === M.protocol.k_runs),
  );
  const methodLabel = (id) => {
    const method = M.functional_methods.find((m) => m.id === id);
    return method ? (zh ? method.label : method.label_en) : id;
  };
  const relationText = relations.map((r) => {
    const net = r.counts.gain - r.counts.loss;
    return `<p data-relation-from="${esc(r.a)}" data-relation-to="${esc(r.b)}" data-relation-net="${net}"><strong>${esc(methodLabel(r.a))} → ${esc(methodLabel(r.b))}</strong>：${esc(message("outcome_relation", {value0: r.counts.gain, value1: r.counts.loss, value2: signed(net, 0, "")}))}</p>`;
  }).join("");
  return (
    hero(T.nav.guide, reportSubtitle()) +
    functionalMethodSummary() +
    inputVersionNote() +
    combinedSummaryChart() +
    (relations.length ? section(
      message("pass_and_failure_changes_between_configurations"),
      "",
      relationText + `<a href="${link("analysis")}">${esc(message("inspect_gained_passes_and_failures"))}</a>`,
    ) : "") +
    section(
      message("what_a_passed_case_means"),
      casePopulation(),
      `<p>${esc(message("each_task_defines_fixed_actions_and_acceptance_conditions_after_a"))}</p>${example ? `<p><code>${esc(example.id)}</code> · ${esc(methodLabel(selected.a))} ${example.passes[selected.a]}/${M.protocol.k_runs} → ${esc(methodLabel(selected.b))} ${example.passes[selected.b]}/${M.protocol.k_runs} <a href="${link("fixed/" + example.id)}">${esc(message("inspect_requirements_actual_output_and_evaluator_evidence"))}</a></p>` : ""}`,
    ) +
    candidateResourceSection() +
    section(
      message("scope_and_evidence"),
      "",
      `<p>${esc(reproduceIntro())}</p><p>${esc(T.noHuman)}</p><a href="${link("analysis")}">${esc(T.nav.analysis)}</a> · <a href="${link("method")}">${esc(T.nav.method)}</a> · <a href="${link("review")}">${esc(T.nav.review)}</a>`,
    )
  );
}
function candidateReproductionSection() {
  if (M.linux_resource_profiles) {
    const profiles = M.linux_resource_profiles;
    const rows = M.functional_methods
      .filter((m) => profiles.configurations[m.id])
      .map((method) => {
        const build = profiles.configurations[method.id].build;
        const run = profiles.receipt.results[`${method.id}:engine`];
        return `<tr><td>${esc(zh ? method.label : method.label_en)}</td><td><code>${esc(run.run_id)}</code></td><td>${fmt(run.rows)}/2,785</td><td><code>${esc(build.sha256)}</code></td></tr>`;
      });
    return section(
      message("reproduce_1_1_9_linux_release_resources"),
      message("a_fixed_docker_linux_image_on_the_mac_host_557_tasks_five_attempt"),
      table(
        [
          message("configuration"),
          message("measurement_id"),
          message("calls"),
          message("resource_binary_sha_256"),
        ],
        rows,
      ) +
        `<p><button data-evidence="reproduction/run_linux_resource_profiles.py" data-sha="${esc(profiles.receipt.orchestrator_sha256)}" data-title="${esc(message("resource_measurement_script"))}">${esc(message("inspect_the_resource_measurement_script"))}</button></p>`,
    );
  }
  let methods = macosResourceMethods();
  if (!methods.length) return "";
  let rows = methods.map((item) => {
    let run = item.config.provenance.profiled;
    return `<tr><td>${esc(item.label)}</td><td><code>${esc(run.run_id)}</code></td><td>2,785/2,785</td><td><code>${esc(run.manifest_sha256.slice(0, 16))}</code></td><td><code>${esc(item.resource.summary.candidate.binary_sha256)}</code></td></tr>`;
  });
  return section(
    message("moli_1_1_9_resource_run_identities"),
    message("each_fixed_configuration_runs_557_tasks_5_calls_with_the_same_tas"),
    table(
      [
        message("configuration"),
        message("run_id"),
        message("coverage"),
        message("manifest_sha_256_prefix"),
        message("binary_sha_256"),
      ],
      rows,
    ) +
      (M.resource_matrix
        ? `<p><button data-evidence="reproduction/run_resource_matrix.py" data-sha="${esc(M.resource_matrix.receipt.orchestrator_sha256)}" data-title="${esc(message("resource_measurement_script"))}">${esc(message("inspect_the_script_used_for_these_resource_measurements"))}</button></p>`
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
      message("run_identities_0df143"),
      "",
      table(
        [
          T.mode,
          message("run_id"),
          message("coverage_c8f31d"),
          message("manifest_hash_prefix"),
        ],
        rows,
      ),
    ) +
    candidateReproductionSection() +
    section(
      message("reproduction_controls"),
      "",
      `<div class="panel"><p>${esc(message("use_official_moli_1_1_8_linux_aarch64_verify_sha_256_and_freeze_t"))}</p>${facts(
        [
          [
            message("host"),
            esc(
              `${M.environment.macos.cpu} · ${M.environment.macos.logical_cpus} logical CPUs · ${M.environment.macos.memory_bytes / 1073741824} GiB`,
            ),
          ],
          [
            message("linux_vm"),
            esc(
              `${M.environment.linux_vm.virtualization} · ${M.environment.linux_vm.cpu_count} vCPU · ${fmt(M.environment.linux_vm.memory_bytes / 1073741824, 2)} GiB · aarch64`,
            ),
          ],
          [
            message("container_image_sha_256"),
            `<code>${esc(M.environment.image_id)}</code>`,
          ],
          [
            zh ? "Moli SHA-256" : "Moli SHA-256",
            `<code>${esc(M.binary_sha256)}</code>`,
          ],
          [
            message("task_manifest_sha_256"),
            `<code>${esc(M.manifest_sha256)}</code>`,
          ],
          [
            message("runner_source_sha_256"),
            `<code>${esc(M.protocol.runner_sha256)}</code>`,
          ],
          [
            message("fixture_tree_sha_256"),
            `<code>${esc(M.protocol.fixtures_sha256)}</code>`,
          ],
          [message("seed"), `<code>${esc(M.protocol.seed)}</code>`],
          [
            message("calls_per_task_and_concurrency"),
            `${M.protocol.k_runs} / ${M.protocol.jobs}`,
          ],
        ],
      )}<p><a href="${base}reproduction/cohort.json">${esc(message("download_frozen_cohort"))}</a> · <a href="${base}reproduction/run.py">${esc(message("download_reproduction_script"))}</a></p><p><a href="${base}data/model.json">${esc(message("download_report_data_model"))}</a></p></div>`,
    )
  );
}
function setNav(page) {
  document
    .getElementById("menu-toggle")
    .setAttribute("aria-label", message("toggle_navigation"));
  document
    .getElementById("site-nav")
    .setAttribute("aria-label", message("report_navigation"));
  document.getElementById("site-nav").innerHTML = Object.entries(T.nav)
    .map(
      ([k, v]) =>
        `<a href="${link(k)}" ${page === k ? 'aria-current="page"' : ""}>${esc(v)}</a>`,
    )
    .join("");
  let other = zh ? "en/index.html" : "index.html";
  document.getElementById("language").href = base + other + location.hash;
  document.getElementById("language").textContent = message("language_label");
  document.querySelector(".skip").textContent = message("skip_to_content");
  document
    .getElementById("theme")
    .setAttribute("aria-label", message("toggle_theme"));
  document.getElementById("dialog-close").setAttribute("aria-label", T.close);
  document.querySelector(".brand small").textContent = message("versions_and_layout");
  document.querySelector(".footer span").textContent = message("compatibility_and_resource_study", {value0: (reportVersion())});
  document.querySelector(".footer a").textContent = message("method_evidence");
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
    (message("failure_diagnosis")) + " · " + T.modes[mode];
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
          message("program_classification"),
          esc(a.failure.class || T.unavailable),
        ],
        [message("independently_reviewed_cause"), esc(T.noHuman)],
      ]) +
      '<div class="error-message">' +
      esc(a.failure.detail || T.unavailable) +
      '</div><p><a data-canonical-result href="' +
      link("result/" + id + "/" + mode) +
      "&attempt=" +
      a.ordinal +
      '">' +
      esc(message("full_result_and_exact_attempt")) +
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
      `<p class="subtle">SHA-256: <code>${esc(b.dataset.sha)}</code></p><div class="evidence-search"><label for="evidence-query">${esc(message("find_in_evidence"))}</label><input id="evidence-query" type="search"><span id="evidence-matches" class="subtle"></span></div><pre id="evidence-text">${esc(raw)}</pre><p><a href="${base + encodeURI(b.dataset.evidence)}" target="_blank" rel="noopener">${esc(T.source)} ↗</a></p>`;
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
          ? message("no_match")
          : message("first_match_located")
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
