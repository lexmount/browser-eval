import fs from "node:fs";
fs.mkdirSync("public", { recursive: true });
fs.copyFileSync(
  "../../assets/infographics/architecture.png",
  "public/architecture.png",
);
const t = JSON.parse(fs.readFileSync("src/timeline.json", "utf8")),
  total = t.durationInFrames;
const changes = new Set([0, total]),
  windows = [];
for (const s of t.scenes) {
  changes.add(s.from);
  windows.push([s.from, s.from + 17]);
  windows.push([Math.floor(s.speechEnd * 30), s.from + s.durationInFrames]);
}
for (const c of t.captions) {
  changes.add(Math.ceil(c.start * 30));
  changes.add(Math.ceil(c.end * 30));
}
let previous = "";
for (const e of t.events) {
  const key = e.scene + "/" + e.target;
  if (key !== previous) {
    const f = Math.ceil(e.start * 30);
    changes.add(f);
    windows.push([f, f + 18]);
  }
  previous = key;
}
for (const [a, b] of windows) {
  changes.add(a);
  changes.add(Math.min(b, total));
}
const bounds = [...changes]
  .filter((f) => f >= 0 && f <= total)
  .sort((a, b) => a - b);
let cursor = 0;
const segments = [];
const frames = [];
for (let i = 0; i < bounds.length - 1; i++) {
  const a = bounds[i],
    b = bounds[i + 1];
  if (b <= a) continue;
  const animated = windows.some(([x, y]) => a >= x && a < y);
  const n = animated ? b - a : 1;
  segments.push({
    start: a,
    end: b,
    reelStart: cursor,
    reelLength: n,
    hold: !animated,
  });
  for (let j = 0; j < n; j++) frames.push(a + j);
  cursor += n;
}
fs.writeFileSync("src/reel.json", JSON.stringify({ frames, segments }));
console.log("Render unique frames", frames.length, "of", total);
