"""Shared, answer-free projections of already-rendered browser content.

Resource and metadata projections only read already-loaded content. The document
projection reads text already loaded in a browser viewer or page; it never
downloads document bytes for external conversion.
"""

RESOURCE_PAGE_EXPRESSION = r"""
(() => {
  const urls = new Set();
  const add = (value, base = location.href) => {
    const raw = String(value || '').trim();
    if (!raw || /^(?:data|blob|javascript|mailto):/i.test(raw)) return;
    try { urls.add(new URL(raw, base).href); } catch {}
  };
  const addSrcset = (value, base) => {
    // HTML srcset URLs end at whitespace; commas inside a URL are literal.
    // Trailing commas delimit candidates without descriptors.
    const text = String(value || '');
    let cursor = 0;
    while (cursor < text.length) {
      while (cursor < text.length && /[\s,]/.test(text[cursor])) cursor++;
      const start = cursor;
      while (cursor < text.length && !/\s/.test(text[cursor])) cursor++;
      const token = text.slice(start, cursor);
      add(token.replace(/,+$/, ''), base);
      if (token.endsWith(',')) continue;
      while (cursor < text.length && text[cursor] !== ',') cursor++;
      cursor++;
    }
  };

  // Enumerate the top document and bounded, already-loaded same-origin frame
  // documents.  This is answer-free: it follows the page's own document
  // hierarchy and never receives an expected URL or case-specific selector.
  const roots = [document];
  for (let cursor = 0; cursor < roots.length && roots.length < 16; cursor++) {
    for (const frame of roots[cursor].querySelectorAll('iframe,frame')) {
      try {
        if (frame.contentDocument && !roots.includes(frame.contentDocument)) {
          roots.push(frame.contentDocument);
          if (roots.length >= 16) break;
        }
      } catch {}
    }
  }
  for (const root of roots) {
    for (const node of root.querySelectorAll(__RESOURCE_SELECTOR__)) {
      for (const name of ['src', 'href', 'data', 'poster', 'srcset']) {
        const value = node.getAttribute(name);
        if (__VIDEO_TASK__ && name === 'href') {
          let path;
          try { path = new URL(value, location.href).pathname.toLowerCase(); } catch { continue; }
          const media = /\.(?:mp4|webm|mov|m4v|mp3|wav|ogg)(?:\.zip)?$/.test(path);
          const detail = __DETAIL_LINKS__ && /\/(?:video|videos|watch)(?:\/|$)/.test(path);
          if (!media && !detail) continue;
        }
        const base = node.baseURI || root.baseURI || location.href;
        if (name === 'srcset') addSrcset(value, base);
        else add(value, base);
      }
    }
  }
  return JSON.stringify({links: [...urls]});
})()
"""


def resource_detail_links(case):
    """Read only the public resource kind; expected URLs never reach the browser."""
    return case.track == 'video' and any(item.get('kind') == 'video-link' for item in getattr(case, 'expected_resources', []))


def resource_expression(track, detail_links=False):
    """Select resource element types from the declared task, never its answers."""
    import json
    selectors = {'image':'img,picture source',
                 'video':'video,video source,audio,audio source,iframe,a[href]'}
    if track not in selectors:
        raise ValueError('Unsupported resource task family')
    selector = 'a[href]' if track == 'video' and detail_links else selectors[track]
    return (RESOURCE_PAGE_EXPRESSION.replace('__RESOURCE_SELECTOR__', json.dumps(selector))
            .replace('__VIDEO_TASK__', 'true' if track == 'video' else 'false')
            .replace('__DETAIL_LINKS__', 'true' if detail_links else 'false'))

