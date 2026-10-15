import puppeteer from "puppeteer-core";
import fs from "node:fs";
const t = JSON.parse(fs.readFileSync("src/timeline.json", "utf8"));
const watchdog = setTimeout(() => {
  console.error("Playback test timeout");
  process.exit(1);
}, 90000);
const b = await puppeteer.launch({
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const p = await b.newPage();
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.setViewport({ width: 1440, height: 1000 });
await p.goto("http://127.0.0.1:8769/watch.html", { waitUntil: "networkidle0" });
await p.waitForFunction(() => document.querySelector("video").readyState >= 1);
const duration = await p.$eval("video", (v) => v.duration);
if (Math.abs(duration - t.duration) > 0.15) throw Error("wrong video duration");
const checks = [];
const buttons = await p.$$("[data-seek]");
for (let i = 0; i < buttons.length; i++) {
  const expected = Number(await buttons[i].evaluate((e) => e.dataset.seek));
  await buttons[i].click();
  await p.waitForFunction(
    (expected) => {
      const v = document.querySelector("video");
      return (
        !v.paused && v.currentTime >= expected && v.currentTime < expected + 4
      );
    },
    {},
    expected,
  );
  await new Promise((r) => setTimeout(r, 500));
  const observed = await p.$eval("video", (v) => ({
    time: v.currentTime,
    paused: v.paused,
    ready: v.readyState,
  }));
  checks.push({ expected, ...observed });
}
await p.$eval("video", (v) => v.pause());
await p.goto("http://127.0.0.1:8769/report.html", {
  waitUntil: "networkidle0",
});
const nav = await p.$$("[data-page-link]");
let navigated = 0;
for (const link of nav) {
  const href = await link.evaluate((e) => e.getAttribute("href"));
  if (!href?.startsWith("#")) continue;
  await link.click();
  await p.waitForFunction(
    (h) =>
      document
        .getElementById(decodeURIComponent(h.slice(1)))
        ?.getBoundingClientRect().width > 0,
    { timeout: 5000 },
    href,
  );
  navigated++;
}
await p.goto("http://127.0.0.1:8769/report.html#clients-used", {
  waitUntil: "networkidle0",
});
await p.waitForFunction(
  () =>
    document.querySelector('[data-lens="product"]').getBoundingClientRect()
      .width > 0,
  { timeout: 5000 },
);
const product = await p.$('[data-lens="product"]');
if (product) {
  await product.click();
  if (
    (await product.evaluate((e) => e.getAttribute("aria-pressed"))) !== "true"
  )
    throw Error("product view not activated");
}
await p.setViewport({ width: 390, height: 844 });
await p.goto("http://127.0.0.1:8769/watch.html", { waitUntil: "networkidle0" });
if (
  await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2)
)
  throw Error("mobile overflow");
await p.screenshot({ path: "../review/current/watch-mobile.png" });
fs.writeFileSync(
  "../review/current/playback-controls.json",
  JSON.stringify(
    {
      duration,
      chapterChecks: checks,
      reportNavigation: navigated,
      mobileOverflow: false,
      errors,
    },
    null,
    2,
  ),
);
await b.close();
clearTimeout(watchdog);
if (errors.length) throw Error(errors.join("\n"));
console.log(
  "Playback, chapter navigation, report navigation and mobile layout passed",
);
