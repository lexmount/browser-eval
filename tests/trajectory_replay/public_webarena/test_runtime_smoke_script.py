from __future__ import annotations

import pytest

from scripts.smoke_webarena_runtime import site_urls


def test_runtime_smoke_selects_active_urls_without_credentials():
    config = {
        "environments": {
            "__SHOPPING__": {
                "urls": ["http://old.example", "http://current.example"],
                "active_url_idx": 1,
                "credentials_env": {
                    "username": "WEBARENA_SHOPPING_USERNAME",
                    "password": "WEBARENA_SHOPPING_PASSWORD",
                },
            },
            "__REDDIT__": {"urls": ["http://reddit.example"]},
        }
    }

    assert site_urls(config, ("shopping", "reddit")) == {
        "shopping": "http://current.example",
        "reddit": "http://reddit.example",
    }


def test_runtime_smoke_rejects_persisted_or_missing_configuration():
    config = {
        "environments": {
            "__SHOPPING__": {
                "urls": ["http://shopping.example"],
                "credentials": {"username": "saved", "password": "saved"},
            }
        }
    }

    with pytest.raises(ValueError, match="must not persist credentials"):
        site_urls(config, ("shopping",))
    with pytest.raises(ValueError, match="missing __REDDIT__"):
        site_urls(config, ("reddit",))
