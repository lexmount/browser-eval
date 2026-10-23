"""Synthetic execution controls shared by provider-boundary tests."""
from harness.web_search.example import synthetic_profile

def execution_profile():
    profile = synthetic_profile()
    profile["protocol"].update(retry=True, interval_s=0)
    for method, key in zip(profile["methods"], ("LEXMOUNT_SEARCH_API_KEY", "DEEPSEEK_API_KEY")):
        method["key_env"] = key
    profile["methods"].append({"id": "bocha-summary", "label": "Fixture C", "adapter": "bocha", "endpoint": "https://fixture-c.invalid/search", "key_env": "BOCHA_API_KEY", "currency": "CNY", "price_per_1000": 36, "billing": "http-200", "output_options": {"summary": True}})
    return profile
