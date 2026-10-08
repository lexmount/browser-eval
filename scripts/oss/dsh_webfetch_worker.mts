import readline from "node:readline";
import { Context } from "@deepseek-ai/cordis";
import { ToolCallId } from "@deepseek-ai/dsh-llm";
import SystemPrompt from "@deepseek-ai/dsh-system-prompt";
import ToolRuntime from "@deepseek-ai/dsh-tools";
import WebRuntime from "@deepseek-ai/dsh-web";
import * as WebFetchHttp from "@deepseek-ai/dsh-web-fetch-http";
import * as ToolWeb from "@deepseek-ai/dsh-tool-web";
import * as TimeoutPolicy from "@deepseek-ai/dsh-tool-call-timeout-policy";

let ctx: Context | undefined;
let activeLimits = "";
async function contextFor(request: any) {
  const maxBytes = Number(request.max_bytes ?? 20000000);
  const timeoutMs = Math.ceil(Number(request.timeout_seconds) * 1000);
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0 || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) throw new Error("Invalid shared fetch limits");
  const key = `${maxBytes}:${timeoutMs}`;
  if (!ctx || key !== activeLimits) {
    if (ctx) await ctx.fiber.dispose();
    ctx = new Context();
    await ctx.plugin(SystemPrompt);
    await ctx.plugin(ToolRuntime);
    await ctx.plugin(WebRuntime, { fetchProvider: WebFetchHttp.LOCAL_FETCH_PROVIDER_ID });
    await ctx.plugin(WebFetchHttp, { maxResponseBytes: maxBytes, maxBodyChars: maxBytes, timeoutMs });
    await ctx.plugin(TimeoutPolicy);
    await ctx.plugin(ToolWeb, { search: false, fetch: true, fetchTimeoutMs: timeoutMs, fetchMaxOutputChars: maxBytes + 20000 });
    activeLimits = key;
  }
  return { ctx, maxBytes, timeoutMs };
}

let counter = 0;
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  const request = JSON.parse(line);
  try {
    const { ctx, maxBytes, timeoutMs } = await contextFor(request);
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
      raw_tool_response: out,
      status_code: statusCode,
      final_url: finalUrl,
      error_type: errorType,
      error_message: errorMessage,
      metadata: { implementation: "DeepSeek Harness ctx.tools.execute(web_fetch)", presentation_meta: meta, timeout_ms: timeoutMs, max_response_bytes: maxBytes, max_body_chars: maxBytes, max_output_chars: maxBytes + 20000 },
    }) + "\n");
  } catch (error: any) {
    const message = String(error?.message ?? error);
    process.stdout.write(JSON.stringify({
      ok: false,
      raw_tool_error: { name: error?.name, message, code: error?.code },
      content: message,
      error_type: error?.code ?? "tool_error",
      error_message: message,
      metadata: { implementation: "DeepSeek Harness ctx.tools.execute(web_fetch)" },
    }) + "\n");
  }
}
