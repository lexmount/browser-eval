"""Provider transport registry. Add a provider here without changing the collector."""
from dataclasses import dataclass
from datetime import date, timedelta
import re


@dataclass(frozen=True)
class Adapter:
    request: object
    authorization_header: str = "Authorization"
    authorization_prefix: str = "Bearer "
    response: object = None
    telemetry: bool = False

    def cost(self, method, status, transport_exit, payload):
        price = method.get("price_per_1000")
        policy = method.get("billing", "unknown")
        if policy not in {"unknown", "http-200", "http-200-nonempty"}:
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
        if not self.telemetry or not isinstance(payload, dict):
            return {}
        return {key: payload.get(key) for key in ("timing", "usage", "cache", "attempts", "stop_reason", "coverage")}



def _options(options, allowed):
    options = options or {}
    if set(options) - allowed or any(type(v) is not bool for v in options.values()):
        raise ValueError("Unsupported search output option")
    return options


def lexmount_request(case, protocol, options=None):
    _options(options, set())
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


def _bocha_response(payload):
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


ADAPTERS = {
    "lexmount": Adapter(lexmount_request, "X-API-Key", "", telemetry=True),
    "deepseek": Adapter(deepseek_request),
    "bocha": Adapter(bocha_request, response=_bocha_response),
}


def adapter_for(name):
    if name not in ADAPTERS:
        raise ValueError("Unknown search adapter: " + str(name))
    return ADAPTERS[name]


def normalize_envelope(payload, adapter=None):
    if adapter is not None:
        transform = adapter_for(adapter).response
        return transform(payload) if transform else payload
    # Saved historical calls predate adapter-tagged native envelopes.
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict) and "webPages" in payload["data"]:
        return ADAPTERS["bocha"].response(payload)
    return payload
