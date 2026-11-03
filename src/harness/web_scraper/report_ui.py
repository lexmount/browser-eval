"""Static report controls for field, method, source and diagnosis drilldown."""

CSS = """
body{overflow-wrap:anywhere}
.controls label{display:flex;align-items:center;gap:6px;font-size:14px}
input[type=search]{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:7px;
background:var(--panel);color:var(--ink);max-width:100%}
.sort-button{font:inherit;border:0;padding:0;background:none;color:inherit;white-space:nowrap}
.field-correct{color:#18744c}.field-missing,.field-unexpected,.field-wrong{color:#a5302c}
html.dark .field-correct{color:#82d5a6}html.dark .field-missing,
html.dark .field-unexpected,html.dark .field-wrong{color:#ffa19d}
.field-button{font-size:13px;padding:3px 7px;white-space:nowrap}
.case-links{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
.source-preview{max-height:65vh;overflow:auto;font-size:14px;line-height:1.7;white-space:pre-wrap}
.source-screenshot{max-width:100%;height:auto;display:block}
.diagnosis-grid{display:grid;grid-template-columns:140px 1fr;gap:8px 16px}
.diagnosis-grid dt{font-weight:600}.diagnosis-grid dd{margin:0;overflow-wrap:anywhere}
.inventory .result-id{max-width:150px}.inventory table{min-width:1080px}
.comparison-table{min-width:950px}.evidence-loading{color:var(--muted)}
@media(max-width:650px){.diagnosis-grid{grid-template-columns:1fr}.controls{gap:8px}}
"""

