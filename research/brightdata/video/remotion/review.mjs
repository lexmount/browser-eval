import { bundle } from "@remotion/bundler";
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
const timeline = JSON.parse(fs.readFileSync("src/timeline.json", "utf8"));
const root = await bundle({
  entryPoint: path.resolve("src/index.tsx"),
  publicDir: path.resolve("public"),
});
const server = http.createServer((req, res) => {
  let p = path.join(root, decodeURIComponent(req.url.split("?")[0]));
  if (fs.existsSync(p) && fs.statSync(p).isDirectory())
    p = path.join(p, "index.html");
  try {
    res.setHeader(
      "Content-Type",
      p.endsWith(".js")
        ? "text/javascript"
        : p.endsWith(".html")
          ? "text/html"
          : p.endsWith(".png")
            ? "image/png"
            : "application/octet-stream",
    );
    res.end(fs.readFileSync(p));
  } catch {
    res.statusCode = 404;
    res.end();
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const browser = await puppeteer.launch({
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.setViewport({ width: 1920, height: 1080 });
await page.evaluateOnNewDocument(() => {
  window.remotion_isMainTab = true;
  window.remotion_puppeteerTimeout = 30000;
  window.remotion_inputProps = "{}";
  window.remotion_initialFrame = 0;
  window.remotion_attempt = 1;
  window.remotion_audioEnabled = false;
  window.remotion_videoEnabled = true;
  window.process = { env: { NODE_ENV: "production" } };
});
await page.goto(`http://127.0.0.1:${server.address().port}`);
await page.waitForFunction(
  () => typeof window.remotion_setBundleMode === "function",
);
await page.evaluate(
  (n) =>
    window.remotion_setBundleMode({
      type: "composition",
      compositionName: "BrightData",
      serializedResolvedPropsWithSchema: "{}",
      compositionDurationInFrames: n,
      compositionFps: 30,
      compositionHeight: 1080,
      compositionWidth: 1920,
    }),
  timeline.durationInFrames,
);
await page.waitForFunction(() => window.remotion_renderReady === true);
const records = [];
fs.mkdirSync("../review/current/focus", { recursive: true });
for (const [i, e] of timeline.events.entries()) {
  const time = e.start + 0.7;
  await page.evaluate(
    (f) => window.remotion_setFrame(f, "BrightData", 1),
    Math.round(time * 30),
  );
  await page.waitForFunction(() => window.remotion_renderReady === true);
  await page.evaluate(() => document.fonts.ready);
  const result = await page.evaluate(() => {
    const active = [...document.querySelectorAll('[data-active="true"]')].map(
      (e) => e.getAttribute("data-target"),
    );
    const clipped = [...document.querySelectorAll("[data-scene] div")]
      .filter(
        (e) =>
          e.childElementCount < 3 &&
          e.textContent.trim() &&
          (e.scrollHeight > e.clientHeight + 2 ||
            e.scrollWidth > e.clientWidth + 2),
      )
      .map((e) => ({
        text: e.textContent,
        sh: e.scrollHeight,
        ch: e.clientHeight,
        sw: e.scrollWidth,
        cw: e.clientWidth,
      }));
    return {
      scene: document.querySelector("[data-scene]")?.getAttribute("data-scene"),
      active,
      clipped,
    };
  });
  records.push({ time, target: e.target, ...result });
  if (
    i === 0 ||
    timeline.events[i - 1].scene !== e.scene ||
    timeline.events[i - 1].target !== e.target
  )
    await page.screenshot({
      path: `../review/current/focus/${String(i).padStart(3, "0")}-${e.scene}-${e.target}.png`,
    });
}
const pauses = [];
for (const s of timeline.scenes) {
  const samples = [];
  for (const fraction of [0.15, 0.8]) {
    const time = s.speechEnd + (s.end - s.speechEnd) * fraction;
    await page.evaluate(
      (f) => window.remotion_setFrame(f, "BrightData", 1),
      Math.round(time * 30),
    );
    await page.waitForFunction(() => window.remotion_renderReady === true);
    const screenshot = await page.screenshot({
      path: `../review/current/focus/pause-${s.id}-${fraction}.png`,
    });
    const recapVisible = await page.evaluate(() =>
      document.body.innerText.includes("回看要点"),
    );
    samples.push({ time, recapVisible, bytes: screenshot.toString("base64") });
  }
  if (
    samples.some((x) => !x.recapVisible) ||
    samples[0].bytes === samples[1].bytes
  )
    throw Error("Frozen or empty thinking interval: " + s.id);
  pauses.push({
    scene: s.id,
    framesDiffer: true,
    recapVisible: true,
    times: samples.map((x) => x.time),
  });
}
fs.writeFileSync(
  "../review/current/pause-review.json",
  JSON.stringify(pauses, null, 2),
);
fs.writeFileSync(
  "../review/current/dom-review.json",
  JSON.stringify({ errors, records }, null, 2),
);
console.log(
  "Review",
  records.length,
  "events; errors",
  errors,
  "clipped",
  records.filter((r) => r.clipped.length).length,
);
await browser.close();
server.close();
