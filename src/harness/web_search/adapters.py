"""Provider transport registry. Add a provider here without changing the collector."""

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

BILLING_POLICIES = frozenset({'unknown', 'http-200', 'http-200-nonempty'})

@dataclass(frozen=True)
class Adapter:
    request: object
    authorization_header: str = "Authorization"
    authorization_prefix: str = "Bearer "
    response: object = None
    telemetry: object = False
    projection: object = None
    account_error_codes: frozenset = frozenset()
    version_path: tuple = ()
    failure: object = None
    output_fields: object = None

    def field_map(self, method=None):
        from lexbench_websearch.outputs import validate_fields

        value = self.output_fields(method or {}) if callable(self.output_fields) else self.output_fields
        return validate_fields(deepcopy(value))

    def account_error(self, payload, method=None):
        response = payload.get("Response") if isinstance(payload, dict) else None
        error = response.get("Error") if isinstance(response, dict) else None
        code = error.get("Code") if isinstance(error, dict) else None
        if isinstance(code, str) and code.split(".")[0] in self.account_error_codes:
            return code
        expected = (method or {}).get("expected_response_version")
        if (expected is not None and isinstance(response, dict) and not error
                and (isinstance(response.get('Version'), str) and bool(response['Version'])
                     or isinstance(response.get('Pages'), list)
                     or (response.get('RequestId') and isinstance(response.get('Query'), str)))):
            version = payload
            for key in self.version_path:
                version = version.get(key) if isinstance(version, dict) else None
            if version != expected:
                return f"ResponseVersionMismatch: expected {expected}, received {version}"
        return None

    def cost(self, method, status, transport_exit, payload):
        price = method.get("price_per_1000")
        policy = method.get("billing", "unknown")
        if policy not in BILLING_POLICIES:
            raise ValueError("Unsupported billing policy")
        if policy == "unknown" or price is None or transport_exit:
            return None
        if status != 200:
            return 0.0
        if (self.account_error(payload, method) or "").startswith("ResponseVersionMismatch:"):
            return None
        if method.get('expected_response_version') is not None and self.version_path:
            response = payload.get('Response') if isinstance(payload, dict) else None
            if (not isinstance(response, dict) or not response.get('Error')
                    and not isinstance(response.get('Pages'), list)
                    and not (response.get('Version') and response.get('RequestId')
                             and isinstance(response.get('Query'), str))):
                return None
        if self.failure and self.failure(payload):
            return 0.0
        if policy == "http-200-nonempty":
            try:
                payload = self.response(payload) if self.response else payload
            except (ValueError, AttributeError, TypeError):
                return None
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                return None
            return price / 1000 if payload["results"] else 0.0
        return price / 1000

    def timing(self, payload):
        if callable(self.telemetry):
            return self.telemetry(payload)
        if not self.telemetry or not isinstance(payload, dict):
            return {}
        return {key: payload.get(key) for key in ("timing", "usage", "cache", "attempts", "stop_reason", "coverage")}


def _options(options, allowed):
    options = options or {}
    if set(options) - allowed or any(type(v) is not bool for v in options.values()):
        raise ValueError("Unsupported search output option")
    return options


def lexmount_request(case, protocol, options=None):
    options = options or {}
    if options:
        if options != {"interface": "public"}:
            raise ValueError("Unsupported Lexmount interface")
        return dict(query=_serp_query(case), limit=protocol["top_k"])
    body = dict(query=case["query"], type="web", limit=protocol["top_k"],
                timeout_ms=protocol["server_timeout_ms"], include=["attempts"])
    if case.get("source"):
        body["source"] = case["source"]
    return body


def deepseek_request(case, protocol, options=None):
    options = _options(options, {"need_content"})
    query = case["query"]
    if case.get("source") and case.get("domains"):
        query += " " + " OR ".join("site:" + d for d in case["domains"])
    return dict(query=query, count=protocol["top_k"], need_content=options.get("need_content", True))


