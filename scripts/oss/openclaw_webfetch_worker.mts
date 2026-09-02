import readline from "node:readline";
import { pathToFileURL } from "node:url";
import path from "node:path";

delete process.env.FIRECRAWL_API_KEY;
const repo = process.env.OPENCLAW_REPO ?? process.cwd();
const source = pathToFileURL(path.join(repo, "src/agents/tools/web-fetch.ts")).href;
const { createWebFetchTool } = await import(source);
const tool = createWebFetchTool({
  config: {
    tools: {
      web: {
        fetch: {
          readability: true,
          cacheTtlMinutes: 0,
          maxChars: 200000,
          maxCharsCap: 200000,
          maxResponseBytes: 10000000,
        },
      },
    },
  },
  sandboxed: false,
});
if (!tool) throw new Error("OpenClaw web_fetch is disabled");

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  const request = JSON.parse(line);
  try {
    const signal = AbortSignal.timeout(Math.ceil(request.timeout_seconds * 1000));
    const out = await tool.execute("browser-harness-eval", {
      url: request.url,
      extractMode: "markdown",
      maxChars: 200000,
    }, signal);
    const details = out.details ?? {};
    process.stdout.write(JSON.stringify({
      ok: true,
      content: details.text ?? out.content?.map((x: any) => x.text ?? "").join("") ?? "",
      status_code: details.status,
      final_url: details.finalUrl ?? request.url,
      title: details.title,
      metadata: {
        implementation: "OpenClaw createWebFetchTool",
        extractor: details.extractor,
        content_type: details.contentType,
        truncated: details.truncated,
        fallback_provider_disabled: true,
      },
    }) + "\n");
  } catch (error: any) {
    process.stdout.write(JSON.stringify({
      ok: false,
      content: String(error?.stack ?? error),
      error_type: "tool_error",
      error_message: String(error?.message ?? error),
      final_url: request.url,
      metadata: { implementation: "OpenClaw createWebFetchTool", fallback_provider_disabled: true },
    }) + "\n");
  }
}