MEDIA_METADATA_EXPRESSION = r"""
(() => {
  const values = [];
  const add = value => {
    const text = String(value || '').trim();
    if (text && !values.includes(text)) values.push(text);
  };
  add(document.title);
  add(document.body?.innerText);
  for (const node of document.querySelectorAll(
    'meta[name="author"],meta[property$=":author"],meta[itemprop="author"],'
    + '[itemprop="author"],[itemprop="creator"],[itemprop="publisher"]'
  )) {
    add(node.content);
    add(node.getAttribute?.('content'));
    add(node.innerText);
    for (const name of node.querySelectorAll?.('[itemprop="name"]') || []) {
      add(name.content);
      add(name.getAttribute?.('content'));
      add(name.innerText);
    }
  }
  let visited = 0;
  const visit = (value, depth = 0) => {
    if (++visited > 2048 || depth > 6 || value == null) return;
    if (Array.isArray(value)) {
      for (const item of value.slice(0, 64)) visit(item, depth + 1);
      return;
    }
    if (typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value).slice(0, 128)) {
      if (/^(?:author|creator|publisher)$/i.test(key)) {
        if (typeof item === 'string') add(item);
        else if (item && typeof item === 'object') add(item.name);
      }
      visit(item, depth + 1);
    }
  };
  for (const script of [...document.querySelectorAll('script[type="application/ld+json"]')]
    .slice(0, 16)) {
    try { visit(JSON.parse(script.textContent)); } catch {}
  }
  return values.join('\n\n');
})()
"""


DOCUMENT_PAGE_EXPRESSION = r"""
(async () => {
  const app = globalThis.PDFViewerApplication;
  if (app?.pdfDocument) {
    const pdf = app.pdfDocument;
    if (pdf) {
      const pages = [];
      for (let number = 1; number <= pdf.numPages; number++) {
        const page = await pdf.getPage(number), text = await page.getTextContent();
        pages.push(text.items.map(item => item.str).join(' '));
      }
      return JSON.stringify({
        kind: 'text', source: new URL(app.url, location.href).href,
        text: pages.join('\n\n')
      });
    }
  }
  return JSON.stringify({kind: 'page_text', text: document.body?.innerText || ''});
})()
"""

DOCUMENT_PAGE_READY = r"""
() => {
  if (globalThis.PDFViewerApplication) return !!globalThis.PDFViewerApplication.pdfDocument;
  return !!document.querySelector('embed[src],iframe[src],object[data],a[href*=".pdf"]')
    || !!document.body?.innerText?.trim();
}
"""