def bocha_request(case, protocol, options=None):
    options = _options(options, {"summary"})
    query = case["query"]
    body = dict(count=protocol["top_k"], summary=options.get("summary", True), freshness="noLimit")
    if case.get("domains") and case.get("family") in ("site", "vertical"):
        body["include"] = "|".join(case["domains"])
        query = re.sub(r"(?<!\S)site:\S+", "", query).strip()
    after = re.search(r"(?<!\S)after:(\d{4}-\d{2}-\d{2})(?!\S)", query)
    if after and protocol.get("as_of"):
        start = date.fromisoformat(after[1]) + timedelta(days=1)
        body["freshness"] = f"{start.isoformat()}..{protocol['as_of']}"
        query = (query[:after.start()] + query[after.end():]).strip()
    body["query"] = " ".join(query.split())
    return body


def bocha_ai_request(case, protocol, options=None):
    options = _options(options, {'answer', 'stream'})
    if options.get('stream', False):
        raise ValueError('The measured AI Search contract requires stream=false')
    body = bocha_request(case, protocol)
    body.pop('summary')
    return dict(body, answer=options.get('answer', True), stream=False)


def bocha_ai_response(payload, *, top_k=None, project_slots=False, preserve_invalid_slots=False):
    if (not isinstance(payload, dict) or payload.get('code') != 200
            or not isinstance(payload.get('messages'), list)):
        raise ValueError('AI Search requires a successful messages envelope')
    pages, answers = [], []
    for message in payload['messages']:
        if not isinstance(message, dict) or message.get('role', 'assistant') != 'assistant':
            raise ValueError('Invalid AI Search message')
        if message.get('content_type') == 'webpage' and message.get('type', 'source') == 'source':
            content = message.get('content')
            content = json.loads(content) if isinstance(content, str) else content
            if not isinstance(content, dict) or not isinstance(content.get('value'), list):
                raise ValueError('AI Search webpage messages require a value array')
            pages.extend(content['value'])
        elif message.get('type') == 'answer' and message.get('content_type') == 'text':
            if not isinstance(message.get('content'), str):
                raise ValueError('AI Search answers must be text')
            answers.append(message['content'])
    if project_slots and top_k is not None:
        pages = pages[:top_k]
    if any(not isinstance(page, dict) for page in pages):
        if not preserve_invalid_slots:
            raise ValueError('AI Search webpages must be objects')
        pages = [p if isinstance(p, dict) else {} for p in pages]
    return dict(results=[dict(url=p.get('url'), title=p.get('name', ''),
                              snippet=p.get('snippet', ''), summary=p.get('summary', ''),
                              citation_id=p.get('id'), date=p.get('datePublished')) for p in pages],
                answer='\n\n'.join(answers))


def _serp_query(case):
    query = case["query"]
    if case.get("source") and case.get("domains"):
        query += " (" + " OR ".join("site:" + d for d in case["domains"]) + ")"
    return query


def _string_options(options, allowed):
    options = options or {}
    if set(options) - allowed or any(not isinstance(v, str) or not v for v in options.values()):
        raise ValueError("Unsupported search output option")
    return options


def browserbase_request(case, protocol, options=None):
    return dict(query=_serp_query(case), numResults=protocol["top_k"],
                **_string_options(options, {"provider", "mode"}))


def serper_request(case, protocol, options=None):
    options = _query_locale(case, protocol, _string_options(options, {'gl', 'hl'}))
    return dict(q=_serp_query(case), num=protocol["top_k"],
                **options)


def _query_locale(case, protocol, options):
    policy = protocol.get('locale_policy')
    if policy not in (None, 'query-language/1'):
        raise ValueError('Unsupported query locale policy')
    language = (case.get('language') or '').casefold().split('-')[0]
    if policy == 'query-language/1' and language and language != 'en':
        return {k:v for k,v in options.items() if k not in ('gl','hl')}
    return options


