// The public web_fetch output schema declares body.kind and body.content.
// `content` is a separate model-facing presentation, never an empty-body rescue.
export function projectDeclaredFetch(out) {
  const rendered = (out.content ?? []).filter(block => block.type === "text").map(block => block.text ?? "").join("");
  const meta = out.meta && typeof out.meta === "object" ? out.meta : {};
  const value = !out.isError && out.value && typeof out.value === "object" ? out.value : {};
  const body = value.body;
  const bodyValid = body && ["html", "text"].includes(body.kind) && typeof body.content === "string";
  const statusCode = Number.isInteger(value.statusCode) ? value.statusCode : (Number.isInteger(meta.statusCode) ? meta.statusCode : undefined);
  const finalUrl = typeof value.url === "string" && value.url ? value.url : (typeof meta.url === "string" && meta.url ? meta.url : undefined);
  const missingStatus = !out.isError && statusCode === undefined;
  const missingBody = !out.isError && !bodyValid;
  const httpError = statusCode !== undefined && (statusCode < 200 || statusCode >= 300);
  const content = out.isError ? rendered : (bodyValid ? body.content : "");
  const ok = !out.isError && !missingStatus && !missingBody && !httpError;
  const errorType = out.isError ? (out.error?.info?.code ?? "tool_error") : (missingStatus ? "invalid_response" : (missingBody ? "invalid_response" : (httpError ? "http_status" : undefined)));
  const errorMessage = out.isError ? rendered : (missingStatus ? "DeepSeek Harness web_fetch returned no HTTP status" : (missingBody ? "DeepSeek Harness web_fetch returned no declared body" : (httpError ? `Origin returned HTTP ${statusCode}` : undefined)));
  return {ok, content, status_code: statusCode, final_url: finalUrl, error_type: errorType, error_message: errorMessage,
          rendered_tool_content: rendered, source_body_kind: bodyValid ? body.kind : undefined,
          truncated: typeof value.truncated === "boolean" ? value.truncated : undefined};
}
