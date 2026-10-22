"""Explicit physical receipts for offline model-response fixtures."""
import base64
import hashlib
import json
from copy import deepcopy
from harness.web_search.relevance import JUDGE_PROMPT, judge_input


def strict_model_evidence(evidence):
    evidence = deepcopy(evidence)
    judge = evidence["judge"]
    configuration = {"receipt_schema":2,"packet_sha256":evidence["packet_sha256"],"model":judge["name"],"base_url":"https://test.invalid/v1","prompt":JUDGE_PROMPT,"temperature":0,"max_tokens":32,"response_format":{"type":"json_object"},"request_headers":{},"attempt_limit":3}
    judge["configuration"] = configuration
    for packet, call in zip(evidence["blind_candidate_packets"], judge["calls"], strict=True):
        call["request"] = {k:configuration[k] for k in ("model","temperature","max_tokens","response_format")}
        call["request"]["messages"] = [{"role":"system","content":JUDGE_PROMPT},{"role":"user","content":json.dumps(judge_input(packet),ensure_ascii=False,sort_keys=True)}]
        response = call["response"]
        judgments = json.loads(response["choices"][0]["message"]["content"])
        for item in judgments["judgments"]:
            item["evidence"] = {"field":"snippet","preview_index":0}
        response["choices"][0]["message"]["content"] = json.dumps(judgments)
        raw = json.dumps(response).encode()
        call["attempts"] = [{"attempt":1,"completed":True,"request_sha256":hashlib.sha256(json.dumps(call["request"],sort_keys=True).encode()).hexdigest(),"url":configuration["base_url"]+"/chat/completions","request_headers":{},"response_bytes_base64":base64.b64encode(raw).decode(),"response_sha256":hashlib.sha256(raw).hexdigest(),"status":200,"error_type":None,"retryable":False}]
    return evidence