def brightdata_request(case, protocol, options=None):
    options = _string_options(options, {"zone", "gl", "hl", "data_format"})
    if not options.get("zone"):
        raise ValueError("Bright Data SERP requires an explicit zone")
    locale = _query_locale(case, protocol, dict(gl=options.get('gl','us'),hl=options.get('hl','en')))
    params = urlencode(dict(q=_serp_query(case), **locale))
    return dict(zone=options["zone"], url="https://www.google.com/search?" + params,
                format="json", data_format=options.get("data_format", "parsed"))


def tencent_wsa_request(case, protocol, options=None):
    options = _string_options(options, {"edition"})
    edition = options.get("edition", "standard")
    if edition not in {"lite", "standard", "flagship"}:
        raise ValueError("Unsupported Yuanbao Search edition")
    # SearchPro starts at ten results; the protocol projects native slots.
    body = dict(Query=_serp_query(case), Cnt=10, Mode=0)
    if edition == "lite":
        body = dict(Query=_serp_query(case))
    domains = case.get("domains", [])
    if len(domains) == 1 and case.get("family") in ("site", "vertical"):
        body["Site"] = domains[0]
        body["Query"] = re.sub(r"(?<!\S)site:" + re.escape(domains[0]) + r"(?!\S)",
                               "", case["query"]).strip()
    return body


def tencent_wsa_response(payload, *, top_k=None, project_slots=False, preserve_invalid_slots=False):
    response = payload.get("Response") if isinstance(payload, dict) else None
    if not isinstance(response, dict):
        raise ValueError("Yuanbao Search response must contain Response")
    if response.get("Error"):
        raise ValueError("Yuanbao Search error: " + json.dumps(response["Error"], ensure_ascii=False))
    pages = response.get("Pages")
    if (preserve_invalid_slots and pages is None and response.get('Version')
            and response.get('RequestId') and isinstance(response.get('Query'), str)):
        pages = []
    if not isinstance(pages, list):
        raise ValueError("Yuanbao Search response must contain a Pages array")
    if top_k is not None:
        pages = pages[:top_k]
    results = []
    for page in pages:
        if preserve_invalid_slots:
            try:
                decoded = json.loads(page) if isinstance(page, str) else None
            except ValueError:
                decoded = None
            if not isinstance(decoded, dict):
                results.append({'url': None})
                continue
            page = decoded
        else:
            if not isinstance(page, str):
                raise ValueError("Yuanbao Search page must be a JSON string")
            page = json.loads(page)
        if not isinstance(page, dict):
            raise ValueError("Yuanbao Search page must decode to an object")
        results.append(dict(url=page.get("url"), title=page.get("title", ""),
                            snippet=page.get("passage", ""), summary=page.get("content", ""),
                            content="", date=page.get("date")))
    return {"results": results}


def _bocha_response(payload, *, top_k=None, project_slots=False, preserve_invalid_slots=False):
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("data"), dict)
        and "webPages" in payload["data"]
    ):
        if payload.get("code") != 200:
            raise ValueError("Search provider reported an error")
        web_pages = payload["data"]["webPages"]
        pages = web_pages.get("value") if isinstance(web_pages, dict) else None
        if not isinstance(pages, list):
            raise ValueError("Response must contain a web page array")
        if project_slots and top_k is not None:
            pages = pages[:top_k]
        if not preserve_invalid_slots and any(not isinstance(item, dict) for item in pages):
            raise ValueError("Each web page must be an object")
        if preserve_invalid_slots:
            pages = [item if isinstance(item, dict) else {} for item in pages]
        payload = {
            "results": [
                {
                    "url": item.get("url"),
                    "title": item.get("name", ""),
                    "snippet": item.get("snippet", ""),
                    "summary": item.get("summary", ""),
                    "content": "",
                    "date": item.get("datePublished"),
                }
                for item in pages
            ]
        }
    return payload


