import readline from "node:readline";
import { Context } from "@deepseek-ai/cordis";
import { ToolCallId } from "@deepseek-ai/dsh-llm";
import SystemPrompt from "@deepseek-ai/dsh-system-prompt";
import ToolRuntime from "@deepseek-ai/dsh-tools";
import WebRuntime from "@deepseek-ai/dsh-web";
import * as WebFetchHttp from "@deepseek-ai/dsh-web-fetch-http";
import * as ToolWeb from "@deepseek-ai/dsh-tool-web";
import * as TimeoutPolicy from "@deepseek-ai/dsh-tool-call-timeout-policy";

const ctx = new Context();
await ctx.plugin(SystemPrompt);
await ctx.plugin(ToolRuntime);
await ctx.plugin(WebRuntime, { fetchProvider: WebFetchHttp.LOCAL_FETCH_PROVIDER_ID });
await ctx.plugin(WebFetchHttp, { maxResponseBytes: 10000000, maxBodyChars: 200000, timeoutMs: 45000 });
await ctx.plugin(TimeoutPolicy);
await ctx.plugin(ToolWeb, { search: false, fetch: true, fetchTimeoutMs: 45000, fetchMaxOutputChars: 220000 });

let counter = 0;
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  const request = JSON.parse(line);
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.ceil(request.timeout_seconds * 1000));
    const out = await ctx.tools.execute({
      signal: controller.signal,
      callId: ToolCallId(`browser-harness-${++counter}`),
      name: "web_fetch",
      arguments: { url: request.url },
    });
    clearTimeout(timer);
    const content = out.content.map((block: any) => block.type === "text" ? block.text : "").join("");
    // ToolRuntime returns the declared presentation projection on `meta` and,
    // for successful calls, the exact declared output on `value`.  Do not read
    // the durable event shape here: it deliberately does not contain `value`.
    const meta: any = out.meta && typeof out.meta === "object" ? out.meta : {};
    const value: any = !out.isError && out.value && typeof out.value === "object" ? out.value : {};
    const statusCode = Number.isInteger(value.statusCode)
      ? value.statusCode
      : (Number.isInteger(meta.statusCode) ? meta.statusCode : undefined);
    const finalUrl = typeof value.url === "string" && value.url
      ? value.url
      : (typeof meta.url === "string" && meta.url ? meta.url : undefined);
    const missingStatus = !out.isError && statusCode === undefined;
    const httpError = statusCode !== undefined && (statusCode < 200 || statusCode >= 300);
    const ok = !out.isError && !missingStatus && !httpError;
    const errorType = out.isError
      ? (out.error?.info?.code ?? "tool_error")
      : (missingStatus ? "invalid_response" : (httpError ? "http_status" : undefined));
    const errorMessage = out.isError
      ? content
      : (missingStatus
          ? "DeepSeek Harness web_fetch returned no HTTP status"
          : (httpError ? `Origin returned HTTP ${statusCode}` : undefined));
    process.stdout.write(JSON.stringify({
      ok,
      content,
      status_code: statusCode,
      final_url: finalUrl,
      error_type: errorType,
      error_message: errorMessage,
      metadata: { implementation: "DeepSeek Harness ctx.tools.execute(web_fetch)", presentation_meta: meta },
    }) + "\n");
  } catch (error: any) {
    const message = String(error?.message ?? error);
    process.stdout.write(JSON.stringify({
      ok: false,
      content: message,
      error_type: error?.code ?? "tool_error",
      error_message: message,
      metadata: { implementation: "DeepSeek Harness ctx.tools.execute(web_fetch)" },
    }) + "\n");
  }
}
