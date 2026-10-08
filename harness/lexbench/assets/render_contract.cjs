"use strict";
const assert = require("node:assert/strict"),
  fs = require("node:fs"),
  vm = require("node:vm"),
  path = require("node:path");
const siteFlag = process.argv.indexOf("--site"),
  site = siteFlag >= 0 ? process.argv[siteFlag + 1] : null;
const appFlag = process.argv.indexOf("--app");
const app = site
  ? path.join(site, "app.js")
  : appFlag >= 0 ? process.argv[appFlag + 1] : null;
if (!app) throw Error("Supply --site or --app for the report renderer");
const source = fs
  .readFileSync(app, "utf8")
  .split(/async function render\(\)\s*\{/)[0];
function render(model, lang, expression) {
  let context = {
    document: {
      documentElement: { dataset: { base: "" }, lang },
      getElementById: () => null,
    },
    location: { hash: "" },
    MODEL: model,
  };
  vm.createContext(context);
  vm.runInContext(source + "\nM=MODEL;", context);
  return vm.runInContext(expression, context);
}
function resourceHighlights(html) {
  let groups = new Map();
  for (let match of html.matchAll(
    /<td class="([^"]*)" data-resource-metric="([^"]*)" data-population="([^"]*)" data-value="([^"]*)">/g,
  )) {
    let key = match[2] + ":" + match[3];
    let group = groups.get(key) || [];
    group.push({
      value: Number(match[4]),
      highlight: match[1].split(/\s+/).includes("winner"),
    });
    groups.set(key, group);
  }
  assert.equal(groups.size, 12);
  for (let group of groups.values()) {
    let min = Math.min(...group.map((x) => x.value)),
      unique = group.filter((x) => x.value === min).length === 1;
    for (let cell of group)
      assert.equal(cell.highlight, unique && cell.value === min);
  }
}
const stat = (n, value) => ({
  n,
  sum: n * value,
  mean: value,
  p50: value,
  p95: value,
});
const config = (rss, cpu) => ({
  metrics: Object.fromEntries(
    [
      ["all_predeclared_calls", 2785],
      ["frozen_historical_common_pass_calls", 1045],
    ].map(([scope, n]) => [
      scope,
      { rss_peak_mib: stat(n, rss), cpu_time_ms: stat(n, cpu) },
    ]),
  ),
});
const methods = [
  ["119_off", "fixed_off", 1],
  ["119_on", "fixed_on", 2],
  ["119_official_on", "fixed_on", 2],
].map(([id, policy, count]) => ({
  id,
  policy,
  label: id,
  label_en: id,
  direct_cases: 372,
  inherited_cases: 0,
  final_passed_cases: count,
  physical_calls: 1116,
}));
const resource = (configurations, profile) => ({
  summary: { candidate: { profile }, configurations },
  evidence: { url: "data/resource.json", sha256: "a".repeat(64) },
});
const model = {
  task_count: 372,
  functional_methods: methods,
  fixed_candidates: Object.fromEntries(methods.map((m) => [m.id, {}])),
  macos_resource: resource(
    { off: config(100, 10), on: config(100, 20) },
    "debug",
  ),
  official_resource: resource({ on: config(120, 15) }, "official_release"),
  macos_comparison: { baseline: "119_official_on" },
  fixed_case_outcomes: [
    { id: "a", passes: { "119_off": 3, "119_on": 0, "119_official_on": 3 } },
    { id: "b", passes: { "119_off": 0, "119_on": 3, "119_official_on": 3 } },
    { id: "c", passes: { "119_off": 0, "119_on": 3, "119_official_on": 0 } },
  ],
};
for (let lang of ["zh", "en"]) {
  const html = render(model, lang, "candidateResourceSection()");
  resourceHighlights(html);
  assert.throws(() => resourceHighlights(html.replace("num winner", "num")));
  const missing = html.replace(
    'data-resource-metric="rss_peak_mib-mean"',
    'data-metric-removed="rss_peak_mib-mean"',
  );
  assert.throws(() => resourceHighlights(missing));
  assert.ok(html.includes("-16.67%"));
  assert.ok(html.includes("+33.33%"));
  for (let id of methods.map((m) => m.id))
    assert.ok(html.includes(`data-macos-method="${id}"`));
  const summary = render(model, lang, "functionalMethodSummary()");
  assert.ok(!summary.includes("num winner"));
  assert.ok(summary.includes("2/372"));
  assert.ok(!summary.includes("try-layout"));
  const relations = JSON.parse(
    JSON.stringify(render(model, lang, "fixedRelations()")),
  );
  assert.deepEqual(relations[0].counts, {
    gain: 2,
    loss: 1,
    bothPass: 0,
    bothFail: 0,
  });
  assert.deepEqual(relations[1].counts, {
    gain: 1,
    loss: 1,
    bothPass: 1,
    bothFail: 0,
  });
}
console.log(
  "PASS: bilingual fixed methods, gains/losses, resource extrema, ties, percentages and negative controls",
);

// Adding layout-off 1.1.9 must preserve registry order everywhere and retain
// both release resource configurations alongside the patched configurations.
const sevenIds = [
  "114_off",
  "118_off",
  "118_on",
  "119_official_off",
  "119_official_on",
  "119_off",
  "119_on",
];
const sevenModel = {
  ...model,
  functional_methods: sevenIds.map((id) => ({
    ...(methods.find((row) => row.id === id) || methods[0]),
    id,
    label: id,
    label_en: id,
    policy: id.endsWith("_off") ? "fixed_off" : "fixed_on",
  })),
  official_resource: resource(
    { off: config(90, 9), on: config(120, 15) },
    "official_release",
  ),
};
for (const lang of ["zh", "en"]) {
  const ids = Array.from(
    render(sevenModel, lang, "macosResourceMethods().map(row => row.id)"),
  );
  assert.deepEqual(ids, sevenIds.slice(3));
  const html = render(sevenModel, lang, "functionalMethodSummary()");
  assert.deepEqual(
    [...html.matchAll(/data-functional-method="([^"]+)"/g)].map(
      (match) => match[1],
    ),
    sevenIds,
  );
  const resources = render(sevenModel, lang, "candidateResourceSection()");
  resourceHighlights(resources);
  assert.ok(resources.includes("+11.11%")); // patched off vs. release off
  assert.ok(
    resources.includes(
      lang === "zh" ? "相同 layout 设置" : "same layout setting",
    ),
  );
  assert.ok(
    render(sevenModel, lang, "combinedSummaryChart()").includes(
      lang === "zh" ? "7 种固定配置" : "7 fixed configurations",
    ),
  );
}

