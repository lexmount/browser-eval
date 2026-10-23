"""Provider transport registry. Add a provider here without changing the collector."""
from dataclasses import dataclass
from datetime import date, timedelta
import json
from urllib.parse import urlencode
import re

BILLING_POLICIES = frozenset({'unknown', 'http-200', 'http-200-nonempty'})

@dataclass(frozen=True)
class Adapter:
    request: object
    authorization_header: str = "Authorization"
    authorization_prefix: str = "Bearer "
    response: object = None
    telemetry: object = False
    projection: object = None

    def cost(self, method, status, transport_exit, payload):
        price = method.get("price_per_1000")
        policy = method.get("billing", "unknown")
        if policy not in BILLING_POLICIES:
            raise ValueError("Unsupported billing policy")
        if policy == "unknown" or price is None or transport_exit:
            return None
        if status != 200:
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
    return dict(q=_serp_query(case), num=protocol["top_k"],
                **_string_options(options, {"gl", "hl"}))


def brightdata_request(case, protocol, options=None):
    options = _string_options(options, {"zone", "gl", "hl", "data_format"})
    if not options.get("zone"):
        raise ValueError("Bright Data SERP requires an explicit zone")
    params = urlencode(dict(q=_serp_query(case), gl=options.get("gl", "us"), hl=options.get("hl", "en")))
    return dict(zone=options["zone"], url="https://www.google.com/search?" + params,
                format="json", data_format=options.get("data_format", "parsed"))


def _bocha_response(payload, **kwargs):
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
        if any(not isinstance(item, dict) for item in pages):
            raise ValueError("Each web page must be an object")
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


def _serp_response(payload, adapter, *, top_k=None, project_slots=False):
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
        if any(not isinstance(item, dict) for item in pages):
            raise ValueError("Each organic result must be an object")
        payload = {"results": [dict(url=p.get("link"), title=p.get("title", ""),
                                   snippet=p.get("snippet", p.get("description", "")),
                                   date=p.get("date")) for p in pages]}
    return payload


ADAPTERS = {
    "lexmount": Adapter(lexmount_request, "X-API-Key", "", telemetry=True),
    "browserbase": Adapter(browserbase_request, "x-bb-api-key", ""),
    "serper": Adapter(serper_request, "X-API-KEY", "", response=lambda payload: _serp_response(payload, "serper"), projection=lambda payload, **kwargs: _serp_response(payload, "serper", **kwargs)),
    "brightdata": Adapter(brightdata_request, response=lambda payload: _serp_response(payload, "brightdata"), projection=lambda payload, **kwargs: _serp_response(payload, "brightdata", **kwargs)),
    "deepseek": Adapter(deepseek_request),
    "bocha": Adapter(bocha_request, response=_bocha_response),
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
        return ADAPTERS["bocha"].response(payload)
    return payload
