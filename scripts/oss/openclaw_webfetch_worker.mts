import readline from "node:readline";
import { pathToFileURL } from "node:url";
import path from "node:path";

delete process.env.FIRECRAWL_API_KEY;
const repo = process.env.OPENCLAW_REPO ?? process.cwd();
const source = pathToFileURL(path.join(repo, "src/agents/tools/web-fetch.ts")).href;
const { createWebFetchTool } = await import(source);
function createTool(request: any) {
  const maxBytes = Number(request.max_bytes ?? 20000000);
  const timeoutSeconds = Number(request.timeout_seconds);
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0 || !Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0) throw new Error("Invalid shared fetch limits");
  const tool = createWebFetchTool({
    config: {
      tools: {
        web: {
          fetch: {
            readability: true,
            cacheTtlMinutes: 0,
            timeoutSeconds,
            maxChars: maxBytes,
            maxCharsCap: maxBytes,
            maxResponseBytes: maxBytes,
          },
        },
      },
    },
    sandboxed: false,
  });
  if (!tool) throw new Error("OpenClaw web_fetch is disabled");
  return { tool, maxBytes, timeoutSeconds };
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  const request = JSON.parse(line);
  try {
    const { tool, maxBytes, timeoutSeconds } = createTool(request);
    const signal = AbortSignal.timeout(Math.ceil(timeoutSeconds * 1000));
    const out = await tool.execute("browser-harness-eval", {
      url: request.url,
      extractMode: "markdown",
      maxChars: maxBytes,
    }, signal);
    const details = out.details ?? {};
    process.stdout.write(JSON.stringify({
      ok: true,
      raw_tool_response: out,
      content: details.text ?? out.content?.map((x: any) => x.text ?? "").join("") ?? "",
      status_code: details.status,
      final_url: details.finalUrl ?? request.url,
      title: details.title,
      metadata: {
        implementation: "OpenClaw createWebFetchTool",
        extractor: details.extractor,
        content_type: details.contentType,
        truncated: details.truncated,
        timeout_seconds: timeoutSeconds,
        max_response_bytes: maxBytes,
        max_output_chars: maxBytes,
        fallback_provider_disabled: true,
      },
    }) + "\n");
  } catch (error: any) {
    const message = String(error?.message ?? error);
    process.stdout.write(JSON.stringify({
      ok: false,
      raw_tool_error: { name: error?.name, message, code: error?.code },
      // Stack traces contain host-local source paths and are diagnostics, not
      // provider answer content. Keep the stable provider error only.
      content: message,
      error_type: "tool_error",
      error_message: message,
      final_url: request.url,
      metadata: { implementation: "OpenClaw createWebFetchTool", fallback_provider_disabled: true },
    }) + "\n");
  }
}
