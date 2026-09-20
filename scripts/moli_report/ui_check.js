"use strict";
const path = require("path"),
  os = require("os");
const dataHome =
  process.env.BROWSER_EVAL_DATA_HOME ||
  path.join(os.homedir(), ".browser-eval");
const site =
  process.env.MOLI_REPORT_SITE ||
  path.join(dataHome, "reports/moli-1.1.8-failures-site");
const { chromium } = require(
  path.join(
    process.env.LEXBENCH_NODE_MODULES ||
      path.join(dataHome, "work/lexbench-headless-repro/node_modules"),
    "playwright-core",
  ),
);
const fs = require("fs");
const model = JSON.parse(
  fs.readFileSync(path.join(site, "data/model.json"), "utf8"),
);
const successId = model.cases.find(
  (c) => c.on_passes === model.protocol.k_runs,
).id;
const shots =
  process.env.MOLI_REPORT_SHOTS ||
  path.join(dataHome, "reports/moli-visual-qa");
fs.mkdirSync(shots, { recursive: true });
async function ready(page) {
  let expected = decodeURIComponent(
    (new URL(page.url()).hash.match(/page=([^&]+)/) || [])[1] || "home",
  );
  await page
    .locator('main[data-route="' + expected + '"] h1')
    .waitFor({ timeout: 15000 });
}
const origin = process.env.MOLI_REPORT_ORIGIN || "http://127.0.0.1:18766";
const routes = [
  "home",
  "dataset",
  "method",
  "analysis",
  "results",
  "review",
  "guide",
  "reproduce",
  "contract/pw_raw_browser_getbrowsercommandline",
  "task/pw_raw_browser_getbrowsercommandline",
  "result/pw_raw_browser_getbrowsercommandline/off",
  "result/" + successId + "/on",
];
const urls = [origin + "/", origin + "/en/index.html"];
const fixedIds = Object.keys(model.fixed_candidates || {});
const fixedPass = model.fixed_case_outcomes?.find(
  (c) => c.passes["119_on"] === 3,
)?.id;
const fixedFail = model.fixed_case_outcomes?.find(
  (c) => c.passes["119_on"] < 3,
)?.id;
for (const id of new Set([fixedPass, fixedFail].filter(Boolean)))
  routes.push("fixed/" + id);
