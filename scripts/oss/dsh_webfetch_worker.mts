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
    const meta: any = out.output?.presentationMeta ?? {};
    process.stdout.write(JSON.stringify({
      ok: !out.isError,
      content,
      status_code: meta.statusCode,
      final_url: meta.url ?? request.url,
      error_type: out.isError ? (out.error?.info?.code ?? "tool_error") : undefined,
      error_message: out.isError ? content : undefined,
      metadata: { implementation: "DeepSeek Harness ctx.tools.execute(web_fetch)", presentation_meta: meta },
    }) + "\n");
  } catch (error: any) {
    process.stdout.write(JSON.stringify({
      ok: false,
      content: String(error?.stack ?? error),
      error_type: error?.code ?? "tool_error",
      error_message: String(error?.message ?? error),
      final_url: request.url,
      metadata: { implementation: "DeepSeek Harness ctx.tools.execute(web_fetch)" },
    }) + "\n");
  }
}