def _serp_response(payload, adapter, *, top_k=None, project_slots=False, preserve_invalid_slots=False):
    if adapter == "brightdata" and isinstance(payload, dict) and "status_code" in payload:
        if payload["status_code"] != 200:
            raise ValueError("Bright Data upstream error: " + str(payload.get("body", payload["status_code"])))
        payload = json.loads(payload["body"]) if isinstance(payload.get("body"), str) else payload.get("body")
    if adapter in ("serper", "brightdata") and isinstance(payload, dict) and not payload.get("error"):
        pages = payload.get("organic")
        if not isinstance(pages, list):
            raise ValueError("Response must contain an organic results array")
        if project_slots and top_k is not None:
            pages = pages[:top_k]
        if not preserve_invalid_slots and any(not isinstance(item, dict) for item in pages):
            raise ValueError("Each organic result must be an object")
        if preserve_invalid_slots:
            pages = [item if isinstance(item, dict) else {} for item in pages]
        payload = {"results": [dict(url=p.get("link"), title=p.get("title", ""),
                                   snippet=p.get("snippet", p.get("description", "")),
                                   date=p.get("date")) for p in pages]}
    return payload


def _fields(*, preview=True, extra=False, answer=False, body=False):
    return dict(primary_preview='snippet' if preview else None,
                extra_text=['summary'] if extra else [], answer='answer' if answer else None,
                page_body='content' if body else None)


ADAPTERS = {
    "lexmount": Adapter(lexmount_request, "X-API-Key", "", telemetry=True, output_fields=_fields()),
    "browserbase": Adapter(browserbase_request, "x-bb-api-key", "", output_fields=_fields(preview=False)),
    "serper": Adapter(serper_request, "X-API-KEY", "", response=lambda payload: _serp_response(payload, "serper"), projection=lambda payload, **kwargs: _serp_response(payload, "serper", **kwargs), output_fields=_fields()),
    "brightdata": Adapter(brightdata_request, response=lambda payload: _serp_response(payload, "brightdata"), projection=lambda payload, **kwargs: _serp_response(payload, "brightdata", **kwargs), output_fields=_fields()),
    "deepseek": Adapter(deepseek_request, output_fields=lambda m: _fields(extra=True, body=(m.get('output_options') or {}).get('need_content', True))),
    "bocha": Adapter(bocha_request, response=_bocha_response, projection=_bocha_response, output_fields=lambda m: _fields(extra=(m.get('output_options') or {}).get('summary', True))),
    "bocha-ai": Adapter(bocha_ai_request, response=bocha_ai_response, projection=bocha_ai_response,
                        output_fields=lambda m: _fields(extra=True, answer=(m.get('output_options') or {}).get('answer', True)),
                        failure=lambda p: isinstance(p, dict) and p.get('code') != 200),
    "tencent-wsa": Adapter(tencent_wsa_request, response=tencent_wsa_response,
                           projection=tencent_wsa_response,
                           account_error_codes=frozenset({"UnauthorizedOperation", "ResourceNotFound",
                                                          "ResourceUnavailable", "AuthFailure"}),
                           version_path=("Response", "Version"),
                           output_fields=lambda m: _fields(extra=(m.get('output_options') or {}).get('edition', 'standard') != 'lite'),
                           failure=lambda p: isinstance(p, dict) and isinstance(p.get("Response"), dict) and bool(p["Response"].get("Error"))),
}


def adapter_for(name):
    if name not in ADAPTERS:
        raise ValueError("Unknown search adapter: " + str(name))
    return ADAPTERS[name]


def normalize_envelope(payload, adapter=None, **kwargs):
    if adapter is not None:
        config = adapter_for(adapter)
        if config.projection:
            return config.projection(payload, **kwargs)
        return config.response(payload) if config.response else payload
    # Saved historical calls predate adapter-tagged native envelopes.
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict) and "webPages" in payload["data"]:
        return ADAPTERS["bocha"].projection(payload, **kwargs)
    return payload
