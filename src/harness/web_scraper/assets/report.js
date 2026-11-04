
const REPORT_MESSAGES = __REPORT_MESSAGES__;
const tr = (key, values = {}) => {
  const language = document.documentElement.lang.startsWith("en") ? "en" : "zh";
  if (!(key in REPORT_MESSAGES[language]))
    throw Error("Missing report translation: " + language + "/" + key);
  return REPORT_MESSAGES[language][key].replace(/\{(\w+)\}/g, (_, name) => {
    if (!(name in values)) throw Error("Missing report value: " + name);
    return String(values[name]);
  });
};
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
  price: tr("field_labels_price"),
  sku: tr("field_labels_sku"),
  availability: tr("field_labels_availability"),
};
const outcomeNames = {
  correct: tr("outcomes_correct"),
  missing: tr("outcomes_missing"),
  unexpected: tr("outcomes_unexpected"),
  wrong: tr("outcomes_wrong"),
  partial: tr("outcomes_partial"),
  invalid: tr("outcomes_invalid"),
};
const stageNames = {
  client_transport: tr("stages_client_transport"),
  model_capacity: tr("stages_model_capacity"),
  navigation: tr("stages_navigation"),
  provider_schema: tr("stages_provider_schema"),
  provider_fetch: tr("stages_provider_fetch"),
  provider_response: tr("stages_provider_response"),
  fields: tr("stages_fields"),
};
function node(tag, text) {
  const value = document.createElement(tag);
  value.textContent = text;
  return value;
}
function valueText(value) {
  if (value === null || value === undefined) return tr("null");
  if (Array.isArray(value)) {
    if (value.length) return value.join(" / ");
    return tr("no_accepted_value");
  }
  return String(value);
}
let opener = null;
function show(dialog, button) {
  opener = button;
  if (!dialog.dataset.bound) {
    dialog.dataset.bound = "1";
    dialog.addEventListener("close", () => {
      dialog.dataset.generation = String(Number(dialog.dataset.generation || 0) + 1);
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
  if (row.record_schema) {
    container.replaceChildren();
    container.append(node("p", `${row.method_label} · ${row.case_id} · HTTP ${row.http_status ?? "—"}`));
    const table = document.createElement("table");
    const header = document.createElement("tr");
    for (const label of [tr("field"), tr("reference"), tr("output"), tr("outcome_243a87d2")]) header.append(node("th", label));
    table.append(header);
    for (const [field, result] of Object.entries(row.field_results || {})) {
      const line = document.createElement("tr");
      for (const value of [row.field_labels?.[field] || field, valueText(result.expected), valueText(result.actual), outcomeNames[result.status] || result.status]) line.append(node("td", value));
      table.append(line);
    }
    container.append(table);
    if (row.native_error || row.http_status !== 200) container.append(node("pre", row.native_error || JSON.stringify(row.response)));
    const link = node("a", tr("full_result"));
    link.href = row.result_href;
    container.append(link);
    const details = document.createElement("details");
    details.append(node("summary", tr("complete_request_response_and_repair_history")), node("pre", JSON.stringify(row, null, 2)));
    container.append(details);
    return;
  }
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
  const resultFinding = row.review?.reviewer_fields?.[selectedField];
  const add = (label, value) => {
    facts.append(node("dt", label), node("dd", value || "—"));
  };
  add(tr("stage_fb689e6a"), stageNames[row.failure_stage] || row.failure_stage);
  add(
    tr("exact_error"),
    row.native_error || (row.compatibility === "structured_output"
      ? tr("structured_fields_returned_inspect_field_differences")
      : tr("no_recognizable_product_fields_returned")),
  );
  add(
    tr("reviewed_cause"),
    diagnosis.cause ||
      resultFinding?.reason ||
      tr("see_field_judgments_and_complete_evidence_below"),
  );
  add(
    tr("treatment"),
    diagnosis.treatment || tr("original_call_and_score_retained"),
  );
  if (row.repair_run_id) add(tr("failure_repair"), row.repair_run_id);
  if (Number.isFinite(row.elapsed_seconds))
    add(tr("current_call_latency"), row.elapsed_seconds.toFixed(3) + " s");
  container.append(facts);
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const label of [
    tr("field"),
    tr("reference"),
    tr("output"),
    tr("outcome_243a87d2"),
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
      node("summary", tr("source_evidence_and_audited_reference")),
    );
    for (const key of ["price", "sku", "availability"]) {
      const finding = row.review.source_field_review?.[key];
      const assessed = row.review.audited_fields?.[key];
      if (!finding || !assessed) continue;
      details.append(node("h3", fieldNames[key]));
      details.append(
        node("p", tr("audited_reference") + valueText(assessed.expected)),
      );
      details.append(node("blockquote", finding.source_quote));
      details.append(node("p", finding.reason));
    }
    container.append(details);
  }
  const links = node("p", "");
  links.className = "case-links";
  for (const [path, label] of [
    [`${lang}/result-${row.method}-${row.case_id}.html`, tr("full_result")],
    [`${lang}/case-${row.case_id}.html`, tr("compare_all_methods_a24f79dd")],
    [`${lang}/source-${row.case_id}.html`, tr("source_and_annotations_cf191383")],
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
      tr("complete_request_response_and_repair_history"),
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
  const generation = String(Number(dialog.dataset.generation || 0) + 1);
  dialog.dataset.generation = generation;
  show(dialog, button);
  const container =
    dialog.querySelector("[data-diagnosis-body]") || dialog.querySelector("pre");
  container.textContent = tr("loading_complete_evidence");
  try {
    const row = await evidence(button.dataset.src, button.dataset.key);
    if (!dialog.open || dialog.dataset.generation !== generation) return;
    if (dialog.querySelector("[data-diagnosis-body]"))
      diagnosisContent(container, row, button.dataset.field);
    else container.textContent = JSON.stringify(row, null, 2);
  } catch (error) {
    if (!dialog.open || dialog.dataset.generation !== generation) return;
    container.textContent =
      tr("load_failed_close_and_retry_or_use_the_raw_file") + error.message;
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
        const am = av === "" || ac.textContent === "—" ||
          (av === undefined && ac.dataset.metric !== undefined);
        const bm = bv === "" || bc.textContent === "—" ||
          (bv === undefined && bc.dataset.metric !== undefined);
        if (am || bm) {
          if (am === bm) return a.index - b.index;
          if (am) return 1;
          return -1;
        }
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
      const applicable = !field || row.dataset[field] !== undefined || row.dataset.status === "pending";
      const bad = field ? (Boolean(row.dataset.stage) && row.dataset.stage !== "fields") || row.dataset[field] !== "correct" : row.dataset.status === "fail";
      const status = controls.status || "all";
      const show =
        applicable &&
        (!controls.domain || row.dataset.domain === controls.domain) &&
        (!controls.site || row.dataset.site === controls.site) &&
        (!controls.method || row.dataset.method === controls.method) &&
        (!controls.stage || row.dataset.stage === controls.stage) &&
        (!controls.query ||
          (row.dataset.search || "").toLowerCase().includes(controls.query.toLowerCase())) &&
        (status === "all" ||
          (status === "fail" && row.dataset.status !== "pending" && bad) ||
          (status === "pass" && row.dataset.status !== "pending" && !bad));
      row.hidden = !show;
      if (show) shown++;
    }
    const count = document.querySelector("[data-result-count]");
    if (count) count.textContent = tr("showing_results", {shown});
  };
  for (const control of form.querySelectorAll("[data-result-filter]"))
    control.addEventListener(control.tagName === "INPUT" ? "input" : "change", update);
  update();
}

// Case comparisons own all participant details; fragment links open one panel.
const revealResultPanel = () => {
  const id = location.hash.slice(1);
  if (!id) return;
  const panel = document.getElementById(id);
  if (panel?.matches?.("details[data-result-panel], details[data-method-example]")) { panel.open = true; panel.scrollIntoView(); }
};
if (typeof window !== "undefined") {
  window.addEventListener("hashchange", revealResultPanel);
  revealResultPanel();
}