const linuxModel = {
  ...sevenModel,
  resource_metrics_source: {
    url: "data/earlier-versions.json",
    sha256: "b".repeat(64),
  },
  resource_metrics: Object.fromEntries(
    sevenIds.map((id, i) => [
      id,
      Object.fromEntries(
        [
          ["all_predeclared_calls", 2785],
          ["frozen_historical_common_pass_calls", 1045],
        ].map(([scope, n]) => [
          scope,
          { pss_peak_mib: stat(n, 100 + i * 10), cpu_time_ms: stat(n, 50 - i) },
        ]),
      ),
    ]),
  ),
  linux_resource_profiles: {
    evidence: { url: "data/linux.json", sha256: "a".repeat(64) },
  },
};
for (const lang of ["zh", "en"]) {
  const html = render(linuxModel, lang, "candidateResourceSection()");
  resourceHighlights(html);
  assert.deepEqual(
    [...html.matchAll(/data-resource-method="([^"]+)"/g)].map((m) => m[1]),
    [...sevenIds, ...sevenIds],
  );
  assert.ok(html.includes("+60.00%"));
  assert.ok(html.includes("-12.00%"));
  assert.ok(!html.includes("RSS"));
  assert.ok(html.includes("PSS"));
  assert.throws(() => resourceHighlights(html.replace("num winner", "num")));
  assert.throws(() =>
    resourceHighlights(
      html.replace(
        'class="num " data-resource-metric',
        'class="num winner" data-resource-metric',
      ),
    ),
  );
  const summary = render(linuxModel, lang, "functionalMethodSummary()");
  assert.ok(
    !summary.includes(
      lang === "zh" ? "执行环境不同" : "incompatible execution",
    ),
  );
}

if (site) {
  const measured = JSON.parse(
    fs.readFileSync(path.join(site, "data/model.json"), "utf8"),
  );
  for (let lang of ["zh", "en"]) {
    for (let page of [
      "home()",
      "guidePage()",
      "methodPage()",
      "reviewPage()",
    ]) {
      const html = render(measured, lang, page);
      const prose = html.replace(/<[^>]+>/g, " ");
      assert.ok(
        !/历史|补测|Historical|historical|Corrected-contract/.test(prose),
        page,
      );
      assert.ok(!html.includes("undefined"));
    }
    if (measured.macos_resource || measured.linux_resource_profiles) {
      for (let page of ["home()", "guidePage()"]) {
        const html = render(measured, lang, page);
        resourceHighlights(html);
        assert.ok(html.includes("moli-all-success-resources-"));
        assert.equal((html.match(/data-combined-chart/g) || []).length, 1);
        assert.equal(
          (html.match(/data-functional-method=/g) || []).length,
          measured.functional_methods.length,
        );
        if (page === "home()") {
          assert.ok(!html.includes("version-summary-table"));
          assert.ok(!html.includes("core-table"));
          assert.ok(html.includes('class="resource-disclosure"'));
        }
      }
    }
    for (let relation of render(measured, lang, "fixedRelations()"))
      assert.equal(
        Object.values(relation.counts).reduce((a, b) => a + b, 0),
        372,
      );
  }
  console.log(
    "PASS: delivered report home/guide comparisons and complete relation counts",
  );
}
