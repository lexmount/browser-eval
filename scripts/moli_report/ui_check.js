import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
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
const languagePath = path.join(site, "report-languages.json");
const languages = fs.existsSync(languagePath)
  ? JSON.parse(fs.readFileSync(languagePath, "utf8")).languages : ["zh", "en"];
if (![JSON.stringify(["zh"]), JSON.stringify(["zh", "en"])].includes(JSON.stringify(languages)))
  throw Error("unsupported report languages");
const model = JSON.parse(
  fs.readFileSync(path.join(site, "data/model.json"), "utf8"),
);
const expectedMetricGroups =
  new Set(model.combined_comparison.configurations.map((row) => row.platform))
    .size * 2;
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
const urls = languages.map((lang) => origin + (lang === "zh" ? "/" : "/en/index.html"));
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
    const groups = new Map();
    for (const cell of document.querySelectorAll("[data-combined-metric]")) {
      const key = cell.dataset.combinedMetric + ":" + cell.dataset.platform;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(cell);
    }
    for (const [key, cells] of groups) {
      const values = cells.map((c) => Number(c.dataset.value));
      const min = Math.min(...values);
      const unique = values.filter((v) => v === min).length === 1;
      for (const cell of cells)
        if (
          cell.classList.contains("winner") !==
          (unique && Number(cell.dataset.value) === min)
        )
          throw Error("resource highlight " + key);
    }
    return groups.size;
  });
}
async function caseMetric(page) {
  const cells = await page
    .locator("[data-functional-method]")
    .evaluateAll((rows) =>
      rows.map((row) => ({
        id: row.dataset.functionalMethod,
        value: Number(
          row.querySelector("[data-functional-final]").dataset.functionalFinal,
        ),
        text: row.querySelector("[data-functional-final]").textContent,
        winner: row
          .querySelector("[data-functional-final]")
          .classList.contains("winner"),
      })),
    );
  const max = Math.max(
    ...model.functional_methods.map((m) => m.final_passed_cases),
  );
  const unique =
    model.functional_methods.filter((m) => m.final_passed_cases === max)
      .length === 1;
  if (
    cells.length !== model.functional_methods.length ||
    cells.some(
      (c, i) =>
        c.id !== model.functional_methods[i].id ||
        c.value !== model.functional_methods[i].final_passed_cases ||
        !c.text.includes("/" + model.task_count + " ") ||
        c.winner !== (unique && c.value === max),
    )
  )
    throw Error("functional registry or verdict mismatch");
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
  if (Object.keys(groups).length !== 12)
    throw Error("resource metric coverage");
  for (let cells of Object.values(groups)) {
    if (
      cells.length !==
      (model.resource_metrics
        ? model.functional_methods.length
        : fixedIds.length)
    )
      throw Error("resource participant coverage");
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
            if (
              ["home", "guide"].includes(route) &&
              (model.macos_resource || model.linux_resource_profiles)
            )
              await fixedMetrics(page);
            if (route === "home") {
              await caseMetric(page);
              let n = await metrics(page);
              if (n !== expectedMetricGroups)
                throw Error("missing grouped resource metrics");
              let chart = page.locator(".summary-chart img");
              if (
                (await chart.count()) !== 1 ||
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
    // Tokenized preview hosts inject a root base URL even on English entries.
    for (const entry of ["/en.html", "/en/index.html"]) {
      await page.goto(origin + entry + "#page=home");
      if (!languages.includes("en")) {
        for (const width of [1440, 390]) for (const theme of ["light", "dark"]) {
          await page.setViewportSize({ width, height: 900 });
          await page.emulateMedia({ colorScheme: theme });
          await page.goto(origin + entry);
          await page.locator("[data-english-not-generated]").waitFor();
          if (!(await page.locator("main").evaluate((node) => node.scrollWidth <= innerWidth)))
            throw Error("English notice overflows the viewport");
          await page.screenshot({
            path: path.join(shots, `notice-${entry.replaceAll("/", "_")}-${width}-${theme}.png`),
          });
          await page.evaluate((url) => {
            const base = document.createElement("base");
            base.href = url + (location.pathname.includes("/en/") ? "/en/" : "/");
            document.head.prepend(base);
          }, origin);
          await page.locator("[data-english-not-generated] a").click();
          await ready(page);
          if (new URL(page.url()).pathname !== "/index.html")
            throw Error("English notice did not return to Chinese home");
          checked++;
        }
        continue;
      }
      await ready(page);
      await page.evaluate((url) => {
        const base = document.createElement("base");
        base.href = url + "/";
        document.head.prepend(base);
      }, origin);
      for (const route of ["guide", "method", "home"]) {
        await page.locator(`nav a[href="#page=${route}"]`).click();
        await page.locator(`main[data-route="${route}"] h1`).waitFor();
        if (
          (await page.locator("html").getAttribute("lang")) !== "en" ||
          new URL(page.url()).pathname !== entry
        )
          throw Error("preview navigation changed the language entry");
      }
    }
    await page.goto(origin + "/#page=home");
    await ready(page);
    await caseMetric(page);
    await page
      .locator("[data-functional-final]")
      .first()
      .evaluate((el) => {
        el.dataset.functionalFinal = "580";
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
    if (baseline !== expectedMetricGroups) throw Error("metric coverage");
    let cell = page.locator(".winner[data-combined-metric]").first();
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
      let old = document.querySelector(".winner[data-combined-metric]");
      let other = [...document.querySelectorAll("[data-combined-metric]")].find(
        (x) =>
          x !== old &&
          x.dataset.combinedMetric === old.dataset.combinedMetric &&
          x.dataset.platform === old.dataset.platform,
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
    let tied = page.locator(".winner[data-combined-metric]").first();
    await tied.evaluate((el) => {
      let other = [...document.querySelectorAll("[data-combined-metric]")].find(
        (x) =>
          x !== el &&
          x.dataset.combinedMetric === el.dataset.combinedMetric &&
          x.dataset.platform === el.dataset.platform,
      );
      other.dataset.value = el.dataset.value;
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
    await caseMetric(page);
    let versionWinner = page.locator(".winner[data-functional-final]").first();
    await versionWinner.evaluate((el) => el.classList.remove("winner"));
    let versionRejected = false;
    try {
      await caseMetric(page);
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
    const resourceEvidence =
      model.linux_resource_profiles || model.resource_matrix;
    if (resourceEvidence) {
      for (const entry of urls) {
        for (const [route, target] of [
          ["guide", resourceEvidence.evidence.url],
          [
            "reproduce",
            model.linux_resource_profiles
              ? "reproduction/run_linux_resource_profiles.py"
              : "reproduction/run_resource_matrix.py",
          ],
        ]) {
          await page.goto(entry + "#page=" + route);
          await ready(page);
          const button = page.locator(`[data-evidence="${target}"]`);
          const expected = await button.getAttribute("data-sha");
          await button.click();
          const evidence = page.locator("dialog[open] #evidence-text");
          await evidence.waitFor();
          const raw = await evidence.textContent();
          if (
            require("node:crypto")
              .createHash("sha256")
              .update(raw)
              .digest("hex") !== expected
          )
            throw Error("resource evidence payload hash mismatch");
          if (route === "guide") {
            const matrix = JSON.parse(raw);
            if (
              !model.linux_resource_profiles &&
              matrix.calls.length !==
                model.resource_matrix.receipt.physical_calls
            )
              throw Error("resource matrix call coverage");
            for (const id of Object.keys(model.resource_builds))
              if (matrix.measurements[id].length !== 2785)
                throw Error("resource measurement coverage");
          }
          await page.keyboard.press("Escape");
          if (
            (await page.locator("dialog[open]").count()) ||
            !(await button.evaluate((el) => document.activeElement === el))
          )
            throw Error("resource evidence keyboard/focus flow");
        }
      }
    }
    await page.close();
    console.log(
      JSON.stringify({
        route_theme_viewport_checks: checked,
        negative_controls: 6,
        evidence_flows:
          (fixedPass && fixedFail ? 6 : 4) + (resourceEvidence ? 4 : 0),
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
