import { bundle } from "@remotion/bundler";
import {
  renderMedia,
  renderStill,
  selectComposition,
} from "@remotion/renderer";
import path from "node:path";
import fs from "node:fs";
const serveUrl = await bundle({
  entryPoint: path.resolve("src/index.tsx"),
  publicDir: path.resolve("public"),
});
const browserExecutable =
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const composition = await selectComposition({
  serveUrl,
  id: process.argv.includes("--stills") ? "BrightData" : "BrightDataReel",
  browserExecutable,
});
const reel = JSON.parse(fs.readFileSync("src/reel.json", "utf8"));
const timeline = JSON.parse(fs.readFileSync("src/timeline.json", "utf8"));
fs.mkdirSync("../review/current/frames", { recursive: true });
if (process.argv.includes("--stills")) {
  for (const s of timeline.scenes) {
    const frame = Math.round((s.speechStart + 2) * 30);
    await renderStill({
      serveUrl,
      composition,
      frame,
      output: path.resolve(`../review/current/frames/${s.id}.png`),
      browserExecutable,
    });
    console.log("Still", s.id);
  }
} else {
  let last = -1;
  await renderMedia({
    serveUrl,
    composition,
    codec: "h264",
    frameRange: [0, reel.frames.length - 1],
    outputLocation: path.resolve("reel.mp4"),
    browserExecutable,
    concurrency: 8,
    crf: 20,
    onProgress: ({ progress }) => {
      const p = Math.floor(progress * 20);
      if (p !== last) {
        last = p;
        console.log("Render", p * 5 + "%");
      }
    },
  });
  console.log("Rendered unique visual seconds", reel.frames.length / 30);
}