async function metrics(page) {
  return page.evaluate(() => {
    let groups = new Map();
    for (let cell of document.querySelectorAll("[data-metric]")) {
      let key = cell.getAttribute("data-metric"),
        list = groups.get(key) || [];
      list.push(cell);
      groups.set(key, list);
    }
    for (let [key, cells] of groups) {
      if (cells.length !== 2) throw Error("metric pair " + key);
      let values = cells.map((c) => Number(c.dataset.sort));
      if (values.some((v) => !Number.isFinite(v)))
        throw Error("numeric value " + key);
      let direction = cells[0].dataset.metricDirection;
      let expected =
        values[0] === values[1]
          ? -1
          : direction === "max"
            ? values[0] > values[1]
              ? 0
              : 1
            : values[0] < values[1]
              ? 0
              : 1;
      let marked = cells
        .map((c, i) => (c.classList.contains("winner") ? i : -1))
        .filter((i) => i >= 0);
      if (expected < 0 && marked.length)
        throw Error("tied metric highlighted " + key);
      if (expected >= 0 && (marked.length !== 1 || marked[0] !== expected))
        throw Error("wrong or missing highlight " + key);
    }
    return groups.size;
  });
}
async function caseMetric(page) {
  let cells = await page
    .locator(".core-table tbody tr")
    .first()
    .locator("[data-metric]")
    .evaluateAll((xs) =>
      xs.map((x) => ({ value: Number(x.dataset.sort), text: x.textContent })),
    );
  if (
    cells.length !== 2 ||
    cells.some(
      (c, i) =>
        c.value !== model.case_outcomes[i ? "on" : "off"] ||
        !c.text.includes("/" + model.task_count + " "),
    )
  )
    throw Error("primary metric must count cases");
}
async function versionMetrics(page) {
  let groups = await page
    .locator("[data-version-metric]")
    .evaluateAll((cells) => {
      let out = {};
      for (let cell of cells) {
        let key = cell.dataset.versionMetric;
        (out[key] ??= []).push({
          value: Number(cell.dataset.value),
          direction: cell.dataset.direction,
          winner: cell.classList.contains("winner"),
        });
      }
      return out;
    });
  for (let [key, cells] of Object.entries(groups)) {
    if (cells.length !== 3) throw Error("version metric cardinality " + key);
    let values = cells.map((cell) => cell.value),
      target =
        cells[0].direction === "max"
          ? Math.max(...values)
          : Math.min(...values),
      unique = values.filter((value) => value === target).length === 1,
      winners = cells.filter((cell) => cell.winner);
    if (unique && (winners.length !== 1 || winners[0].value !== target))
      throw Error("version metric highlight " + key);
    if (!unique && winners.length)
      throw Error("tied version metric highlighted " + key);
  }
  return Object.keys(groups).length;
}
async function fixedMetrics(page) {
  let groups = await page
    .locator("[data-resource-metric]")
    .evaluateAll((cells) => {
      let groups = {};
      for (let cell of cells) {
        let key = cell.dataset.resourceMetric + ":" + cell.dataset.population;
        (groups[key] ??= []).push({
          value: Number(cell.dataset.value),
          winner: cell.classList.contains("winner"),
        });
      }
      return groups;
    });
  if (Object.keys(groups).length !== 12) throw Error("macOS metric coverage");
  for (let cells of Object.values(groups)) {
    if (cells.length !== fixedIds.length)
      throw Error("macOS participant coverage");
    let min = Math.min(...cells.map((x) => x.value)),
      unique = cells.filter((x) => x.value === min).length === 1;
    for (let cell of cells)
      if (cell.winner !== (unique && cell.value === min))
        throw Error("macOS unique minimum");
  }
  let functional = page.locator("[data-functional-method]");
  if ((await functional.count()) !== model.functional_methods.length)
    throw Error("functional method coverage");
}
async function run() {
  let browser = await chromium.launch({
    executablePath:
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--no-sandbox"],
  });
  let checked = 0;
  try {
    for (let width of [1440, 390])
      for (let theme of ["light", "dark"])
        for (let base of urls) {
          let page = await browser.newPage({
            viewport: { width, height: 900 },
          });
          await page.goto(base);
          await page.evaluate((theme) => {
            localStorage.setItem("moli-layout-theme", theme);
            document.documentElement.dataset.theme = theme;
          }, theme);
          for (let route of routes) {
            await page.goto(base + "#page=" + encodeURIComponent(route));
            await ready(page);
            let err = await page.locator("main h1").innerText();
            if (/data read|读取失败|Could not load/.test(err))
              throw Error("route failed " + route);
            let overflow = await page.evaluate(
              () =>
                document.documentElement.scrollWidth -
                document.documentElement.clientWidth,
            );
            if (overflow > 1)
              throw Error(
                "horizontal viewport overflow " +
                  route +
                  " " +
                  width +
                  " " +
                  overflow,
              );
            if (
              width === 390 &&
              !(await page
                .locator("th")
                .evaluateAll((cells) =>
                  cells.every(
                    (c) => getComputedStyle(c).whiteSpace === "nowrap",
                  ),
                ))
            )
              throw Error("vertical table labels " + route);
            if (route === "method") {
              await page.locator(".mermaid svg").waitFor({ timeout: 10000 });
            }
            if (["home", "guide"].includes(route) && model.macos_resource)
              await fixedMetrics(page);
            if (route === "home") {
              await caseMetric(page);
              let n = await metrics(page);
              if (n < 8) throw Error("missing comparison metrics");
              if ((await versionMetrics(page)) !== 3)
                throw Error("missing version summary metrics");
              if (
                (await page
                  .locator(".version-summary-table tbody tr")
                  .count()) !== 3
              )
                throw Error("version summary rows");
              let chart = page.locator(".summary-chart img");
              if (
                (await chart.count()) !== (model.macos_resource ? 2 : 1) ||
                !(await chart.evaluateAll((imgs) =>
                  imgs.every((img) => img.complete && img.naturalWidth > 0),
                ))
              )
                throw Error("summary chart unavailable");
              if ((await page.locator("main h1").count()) !== 1)
                throw Error("home summary count");
            }
            if (route === "analysis") {
              let actual = await page
                .locator(".metric-card .value")
                .allTextContents();
              let p = model.case_outcomes.paired;
              let expected = [
                p.off_fail_on_pass,
                p.off_pass_on_fail,
                p.both_pass,
                p.both_fail,
              ].map((n) => n + " / " + model.task_count);
              if (JSON.stringify(actual) !== JSON.stringify(expected))
                throw Error("case relation counts");
              let contributors = page.locator(".cost-contributors tbody tr");
              if ((await contributors.count()) !== 3)
                throw Error("missing cost decomposition");
              if (
                !(await page
                  .locator("main")
                  .innerText()
                  .then((x) =>
                    model.task_cost.slice(0, 3).every((c) => x.includes(c.id)),
                  ))
              )
                throw Error("cost contributors missing");
            }
            if (route.startsWith("result/")) {
              if (
                (await page
                  .locator("section")
                  .filter({ has: page.locator("h2") })
                  .count()) < 2
              )
                throw Error("detail sections");
              if (
                (await page.locator("[data-report-detail]").count()) !==
                model.protocol.k_runs
              )
                throw Error("attempt details");
            }
            if (
              (await page.locator("html").getAttribute("data-theme")) !== theme
            )
              throw Error("theme not applied");
            await page.screenshot({
              path:
                shots +
                "/" +
                (base.includes("/en/") ? "en" : "zh") +
                "-" +
                width +
                "-" +
                theme +
                "-" +
                route.replaceAll("/", "_") +
                ".png",
            });
            if (
              width === 1440 &&
              theme === "light" &&
              !base.includes("/en/") &&
              ["home", "review", "guide"].includes(route)
            )
              await page.screenshot({
                path: shots + "/full-" + route + ".png",
                fullPage: true,
              });
            checked++;
          }
          await page.close();
        }
    let page = await browser.newPage();
    await page.goto(origin + "/#page=home");
    await ready(page);
    await caseMetric(page);
    await page
      .locator(".core-table [data-metric]")
      .first()
      .evaluate((el) => {
        el.dataset.sort = "580";
        el.textContent = "580/750";
      });
    let caseRejected = false;
    try {
      await caseMetric(page);
    } catch {
      caseRejected = true;
    }
    if (!caseRejected) throw Error("call-count headline negative control");
    await page.reload();
    await ready(page);
    let baseline = await metrics(page);
    if (baseline < 8) throw Error("metric coverage");
    let cell = page.locator(".winner[data-metric]").first();
    await cell.evaluate((el) => el.classList.remove("winner"));
    let wrongRejected = false;
    try {
      await metrics(page);
    } catch {
      wrongRejected = true;
    }
    if (!wrongRejected) throw Error("missing-highlight negative control");
    await page.reload();
    await ready(page);
    await page.evaluate(() => {
      let old = document.querySelector(".winner[data-metric]");
      let other = [...document.querySelectorAll("[data-metric]")].find(
        (x) => x !== old && x.dataset.metric === old.dataset.metric,
      );
      old.classList.remove("winner");
      other.classList.add("winner");
    });
    let shiftedRejected = false;
    try {
      await metrics(page);
    } catch {
      shiftedRejected = true;
    }
    if (!shiftedRejected) throw Error("wrong-highlight negative control");
    await page.reload();
    await ready(page);
    let tied = page.locator("[data-metric]").first();
    await tied.evaluate((el) => {
      let other = [...document.querySelectorAll("[data-metric]")].find(
        (x) => x !== el && x.dataset.metric === el.dataset.metric,
      );
      other.dataset.sort = el.dataset.sort;
    });
    let tieRejected = false;
    try {
      await metrics(page);
    } catch {
      tieRejected = true;
    }
    if (!tieRejected) throw Error("tie-highlight negative control");
    await page.reload();
    await ready(page);
    await versionMetrics(page);
    let versionWinner = page.locator(".winner[data-version-metric]").first();
    await versionWinner.evaluate((el) => el.classList.remove("winner"));
    let versionRejected = false;
    try {
      await versionMetrics(page);
    } catch {
      versionRejected = true;
    }
    if (!versionRejected) throw Error("version-highlight negative control");
    await page.goto(
      origin + "/#page=result%2Fpw_raw_browser_getbrowsercommandline%2Foff",
    );
    await ready(page);
    let section = page
      .locator("section")
      .filter({ has: page.locator("h2") })
      .filter({ hasText: "结果摘要" });
    if ((await section.count()) !== 1) throw Error("duplicate summary");
    await page.evaluate(() => {
      let clone = document.querySelector("section").cloneNode(true);
      document.querySelector("main").append(clone);
    });
    if ((await section.count()) === 1)
      throw Error("duplicate-summary negative control");
    await page.reload();
    await ready(page);
    await page.locator("details").first().locator("summary").click();
    let button = page.locator('button[data-evidence$="/cdp.jsonl"]').first();
    await button.click();
    await page.locator("dialog[open]").waitFor();
    await page.waitForFunction(() =>
      document.querySelector("dialog")?.innerText.includes("UnknownMethod"),
    );
    if (
      !(await page
        .locator("dialog")
        .innerText()
        .then(
          (x) =>
            x.includes("Browser.getBrowserCommandLine") &&
            x.includes("UnknownMethod"),
        ))
    )
      throw Error("failure evidence missing");
    await page.locator("mark.evidence-match").waitFor({ state: "visible" });
    if (
      (await page.locator("mark.evidence-match").innerText()) !==
      "UnknownMethod"
    )
      throw Error("error log locator");
    await page.screenshot({ path: shots + "/failure-evidence-dialog.png" });
    await page.keyboard.press("Escape");
    if (await page.locator("dialog[open]").count())
      throw Error("Escape did not close");
    if (!(await button.evaluate((el) => document.activeElement === el)))
      throw Error("focus not restored");
    await page.goto(
      origin + "/#page=" + encodeURIComponent("result/" + successId + "/on"),
    );
    await ready(page);
    await page.locator("details").first().locator("summary").click();
    await page.locator('button[data-evidence$="/grader.json"]').first().click();
    await page.locator("dialog[open]").waitFor();
    await page.waitForFunction(() =>
      document.querySelector("dialog")?.innerText.includes('"ok": true'),
    );
    if (
      !(await page
        .locator("dialog")
        .innerText()
        .then((x) => x.includes('"ok": true')))
    )
      throw Error("success evidence missing");
    await page.goto(origin + "/#page=results");
    await ready(page);
    await page.locator("#query").fill("Browser.getBrowserCommandLine");
    if ((await page.locator("#inventory-table tbody tr").count()) < 1)
      throw Error("error search mismatch");
    let diagnosisTrigger = page.locator(".failure-cell button").first();
    await diagnosisTrigger.click();
    await page.locator("dialog[open] [data-canonical-result]").waitFor();
    if (
      !(await page
        .locator("dialog")
        .innerText()
        .then((x) =>
          x.includes("Browser.getBrowserCommandLine: UnknownMethod"),
        ))
    )
      throw Error("inventory diagnosis missing");
    await page.screenshot({ path: shots + "/inventory-diagnosis-dialog.png" });
    await page.locator("dialog [data-canonical-result]").click();
    await ready(page);
    if (
      !(await page
        .locator("main")
        .innerText()
        .then((x) =>
          x.includes("Browser.getBrowserCommandLine: UnknownMethod"),
        ))
    )
      throw Error("error drilldown mismatch");
    await page.goto(
      origin + "/#page=task%2Fpw_raw_browser_getbrowsercommandline",
    );
    await ready(page);
    if (
      (await page.locator("[data-pair-row]").count()) !== model.protocol.k_runs
    )
      throw Error("paired attempt coverage");
    await page.locator('[data-pair-row="3"] a').first().click();
    await page
      .locator(
        '[data-report-detail="pw_raw_browser_getbrowsercommandline-off-3"] details[open]',
      )
      .waitFor();
    if (!page.url().includes("attempt=3")) throw Error("paired attempt target");
    if (fixedPass && fixedFail) {
      for (let id of [fixedPass, fixedFail]) {
        await page.goto(origin + "/#page=" + encodeURIComponent("fixed/" + id));
        await ready(page);
        let detail = page.locator('[data-fixed-detail="119_on-1"]');
        await detail.locator("summary").click();
        let button = detail
          .locator(
            'button[data-evidence$="/grader.json"],button[data-evidence$="/stdout.log"]',
          )
          .first();
        await button.click();
        await page.locator("dialog[open] #evidence-text").waitFor();
        if (!(await page.locator("#evidence-text").innerText()).trim())
          throw Error("empty fixed-mode evidence");
        await page.keyboard.press("Escape");
        if (await page.locator("dialog[open]").count())
          throw Error("fixed evidence Escape");
        if (!(await button.evaluate((el) => document.activeElement === el)))
          throw Error("fixed evidence focus");
        await page.locator(".breadcrumb a").click();
        await ready(page);
      }
      await page.locator("#fixed-relation").selectOption("1");
      await page.locator("#fixed-outcome").selectOption("all");
      if (
        (await page.locator(".fixed-explorer-table tbody tr").count()) !== 372
      )
        throw Error("fixed case explorer coverage");
    }
    await page.close();
    console.log(
      JSON.stringify({
        route_theme_viewport_checks: checked,
        negative_controls: 6,
        evidence_flows: fixedPass && fixedFail ? 6 : 4,
        result: "pass",
      }),
    );
  } finally {
    await browser.close();
  }
}
run().catch((e) => {
  console.error(e);
  process.exit(1);
});
