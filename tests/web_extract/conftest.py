"""Explicit in-process test doubles; HTTPS scenarios exercise real spawned workers."""

from dataclasses import replace

import pytest

from harness.web_extract import pipeline


@pytest.fixture
def stubbed_serial_collection(monkeypatch):
    """Keep module-local provider stubs in the parent without changing production policies."""
    original = pipeline.run_jobs

    def run(jobs, execute, commit, policy, **options):
        if policy.max_in_flight == 1:
            policy = replace(policy, backend='thread')
        return original(jobs, execute, commit, policy, **options)

    monkeypatch.setattr(pipeline, 'run_jobs', run)
    monkeypatch.setattr(pipeline, '_COLLECTION_CLOCK', None)