def require_native_task_routes(cases, records):
    """Reject substitutions and stale task adapters before report publication."""
    import hashlib
    from types import SimpleNamespace
    from .providers.headless import output_mode_for_case
    from .retrieval_hints import native_contract_for_case

    inventory = {case['id']: SimpleNamespace(id=case['id'], url=case['url'], track=case.get('track','web'), task_kind=case.get('task_kind','body'), retrieval_hints=case.get('retrieval_hints',{}), source_benchmark=case.get('source_benchmark',{}), expected_resources=[{'kind':r.get('kind')} for r in case.get('expected_resources',[])]) for case in cases}
    expressions = {'resource':RESOURCE_PAGE_EXPRESSION, 'media_metadata':MEDIA_METADATA_EXPRESSION,
                   'document_page':DOCUMENT_PAGE_EXPRESSION}
    failures = []
    for row in records:
        from .methods import native_browser_engine
        provider = (
            'moli'
            if row.get('provider_type') == 'moli_webfetch'
            else native_browser_engine(row['provider'])
        )
        if provider not in {'moli','lightpanda','obscura','chromium','kitesurf'}:
            failures.append(row['provider'] + '/' + row['case_id'])
            continue
        if row.get('error_type') == 'configuration':
            failures.append(row['provider'] + '/' + row['case_id'])
            continue
        case = inventory[row['case_id']]
        mode = output_mode_for_case(provider, 'headless_markdown', case)
        native_contract = native_contract_for_case(case)
        meta = row.get('provider_metadata') or {}
        # The runner may expire before a worker returns metadata. Its retained
        # call and cohort receipts still bind the invocation and deadline.
        runner_fields = {'call_log', 'configured_min_interval_seconds', 'client_throttle_wait_ms'}
        if row.get('ok') is False and row.get('error_type') in {'timeout','network'} and meta.get('call_log') and not (set(meta) - runner_fields):
            continue
        valid = True
        if mode == 'markdown':
            # Page-body tasks have one formal deliverable: Markdown.  Every
            # non-Chromium method must obtain it from its browser's native
            # Markdown route; Chromium is the sole declared shared-converter
            # exception.  Older Moli evidence did not spell out the two
            # redundant metadata fields, so its native route/output pair is
            # also the backward-compatible proof of this contract.
            standard_body = case.track not in {'document', 'image', 'video'}
            if standard_body:
                declared_contract = meta.get('output_contract')
                if declared_contract not in {None, 'markdown'}:
                    valid = False
            if provider == 'moli':
                native = (meta.get('route') == 'ordinary_markdown'
                          and meta.get('output') == 'markdown')
                if meta.get('route') == 'dynamic_native_markdown':
                    native = (case.track == 'dynamic' and meta.get('output') == 'markdown'
                              and meta.get('dump') == 'markdown')
                valid = valid and native and meta.get('extractor') in {None, 'moli_native_markdown'}
            elif provider == 'chromium':
                valid = valid and (meta.get('extractor') == 'node-html-markdown'
                                   and (meta.get('extractor_version') == '2.0.0'
                                        or (row.get('ok') is False and meta.get('extractor_version') is None)))
            elif provider in {'lightpanda','obscura'}:
                valid = valid and meta.get('extractor') == provider+'_native_markdown'
            elif provider == 'kitesurf':
                valid = valid and (meta.get('route') == 'browser_rendering_markdown'
                                   and meta.get('extractor') == 'cloudflare_native_markdown'
                                   and meta.get('browser') == 'kitesurf')
        elif mode in {'document','file'}:
            if provider == 'moli':
                valid = (meta.get('route') == ('raw_document' if mode == 'document' else 'raw_file')
                         and meta.get('extractor') == 'moli_cdp_response' + ('+document_text' if mode == 'document' else ''))
            else:
                prefixes = {'lightpanda':'lightpanda_cdp_response', 'obscura':'obscura_original_response',
                            'chromium':'browser_native_response_or_download', 'kitesurf':'kitesurf_cdp_response'}
                valid = meta.get('extractor') == prefixes[provider] + ('+document_text' if mode == 'document' else '')
            valid = valid and meta.get('route') != 'workers_ai_tomarkdown'
        elif mode == 'resource' and provider == 'moli':
            if case.track == 'video':
                valid = (
                    meta.get('route') == 'resource_native_json_trace'
                    and meta.get('output') == 'native_json_resource_urls'
                    and meta.get('output_contract') == 'native_json_resource_urls'
                    and meta.get('dump') == 'json'
                    and meta.get('network_trace_requested') is True
                    and meta.get('wait_until') == 'done'
                    and meta.get('wait', {}).get('delay_ms') == 5000
                )
            else:
                valid = (
                    meta.get('route') == 'resource_native_html'
                    and meta.get('output') == 'native_html_resource_urls'
                    and meta.get('output_contract') == 'native_html_resource_urls'
                    and meta.get('dump') == 'html'
                    and not meta.get('network_trace_requested')
                )
            valid = valid and not meta.get('output_projection_sha256')
        elif mode in expressions:
            expression = resource_expression(case.track, resource_detail_links(case)) if mode == 'resource' else expressions[mode]
            valid = meta.get('output_projection_sha256') == hashlib.sha256(expression.encode()).hexdigest()
            if provider == 'kitesurf':
                valid = (valid and meta.get('browser') == 'kitesurf'
                         and meta.get('endpoint_route') == '/browser-run/devtools/browser?browser=kitesurf')
        if case.track == 'document':
            valid = valid and meta.get('document_extractor') in {None, 'pdfjs_runtime', 'rendered_page_text'}
            valid = valid and '+document_text' not in str(meta.get('extractor', ''))
        frame_contract = bool(native_contract and native_contract.include_subdocuments)
        frame_metadata = (
            (meta.get('wait') or {}).get('subdocuments')
            if provider == 'moli'
            else meta.get('subdocuments')
        )
        if frame_contract:
            expected = {
                'moli': ('native', '--with-frames'),
                'lightpanda': (
                    'native_markdown_omits_child_document',
                    '--load-resources iframe + --with-frames',
                ),
            }.get(provider, ('unsupported', None))
            valid = valid and frame_metadata == {
                'requested': True,
                'support': expected[0],
                'interface': expected[1],
                'harness_content_assembly': False,
            }
        elif frame_metadata is not None:
            valid = False
        if not valid:
            failures.append(row['provider'] + '/' + case.id)
    if failures:
        raise ValueError('Native task route contract violated: ' + ', '.join(failures))