JS = r"""
const tr = (zh, en) => (document.documentElement.lang.startsWith("en") ? en : zh);
try {
  document.documentElement.classList.toggle(
    "dark",
    localStorage.getItem("scraper-theme") === "dark",
  );
} catch {}
document.querySelector("#theme")?.addEventListener("click", () => {
  document.documentElement.classList.toggle("dark");
  try {
    localStorage.setItem(
      "scraper-theme",
      document.documentElement.classList.contains("dark") ? "dark" : "light",
    );
  } catch {}
});
const evidenceCache = new Map();
async function evidence(src, key) {
  const url = new URL(src, document.baseURI).href;
  if (!evidenceCache.has(url))
    evidenceCache.set(
      url,
      fetch(url)
        .then((r) => {
          if (!r.ok) throw Error("HTTP " + r.status);
          return r.json();
        })
        .catch((error) => {
          evidenceCache.delete(url);
          throw error;
        }),
    );
  const value = await evidenceCache.get(url);
  return key ? value[key] : value;
}
const fieldNames = {
  price: tr("价格", "Price"),
  sku: "SKU",
  availability: tr("库存", "Stock"),
  OutOfStock: tr("缺货", "Out of stock"),
};
const outcomeNames = {
  correct: tr("符合标注", "Matches reference"),
  missing: tr("漏提取", "Missing"),
  unexpected: tr("多提取", "Unexpected"),
  wrong: tr("值不匹配", "Wrong value"),
};
const stageNames = {
  client_transport: tr("客户端传输", "Client transport"),
  model_capacity: tr("模型容量", "Model capacity"),
  navigation: tr("页面加载", "Page navigation"),
  provider_schema: tr("服务端 Schema 处理", "Provider schema handling"),
  provider_fetch: tr("服务端附加抓取", "Provider fetching"),
  provider_response: tr("服务端响应", "Provider response"),
  fields: tr("字段匹配", "Field matching"),
};
function node(tag, text) {
  const value = document.createElement(tag);
  value.textContent = text;
  return value;
}
function valueText(value) {
  return value === null || value === undefined
    ? tr("空值", "null")
    : Array.isArray(value)
      ? value.length
        ? value.join(" / ")
        : tr("无可接受值", "No accepted value")
      : String(value);
}
let opener = null;
function show(dialog, button) {
  opener = button;
  if (!dialog.dataset.bound) {
    dialog.dataset.bound = "1";
    dialog.addEventListener("close", () => {
      opener?.focus();
      opener = null;
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      dialog.close();
    });
  }
  dialog.showModal();
  dialog.querySelector("[data-call-log-close]")?.focus();
}
function diagnosisContent(container, row, field) {
  container.replaceChildren();
  const lang = document.documentElement.lang.startsWith("en") ? "en" : "zh";
  container.append(
    node(
      "p",
      `${row.method_label || row.method} · ${row.case_id} · HTTP ${row.http_status ?? "—"}`,
    ),
  );
  const facts = document.createElement("dl");
  facts.className = "diagnosis-grid";
  const diagnosis = row.diagnosis || {};
  const selectedField =
    field ||
    ["price", "sku", "availability"].find(
      (key) => row.field_results?.[key]?.status !== "correct",
    ) ||
    "price";
  const sourceFinding = row.review?.source_field_review?.[selectedField];
  const add = (label, value) => {
    facts.append(node("dt", label), node("dd", value || "—"));
  };
  add(tr("失败环节", "Stage"), stageNames[row.failure_stage] || row.failure_stage);
  add(
    tr("准确错误", "Exact error"),
    row.native_error ||
      tr(
        "接口已返回结构化字段；查看各字段差异。",
        "Structured fields returned; inspect field differences.",
      ),
  );
  add(
    tr("核查原因", "Reviewed cause"),
    diagnosis.cause ||
      sourceFinding?.reason ||
      tr(
        "字段判断见下方；完整记录可展开。",
        "See field judgments and complete evidence below.",
      ),
  );
  add(
    tr("处理结果", "Treatment"),
    diagnosis.treatment || tr("保留原调用及评分。", "Original call and score retained."),
  );
  if (row.repair_run_id) add(tr("失败补跑", "Failure repair"), row.repair_run_id);
  if (Number.isFinite(row.elapsed_seconds))
    add(tr("本次耗时", "Current call latency"), row.elapsed_seconds.toFixed(3) + " s");
  container.append(facts);
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const label of [
    tr("字段", "Field"),
    tr("参考值", "Reference"),
    tr("输出", "Output"),
    tr("判定", "Outcome"),
  ])
    head.append(node("th", label));
  table.append(head);
  for (const key of ["price", "sku", "availability"]) {
    const item = row.field_results?.[key];
    if (!item) continue;
    const r = document.createElement("tr");
    if (field === key) r.className = "selected-field";
    for (const text of [
      fieldNames[key],
      valueText(item.expected),
      valueText(item.actual),
      outcomeNames[item.status],
    ])
      r.append(node("td", text));
    table.append(r);
  }
  const wrap = node("div", "");
  wrap.className = "table-wrap";
  wrap.append(table);
  container.append(wrap);
  if (row.review) {
    const details = document.createElement("details");
    details.append(
      node("summary", tr("原文证据与核查后的参考值", "Source evidence and audited reference")),
    );
    for (const key of ["price", "sku", "availability"]) {
      const finding = row.review.source_field_review?.[key];
      const assessed = row.review.audited_fields?.[key];
      if (!finding || !assessed) continue;
      details.append(node("h3", fieldNames[key]));
      details.append(
        node("p", tr("核查参考值：", "Audited reference: ") + valueText(assessed.expected)),
      );
      details.append(node("blockquote", finding.source_quote));
      details.append(node("p", finding.reason));
    }
    container.append(details);
  }
  const links = node("p", "");
  links.className = "case-links";
  for (const [path, label] of [
    [`${lang}/result-${row.method}-${row.case_id}.html`, tr("完整结果", "Full result")],
    [`${lang}/case-${row.case_id}.html`, tr("同题比较全部方法", "Compare all methods")],
    [`${lang}/source-${row.case_id}.html`, tr("页面、标注与来源", "Source and annotations")],
  ]) {
    const a = node("a", label);
    a.href = path;
    links.append(a);
  }
  container.append(links);
  const details = document.createElement("details");
  details.append(
    node(
      "summary",
      tr("完整请求、响应与补跑历史", "Complete request, response and repair history"),
    ),
  );
  details.append(node("pre", JSON.stringify(row, null, 2)));
  container.append(details);
}
document.addEventListener("click", async (event) => {
  const close = event.target.closest("[data-call-log-close]");
  if (close) {
    close.closest("dialog").close();
    return;
  }
  const button = event.target.closest("[data-call-log-open]");
  if (!button) return;
  const dialog = document.getElementById(button.dataset.callLogOpen);
  if (!dialog) return;
  show(dialog, button);
  const container =
    dialog.querySelector("[data-diagnosis-body]") || dialog.querySelector("pre");
  container.textContent = tr("正在加载完整证据…", "Loading complete evidence…");
  try {
    const row = await evidence(button.dataset.src, button.dataset.key);
    if (dialog.querySelector("[data-diagnosis-body]"))
      diagnosisContent(container, row, button.dataset.field);
    else container.textContent = JSON.stringify(row, null, 2);
  } catch (error) {
    container.textContent =
      tr(
        "读取失败，可关闭后重试；原始文件链接仍可使用：",
        "Load failed; close and retry or use the raw file: ",
      ) + error.message;
  }
});
for (const table of document.querySelectorAll("table[data-sortable]")) {
  for (const [column, th] of [...table.tHead.rows[0].cells].entries()) {
    const button = node("button", th.textContent + " ↕");
    button.className = "sort-button";
    button.type = "button";
    th.replaceChildren(button);
    button.addEventListener("click", () => {
      const ascending = th.getAttribute("aria-sort") !== "ascending";
      const rows = [...table.tBodies[0].rows].map((row, index) => ({ row, index }));
      rows.sort((a, b) => {
        const ac = a.row.cells[column],
          bc = b.row.cells[column];
        const av = ac.dataset.sort,
          bv = bc.dataset.sort;
        const am = av === "" || ac.textContent === "—",
          bm = bv === "" || bc.textContent === "—";
        if (am || bm) return am === bm ? a.index - b.index : am ? 1 : -1;
        const delta =
          av !== undefined && bv !== undefined
            ? Number(av) - Number(bv)
            : ac.textContent.localeCompare(bc.textContent);
        return (ascending ? delta : -delta) || a.index - b.index;
      });
      for (const { row } of rows) table.tBodies[0].append(row);
      for (const h of table.tHead.rows[0].cells) h.removeAttribute("aria-sort");
      th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
    });
  }
}
const form = document.querySelector("[data-result-controls]");
if (form) {
  const params = new URLSearchParams(location.search);
  for (const control of form.querySelectorAll("[data-result-filter]")) {
    const key = control.dataset.resultFilter;
    if (params.has(key)) control.value = params.get(key);
  }
  const update = () => {
    const controls = Object.fromEntries(
      [...form.querySelectorAll("[data-result-filter]")].map((x) => [
        x.dataset.resultFilter,
        x.value,
      ]),
    );
    let shown = 0;
    for (const row of document.querySelectorAll("tr[data-status]")) {
      const field = controls.field || "";
      const bad = row.dataset.status === "fail" ||
        (field ? row.dataset[field] : row.dataset.outcome) !== "correct";
      const status = controls.status || "all";
      const show =
        (!controls.method || row.dataset.method === controls.method) &&
        (!controls.stage || row.dataset.stage === controls.stage) &&
        (!controls.query ||
          (row.dataset.search || "").toLowerCase().includes(controls.query.toLowerCase())) &&
        (status === "all" ||
          (status === "pending" && row.dataset.status === "pending") ||
          (status === "fail" && row.dataset.status !== "pending" && bad) ||
          (status === "pass" && row.dataset.status !== "pending" && !bad));
      row.hidden = !show;
      if (show) shown++;
    }
    const count = document.querySelector("[data-result-count]");
    if (count) count.textContent = tr(`显示 ${shown} 条`, `Showing ${shown} results`);
  };
  for (const control of form.querySelectorAll("[data-result-filter]"))
    control.addEventListener(control.tagName === "INPUT" ? "input" : "change", update);
  update();
} else {
  const filter = document.querySelector("#filter");
  if (filter) {
    const update = () => {
      for (const row of document.querySelectorAll("tr[data-status]"))
        row.hidden = filter.value !== "all" && row.dataset.status !== filter.value;
    };
    filter.addEventListener("change", update);
    update();
  }
}
"""
