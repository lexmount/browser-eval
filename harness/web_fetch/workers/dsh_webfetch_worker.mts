import readline from "node:readline";
import path from "node:path";
import { pathToFileURL } from "node:url";
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
let dnsTraces: any[] = [];
async function contextFor(request: any) {
  const maxBytes = Number(request.max_bytes ?? 20000000);
  const timeoutMs = Math.ceil(Number(request.timeout_seconds) * 1000);
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0 || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) throw new Error("Invalid shared fetch limits");
  const dnsMode = request.dns_mode ?? "system";
  if (!["system", "public_doh"].includes(dnsMode)) throw new Error("dns_mode must be system or public_doh");
  const key = `${maxBytes}:${timeoutMs}:${dnsMode}:${!!request.uniform_native_parameters}`;
  const nativeLimits = WebFetchHttp.Config({ maxResponseBytes: maxBytes, maxBodyChars: maxBytes, ...(request.uniform_native_parameters ? {} : { timeoutMs }) });
  if (!ctx || key !== activeLimits) {
    if (ctx) await ctx.fiber.dispose();
    ctx = new Context();
    await ctx.plugin(SystemPrompt);
    await ctx.plugin(ToolRuntime);
    await ctx.plugin(WebRuntime, { fetchProvider: WebFetchHttp.LOCAL_FETCH_PROVIDER_ID });
    if (dnsMode === "system") {
      await ctx.plugin(WebFetchHttp, nativeLimits);
    } else {
      const { createPublicDnsLookup } = await import(pathToFileURL(path.join(process.env.BROWSER_EVAL_HARNESS_ROOT!, "harness/web_fetch/workers/public_dns.mjs")).href);
      const { resolvePublicAddresses } = await import("@deepseek-ai/dsh-web-fetch-http/src/network.ts");
      // Use the exported native HTTP provider, native validator and native pinning.
      // DNS is the only environment seam changed; redirect policy is unchanged.
      await ctx.plugin({ name: "shared-dns-environment", inject: ["web"], apply(context: Context) {
        context.web.registerFetchProvider(new WebFetchHttp.HttpFetchProvider({
          ...nativeLimits,
        }, (hostname, signal) => resolvePublicAddresses(hostname, signal, createPublicDnsLookup(signal, dnsTraces))));
      }});
    }
    await ctx.plugin(TimeoutPolicy);
    await ctx.plugin(ToolWeb, { search: false, fetch: true, ...(request.uniform_native_parameters ? {} : { fetchTimeoutMs: timeoutMs }), fetchMaxOutputChars: maxBytes + 20000 });
    activeLimits = key;
  }
  return { ctx, maxBytes, timeoutMs, dnsMode };
}

let counter = 0;
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  const request = JSON.parse(line);
  dnsTraces = [];
  const debug = (event: string, fields: any = {}) => { if (request.log_level === "debug") process.stderr.write(JSON.stringify({ source: "caller_worker", event, at: new Date().toISOString(), ...fields }) + "\n"); };
  debug("native_tool_invocation_started", { url: request.url, internal_debug: "not_exposed_by_locked_fetch_implementation" });
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const { ctx, maxBytes, timeoutMs, dnsMode } = await contextFor(request);
    const controller = new AbortController();
    timer = setTimeout(() => controller.abort(), Math.ceil(request.timeout_seconds * 1000));
    let out;
    try {
      out = await ctx.tools.execute({
        signal: controller.signal,
        callId: ToolCallId(`browser-harness-${++counter}`),
        name: "web_fetch",
        arguments: { url: request.url },
      });
    } finally {
      clearTimeout(timer);
    }
    const { projectDeclaredFetch } = await import(pathToFileURL(path.join(process.env.BROWSER_EVAL_HARNESS_ROOT!, "harness/web_fetch/workers/dsh_output.mjs")).href);
    const projected = projectDeclaredFetch(out);
    debug("native_tool_returned", { ok: projected.ok, body_kind: projected.source_body_kind, dns_resolutions: dnsTraces });
    process.stdout.write(JSON.stringify({
      ...projected,
      raw_tool_response: out,
      metadata: { implementation: "DeepSeek Harness ctx.tools.execute(web_fetch)",
        output_projection: "declared_body", source_body_kind: projected.source_body_kind,
        truncated: projected.truncated, dns_mode: dnsMode, dns_resolutions: dnsTraces,
        caller_timeout_ms: timeoutMs, internal_timeout_policy: request.uniform_native_parameters ? "native_default" : "explicit", max_response_bytes: maxBytes, max_body_chars: maxBytes,
        max_output_chars: maxBytes + 20000 },
    }) + "\n");
  } catch (error: any) {
    const message = String(error?.message ?? error);
    debug("native_tool_error", { name: error?.name, message, stack: error?.stack });
    process.stdout.write(JSON.stringify({
      ok: false,
      raw_tool_error: { name: error?.name, message, code: error?.code },
      content: message,
      error_type: error?.code ?? "tool_error",
      error_message: message,
      metadata: { implementation: "DeepSeek Harness ctx.tools.execute(web_fetch)", dns_mode: request.dns_mode ?? "system", dns_resolutions: dnsTraces, timeout_ms: Math.ceil(Number(request.timeout_seconds) * 1000) },
    }) + "\n");
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
